# ADR-0002: Durable Database Job Queue & Transactional Outbox SSE

- **Status:** Accepted
- **Date:** 2026-11-27

## Context
Colab workers can disconnect mid-render, laptop browsers can sleep, and Phase 0 setups should run cleanly with minimal infrastructure before scaling to PostgreSQL + Redis on a VPS. In-memory queues lose jobs on restart, while separate Celery/RabbitMQ brokers add operational overhead without providing DAG dependency tracking across `tts → align → keyframe → qa → i2v → upscale`.

## Decision
- Store all jobs in the relational `jobs` table with `depends_on` DAG prerequisites, unique `idempotency_key` deduplication, `lease_until` (300 s renewable leases), and exponential backoff retries (`30s → 120s → 480s → dead`).
- Use `SELECT ... FOR UPDATE SKIP LOCKED` on PostgreSQL and atomic conditional compare-and-set updates on Phase 0 SQLite.
- Persist domain events inside an `events` transactional outbox table in the same DB transaction as state mutations, streaming them over SSE (`/v1/events`) with `Last-Event-ID` replay (fanned out via Redis Pub/Sub when `REDIS_URL` is configured).

## Consequences
- **Positive:** Zero lost jobs across restarts, guaranteed idempotent retries, full SQL observability, and reliable browser reconnection after Wi-Fi drops.
- **Negative:** Requires a periodic orchestrator sweep (`30 s` interval) to reclaim expired leases and advance downstream DAG jobs, which adds minimal latency between stages.
