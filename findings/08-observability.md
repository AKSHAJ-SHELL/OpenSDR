## F-08 — Observability & operational recovery (D2): implemented

**Severity:** medium (was a gap) → resolved
**README claims affected:** #5 (rejection rate is a dashboard metric) — now a real metric
**Status:** fixed

### Was
No structured logging/metrics; no dead-letter for exhausted Celery retries; `error`
enrollments and crash-orphaned claims were stuck forever.

### Shipped (four phases, four commits)
1. **Logging** — `core/logging.py`: JSON formatter on the root logger (API lifespan +
   Celery `setup_logging` signal); a contextvar `CorrelationFilter` injects
   lead/enrollment/message ids; task entrypoints bind them. `LOG_LEVEL` controls verbosity.
2. **Metrics** — `core/metrics.py`: a pull-based collector reads Postgres + Redis at scrape
   time (cross-process by design — a worker's in-process counters are invisible to the API
   that serves `/metrics`). Exposes enrollments-by-state, leads-by-status,
   replies-by-classification, outbound total, review-queue depth, Celery queue depths,
   send rejections (Redis counters written by the worker), and dead-letter count.
   `GET /metrics` is read-scope gated; degrades gracefully on a DB/Redis blip.
3. **Dead-letter** — `dead_letters` table (migration `0003`) fed by the `task_failure`
   signal (fires only on terminal failure; retries raise `Retry`, not a failure).
   `GET /dead-letters` (read scope). Recording is best-effort and never masks the error.
4. **Re-drive** — `sequencer/redrive.py`: `redrive_enrollment(retry|skip|kill)` re-enters
   the pipeline (audited); `POST /inbox/review/{id}/action` (operate scope) resolves a
   review item and applies the action; `redrive_unsent` beat sweep (migration `0004` adds
   `messages.created_at`) deletes crash-orphaned claims older than
   `redrive_unsent_after_minutes` (15), frees the campaign slot, and re-readies the lead —
   closing the "may-rarely-skip" residual from M0.6a.

### Verified
Logging JSON shape + context injection/isolation; metrics content vs a seeded DB + fake
Redis and read-gating; dead-letter row written with the right fields, swallow-on-error,
endpoint gating, metric reflects count; re-drive retry/skip/kill semantics + audit,
endpoint auth (operate) and 400/404 paths, and the sweep (old stuck claim swept + slot
released + re-armed; fresh claim and already-sent message left alone). Migrations 0003/0004
upgrade/downgrade/no-drift clean. 213 passed, 0 skipped.

### Notes / out of scope
- Tracing/OpenTelemetry, Grafana dashboards, and alert rules are deployment concerns, not
  built here. Metric labels are single-tenant (per D3/M5).
- `reset_daily_counters` and `redrive_unsent` route to the `settle` queue so the existing
  worker (`-Q …,settle`) consumes them; `reset_daily_counters` was previously unrouted
  (default queue) — a latent gap now avoided for `redrive_unsent`.
