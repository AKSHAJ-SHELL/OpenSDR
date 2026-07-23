# M3 boundary audit (independent, fresh session) — 2026-07-23

Per ROADMAP execution rule 5 / TESTING.md §5: adversarial audit of the M3 diff
(`fc7604d..029e4c4`, commits 2fc6c9b / 280c919 / 9f4073e) run in a fresh session with
no prior context, assuming the previous session was motivated to look successful.

## Verdict: PASS-WITH-NOTES

## (a) Existing tests modified / skipped / weakened — NO ISSUES

- `git diff fc7604d..029e4c4 --diff-filter=DM --name-status -- tests/` → empty. The
  tests/ diff is exactly 7 new files (+1189/−0). Zero skip/xfail/loosened tolerances.
- New adversarial tests are substantive, not vacuous: SMTP tripwire via monkeypatched
  `craftsman.sender.smtp.deliver` raising on any call
  (`tests/adversarial/test_task_channel_attacks.py:101-105`); bit-for-bit
  `(variant.alpha, variant.beta)` posterior assertions (:206-217); idempotency tested
  against real committed sessions with IntegrityError (:146-201).

## (b) Thresholds / caps / parameters — NO ISSUES

- `config.py` additions only, byte-identical to the ⛔ Gate M3 sign-off in
  `plans/m3-multichannel-sequences.md:11-23`. No existing knob touched.
- `validator.py` append-only (`validate_fill` untouched); `machine.py` pre-existing
  transitions byte-identical; `idx_enroll_due` recreation pre-approved in the plan;
  migration 0010 has a symmetric downgrade.

## (c) Claims not backed by raw output — TWO FINDINGS (resolved by re-run)

1. **No M3 findings file exists** (findings stopped at `08-observability.md`);
   `findings/01-raw/` artifacts are pre-M3. The commit-message claims
   ("402/423/442 passed / 0 skipped; web tsc + eslint + build clean") had no raw
   output stored. **Independent re-run at 029e4c4:**

   ```
   .venv/bin/python -m pytest tests/ -q
   442 passed, 6 warnings in 5.17s
   ```

   `npx tsc --noEmit` → exit 0. `npx eslint .` → exit 0. `next build` NOT re-run —
   that sub-claim remains unverified. Arithmetic reconciliation of per-commit counts:
   77 M3 tests collect; 365+37=402 ✓, +21=423 ✓, +19=442 ✓.
2. **M3.1 commit message miscounts its unit tests**: claims "28 unit", actual 19
   (test_machine_channels 13 + test_channels_registry 6). e2e count and suite total
   correct — inaccurate breakdown, not a fabricated total.

## (d) Behavior characterized as correct without evidence — MINOR FINDINGS

- Complete-path suppression (`tasks.py` complete → 409 + cancel) present in code but
  **untested** (only the list-path cancellation is tested).
- `GET /tasks/{task_id}` does not re-check suppression — a suppressed lead's open
  task stays readable by direct ID; `POST /tasks/{id}/skip` also skips the check.
- `run_dry_run` is not channel-aware: always uses step 1's variants + the email
  copywriter. Mailpit-only, so a gap, not a send risk.

## Safety-claim verdicts (all independently verified in code + tests)

1. **Email is the only autonomous channel — VERIFIED.** Only two delivery call sites
   (`generate_and_send`, Mailpit dry run); channel guard precedes SMTP; task paths
   import nothing from `sender.smtp`; tick never falls through to `enqueue_send`.
2. **Suppression on generation AND read/complete — VERIFIED** with the two minor
   gaps above (single GET, skip).
3. **No double-advance from task completion — VERIFIED.** `resolve_task` refuses
   non-open tasks; only the tick TIMER increments `current_step`.
4. **Bandit posteriors untouched by task activity — VERIFIED.** α/β writes exist only
   in `bandit/settle.py`, driven exclusively by Message rows; task paths create none.

## Follow-ups applied on the M4 branch (same session, separate commit)

- Suppression re-check added to `GET /tasks/{task_id}` (cancels the open task) and
  `POST /tasks/{id}/skip` (cancel + 409, matching complete).
- Dedicated test for complete-path suppression + the two new checks.
- Dry-run channel-awareness deferred: recorded as a known gap (sandbox-only path);
  candidate for the M6 copy-lab work.
