"""CRM adapter units (M5.2): HubSpot + Salesforce request/response handling via
httpx.MockTransport — no live network. The Salesforce SSRF guard and SOQL id
validation are exercised here too (the adversarial suite re-checks them through
the API layer)."""

import json
from datetime import datetime, timezone

import httpx
import pytest

from craftsman.crm.hubspot_impl import HubSpotClient
from craftsman.crm.provider import CRMActivity
from craftsman.crm.salesforce_impl import SalesforceClient, SalesforceError
from craftsman.research.fetch import UnsafeURL

OCCURRED = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def _activity(kind="reply", remote_id="003xx000004TmiQAAS"):
    return CRMActivity(
        remote_id=remote_id, remote_type="Contact", kind=kind,
        summary="Replied: interested", detail="Full reply text", occurred_at=OCCURRED,
    )


# ---------------------------------------------------------------- HubSpot

def _hubspot(handler):
    return HubSpotClient(
        {"access_token": "pat-test"}, transport=httpx.MockTransport(handler)
    )


async def test_hubspot_contacts_pages_memberships_then_batch_reads():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path == "/crm/v3/lists/7/memberships":
            if request.url.params.get("after") is None:
                return httpx.Response(200, json={
                    "results": [{"recordId": 1}, {"recordId": 2}],
                    "paging": {"next": {"after": "2"}},
                })
            return httpx.Response(200, json={"results": [{"recordId": 3}]})
        if path == "/crm/v3/objects/contacts/batch/read":
            body = json.loads(request.content)
            return httpx.Response(200, json={"results": [
                {"id": i["id"], "properties": {"email": f"u{i['id']}@x.co", "firstname": "U"}}
                for i in body["inputs"]
            ]})
        raise AssertionError(f"unexpected call: {path}")

    contacts = await _hubspot(handler).contacts("7")
    assert [c.remote_id for c in contacts] == ["1", "2", "3"]
    assert contacts[0].fields["email"] == "u1@x.co"
    assert contacts[0].remote_type == "contact"
    # memberships paged twice, one batch read (3 ids < page size)
    assert len(calls) == 3
    assert all(c.headers["authorization"] == "Bearer pat-test" for c in calls)


async def test_hubspot_note_carries_association_and_timestamp():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(201, json={"id": "n1"})

    await _hubspot(handler).log_activity(_activity(remote_id="42"))
    assert seen["properties"]["hs_timestamp"] == "2026-07-27T12:00:00.000Z"
    assert "[Craftsman reply] Replied: interested" in seen["properties"]["hs_note_body"]
    assoc = seen["associations"][0]
    assert assoc["to"] == {"id": "42"}
    assert assoc["types"][0]["associationTypeId"] == 202  # note→contact


async def test_hubspot_meeting_uses_meeting_object_and_association():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen.update(json.loads(request.content))
        return httpx.Response(201, json={"id": "m1"})

    await _hubspot(handler).log_meeting(_activity(kind="meeting", remote_id="42"))
    assert seen["path"] == "/crm/v3/objects/meetings"
    assert seen["associations"][0]["types"][0]["associationTypeId"] == 200  # meeting→contact


async def test_hubspot_api_error_propagates():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "missing scope"})

    with pytest.raises(httpx.HTTPStatusError):
        await _hubspot(handler).test()


# ---------------------------------------------------------------- Salesforce

CREDS = {
    "instance_url": "https://acme.example.com",
    "client_id": "cid",
    "client_secret": "csec",
}


