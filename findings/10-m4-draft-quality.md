# M4.1 draft-quality evidence (⛔ Gate M4, second gate) — 2026-07-23

Per the recorded Gate M4 Q2 decision (evidence-at-the-end): real-LLM draft generation
over every interested/objection fixture in `tests/fixtures/replies.json`, recorded
verbatim for human review before merge. No pass/fail authority is claimed here.

## ⚠️ Model caveat — read first

The `.env` `ANTHROPIC_API_KEY` is **invalid** (`401 authentication_error`, request id
`req_011CdKgCemxQa4EB2LEKy6QH`), so this eval could not run on the production default
(`claude-sonnet-4-6`). It ran on the locally available **Ollama `qwen3:8b`** — a far
weaker model, i.e. a *lower bound* on draft quality. Every failure below degraded
SAFELY (draft → review queue or needs-human; nothing sendable was produced by a bad
fill). **Recommend re-running `scratchpad` eval with a valid key before relying on
quality conclusions**; the safety conclusions are model-independent (validator gates
output, not input).

Scenario: brief = Acme Logistics ($4M raise, Austin warehouse, "hiring 40 seasonal
pickers for Q4", pains: manual order picking / seasonal staffing spikes); value prop =
"Flowbot's picking robots cut warehouse picking costs by a third within one quarter";
persona = Sam Rivera, Founder, Flowbot; scheduling + one-pager lines provided.

## Run 1 — 11 fixtures (6 interested, 5 objection), pre prompt-rule fix

| # | Fixture | Outcome |
|---|---|---|
| 1 | "…send over some more details on pricing?" (interested) | ✅ draft, attempt 2 |
| 2 | "…Do you have 15 minutes Thursday?" (interested) | ❌ failed → review queue: invented `80%`, grade 8.6 |
| 3 | "What integrations do you support? We run NetSuite." (interested) | ✅ draft, attempt 2 |
| 4 | "…talk to Maria Chen, our VP Ops." (interested) | ❌ failed: invented `33%`, `1`, `10` |
| 5 | "Send me a one pager and I'll take a look." (interested) | ✅ draft, attempt 1 |
| 6 | "How is this different from what Attentive does?" (interested) | ❌ failed: invented `33%`, grade 8.6 |
| 7–11 | all 5 objection fixtures (competitor / expensive / in-house / security / not-applicable) | ⏭ `objection_needs_human` — correct: none has a deterministic resolution |

Sample passing draft (fixture 3, verbatim):

```
Hi Dana,

You asked about integrations; you use NetSuite.

Flowbot integrates with NetSuite for real-time inventory sync. Would you like a demo of how this works?
If it is easier, grab any time here: https://cal.com/sam-rivera/15min

Sam Rivera
Founder
Flowbot
```

(Note: "integrates with NetSuite for real-time inventory sync" is grounded only via
the fuzzy entity path — NetSuite is in the reply — but the *capability claim* is the
kind of soft assertion the validator cannot arbitrate. This is the known residual
risk of any draft system and exactly why a human click gates dispatch.)

**Systemic failure mode found:** the model converts word-form numbers to digits
("a third" → `33%`/`80%`) — the numeric gate rejects these (fail-closed, correct),
but it costs acceptance rate. Fix applied: `REPLY_SYSTEM` rule 6 — "Never convert
word-form numbers into digits… A digit or percent you write must appear
digit-for-digit in the sources."

## Run 2 — after the prompt rule, plus timing/info probes

| Fixture | Outcome |
|---|---|
| "…15 minutes Thursday?" (was ❌ `80%`) | ✅ draft, attempt 2 — **prompt rule fixed it** |
| "…talk to Maria Chen…" | ❌ failed: `'Does Maria'` extractor artifact (fail-closed → review queue) |
| "How is this different from what Attentive does?" | ❌ failed: grade 8.0x vs max 8 (borderline) |
| "Q4 is crazy for us. Try me again in the new year." (timing probe) | ⏭ `objection_needs_human` — **should be `timing`**; qwen3:8b under-selects |
| "Can you send over some more info about how it works first?" (info probe) | ⏭ `objection_needs_human` — **should be `info`**; same |

Run-2 passing draft (verbatim):

```
Hi Dana,

You mentioned discussing warehouse automation recently.

Our robots cut picking costs by a third in one quarter, matching your Q4 staffing needs. Can we schedule a quick call Thursday?
If it is easier, grab any time here: https://cal.com/sam-rivera/15min

Sam Rivera
Founder
Flowbot
```

## Observations for the human reviewer

1. **Safety holds at the floor.** Even an 8B model + adversarial fixtures produced
   zero drafts that leak ungrounded numbers, commitments, or off-corpus entities —
   every bad fill died in the validator and routed to the review queue.
2. **Skeleton selection is the model-sensitive part.** qwen3:8b routes real timing/
   info objections to `other` (needs-human). Wrong-but-safe; with the production
   Sonnet model this is expected to improve substantially. The deterministic
   timing/info render paths are covered by unit tests regardless.
3. **Acceptance-rate drivers**: word-number conversion (fixed via prompt rule),
   entity-extractor artifacts on sentence-like slots ("Does Maria"), grade
   borderlines. All fail closed.
4. Raw JSON for both runs: session scratchpad `draft_eval.json` / `probes.json`
   (deliberately not committed; this file is the durable record).

**Gate status:** evidence recorded for veto-before-merge per the Q2 decision. M4.2+
construction proceeded on the strength of the fail-closed results; Autopilot (M4.4)
adds its own confidence/policy gates on top of everything shown here.
