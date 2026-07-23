"""Deliverability report endpoint (M1.4).

Guarantees under test:
- GET /mailboxes/{id}/deliverability returns live SPF/DKIM/DMARC status (resolver
  patched) plus the warmup ramp;
- it's read-scoped and 404s for an unknown mailbox;
- dkim_selector round-trips through create/update and is authoritative for the check.
"""

from craftsman.deliverability import dns_auth


def _headers(make_key, scope="read"):
    return {"Authorization": f"Bearer {make_key(scope)}"}


def _patch_dns(monkeypatch, table):
    def fake(name, timeout=5.0):
        return list(table.get(name, []))

    monkeypatch.setattr(dns_auth, "resolve_txt", fake)


def _create_mailbox(client, make_key, **extra):
    body = {
        "email": "sender@acme.com",
        "smtp_host": "smtp.acme.com",
        "smtp_port": 587,
        "smtp_user": "sender@acme.com",
        "smtp_password": "secret",
        **extra,
    }
    r = client.post("/mailboxes", json=body, headers={"Authorization": f"Bearer {make_key('admin')}"})
    assert r.status_code == 200, r.text
    return r.json()


def test_deliverability_all_present(client, make_key, monkeypatch):
    _patch_dns(
        monkeypatch,
        {
            "acme.com": ["v=spf1 include:_spf.google.com ~all"],
            "_dmarc.acme.com": ["v=DMARC1; p=quarantine; rua=mailto:d@acme.com"],
            "sel1._domainkey.acme.com": ["v=DKIM1; k=rsa; p=MIGf"],
        },
    )
    box = _create_mailbox(client, make_key, dkim_selector="sel1", daily_limit=40)
    assert box["dkim_selector"] == "sel1"  # round-trips through create

    r = client.get(f"/mailboxes/{box['id']}/deliverability", headers=_headers(make_key))
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["domain"] == "acme.com"
    assert rep["primary_domain_warning"] is True  # acme.com is a bare domain
    assert rep["spf"]["status"] == "pass"
    assert rep["dmarc"]["status"] == "pass" and rep["dmarc"]["policy"] == "quarantine"
    assert rep["dkim"]["status"] == "pass" and rep["dkim"]["selector"] == "sel1"
    # warmup ramp surfaced from the stored stage/limit
    assert rep["warmup"]["daily_limit"] == 40
    assert rep["warmup"]["advances_per_day"] == 1
    assert [s["cap"] for s in rep["warmup"]["schedule"]] == [10, 20, 30, 40, 40]


def test_deliverability_missing_records(client, make_key, monkeypatch):
    _patch_dns(monkeypatch, {})  # nothing resolves
    box = _create_mailbox(client, make_key)
    r = client.get(f"/mailboxes/{box['id']}/deliverability", headers=_headers(make_key))
    rep = r.json()
    assert rep["spf"]["status"] == "missing"
    assert rep["dmarc"]["status"] == "missing"
    assert rep["dkim"]["status"] == "missing"
    # the fix-it values are handed over even when absent
    assert rep["spf"]["recommended"].startswith("v=spf1")
    assert "acme.com" in rep["dmarc"]["recommended"]


def test_deliverability_requires_read_scope(client, make_key, monkeypatch):
    _patch_dns(monkeypatch, {})
    box = _create_mailbox(client, make_key)
    r = client.get(f"/mailboxes/{box['id']}/deliverability")  # no auth
    assert r.status_code == 401


def test_deliverability_unknown_mailbox_404(client, make_key, monkeypatch):
    _patch_dns(monkeypatch, {})
    import uuid

    r = client.get(f"/mailboxes/{uuid.uuid4()}/deliverability", headers=_headers(make_key))
    assert r.status_code == 404


def test_dkim_selector_updates(client, make_key, monkeypatch):
    _patch_dns(monkeypatch, {"newsel._domainkey.acme.com": ["v=DKIM1; p=abc"]})
    box = _create_mailbox(client, make_key)  # no selector → would probe
    admin = {"Authorization": f"Bearer {make_key('admin')}"}
    r = client.patch(f"/mailboxes/{box['id']}", json={"dkim_selector": "newsel"}, headers=admin)
    assert r.status_code == 200 and r.json()["dkim_selector"] == "newsel"

    rep = client.get(f"/mailboxes/{box['id']}/deliverability", headers=_headers(make_key)).json()
    assert rep["dkim"]["status"] == "pass" and rep["dkim"]["selector"] == "newsel"
