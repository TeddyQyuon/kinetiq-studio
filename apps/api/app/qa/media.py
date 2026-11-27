"""Read only verified project media and sample the actual encoded proxy."""

from pathlib import Path
import subprocess
import tempfile

from sqlalchemy.orm import Session

from app.models import Asset, Project


async def read_media(db: Session, project: Project, asset: Asset, limit: int) -> bytes:
    from app.storage.drive import get_storage_provider

    if asset.project_id != project.id or asset.sync_state != "synced" or (asset.size_bytes or 0) > limit:
        raise ValueError("Media is unavailable or exceeds the QA input limit")
    storage = get_storage_provider(db)
    content, total, _ = await storage.read_range(
        user_id=project.owner_id, storage_key=asset.storage_key, drive_file_id=asset.drive_file_id, start=0, end=limit
    )
    if not content or total > limit or len(content) != total:
        raise ValueError("Incomplete QA media")
    return content


def sample_proxy_frames(video: bytes) -> list[bytes]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "proxy.mp4"
        path.write_bytes(video)
        source = ["-v", "error", "-protocol_whitelist", "file", "-f", "mov", "-i", str(path)]
        # Decode/count when a container has no reliable duration or frame-count metadata.
        count = subprocess.run(
            [
                "ffprobe",
                *source,
                "-select_streams",
                "v:0",
                "-count_frames",
                "-show_entries",
                "stream=nb_read_frames",
                "-of",
                "csv=p=0",
            ],
            capture_output=True,
            timeout=30,
            check=True,
        )
        total = int(count.stdout.strip())
        if total < 4:
            raise ValueError("A proxy needs at least four frames")
        indices = [round(i * (total - 1) / 3) for i in range(4)]
        select = "+".join(f"eq(n\\,{n})" for n in indices)
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                *source,
                "-map",
                "0:v:0",
                "-vf",
                f"select={select},scale=512:512:force_original_aspect_ratio=decrease",
                "-fps_mode",
                "vfr",
                "-frames:v",
                "4",
                "-threads",
                "1",
                "-q:v",
                "4",
                str(path.parent / "%d.jpg"),
            ],
            capture_output=True,
            timeout=30,
            check=True,
        )
        return [(path.parent / f"{i}.jpg").read_bytes() for i in range(1, 5)]
