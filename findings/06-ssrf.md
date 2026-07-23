## F-06 — SSRF guard on the research fetcher: fix + verification

**Severity:** high (was) → fixed (with one documented residual)
**README claim affected:** none directly; supports the "self-host safely" posture
**Status:** fixed

### Claim under test
`research/fetch.py` fetches company URLs derived from a CSV-supplied `company_domain`.
Pre-M0.5 it built `https://{domain}` and followed redirects with no validation, so a
crafted CSV row could point it at internal infrastructure.

### Method
Confirmed the data path by reading `ingest/csv_import.py` (domain taken verbatim,
`.strip().lower()` only) → `Company.domain` → `fetch.py:37`. Then:
`tests/unit/test_fetch_ssrf.py` (24 pure tests) and
`tests/adversarial/test_fetch_ssrf_attacks.py` (7 offline tests via a fake httpx
client). Full suite after: green, 0 skipped, Postgres up.

### The four named vectors — predicted vs actual
| Vector | Predicted | Actual |
|---|---|---|
| `169.254.169.254` (cloud metadata) | blocked before any request | blocked, `client.calls == []` |
| `localhost:6379` (Redis) | blocked (port not allowed) | blocked, no request made |
| `file://` redirect target | first hop fetched, `file://` blocked on scheme | blocked, file URL never fetched |
| `evil.com → http://169.254…` | first hop fetched, redirect blocked (scheme+IP) | blocked, metadata host never fetched |

Also verified: >3-hop redirect chains stop; a normal public host still succeeds; one
legitimate public→public redirect is followed.

### The guard (`craftsman/research/fetch.py`)
- `validate_url`: https-only; port ∈ {443, default}; host resolved and **every** A/AAAA
  record must be public (`_is_public_ip` rejects private, loopback, link-local, CGNAT
  `100.64/10`, multicast, reserved, unspecified). A single private record rejects the host.
- `_safe_get`: `follow_redirects=False`, manual loop ≤ `MAX_REDIRECTS` (3), each hop
  re-validated. DNS runs in a thread executor so it doesn't block the async gather.
- Blocked URLs are skipped + logged; a fully-poisoned domain yields no sources →
  existing `ResearchError` → the enrollment goes `research_failed` and never sends.

### Documented residual (approved by human 2026-07-21, Q1)
**DNS-rebinding TOCTOU:** between `validate_url`'s resolution and httpx's own resolution
at connect time, a hostile resolver could return a public IP to the check and a private
IP to the connection. Fully closing this requires pinning the socket to the validated IP
while preserving SNI/cert validation — a transport-level change deferred to a future
hardening pass. Impact here is bounded: the fetched bytes only feed the grounded
copywriter/validator, so the residual is read-only probing, not exfiltration.

Second minor residual: the port allowlist is strict (443 only), so a legitimate company
site on `:8443` is refused. Accepted for a research fetcher (human Q2).
