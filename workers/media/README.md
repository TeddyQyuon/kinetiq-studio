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
