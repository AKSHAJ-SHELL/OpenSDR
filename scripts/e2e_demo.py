"""End-to-end system test: the full pipeline with real SMTP, real Postgres, mock LLM.

What actually runs (no shortcuts except the LLM and web fetch):
  - real SMTP delivery over the wire to a local aiosmtpd sink
  - real sequencer tick / state machine / audit log
  - real copywriter fill + deterministic validator (with injected hallucinations
    to prove the gate catches them)
  - real send-engine pre-send checks (suppression, caps, rate limiter vs Redis)
  - real inbox pipeline: threading headers matched, quoted text stripped,
    classification applied, bandit posteriors updated, suppression on unsubscribe
  - scripted reply personas with seeded per-variant reply rates so Thompson
    sampling has a real signal to find

Run: python scripts/e2e_demo.py [n_leads]
"""

import asyncio
import os
import random
import sys
import threading
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://craftsman:craftsman@localhost:5432/craftsman_demo"
)
os.environ["LLM_PROVIDER"] = "mock"

from aiosmtpd.controller import Controller
from celery.exceptions import Retry
from sqlalchemy import create_engine, func, select, text

import craftsman.sender.limiter as limiter
from craftsman.core.db import init_db, session_scope
from craftsman.core.models import (
    AuditLog, Campaign, Company, Enrollment, Lead, Mailbox, Message,
    ReviewQueueItem, SequenceStep, SuppressionEntry, Variant,
)
from craftsman.core.schemas import ReplyClassification, ResearchBrief, SlotFill, TriggerEvent
from craftsman.inbox.pipeline import handle_inbound
from craftsman.inbox.poller import parse_raw_email
from craftsman.llm.mock_impl import MockLLM
from craftsman.sequencer.tick import tick

SMTP_PORT = 2525

# --- speed up the rate limiter for the demo (interface unchanged, interval tiny)
limiter.MIN_INTERVAL_S = 0.0001
limiter.MAX_INTERVAL_S = 0.0002

SKELETON = """Subject: {{subject_hook}}

Hi {{first_name}},

{{personalization_sentence}}

{{value_prop_bridge}} {{cta_question}}

{{signature}}"""

SOURCES = {
    "https://acme-demo.test/about": (
        "Acme Robotics builds warehouse automation robots for mid-size 3PLs. "
        "We just opened our new Austin facility to serve Texas customers. "
        "Our customers struggle with manual picking costs and labor shortages."
    )
}

BRIEF = ResearchBrief(
    what_they_do="Acme Robotics builds warehouse automation robots for mid-size 3PLs.",
    industry="logistics automation",
    trigger_events=[TriggerEvent(
        claim="opened a new Austin facility",
        source_url="https://acme-demo.test/about", approx_date="2026-06",
    )],
    likely_pain_points=["manual picking costs", "labor shortages"],
    evidence_quotes=["We just opened our new Austin facility to serve Texas customers."],
)

GOOD_FILL = SlotFill(
    subject_hook="your new Austin facility",
    personalization_sentence="Saw that you opened a new Austin facility.",
    value_prop_bridge="Flowbot cuts warehouse picking costs by a third.",
    cta_question="Worth a look?",
)
# hallucinated fill — the validator must catch this and force a retry
BAD_FILL = SlotFill(
    subject_hook="congrats on the Sequoia round",
    personalization_sentence="Huge news on the $40M Series B from Sequoia.",
    value_prop_bridge="Flowbot cuts warehouse picking costs by a third.",
    cta_question="Worth a look?",
)

# per-variant true reply rates the bandit must discover
TRUE_RATES = {"pain_led": 0.10, "trigger_led": 0.03, "question_led": 0.05}

REPLY_SCRIPTS = [
    ("interested", "Sounds relevant, can you send pricing details?", 0.95, 0.45),
    ("objection", "We already use a competitor for this.", 0.9, 0.25),
    ("not_now", "Not a priority this quarter, try me in Q4.", 0.9, 0.15),
    ("unsubscribe", "Please remove me from your list.", 0.98, 0.10),
    ("ooo", "Out of office until Aug 10 but this looks interesting, ping me later!", 0.55, 0.05),
]


class SmtpSink:
    """Real SMTP server capturing every delivered email."""

    def __init__(self):
        self.emails = []
        self.lock = threading.Lock()

    async def handle_DATA(self, server, session, envelope):
        with self.lock:
            self.emails.append(envelope.content)
        return "250 Message accepted for delivery"

    def drain(self):
        with self.lock:
            out, self.emails = self.emails, []
        return out


