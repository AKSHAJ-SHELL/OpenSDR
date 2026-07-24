# M5.1 tenancy isolation evidence — ⛔ Gate M5 review artifact — 2026-07-24

Branch `m5-enterprise-platform`. Suite at time of writing: **604 passed / 0
failed / 0 skipped** (Postgres up, `LLM_PROVIDER=mock`). The isolation suite is
`tests/adversarial/test_tenancy_isolation.py` (11 tests, predict-then-run per
TESTING.md §3).

## F-01 — Central org scoping holds across the API surface

**Severity:** informational (evidence of the property, not a defect)
**Status:** human-review (this IS the gate artifact)

### Claim under test
No request authenticated as org B can read, write, mutate, or infer the
existence of anything belonging to org A — enforced at the session layer
(`craftsman/core/tenancy.py`), not per-router.

### Method
Two-org fixture (org A = populated default org; org B = fresh org + its own
admin key). Every list endpoint hit with B's key; item endpoints hit with A's
real ids; forged org_id in payloads; suppression/unsubscribe cross-checks;
ORM-level write/move/delete attempts; worker bootstrap under a deliberately
wrong ambient org. Run: `pytest tests/adversarial/test_tenancy_isolation.py -q`.

### Predicted / Actual
All eleven predictions stated in-file before running. Two initial mismatches,
both resolved and re-run green:

1. `GET /campaigns/{id}/bandit` returned `200 []` for a foreign campaign
   instead of 404. Not an existence leak (nonexistent ids also gave `200 []`)
   but inconsistent with the foreign-id ≡ nonexistent convention — fixed in the
   router, re-run passes.
2. Two erase-path cases initially "failed" because the shared test session's
   identity map returned org A's row to `db.get()` without SQL — at which point
   the **flush-time guard refused the delete** (`TenancyError: delete of Lead
   owned by org …`). This is the second enforcement layer working, not a leak;
   production request sessions are fresh, so `db.get` always emits filtered
   SQL. The suite now clears the fixture identity map for production parity and
   keeps `test_cross_org_writes_and_moves_refused` as explicit proof of the
   flush-guard layer.

Final: `11 passed`.

### Verdict
VERIFIED (behavior, not just test-passed): list endpoints return zero foreign
rows; item endpoints 404 (never 403); forged payload org_ids are ignored and
rows land in the caller's org; erasure cannot cross orgs; analytics aggregate
only the caller's org; no-context queries fail closed with `TenancyError`;
cross-org ORM writes, deletes, and org moves are refused at flush; worker
tasks derive their org from the row they process, never ambient state.

## F-02 — Suppression scope decision implemented as approved (⛔ Q1a)

**Status:** human-review

Per-org lists + optional global overlay: suppressing an address in org B
leaves the same address contactable in org A (`test_suppression_is_per_org`);
the overlay, when enabled, suppresses additively in every org and cannot be
shadowed (`test_global_overlay_is_additive`); an unsubscribe token suppresses
only in the org that sent the mail (`test_unsubscribe_token_scopes_to_its_org`).
Propagation of unsubscribes to the overlay is a second opt-in knob and only
carries consent-shaped reasons (unsubscribe/gdpr — never bounce/manual).

## F-03 — Known residuals (documented, not defects)

- **Identity-map caveat:** `Session.get()` on an id already loaded in the same
  session bypasses the SQL filter; the flush guard still blocks writes, and no
  foreign row can *enter* an identity map through a filtered query. Risk is
  confined to sessions that mix `unscoped_context` loads with later same-id
  gets — the security-critical SSO exchange uses an explicit filtered SELECT
  for exactly this reason.
- **`/metrics` and the Cal.com webhook are instance-wide by design** (counts
  only / HMAC-gated with per-row org attribution) — both documented at the
  use site of `unscoped_context()`, which remains the single grep-able escape
  hatch (module docstring lists every legitimate use).
- **Postgres RLS not wired** — the denormalized org_id makes it possible
  later; recorded as defense-in-depth debt in the plan's non-goals.
