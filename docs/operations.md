# Operations runbook

Production operations for a self-hosted Craftsman: backup/restore, scaling,
webhooks, migrations, and quota administration. Deployment itself is covered by
`docker-compose.prod.yml` (single host) and `deploy/helm/craftsman` (Kubernetes)
— see the README's "Production deployment" section.

## Backup & restore

### Postgres — the only state that matters

Everything durable lives in Postgres: leads, campaigns, messages, mailbox
credentials (Fernet-encrypted), webhook endpoints/deliveries, the audit log,
Alembic's schema stamp. Back it up like it's the whole product, because it is.

```bash
# backup (custom format: compressed, restorable table-by-table)
pg_dump -Fc -h <host> -U craftsman craftsman > craftsman-$(date +%F).dump

# restore into a fresh database
createdb -h <host> -U craftsman craftsman
pg_restore -h <host> -U craftsman -d craftsman --no-owner craftsman-YYYY-MM-DD.dump
```

**pgvector note:** the dump contains `CREATE EXTENSION vector` and the restore
target must have the pgvector extension *available* (the
`pgvector/pgvector:pg16` image, or `apt install postgresql-16-pgvector`).
Restoring into a vanilla Postgres fails at the embedding columns. Restoring as
a non-superuser may require creating the extension first:
`CREATE EXTENSION IF NOT EXISTS vector;`.

**Also back up `CRAFTSMAN_SECRET_KEY`** (wherever you keep secrets). A database
restore without that key leaves every mailbox password and webhook secret
undecryptable — you would have to re-enter mailbox credentials and rotate every
webhook secret.

Run a periodic dump from cron/K8s CronJob; test restores quarterly into a
scratch database (a backup you have never restored is a hope, not a backup).

### Redis — rebuildable, and what you lose

Redis holds only coordination state. It needs **no backup**; on loss, restart
the stack against an empty Redis. What is actually lost:

- **Queued Celery tasks** — anything enqueued but not yet executed. The beat
  sweeps re-derive almost all of it: `sequencer_tick` re-enqueues due
  enrollments, `redrive_unsent` re-drives stuck send claims, `poll_inboxes`
  re-reads mailboxes. Webhook deliveries that were enqueued but never attempted
  remain visible as `pending` rows in `webhook_deliveries` (re-drive by POSTing
  `/webhooks/{id}/test` or re-triggering the event; there is deliberately no
  automatic pending-sweep yet).
- **Rate-limit token buckets** (per-mailbox 45–90s spacing, per-domain
  interval) — they refill immediately; worst case a send happens slightly
  sooner after restart than the spacing would have allowed.
- **SSO `jti` tombstones** (one-time login-code replay guards) — a code minted
  before the flush could in theory be replayed within its short TTL; codes are
  short-lived and single-purpose, and sessions already issued are unaffected.
- **Prometheus counters served from Redis** — gauges rebuild from Postgres on
  the next scrape; counter-style series restart at zero.

## Horizontal worker scaling

Scaling workers up is safe **today**, by design:

- Workers are stateless; every task loads its aggregate by id.
- Cross-worker coordination is Postgres row locks (atomic
  campaign/org send-cap reserve/release, send-claim unique indexes) and Redis
  token buckets — never process memory.
- Tenancy contexts are derived from the loaded row (`_org_task_scope`), so a
  task lands in the right org no matter which worker runs it.

Scale by raising worker replicas (`worker.replicas` in Helm,
`--scale worker=N` in compose) or by splitting queues onto dedicated workers
(`-Q send` vs `-Q research,enrich` — the send queue benefits from isolation
since it holds the rate-limit sleeps). Two rules:

1. **Exactly one `beat`.** It is the schedule's single writer; N beats fire
   every periodic task N times.
2. Keep `task_acks_late` semantics in mind: a worker killed mid-task causes a
   redelivery, which the idempotency claims absorb (that is what they are for).

## Webhook operations

- Register endpoints with `POST /webhooks` (admin). The secret is shown once;
  receivers verify `X-Craftsman-Signature-256: sha256=<hmac>` — HMAC-SHA256 of
  the raw body with that secret (same scheme Craftsman verifies inbound from
  Cal.com).
- URLs must be https and pass the SSRF guard at registration **and again at
  every delivery** (DNS changes are re-checked, not trusted).
- Delivery retries: exponential backoff 30s→1h, up to `WEBHOOK_MAX_ATTEMPTS`
  (default 8). Terminal failures mark the delivery `failed` AND write a
  `dead_letters` row.
- Inspect per-endpoint history: `GET /webhooks/{id}/deliveries` (last 50).
  Smoke-test plumbing end-to-end: `POST /webhooks/{id}/test` sends a signed
  `ping`.
- A misbehaving receiver is not your outage: delivery runs on the `settle`
  queue and never blocks sends, classification, or booking.

## Migration policy

- Schema is Alembic-managed; the API runs `alembic upgrade head` in its startup
  lifespan, so single-node deployments upgrade by restarting with a new image.
- **Multi-replica note:** with more than one API replica, startup migrations
  race. Run migrations as a one-shot step instead (K8s Job / init container /
  `docker compose run --rm api alembic upgrade head`) before rolling the
  replicas. Migrations are forward-only in production; downgrade paths exist
  and are tested, but treat them as a development tool.
- A model change without a migration fails CI (`tests/e2e/test_migrations.py`
  runs `alembic check` — the no-drift guard).
- Take a backup before upgrading across a milestone boundary. Boring, correct.

## Audit log export & retention

- `GET /audit/export` (admin key, org-scoped automatically) streams the org's
  audit log as NDJSON, oldest first; `?since=2026-07-01T00:00:00Z` for
  incremental pulls into your SIEM.
- Retention: `AUDIT_RETENTION_DAYS` (default `0` = keep forever). When set >0,
  the daily reset sweep deletes each org's audit rows older than the cutoff —
  per-org, inside the tenancy boundary. Export before you shorten it.

## Quota administration

Per-org quotas (daily send cap, mailbox count, enrichment budget) are **data on
the org row**, set by the instance operator — tenants can read theirs
(`GET /org`) but never write:

```bash
python -m craftsman.manage_org list
python -m craftsman.manage_org create --name "Acme" --slug acme
python -m craftsman.manage_org set-quota --org acme --daily-send-cap 500 --max-mailboxes 10
python -m craftsman.manage_org set-quota --org acme --enrichment-daily-budget 200
```

`NULL` means unlimited (the single-tenant self-hoster default). Counters reset
at the daily sweep; exhausted enrichment budget degrades to verify-only —
never an error.
