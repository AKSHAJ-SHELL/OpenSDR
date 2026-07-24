# Craftsman

**This is currently in alpha stage no real inboxes were sent and commands are mocked**

**An open-source AI SDR with a learning loop the funded versions don't have.**

Craftsman runs end-to-end outbound: leads in → researched, personalized sequences out → replies classified → a validated reply draft waiting for your click, and human handoff the moment someone is interested. It never free-writes an email, never auto-replies unless you enable **Guarded Autopilot** — whose replies are template-constrained, validated, and policy-escalated — and it gets measurably better with every send via a Thompson-sampling bandit over copy variants.

```
docker compose up
```

That's the whole deployment. Bring your own LLM: Anthropic or OpenAI keys, or run Ollama locally for $0 marginal cost — `LLM_PROVIDER=anthropic | openai | ollama`.

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

- **Auto-reply to a human — unless you explicitly enable Guarded Autopilot.** By default (Copilot, M4.1): interested replies stop the sequence, ping Slack, and a validated draft waits in the inbox; **a human click is the only path to dispatch**. Guarded Autopilot (M4.4) is opt-in per campaign, requires an admin-scoped API call, and may auto-send **only** template-constrained, validator-passed replies for three deterministic intents (interested → your scheduling link, "send me info" → your approved one-pager, timing objection → a follow-up offer) at classifier confidence ≥ 0.9, inside business hours, with no escalation-rule match — and **at most one auto-reply per thread, ever** (a reply to the auto-reply always escalates; that limit is hardcoded, not a knob). Everything else — pricing, competitor, hostile, legal, ambiguous — goes to a human. A free-text AI reply remains impossible by construction. Kill switch: `POST /campaigns/{id}/autopilot/disable`, instant.
- **Send to unverified emails.** Syntax → MX → optional SMTP handshake; unverifiable addresses never enroll. Bounce risk is the #1 deliverability killer.
- **Batch-blast.** Sends land inside the lead's local business hours (9:00–16:30, jittered), one per mailbox per 45–90s, with warmup ramps and per-campaign caps.
- **Automate LinkedIn.** Bot-driven LinkedIn outreach violates LinkedIn's terms and gets accounts restricted; the products that do it anyway are gambling with *your* account. Craftsman's LinkedIn and call steps are **assisted**: it writes the message, validates every claim, and queues a task — a human clicks send. No browser automation, no session cookies, ever. Email is the only autonomous channel.

## Architecture

