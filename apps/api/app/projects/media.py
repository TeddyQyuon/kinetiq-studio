"""Parse, synthesize and verify project music in the active vault."""

from __future__ import annotations

import asyncio
import hashlib
import io
import math
import struct
import wave
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import httpx
from fastapi import HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from app.core.errors import ValidationFailedError
from app.models import Asset, Project, new_id
from app.projects.schemas import MusicFileOptions, MusicPresetRequest
from app.storage.drive import get_storage_provider
from app.storage.local import put_chunk, start_upload

MAX_AUDIO_BYTES = 25 * 1024 * 1024
AUDIO_TYPES = {
    ".mp3": ("audio/mpeg", "audio/mp3", "audio/x-mp3"),
    ".wav": ("audio/wav", "audio/wave", "audio/x-wav", "audio/vnd.wave"),
    ".m4a": ("audio/mp4", "audio/m4a", "audio/x-m4a"),
    ".ogg": ("audio/ogg", "application/ogg"),
}


@dataclass
class MusicInput:
    data: bytes
    extension: str
    options: MusicFileOptions
    duration_ms: int | None = None


@lru_cache(maxsize=3)
def synthesize_ambient(preset: str) -> bytes:
    # Integer cycle counts over the entire loop keep pitch and amplitude
    # continuous across its seam, even when the player repeats it indefinitely.
    notes, pulses = {
        "ambient_pulse": ((110.0, 164.81, 246.94), 16),
        "warm_pad": ((130.81, 164.81, 196.0), 2),
        "documentary_minimal": ((98.0, 146.83, 220.0), 6),
    }[preset]
    rate = 22050
    frames = rate * 15
    pitches = [round(hz * 15) for hz in notes]
    pcm = bytearray(frames * 2)
    for i in range(frames):
        phase = math.tau * i / frames
        level = 0.75 - 0.25 * math.cos(pulses * phase)
        chord = math.sin(pitches[0] * phase) + 0.6 * math.sin(pitches[1] * phase) + 0.35 * math.sin(pitches[2] * phase)
        struct.pack_into("<h", pcm, 2 * i, round(7700 * chord * level))
    data = io.BytesIO()
    with wave.open(data, "wb") as wav:
        wav.setparams((1, 2, rate, frames, "NONE", "not compressed"))
        wav.writeframes(pcm)
    return data.getvalue()


class _UploadTooLarge(MultiPartException):
    """A parser exception also closes spooled files on an interrupted upload."""


async def _bounded_body(request: Request, limit: int):
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > limit:
            raise _UploadTooLarge("Music upload exceeds the allowed size")
        yield chunk


async def read_music(request: Request) -> MusicInput:
    content_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    try:
        if content_type == "application/json":
            raw = b"".join([chunk async for chunk in _bounded_body(request, 8192)])
            preset = MusicPresetRequest.model_validate_json(raw)
            data = await asyncio.to_thread(synthesize_ambient, preset.preset)
            options = MusicFileOptions(mood=preset.preset, volume=preset.volume, duck_db=preset.duck_db)
            return MusicInput(data, ".wav", options, duration_ms=15000)
        if content_type != "multipart/form-data":
            raise HTTPException(415, "Use JSON for a preset or multipart/form-data for an audio file")

        # Count framing too so chunked requests cannot spool unbounded files before validation.
        parser = MultiPartParser(
            request.headers, _bounded_body(request, MAX_AUDIO_BYTES + 65536), max_files=1, max_fields=3
        )
        form = await parser.parse()
        try:
            file = form.get("file")
            if not isinstance(file, UploadFile) or not file.filename:
                raise ValidationFailedError("Please include the audio file in the file field")
            extension = Path(file.filename).suffix.lower()
            mime = (file.content_type or "").partition(";")[0].lower().strip()
            if extension not in AUDIO_TYPES or mime not in AUDIO_TYPES[extension]:
                raise HTTPException(415, "Use an MP3, WAV, M4A, or OGG audio file with a matching content type")
            options = MusicFileOptions.model_validate({k: v for k, v in form.items() if k != "file"})
            if file.size is not None and file.size > MAX_AUDIO_BYTES:
                raise _UploadTooLarge("Music files must be at most 25 MiB")
            data = await file.read(MAX_AUDIO_BYTES + 1)
            if len(data) > MAX_AUDIO_BYTES:
                raise _UploadTooLarge("Music files must be at most 25 MiB")
            if not data:
                raise ValidationFailedError("The music file is empty")
            return MusicInput(data, extension, options)
        finally:
            await form.close()
    except _UploadTooLarge as exc:
        raise HTTPException(413, exc.message) from exc
    except MultiPartException as exc:
        raise ValidationFailedError(exc.message) from exc
    except ValidationError as exc:
        raise ValidationFailedError(str(exc)) from exc


def _write_local(storage_key: str, data: bytes) -> None:
    size = len(data)
    session = start_upload(storage_key, size)
    put_chunk(session, f"bytes 0-{size - 1}/{size}", data)


async def store_music(db: Session, project: Project, music: MusicInput, revision: int) -> Asset:
    provider = get_storage_provider(db)
    asset_id = new_id("ast")
    filename = asset_id + music.extension
    storage_key = f"{project.id}/music/{filename}"
    digest = hashlib.sha256(music.data).hexdigest()
    mime = AUDIO_TYPES[music.extension][0]
    size = len(music.data)
    drive_id = None
    folder_id = project.drive_folder_id
    if provider.name == "local":
        await asyncio.to_thread(_write_local, storage_key, music.data)
    else:
        if not folder_id:
            slug = "".join(c if c.isalnum() else "-" for c in project.title.lower()).strip("-")[:32] or "project"
            folder_id = await provider.ensure_project_space(project.id, slug, project.owner_id)
        upload = await provider.create_upload_session(
            user_id=project.owner_id,
            project_id=project.id,
            project_folder_id=folder_id,
            storage_key=storage_key,
            filename=filename,
            content_type=mime,
            total_bytes=size,
            app_properties={"project_id": project.id, "kind": "music", "revision": str(revision + 1), "sha256": digest},
        )
        # A bounded upload fits in one final resumable chunk.
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.put(
                upload.session_uri,
                content=music.data,
                headers={**upload.upload_headers, "Content-Type": mime, "Content-Range": f"bytes 0-{size - 1}/{size}"},
            )
            response.raise_for_status()
            drive_id = response.json()["id"]
    verified = await provider.verify_uploaded_file(
        user_id=project.owner_id,
        storage_key=storage_key,
        drive_file_id=drive_id,
        expected_size=size,
        expected_sha256=digest,
    )
    if verified.sha256 != digest:
        raise ValidationFailedError("Storage could not verify the music checksum")
    if provider.name == "drive":
        project.drive_folder_id = folder_id
    return Asset(
        id=asset_id,
        kind="music",
        project_id=project.id,
        storage_provider=provider.name,
        storage_key=storage_key,
        drive_file_id=drive_id,
        content_type=mime,
        sha256=digest,
        size_bytes=size,
        duration_ms=music.duration_ms,
        sync_state="synced",
    )
