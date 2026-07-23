"""Dry-run / preflight (M1.2, E4).

The guarantees under test:
- a dry run routes the real pipeline (research → pick → fill → validate → build → send)
  but delivery goes to Mailpit's SMTP regardless of mailbox config;
- it touches NO production state: no Messages, no unsubscribe tokens, no enrollments,
  no campaign/mailbox counters, no bandit posteriors, no persisted lead scores;
- validator rejection and per-lead research failure are recorded outcomes, not run
  failures;
- suppressed leads are never sampled;
- erasure removes a lead's dry-run items (the M0.4 cascade extends to the new store).

Task-level tests use dedicated committed sessions (the task commits internally),
mirroring tests/e2e/test_send_idempotency.py.
"""

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from craftsman.core.models import (
    Campaign,
    Company,
    DryRun,
    DryRunItem,
    Lead,
    Mailbox,
    Message,
    SequenceStep,
    SuppressionEntry,
    UnsubscribeToken,
    Variant,
)
from craftsman.core.schemas import SlotFill
from craftsman.llm.mock_impl import MockLLM
from craftsman.workers import tasks

SKELETON = (
    "Subject: {{subject_hook}}\n\nHi {{first_name}},\n\n"
    "{{personalization_sentence}} {{value_prop_bridge}} {{cta_question}}\n\n{{signature}}"
)

GOOD_FILL = SlotFill(
    subject_hook="a note on operations",
    personalization_sentence="you focus on operations work.",
    value_prop_bridge="we help teams cut costs.",
    cta_question="worth a look?",
)

# "Acme Corp" appears nowhere in the brief → grounding gate rejects (both attempts).
UNGROUNDED_FILL = SlotFill(
    subject_hook="a note on operations",
    personalization_sentence="Acme Corp is clearly growing fast.",
    value_prop_bridge="we help teams cut costs.",
    cta_question="worth a look?",
)


def _setup_scenario(engine, *, n_leads=2, mailbox_host="smtp.production.example"):
    """Commit a campaign with one step-1 variant and n verified leads (fresh cached
    briefs, so research never fetches). Mailbox deliberately points at a NON-Mailpit
    host — the dry run must ignore it. Returns an ids dict."""
    tag = uuid.uuid4().hex[:8]
    with Session(bind=engine) as s:
        campaign = Campaign(
            name=f"dry-{tag}", icp_description="operations software teams",
            value_prop="we help teams cut costs", daily_cap=100, status="draft",
            sender_persona={"name": "Sam", "title": "Founder"},
        )
        s.add(campaign)
        s.flush()
        step = SequenceStep(campaign_id=campaign.id, step_order=1, wait_days=0)
        s.add(step)
        s.flush()
        variant = Variant(step_id=step.id, name="v1", skeleton=SKELETON, slot_schema={})
        s.add(variant)
        s.flush()
        mailbox = Mailbox(
            email=f"box-{tag}@send.test", smtp_host=mailbox_host,
            smtp_port=587, daily_limit=1000, warmup_stage=4, health="ok",
        )
        s.add(mailbox)

        lead_ids, company_ids = [], []
        for i in range(n_leads):
            company = Company(
                domain=f"dry-{tag}-{i}.test",
                research_brief={"what_they_do": "operations software", "industry": "ops"},
                research_fetched_at=datetime.now(timezone.utc),
            )
            s.add(company)
            s.flush()
            lead = Lead(
                email=f"lead-{tag}-{i}@dry.test", company_id=company.id,
                first_name="Pat", title="operations lead",
                status="verified", email_verified=True,
            )
            s.add(lead)
            s.flush()
            lead_ids.append(lead.id)
            company_ids.append(company.id)

        run = DryRun(campaign_id=campaign.id, status="running", requested_n=n_leads)
        s.add(run)
        s.commit()
        return {
            "campaign_id": campaign.id, "variant_id": variant.id, "run_id": str(run.id),
            "lead_ids": lead_ids, "company_ids": company_ids, "mailbox_id": mailbox.id,
            "tag": tag,
        }


def _cleanup(engine, ids):
    with Session(bind=engine) as s:
        s.query(DryRunItem).filter(
            DryRunItem.dry_run_id.in_(
                select(DryRun.id).where(DryRun.campaign_id == ids["campaign_id"])
            )
        ).delete(synchronize_session=False)
        s.query(DryRun).filter(DryRun.campaign_id == ids["campaign_id"]).delete()
        s.query(Variant).filter(Variant.id == ids["variant_id"]).delete()
        s.query(SequenceStep).filter(SequenceStep.campaign_id == ids["campaign_id"]).delete()
        for lid in ids["lead_ids"]:
            lead = s.get(Lead, lid)
            if lead is not None:
                s.query(SuppressionEntry).filter(SuppressionEntry.email == lead.email).delete()
                s.delete(lead)
        s.query(Campaign).filter(Campaign.id == ids["campaign_id"]).delete()
        s.query(Mailbox).filter(Mailbox.id == ids["mailbox_id"]).delete()
        for cid in ids["company_ids"]:
            comp = s.get(Company, cid)
            if comp is not None:
                s.delete(comp)
        s.commit()


