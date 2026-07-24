# M4 boundary audit (independent, fresh session) — 2026-07-24

Per ROADMAP execution rule 5 / TESTING.md §5: adversarial audit of the M4 diff
(`origin/main...m4-reply-handling`, commits 85118c8 / 6a622c9 / 2ef324f / 7477c83 /
e0bae0a / 4020c0f / 7e40076, PR #6), run in a fresh session with no prior context,
assuming the previous session was motivated to look successful.

## Verdict: PASS-WITH-NOTES

Suite reproduced at tip (this session, raw):

```
$ LLM_PROVIDER=mock .venv/bin/python -m pytest -q
560 passed, 14 warnings in 8.75s        (0 failed, 0 skipped)
```

Matches findings/11 exactly. `npx tsc --noEmit` → exit 0; `npx eslint src --quiet`
→ exit 0 (both re-run this session). `next build` NOT re-run — that sub-claim
remains UNVERIFIED, same status the M3 audit gave it.

---

## F-01 — Three pre-existing test files modified; no weakening beyond two recorded, justified relaxations

**Severity:** informational
**README claim affected:** none
**Status:** open

### Claim under test
TESTING.md §5(a): no existing test modified, skipped, or weakened to make the suite pass.

### Method
`git diff origin/main...m4-reply-handling --diff-filter=DM --name-status -- tests/`,
then line-level review of each modified file. `grep -rn "pytest.mark.skip\|xfail"`
over tests/ for new markers.

### Predicted
Zero modifications, or modifications matching recorded deviations.

### Actual
Exactly three files modified (all other tests/ changes are 11 new files):

1. `tests/unit/test_machine.py` — `test_terminal_states_are_closed` gains a
   three-pair carve-out (`replied_* × MEETING_BOOKED → meeting_booked`). This IS a
   relaxation of a prior invariant, but: the carve-out is enumerated exactly (any
   other terminal exit still raises by construction), the carved-out pairs assert
   the new target state rather than being skipped, and the change is recorded as
   deviation #4 in findings/11 and in the plan header.
2. `tests/e2e/test_auth_integration.py` — `UNAUTH_ALLOWLIST` widened by one route,
   `/meetings/webhooks/calcom`, with an in-test rationale comment. This weakens the
   "everything requires a key" property by one deliberate, HMAC-gated route (see
   F-06). Plan-approved (M4.3 explicitly ships an unauthenticated, HMAC-verified
   webhook).
3. `tests/e2e/test_touch_tasks.py` — append-only: three NEW tests covering the M3
   audit's untested suppression paths (complete/single-GET/skip). Strictly
   strengthening.

No skip/xfail markers anywhere in tests/. No tolerance loosened.

### Verdict
VERIFIED (behavior: I read every changed hunk, not just filenames). The two
relaxations are exactly the plan-recorded ones; nothing silent.

---

## F-02 — Thresholds, caps, parameters: append-only; new knobs match plan and APPLICATION_OVERVIEW

**Severity:** informational
**README claim affected:** none
**Status:** open

### Claim under test
TESTING.md §5(b): no threshold/cap/parameter changed outside an approved fix.

### Method
`git diff origin/main...m4-reply-handling -- craftsman/core/config.py
craftsman/copywriter/validator.py craftsman/copywriter/banned_phrases.txt`; compared
new knob defaults against the plan's knob table and APPLICATION_OVERVIEW's table.

### Predicted
Config append-only; existing validator constants untouched.

### Actual
- `config.py`: pure additions — `reply_draft_max_words=120`,
  `reply_followup_weeks=4`, `calcom_api_key=""`, `calcom_webhook_secret=""`,
  `autopilot_min_confidence=0.9`. Byte-match to the ⛔ Gate M4 plan table and to the
  new APPLICATION_OVERVIEW rows. (`AUTOPILOT_FOLLOWUP_WEEKS` shipped as
  `reply_followup_weeks` — recorded deviation #3.)
- `validator.py`: append-only (`load_commitment_terms`, `validate_reply_fill`).
  `MAX_BODY_WORDS=90`, `MAX_READING_GRADE=8.0`, `validate_fill`, `validate_task_fill`
  byte-identical. `banned_phrases.txt` untouched; `commitment_terms.txt` is new.
- `machine.py`: pre-existing transitions untouched; additions are the
  `MEETING_BOOKED` event, `meeting_booked` terminal, and 7 explicit pairs (matches
  plan list exactly).
- Migrations 0011–0014 all have symmetric downgrades; model parity verified by
  `test_migrations` inside the reproduced 560.

### Verdict
VERIFIED (behavior — diff read line by line).

---

## F-03 — Raw-output backing for findings/11 claims

**Severity:** low
**README claim affected:** #15 (suite counts)
**Status:** open

### Claim under test
TESTING.md §5(c): every "verified/passing" claim backed by raw output.

### Method
Re-ran the suite and web checks (see header). Compared against findings/11's
claims.

### Predicted
Tip count reproducible; intermediate claims may lack raw backing.

### Actual
- Tip suite claim (560/0/0): findings/11 records the raw line; **independently
  reproduced this session, identical counts** (8.75s vs their 6.91s).
- Per-commit suite sizes (445 → 493 → 513 → 534 → 560): stated without raw output;
  NOT re-run per-commit this session. Arithmetic is self-consistent with the
  commit messages, but these four intermediate counts are UNVERIFIED.
- Web claims: `tsc` exit 0 and `eslint` exit 0 re-verified this session (raw:
  both printed `exit: 0`). `next build` success claim UNVERIFIED (not re-run).
- Migration-chain claim ("3 passed") is inside the reproduced 560 (Postgres was
  up, so the e2e layer genuinely ran — 0 skipped confirms no silent skip).

### Verdict
VERIFIED for everything load-bearing; UNVERIFIED for the four intermediate
per-commit counts and `next build` — neither affects the safety boundary.

---

## F-04 — Currency/commitment gate: implemented as "trusted sources", not "campaign config only"

**Severity:** low
**README claim affected:** #2/#3 (grounding discipline)
**Status:** open — human should confirm the widened license

### Claim under test
The commitment gate: prospect-quoted numbers/prices cannot echo into a draft
unless present in **campaign config**.

### Method
Read `validator.py:validate_reply_fill` (currency branch), `commitment_terms.txt`,
`tests/unit/test_reply_validator.py`, `tests/adversarial/test_reply_attacks.py`.
All relevant tests ran inside the reproduced 560.

### Predicted
Campaign-config-only licensing per the original plan text.

### Actual
Two distinct gates, correctly fail-closed but with different licenses:
- **Commitment TERMS** (`discount`, `pricing`, `guarantee`, …): licensed by
  campaign config (value_prop/persona) ONLY. As specified.
- **Currency amounts**: licensed by any TRUSTED source — research brief OR
  campaign config OR persona — but never by the prospect's reply alone
  (`trusted_numerics` excludes `reply_text`). This is wider than "campaign config
  only" and is recorded deviation #2 in findings/11 and the plan header
  ("congrats on the $4M raise" is brief-grounded product behavior).
- Adversarially verified: `test_attacker_supplied_price_is_not_grounding` ($99
  echo fails even though the attacker's number IS in the corpus),
  `test_prospect_currency_cannot_be_echoed`, `test_magnitude_drift_rejects_in_drafts`
  ($4M→$40M), `test_prompt_injection_cannot_buy_a_discount` — all in the 560.
- Note: a wrong or poisoned research brief could license a currency figure into a
  reply draft. That risk already exists for opener emails (same standard, as the
  deviation argues), and under Copilot a human click still gates dispatch; under
  Autopilot the three deterministic skeletons don't solicit currency slots — but
  the license is validator-level, not skeleton-level.

### Verdict
VERIFIED as-implemented (behavior, via reproduced adversarial tests). The
deviation is recorded, not silent. Human judgment item: accept brief-licensed
currency in replies, or narrow to campaign-only.

### Product question for the human
Is a brief-sourced dollar figure ("your $4M raise") acceptable in an
**auto-sendable** draft, or should Autopilot-eligible skeletons face the
campaign-only currency standard while Copilot keeps the wider one?

---

## F-05 — ≤1-auto-reply-per-thread invariant: enforced by read-then-act, no DB-level guard for concurrent distinct replies

**Severity:** medium
**README claim affected:** #7 (as amended: "at most one auto-reply per thread, ever")
**Status:** open — human review before merge

### Claim under test
"≤ 1 auto-reply per thread, EVER — hardcoded, not a knob" (README amendment,
`inbox/autopilot.py` module doc, findings/11).

### Method
Read `autopilot.prior_auto_replies` (count query), `workers/tasks.py
_maybe_autopilot_send`, `sender/reply.py _claim`, migration 0011 constraints;
reviewed the concurrency tests that exist.

### Predicted
A DB constraint or claim covering the thread, not just the draft.

### Actual
Three layers exist, and they cover different races:
1. `UNIQUE(inbound_message_id)` — stops duplicate task delivery for the SAME
   inbound (tested: `test_worker_is_idempotent`).
2. CAS `pending→sending` on the draft row — stops double dispatch of the SAME
   draft (tested: `test_double_send_is_409_single_email`).
3. `prior_auto_replies(db, enrollment_id)` — a plain COUNT of committed
   `auto_sent=true, status in (sent, edited_sent)` rows, read before deciding.

Layer 3 is read-then-act with no unique partial index on
`(enrollment_id) WHERE auto_sent`. Two DIFFERENT qualifying inbound replies in the
same thread, processed concurrently on separate Celery workers, can each read
`prior=0` and both auto-send — two auto-replies in one thread, violating the
advertised "ever" invariant. The window spans the second task's LLM round-trip
plus the first task's SMTP delivery (seconds), and requires Autopilot ON plus two
near-simultaneous qualifying replies from the same prospect (e.g. a double-send
from their client).

The existing tests prove the SEQUENTIAL invariant
(`test_thread_invariant_across_many_replies`: statuses sent/pending/pending/pending
— reproduced in the 560) and the same-draft/same-inbound races only. findings/11's
wording ("across 4 successive replies") is accurate about what was tested; the
plan's "one-reply-per-thread invariant under concurrent duplicate delivery (CAS
claim)" only ever meant layers 1–2.

### Verdict
VERIFIED that the guard is absent in code; UNVERIFIED as an actual reproduced
violation (would need parallel workers — not attempted, read-only audit). This is
"test passed" ≠ "behavior correct under concurrency": the tests that exist pass
and are honest, but they do not cover this interleaving.

### Product question for the human
Accept the small race window, or add a partial unique index (e.g.
`CREATE UNIQUE INDEX ... ON reply_drafts (enrollment_id) WHERE auto_sent AND
status IN ('sent','edited_sent')`) / row-lock on the enrollment before dispatch?
The index is a one-migration fix that makes the invariant structural.

---

## F-06 — Cal.com webhook: HMAC gate solid; no replay protection (bounded consequences)

**Severity:** low
**README claim affected:** none (new M4.3 surface)
**Status:** open

### Claim under test
Webhook is HMAC-SHA256-gated, 503 when `CALCOM_WEBHOOK_SECRET` empty, no auth
bypass or replay issue.

### Method
Read `meetings/providers.py`, `api/routers/meetings.py`; reviewed
`test_meeting_providers.py` (bad signature, exact-bytes, keyless-off) and
`test_meetings.py` (503 unconfigured, 401 bad signature, duplicate idempotent,
anonymous `/meetings` list still 401) — all inside the reproduced 560.

### Predicted
Gate correct; replay likely unaddressed (Cal.com's scheme has no timestamp).

### Actual
- `build_provider` returns None on empty secret → route raises 503 before reading
  the body. Verified by `test_webhook_503_when_unconfigured`.
- HMAC-SHA256 over the raw body, `hmac.compare_digest` (constant-time), 401 on
  mismatch or missing header. Empty-secret-HMAC bypass impossible (503 first).
- **Replay:** no timestamp/nonce — a captured valid payload replays successfully
  forever. Consequences are bounded by design: meeting upsert is idempotent on
  `provider_event_id`; the `booked_at is None` guard means the state-machine event
  fires at most once; no path from webhook to any email send. Residual effect: a
  replayed `BOOKING_CREATED` after a `BOOKING_CANCELLED` flips the meeting ROW
  status back to `booked` (funnel state and enrollment untouched). Data-quality
  nit, not a safety hole — and it requires possession of a validly-signed capture.

### Verdict
VERIFIED (gate behavior via reproduced tests; replay analysis from code — the
replay itself NOT exercised by any test, so that sub-property is characterized,
not tested).

---

## F-07 — Q2 draft-quality gate evidence ran on the wrong model (recorded, but the gate is weaker than approved)

**Severity:** medium (process, not code)
**README claim affected:** none
**Status:** open — human action required before merge

### Claim under test
Plan Q2: real-LLM (`LLM_PROVIDER=anthropic`) draft outputs recorded verbatim in
findings/10 for human veto before merge.

### Method
Read findings/10 and MEMORY (the invalid-key note); no re-run (would need a real
key — forbidden to this audit).

### Predicted
Anthropic-model evidence per the approved gate.

### Actual
The `.env` Anthropic key 401s (request id recorded), so the eval ran on **Ollama
qwen3:8b** — self-disclosed prominently in findings/10 with the correct framing
(lower bound; safety conclusions model-independent because the validator gates
output; quality conclusions NOT trustworthy). Two drafts quoted verbatim; the full
raw JSON lives only in an uncommitted session scratchpad, so the durable verbatim
record is partial. Additionally, a prompt change WAS made mid-eval (REPLY_SYSTEM
rule 6, word-form numbers) in response to eval failures — this is a
generation-prompt fix, not a validator/threshold change, so it doesn't violate the
§0 knob rule, but the human should know the recorded Run 2 reflects a prompt
tuned on Run 1's failures.

### Verdict
VERIFIED that the deviation is honestly recorded; the Q2 gate itself is
UNSATISFIED as originally approved. The session already says so ("re-run with a
valid key before trusting quality conclusions") and deliberately left the PR
unmerged. The veto decision must not be made on findings/10's quality numbers.

---

## F-08 — "Reply-to-auto-reply always escalates": implemented via the thread counter, not a distinct detector

**Severity:** informational
**README claim affected:** #7 (amended)
**Status:** open

### Claim under test
Plan: "the inbound is not itself a reply to an auto-reply (always escalates — no
AI↔human loops)".

### Method
Read `autopilot.py` (`prior_auto_replies` + the `>=` gate comment),
`test_second_reply_in_thread_escalates`, `test_thread_invariant_across_many_replies`.

### Actual
There is no "is this a reply to an auto-reply" predicate; the property falls out
of `prior_auto_replies >= 1 → decline`. Given the ≤1 invariant these are logically
equivalent (any reply-to-auto-reply implies a prior auto-send in the thread).
"Escalates" concretely means: the new reply still gets a validated PENDING draft
in the inbox, an `autopilot_declined` audit row, and the normal escalation-rule
actions (confident interested → Slack ping) — a human owns the thread. Verified by
the two tests above (in the reproduced 560). Note this equivalence inherits
F-05's caveat: under the concurrent race, "prior committed auto-reply" can lag
reality.

### Verdict
VERIFIED (behavior, sequentially).

---

## F-09 — Stale comment in commitment_terms.txt contradicts the implemented currency license

**Severity:** informational
**README claim affected:** none
**Status:** open

### Actual
`craftsman/copywriter/commitment_terms.txt` header: "any currency amount in a
draft must appear in the campaign config itself". The code (and APPLICATION_OVERVIEW's
new row, and deviation #2) say trusted sources = brief + campaign + persona. The
in-file comment predates the deviation. One-line doc fix; safety-relevant only in
that a future maintainer might "fix" the code to match the stale comment (which
would be a narrowing, so fail-safe).

### Verdict
VERIFIED (doc drift, not behavior).

---

## F-10 — Mandated safety-claim verdicts (code-verified, not findings-prose-verified)

**Severity:** informational
**README claim affected:** #2, #7
**Status:** open

Each checked against code + the reproduced suite, per the audit mandate:

1. **Human click is the only Copilot dispatch path — VERIFIED.** Exhaustive caller
   enumeration: `deliver()` is invoked from exactly `sender/reply.py:175` (draft
   dispatch), `workers/tasks.py:234` (pre-existing `generate_and_send`), plus the
   pre-existing Mailpit dry run. `send_reply_draft` has exactly two callers:
   `POST /inbox/drafts/{id}/send` (operate scope — the click) and
   `workers/tasks.py:_maybe_autopilot_send`, which is unreachable without passing
   `autopilot.decide()` (default-off flag). `generate_reply_draft` itself creates
   only a ReplyDraft row. Adversarial tripwire tests (`deliver` monkeypatched to
   raise) cover every label. Note the auto-send call site physically lives in
   `workers/tasks.py`, not `inbox/autopilot.py` — the policy is in autopilot.py,
   the dispatch is not; the property as meant (no ungated auto-send) holds.
2. **Commitment gate — VERIFIED as-implemented** (see F-04 for the recorded
   campaign-vs-trusted-sources deviation).
3. **Escalation union semantics — VERIFIED.** `evaluate()` is a pure union over
   enabled matching rules; `load_rules` ALWAYS prepends `default_rules()` from
   code — no DB row can remove, disable, or shadow the legal tripwire
   (`test_no_shadowing_possible`, `test_legal_threat_fires_regardless_of_label_and_confidence`).
   The tripwire matches on keywords alone (label/confidence-independent) and runs
   even on low-confidence inbounds (`handle_inbound` evaluates escalation on the
   review-queue path too). Legal path is double-covered: `block_draft` gates the
   enqueue AND the tripwire's `suppress` makes `generate_reply_draft` bail on
   `is_suppressed`.
4. **Autopilot invariants — VERIFIED** (with F-05's concurrency caveat).
   `MAX_AUTO_REPLIES_PER_THREAD = 1` is a module constant, asserted `== 1` in a
   test. The claimed 3,600-combination matrix is real:
   2(enabled)×5(skeleton)×5(confidence)×2(escalation)×3(prior)×2(sched)×2(info)×3(window)
   with `assert checked == 3600` and a both-regions-exercised assertion; the
   expected-value oracle is independently constructed in the test. Confidence
   boundary tested at exactly 0.9/0.8999. Allowed set proven EXACT over
   18 label×kind combos with real generation + captured delivery
   (`test_allowed_set_is_exact`). Validator-rejected fills never rescued
   (`test_autopilot_never_rescues_a_rejected_draft`). `block_autopilot` veto,
   admin-scope enable (403 for operate), operate-scope kill switch, migration 0014
   `server_default=false` — all present and tested. Validator gates unchanged
   (F-02).
5. **Cal.com webhook — VERIFIED** with the low-severity replay note (F-06).
6. **README amendment atomicity — VERIFIED.** `git show 4020c0f --stat` lists
   `README.md` (4 ±), `APPLICATION_OVERVIEW.md` (+3), and
   `craftsman/migrations/versions/0014_autopilot.py` (+31) in the same commit as
   `inbox/autopilot.py` and the enable/disable endpoints.

Minor test-plan drift, for completeness: the plan promised word-cap boundary tests
"at 120/121 words"; `test_word_cap_boundary` proves the boundary mechanism at
`max_words=6` instead. The 120 knob's wiring is separately confirmed at both call
sites (`reply_fill.py`, `sender/reply.py` edited-body path), so this is mechanism-
plus-wiring rather than the literal promised case. Not a weakening.

---

## Items a human must review before PR #6 merges

1. **F-07 / Q2 gate:** re-run the findings/10 draft-quality eval with a valid
   Anthropic key (the approved gate condition) and exercise the veto on that
   evidence — the current quality evidence is from qwen3:8b and the session itself
   says not to trust it.
2. **F-05:** decide whether the ≤1-auto-reply-per-thread invariant needs a
   structural (DB-level) guard against the concurrent-distinct-replies race, or
   whether the race window is acceptable. Cheap fix available.
3. **F-04:** confirm the recorded widening of currency licensing (brief + campaign,
   not campaign-only) is acceptable for Autopilot-eligible drafts.
4. **F-01:** sign off on the two deliberate test relaxations (terminal-state
   carve-out; unauth allowlist +1 route) — both recorded, both reviewed here as
   sound.
5. `next build` at tip remains unverified by any session's raw output (tsc/eslint
   are verified); run it once before merge if the web bundle matters to this PR.

## Behavior vs test-passed, stated plainly

- Reproduced-suite counts, escalation union, autopilot policy matrix, HMAC gate,
  commitment/currency gates, admin scoping: checked as **behavior** (code read +
  tests reproduced locally, 560/0/0).
- Per-commit intermediate counts, `next build`: **claims only**, UNVERIFIED.
- F-05 concurrency violation: **not reproduced** — asserted from code reading;
  the passing tests genuinely do not cover that interleaving.
- Draft QUALITY (as opposed to safety): explicitly NOT verified by anyone yet —
  that is the open Q2 gate.
