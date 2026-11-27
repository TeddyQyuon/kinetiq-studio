# Render Worker (`workers/render`)

Pull-based final mastering worker using `kinetiq-agent-core` (`capabilities = ["render"]`), Node + Remotion (`packages/remotion-studio`), and FFmpeg.

## Responsibilities (Milestone M7)
1. Fetches frozen `TimelineDocument` revision + full-resolution `clips/master/` (or `upscaled_4k`) assets from Google Drive / Local Vault.
2. Invokes `npx remotion render` on `EduComposition` at native `1080×1920`, `1920×1080`, or `3840×2160` (`4K`).
3. Runs FFmpeg mastering pass:
   - Video: `libx264 -profile:v high -crf 17 -pix_fmt yuv420p -movflags +faststart`
   - Audio: `loudnorm=I=-14:TP=-1.5:LRA=11`, AAC `192k` `48kHz`, music ducked `-26 dB`
4. Uploads `exports/final_{res}.mp4` + `captions.srt` via 8 MiB resumable upload (`WorkerClient.upload_artifact`).
