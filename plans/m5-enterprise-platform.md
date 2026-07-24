# Plan — M5: Enterprise platform (G10, G12, G13) ⛔ Gate M5

> Session: 2026-07-23. Branch `m5-enterprise-platform` (on top of merged M4 / PR #6).
> Protocol: ROADMAP §9 — plan → **stop for approval** → implement → tests green (0 skipped)
> → docs → stop. Fresh-session M4 boundary audit (rule 5) running before any M5 code lands.

> **⛔ Gate M5 decisions (RECORDED 2026-07-24, before implementation):**
>
> - **Q1 → (a) approved:** per-org suppression + optional global overlay.
> - **Q2 → approved:** this session executes M5.1 + M5.3 + M5.4; M5.2 CRM sync
>   held for post-gate review of the isolation evidence (findings/13).
> - **Q3 → approved:** `authlib` dependency for OIDC.
> - **Q4 → done:** PR #6 merged after the fresh-session M4 audit returned
>   PASS-WITH-NOTES (findings/12); F-05 fixed as the first commit on this branch
>   (structural thread guard, migration 0015 — orgs migration shifts to 0016).
>   F-07 (draft-quality eval on the approved model) remains an open human item:
>   requires a valid Anthropic key.
>
> *(Original decision framing, kept for the record:)*
>
> - **Q1 — Suppression scope** (ROADMAP M5.1, "do not decide unilaterally"):
>   - **(a) Per-org suppression + optional global overlay (roadmap default, recommended).**
>     Each org has its own do-not-contact list; a self-hoster running shared infra can
>     additionally maintain a global overlay list that suppresses across all orgs.
>     Unsubscribes write to the lead's org list; the overlay is operator-curated
>     (or opt-in auto-propagation via config knob, default off). No cross-tenant
>     presence leak: org A can never observe that org B suppressed someone.
>   - (b) Global suppression always. Honors "unsubscribed is unsubscribed" but leaks
>     presence across tenants (org A can detect org B's contacts by import-time
>     suppression counts) — rejected by default for the isolation-first posture.
>   - (c) Per-org only, no overlay. Simplest, but shared-infra hosts lose the ability
>     to honor a person's unsubscribe across their tenants.
> - **Q2 — Session scope:** M5.1 alone, then stop at the gate (strict reading) — or
>   M5.1 + M5.3 + M5.4 with gate evidence at the end (M4 precedent), holding **M5.2
>   CRM sync** back because the gate text explicitly requires human review of the
>   isolation results *before CRM sync begins*. Recommended: the latter.
> - **Q3 — New dependency:** OIDC SSO needs `authlib` (server-side code flow in
>   FastAPI). TESTING.md §0 requires sign-off for any pyproject change.
> - **Q4 — PR #6:** was never actually merged (origin/main is at the M3 merge).
>   M5 must build on M4. Merge once the fresh-session M4 audit returns PASS.

## Goal

Craftsman becomes multi-tenant with real users, roles, SSO, per-org quotas, a
deliverability health suite, and operational surfaces (webhooks out, Helm,
backup/audit-export runbooks) — without weakening any existing guarantee. The
two design commitments are untouched: no free-written outbound text, no message
past the validator. Tenancy adds a third structural guarantee: **no query result
ever crosses an org boundary.**

## Current state (verified in code this session)

- Auth: scoped API keys (`read ⊂ operate ⊂ admin`), SHA-256 at rest
  (`api/auth.py`); dashboard scrypt password + session proxy holding one
  server-side key. No user model, no orgs — everything global (D3).
- 25 tables; global unique constraints that must become per-org:
  `leads.email`, `companies.domain`, `mailboxes.email`,
  `suppression_list.email` (PK).
- Send path chokepoints where org quotas slot in cleanly:
  `run_presend_checks` (suppression → campaign cap → mailbox cap → rate slot),
  `enrich_lead` (enrichment budget), `POST /mailboxes` (mailbox count).
- Workers are stateless; all coordination is Postgres row locks + Redis buckets;
  every Celery task loads its aggregate by id — org context can be derived
  server-side from the loaded row, never from task args.
- Migration head: `0014_autopilot`.

## Epics → commits (one branch, one PR; M5.2 deliberately absent — gate-blocked)

### Commit 1 — M5.1a Tenancy data model & central scoping

- Migration `0016_orgs` (0015 taken by the F-05 thread guard): `orgs` (id, name, slug unique, quota columns — see
  commit 3), `users` (id, org_id FK, email, display_name, role
  `owner|operator|viewer`, password_hash nullable, oidc_issuer/oidc_sub
  nullable + unique-together, disabled_at, timestamps; unique (org_id, email)).
- `org_id` FK NOT NULL added to every table that holds tenant data — the
  roadmap's six roots (campaigns, leads, mailboxes, suppression, signals,
  meetings) **plus** companies, api_keys, escalation_rules, unsubscribe_tokens,
  enrollments, messages, review_queue, reply_drafts, touch_tasks, dry_runs,
  audit_log, lead_enrichments, collector_state, signal_rules, dead_letters.
  *Deviation from the literal roadmap list, recorded here:* child tables get a
  denormalized org_id because the list endpoints (`/inbox`, `/inbox/review`,
  `/tasks`, `/dead-letters`, `/analytics/overview`) query them directly; join-
  derived scoping per-router is exactly the "per-router enforcement" the roadmap
  forbids. Denormalization is what makes central enforcement — and later
  optional Postgres RLS — possible.
