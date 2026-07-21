## F-07 — Send-path concurrency & reproducibility: fix + verification

**Severity:** high (was) → fixed
**README claims affected:** #11 (spacing), #12 (caps enforced), #24 (sim reproducibility)
**Status:** fixed

### Claims under test
(B3a) Per-campaign caps hold under concurrent workers. (B3b) A worker killed mid-send
does not cause a duplicate email. (B2) The bandit can be made reproducible.

### Method
`tests/adversarial/test_send_concurrency.py` (real threaded Postgres),
`tests/e2e/test_send_idempotency.py` (unique-index DB tests + task-level tests on
dedicated committed sessions), `tests/unit/test_bandit_seed.py`. Full suite after:
186 passed, 0 skipped, Postgres up.

### B3a — per-campaign cap race (confirmed, fixed)
- **Was:** `run_presend_checks` counted `Message` rows and compared to `daily_cap` — a
  read-then-act race; N workers all read 49/50 and all send.
- **Now:** atomic `reserve_campaign_slot` = `UPDATE campaigns SET sent_today = sent_today+1
  WHERE id=:id AND sent_today < daily_cap` (row lock serializes workers); `release_campaign_slot`
  on failure; `reset_daily_counters` zeroes it at midnight. New column `campaigns.sent_today`
  (migration `0002`).
- **Verified:** 12 threads racing a `cap=5` campaign → **exactly 5** reservations succeed,
  `sent_today == 5`. Reserve→full→release→reusable round-trips; release never goes negative.

### B3b — send idempotency (confirmed, fixed)
- **Was:** with `acks_late=True`, a crash after `deliver()` but before commit left the
  enrollment `ready`; the redelivered task re-sent. Nothing prevented duplicate outbound rows.
- **Now:** partial unique index `uq_outbound_step` on
  `messages(enrollment_id, step_order) WHERE direction='outbound'` (migration `0002`).
  `generate_and_send` inserts + **commits the claim before `deliver()`**; a redelivery trips
  IntegrityError and skips. In-process delivery failure deletes the claim + releases the slot
  and retries (so transient SMTP errors re-send); only a hard crash between claim-commit and
  deliver leaves a stuck claim (never-double, may-rarely-skip).
- **Verified:** unique index rejects a duplicate (enrollment, step) outbound; different steps
  and inbound rows are unaffected. Task-level: happy path delivers exactly once and finalizes
  (`sent_today == 1`); a pre-existing claim (simulating crash-before-finalize) causes the retry
  to **not deliver** and to release the briefly-reserved slot (`sent_today == 0`).

### B2 — seedable bandit RNG (fixed)
`get_bandit_rng()`: cached seeded generator when `BANDIT_SEED` is set (deterministic stream),
fresh `default_rng()` otherwise. Documented as sim/CI-only — a multi-worker prod would seed
every process identically and lose independence. Verified: seeded runs reproduce the pick
sequence; unset returns independent generators.

### Deferred to M0.6b
- **Re-drive of genuinely-unsent claims:** rows stuck with `sent_at IS NULL` older than N
  minutes (the rare hard-crash case) should be re-driven. Belongs with 0.6b's `error`/re-drive
  work, alongside the Celery dead-letter queue.
- Full `--concurrency=4` live-worker load test is a manual/ops check; the invariants here are
  covered deterministically (row-lock cap, unique-index idempotency).
