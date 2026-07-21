# Craftsman — Enterprise Roadmap: Closing the Gap to a True Open-Source Artisan

> **For Claude Code.** This is the build plan. It extends — and never overrides — the
> working agreement in `TESTING.md` §0 and the verified findings in `APPLICATION_OVERVIEW.md`.
> Execute one epic per session. Every milestone has human gates; stop at them.
>
> **Prime directive:** Ship Artisan-class capability *without* abandoning the two design
> commitments that define this repo: (1) the LLM never free-writes outbound text, and
> (2) no message reaches a human without passing a deterministic validator. Any feature
> that cannot honor these must be escalated as a design decision, not built around them.

---

## 0. Ground rules for this roadmap

- **Order is dependency order.** M0 is a hard prerequisite for everything: you cannot add
  multi-tenancy without migrations, cannot add UI features without auth, cannot extend the
  validator while its numeric bug stands.
- **Session hygiene:** one epic per Claude Code session, context cleared between epics.
  Each epic ends with: tests green (0 skipped, Postgres up), findings/decisions written,
  and a stop for human review at every ⛔ gate.
- **No silent guarantee changes.** If an epic would weaken a README guarantee, stop and
  present options. The README is a contract; contracts get amended in the open.
- **Definition of done (every epic):** code + Alembic migration (if schema changes) +
  tests (unit + adversarial where marked) + API docs + dashboard surface (if user-facing)
  + README/docs update. A feature without its docs and tests is not done.
- **Bring-your-own-provider is the architecture** for anything that is a data business
  (contact databases, intent feeds, dialers, calendars). Craftsman ships the engine and
  clean provider interfaces; users bring keys. This is the honest open-source answer to
  features that are proprietary datasets in the commercial product.

---

## 1. Gap matrix — Artisan (Ava 2.0) vs. Craftsman today

Grounded in `APPLICATION_OVERVIEW.md` (code facts) and artisan.co's published feature set.

| # | Artisan capability | Craftsman today | Gap size | Closed by |
|---|---|---|---|---|
| G1 | Find leads: 250M+ B2B contacts, CRM import | CSV import only; Apollo/Hunter adapters exist but **orphaned** (`ingest/adapters.py`, no caller) | Large | M2 |
| G2 | Enrich via 22+ data sources | Email verify (syntax→MX→RCPT) only | Large | M2 |
| G3 | Prioritize by intent signals (funding, leadership hires) | Static ICP score at activate time (0.7·cosine + 0.3·rules) | Large | M2 |
| G4 | Personalized email generation | ✅ Research brief → slot-fill → validator. **Stronger than parity** (grounded by construction) | None (fix B1) | M0 |
| G5 | Multi-channel: email + social + dialer | Email only | Large | M3 |
| G6 | A/Z testing, shifts volume to winners | ✅ Thompson bandit — **at parity or better** (principled, inspectable) | Small (see M6) | M6 |
| G7 | Handles replies autonomously | Classify → state change → Slack ping. **Deliberate non-feature** | Design decision | M4 ⛔ |
| G8 | Books meetings on reps' calendars | Nothing | Large | M4 |
| G9 | Escalation rules (when a human is pulled in) | Fixed: everything interesting escalates | Medium | M4 |
| G10 | CRM integration (HubSpot, Salesforce), re-engage closed-lost/churned | Nothing | Large | M5 |
| G11 | Campaign creation UX | API/curl only — no UI to create campaigns or variants | Medium | M1 |
| G12 | Deliverability infra (managed domains, warmup network) | Warmup ramps + rate limits + bounce degradation; **no DKIM/SPF/DMARC guidance**, no placement testing | Medium | M1, M5 |
| G13 | Enterprise: SOC2, SSO, teams, full control | No auth at all; single-tenant; no migrations; no RBAC | Very large | M0, M5 |
| G14 | Website visitor identification | Nothing | Out of scope v1 — parked in §8 | — |

**What is NOT a gap (do not "fix"):** the anti-hallucination gate, the human-handoff
default, the bandit, the state machine, compliance headers, the honest README. These are
the product. Artisan parity is achieved *around* them.

---

## 2. M0 — Foundations (hard blockers; ~everything depends on this)

All items pre-verified in `APPLICATION_OVERVIEW.md` §11. Nothing user-visible ships before M0 completes.

