# Phase 0 — Read-only reconnaissance map

**Scope:** Static code reading only. Nothing was executed, no tests were run, no
services were started. Every statement below is grounded in source at `file:line`.
Where behavior depends on runtime state that cannot be settled by reading, it is
marked `UNVERIFIED` and moved to **Open Questions**. Per the working agreement,
guessing is not permitted.

**Method:** Direct reads of the core path (`copywriter/fill.py`, `copywriter/validator.py`,
`workers/tasks.py`, `inbox/pipeline.py`, `sequencer/machine.py`, `core/config.py`, `README.md`)
plus four parallel read-only sweeps (ingest→SMTP trace, inbound→outbound trace,
LLM→send bypass hunt, network-call + threshold inventory). Findings below are the
intersection of the direct reads and the sweeps; nothing is reported that only one
source asserted without a `file:line` anchor.

---

## 1. Call graph: lead ingest → SMTP dispatch

The path is **not synchronous**. It spans one operator action (`activate`), a 60s beat
tick, and three Celery task boundaries. A CSV row cannot reach SMTP in a single request.

```
POST /leads/import                         craftsman/api/routers/leads.py:16  (async route)
  └─ import_csv(db, raw)                    craftsman/ingest/csv_import.py:35
       ├─ syntax_ok(email)                  craftsman/ingest/verify.py:15
       ├─ domain_of(email)                  craftsman/ingest/verify.py:19
       ├─ dedupe vs Lead.email              csv_import.py:42-44
       ├─ dedupe vs SuppressionEntry.email  csv_import.py:45-49
       └─ persist Company + Lead(status new) csv_import.py:74, :80-90
  └─ enrich_lead.delay(lead.id)             leads.py:28     ── Celery enqueue → queue "enrich"

enrich_lead(lead_id)                        craftsman/workers/tasks.py:195  ── Celery execute
  └─ verify_email(lead.email)               craftsman/ingest/verify.py:47
       ├─ syntax_ok                         verify.py:49
       ├─ mx_hosts (DNS MX)                 verify.py:51
       └─ smtp_rcpt_ok (optional, do_rcpt) verify.py:55
     ⇒ lead.email_verified=True, status="verified"   tasks.py:203-205

POST /campaigns/{id}/activate               craftsman/api/routers/campaigns.py:75  (operator)
  ├─ select verified leads                  campaigns.py:96-98
  ├─ ICP score                              campaigns.py:104
  └─ Enrollment(state="queued", step=0)     campaigns.py:118-124

beat every 60s → sequencer_tick             craftsman/workers/tasks.py:26  ── Celery execute
  └─ tick(db, enqueue_research, enqueue_send) craftsman/sequencer/tick.py:59
       ├─ due_enrollments (FOR UPDATE SKIP LOCKED) tick.py:45
       ├─ queued  → set "researching", enqueue_research(eid)  tick.py:70 → queue "research"
       ├─ ready   → enqueue_send(eid)        tick.py:95 → queue "send"
       └─ waiting/ooo_rescheduled + TIMER → step+=1, enqueue_send(eid)  tick.py:92

research_enrollment(eid)                     craftsman/workers/tasks.py:43  ── Celery execute
  ├─ research_company(...)                   tasks.py:55
  └─ apply_event(RESEARCH_DONE) ⇒ state "ready", next_action_at=now  tasks.py:56-57

generate_and_send(eid)                       craftsman/workers/tasks.py:67  ── Celery execute
  guard: proceeds only if enrollment.state == "ready"   tasks.py:87
  1. is_suppressed(db, lead.email)           tasks.py:94   (generation-time re-check)
  2. pick_arm(arms, rng)                     tasks.py:113  (bandit variant pick)
  3. generate_copy(...)                      tasks.py:117-126
        └─ [see §2 / Copywriting]  → validate_fill  validator.py:98
        if not copy.ok → ReviewQueueItem, state="error", RETURN (no send)  tasks.py:127-138
  4. run_presend_checks(db, lead, campaign)  craftsman/sender/smtp.py:71
        ├─ is_suppressed                     smtp.py:72   (send-time re-check)
        ├─ campaign_sent_today >= daily_cap  smtp.py:74
        ├─ pick_mailbox → effective_daily_limit(daily_limit, warmup_stage)  smtp.py:47, warmup.py:6
        └─ acquire_send_slot(mailbox.id)     smtp.py:79 → limiter.py:21 (Redis token bucket)
             wait>0 → SendBlocked(retry_in) → self.retry(countdown)  tasks.py:143-144
  5. last_outbound_in_thread(db, eid)        tasks.py:150
  6. build_email(...)                        craftsman/sender/smtp.py:85
        └─ make_unsubscribe_token, List-Unsubscribe + threading headers  smtp.py:96
  7. deliver(mailbox, email_msg)             craftsman/sender/smtp.py:117
        └─ decrypt(smtp_pass_enc)            smtp.py:124
        └─ aiosmtplib.send(msg, ...)         smtp.py:126   ★ MESSAGE HANDED TO SMTP SERVER
  8. persist outbound Message(bandit_outcome="pending", outcome_deadline)  tasks.py:170-183
     mailbox.sent_today += 1                 tasks.py:184
     apply_event(SEND_OK) ⇒ "ready"→"waiting"  tasks.py:187
     schedule_next_step(...)                 tasks.py:188
```