FastAPI + Postgres/pgvector + Celery/Redis. One `docker compose up` brings up the API, workers, beat scheduler, Next.js dashboard (`web/`), and a [Mailpit](https://mailpit.axllent.org/) sandbox for testing without touching real inboxes.

Pipeline per lead: ingest → verify email → enrich (optional, BYO provider keys) → embed + ICP-score → research (cached 30d per company) → enroll → per step: bandit picks variant → copywriter fills slots → validator gates → send engine dispatches in-window → inbox poller catches the reply → classifier updates state → bandit posterior updates → human notified if interested.

Key modules:

| Path | What it is |
|---|---|
| `craftsman/sequencer/machine.py` | Pure-function state machine (the transition table is data) |
| `craftsman/copywriter/validator.py` | The deterministic anti-hallucination gate |
| `craftsman/research/agent.py` | Grounded research briefs, cached per company |
| `craftsman/bandit/thompson.py` | The learning loop |
| `craftsman/inbox/pipeline.py` | Reply → classify → state → bandit → handoff |
| `craftsman/sender/smtp.py` | Suppression/cap/warmup/rate-limit checks + compliant headers |
| `craftsman/llm/` | Provider-agnostic structured-output client (Claude, OpenAI/compatible, Ollama, mock for tests) |
| `web/` | Next.js dashboard (Gojiberry-style agent UI) |

## Deliverability (read this before you send)

The hard part of cold outbound isn't the AI — it's landing in the inbox. Three things
decide that, and the dashboard's **Deliverability** page checks all three per mailbox by
live DNS lookup, with copy-paste values for whatever's missing:

- **Send from a subdomain, never your primary domain.** Put outbound on a dedicated
  sender like `outbound.yourco.com` with its own auth records. A bad reputation stretch
  then stays contained and never poisons the domain your real mail and website depend on.
  The page flags a mailbox whose domain looks primary.
- **SPF / DKIM / DMARC.** SPF and DMARC get concrete recommended records (DMARC is
  generated for you; SPF is a template with your provider's `include:`). DKIM keys are
  minted by your sending provider, so we verify yours (set the selector on the mailbox, or
  we probe the common ones) but never fabricate a key. A resolver hiccup reads as
  "couldn't check", never a false "missing".
- **Warm up slowly.** Every new mailbox ramps automatically — 10 → 20 → 30 → 40 → full
  daily limit, one stage per calendar day. The page shows where each mailbox is on that
  ramp and how much of today's cap is spent. This is enforced in the send path
  (`craftsman/sender/warmup.py`), not just advice.

None of this is optional-nice-to-have: bounces and unauthenticated mail are the fastest
way to burn a domain, which is why verification gates enrollment and warmup gates volume.

## Quickstart

```bash
cp .env.example .env
# pick an LLM: LLM_PROVIDER=ollama (local, no key), or anthropic/openai with the
# matching API key set. OPENAI_BASE_URL accepts any OpenAI-compatible endpoint.
# Then generate CRAFTSMAN_SECRET_KEY:
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
```

Then build the campaign in the dashboard: **Campaigns → New campaign** (ICP, value
prop, sender persona, step cadence), add at least one skeleton variant per step in the
builder — placeholders are validated as you type and previewed with sample fills — then
**dry-run before you activate**: the builder's Dry run panel routes the real pipeline
(research → variant pick → slot-fill → validator) for your top-scoring sample leads and
delivers to Mailpit only, never a real inbox. You see the exact emails and validator
verdicts the live campaign would produce; Activate asks for an explicit override if no
dry-run has completed. Everything is equally scriptable against the API (`/docs`,
key-gated); once a variant's bandit arm has recorded trials its skeleton is frozen —
clone it as a new variant instead of rewriting measured history.

**Leads** (dashboard → **Leads**) is where day-to-day operators live. Import a CSV
inline, filter by status or a minimum ICP score, and read *why* a lead scored what it
did: hover the score bar for the honest breakdown — `cosine × 0.7` (semantic fit to your
ICP) plus `rule × 0.3` (title seniority), the keyword that matched, and which campaign's
activation produced the score. Leads scored before component tracking shipped say so
rather than inventing a breakdown. Per-lead **Suppress** stops all mail while keeping the
row (needs `operate`); **Erase** is the irreversible GDPR delete and needs `admin` —
which the dashboard key deliberately lacks by default, so the button explains the 403
instead of failing silently. Erase from an admin key, or widen the dashboard key's scope
only if you accept the blast radius.

**Enrichment (bring your own keys).** After a lead verifies, an optional provider chain
(Apollo, Hunter — your accounts, your keys) fills what your CSV left blank: title,
seniority, phone, LinkedIn, company industry/size/description. Set
`ENRICHMENT_PROVIDERS=apollo,hunter` (precedence order) plus the matching
`APOLLO_API_KEY`/`HUNTER_API_KEY`; leave it unset and the pipeline is verify-only. Two
promises: a dead provider never blocks verification, and a provider value never
overwrites data you supplied — every provider answer is recorded per-field in a
provenance table (who said what, when, at what confidence; click a lead's **Source** in
the dashboard to see it), but your CSV wins where it already had an answer. There is no
proprietary contact database here and we don't pretend otherwise: results come from
*your* provider accounts and are labeled with their source.

**Find leads (bring your own keys).** *Craftsman → **Find leads*** searches your own
provider account for people matching an ICP query + filters (titles, seniorities,
industries, locations, employee ranges) and previews each candidate against the **same
import gate a CSV faces** — syntax, dedupe, suppression — labeling every row `new` /
`duplicate` / `suppressed` / `no usable email` before you import a thing. Set
`LEAD_SOURCE_PROVIDERS=apollo,webhook` plus the matching `APOLLO_API_KEY` /
`LEAD_SOURCE_WEBHOOK_URL`; unset means the page shows a configure-me state, never fake
data. Sourced leads get **zero shortcuts**: the gate re-runs on import (a hand-forged
request can't smuggle a suppressed address in), and they land as `status="new"`, queued
for the same verify → enrich pipeline. Two honesty notes on the Apollo connector: people
whose email is **credit-locked** (Apollo returns a valid-looking placeholder) are
**dropped, not imported and not faked** — we never silently spend your unlock credits;
and the webhook source only fetches **https** URLs through the same SSRF guard the
research fetcher uses. Respect each provider's terms and credit model.

**Intent signals (bring your own sources).** Craftsman can prioritize by *intent* — funding,
hiring, tech-stack moves — not just static fit. Optional, independently-disableable
collectors watch your own sources (`SIGNAL_COLLECTORS=homepage_diff,careers_diff,rss_funding`
+ `SIGNAL_FUNDING_RSS_URL`): careers/homepage **diffing** through the same SSRF guard, and an
RSS/news **funding** watch. Each observation attaches to a company and feeds a **decaying**
signal component of the ICP score (`SIGNAL_HALF_LIFE_DAYS`, default 30 — a fresh funding
round counts full, a month-old one counts half). The scoring is honest about the switch:
a lead whose company has **no** signals is scored exactly as before (`0.7·cosine +
0.3·rule`); only leads *with* signals use the 3-way blend (`0.6·cosine + 0.25·rule +
0.15·signal`). So configuring signals never silently re-ranks the leads you already have —
it only adds lift where intent actually exists. Per-campaign **signal rules** (on the
campaign page) decide what a signal *does*: `boost_score`, `notify` (Slack), or `enroll`.
`enroll` is deliberate autonomy and is **off until you create the rule** — and even then
it's guarded (verified + above-threshold + not-already-enrolled) and lands the lead in
`queued`, so research and the anti-hallucination validator **still run** on every
auto-enrolled lead. Nothing is skipped. Collectors read *your* watched sources — there's
no proprietary intent database — so respect each source's robots/ToS and feed terms.

**Multi-channel sequences (assisted by design).** Sequence steps have a channel:
`email` (autonomous, exactly as before — existing campaigns are untouched),
`linkedin_task`, or `call_task`. Task steps don't send anything. Instead the pipeline
runs as usual (research → fill → **the same validator**) and queues the result on the
dashboard's **Tasks** page for a human: LinkedIn steps produce a connection-note-sized
message (≤ 280 chars rendered, every claim grounded in the research brief) with a copy
button and a deep link to the profile; call steps produce a structured **call brief** —
opener, up to two pain hypotheses drawn from the brief, objection notes — deliberately
*not* a script, plus a `tel:` link (or optional BYO-Twilio click-to-dial that rings
**your** phone first, then connects the lead — Craftsman never robocalls a prospect).
Completing or skipping the task advances the sequence through the normal state machine;
an undone task **holds** the sequence and shows as overdue (per-step opt-in
`skip_on_expire` advances it after the due window instead, `TOUCH_TASK_DUE_DAYS`,
default 3 business days). Replies, bounces, and unsubscribes still route normally while
a task is open — an open task is cancelled the moment the lead answers or opts out, so
you never touch someone who already replied. Task completions are recorded in the
per-lead **timeline** (click any lead), not in the bandit: a completed touch is not a
reply, so it never moves copy posteriors. And to say it once more for the people
shopping for a growth hack: **there is no LinkedIn automation here and there never will
be** — that is a feature of the honest version, not a missing one.

**Review** (dashboard → **Review**) is where the agent hands off. Two things wait here:
*blocked copy* (the validator rejected both generation attempts, so the enrollment is
stuck — you get the validator's errors, the rejected slot text, and Retry / Skip step /
Kill) and *uncertain classifications* (a reply the classifier scored below threshold, so
no state change happened — you see the reply and Approve the model's label or override
it). Approving applies the label at full confidence and clears the item **without**
re-driving the sequence, so a human call never silently advances the campaign.

> **⚠️ Exposure warning.** The API and dashboard bind to `localhost` by default.
> **Do not expose port 8000 or 3000 to the internet.** Every API route now requires a
> scoped key and the dashboard requires a login, but that is your *only* wall between a
> public port and your lead database + an open mail cannon. If you must reach it remotely,
> put it behind a VPN or an authenticating reverse proxy with TLS — never a raw port.

Local app processes (Ollama on the host): infra via `docker compose up -d postgres redis mailpit`, then:

```bash
pip install -e ".[dev]"
uvicorn craftsman.api.app:app --host 0.0.0.0 --port 8000 --reload
celery -A craftsman.workers.celery_app worker -Q research,generate,send,inbox,enrich,settle -l info
celery -A craftsman.workers.celery_app beat -l info
cd web && npm install && npm run dev   # http://localhost:3000
```

> **`--reload` matters in dev.** `next dev` hot-reloads the dashboard, but uvicorn and
> Celery do not reload on their own. Without it you can end up with a new dashboard
> talking to an API running yesterday's code — new endpoints 404/405 and pages fail in
> confusing ways. **Celery workers never auto-reload**: restart them by hand after
> changing anything under `craftsman/workers/`, `research/`, `copywriter/`, or `sender/`.

## Authentication

Every API endpoint requires an `Authorization: Bearer <key>` header, except `/health`,
the RFC 8058 one-click unsubscribe at `/u/{token}` (which must stay anonymous), the
HMAC-gated Cal.com webhook, and the two OIDC SSO routes (`/auth/oidc/login|callback` —
a browser mid-login cannot hold a key; they are gated by signed state and full id_token
validation instead, and 503 until SSO is configured). Keys are random `csk_…` tokens,
stored only as a SHA-256 hash, and carry hierarchical scopes:

| Scope | Grants | Example routes |
|---|---|---|
| `read` | read-only | list leads/campaigns/inbox, analytics, bandit posteriors, `/docs` |
| `operate` | read + run the pipeline | import leads, create/activate/pause campaigns, reclassify |
| `admin` | operate + manage secrets | mailboxes, `DELETE /leads/{id}/erase`, `/keys` |

`admin` implies `operate` implies `read`. Create keys with
`python -m craftsman.create_key --name <n> --scopes <...> [--org <slug>]` or, once you
have an admin key, `POST /keys`. Revoke with `DELETE /keys/{id}`. `/docs` and
`/openapi.json` are gated behind `read`, so the API surface isn't enumerable without a key.

**Orgs (multi-tenancy, M5.1).** Every key, lead, campaign, mailbox, and suppression
entry belongs to exactly one org, enforced centrally at the ORM session layer — a
forgotten filter in any router fails closed instead of leaking, and the adversarial
suite (`tests/adversarial/test_tenancy_isolation.py`) proves the boundary. A fresh
install has one org (`default`); single-tenant self-hosters never need to think about
it. Hosts create orgs and set per-org quotas (daily sends, mailbox count, enrichment
budget) with `python -m craftsman.manage_org`; suppression is per-org with an optional
cross-org overlay list (off by default).

**Users, roles & SSO (M5.1b).** The dashboard signs in real users (`owner` /
`operator` / `viewer` → `admin` / `operate` / `read`), managed at `/settings/users`
and enforced both in the dashboard proxy and by the API's own scopes. Generic OIDC SSO
(Google/Okta/Entra) is off until `OIDC_DISCOVERY_URL` + client credentials are set;
unknown subjects are rejected unless `OIDC_AUTO_PROVISION` is enabled (viewer role).
The legacy single-admin password (`DASHBOARD_PASSWORD_HASH`) still works as break-glass
owner access. The dashboard calls the API with a server-held key routed through a
session-gated proxy — neither the key nor any id_token is ever exposed to the browser.

## Production deployment

Two starting points ship in-repo (M5.4):

- **Single host:** `docker compose -f docker-compose.prod.yml up -d` — the dev
  stack minus Mailpit, with healthchecks and required-env fail-fast. Sending
  requires real mailboxes (`POST /mailboxes` with real SMTP credentials).
- **Kubernetes:** `deploy/helm/craftsman` — Deployments for api/worker/beat/web,
  Services, an optional API Ingress, env from a pre-created Secret. Postgres and
  Redis are deliberately **external** (managed services or your own
  StatefulSets); the chart never runs databases. Validate with
  `helm template deploy/helm/craftsman`.

Both bind/serve plain HTTP — **TLS termination is yours** (reverse proxy or
Ingress), and the exposure warning above applies doubly in production: the API
holds mailbox credentials and can send mail as you. Runbooks — backup/restore
(pg_dump + pgvector notes; Redis is rebuildable), horizontal worker scaling,
webhook operations, migration policy for multi-replica, audit export/retention,
and per-org quota administration — live in [`docs/operations.md`](docs/operations.md).
An honest SOC 2 **alignment** (not certification) map is in
[`docs/soc2-alignment.md`](docs/soc2-alignment.md).

Outbound webhooks (`POST /webhooks`, admin) push `lead.status_changed`,
`reply.received`, `meeting.updated`, `autopilot.sent`, and `escalation.fired`
to your https endpoints, signed with HMAC-SHA256 over the raw body
(`X-Craftsman-Signature-256` — the same scheme Craftsman verifies inbound from
Cal.com). Endpoint URLs pass the SSRF guard at registration and again at every
delivery; retries back off exponentially up to `WEBHOOK_MAX_ATTEMPTS`, then
dead-letter.

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
