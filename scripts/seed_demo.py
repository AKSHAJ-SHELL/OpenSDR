"""Seed the database with demo data so the dashboard has something to show.

Run inside the stack: python scripts/seed_demo.py
"""

import random
from datetime import datetime, timedelta, timezone

from craftsman.core.db import init_db, session_scope
from craftsman.core.models import (
    Campaign, Company, Enrollment, Lead, Message, SequenceStep, Variant,
)

SKELETON = """Subject: {{subject_hook}}

Hi {{first_name}},

{{personalization_sentence}}

{{value_prop_bridge}} {{cta_question}}

{{signature}}"""

SLOT_SCHEMA = {
    "subject_hook": {"max_words": 7},
    "personalization_sentence": {"max_words": 25},
    "value_prop_bridge": {"max_words": 20},
    "cta_question": {"max_words": 12},
}


def main() -> None:
    from craftsman.core.tenancy import DEFAULT_ORG_ID, set_request_org

    init_db()
    set_request_org(DEFAULT_ORG_ID)  # demo data lives in the default org (M5.1)
    rng = random.Random(42)
    with session_scope() as db:
        campaign = Campaign(
            name="Warehouse ops Q3",
            icp_description="VP/Head of Operations at mid-size 3PL and e-commerce fulfillment companies",
            value_prop="Flowbot cuts warehouse picking costs by a third.",
            sender_persona={"name": "Sam Rivera", "title": "Founder", "company": "Flowbot"},
            status="active",
        )
        db.add(campaign)
        db.flush()

        variants_by_step = {}
        for order, wait in [(1, 0), (2, 3), (3, 4)]:
            step = SequenceStep(campaign_id=campaign.id, step_order=order, wait_days=wait)
            db.add(step)
            db.flush()
            names = ["pain_led", "trigger_led", "question_led"]
            variants_by_step[order] = []
            for name in names:
                # seed posteriors as if ~150 sends already happened
                true_rate = {"pain_led": 0.06, "trigger_led": 0.02, "question_led": 0.035}[name]
                trials = rng.randint(30, 80)
                successes = sum(rng.random() < true_rate for _ in range(trials))
                v = Variant(
                    step_id=step.id, name=name, skeleton=SKELETON, slot_schema=SLOT_SCHEMA,
                    alpha=1.0 + successes, beta=1.0 + trials - successes,
                )
                db.add(v)
                variants_by_step[order].append(v)
        db.flush()

        domains = ["acmerobotics.com", "shipfast.io", "boxline.co", "palletpro.com", "fulfilledly.com"]
        titles = ["VP Operations", "Head of Warehouse", "COO", "Director of Fulfillment", "Ops Manager"]
        first_names = ["Dana", "Raj", "Mia", "Tom", "Aisha", "Leo", "Priya", "Sam"]
        classifications = ["interested", "objection", "not_now", "ooo", None, None, None]

        for i in range(40):
            domain = domains[i % len(domains)]
            company = db.query(Company).filter_by(domain=domain).first()
            if company is None:
                company = Company(domain=domain, name=domain.split(".")[0].title())
                db.add(company)
                db.flush()
            lead = Lead(
                email=f"lead{i}@{domain}",
                company_id=company.id,
                first_name=first_names[i % len(first_names)],
                title=titles[i % len(titles)],
                email_verified=True,
                status="verified",
                icp_score=round(rng.uniform(0.5, 0.95), 2),
            )
            db.add(lead)
            db.flush()
            enrollment = Enrollment(
                lead_id=lead.id, campaign_id=campaign.id,
                state=rng.choice(["waiting", "waiting", "replied_interested", "finished_no_reply"]),
                current_step=rng.randint(1, 3),
            )
            db.add(enrollment)
            db.flush()
            sent_at = datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 10))
            variant = rng.choice(variants_by_step[1])
            outbound = Message(
                enrollment_id=enrollment.id, variant_id=variant.id, direction="outbound",
                subject="your new facility", body="(demo outbound)",
                smtp_message_id=f"<demo-{i}@flowbot.io>",
                bandit_outcome="success" if enrollment.state == "replied_interested" else "pending",
                outcome_deadline=sent_at + timedelta(days=3), sent_at=sent_at,
            )
            db.add(outbound)
            label = rng.choice(classifications)
            if label:
                db.add(
                    Message(
                        enrollment_id=enrollment.id, direction="inbound",
                        subject="Re: your new facility",
                        body={"interested": "Sure, send more details?",
                              "objection": "We already use a competitor.",
                              "not_now": "Not a priority this quarter.",
                              "ooo": "Out of office until Monday."}[label],
                        classification=label,
                        classification_confidence=round(rng.uniform(0.75, 0.99), 2),
                    )
                )
    print("seeded demo campaign, 40 leads, messages, and bandit posteriors")


if __name__ == "__main__":
    main()