**The single wire-send call site** is `craftsman/sender/smtp.py:126` (`aiosmtplib.send`),
reached only from `deliver()` (smtp.py:117), whose only caller is
`generate_and_send` at `craftsman/workers/tasks.py:166`. There is no other caller of
`deliver` anywhere in `craftsman/` (verified by grep in the bypass sweep).

Note: `tick` receives its two enqueue callables by injection (tasks.py:32-33), so `tick`
holds no static reference to the send task; the edge `tick → generate_and_send` exists
only at runtime via `sequencer_tick`.

---

## 2. Enumeration: every path by which LLM text can reach the send engine

The bar: does the path pass through `craftsman/copywriter/validator.py` (`validate_fill`)
before `deliver()`? There is exactly one send path, and it is gated.

| # | Path | Reaches `deliver()`? | Validated? | Evidence |
|---|---|---|---|---|
| 1 | Normal generate→validate→send | Yes | **YES** | `generate_copy` returns `ok=True` only when `validate_fill` passes (`fill.py:111-126`); send half is under `if not copy.ok: return` guard (`tasks.py:127-138`), so `deliver` (`tasks.py:166`) is unreachable without a passing validation. |
| 2a | Copywriter internal retry (attempt 2) | Yes | **YES** | Retry loop re-prompts with prior errors appended (`fill.py:74-80, 99`) and calls `validate_fill` on *every* iteration (`fill.py:111`). No branch returns retried copy without re-validating. |
| 2b | Celery send-task retry (`max_retries=3`) | Yes | **YES** | `self.retry` fires only on `SendBlocked` (rate-limit / cap), i.e. *after* validation already passed (`tasks.py:143-146`). Retry re-runs the whole task from the top → `generate_copy` → `validate_fill` again on freshly-generated text. |
| 3 | Human-review queue release | No path to send | N/A | Rejected copy → `ReviewQueueItem(kind="copywriter", payload={slots})` + `state="error"` (`tasks.py:128-138`). Queue is read-only: `GET /inbox/review` (`inbox.py:62-78`). No endpoint sets `resolved=True`, reads `payload["slots"]`, or resets an `error` enrollment to `ready`. `generate_and_send` early-returns unless `state=="ready"` (`tasks.py:87`). **No release-and-send route.** |
| 4 | Admin/test/debug send endpoint | None exists | N/A | All six routers registered (`app.py:35-40`) inspected. Only `campaigns.py:75 activate` enrolls (creates rows), never sends. No router calls `deliver`/`build_email`/`generate_copy`. |
| 5 | Template preview / "send test email" | None exists | N/A | `web/src/lib/api.ts` exposes no send/preview/test-send; only read-only `GET /inbox/review` (`api.ts:51`). No such endpoint in the API. |

**Conclusion (static):** No code path lets unvalidated LLM text reach `aiosmtplib.send`.
This supports README claim #2 ("LLM never free-writes an email") and #4 ("rejected fills
retry once, then human review") at the **structural** level. It is `UNVERIFIED` at the
**behavioral** level — whether the validator's gates *actually catch* bad fills is Phase 2/3,
and the validator itself has a suspected weakness (see §5, numbers via fuzzy path).

---