### Epic M0.1 — Authentication & authorization (A1)
- API-key auth middleware on FastAPI (`Authorization: Bearer`), keys hashed at rest,
  scoped: `read`, `operate` (activate/pause/reclassify), `admin` (mailboxes, erase, keys).
- Dashboard: session login (single admin user for now; real user model lands in M5.1).
  All `web/src/lib/api.ts` calls carry auth.
- `/u/{token}` unsubscribe and `/health` remain **unauthenticated** (RFC 8058 requires
  unauthenticated POST; probes need liveness).
- Bind to localhost by default stays; add loud quickstart warning about exposure.
- **Tests:** every endpoint 401s without a key; scope enforcement per route; unsubscribe
  still works anonymously.

### Epic M0.2 — Alembic migrations (D1)
- Introduce Alembic; generate the baseline from current models; `init_db()` stops calling
  `create_all` outside dev/test. Every later epic that touches models ships a migration.
- **Tests:** upgrade from empty → head; downgrade one step; CI job runs migrations against
  a fresh Postgres.

### Epic M0.3 — Validator numeric grounding (B1) 🎯
- Split grounding into two paths: entities keep `partial_ratio ≥ 90`; **numbers, currency,
  percentages, dates get exact match after normalization** (strip separators/currency
  symbols, canonicalize magnitude suffixes: `$4M` ≡ `$4,000,000` ≢ `$40M`).
- **Tests:** the full adversarial table from `TESTING.md` §3.1 becomes the regression suite.
  `$4M`→`$40M` must reject; `1,000`→`1000` must pass.

