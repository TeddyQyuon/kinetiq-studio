"""Start the Phase 0 API, web studio and local workers from either platform launcher."""

import argparse
import contextlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "apps/api"
API_URL = "http://127.0.0.1:8000"
REMOTION = ROOT / "node_modules/@remotion/cli/package.json"


def ensure_env() -> None:
    path = ROOT / ".env"
    if path.exists():
        return
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    for key in ("ENCRYPTION_MASTER_KEY", "SESSION_SECRET", "MEDIA_SIGNING_SECRET", "MCP_ACCESS_TOKEN"):
        template = re.sub(rf"^{key}=.*$", f"{key}={secrets.token_urlsafe(32)}", template, flags=re.MULTILINE)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(template)
    print("Created .env with generated local secrets. Keep it private and back it up.", flush=True)


def prepare_python(real_media: bool) -> Path:
    env_dir = Path(os.environ.get("VIRTUAL_ENV") or API / ".venv")
    py = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not py.is_file():
        subprocess.run([sys.executable, "-m", "venv", str(env_dir)], check=True)
    subprocess.run([py, "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r",
                    str(API / "requirements.txt")], check=True)
    if real_media:
        subprocess.run([py, "-m", "pip", "install", "-r", str(ROOT / "workers/media/requirements.txt")], check=True)
        subprocess.run([py, "-m", "spacy", "download", "en_core_web_sm"], check=True)
        subprocess.run([py, "-m", "nltk.downloader", "punkt_tab"], check=True)
    return py


def stop_process(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        if proc.poll() is None:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], check=False, capture_output=True)
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
        proc.wait()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--no-workers", action="store_true", help="Start only FastAPI and Next.js")
    modes.add_argument("--real-media", action="store_true", help="Install and use Kokoro and WhisperX (CPU)")
    modes.add_argument("--mock", action="store_true", help="Use model-free media and the normal renderer (default)")
    args = parser.parse_args()
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    node = shutil.which("node")
    if not npm or not node or int(subprocess.check_output([node, "-p", "process.versions.node.split('.')[0]"])) < 22:
        parser.exit(1, "Install Node.js 22+ with npm to start the studio.\n")
    if not args.no_workers and not all(shutil.which(cmd) for cmd in ("ffmpeg", "ffprobe")):
        parser.exit(1, "Install FFmpeg (ffmpeg and ffprobe), or use --no-workers.\n")
    for port in (8000, 3000):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                parser.exit(1, f"Port {port} is already in use. Stop that service before starting the studio.\n")

    ensure_env()
    py = prepare_python(args.real_media)
    if not (ROOT / "node_modules/next/package.json").is_file():
        subprocess.run([npm, "ci"], cwd=ROOT, check=True)
    if not args.no_workers and (not REMOTION.is_file() or json.loads(REMOTION.read_bytes())["version"] != "4.0.547"):
        subprocess.run([npm, "install", "--no-save", "--package-lock=false", "--workspaces=false",
                        "@remotion/cli@4.0.547"], cwd=ROOT, check=True)

    # Reuse the repo's dotenv parser without evaluating shell syntax from .env.
    read_env = ("import {loadEnvFile} from './scripts/check-env.mjs';"
                "process.stdout.write(JSON.stringify(loadEnvFile('.env')))")
    config = json.loads(subprocess.check_output([node, "--input-type=module", "-e", read_env], cwd=ROOT))
    env = {**config, **os.environ}
    path_entries = [str(ROOT / "workers/agent-core"), str(ROOT)]
    if env.get("PYTHONPATH"):
        path_entries.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(path_entries)
    processes = {}

    def start(name, command, cwd, child_env):
        processes[name] = subprocess.Popen(command, cwd=cwd, env=child_env, start_new_session=os.name != "nt",
                                           creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)

    def check_children():
        for name, process in processes.items():
            if (status := process.poll()) is not None:
                raise RuntimeError(f"{name} exited with status {status}.")

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    try:
        start("API", [py, "-m", "uvicorn", "app.main:app", "--port", "8000", "--reload"], API, env)
        opener = build_opener(ProxyHandler({}))
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            check_children()
            try:
                with opener.open(API_URL + "/ready", timeout=1) as response:
                    if json.load(response).get("status") == "ready":
                        break
            except OSError:
                pass
            time.sleep(0.25)
        else:
            raise RuntimeError("FastAPI did not become ready on port 8000 within 60 seconds.")
        if not args.no_workers:
            for role, caps in (("media", "tts,align,transcode"), ("render", "render")):
                token_file = ROOT / "storage/worker-tokens" / f"{role}.token"
                subprocess.run([py, "-m", "app.workers.bootstrap", "--name", f"local-{role}", "--profile",
                                role, "--capabilities", caps, "--token-file", token_file],
                               cwd=API, env=env, check=True)
                worker_env = {**os.environ, "PYTHONPATH": env["PYTHONPATH"], "API_BASE_URL": API_URL,
                              "PUBLIC_API_URL": env.get("PUBLIC_API_URL", API_URL), "MEDIA_MOCK_MODE": "false"}
                worker_env.pop("WORKER_TOKEN", None)
                command = [py, "-m", f"workers.{role}.runner", "--token-file", token_file]
                if role == "media" and not args.real_media:
                    command.append("--mock")
                start(role, command, ROOT, worker_env)
        web_env = {**os.environ, "NEXT_PUBLIC_API_URL": env.get("NEXT_PUBLIC_API_URL", "http://localhost:8000")}
        start("Next.js", [npm, "run", "dev", "--workspace", "@kinetiq/web", "--", "--port", "3000", "--hostname", "127.0.0.1"], ROOT, web_env)
        print("Studio  http://localhost:3000\nAPI docs  http://localhost:8000/docs\nPress Ctrl+C to stop.", flush=True)
        while True:
            check_children()
            time.sleep(0.5)
    finally:
        for process in reversed(list(processes.values())):
            stop_process(process)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        sys.exit(str(exc))