## 3. Enumeration: outbound message produced in response to an inbound one

README claim #7: "Never auto-replies to an interested human." The pipeline is
`craftsman/inbox/pipeline.py`.

**Direct auto-reply: ABSENT.** `handle_inbound` (`pipeline.py:35-86`) classifies and then
either routes low-confidence to the review queue (`pipeline.py:66-83`, returns) or calls
`apply_classification` (`pipeline.py:89-134`). The only side effects are:
- state transition via `apply_event` — pure DB, no I/O (`pipeline.py:103`);
- bandit update `record_reply_outcome` (`pipeline.py:118` → `settle.py`);
- `unsubscribe`/`bounce_or_auto` → `suppress` (+ `record_bounce`, which only increments
  `hard_bounces_today` and degrades the mailbox — sends nothing) (`pipeline.py:121-132`);
- `interested` → `notify_interested` → **Slack webhook only** (`pipeline.py:137-158`).

The inbox package imports `record_bounce` from `craftsman/sender/` but never `deliver`,
`build_email`, `generate_copy`, or any `.delay()` of a send task. `poll_inboxes`
(`tasks.py:212-238`) enqueues no send. The API inbox router's only mutating endpoint,
`POST /inbox/{id}/reclassify` (`inbox.py:85-110`), re-runs `apply_classification` with
confidence 1.0 — same non-sending side effects.

**Indirect inbound → later outbound: ONE path (OOO).** In the state machine
(`machine.py:39-46`): `REPLY_INTERESTED/OBJECTION/NOT_NOW/BOUNCE/UNSUBSCRIBE` all land in
`TERMINAL_STATES` (`machine.py:25-28`) and the pipeline nulls `next_action_at`
(`pipeline.py:110-112`), so the scheduler never re-scans them — the sequence permanently
stops. **`REPLY_OOO` → `ooo_rescheduled` is NOT terminal** and IS in `SCANNABLE_STATES`
(`tick.py:18`); the pipeline sets `next_action_at` to the OOO return date
(`pipeline.py:106-109`). When that time arrives, `tick` treats it like `waiting`, applies
`TIMER`, increments the step, and enqueues `generate_and_send` (`tick.py:92`).

So receiving an OOO auto-response **resumes the already-queued drip sequence** at its next
planned step on the return date. This is a *deferral of pre-existing outreach*, not a reply
to the inbound text. Two readings, both worth surfacing to the human:
- Strict README ("never auto-*reply*"): holds — nothing composes a response to the reply.
- Literal ("inbound can cause a later outbound"): true for OOO only.

**Conclusion (static):** No direct auto-reply path exists. README claim #7 holds
structurally; flag the OOO reschedule as the one inbound→eventual-outbound linkage. This
is `UNVERIFIED` behaviorally (no misclassification testing was run — that is §3.4/Phase 2).

---

## 4. External network calls (module → file:line → target)

| Call | Module | file:line | Library | Target |
|---|---|---|---|---|
| Company page fetch (research) | `research/fetch.py` | :30, :39 | httpx async | `https://{domain}{/about,/company,...}`, 15s, UA `CraftsmanResearch/0.1` (:9) |
| Slack notify (interested) | `inbox/pipeline.py` | :144 | httpx sync | configured `slack_webhook_url`, 10s |
| Mailpit inbox (sandbox) | `inbox/poller.py` | :135-147 | httpx sync | `{mailpit_url}/api/v1/messages`, 15s |
| Voyage embeddings | `scoring/embeddings.py` | :29-31 | httpx async | `https://api.voyageai.com/v1/embeddings` (only if `embedding_provider=="voyage"`) |
| Apollo enrichment | `ingest/adapters.py` | :21-22 | httpx async | `https://api.apollo.io/api/v1/people/match` — **see Open Q1 (orphaned)** |
| Hunter enrichment | `ingest/adapters.py` | :49-50 | httpx async | `https://api.hunter.io/v2/combined/find` — **see Open Q1 (orphaned)** |
| Ollama LLM | `llm/ollama_impl.py` | :36-38 | httpx async | `{ollama_base_url}/api/chat` (default `http://localhost:11434`), 120s |
| Anthropic LLM | `llm/anthropic_impl.py` | :19, :40 | `anthropic` SDK | `api.anthropic.com`, model `claude-sonnet-4-6` |
| Outbound SMTP | `sender/smtp.py` | :126 | aiosmtplib | `mailbox.smtp_host:{smtp_port or 587}`, STARTTLS@587 |
| SMTP RCPT probe | `ingest/verify.py` | :34-37 | smtplib | domain MX host :25 (only if `do_rcpt=True`) |
| IMAP poll | `inbox/poller.py` | :72-102 | imaplib | `mailbox.imap_host:{imap_port or 143/993}` |
| DNS MX | `ingest/verify.py` | :25 | dnspython | domain MX, 5s lifetime |
| Postgres | `core/db.py` | :16 | SQLAlchemy | `database_url` (pgvector; `CREATE EXTENSION vector` db.py:58) |
| Redis (limiter) | `sender/limiter.py` | :18 | redis-py | `redis_url` token bucket |
| Redis (Celery broker/backend) | `workers/celery_app.py` | :9-10 | Celery | `redis_url` |