def _patch_task(engine, monkeypatch, fill=GOOD_FILL):
    """Run the task against the test engine, mock LLM, and capture every SMTP call's
    kwargs (patched at aiosmtplib level so deliver_to_mailpit's host choice is real)."""
    smtp_calls: list[dict] = []

    async def fake_send(msg, **kwargs):
        smtp_calls.append({"msg": msg, **kwargs})

    mock = MockLLM()
    mock.respond_with(SlotFill, lambda system, user: fill.model_copy())

    monkeypatch.setattr("craftsman.sender.smtp.aiosmtplib.send", fake_send)
    monkeypatch.setattr(tasks, "get_llm", lambda: mock)

    @contextmanager
    def scope():
        s = Session(bind=engine)
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    monkeypatch.setattr(tasks, "session_scope", scope)
    return smtp_calls


def _counts(s) -> dict:
    return {
        "messages": s.scalar(select(func.count(Message.id))),
        "tokens": s.scalar(select(func.count(UnsubscribeToken.token))),
    }


# ---------------------------------------------------------------- task behavior


def test_dry_run_delivers_to_mailpit_and_mutates_nothing(engine, monkeypatch):
    ids = _setup_scenario(engine, n_leads=2)
    try:
        with Session(bind=engine) as s:
            before = _counts(s)
        smtp_calls = _patch_task(engine, monkeypatch)

        tasks.run_dry_run.run(ids["run_id"])

        with Session(bind=engine) as s:
            run = s.get(DryRun, uuid.UUID(ids["run_id"]))
            assert run.status == "complete" and run.error is None
            items = list(s.scalars(select(DryRunItem).where(DryRunItem.dry_run_id == run.id)))
            assert len(items) == 2
            for item in items:
                assert item.validator_ok is True and item.delivered is True
                assert item.icp_score is not None
                assert item.subject and item.body
                assert item.variant_id == ids["variant_id"]

            # Mailpit only — the mailbox's production SMTP host must not appear.
            assert len(smtp_calls) == 2
            from craftsman.core.config import get_settings

            for call in smtp_calls:
                assert call["hostname"] == get_settings().mailpit_smtp_host
                assert call["port"] == get_settings().mailpit_smtp_port
                assert call["msg"]["X-Craftsman-Dry-Run"] == "1"
                assert call["msg"]["From"] == "dry-run@localhost"
                # placeholder unsubscribe URL in the footer — never a real token
                assert "dry-run-preview" in call["msg"].get_content()

            # No production state touched.
            assert _counts(s) == before
            campaign = s.get(Campaign, ids["campaign_id"])
            assert campaign.sent_today == 0
            variant = s.get(Variant, ids["variant_id"])
            assert (variant.alpha, variant.beta) == (1.0, 1.0)
            mailbox = s.get(Mailbox, ids["mailbox_id"])
            assert mailbox.sent_today == 0
            for lid in ids["lead_ids"]:
                lead = s.get(Lead, lid)
                assert lead.icp_score is None and lead.status == "verified"
    finally:
        _cleanup(engine, ids)


def test_validator_rejection_is_a_recorded_outcome_not_a_failure(engine, monkeypatch):
    ids = _setup_scenario(engine, n_leads=1)
    try:
        smtp_calls = _patch_task(engine, monkeypatch, fill=UNGROUNDED_FILL)
        tasks.run_dry_run.run(ids["run_id"])

        with Session(bind=engine) as s:
            run = s.get(DryRun, uuid.UUID(ids["run_id"]))
            assert run.status == "complete"
            (item,) = s.scalars(select(DryRunItem).where(DryRunItem.dry_run_id == run.id))
            assert item.validator_ok is False
            assert item.validator_errors  # grounding errors surfaced verbatim
            assert item.delivered is False
        assert smtp_calls == []  # a rejected fill never reaches SMTP, even Mailpit
    finally:
        _cleanup(engine, ids)


def test_suppressed_lead_is_never_sampled(engine, monkeypatch):
    ids = _setup_scenario(engine, n_leads=2)
    try:
        with Session(bind=engine) as s:
            suppressed = s.get(Lead, ids["lead_ids"][0])
            s.add(SuppressionEntry(email=suppressed.email, reason="unsubscribe"))
            s.commit()
            suppressed_email = suppressed.email

        _patch_task(engine, monkeypatch)
        tasks.run_dry_run.run(ids["run_id"])

        with Session(bind=engine) as s:
            items = list(
                s.scalars(
                    select(DryRunItem).where(
                        DryRunItem.dry_run_id == uuid.UUID(ids["run_id"])
                    )
                )
            )
            assert len(items) == 1
            assert items[0].lead_email != suppressed_email
    finally:
        _cleanup(engine, ids)