def _sf(handler, monkeypatch=None):
    # neutralize live DNS resolution inside the SSRF guard for unit tests
    return SalesforceClient(CREDS, transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    from craftsman.research import fetch

    monkeypatch.setattr(fetch, "_resolve_ips", lambda host: ["93.184.216.34"])


def _sf_handler(pages):
    """Token endpoint + scripted query/sobject responses."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/services/oauth2/token":
            assert b"grant_type=client_credentials" in request.content
            return httpx.Response(200, json={"access_token": "sf-tok"})
        return pages(request)

    handler.calls = calls
    return handler


async def test_salesforce_authenticates_then_queries_with_paging():
    def pages(request):
        q = request.url.params.get("q", "")
        if "FROM Organization" in q:
            return httpx.Response(200, json={
                "done": False, "nextRecordsUrl": "/services/data/v59.0/query/next-1",
                "records": [{"Name": "Acme"}],
            })
        if request.url.path.endswith("next-1"):
            return httpx.Response(200, json={"done": True, "records": [{"Name": "Ignored"}]})
        raise AssertionError(f"unexpected: {request.url}")

    handler = _sf_handler(pages)
    out = await _sf(handler).test()
    assert "Acme" in out
    # first call is the token grant, then the two query pages
    assert handler.calls[0].url.path == "/services/oauth2/token"
    assert handler.calls[1].headers["authorization"] == "Bearer sf-tok"


async def test_salesforce_campaign_members_cover_contacts_and_leads():
    def pages(request):
        q = request.url.params.get("q", "")
        if "ContactId != null" in q:
            return httpx.Response(200, json={"done": True, "records": [{
                "ContactId": "003xx000004TmiQAAS",
                "Contact": {"attributes": {"type": "Contact"}, "Email": "c@x.co",
                            "FirstName": "Con", "LastName": "Tact", "Title": "VP"},
            }]})
        if "LeadId != null" in q:
            return httpx.Response(200, json={"done": True, "records": [{
                "LeadId": "00Qxx000004TmiQEAS",
                "Lead": {"attributes": {"type": "Lead"}, "Email": "l@y.co",
                         "FirstName": "Lea", "LastName": "D", "Title": None,
                         "Company": "Y Inc", "Website": "https://y.co"},
            }]})
        raise AssertionError(f"unexpected: {q}")

    contacts = await _sf(_sf_handler(pages)).contacts("701xx000000AAAA")
    assert {c.remote_type for c in contacts} == {"Contact", "Lead"}
    lead = next(c for c in contacts if c.remote_type == "Lead")
    assert lead.fields["Company"] == "Y Inc"
    assert "attributes" not in lead.fields


async def test_salesforce_soql_injection_refused():
    client = _sf(_sf_handler(lambda r: httpx.Response(200, json={})))
    with pytest.raises(SalesforceError, match="not a Salesforce id"):
        await client.contacts("701' OR Name != null OR Id = '")


async def test_salesforce_reauths_once_on_401():
    state = {"queries": 0}

    def pages(request):
        state["queries"] += 1
        if state["queries"] == 1:
            return httpx.Response(401, json=[{"errorCode": "INVALID_SESSION_ID"}])
        return httpx.Response(200, json={"done": True, "records": [{"Name": "Acme"}]})

    handler = _sf_handler(pages)
    out = await _sf(handler).test()
    assert "Acme" in out
    token_calls = [c for c in handler.calls if c.url.path == "/services/oauth2/token"]
    assert len(token_calls) == 2  # initial + refresh after 401


async def test_salesforce_task_and_event_shapes():
    posted = []

    def pages(request):
        posted.append((request.url.path, json.loads(request.content)))
        return httpx.Response(201, json={"id": "00Txx", "success": True})

    client = _sf(_sf_handler(pages))
    await client.log_activity(_activity())
    await client.log_meeting(_activity(kind="meeting"))
    (task_path, task), (event_path, event) = posted
    assert task_path.endswith("/sobjects/Task")
    assert task["WhoId"] == "003xx000004TmiQAAS"
    assert task["Status"] == "Completed"
    assert event_path.endswith("/sobjects/Event")
    assert event["ActivityDateTime"] == "2026-07-27T12:00:00Z"
    assert event["DurationInMinutes"] == 30


async def test_salesforce_http_instance_url_refused():
    with pytest.raises(UnsafeURL):
        SalesforceClient({**CREDS, "instance_url": "http://acme.example.com"})