Non-localhost calls in the ingest→send→inbox core path: research fetch (arbitrary company
URLs — see §5 SSRF surface, deferred to Phase 2), Anthropic/Voyage/Slack (external SaaS),
outbound SMTP, and MX/RCPT DNS+SMTP probes. Everything else defaults to localhost.

---

## 5. Threshold / cap / magic-number inventory (file:line)

**Validator** (`copywriter/validator.py`): `FUZZY_THRESHOLD=90.0` (:14), `MAX_SUBJECT_WORDS=7`
(:15), `MAX_BODY_WORDS=90` (:16), `MAX_READING_GRADE=8.0` (:17); single-char proper-noun
cutoff `<=2` (:82). Grounding fuzzy applied at :93; caps at :136-140; grade at :145.
> **Flagged for Phase 2:** `validate_fill` runs **numbers through the same
> `_grounded` fuzzy path as proper nouns** (`validator.py:117-124` iterates `proper + numbers`,
> both via `_grounded`/`fuzz.partial_ratio` at :88-95). Per TESTING.md §3.1 this is a
> correctness concern (`$4M` vs `$40M`), not a tuning question. Not tested here — noted.

**Copywriter** (`copywriter/fill.py`): `max_attempts=2` (:92), `max_tokens=400` (:104),
`temperature=0.7` (:105).

**Config defaults** (`core/config.py`): `icp_threshold=0.55` (:33); send window
`start_hour=9`/`end_hour=16`/`end_minute=30` (:37-39) → 9:00–16:30; `send_jitter_minutes=20`
(:40); `bandit_deactivate_min_trials=30` (:42); `classifier_confidence_threshold=0.7` (:45);
`gdpr_mode=False` (:32); `physical_address` default (:31); `anthropic_model="claude-sonnet-4-6"`
(:12); `embedding_provider="hash"` (:17), `embedding_dim=1024` (:20).

**Rate limiter** (`sender/limiter.py`): `MIN_INTERVAL_S=45` (:13), `MAX_INTERVAL_S=90` (:14),
key TTL `MAX_INTERVAL_S*2` (:41).

**Warmup** (`sender/warmup.py`): `WARMUP_CAPS={0:10,1:20,2:30,3:40}` (:3); stage `>=4` → full
`daily_limit` (:7).

**SMTP** (`sender/smtp.py`): campaign daily cap check (:74); degraded mailbox cap halved
`limit//2` (:51); default port 587 (:121,:125); `hard_bounces_today>=2` → degraded (:132).

**Scheduling** (`sequencer/scheduling.py`): default tz fallback `America/Los_Angeles` (:40);
weekends skipped (:19,:63); uses config window/jitter (:45-47).

**Tick** (`sequencer/tick.py`): `SCANNABLE_STATES` (:18); `TICK_BATCH=200` (:19).

**Bandit** (`bandit/thompson.py`): `DEACTIVATE_RATIO=0.5` (:15), `DEACTIVATE_MIN_TRIALS=30`
(:16), prior `Beta(1,1)` (:27), PDF domain `[0,0.25]` (:75). `settle.py`: needs `>=2` variants
to compare before deactivating (:69). `simulator.py`: `n_sends=500` (:39), `seed=0` (:40),
`snapshot_every=25` (:41), true rates 0.06/0.02/0.035 (:75-77), seed 42 (:82).