### Epic M0.4 — GDPR erasure cascade (C2)
- `erase_lead` becomes a multi-store erase: leads, enrollments, messages (reply text is
  prospect PII), unsubscribe_tokens, review_queue payloads, audit_log details, queued
  Celery payloads referencing the lead, and research briefs *if the brief names the person*
  (company facts may stay; person mentions are scrubbed). Add `ON DELETE` behavior or
  explicit ordered deletes. Suppression entry survives (it must — it's the do-not-contact).
- **Tests:** create a lead with full history → erase → assert zero rows/keys across every
  store; delete-with-enrollments no longer raises IntegrityError.

### Epic M0.5 — SSRF guard on research fetch (A2)
- `research/fetch.py`: https-only scheme allowlist; resolve DNS and reject private/
  link-local/loopback ranges (including on redirect hops); bound redirects; per-fetch
  timeout stays.
- **Tests:** fixtures for `169.254.169.254`, `localhost:6379`, `file://`, redirect-to-private.

### Epic M0.6 — Operational integrity (B2, B3, D2)
- Seedable RNG mode for `pick_arm` (`BANDIT_SEED` env; unset = current behavior).
- Verify + fix under test: per-campaign cap enforcement under 4 workers (atomic
  check-and-increment, not read-then-write); idempotency key on `generate_and_send`
  (dedupe **before** dispatch; kill-worker retry test from `TESTING.md` §3.3).
- Structured JSON logging with correlation IDs (lead/enrollment/message); Prometheus
  `/metrics` (sends, rejections, classification distribution, queue depths); Celery
  dead-letter queue; **re-drive path for `error` enrollments** (review-queue action:
  retry copy / skip step / kill enrollment) — today a rejected copy is stuck forever.

⛔ **Gate M0:** human reviews the auth model, the erasure semantics (what survives?),
and the numeric-normalization rules before M1 begins.

---

## 3. M1 — Operator experience: make the existing engine usable

Closes G11 and the biggest onboarding complaints ("not obvious how to create a campaign").
No new engine capability — pure surfacing of what exists.

### Epic M1.1 — Campaign builder UI
- `web`: create/edit campaign (name, ICP description, value prop, persona, caps) →
  steps (order, wait_days) → variants per step (name, skeleton editor with slot
  placeholders, slot schema). Server actions hit the existing POST endpoints.
- Skeleton editor validates slots against `SlotFill` schema client-side; live preview
  renders the skeleton with sample fills.

### Epic M1.2 — Dry-run / preflight mode (E4)
- `POST /campaigns/{id}/dry-run`: route the full pipeline (research → fill → validate →
  send) for N sample leads **through Mailpit only**, regardless of configured SMTP.
  Dashboard shows the rendered emails + validator verdicts side by side.
- This is the "interview your next hire" moment for self-hosters: see exactly what would
  be sent before anything is real. Activate button becomes two-step: dry-run first
  (skippable with explicit override).

### Epic M1.3 — Lead & review operations UI (E2)
- Leads page: CSV upload UI, per-lead erase (confirm dialog) + manual suppress, filter by
  status/score, ICP score explanation popover (cosine vs rule components — the score is
  currently a bare number).
- Review queue UI: show blocked copy with validator errors inline; actions from M0.6
  (retry / skip / kill). Classification review: approve/override (wires the existing
  reclassify endpoint into the queue view).

### Epic M1.4 — Deliverability onboarding (C3)
- Docs page + dashboard checklist per mailbox: SPF/DKIM/DMARC records with copy-paste
  DNS values, verified by live DNS lookup; warmup stage visible with ramp schedule;
  "do not send from your primary domain" guidance front and center.
- README: deliverability section moves above the quickstart.

⛔ **Gate M1:** human walks the golden path end-to-end — import CSV → build campaign in
UI → dry-run → activate → see reply in inbox → reclassify — and signs off on UX.

---

## 4. M2 — Lead acquisition, enrichment & intent (G1–G3)

The engine stops being CSV-only. Everything here is provider-interface + BYO keys.

### Epic M2.1 — Enrichment framework (revive the orphans)
- Promote `ingest/adapters.py` from dead code to a real `EnrichmentProvider` protocol:
  `enrich(lead) -> EnrichmentResult` (title, seniority, company size/industry, LinkedIn
  URL, phone, confidence, source). Ship Apollo + Hunter implementations (existing code),
  plus a `null` provider. Providers chain with per-field precedence; results stored with
  provenance (`lead_enrichments` table: field, value, source, fetched_at).
- Wire into the pipeline: `enrich_lead` task runs verify → enrichment chain. New Celery
  queue `enrich` already exists — use it.
- **Tests:** provider protocol contract tests with recorded fixtures; chain precedence;
  graceful per-provider failure (one dead provider never blocks verify).

### Epic M2.2 — Lead sourcing connectors
- `LeadSourceProvider` protocol: `search(icp_query, filters) -> leads[]`. Implementations:
  Apollo search (BYO key), generic CSV/webhook source, and a `POST /leads/source` endpoint
  that runs a search → dedupe → import through the existing gate (suppression + syntax
  checks apply identically — sourced leads get zero shortcuts).
- Dashboard: "Find leads" page — ICP-driven search form → preview results → import
  selected. Honest labeling: results come from *your* provider account, with provider
  branding shown. No pretending we have a proprietary database.

### Epic M2.3 — Intent signals engine
- New tables: `signals` (company_id, type, payload, observed_at, source) and
  `signal_rules` (campaign_id, signal_type, action: boost_score | enroll | notify).
- Signal collectors (Celery beat, per-source): funding events (BYO Crunchbase key or
  RSS/news watch), leadership hires & job postings (careers-page diffing via the existing
  SSRF-guarded fetcher; job-board APIs where keyed), tech-stack change (homepage diff).
  Each collector is optional and independently disableable.
- Scoring integration: `icp_score` gains a decaying signal component —
  `0.6·cosine + 0.25·rules + 0.15·signal_boost` (weights in config, documented as
  product knobs per `TESTING.md` §0 — changing them needs human sign-off).
- **Signal-triggered enrollment:** a `signal_rules.enroll` match on an already-verified,
  above-threshold lead auto-enrolls into the mapped campaign — through the normal state
  machine (`queued`), never skipping research or validation.
- **Tests:** collector fixtures; decay math; rule matching; triggered enrollment lands in
  `queued` and nowhere further.

⛔ **Gate M2:** human reviews the scoring weight change (it alters who gets emailed),
the signal-triggered enrollment policy, and provider ToS compliance notes per connector.

---

## 5. M3 — Multi-channel sequences (G5)

**Design constraint stated up front:** automated LinkedIn actions violate LinkedIn's ToS
and get accounts banned. The commercial products do it anyway or via gray-area vendors.
The honest open-source version is **assisted, not automated** for social: Craftsman
generates the message (validated!), queues it as a task, a human clicks send. Calls are
the same pattern. Email remains the only fully autonomous channel.

### Epic M3.1 — Channel abstraction
- `sequence_steps` gains `channel: email | linkedin_task | call_task` (migration;
  default email — existing campaigns unaffected).
- State machine: task-channel steps transition to a new `awaiting_human_touch` state with
  a due date; completing the task (or expiry with `skip_on_expire=true`) advances the
  sequence. Wildcard BOUNCE/UNSUBSCRIBE routing unchanged.
- Touch history: unified per-lead timeline (email sends, task completions, replies)
  backing a lead-detail page.

### Epic M3.2 — LinkedIn task queue
- For `linkedin_task` steps the copywriter fills a **LinkedIn skeleton** (connection note
  ≤ 280 chars or InMail-style short message) through the **same validator** (grounding,
  banned phrases, length caps adapted per channel — new cap set in config).
- Dashboard "Tasks" page: card per due task — lead context, research brief highlights,
  the validated message with copy button, deep link to the profile, done/skip buttons.
- **No browser automation. No session-cookie handling. Ever.** If a user asks for it,
  the docs explain why not (ToS + account risk) — this is a feature of the honest version.

### Epic M3.3 — Call task queue + optional dialer
- `call_task` steps generate a **call brief** (structured: opener, 2 pain hypotheses from
  the research brief, objection notes — all grounded, all validated) rather than a script
  of fake rapport. Task card shows brief + click-to-call via `tel:` link; optional Twilio
  provider for click-to-dial from the dashboard (BYO account) with outcome logging
  (connected / voicemail / no-answer → feeds the touch history, not the bandit).
- **Bandit scope decision (do not decide unilaterally):** task channels do not update
  copy posteriors in v1 (completion ≠ reply; reward semantics differ). Flag for M6.

⛔ **Gate M3:** human reviews the `awaiting_human_touch` timeout semantics (what happens
to a sequence when nobody does the task?) and the per-channel validator caps.

---

## 6. M4 — Reply handling & meeting booking (G7–G9) ⛔ DESIGN DECISION FIRST

**This milestone begins with a human decision, not code.** Artisan's headline feature —
"handles every reply, books meetings autonomously" — is the exact thing Craftsman's README
promises never to do, and calls "exactly where AI SDRs get caught." Silently building it
would make the README a lie. Three options, presented for sign-off:

- **Option A — Copilot (recommended default):** every interested/objection reply gets an
  auto-drafted response — slot-filled from the research brief + thread context, gated by a
  reply-specific validator — waiting in the inbox UI for **one-click human approve/edit/send**.
  The guarantee stands verbatim. Ships 80% of the speed value (reply latency drops from
  hours to minutes) with zero autonomy risk.
- **Option B — Guarded Autopilot (opt-in, off by default):** a separate, policy-gated
  reply path may auto-send **only** for reply intents with deterministic resolutions:
  interested → send scheduling link; "send me info" → send approved one-pager; timing
  objection → offer to follow up in N weeks. Everything else (pricing, competitor,
  hostile, ambiguous, any classifier confidence < 0.9) **escalates**. Every auto-reply is
  slot-filled + validated; free-text reply generation remains impossible by construction.
  README guarantee is amended openly: *"never auto-replies unless you enable Autopilot,
  whose replies are template-constrained, validated, and policy-escalated."*
- **Option C — Full parity:** LLM-composed conversational replies. **Recommend rejecting:**
  it abandons both design commitments and the repo's reason to exist.

The epics below implement A, then B behind its flag. C is not planned.

### Epic M4.1 — Reply drafts (Copilot)
- New skeletons: `reply_interested`, `reply_objection_timing`, `reply_objection_info`,
  each with typed slots grounded in (research brief + campaign config + **the inbound
  reply text**, which becomes part of the grounding corpus so the draft may reference
  what the prospect actually said — and only that).
- Reply validator variant: same four gates, plus: must not introduce commitments
  (pricing numbers, discounts, legal terms) unless present in campaign config; quotes
  the thread correctly; ≤ 120 words.
- Inbox UI: draft shown under the reply with approve / edit / send / discard. Sending
  goes through the normal send engine (suppression + rate limits apply). **A human click
  is the only path to dispatch.**
- Bandit: approved-and-sent drafts are logged but do not update copy arms (different
  reward process). Draft acceptance rate becomes a dashboard metric.

### Epic M4.2 — Escalation rules engine (G9)
- `escalation_rules` per campaign: match on (classification, confidence, keyword sets —
  e.g. legal/GDPR/angry terms, seniority of replier, account value tag) → action
  (Slack ping, assign to human, auto-suppress, block Autopilot). Default ruleset ships
  matching today's behavior exactly. Legal-threat/GDPR-demand keywords → suppress +
  urgent notify, never a draft (extends `TESTING.md` §3.4 fixtures into production rules).

### Epic M4.3 — Meeting booking (G8)
- `CalendarProvider` protocol: **Cal.com** first (open-source-native fit, self-hostable),
  then Google Calendar / MS Graph (BYO OAuth apps). Scheduling links per rep/persona.
- New `meetings` table (enrollment_id, provider_event_id, status: proposed/booked/
  completed/no_show, booked_at). Webhook receivers update status.
- Copilot drafts for interested replies embed the scheduling link. Under Autopilot
  (M4.4), interested + confidence ≥ 0.9 + no escalation match → the link reply may
  auto-send.
- **Booked meeting becomes a first-class outcome:** state machine gains
  `meeting_booked`; dashboard funnel becomes sent → replied → interested → booked.

### Epic M4.4 — Guarded Autopilot (only if Option B approved)
- `autopilot_enabled` per campaign, default false, requires an `admin`-scoped API call to
  enable (deliberate friction). Policy engine evaluates: classification ∈ allowed set,
  confidence ≥ 0.9, no escalation match, within business hours, ≤ 1 auto-reply per thread
  ever (a reply to the auto-reply always escalates — no AI-to-human conversation loops).
- Every auto-sent reply is flagged in the inbox UI and in headers-level audit; a kill
  switch (`POST /campaigns/{id}/autopilot/disable`) works instantly.
- README + docs amended in the same PR that ships the flag. The guarantee change and the
  code change are one reviewable unit.
- **Tests:** the safety property from `TESTING.md` §3.4 inverts precisely here — force
  every classification and assert auto-send occurs **only** in the allowed set under all
  policy conditions; fuzz the policy engine; verify the one-reply-per-thread invariant.

⛔ **Gate M4:** Option A/B/C decision before any code; second gate after M4.1 (review
actual draft quality on the classifier eval fixtures before building autonomy on top).

---

## 7. M5 — Enterprise platform (G10, G12, G13)

### Epic M5.1 — Multi-tenancy & RBAC (D3)
- `orgs` and `users` tables; org_id FK on campaigns, leads, mailboxes, suppression,
  signals, meetings (migration with backfill to a default org). Every query org-scoped
  via a session-level dependency — enforced centrally, not per-router.
- Roles: `owner`, `operator`, `viewer`. SSO via OIDC (generic; Google/Okta/Entra
  configs documented). API keys become org-scoped.
- **Per-org quotas:** daily send caps, mailbox counts, enrichment call budgets.
- **Suppression semantics decision (⛔ ask):** suppression should arguably be global
  across orgs on shared infra (a person who unsubscribed is unsubscribed) — but that
  leaks presence across tenants. Present tradeoffs; default: per-org suppression +
  optional global overlay list.
- **Tests:** cross-tenant isolation suite — every endpoint attempted with the wrong
  org's key; row-level leak checks on every list endpoint.

### Epic M5.2 — CRM sync (G10)
- `CRMProvider` protocol: HubSpot first (friendliest API), Salesforce second. Inbound:
  import contacts/segments as lead sources (closed-lost, churned, MQL lists → mapped to
  campaigns — Artisan's "put your CRM to work"). Outbound: sync activity (sends, replies,
  classifications, meetings) to the contact timeline; booked meetings create CRM events.
- Field mapping UI with dry-run preview; sync conflicts always resolve toward the CRM
  as source of truth for contact fields, Craftsman for engagement history.

### Epic M5.3 — Deliverability suite (G12)
- Per-domain health dashboard: bounce/complaint trends, blocklist checks (Spamhaus et
  al. lookups), DNS auth status (from M1.4) — one health score per sending domain.
- Inbox placement smoke test: send to a user-provided seed-address list, report
  inbox/spam/missing (BYO seed accounts; no proprietary network to fake).
- Per-domain (not just per-mailbox) rate governance; automatic pause on complaint spike.

### Epic M5.4 — Platform operations
- Webhooks out (lead events, replies, meetings, autopilot actions) with HMAC signatures.
- Helm chart + production compose profile (real SMTP, no Mailpit, TLS termination docs);
  horizontal worker scaling docs (safe today: locks are Postgres/Redis, workers stateless).
- Backup/restore runbook; audit-log export (the table exists — surface + retention policy);
  SOC2-alignment doc mapping controls to features (honest: *alignment*, not certification).

⛔ **Gate M5:** human reviews tenancy isolation test results and the suppression-scope
decision before CRM sync (which moves real customer data) begins.

---

## 8. M6 — Optimization maturity (G6 beyond parity)

- **Auto-variant proposal:** an LLM drafts new skeleton variants from winning arms'
  patterns → lands in the review queue → human approves → enters the bandit as a fresh
  arm. Artisan's "dozens of variations" with a human gate — the copy that goes out is
  still only ever human-approved skeletons + validated fills.
- **Contextual bandit:** per-segment posteriors (industry × seniority buckets) with
  hierarchical shrinkage toward the global arm — small-sample honesty preserved.
- **Reward upgrade:** graded rewards (interested=1.0, objection=0.4, not_now=0.2,
  booked meeting = terminal bonus) replacing binary — changes `update_arm` semantics.
  ⛔ Product decision: sign off on the reward table.
- Experiment reporting: per-arm credible intervals, expected loss, and a "time to
  decision" estimate on the dashboard — replacing gut-feel with the math that's already
  there.
- Revisit M3.3's parked question: whether task-channel touches should carry bandit signal.

**Parked indefinitely (revisit only on demand):** website visitor identification (G14 —
privacy posture conflicts with the repo's ethos; requires tracking infrastructure),
managed warmup network (requires operating shared infrastructure — a service, not software).

---

## 9. Execution protocol for Claude Code

1. **Sequence:** M0 → M1 → M2 → M3 → M4 → M5 → M6. Within a milestone, epics in listed
   order. No parallel milestones.
2. **Per epic:** read this file + `TESTING.md` + relevant `APPLICATION_OVERVIEW.md`
   sections → write a short implementation plan (`plans/mX.Y-name.md`) → **stop for
   approval** → implement → tests green (0 skipped) → docs → stop.
3. **Adversarial-test debt travels with features:** every epic marked with validator,
   send-path, autonomy, or tenancy scope must add to `tests/adversarial/`, following the
   predict-then-run method from `TESTING.md` §3.
4. **The knobs table** (`APPLICATION_OVERVIEW.md` §10) grows with every config addition;
   changing an existing knob's default is always a ⛔ human decision.
5. **Independent audit** (`TESTING.md` §5) runs at every milestone boundary in a fresh
   session before the gate review.
6. **README amendments** ship in the same PR as the feature that necessitates them —
   the honest-README ethos is a deliverable, not a chore.

---

## 10. What "true open-source Artisan" means when we're done

Every Ava 2.0 headline mapped to its Craftsman answer:

| Artisan says | Craftsman ships |
|---|---|
| "Finds and prioritizes high-intent leads" | BYO-provider sourcing + enrichment chain + open intent-signal engine with inspectable scoring (M2) |
| "Launches personalized multi-channel sequences" | Email autonomous; social & calls as validated, assisted task queues — the ToS-honest version (M3) |
| "Tests and optimizes messaging automatically" | Thompson bandit + human-gated auto-variant proposal + contextual segments (already shipped, extended in M6) |
| "Handles replies and books meetings autonomously" | Copilot drafts by default; opt-in Guarded Autopilot with template-constrained, validated, policy-escalated replies + Cal.com/GCal booking (M4) |
| "Enterprise: full control" | Actual full control: AGPL source, self-hosted, org/RBAC/SSO, audit logs, your data never leaves your infra (M0, M5) |

The differentiator stays what it was on day one: every mechanism inspectable, every
guarantee tested, and the one thing the funded products won't show you — exactly how
and when the AI is allowed to speak — is a config file you can read.
