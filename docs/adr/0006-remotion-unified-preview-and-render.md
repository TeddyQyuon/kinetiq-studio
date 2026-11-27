# ADR-0006: Unified Preview and Final Render via Remotion

- **Status:** Accepted
- **Date:** 2026-11-27

## Context
Browser video editors frequently suffer from WYSIWYG drift: animated diagrams, kinetic captions, and timing look one way in the web preview and render differently in an FFmpeg-only backend script.

## Decision
Share a single **`packages/remotion-studio`** React package between:
- **`apps/web`** (`@remotion/player` rendering 540p Drive proxies + native React overlays/charts/captions live in Chrome), and
- **`workers/render`** (headless Remotion + FFmpeg finalizing native `1080p` or `4K` masters with `libx264 -crf 17 -pix_fmt yuv420p` and `-14 LUFS` audio normalization).

Pin exact `remotion` and `@remotion/player` versions (`4.0.547`) across the monorepo.

## Consequences
- **Positive:** Pixel-accurate parity between editor preview and final MP4 export, plus razor-sharp 4K vector typography and charts with zero GPU cost.
- **Negative:** Requires Node + headless Chromium on the render worker; teams larger than 3 people require a Remotion commercial license.
