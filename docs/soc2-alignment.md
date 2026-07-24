# SOC 2 alignment map

**This is ALIGNMENT, not certification.** SOC 2 is an audit of an
*organization's* controls performed by a licensed CPA firm over an observation
period. Software cannot "be SOC 2 compliant," and this open-source project has
no auditor, no observation period, and no report. What this document does is
honest and narrower: map the common SOC 2 control areas to the Craftsman
features a self-hosting organization would point its own auditors at. The gaps
column is part of the point.

| Control area | Craftsman feature (where) | What remains YOUR responsibility |
|---|---|---|
| **Access control** | Scoped API keys, hierarchical `read ⊂ operate ⊂ admin`, SHA-256 at rest (`api/auth.py`); fail-closed route audit — an unauthenticated new route breaks CI (`tests/e2e/test_auth_integration.py`); user RBAC `owner/operator/viewer` mapped to scopes (M5.1b); OIDC SSO with JIT provisioning off by default; break-glass password login (scrypt) | Key rotation cadence; offboarding process; IdP configuration and its MFA policy; who holds the admin keys |
| **Tenant isolation** | Central, session-layer org scoping — every ORM query filtered, fail-closed with no context (`core/tenancy.py`); adversarial isolation suite as gate evidence (`tests/adversarial/test_tenancy_isolation.py`, findings/13) | Running genuinely shared infra implies your own pen-testing; Postgres RLS is possible (org_id is denormalized everywhere) but not enabled |
| **Audit logging** | Append-mostly `audit_log` (state transitions, autopilot decisions, escalations, quota/admin actions); org-scoped NDJSON export `GET /audit/export` with `?since=` for SIEM pulls; retention knob `audit_retention_days` (default: keep forever) | Shipping exports somewhere durable; alerting on them; log retention policy that matches your compliance regime |
| **Data protection** | Mailbox credentials and webhook secrets Fernet-encrypted at rest (`core/crypto.py`); API keys stored as digests only; GDPR erasure cascade with PII scrubbing (`compliance/suppression.py erase_lead`); per-org suppression + optional global overlay; unsubscribe honored structurally (checked at generation AND send) | Postgres disk/backup encryption; TLS termination (reverse proxy/ingress); custody of `CRAFTSMAN_SECRET_KEY`; DPAs with your LLM/enrichment providers |
| **Availability** | Healthchecks on every service (compose prod profile, Helm probes); dead-letter table for terminally-failed tasks with re-drive; stateless workers → horizontal scaling (docs/operations.md); Redis loss is designed-for (rebuildable state, documented losses); webhook retries with backoff | Actual redundancy (replicas, managed Postgres with failover); backup execution and restore drills; monitoring/paging |
| **Change management** | Alembic-only schema changes with a CI no-drift guard (`tests/e2e/test_migrations.py`); 650+ test suite with 0-skip policy; adversarial tests travel with every autonomy-bearing feature; ⛔-gated product decisions recorded in plans/findings; knob defaults changeable only by recorded human decision | Your own review/approval workflow; environment separation; who may deploy |
| **Outbound integrity** (the domain-specific control) | No free-written outbound text — skeleton + validated slot fills, four-gate validator; no message passes the validator unvalidated, ever; Autopilot is opt-in, policy-gated, ≤1 auto-reply per thread structurally | Approving the skeletons; deciding whether Autopilot is on at all |

If you pursue an actual SOC 2 report for a Craftsman-based service: the
features above are evidence *sources*, not controls. Your policies, their
consistent operation, and the auditor's testing are the certification.
