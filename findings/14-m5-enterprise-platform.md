# M5 session record — enterprise platform (G10/G12/G13 minus CRM) — 2026-07-24

Evidence discipline per TESTING.md. Branch `m5-enterprise-platform`. Commits:
audit follow-ups + F-05 fix (b9d9d6b), M5.1a (1fe42ee), M5.1b API (6dffc13),
M5.1c (43a42f3), M5.1d (f2bdf87), M5.1b web (8ab7821), M5.3 (081974f), M5.4 +
docs (this commit).

## Gate decisions (recorded before implementation, plans/m5-enterprise-platform.md)

- **Q1 → (a)**: per-org suppression + optional global overlay (additive,
  boolean-only, propagation opt-in and consent-reasons-only). Verified in the
  isolation suite (findings/13 F-02).
- **Q2 → approved**: session scope M5.1 + M5.3 + M5.4; **M5.2 CRM sync
  deliberately absent** — the gate text requires human review of the isolation
  evidence before CRM sync begins. findings/13 is that review artifact.
- **Q3 → approved**: `authlib` dependency for OIDC.
- **Q4 → done**: PR #6 merged after the fresh-session M4 audit (findings/12,
  PASS-WITH-NOTES); F-05 fixed structurally as this branch's first commit.

## Suite evidence (raw)

- Start of session (M4 merged): `560 passed` → after F-05 fix: `562 passed`
  → after M5.1a: `562 passed` → M5.1b API: `586` → M5.1c: `593` → M5.1d: `604`
  → M5.3: `650` → M5.4: `690 passed, 0 failed, 0 skipped` (Postgres up,
  `LLM_PROVIDER=mock`). Reproduce: `docker compose up -d postgres redis mailpit
  && python -m pytest -q`.
- Web at every web-touching commit: `npx tsc --noEmit`, `npx eslint src
  --quiet`, `npm run build` all clean.
- `ruff check craftsman tests`: one pre-existing `dns_auth.py` E741 remains
  (out of scope, noted since M4).
- Migrations 0015–0018 covered by upgrade→head + downgrade round-trip + the
  `alembic check` no-drift guard (tests/e2e/test_migrations.py, green).

## Deviations from the roadmap text (with rationale)

1. **org_id on ALL tenant tables**, not only the six roots the roadmap names —
   list endpoints query child tables directly; join-derived scoping per-router
   is exactly the per-router enforcement the roadmap forbids. Denormalization
   is what makes central enforcement (and later optional RLS) possible.
2. **OIDC lives in FastAPI**, not the Next proxy — SSO covers API-first
   deployments; the dashboard consumes the same flow.
3. **Suppression PK became a surrogate id** (+ UNIQUE(org_id, email)) — the
   old `email` PK cannot express per-org lists. Breaking for direct-SQL users;
   called out in the PR body.
4. **Org quotas bound cold sends only** — replies to engaged humans follow the
   M4.1 campaign-cap doctrine (mailbox limits still bound them).
5. **manage_org is a CLI, not an API** — a tenant that could raise its own
   quotas would not have quotas; host operations happen on the box, like
   create_key.
6. **Placement suppressed-seed stance**: seeds are operator-owned test
   accounts, not prospects — suppression (a prospect concept) does not block
   placement sends; recorded in `deliverability/placement.py` and asserted
   adversarially.
7. **lead.status_changed webhook granularity**: emitted from classification
   transitions and manual suppression only (not every ingest-time flip) —
   documented honestly in `webhooks/events.py`.

## Bugs found and fixed along the way (not pre-planned)

- **F-05 (from findings/12)**: ≤1-auto-reply-per-thread was read-then-act →
  now structural (claim-time `auto_sent` stamp under partial unique index
  `uq_auto_reply_per_thread`, migration 0015) with race + reservation-release
  adversarial tests.
- **`record_bounce` demoted paused mailboxes**: a straggler bounce set
  `health="degraded"` on a paused mailbox, returning it to the sendable pool.
  Paused now outranks degraded (M5.3 commit).
- **`/campaigns/{id}/bandit` returned `200 []` for foreign/nonexistent ids** —
  now 404, matching the foreign-id ≡ nonexistent convention (M5.1d commit).

## Known gaps / open items for the human

1. **⛔ Gate M5 review**: read findings/13 (tenancy isolation evidence) before
   M5.2 CRM sync begins — the roadmap's own precondition.
2. **F-07 from the M4 audit remains open**: re-run the M4 draft-quality eval on
   the approved Anthropic model once a valid key exists (safety conclusions
   held on the local-model floor; quality conclusions still rest on it).
3. **OIDC validated against a stubbed IdP only** — state/nonce/link/provision
   logic is fully tested; the authlib JWKS validation path has not been run
   against a live provider. UNVERIFIED until someone logs in against a real
   Google/Okta/Entra app.
4. **Postgres RLS**: possible now (org_id everywhere), not wired — recorded
   defense-in-depth debt.
5. **Identity-map caveat** (findings/13 F-03): `Session.get` can bypass the
   SQL filter for already-loaded ids; flush guard blocks writes; fresh
   per-request sessions make the read path safe. Documented, monitored by the
   isolation suite.
6. **Placement marking is manual (v1)** — IMAP-crawl automation is future work.
7. **Helm chart is a starting point** — rendered/linted (`helm lint`/`helm
   template`), never applied to a live cluster this session.
