# Plan — M3: Multi-channel sequences (G5) ⛔ Gate M3

**Branch:** `m3-multichannel` (off `main` = M0+M1+M2) · **Status:** approved, executing · **Roadmap:** M3 (`ROADMAP.md:203`)

> **Design constraint (roadmap, verbatim intent):** automated LinkedIn actions violate
> LinkedIn's ToS. The honest open-source version is **assisted, not automated**: Craftsman
> generates the message (validated!), queues it as a task, a human clicks send. Calls are
> the same pattern. **Email remains the only fully autonomous channel.** No browser
> automation, no session-cookie handling, ever — documented as a feature.

> **⛔ Gate M3 sign-off (recorded before implementation):**
> **Q1 — Expiry semantics → Pause + flag overdue.** Default `skip_on_expire=false`: an
> undone task holds the enrollment in `awaiting_human_touch`, surfaced as overdue on the
> Tasks page. A human-touch step means a human touched it; operators opt into auto-skip
> per step (`skip_on_expire=true` ⇒ after the due window the step is marked `expired` and
> the sequence advances). Due window: `TOUCH_TASK_DUE_DAYS` business days, default 3.
> **Q2 — Per-channel validator caps → defaults approved.** LinkedIn: rendered note
> ≤ 280 chars, reading grade ≤ 8, grounding + banned phrases unchanged, no subject gate.
> Call brief: opener ≤ 25 words, each of ≤ 2 pain hypotheses ≤ 20 words, objection notes
> ≤ 40 words, every field grounded + banned-phrase-checked. Email caps untouched.
> **Bandit scope (decided by roadmap M3.3, not re-decided):** task channels do NOT update
> copy posteriors in v1 (completion ≠ reply). `pick_arm` over Beta(1,1) priors that never
> update = uniform variant rotation. Flagged for M6.

## Goal

`sequence_steps` gains a `channel` (email | linkedin_task | call_task). Email steps work
exactly as today. Task-channel steps generate **validated** content (LinkedIn note through
the same four-gate validator with channel caps; calls get a grounded structured brief, not
a fake-rapport script), land in `awaiting_human_touch` with a due date, and appear on a
dashboard **Tasks** page where a human completes them. Completion (or expiry, where opted
in) advances the sequence through the normal state machine. A unified per-lead timeline
(sends, replies, task touches) backs a lead-detail view.

## Current state (verified in code this session)

- `sequence_steps` has no channel column (`core/models.py:184`); tick routes every due
  `ready` enrollment to `generate_and_send` (`sequencer/tick.py:94`).
- State machine is a pure transition table (`sequencer/machine.py`); wildcard
  BOUNCE/UNSUBSCRIBE matches any non-terminal state — new states inherit this for free.
- `idx_enroll_due` is a partial index over `('queued','ready','waiting','ooo_rescheduled')`
  (`core/models.py:222`) — must be recreated to include `awaiting_human_touch`.
- Validator gates are reusable functions (`copywriter/validator.py`): `_grounded`,
  `_corpus_numerics`, `BANNED_PHRASES`; `validate_fill` itself stays byte-identical.
- Send idempotency = partial unique index claim before I/O (`workers/tasks.py:194`);
  tasks mirror this with `UNIQUE(enrollment_id, step_order)` on `touch_tasks`.
- `erase_lead` (`compliance/suppression.py:102`) deletes per-store explicitly, no FK
  cascades (M0.4 doctrine) — `touch_tasks` holds person PII (the validated message) and
  must join the cascade.
- Inbox pipeline (`inbox/pipeline.py:95`) applies reply events; a reply/unsub/bounce
  arriving while a task is open must cancel the open task.
- `activate` requires ≥1 variant per step (`campaigns.py`): call steps have no skeleton
  (structured brief), so the check becomes per-channel.

## Epics → commits (one branch, three commits, one PR)

### Commit 1 — M3.1 Channel abstraction
- **Migration 0010**: `sequence_steps.channel` TEXT NOT NULL DEFAULT 'email';
  `sequence_steps.skip_on_expire` BOOL NOT NULL DEFAULT false; `touch_tasks` table
  (id, enrollment_id FK no-cascade, step_order, channel, variant_id nullable FK,
  payload JSONB, status open|done|skipped|expired|cancelled, outcome nullable,
  due_at, created_at, resolved_at, `UNIQUE(enrollment_id, step_order)`);
  recreate `idx_enroll_due` including `awaiting_human_touch`. Existing campaigns
  unaffected (server default 'email').
- `craftsman/channels.py`: the channel registry — the fork-friendly seam. Channel names,
  `is_assisted()`, per-channel slot vocabularies + static slots + validator caps source.
- `machine.py`: state `awaiting_human_touch`; events TASK_CREATED (ready→aht),
  TASK_DONE / TASK_SKIPPED / TASK_EXPIRED (aht→waiting), REPLY_* + OOO from aht,
  wildcards unchanged. New tests only; existing transition tests untouched.
- `tick.py`: `ready` branch routes by step channel (new optional `enqueue_task` kwarg —
  existing call sites keep working); due `awaiting_human_touch` (only reachable when
  `skip_on_expire` — otherwise `next_action_at` stays NULL) expires the task and advances.