def make_llm(hallucinate_every: int) -> MockLLM:
    llm = MockLLM()
    llm.respond_with(ResearchBrief, lambda s, u: BRIEF.model_copy(deep=True))

    fill_count = {"n": 0}

    def fill_factory(system: str, user: str) -> SlotFill:
        if "REJECTED BY THE VALIDATOR" in user:
            return GOOD_FILL.model_copy(deep=True)  # model fixes itself on retry
        fill_count["n"] += 1
        if fill_count["n"] % hallucinate_every == 0:
            return BAD_FILL.model_copy(deep=True)  # every Nth first attempt hallucinates
        return GOOD_FILL.model_copy(deep=True)

    llm.respond_with(SlotFill, fill_factory)

    def classify_factory(system: str, user: str) -> ReplyClassification:
        for label, snippet, conf, _ in REPLY_SCRIPTS:
            if snippet[:20] in user:
                return ReplyClassification(
                    label=label, confidence=conf,
                    ooo_return_date=date(2026, 8, 10) if label == "ooo" else None,
                )
        return ReplyClassification(label="not_now", confidence=0.6)

    llm.respond_with(ReplyClassification, classify_factory)
    return llm


def setup_db(n_leads: int) -> None:
    admin = create_engine(
        "postgresql+psycopg://craftsman:craftsman@localhost:5432/postgres",
        isolation_level="AUTOCOMMIT",
    )
    with admin.connect() as conn:
        conn.execute(text("DROP DATABASE IF EXISTS craftsman_demo WITH (FORCE)"))
        conn.execute(text("CREATE DATABASE craftsman_demo"))
    init_db()
    from craftsman.core.tenancy import DEFAULT_ORG_ID, set_request_org

    set_request_org(DEFAULT_ORG_ID)  # demo rows live in the default org (M5.1)

    with session_scope() as db:
        db.add(Mailbox(email="sam@flowbot-demo.test", smtp_host="localhost",
                       smtp_port=SMTP_PORT, daily_limit=100000, warmup_stage=4))
        campaign = Campaign(
            name="e2e demo", icp_description="ops leaders",
            value_prop="Flowbot cuts warehouse picking costs by a third.",
            sender_persona={"name": "Sam Rivera", "title": "Founder", "company": "Flowbot"},
            daily_cap=100000, status="active",
        )
        db.add(campaign)
        db.flush()
        step = SequenceStep(campaign_id=campaign.id, step_order=1, wait_days=0)
        db.add(step)
        db.flush()
        for name in TRUE_RATES:
            db.add(Variant(step_id=step.id, name=name, skeleton=SKELETON,
                           slot_schema={"slots": list(SlotFill.model_fields)}))
        company = Company(domain="acme-demo.test", name="Acme Robotics")
        db.add(company)
        db.flush()
        now = datetime.now(timezone.utc)
        for i in range(n_leads):
            lead = Lead(email=f"lead{i}@acme-demo.test", company_id=company.id,
                        first_name=f"Lead{i}", title="VP Operations",
                        email_verified=True, status="verified", icp_score=0.8)
            db.add(lead)
            db.flush()
            db.add(Enrollment(lead_id=lead.id, campaign_id=campaign.id, state="queued",
                              current_step=0, next_action_at=now - timedelta(minutes=1)))


def send_with_retry(task, eid: str, attempts: int = 8):
    for _ in range(attempts):
        try:
            return task(eid)
        except Retry:
            time.sleep(0.01)


