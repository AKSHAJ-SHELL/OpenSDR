"""Campaign builder API (M1.1): read-back detail, guarded edits, skeleton validation.

The guarantees under test:
- the builder can read back everything it can write (steps + variants + skeletons);
- a skeleton typo is a 422 at authoring time, not an `error` enrollment at send time;
- sequence structure freezes once anyone is enrolled;
- a variant's skeleton freezes once its arm has trials (clone, don't rewrite history).
"""

import uuid

from sqlalchemy import select

from craftsman.core.models import Campaign, Enrollment, Lead, SequenceStep, Variant

SKELETON = (
    "Subject: {{subject_hook}}\n\n"
    "Hi {{first_name}},\n\n"
    "{{personalization_sentence}}\n\n"
    "{{value_prop_bridge}} {{cta_question}}\n\n"
    "{{signature}}"
)


def _operate(make_key):
    return {"Authorization": f"Bearer {make_key('operate')}"}


def _create_campaign(client, headers, steps=(0, 3)):
    resp = client.post(
        "/campaigns",
        json={
            "name": "builder-test",
            "icp_description": "seed-stage devtools founders",
            "value_prop": "we shorten CI times",
            "sender_persona": {"name": "Ada", "title": "SE", "company": "Craftsman"},
            "daily_cap": 10,
            "steps": list(steps),
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _enroll_someone(db, campaign_id):
    lead = Lead(email=f"p-{uuid.uuid4().hex[:8]}@example.com", status="verified")
    db.add(lead)
    db.flush()
    db.add(Enrollment(lead_id=lead.id, campaign_id=uuid.UUID(campaign_id), state="queued"))
    db.flush()


# ---------------------------------------------------------------- read-back


def test_detail_reads_back_steps_and_variants(client, make_key):
    h = _operate(make_key)
    cid = _create_campaign(client, h, steps=(0, 3, 4))
    assert (
        client.post(
            f"/campaigns/{cid}/variants",
            json={"step_order": 1, "name": "pain_led", "skeleton": SKELETON},
            headers=h,
        ).status_code
        == 200
    )

    detail = client.get(f"/campaigns/{cid}", headers=h).json()
    assert detail["sender_persona"]["name"] == "Ada"
    assert [s["step_order"] for s in detail["steps"]] == [1, 2, 3]
    assert [s["wait_days"] for s in detail["steps"]] == [0, 3, 4]
    (variant,) = detail["steps"][0]["variants"]
    assert variant["skeleton"] == SKELETON
    assert variant["trials"] == 0
    assert variant["active"] is True


# ---------------------------------------------------------------- campaign PATCH


def test_patch_reembeds_only_on_icp_change(client, db, make_key):
    h = _operate(make_key)
    cid = _create_campaign(client, h)
    baseline = list(db.get(Campaign, uuid.UUID(cid)).icp_embedding)

    resp = client.patch(f"/campaigns/{cid}", json={"name": "renamed", "daily_cap": 25}, headers=h)
    assert resp.status_code == 200
    campaign = db.get(Campaign, uuid.UUID(cid))
    assert campaign.name == "renamed" and campaign.daily_cap == 25
    assert list(campaign.icp_embedding) == baseline  # untouched

    resp = client.patch(
        f"/campaigns/{cid}", json={"icp_description": "series-B fintech CTOs"}, headers=h
    )
    assert resp.status_code == 200
    assert list(db.get(Campaign, uuid.UUID(cid)).icp_embedding) != baseline

    assert resp.json()["icp_description"] == "series-B fintech CTOs"


def test_patch_cannot_touch_status(client, make_key):
    h = _operate(make_key)
    cid = _create_campaign(client, h)
    resp = client.patch(f"/campaigns/{cid}", json={"status": "active"}, headers=h)
    # unknown field is ignored by the schema; status stays draft
    assert resp.status_code == 200
    assert resp.json()["status"] != "active"


# ---------------------------------------------------------------- steps


def test_add_step_appends_and_delete_renumbers(client, make_key):
    h = _operate(make_key)
    cid = _create_campaign(client, h, steps=(0, 3))

    created = client.post(f"/campaigns/{cid}/steps", json={"wait_days": 7}, headers=h)
    assert created.status_code == 201
    assert created.json()["step_order"] == 3

    detail = client.get(f"/campaigns/{cid}", headers=h).json()
    step2 = next(s for s in detail["steps"] if s["step_order"] == 2)
    assert client.delete(f"/campaigns/{cid}/steps/{step2['id']}", headers=h).status_code == 204

    after = client.get(f"/campaigns/{cid}", headers=h).json()
    assert [s["step_order"] for s in after["steps"]] == [1, 2]
    assert [s["wait_days"] for s in after["steps"]] == [0, 7]  # the old step 3 moved up


def test_structure_freezes_once_enrolled_but_wait_days_stays_editable(client, db, make_key):
    h = _operate(make_key)
    cid = _create_campaign(client, h, steps=(0, 3))
    _enroll_someone(db, cid)

    assert client.post(f"/campaigns/{cid}/steps", json={"wait_days": 5}, headers=h).status_code == 409
    detail = client.get(f"/campaigns/{cid}", headers=h).json()
    sid = detail["steps"][0]["id"]
    assert client.delete(f"/campaigns/{cid}/steps/{sid}", headers=h).status_code == 409

    # wait_days is read at scheduling time — editable on a live campaign
    resp = client.patch(f"/campaigns/{cid}/steps/{sid}", json={"wait_days": 9}, headers=h)
    assert resp.status_code == 200
    assert resp.json()["wait_days"] == 9


# ---------------------------------------------------------------- variants


def test_variant_unknown_placeholder_is_422_at_authoring_time(client, make_key):
    h = _operate(make_key)
    cid = _create_campaign(client, h)
    resp = client.post(
        f"/campaigns/{cid}/variants",
        json={"step_order": 1, "name": "typo", "skeleton": "Subject: {{subject_hok}}\n{{signature}}"},
        headers=h,
    )
    assert resp.status_code == 422
    assert "subject_hok" in resp.json()["detail"]


def test_variant_slot_schema_derived_when_omitted(client, make_key):
    h = _operate(make_key)
    cid = _create_campaign(client, h)
    resp = client.post(
        f"/campaigns/{cid}/variants",
        json={"step_order": 1, "name": "v", "skeleton": SKELETON},
        headers=h,
    )
    assert resp.status_code == 200
    # derived from placeholders actually used; static slots excluded
    assert resp.json()["slot_schema"] == {
        "cta_question": "string",
        "personalization_sentence": "string",
        "subject_hook": "string",
        "value_prop_bridge": "string",
    }


def test_variant_skeleton_freezes_after_first_trial(client, db, make_key):
    h = _operate(make_key)
    cid = _create_campaign(client, h)
    created = client.post(
        f"/campaigns/{cid}/variants",
        json={"step_order": 1, "name": "v", "skeleton": SKELETON},
        headers=h,
    ).json()

    # trials == 0 → skeleton editable, slot_schema re-derived
    slim = "Subject: {{subject_hook}}\n\n{{value_prop_bridge}}\n\n{{signature}}"
    resp = client.patch(
        f"/campaigns/{cid}/variants/{created['id']}", json={"skeleton": slim}, headers=h
    )
    assert resp.status_code == 200
    assert resp.json()["slot_schema"] == {"subject_hook": "string", "value_prop_bridge": "string"}

    # record a trial → frozen
    variant = db.get(Variant, uuid.UUID(created["id"]))
    variant.beta += 1.0
    db.flush()
    resp = client.patch(
        f"/campaigns/{cid}/variants/{created['id']}", json={"skeleton": SKELETON}, headers=h
    )
    assert resp.status_code == 409
    assert "clone" in resp.json()["detail"]

    # name and active stay editable on a tried arm (deactivation = the clone flow)
    resp = client.patch(
        f"/campaigns/{cid}/variants/{created['id']}",
        json={"name": "v-old", "active": False},
        headers=h,
    )
    assert resp.status_code == 200
    assert resp.json()["active"] is False


def test_variant_endpoints_404_across_campaigns(client, make_key):
    h = _operate(make_key)
    cid_a = _create_campaign(client, h)
    cid_b = _create_campaign(client, h)
    created = client.post(
        f"/campaigns/{cid_a}/variants",
        json={"step_order": 1, "name": "v", "skeleton": SKELETON},
        headers=h,
    ).json()
    # a variant is only addressable through its own campaign
    resp = client.patch(
        f"/campaigns/{cid_b}/variants/{created['id']}", json={"name": "x"}, headers=h
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------- scopes


def test_builder_write_endpoints_require_operate(client, make_key):
    h = {"Authorization": f"Bearer {make_key('read')}"}
    cid = str(uuid.uuid4())
    assert client.patch(f"/campaigns/{cid}", json={"name": "x"}, headers=h).status_code == 403
    assert client.post(f"/campaigns/{cid}/steps", json={"wait_days": 1}, headers=h).status_code == 403
    assert (
        client.patch(
            f"/campaigns/{cid}/steps/{uuid.uuid4()}", json={"wait_days": 1}, headers=h
        ).status_code
        == 403
    )
    assert client.delete(f"/campaigns/{cid}/steps/{uuid.uuid4()}", headers=h).status_code == 403
    assert (
        client.patch(f"/campaigns/{cid}/variants/{uuid.uuid4()}", json={}, headers=h).status_code
        == 403
    )
