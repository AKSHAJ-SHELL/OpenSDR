"""Salesforce adapter (M5.2). Auth: OAuth2 client-credentials against the
org's My Domain token endpoint (External Client App / Connected App with the
flow enabled and a run-as user). The instance URL is operator-supplied, so it
passes the M0.5 SSRF guard at connection registration AND again here at every
client construction — DNS can change between the two.

Credentials dict shape:
    {"instance_url": "https://acme.my.salesforce.com",
     "client_id": "...", "client_secret": "..."}

"Lists" are Salesforce Campaigns; their members can be Contacts or Leads, and
both are imported (remote_type records which sobject the id belongs to, so
activity push targets the right WhoId). SOQL is never built from free-form
caller input — remote ids are validated as Salesforce ID literals first.
"""

import re
from datetime import datetime, timezone

import httpx

from craftsman.crm.provider import CRMActivity, CRMContact, CRMListRef
from craftsman.research.fetch import validate_url

API = "/services/data/v59.0"
_SF_ID = re.compile(r"^[a-zA-Z0-9]{15,18}$")

CONTACT_FIELDS = ["Email", "FirstName", "LastName", "Title"]


class SalesforceError(RuntimeError):
    pass


def _sf_id(value: str) -> str:
    """The only thing ever interpolated into SOQL. Anything that is not a
    bare Salesforce ID literal is refused — there is no escaping path."""
    if not _SF_ID.match(value or ""):
        raise SalesforceError(f"not a Salesforce id: {value!r}")
    return value


class SalesforceClient:
    provider = "salesforce"

    def __init__(self, credentials: dict, transport: httpx.AsyncBaseTransport | None = None):
        self.instance_url = credentials["instance_url"].rstrip("/")
        validate_url(self.instance_url)  # SSRF: https-only, public IPs only
        self._client_id = credentials["client_id"]
        self._client_secret = credentials["client_secret"]
        self._transport = transport
        self._token: str | None = None

    def _client(self) -> httpx.AsyncClient:
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        return httpx.AsyncClient(
            base_url=self.instance_url, headers=headers, timeout=30,
            transport=self._transport,
        )

    async def _authenticate(self) -> None:
        async with httpx.AsyncClient(
            base_url=self.instance_url, timeout=30, transport=self._transport
        ) as c:
            resp = await c.post(
                "/services/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )
            if resp.status_code != 200:
                raise SalesforceError(f"token request failed: {resp.status_code}")
            self._token = resp.json()["access_token"]

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """One transparent re-auth on 401 (expired token), then fail loudly."""
        if self._token is None:
            await self._authenticate()
        async with self._client() as c:
            resp = await c.request(method, url, **kwargs)
        if resp.status_code == 401:
            await self._authenticate()
            async with self._client() as c:
                resp = await c.request(method, url, **kwargs)
        resp.raise_for_status()
        return resp

    async def _query(self, soql: str) -> list[dict]:
        records: list[dict] = []
        resp = await self._request("GET", f"{API}/query", params={"q": soql})
        data = resp.json()
        records += data.get("records", [])
        while not data.get("done") and data.get("nextRecordsUrl"):
            resp = await self._request("GET", data["nextRecordsUrl"])
            data = resp.json()
            records += data.get("records", [])
        return records

    async def test(self) -> str:
        rows = await self._query("SELECT Name FROM Organization")
        name = rows[0]["Name"] if rows else "?"
        return f"Salesforce org {name} ({self.instance_url})"

    async def lists(self) -> list[CRMListRef]:
        rows = await self._query(
            "SELECT Id, Name, NumberOfContacts, NumberOfLeads FROM Campaign ORDER BY Name"
        )
        return [
            CRMListRef(
                remote_id=r["Id"],
                name=r.get("Name", ""),
                size=(r.get("NumberOfContacts") or 0) + (r.get("NumberOfLeads") or 0),
            )
            for r in rows
        ]

    async def contacts(self, list_id: str) -> list[CRMContact]:
        cid = _sf_id(list_id)
        out: list[CRMContact] = []
        for sobject, id_field in (("Contact", "ContactId"), ("Lead", "LeadId")):
            fields = list(CONTACT_FIELDS)
            if sobject == "Lead":
                fields += ["Company", "Website"]
            select = ", ".join(f"{sobject}.{f}" for f in fields)
            rows = await self._query(
                f"SELECT {id_field}, {select} FROM CampaignMember "
                f"WHERE CampaignId = '{cid}' AND {id_field} != null"
            )
            for r in rows:
                nested = r.get(sobject) or {}
                nested.pop("attributes", None)
                out.append(
                    CRMContact(
                        remote_id=r[id_field], remote_type=sobject, fields=nested
                    )
                )
        return out

    async def log_activity(self, activity: CRMActivity) -> None:
        await self._request(
            "POST",
            f"{API}/sobjects/Task",
            json={
                "WhoId": _sf_id(activity.remote_id),
                "Subject": f"[Craftsman {activity.kind}] {activity.summary}",
                "Description": activity.detail,
                "Status": "Completed",
                "ActivityDate": activity.occurred_at.date().isoformat(),
            },
        )

    async def log_meeting(self, activity: CRMActivity) -> None:
        await self._request(
            "POST",
            f"{API}/sobjects/Event",
            json={
                "WhoId": _sf_id(activity.remote_id),
                "Subject": activity.summary,
                "Description": activity.detail,
                "ActivityDateTime": activity.occurred_at.astimezone(timezone.utc)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z"),
                "DurationInMinutes": 30,
            },
        )
