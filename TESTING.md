# Craftsman — Verification & Hardening Plan

> **For Claude Code.** This document is the work order. Execute it in phases, in order.
> Do not skip ahead. Do not begin Phase 3 until a human has reviewed the Phase 2 findings.
>
> **Prime directive:** The goal is to find out whether the README is true. A red test
> suite is a successful outcome. Making tests pass is not the objective; establishing
> ground truth is.

---

## 0. Working agreement (binding)

### Never

- Modify, delete, skip, or weaken an existing test in order to make a suite pass.
- Add `pytest.mark.skip`, `xfail`, `try/except` around an assertion, or loosen a
  tolerance without asking first.
- Change validator thresholds, fuzzy-match ratios, bandit parameters, banned-phrase
  lists, or length caps in order to fit a test result.
- Send mail to any host other than the local Mailpit sandbox (`localhost:1025`).
- Use a real `ANTHROPIC_API_KEY`. Default to `LLM_PROVIDER=mock` unless a phase
  explicitly says otherwise **and** the human has approved it in that session.
- Commit, push, branch, rebase, or touch git history.
- `docker compose down -v` or otherwise destroy volumes.

### Always

- Report failures verbatim, including full stack traces. Truncating output hides bugs.
- Distinguish **"the test passes"** from **"the behavior is correct."** State which
  you actually checked.
- If a claim cannot be verified with the tools available, write `UNVERIFIED` and say
  why. Never infer a pass from reading code.
- Write every finding to `findings/NN-topic.md` using the template in §7.
- Prefer *characterizing* current behavior over asserting desired behavior. When they
  differ, that difference is the finding.

### Ask the human before

- Installing any package, or modifying `pyproject.toml` / `package.json`.
- Editing `.env`, `docker-compose.yml`, or any SMTP/provider config.
- Any change to a file under `craftsman/` (as opposed to `tests/` or `findings/`).
- Running anything that costs money or touches the network beyond localhost.

### Session hygiene

Run each phase as a **separate session with cleared context**. Mixing investigation
and repair in one session is the single most common cause of an agent quietly
patching tests instead of code.

---

## 1. Phase 0 — Read-only reconnaissance

**Writes allowed:** `findings/00-map.md` only.

### Tasks

1. Trace and document the real call path from lead ingest → SMTP dispatch. Name the
   actual functions, not the README's description of them.
2. Enumerate **every** code path by which LLM-generated text can reach the send
   engine. For each, state whether it passes through `craftsman/copywriter/validator.py`.
   Pay specific attention to:
   - the retry path (does the *retry* output get re-validated?)
   - the human-review queue (can a reviewer release an unvalidated fill?)
   - any admin/test/debug endpoint that composes or sends mail
   - any template preview or "send test email" affordance in `web/`
3. Enumerate every path by which the system can produce an *outbound* message in
   response to an *inbound* one. The README claims this is impossible. Verify or refute.
4. List all external network calls the system makes and from which module.
5. List every place a threshold, cap, or magic number is defined, with file:line.

### Deliverable

`findings/00-map.md` containing the call graph, the two path enumerations above, and
an **Open Questions** section. Guessing is not permitted — anything ambiguous goes in
Open Questions.

---

## 2. Phase 1 — Baseline, unmodified

**Writes allowed:** `findings/01-baseline.md`, `findings/01-raw/*.txt`.

Change **nothing** in this phase. Not even a typo.

### Tasks

```bash
cp .env.example .env          # ask first if .env already exists
# set LLM_PROVIDER=mock
docker compose up -d postgres redis mailpit
pip install -e ".[dev]"

pytest -v --tb=long 2>&1 | tee findings/01-raw/pytest.txt
pytest --collect-only -q 2>&1 | tee findings/01-raw/collected.txt
```

### Deliverable

`findings/01-baseline.md` reporting:

- Pass / fail / skip counts, and the **full text of every failure**.
- **Every skipped test, with the skip reason.** If a skip is conditional
  (e.g. "Postgres absent"), state whether the condition was met — a suite that
  silently skips its integration layer is reporting a false green.
- Does `.env.example` actually contain every variable the app requires? List any
  missing ones. Test the cold-start claim: did `docker compose up` work first try,
  or did migrations race Postgres?