- Backfill: migration creates the **default org** (slug `default`), stamps every
  existing row, then tightens to NOT NULL. Downgrade drops columns cleanly.
- Unique constraints rewritten: `(org_id, email)` on leads,
  `(org_id, domain)` on companies, `(org_id, email)` on mailboxes;
  suppression PK → `(org_id, email)`. Global overlay (per Q1a) is a separate
  `global_suppression` table (email PK, reason) checked alongside org
  suppression in `is_suppressed` — additive, like escalation defaults.
- **Central enforcement, not per-router:** a `current_org_id` contextvar +
  a SQLAlchemy `do_orm_execute` session event that (a) injects
  `with_loader_criteria(OrgScoped, org_id == current)` for every mapped class
  bearing org_id, and (b) **fails closed** — an ORM select against a tenant
  table with no org context raises `TenancyError` instead of returning
  cross-tenant rows. Flush-time guard stamps org_id on new objects and rejects
  writes to another org's rows. Workers enter org context from the row they
  load (`org_context(entity.org_id)`); beat-wide sweeps (tick, settle, poller,
  reset) iterate orgs explicitly.
- API keys become org-scoped: `api_keys.org_id`; `require_scope` sets the org
  context from the authenticated key for the request lifetime.
  `craftsman/create_key.py` takes `--org`. `/u/{token}` and the Cal.com webhook
  derive org from the token/meeting row (both stay unauthenticated by design).

### Commit 2 — M5.1b Users, RBAC & OIDC SSO

- Role → API-scope mapping is fixed data: `owner→admin`, `operator→operate`,
  `viewer→read`. Dashboard session carries user id + role; the session proxy
  enforces the role's scope before forwarding (defense in depth — the API still
  checks its own key scope).
- OIDC (generic, `authlib` pending Q3): discovery-URL + client id/secret knobs;
  code flow handled by FastAPI (`/auth/oidc/login|callback`); JIT user
  provisioning **off by default** (`oidc_auto_provision=false` — unknown
  subjects are rejected until an owner invites them; default role on provision:
  `viewer`). Google/Okta/Entra config examples documented. Password login
  remains as break-glass owner access.
- User management: `/orgs/{id}/users` CRUD (owner-only), audit-logged;
  dashboard settings page for invite/role-change/disable.

### Commit 3 — M5.1c Per-org quotas

- `orgs.daily_send_cap` (atomic reserve/release counter `sent_today`, exactly
  the campaign-cap pattern from M0.6a — checked in `run_presend_checks`
  between campaign cap and mailbox cap), `orgs.max_mailboxes` (checked at
  `POST /mailboxes`), `orgs.enrichment_daily_budget` (atomic counter consumed
  per provider call in `chain_enrich`; exhausted budget ⇒ verify-only, logged,
  never an error). All nullable = unlimited (self-hosters default); reset by
  `reset_daily_counters`.

### Commit 4 — M5.1d Cross-tenant isolation suite (the gate evidence)

- `tests/adversarial/test_tenancy_isolation.py`, predict-then-run per
  TESTING.md §3: two orgs fixture; **every** endpoint exercised with the wrong
  org's key — list endpoints must return zero foreign rows, item endpoints 404
  (never 403 — a 403 confirms existence, which is itself a leak); create
  endpoints must not attach to a foreign org even with a forged org_id in the
  body; the fail-closed no-context guard; worker tasks can't cross orgs;
  suppression/unsubscribe of org A's lead never suppresses org B's identical
  email (and the overlay does, when enabled); `/metrics` and `/analytics`
  aggregate only the caller's org. Row-leak checks on every list endpoint.
- Results table lands in `findings/13-m5-tenancy.md` — the Gate M5 review
  artifact.

### Commit 5 — M5.3 Deliverability suite (G12)

- Per-domain (not per-mailbox) health: new `domain_stats` rollup (bounces,
  complaints-proxy = spam-folder bounces + FBL hook for later, sends/day) +
  blocklist lookups (Spamhaus ZEN, SpamCop via DNSBL queries — BYO nothing,
  DNS only, SSRF-guard-exempt as pure DNS) + the M1.4 SPF/DKIM/DMARC checks →
  one 0–100 health score per sending domain, formula documented and
  inspectable. Dashboard page + `GET /deliverability/domains`.
