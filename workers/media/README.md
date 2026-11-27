# Media Worker (`workers/media`)

Pull-based CPU media worker using `kinetiq-agent-core` (`capabilities = ["tts", "align", "transcode"]`).

## Responsibilities (Milestone M4)
1. **`tts` (`Kokoro-82M`):** Generates per-scene English narration WAVs (`audio/scene_001.wav`, etc.) with 250 ms sentence pauses so editing one scene only re-voices that single scene.
2. **`align` (`WhisperX`):** Extracts millisecond word-level timings (`words.json`) to lock `start_ms` / `end_ms` and drive kinetic TikTok/FB captions.
3. **`transcode` (`FFmpeg`):** Builds `540p` fast-seek proxies (`-g 15 -crf 24`) when needed.

Run via `WorkerClient` from `workers/agent-core`:
```python
from kinetiq_agent_core import WorkerClient
```

## Run the local stack

From the repository root, run `bash scripts/dev-local.sh` on macOS/Linux or `.\scripts\dev-local.ps1` in Windows PowerShell. Install Python 3.12+, Node.js 22+ with npm, and FFmpeg (including ffprobe) first. The launcher creates a private local .env on first use, prepares dependencies, and starts the API, studio, model-free media worker, and Remotion renderer. Ctrl+C stops the stack.

Use `--no-workers` to run only the API and studio. Use `--real-media` for Kokoro and WhisperX with their downloaded language models. Real media also needs the system libraries for eSpeak NG and libsndfile. The web studio opens at http://localhost:3000 and API documentation at http://localhost:8000/docs.

For Docker, run `docker compose up --build`. The default Phase 0 media image skips the model dependencies and uses mock synthesis and alignment. Set `MEDIA_INSTALL_MODELS=true` and `MEDIA_MOCK_MODE=false` in .env for real local media. The VPS override selects those settings automatically. The bootstrap service provisions both worker tokens on first start and reuses them on subsequent starts.
