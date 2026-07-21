# Craftsman

**This is currently in alpha stage no real inboxes were sent and commands are mocked**

**An open-source AI SDR with a learning loop the funded versions don't have.**

Craftsman runs end-to-end outbound: leads in → researched, personalized sequences out → replies classified → human handoff the moment someone is interested. It never free-writes an email, never auto-replies to a human, and it gets measurably better with every send via a Thompson-sampling bandit over copy variants.

```
docker compose up
```

That's the whole deployment. Bring your own Anthropic API key (or run the Ollama fallback for $0 marginal cost).

## Why this exists

Commercial "AI SDR employees" are, mechanically: a state machine, a scraper, two constrained LLM calls, SMTP plumbing, and (in Craftsman's case) a contextual bandit. The genuinely hard parts of outbound at scale — deliverability, warm-up networks, IP reputation — have nothing to do with AI. This repo is the honest version: every mechanism is inspectable, and the one feature the funded products don't ship openly — **a closed learning loop that optimizes measured reply rate** — is ~120 lines of Thompson sampling you can read in [`craftsman/bandit/thompson.py`](craftsman/bandit/thompson.py).

## Anti-hallucination by construction

The LLM never writes an email. It fills **typed slots** in a fixed skeleton, from a **structured research brief** whose every claim must be backed by verbatim source text. Then a deterministic validator gates the output:

1. Every proper noun in the fill must appear in the research brief or campaign config (fuzzy match ≥ 0.9) — and every **number must match exactly** after normalization ($4M counts as $4,000,000, but never as $40M; 12% needs a percent source). Numbers are never fuzzy-matched. Fail either check and the fill is rejected.
2. Banned-phrase list (every "I hope this finds you well"-style tell).
3. Length caps: subject ≤ 7 words, body ≤ 90 words.
4. Reading grade ≤ 8.

Rejected fills retry once with the validator errors appended; a second failure goes to a human review queue. The rejection rate is a first-class dashboard metric — public proof the gate is real.

## The learning loop

Each copy variant per sequence step is a bandit arm with a Beta posterior over reply rate. At send time the bandit samples each posterior and picks the winner; classified human replies update the posteriors; arms that fall far behind auto-deactivate. Reply rates are low (2–8%), so posteriors stay honest about uncertainty — exactly what Thompson sampling is for.

Run the simulator and watch it converge with zero real emails sent:

```
python -m craftsman.bandit.simulator
```

```
arm           true rate  traffic  posterior mean
pain_led      0.060      372      0.0775
trigger_led   0.020      45       0.0213
question_led  0.035      83       0.0353

best arm captured 74% of traffic
```

The dashboard renders the Beta PDFs converging live (`Bandit` page, with an interactive demo mode that needs no data).

## What it will never do

- **Auto-reply to an interested human.** Interested replies stop the sequence and ping Slack. A human takes over. This is a feature, not a gap — it's also exactly where AI SDRs get caught.
- **Send to unverified emails.** Syntax → MX → optional SMTP handshake; unverifiable addresses never enroll. Bounce risk is the #1 deliverability killer.
- **Batch-blast.** Sends land inside the lead's local business hours (9:00–16:30, jittered), one per mailbox per 45–90s, with warmup ramps and per-campaign caps.

## Architecture

FastAPI + Postgres/pgvector + Celery/Redis. One `docker compose up` brings up the API, workers, beat scheduler, Next.js dashboard (`web/`), and a [Mailpit](https://mailpit.axllent.org/) sandbox for testing without touching real inboxes.

Pipeline per lead: ingest → verify email → embed + ICP-score → research (cached 30d per company) → enroll → per step: bandit picks variant → copywriter fills slots → validator gates → send engine dispatches in-window → inbox poller catches the reply → classifier updates state → bandit posterior updates → human notified if interested.

Key modules:

| Path | What it is |
|---|---|
| `craftsman/sequencer/machine.py` | Pure-function state machine (the transition table is data) |
| `craftsman/copywriter/validator.py` | The deterministic anti-hallucination gate |
| `craftsman/research/agent.py` | Grounded research briefs, cached per company |
| `craftsman/bandit/thompson.py` | The learning loop |
| `craftsman/inbox/pipeline.py` | Reply → classify → state → bandit → handoff |
| `craftsman/sender/smtp.py` | Suppression/cap/warmup/rate-limit checks + compliant headers |
| `craftsman/llm/` | Provider-agnostic structured-output client (Claude default, Ollama fallback, mock for tests) |
| `web/` | Next.js dashboard (Gojiberry-style agent UI) |

## Quickstart

```bash
cp .env.example .env
# set ANTHROPIC_API_KEY (or LLM_PROVIDER=ollama) and generate CRAFTSMAN_SECRET_KEY:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# --- auth setup (required) ---
# 1. bootstrap an admin API key (needs Postgres up: docker compose up -d postgres)
python -m craftsman.create_key --name bootstrap --scopes admin
# 2. a key for the dashboard to call the API with (kept server-side):
python -m craftsman.create_key --name dashboard --scopes read operate
# 3. a login password hash + a cookie-signing secret:
node web/scripts/hash-password.mjs 'choose-a-strong-password'
python -c "import secrets; print(secrets.token_urlsafe(32))"
# put the dashboard key, password hash, and secret into .env
# (CRAFTSMAN_API_KEY, DASHBOARD_PASSWORD_HASH, DASHBOARD_SESSION_SECRET)

docker compose up -d
# API:       http://localhost:8000   (every route needs Authorization: Bearer <key>)
# Dashboard: http://localhost:3000   (sign in with your password)
# Mailpit:   http://localhost:8025

# import leads (Bearer key required)
curl -F "file=@leads.csv" -H "Authorization: Bearer $KEY" http://localhost:8000/leads/import

# create a campaign, add variants, activate — see /docs (also key-gated)
```

> **⚠️ Exposure warning.** The API and dashboard bind to `localhost` by default.
> **Do not expose port 8000 or 3000 to the internet.** Every API route now requires a
> scoped key and the dashboard requires a login, but that is your *only* wall between a
> public port and your lead database + an open mail cannon. If you must reach it remotely,
> put it behind a VPN or an authenticating reverse proxy with TLS — never a raw port.

Local app processes (Ollama on the host): infra via `docker compose up -d postgres redis mailpit`, then:

```bash
pip install -e ".[dev]"
uvicorn craftsman.api.app:app --host 0.0.0.0 --port 8000
celery -A craftsman.workers.celery_app worker -Q research,generate,send,inbox,enrich,settle -l info
celery -A craftsman.workers.celery_app beat -l info
cd web && npm install && npm run dev   # http://localhost:3000
```

## Authentication

Every API endpoint requires an `Authorization: Bearer <key>` header, except `/health`
and the RFC 8058 one-click unsubscribe at `/u/{token}` (which must stay anonymous). Keys
are random `csk_…` tokens, stored only as a SHA-256 hash, and carry hierarchical scopes:

| Scope | Grants | Example routes |
|---|---|---|
| `read` | read-only | list leads/campaigns/inbox, analytics, bandit posteriors, `/docs` |
| `operate` | read + run the pipeline | import leads, create/activate/pause campaigns, reclassify |
| `admin` | operate + manage secrets | mailboxes, `DELETE /leads/{id}/erase`, `/keys` |

`admin` implies `operate` implies `read`. Create keys with
`python -m craftsman.create_key --name <n> --scopes <...>` or, once you have an admin key,
`POST /keys`. Revoke with `DELETE /keys/{id}`. The dashboard signs in a single admin
(password hashed with scrypt in `DASHBOARD_PASSWORD_HASH`) and calls the API with a
server-held key routed through a session-gated proxy — the key is never exposed to the
browser. `/docs` and `/openapi.json` are gated behind `read`, so the API surface isn't
enumerable without a key.

## Testing

```bash
pytest             # state machine, validator, bandit math, scheduling, copywriter retry,
                   # full Postgres integration flow, and the auth suite (unit + adversarial)
python scripts/eval_classifier.py   # live LLM eval vs 32 adversarial reply fixtures
python scripts/seed_demo.py         # populate the dashboard with demo data
```

The unit layer is pure functions and a mock LLM — no API key, no network. Integration tests
(including the auth flow, a fail-closed route audit that 401s every non-allowlisted endpoint,
and the Alembic migration round-trip) run against real Postgres and skip cleanly if it's
absent. `tests/adversarial/` holds the predict-then-run attack cases: fuzzed tokens, scheme
confusion, revoked-key reuse, query-string tokens. The classifier eval includes the replies
that matter: the OOO that mentions interest, the polite unsubscribe, the helpdesk auto-ack.

**Schema changes ship a migration.** The database schema is managed by Alembic
(`craftsman/migrations/`); the API runs `alembic upgrade head` on startup, so
`docker compose up` stays one command. When you change a model, generate a migration
(`alembic revision --autogenerate -m "what changed"`) and review it — a CI check fails if
the models and migrations drift apart.

## Compliance

One-click unsubscribe (RFC 8058 `List-Unsubscribe-Post`), physical address in every footer (CAN-SPAM), permanent suppression list checked at generation *and* send time, GDPR mode that blocks EU-TLD enrollment for non-opt-in lists, and `DELETE /leads/{id}/erase` for data-subject requests. Deliverability guardrails (verification, warmup ramps, rate jitter, bounce-driven mailbox degradation) are on by default so self-hosters don't torch their domains.

**What erasure actually deletes:** the lead row, enrollments, all messages (including the
prospect's reply text), review-queue items, and unsubscribe tokens; the cached company
research brief is scrubbed of the person's name and email (company facts stay). Audit-log
rows are kept but anonymized — the enrollment link is severed and identifiers are scrubbed,
so they no longer relate to an identifiable person. The suppression entry deliberately
survives: it is the do-not-contact record that keeps the address from ever being re-imported.
Queued background jobs reference database IDs only, so a job queued before erasure is a
verified no-op after it.

## License

AGPL-3.0. If you host a modified Craftsman as a service, you share your changes. That's the point.