- Inbox placement smoke test: operator supplies seed addresses (BYO — honest:
  no proprietary network); a placement run sends the campaign's opener to each
  seed through the normal pipeline in dry-run-style isolation; verdict recorded
  per seed (inbox/spam/missing) — via IMAP creds per seed where given, manual
  marking otherwise.
- Per-domain rate governance: domain-level token bucket wrapping the existing
  per-mailbox bucket; automatic pause: hard-bounce/complaint spike over
  threshold (knobs) pauses the **domain's** mailboxes (health `paused`),
  urgent-notifies, audit-logs. Un-pause is an explicit operate action.

### Commit 6 — M5.4 Platform operations

- Webhooks out: `webhook_endpoints` (org-scoped, url https-only through the
  SSRF guard, secret, event mask) + `webhook_deliveries` (payload, attempt
  count, status). Events: lead status changes, replies+classification,
  meetings, autopilot actions, escalations. Celery delivery with backoff →
  dead-letter on exhaustion. HMAC-SHA256 signature header (same scheme we
  verify inbound from Cal.com — symmetry documented).
- Helm chart (`deploy/helm/craftsman`) + `docker-compose.prod.yml` profile
  (no Mailpit, real SMTP required, TLS termination documented, non-localhost
  binding with the loud auth warning) — both smoke-validated (`helm template`
  in CI; no cluster needed).
- `docs/operations.md`: backup/restore runbook (pg_dump/restore incl. pgvector,
  Redis is rebuildable state — documented as such), horizontal worker scaling
  (safe today: Postgres/Redis locks, stateless workers — now with org
  contexts), audit-log export (`GET /audit/export` NDJSON, org-scoped,
  admin) + retention knob, SOC2-**alignment** map (control → feature; honest:
  alignment, not certification).

## New knobs (all `core/config.py`, documented in APPLICATION_OVERVIEW §10)

| Knob | Default | Purpose |
|---|---|---|
| `oidc_discovery_url` / `oidc_client_id` / `oidc_client_secret` | "" (SSO off; password login works) | generic OIDC |
| `oidc_auto_provision` | false | JIT user creation on first SSO login |
| `global_suppression_enabled` | false ⛔ | overlay list active (Q1a) |
| `unsubscribe_propagate_global` | false ⛔ | unsubscribes also write the overlay |
| `domain_pause_bounce_threshold` | 5/day ⛔ | auto-pause a sending domain |
| `blocklist_zones` | zen.spamhaus.org,bl.spamcop.net | DNSBL lookups |
| `audit_retention_days` | 0 (keep forever) | audit-log retention sweep |
| `webhook_max_attempts` | 8 | outbound webhook retry budget |

Org quota columns (`daily_send_cap`, `max_mailboxes`, `enrichment_daily_budget`)
are per-org data, not global knobs — null = unlimited.

## Test plan

- Unit: org-context manager, fail-closed guard, role→scope map, quota
  reserve/release math, health-score formula, webhook signing, DNSBL parsing
  (fixtures — no live DNS in unit layer).
- E2E (Postgres): migration 0015 upgrade→backfill→downgrade round-trip on a
  seeded pre-M5 database; two-org lifecycle (import→activate→send→reply→erase)
  fully isolated; OIDC callback flow against a stubbed provider; quota
  exhaustion under 4-worker contention (M0.6a threaded pattern); webhook
  delivery + retry + dead-letter.
- Adversarial: the commit-4 isolation suite (the gate evidence) + forged
  org_id writes + unsubscribe-token cross-org probes + webhook SSRF attempts
  (`http://169.254.169.254` endpoint registration must be rejected).
- Suite green, **0 skipped**, Postgres up; web `tsc`/eslint/build clean.

## Non-goals (explicit)

- **M5.2 CRM sync** — gate-blocked by roadmap text; begins only after human
  review of `findings/13`.
- Postgres RLS — the denormalized org_id makes it *possible* later; not wired
  now (the ORM guard is the enforced layer; RLS is defense-in-depth debt,
  recorded).
- SCIM provisioning, per-user API keys, org deletion/export tooling, billing.
- Any change to validator, autopilot policy, or bandit semantics.

## Deviations from roadmap text (recorded per house rule)

1. org_id on all tenant tables, not only the six named roots — rationale in
   commit 1; required for central (not per-router) enforcement.
2. Suppression PK change is schema-breaking for direct-SQL users — called out
   in the PR body and CHANGELOG-style README note.
3. OIDC lives in FastAPI (not the Next proxy) so SSO also covers API-first
   deployments; the dashboard consumes the same flow.
