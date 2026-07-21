## F-05 — GDPR erasure cascade: fix + verification

**Severity:** critical (was) → fixed
**README claim affected:** #22 (`DELETE /leads/{id}/erase` fulfills erasure requests)
**Status:** fixed

### Claim under test
Erasure removes the person from every store. Pre-M0.4 it deleted only the `leads` row +
added suppression — and **raised IntegrityError for any lead with enrollments**, i.e. it
only worked on leads with nothing to erase.

### Method
Failing test written first (`test_erase_with_enrollment_no_integrity_error`) — shown
failing verbatim: `IntegrityError ... DELETE FROM leads WHERE leads.id = ...` (FK from
`enrollments.lead_id`). Then the cascade was implemented and the suite extended:
`tests/e2e/test_erasure.py` (4 tests) + `tests/unit/test_scrub_pii.py` (6 tests).
Full suite after: 143 passed, 0 skipped, Postgres up.

### What erase_lead now does (ordered, one transaction)
1. delete review-queue items (by enrollment or message link — payloads held generated
   copy naming the person and the lead's email)
2. **anonymize** audit rows: `enrollment_id → NULL`, identifiers scrubbed from `detail`
   (human decision 2026-07-21: keep the data; anonymized rows are outside GDPR scope)
3. delete messages (inbound bodies are prospect-authored PII)
4. delete enrollments
5. delete unsubscribe tokens by email
6. scrub the cached company research brief (email + full name always; first/last names
   individually when ≥ 3 chars; case-insensitive; `[redacted]` in place) — company facts
   stay; **no re-fetch**, so a team-page scrape can't reintroduce the name
7. delete the lead row
8. suppress(email, "gdpr") — survives by design as the do-not-contact record

### Verified properties (behavior, not just green tests)
- Zero-rows sweep across leads/enrollments/messages/review_queue/unsubscribe_tokens.
- Audit rows survive erasure with `enrollment_id IS NULL` and no identifiers in detail.
- Brief post-erase contains `[redacted]`, no name/email, and still contains company facts.
- Suppression survives with reason `gdpr`; `is_suppressed` true post-erase.
- Multi-lead isolation: colleague at the same company untouched, company row kept.
- Queued-task no-op: `enrich_lead` / `research_enrollment` / `generate_and_send` called
  with erased IDs create no rows and raise nothing (Celery payloads are IDs only —
  verified in `tasks.py`; the broker/result backend hold no PII).

### Known limits (documented, not hidden)
- Slack "interested" notifications already sent live in Slack — an external system out
  of erasure's reach. Operators handling a GDPR request must delete those manually.
- Mailpit/real inboxes hold delivered mail — likewise external to the database cascade.
- The scrub is substring-based: a person referred to only by nickname or title in the
  brief is not detected. Names < 3 chars are skipped by design (full name still caught).