def run(n_leads: int = 300) -> None:
    rng = random.Random(7)
    sink = SmtpSink()
    controller = Controller(sink, hostname="localhost", port=SMTP_PORT)
    controller.start()

    setup_db(n_leads)
    llm = make_llm(hallucinate_every=25)

    # patch the LLM + web fetch (the only two things not run for real)
    import craftsman.research.agent as research_agent
    import craftsman.workers.tasks as tasks

    tasks.get_llm = lambda: llm

    async def fake_fetch(domain):
        return dict(SOURCES)

    research_agent.fetch_company_text = fake_fetch

    variant_names: dict = {}
    sent_per_variant: Counter = Counter()
    start = time.time()

    print(f"processing {n_leads} leads through the full pipeline...\n")
    for round_no in range(1, 20):
        research_ids: list[str] = []
        send_ids: list[str] = []
        with session_scope() as db:
            handled = tick(db, research_ids.append, send_ids.append)

        for eid in research_ids:
            tasks.research_enrollment(eid)
        for eid in send_ids:
            send_with_retry(tasks.generate_and_send, eid)

        # real emails arrived at the SMTP sink → simulate humans replying
        outbox = sink.drain()
        with session_scope() as db:
            if not variant_names:
                variant_names = dict(db.execute(select(Variant.id, Variant.name)).all())
            for raw in outbox:
                parsed = parse_raw_email(raw)
                outbound = db.scalar(
                    select(Message).where(Message.smtp_message_id == parsed.message_id)
                )
                if outbound is None:
                    continue
                vname = variant_names[outbound.variant_id]
                sent_per_variant[vname] += 1
                if rng.random() >= TRUE_RATES[vname]:
                    continue  # no reply — the settle job counts it as a failure
                r, cum = rng.random(), 0.0
                for label, body, conf, share in REPLY_SCRIPTS:
                    cum += share
                    if r <= cum:
                        break
                reply_raw = (
                    f"From: reply@acme-demo.test\r\n"
                    f"Subject: Re: {outbound.subject}\r\n"
                    f"In-Reply-To: {outbound.smtp_message_id}\r\n"
                    f"Message-ID: <reply-{outbound.id}@acme-demo.test>\r\n"
                    f"\r\n{body}\r\n\r\n"
                    f"On Mon, Jul 20, 2026 Sam Rivera wrote:\r\n"
                    f"> {outbound.body.splitlines()[0]}\r\n"
                ).encode()
                asyncio.run(handle_inbound(db, llm, parse_raw_email(reply_raw)))

        # settle non-replies past their deadline (wait_days=0 → immediate)
        with session_scope() as db:
            from craftsman.bandit.settle import settle_expired

            settle_expired(db)

        # re-arm scheduling jitter so the demo doesn't wait for business windows
        with session_scope() as db:
            db.execute(text(
                "UPDATE enrollments SET next_action_at = now() "
                "WHERE state IN ('ready','queued','waiting')"
            ))

        done = handled == 0 and not outbox
        if outbox:
            print(f"round {round_no:>2}: {len(outbox):>3} emails over SMTP "
                  f"(total {sum(sent_per_variant.values())})")
        if done:
            break

    controller.stop()
    elapsed = time.time() - start

    # ---------------------------------------------------------------- report
    validator_retries = sum(
        1 for c in llm.calls
        if c["schema"] == "SlotFill" and "REJECTED BY THE VALIDATOR" in c["user"]
    )
    with session_scope() as db:
        n_out = db.scalar(select(func.count(Message.id)).where(Message.direction == "outbound"))
        n_in = db.scalar(select(func.count(Message.id)).where(Message.direction == "inbound"))
        states = dict(db.execute(
            select(Enrollment.state, func.count()).group_by(Enrollment.state)).all())
        labels = dict(db.execute(
            select(Message.classification, func.count())
            .where(Message.direction == "inbound").group_by(Message.classification)).all())
        review = db.scalar(select(func.count(ReviewQueueItem.id)))
        suppressed = db.scalar(select(func.count(SuppressionEntry.email)))
        audits = db.scalar(select(func.count(AuditLog.id)))
        arms = db.execute(select(Variant.name, Variant.alpha, Variant.beta)).all()

    print(f"\n================ E2E REPORT ({elapsed:.0f}s) ================")
    print(f"real emails sent over SMTP : {n_out}")
    print(f"inbound replies processed  : {n_in}")
    print(f"classification breakdown   : {labels}")
    print(f"enrollment end states      : {states}")
    print(f"validator caught + retried : {validator_retries} hallucinated fills")
    print(f"human review queue items   : {review} (low-confidence OOO-with-interest cases)")
    print(f"suppression list entries   : {suppressed} (from unsubscribe replies)")
    print(f"audit log rows             : {audits}")
    print("\nbandit posteriors (true rates: pain_led 10%, question_led 5%, trigger_led 3%):")
    total_traffic = sum(sent_per_variant.values()) or 1
    for name, alpha, beta in sorted(arms, key=lambda r: -(r.alpha / (r.alpha + r.beta))):
        share = sent_per_variant[name] / total_traffic
        print(f"  {name:<14} Beta({alpha:.0f},{beta:.0f})  "
              f"posterior mean {alpha / (alpha + beta):.3f}  "
              f"traffic {sent_per_variant[name]:>4} ({share:.0%})")
    best = max(sent_per_variant, key=sent_per_variant.get)
    print(f"\nbandit routed {sent_per_variant[best] / total_traffic:.0%} "
          f"of traffic to the best arm ({best})")


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 300)
