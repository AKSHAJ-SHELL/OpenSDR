# Craftsman

**An open-source AI SDR with a learning loop the funded versions don't have.**

Craftsman runs end-to-end outbound: leads in → researched, personalized sequences out → replies classified → human handoff the moment someone is interested. It never free-writes an email, never auto-replies to a human, and it gets measurably better with every send via a Thompson-sampling bandit over copy variants.

```
docker compose up
```

That's the whole deployment. Bring your own Anthropic API key (or run the Ollama fallback for $0 marginal cost).

## Why this exists

Commercial "AI SDR employees" are, mechanically: a state machine, a scraper, two constrained LLM calls, SMTP plumbing, and (in Craftsman's case) a contextual bandit. The genuinely hard parts of outbound at scale — deliverability, warm-up networks, IP reputation — have nothing to do with AI. This repo is the honest version: every mechanism is inspectable, and the one feature the funded products don't ship openly — **a closed learning loop that optimizes measured reply rate** — is ~120 lines of Thompson sampling you can read in [`craftsman/bandit/thompson.py`](craftsman/bandit/thompson.py).

## Anti-hallucination by construction

The LLM never writes an email. It fills **typed slots** in a fixed skeleton, from a **structured research brief** whose every claim must be backed by verbatim source text. Then a deterministic validator gates the output:

1. Every proper noun and number in the fill must appear in the research brief or campaign config (fuzzy match ≥ 0.9) — or the fill is rejected.
2. Banned-phrase list (every "I hope this finds you well"-style tell).
3. Length caps: subject ≤ 7 words, body ≤ 90 words.
4. Reading grade ≤ 8.

Rejected fills retry once with the validator errors appended; a second failure goes to a human review queue. The rejection rate is a first-class dashboard metric — public proof the gate is real.

## The learning loop

Each copy variant per sequence step is a bandit arm with a Beta posterior over reply rate. At send time the bandit samples each posterior and picks the winner; classified human replies update the posteriors; arms that fall far behind auto-deactivate. Reply rates are low (2–8%), so posteriors stay honest about uncertainty — exactly what Thompson sampling is for.

Run the simulator and watch it converge with zero real emails sent:

```
python -m craftsman.bandit.simulator
```

```
arm           true rate  traffic  posterior mean
pain_led      0.060      372      0.0775
trigger_led   0.020      45       0.0213
question_led  0.035      83       0.0353

best arm captured 74% of traffic
```

The dashboard renders the Beta PDFs converging live (`Bandit` page, with an interactive demo mode that needs no data).

## What it will never do

- **Auto-reply to an interested human.** Interested replies stop the sequence and ping Slack. A human takes over. This is a feature, not a gap — it's also exactly where AI SDRs get caught.
- **Send to unverified emails.** Syntax → MX → optional SMTP handshake; unverifiable addresses never enroll. Bounce risk is the #1 deliverability killer.
- **Batch-blast.** Sends land inside the lead's local business hours (9:00–16:30, jittered), one per mailbox per 45–90s, with warmup ramps and per-campaign caps.

## Architecture

FastAPI + Postgres/pgvector + Celery/Redis. One `docker compose up` brings up the API, workers, beat scheduler, Streamlit dashboard, and a [Mailpit](https://mailpit.axllent.org/) sandbox for testing without touching real inboxes.

Pipeline per lead: ingest → verify email → embed + ICP-score → research (cached 30d per company) → enroll → per step: bandit picks variant → copywriter fills slots → validator gates → send engine dispatches in-window → inbox poller catches the reply → classifier updates state → bandit posterior updates → human notified if interested.

Key modules:

| Path | What it is |
|---|---|
| `craftsman/sequencer/machine.py` | Pure-function state machine (the transition table is data) |
| `craftsman/copywriter/validator.py` | The deterministic anti-hallucination gate |
| `craftsman/research/agent.py` | Grounded research briefs, cached per company |
| `craftsman/bandit/thompson.py` | The learning loop |
| `craftsman/inbox/pipeline.py` | Reply → classify → state → bandit → handoff |
| `craftsman/sender/smtp.py` | Suppression/cap/warmup/rate-limit checks + compliant headers |
| `craftsman/llm/` | Provider-agnostic structured-output client (Claude default, Ollama fallback, mock for tests) |

## Quickstart

```bash
cp .env.example .env
# set ANTHROPIC_API_KEY and generate CRAFTSMAN_SECRET_KEY:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

docker compose up -d
# API:       http://localhost:8000/docs
# Dashboard: http://localhost:8501
# Mailpit:   http://localhost:8025

# import leads
curl -F "file=@leads.csv" http://localhost:8000/leads/import

# create a campaign, add variants, activate — see /docs
```

For local dev without Docker: `pip install -e ".[dashboard,dev]"`, run Postgres (with pgvector) + Redis, then `uvicorn craftsman.api.app:app` and `celery -A craftsman.workers.celery_app worker -B`.

## Testing

```bash
pytest             # 54 tests: state machine, validator, bandit math, scheduling,
                   # copywriter retry loop, full Postgres integration flow
python scripts/eval_classifier.py   # live LLM eval vs 32 adversarial reply fixtures
python scripts/seed_demo.py         # populate the dashboard with demo data
```

The unit layer is pure functions and a mock LLM — no API key, no network. Integration tests run against real Postgres and skip cleanly if it's absent. The classifier eval includes the adversarial cases that matter: the OOO that mentions interest, the polite unsubscribe, the helpdesk auto-ack.

## Compliance

One-click unsubscribe (RFC 8058 `List-Unsubscribe-Post`), physical address in every footer (CAN-SPAM), permanent suppression list checked at generation *and* send time, GDPR mode that blocks EU-TLD enrollment for non-opt-in lists, and `DELETE /leads/{id}/erase` for data-subject requests. Deliverability guardrails (verification, warmup ramps, rate jitter, bounce-driven mailbox degradation) are on by default so self-hosters don't torch their domains.

## License

AGPL-3.0. If you host a modified Craftsman as a service, you share your changes. That's the point.
