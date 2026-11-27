# ADR-0004: Automatic Gemini Vision QA with Human-on-the-Loop (HOTL) & Hybrid Routing

- **Status:** Accepted
- **Date:** 2026-11-27

## Context
Raw open-weight video models (`LTX-2.5`, `Wan 2.2`) waste significant Colab Compute Units if they animate flawed keyframes, garble text on charts/diagrams, or drift visually over 10-minute lessons. Conversely, requiring manual human approval before every single scene blocks overnight/background generation.

## Decision
1. **Hybrid Scene Routing:** Route data charts, bullet breakdowns, timelines, and callouts to **Remotion native 1080p/4K React templates** (`0` GPU cost, zero AI typos), while reserving Colab GPUs for cinematic B-roll (`ai_video`) and complex illustrations (`image_motion`).
2. **HOTL Vision QA (`hotl_smart` default):** After each fast 1080p keyframe renders, **Gemini Vision** scores it (`0–10`) on `narration_match (40%)`, `artifact_free (30%)`, `style_consistency (20%)`, and `safe_zone (10%)`:
   - `score >= 8.0` → auto-advances immediately to `i2v` clip generation.
   - `score < 8.0` with `< 2` retries → auto-heals (rewrites prompt, changes seed, or converts text-heavy scene into a native Remotion diagram).
   - `score < 8.0` after `2` retries (or `hitl_strict_gate` policy) → escalates to `needs_review` for human override (`Force Approve`, `Regenerate`, or `Convert to Diagram`) without stalling other scenes.

## Consequences
- **Positive:** Prevents silent Compute Unit burn on bad scenes while preserving hands-free generation and full human override authority.
- **Negative:** Adds ~1–3 seconds and one Flash vision call per keyframe.