- **Coverage gap analysis:** what do the 54 tests *not* cover? Map tests to the
  modules in the README's key-modules table and name the uncovered ones. This is
  the most valuable output of the phase — be thorough and specific.

---

## 3. Phase 2 — Adversarial characterization

**Writes allowed:** `tests/adversarial/**`, `findings/02*.md`. Nothing under `craftsman/`.

### Method (mandatory for every case)

For each test case, in this order:

1. **Predict** the outcome in a comment above the test, before running anything.
2. **Run** it.
3. **Report** prediction vs. actual in the findings file.
4. Where they differ — that is a finding. **Flag it. Do not fix it.**

Writing the assertion after seeing the output is transcription, not testing. Don't.

### 3.1 Validator — `tests/adversarial/test_validator_attacks.py`

The fuzzy match at ≥ 0.9 is the primary attack surface.

| Case | Brief contains | Fill contains | Concern |
|---|---|---|---|
| Magnitude | `$4M` | `$40M` | One char apart — does 0.9 pass it? |
| Magnitude | `1,000` | `10,000` | Same, with separators |
| Round | `$4.2M` | `$4M` | Silent rounding |
| Normalization | `1,000` | `1000` | False *rejection* risk |
| Normalization | `Q3 2025` | `third quarter` | False rejection risk |
| Entity | `Acme Corp` | `Acme Group` | Suffix swap |
| Entity | `Series A` | `Series B` | One char |
| Entity | `Anthropic` | `Anthropics` | Pluralization |
| Casing | `iPhone`, `deepmind`, `eBay` | — | Capitalization heuristics miss these |
| Casing | sentence-initial common noun | — | Spurious proper-noun flag |
| Dates | `March 2024` | `2024` | Partial match |
| Percent | `12%` | `12x` | Unit swap |

Then, banned phrases: altered case, trailing punctuation, embedded whitespace,
unicode lookalikes, and near-variants ("Hope this email finds you well!").

Then, length/grade caps: exactly-at-boundary and one-over for subject (7 words),
body (90 words), reading grade (8). Include a body with a URL and one with an
em-dash — tokenizers disagree about these.

**Explicitly report:** how proper nouns are detected, what the similarity metric is,
and whether numbers go through the same fuzzy path as text. If numbers are fuzzy-matched,
say so prominently — that's a correctness bug, not a tuning question.

### 3.2 Bandit — `tests/adversarial/test_bandit_delayed_feedback.py`

The simulator converges because rewards are instant. Reality is not.

- **Failure accounting.** When is a non-reply recorded as a failure? If a send
  immediately posts a failure and a later reply flips it, verify the update *undoes*
  the failure rather than only adding a success. Trace the `settle` queue and
  document the settlement window.
- **Late replies.** A reply arriving after settlement — is it credited, dropped, or
  double-counted? Test all three orderings.
- **Attribution.** Lead receives step 1 (variant A) and step 2 (variant B), then
  replies. Which arm gets credit? Verify via `Message-ID` / `In-Reply-To` threading,
  and test a reply with no threading headers at all.
- **Null-effect deactivation.** Simulate N arms with *identical* true rates at
  realistic volume. Assert no arm deactivates. If one does, the threshold is killing
  arms on variance alone — quantify the false-deactivation rate over 1000 seeds.
- **Cold start.** New arm added to a mature campaign — does it ever get sampled?
- **Determinism.** Is the sampler seedable? If not, flag it; unseedable randomness
  makes every downstream test flaky.

### 3.3 Scheduler & concurrency — `tests/adversarial/test_send_scheduling.py`

- **Timezone inference.** Document the actual mechanism. Test: `.com` address for a
  Tokyo company; lead with no location data at all; a DST spring-forward boundary;
  a lead whose local window has already closed today.
- **Per-mailbox spacing under concurrency.** Run Celery with `--concurrency=4` against
  Mailpit, dispatch 50 sends, and measure the actual inter-send gaps per mailbox.
  Assert 45–90s holds. **Then report the mechanism**: is spacing enforced by a Redis
  lock, a DB row lock, or per-worker in-memory state? In-memory state is broken under
  multi-worker and a green test would be luck.