- `workers/tasks.py`: `generate_touch_task` Celery task — suppression re-check → variant
  pick (uniform) → LLM fill → channel validator → retry once → review queue on double
  reject (same policy as email); idempotent task claim; sets due date via
  `add_business_days`; `generate_and_send` gains a channel guard (a task step can never
  reach SMTP even if mis-enqueued).
- Tasks API router (`/tasks`): list w/ lead+campaign context and overdue flag (read),
  complete/skip (operate) — both advance via the state machine + `schedule_next_step`;
  409 on non-open tasks (no double-advance).
- `GET /leads/{id}/timeline`: unified sends + replies + task touches.
- `erase_lead` deletes `touch_tasks` rows; inbox pipeline cancels open tasks on
  reply/unsub/bounce.
- `apply/skip` on campaign step endpoints: `channel` + `skip_on_expire` on create/update
  (frozen once enrolled, same as structure today); activate's variant check per channel.

### Commit 2 — M3.2 LinkedIn task queue
- `LinkedInSlotFill` schema (personalization_hook, value_bridge, cta_question);
  `copywriter/task_fill.py` with a LinkedIn-specific system prompt (same only-the-brief
  rules); default skeleton `skeletons/linkedin_connection.txt`; static slot: first_name
  (no signature — LinkedIn shows the sender's profile).
- `validator.py` additions (no existing byte changed): `validate_task_fill(...)` — same
  grounding corpus machinery, banned phrases, em-dash gate, char cap (280), grade ≤ 8,
  no subject gate. Knob: `LINKEDIN_NOTE_MAX_CHARS=280`.
- Web: **Tasks** page — card per task: channel badge, lead context (name/title/company),
  research-brief highlights, the validated message + copy button, deep link to
  `lead.linkedin_url`, done/skip, due/overdue. Nav entry.
- Campaign builder: channel select + skip_on_expire per step; LinkedIn skeleton editing
  validates against the LinkedIn slot vocabulary.
- Docs: the "why we will never automate LinkedIn" section (README + Tasks page footer).

### Commit 3 — M3.3 Call task queue + optional dialer
- `CallBrief` schema (opener, ≤2 pain_hypotheses from the brief, objection_notes) —
  structured, grounded, validated per approved caps; no skeleton/variant needed
  (activate check already per-channel from commit 1).
- Task card shows the brief + `tel:` link (lead.phone, enrichment-fillable since M2.1);
  call outcome on complete: connected | voicemail | no_answer → touch history only,
  **never** the bandit.
- Optional Twilio click-to-dial (BYO): `sender/dialer.py` httpx-only (no new dependency),
  `POST /tasks/{id}/dial` rings the operator, TwiML-dials the lead; disabled unless all
  four `TWILIO_*` knobs set. Unit-tested against a mocked transport; no network in tests.
- README multi-channel section; APPLICATION_OVERVIEW knobs table + .env.example.

## New knobs (all in `core/config.py`, documented in APPLICATION_OVERVIEW §10)

| Knob | Default | Meaning |
|---|---|---|
| `TOUCH_TASK_DUE_DAYS` | 3 | business days until a task is due |
| `LINKEDIN_NOTE_MAX_CHARS` | 280 | rendered LinkedIn note char cap |
| `CALL_OPENER_MAX_WORDS` | 25 | call-brief opener cap |
| `CALL_PAIN_MAX_WORDS` | 20 | per pain-hypothesis cap |
| `CALL_OBJECTION_MAX_WORDS` | 40 | objection-notes cap |
| `TWILIO_ACCOUNT_SID/_AUTH_TOKEN/_FROM_NUMBER/_OPERATOR_NUMBER` | "" | click-to-dial (all four required) |

## Test plan

- **unit**: new machine transitions; `validate_task_fill` boundary cases (280/281 chars,
  word caps at/over, grounding + banned phrases in notes and briefs); channel registry;
  dialer against mock transport; expiry date math.
- **e2e**: full lifecycle (email→linkedin→call campaign; tick → task → complete →
  advances; skip advances with audit; expiry honors skip_on_expire both ways; reply
  while task open cancels it; unsubscribe wildcard from `awaiting_human_touch`;
  erase_lead leaves zero touch_tasks; timeline contents); migration 0010 up/down.
- **adversarial** (`tests/adversarial/test_task_channel_attacks.py`, predict-then-run):
  task steps never create outbound Message rows / never reach SMTP (including
  `generate_and_send` invoked directly on a task step); completing/skipping tasks never
  moves any variant α/β; double-complete → 409, single advance; duplicate
  `generate_touch_task` → one task; `$4M`→`$40M` in a LinkedIn note rejects; injected
  ungrounded exec name in a call brief rejects.

## Non-goals (explicit)

- No browser automation / cookie handling for LinkedIn — ever (documented, not just omitted).
- No bandit updates from task channels (M6 revisit, per roadmap).
- No InMail sending, no LinkedIn API integration — the deep link + copy button is the product.
- Existing email pipeline behavior byte-identical for email-only campaigns.
