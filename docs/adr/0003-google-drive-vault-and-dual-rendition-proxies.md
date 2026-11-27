# ADR-0003: Google Drive (`drive.file`) as Media Vault & Dual-Rendition Proxies

- **Status:** Accepted
- **Date:** 2026-11-27

## Context
Streaming 1080p and 4K video clips through a small laptop or VPS backend exhausts RAM, triggers gateway timeouts, and incurs recurring cloud object storage costs. At the same time, raw 4K clips are too heavy for smooth scrubbing in a browser timeline editor.

## Decision
- Use the user's **Google Drive (2 TB)** via least-privilege **`drive.file` OAuth scope** as the permanent source-of-truth media vault (`KinetiqStudio/{slug}_{id}/`), attaching `appProperties` (`project_id`, `scene_id`, `kind`, `revision`, `sha256`) to every Drive file so the database can be rebuilt from Drive alone.
- Workers upload **directly to Drive** via Drive API v3 Resumable Uploads in **8 MiB (`32 × 256 KiB`)** chunks, carrying the `Authorization: Bearer` header on every chunk `PUT` and recovering from drops via `HTTP 308` byte-offset checks.
- Store **two renditions** for every video clip: `clips/master/` (native 1080p/4K) for final export and `clips/proxy/` (`540p` fast-seek MP4) for `< 150 ms` browser editing and OPFS caching.
- Implement a clean `StorageProvider` interface with `LocalVaultProvider` (supporting the exact same `HTTP 308` chunked upload contract on disk) for offline Phase 0 testing.

## Consequences
- **Positive:** \$0 media storage fees, zero backend upload bandwidth bottleneck, crash-proof persistence, and smooth browser editing.
- **Negative:** Bound by Google Drive's ~750 GB/day upload quota and rate limits; mitigated via exponential backoff and optional post-export master pruning.