def test_per_lead_research_failure_does_not_kill_the_run(engine, monkeypatch):
    ids = _setup_scenario(engine, n_leads=2)
    try:
        with Session(bind=engine) as s:
            # First lead's company loses its cached brief → research must run → fails
            # (fetch patched to return nothing).
            company = s.get(Company, ids["company_ids"][0])
            company.research_brief = None
            company.research_fetched_at = None
            s.commit()
            broken_domain = company.domain

        async def no_sources(domain):
            return []

        monkeypatch.setattr("craftsman.research.agent.fetch_company_text", no_sources)
        _patch_task(engine, monkeypatch)
        tasks.run_dry_run.run(ids["run_id"])

        with Session(bind=engine) as s:
            run = s.get(DryRun, uuid.UUID(ids["run_id"]))
            assert run.status == "complete"
            items = list(s.scalars(select(DryRunItem).where(DryRunItem.dry_run_id == run.id)))
            assert len(items) == 2
            failed = [i for i in items if i.error]
            ok = [i for i in items if not i.error]
            assert len(failed) == 1 and broken_domain in failed[0].error
            assert len(ok) == 1 and ok[0].delivered is True
    finally:
        _cleanup(engine, ids)


# ---------------------------------------------------------------- erasure


def test_erasure_removes_dry_run_items(engine):
    from craftsman.compliance.suppression import erase_lead

    ids = _setup_scenario(engine, n_leads=1)
    try:
        with Session(bind=engine) as s:
            run = s.get(DryRun, uuid.UUID(ids["run_id"]))
            lead = s.get(Lead, ids["lead_ids"][0])
            s.add(
                DryRunItem(
                    dry_run_id=run.id, lead_id=lead.id, lead_email=lead.email,
                    lead_name="Pat", subject="s", body="personalized text", delivered=True,
                )
            )
            s.commit()

        with Session(bind=engine) as s:
            lead = s.get(Lead, ids["lead_ids"][0])
            erase_lead(s, lead)
            s.commit()

        with Session(bind=engine) as s:
            remaining = s.scalar(
                select(func.count(DryRunItem.id)).where(
                    DryRunItem.lead_id == ids["lead_ids"][0]
                )
            )
            assert remaining == 0
    finally:
        _cleanup(engine, ids)


# ---------------------------------------------------------------- API surface


def _operate(make_key):
    return {"Authorization": f"Bearer {make_key('operate')}"}


def test_dry_run_endpoint_requires_variants(client, db, make_key):
    h = _operate(make_key)
    created = client.post(
        "/campaigns",
        json={"name": "no-variants", "icp_description": "x", "value_prop": "y"},
        headers=h,
    ).json()
    resp = client.post(f"/campaigns/{created['id']}/dry-run", json={}, headers=h)
    assert resp.status_code == 400
    assert "variant" in resp.json()["detail"]


def test_dry_run_endpoint_enqueues_and_reads_back(client, db, make_key, monkeypatch):
    h = _operate(make_key)
    created = client.post(
        "/campaigns",
        json={"name": "preflight", "icp_description": "x", "value_prop": "y"},
        headers=h,
    ).json()
    client.post(
        f"/campaigns/{created['id']}/variants",
        json={"step_order": 1, "name": "v1", "skeleton": SKELETON},
        headers=h,
    )

    enqueued: list = []
    monkeypatch.setattr(tasks.run_dry_run, "delay", lambda *a: enqueued.append(a))

    resp = client.post(f"/campaigns/{created['id']}/dry-run", json={"n": 5}, headers=h)
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "running" and body["requested_n"] == 5
    assert enqueued == [(body["id"],)]

    listed = client.get(f"/campaigns/{created['id']}/dry-runs", headers=h).json()
    assert [r["id"] for r in listed] == [body["id"]]
    got = client.get(f"/campaigns/{created['id']}/dry-runs/{body['id']}", headers=h).json()
    assert got["items"] == []

    # n out of bounds
    assert (
        client.post(f"/campaigns/{created['id']}/dry-run", json={"n": 11}, headers=h).status_code
        == 422
    )
    # cross-campaign read 404s
    other = client.post(
        "/campaigns", json={"name": "other", "icp_description": "x", "value_prop": "y"}, headers=h
    ).json()
    assert (
        client.get(f"/campaigns/{other['id']}/dry-runs/{body['id']}", headers=h).status_code
        == 404
    )


def test_dry_run_endpoints_require_scopes(client, db, make_key):
    read_h = {"Authorization": f"Bearer {make_key('read')}"}
    cid = uuid.uuid4()
    assert client.post(f"/campaigns/{cid}/dry-run", json={}, headers=read_h).status_code == 403
    assert client.get(f"/campaigns/{cid}/dry-runs", headers=read_h).status_code == 404  # read ok, campaign missing