- **Idempotency.** Kill a worker mid-send (`docker kill` the container between
  dispatch and ack) and let Celery retry. Does the lead receive two emails? Verify
  the dedupe check happens *before* dispatch, not after.
- **Suppression race.** Unsubscribe a lead between generation and send. Confirm the
  send-time check queries live state rather than a cached snapshot.
- **Cap enforcement.** Per-campaign and warmup caps under concurrent workers — can
  4 workers each pass a check that should have failed collectively?

### 3.4 Classifier — extend `scripts/eval_classifier.py` fixtures

Requires a real API key — **stop and ask the human before running this one.**

Add adversarial fixtures beyond the existing 32:

- Hostile-but-engaged: "stop emailing me, but what's your pricing?"
- Forward from a colleague (From address ≠ lead address)
- Reply quoting the entire original email (does it classify its own copy?)
- Non-English replies (at minimum: Spanish, German, Japanese)
- Legal threat / GDPR demand → must suppress **and** alert a human, never "not interested"
- Bounce that contains the word "interested" in the quoted body
- Out-of-office that names a colleague to contact instead
- A single-word reply: "no", "yes", "?"

**Then the safety property that matters most:** for *every* classification outcome,
including misclassification, verify no code path emits an LLM-authored reply. Test
this by forcing each label programmatically and asserting on outbound messages.

### 3.5 Compliance & erasure — `tests/adversarial/test_compliance.py`

- `DELETE /leads/{id}/erase` — verify the delete cascades to **all** of: the leads
  table, pgvector embeddings, the 30-day research cache (which likely names the
  person), Celery task payloads still queued in Redis, sent-message records, and any
  logged email bodies. Erasure that leaves the name in a cached brief is not erasure.
  Enumerate every store that holds lead-derived data and check each one.
- Inspect the raw message source in Mailpit and assert on headers: `List-Unsubscribe`
  **and** `List-Unsubscribe-Post: List-Unsubscribe=One-Click`. Confirm the unsubscribe
  endpoint accepts an **unauthenticated POST** (RFC 8058 requires it).
- Physical address present in every footer, including the shortest possible body.
- GDPR mode: verify EU-TLD blocking works, then document that it is a weak heuristic —
  an EU resident with a `.com` Gmail is still covered. This belongs in the README.

### 3.6 Security — `findings/02-security.md`

**Investigate and report only. Do not write exploit code that touches the network.**

- **Auth.** Is there any authentication on the FastAPI app or the Next.js dashboard?
  If not, this is the highest-severity finding in the repo: a self-hoster exposing
  port 8000 has published their lead database and an open mail cannon.
- **SSRF.** The research agent fetches company URLs. Can a crafted CSV row point it
  at `169.254.169.254`, `localhost:6379`, or a `file://` URL? Report whether there is
  any allowlist, scheme check, or private-IP guard.
- **Prompt injection.** Research briefs are LLM-summarized web text flowing into the
  copywriter. Construct a *local fixture* page containing adversarial instructions and
  trace whether they can influence slot fills. The slot-fill + validator design should
  contain this — verify that it actually does, and identify what the validator would
  *not* catch (e.g. injected text that is genuinely present in the brief, and so
  passes the grounding check).
- **CSV import.** Formula injection (`=cmd|...`) on re-export; oversized files;
  malformed unicode; header injection via lead fields reaching email headers.
- **Secrets.** Any key, token, or `CRAFTSMAN_SECRET_KEY` logged, echoed in an error
  response, or exposed via `/docs`.

---

## 4. Phase 3 — Repair (human-gated)

**Do not begin until a human has read `findings/` and told you which items to fix.**

Per approved finding, in this order:

1. Write a **failing** test that captures the bug. Show it failing.
2. Propose the fix in prose and wait for approval before editing `craftsman/`.
3. Apply the minimal fix.
4. Show the new test passing **and** the entire pre-existing suite still passing.
5. If a pre-existing test now fails, **stop and report** — do not adjust it.

One finding per commit-sized unit of work. Never batch.

---

## 5. Phase 4 — Independent audit

Run this in a **fresh session with no prior context.** Prompt:

> Read `findings/` and the full `git diff`. Act as an adversarial auditor. Identify:
> (a) any existing test that was modified, skipped, or weakened; (b) any threshold,
> cap, or parameter changed outside an approved fix; (c) any claim of "verified" or
> "passing" not backed by raw output in the findings; (d) any place where behavior
> was characterized as correct without evidence. Assume the previous session was
> motivated to look successful.

---

## 6. README claim checklist

Every claim below gets an explicit verdict: `VERIFIED` / `REFUTED` / `UNVERIFIED`,
each with a pointer to raw evidence.

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| 1 | `docker compose up` is the whole deployment | | |
| 2 | The LLM never free-writes an email | | |
| 3 | Every proper noun/number is backed by the brief (fuzzy ≥ 0.9) | | |
| 4 | Rejected fills retry once, then go to human review | | |
| 5 | Rejection rate is a dashboard metric | | |
| 6 | Subject ≤ 7 words, body ≤ 90 words, grade ≤ 8 enforced | | |
| 7 | Never auto-replies to an interested human | | |
| 8 | Interested replies stop the sequence and ping Slack | | |
| 9 | Never sends to unverified emails (syntax → MX → SMTP) | | |
| 10 | Sends land in lead-local 9:00–16:30, jittered | | |
| 11 | One send per mailbox per 45–90s | | |
| 12 | Warmup ramps and per-campaign caps enforced | | |
| 13 | Bandit posteriors update from classified replies | | |
| 14 | Lagging arms auto-deactivate | | |
| 15 | 54 tests pass | | |
| 16 | Unit layer needs no API key and no network | | |
| 17 | Integration tests skip cleanly without Postgres | | |
| 18 | Suppression checked at generation *and* send time | | |
| 19 | RFC 8058 one-click unsubscribe | | |
| 20 | CAN-SPAM physical address in every footer | | |
| 21 | GDPR mode blocks EU-TLD enrollment | | |
| 22 | `DELETE /leads/{id}/erase` fulfills erasure requests | | |
| 23 | Ollama fallback works at $0 marginal cost | | |
| 24 | Simulator converges; best arm captures ~74% of traffic | | |

---

## 7. Findings template

Every entry in `findings/` uses this shape. No exceptions.

```markdown
## F-NN — <one-line title>

**Severity:** critical | high | medium | low | informational
**README claim affected:** #N, or "none"
**Status:** open | human-reviewed | fixed | won't-fix

### Claim under test
What the README or code implies should be true.

### Method
Exact commands run, fixtures used, environment. Reproducible by a stranger.

### Predicted
What I expected before running it.

### Actual
Raw output. Verbatim. Not summarized.

### Verdict
VERIFIED / REFUTED / UNVERIFIED — and whether I checked *behavior* or only
that a test passed.

### Product question for the human
Where the correct behavior is a judgment call rather than a code question.
State the options and the tradeoff. Do not decide unilaterally.
```

---

## 8. Escalate to the human immediately

Stop work and ask when you hit any of these:

- A finding implies the system could send mail a human didn't intend.
- A finding implies the "never auto-reply" guarantee can be broken.
- Any test requires a real API key, real SMTP, or non-localhost network.
- A fix would require changing a threshold, cap, or model parameter.
- A pre-existing test fails after your change.
- You're about to conclude "this is fine" without raw output to back it.
- Anything under §3.6 Security returns a positive result.

---

## 9. Polish backlog (after verification, not before)

Do not start these until §6 is fully filled in.

- Document DKIM / SPF / DMARC setup prominently. The README correctly says
  deliverability is the hard part, then the quickstart never mentions it.
- Add API-key middleware to FastAPI and auth to the dashboard, plus a loud warning
  in the quickstart about exposing port 8000.
- `make demo` — seed + simulator + dashboard in one command for the first-run path.
- Surface validator rejection rate and classifier confidence distribution on the
  dashboard if not already present.
- Dry-run campaign mode: route a real campaign end-to-end through Mailpit as a preflight.
- Soften the GDPR-mode claim in the README to reflect that TLD blocking is a heuristic.
- Add a seeded-RNG mode to the bandit so simulator output is reproducible in CI.
