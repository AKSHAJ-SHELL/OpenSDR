# M4 session record — reply handling & meeting booking (G7–G9) — 2026-07-23

Evidence discipline per TESTING.md (and per the M3 audit's finding that M3 skipped
this file). Branch `m4-reply-handling`, commits: audit follow-ups (85118c8), M4.1
(6a622c9), gate evidence (2ef324f), M4.2 (7477c83), M4.3 (e0bae0a), M4.4 (4020c0f).

## Gate decisions (recorded before implementation, plans/m4-reply-handling.md)

- **Q1 — Option B approved**: Copilot everywhere + opt-in Guarded Autopilot.
  README guarantee amended in the same commit as the flag (4020c0f).
- **Q2 — second gate**: draft-quality evidence recorded (`findings/10`) for human
  veto **before merge** — the PR is opened but deliberately not merged by this
  session.

## Raw verification (merged branch tip, this session)

```
$ .venv/bin/python -m pytest tests/ -q
560 passed, 14 warnings in 6.91s        (0 failed, 0 skipped; 560 collected)
```

Per-commit suite sizes: 445 (audit follow-ups) → 493 (M4.1) → 513 (M4.2) → 534
(M4.3) → 560 (M4.4). Web at tip: `tsc --noEmit` exit 0, `eslint src --quiet`
exit 0, `next build` success — run before each commit that touched `web/`.
Migration chain 0011→0014 verified by `tests/e2e/test_migrations.py` (upgrade to
head, model parity via autogenerate-diff, downgrade) — 3 passed.

## Deviations from the plan (all deliberate, none silent)

1. **Escalation is union-of-matches, not first-match-by-priority.** First-match
   would let a benign notify rule shadow the legal-threat suppression. Priority
   orders evaluation/reporting only. Recorded in `inbox/escalation.py` module doc
   and the rule-create endpoint docstring.
2. **Currency licensing widened from campaign-only to trusted-sources** (campaign
   + research brief): "congrats on the $4M raise" is core product behavior vetted
   at the same standard as opener emails; the dangerous case — a price grounded
   only in the prospect's reply — still rejects. Covered by
   `test_prospect_currency_cannot_be_echoed` / `test_brief_currency_is_licensed`.
3. **`AUTOPILOT_FOLLOWUP_WEEKS` shipped as `reply_followup_weeks`** — it drives
   Copilot timing drafts too, not just Autopilot.
4. **`test_terminal_states_are_closed` narrowed**, not weakened: the roadmap-
   mandated `replied_* → MEETING_BOOKED` exits are enumerated as the exact
   carve-out; any other terminal exit still breaks the test by construction.
5. **Campaign daily cap NOT applied to reply dispatch** (mailbox limits + rate
   limits + suppression still apply): the cap bounds cold outbound; blocking an
   answer to an engaged human on cold-send budget would be the wrong safety.
   Documented in `sender/reply.py` module doc.
6. **Reply-draft generation idempotency claims before the LLM call** (not after,
   as touch tasks do) — a redelivered Celery task must not burn tokens or race a
   human already acting on the first draft.

## Bug found in pre-existing code (fixed in 85118c8)

`complete`/`dial` suppression paths cancelled the open task and raised 409 — but
`get_db` rolls back on exception, so the cancellation never persisted in
production. Fixed by committing the cancellation before raising; regression tests
added for all three task suppression paths.

## Safety properties, adversarially proven (predict-then-run)

- Forced misclassification of every label: zero sends without a human click
  (Copilot), and with Autopilot ON the auto-send set is EXACTLY
  {interested×any, objection×timing, objection×info} under perfect conditions
  (`test_allowed_set_is_exact`, 18 combos).
- Prompt injection in a reply cannot buy a discount, echo an attacker's price, or
  survive as a pending draft; validator-rejected fills are never rescued by
  autonomy.
- ≤ 1 auto-reply per thread across 4 successive replies: statuses
  `sent, pending, pending, pending`; the invariant is a code constant, not a knob.
- Bandit posteriors bit-identical through draft generation, human send, and
  auto-send; replies never advance sequences.
- Legal/GDPR keywords: suppress + urgent notify + review + no draft + no
  autopilot, at any confidence, under any label, unshadowable by DB rules.
- 3,600-combination exhaustive gate matrix over `autopilot.decide` — none skipped.

## Known gaps / deferred

- Draft-quality eval ran on Ollama qwen3:8b — the `.env` Anthropic key is invalid
  (401). Re-run `findings/10` eval with a valid key before trusting quality (not
  safety) conclusions.
- Dry run remains channel-unaware (M3 audit note; Mailpit-only, no send risk).
- Cal.com is the only implemented CalendarProvider; GCal/MS Graph are documented
  fork points on the protocol.
- `calcom_api_key` is reserved (unused); only the webhook secret is required.