**Research** (`research/agent.py`): `CACHE_DAYS=30` (:13). `research/fetch.py`: `MAX_CHARS=24_000`
(:10), timeout 15s (:33), skip cleaned text `<=200` chars (:42).

**Scoring** (`scoring/icp.py`): seniority weights 1.0…0.4 (:9-14), unknown-title `0.3` (:27),
blend `0.7*sim01 + 0.3*rule_score` (:43). `scoring/embeddings.py`: `DIM=1024` (:15).

**Classifier** (`inbox/classifier.py`): `max_tokens=200` (:34), `temperature=0.0` (:35).

**LLM clients**: `MAX_RETRIES=2` in both `anthropic_impl.py`/`ollama_impl.py` (:13);
anthropic `max_tokens=1024`, `temperature=0.2` (:28-29); `client.py`/`mock_impl.py` same
defaults (:17-18 / :33-34).

**Tasks** (`workers/tasks.py`): enrich `max_retries=2, default_retry_delay=300` (:42); send
`max_retries=3` (:66); `step_order=max(current_step,1)` (:98); rate-limit retry
`countdown=retry_in+1` (:144); no-capacity retry `countdown=3600` (:146); default
`wait_days=3` (:169); `outcome_deadline=now+wait_days` (:180).

**Beat** (`workers/celery_app.py`): sequencer-tick 60s (:24), poll-inboxes 120s (:25),
settle-bandit 3600s (:26), reset-daily-counters 86400s (:29); `task_acks_late=True` (:32),
`worker_prefetch_multiplier=1` (:33).

**Compliance** (`compliance/suppression.py`): unsubscribe token `secrets.token_urlsafe(24)` (:43).

---

## 6. Open Questions (ambiguities — not resolved by static reading)

1. **`ingest/adapters.py` (Apollo/Hunter) is orphaned in the traced path.** `ApolloAdapter.enrich`
   (adapters.py:20) and `HunterAdapter.enrich` (adapters.py:48) have no caller in the
   ingest→send chain; `enrich_lead` uses `verify.py` only, not the adapters. Are these dead
   code, wired elsewhere, or intended-but-unconnected? They still define real external API
   calls (§4), so they matter for the network-surface claim.

2. **OOO reclassify with no return date.** `POST /inbox/{id}/reclassify` builds
   `ReplyClassification(label, confidence=1.0)` with `ooo_return_date` unset (`inbox.py:92`).
   If a human reclassifies to `ooo`, `apply_classification` transitions to `ooo_rescheduled`
   but skips the `next_action_at` reset (guarded by `if classification.ooo_return_date`,
   `pipeline.py:106`). Whether the enrollment is then resumable/sendable depends on its prior
   `next_action_at` value — **cannot be determined statically.** Flagged; not a direct send path.

3. **Does the validator fuzzy-match numbers?** Static reading says yes (numbers and proper
   nouns share `_grounded`, validator.py:117-124). Whether `$4M`→`$40M` actually passes at
   ratio 90 is a Phase 2 experiment — recorded here as a *suspected* correctness bug, `UNVERIFIED`.

4. **Warmup ramp / per-campaign cap enforcement under concurrency.** `effective_daily_limit`
   and `campaign.daily_cap` checks (smtp.py:47,:74) and the Redis rate limiter (limiter.py)
   are read; whether the checks hold under multiple Celery workers (in-memory vs Redis/DB
   lock semantics) is a Phase 2 concurrency question, not answerable by reading.

5. **Whether `validate_fill` is *behaviorally* sound.** §2 establishes the validator is the
   sole structural gate. It does **not** establish that the gate catches what it claims to
   (proper-noun detection heuristics at validator.py:64-85, casing edge cases, banned-phrase
   evasion). All of that is Phase 2 adversarial characterization, deliberately not attempted here.

6. **`.env.example` completeness / cold-start.** Deferred to Phase 1 by design (not read/tested
   in Phase 0).

---

## 7. Status

Phase 0 deliverable complete. No files under `craftsman/`, `tests/`, or any config were
modified. Only this file (`findings/00-map.md`) was written, as the phase permits.
All verdicts above are structural (static) unless explicitly marked; behavioral verification
of README claims begins in Phase 1 (baseline) and Phase 2 (adversarial), which are separate
sessions and were **not** started.
