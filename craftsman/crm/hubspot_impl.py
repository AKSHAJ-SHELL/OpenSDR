"""HubSpot adapter (M5.2). Auth: a private-app access token — the self-hoster
path (Settings → Integrations → Private Apps; scopes: crm.objects.contacts.read,
crm.lists.read, crm.objects.notes.write, crm.objects.meetings.write). No OAuth
dance, no SDK; every call is raw httpx against api.hubapi.com.

Credentials dict shape: {"access_token": "pat-..."}.
"""

from datetime import datetime, timezone

import httpx

from craftsman.crm.provider import CRMActivity, CRMContact, CRMListRef

BASE = "https://api.hubapi.com"
PAGE = 100

# HUBSPOT_DEFINED association type ids (fixed platform constants)
_NOTE_TO_CONTACT = 202
_MEETING_TO_CONTACT = 200

CONTACT_PROPERTIES = [
    "email",
    "firstname",
    "lastname",
    "jobtitle",
    "company",
    "website",
    "linkedin_url",
    "hs_timezone",
]


class HubSpotClient:
    provider = "hubspot"

    def __init__(self, credentials: dict, transport: httpx.AsyncBaseTransport | None = None):
        self._headers = {"Authorization": f"Bearer {credentials['access_token']}"}
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=BASE, headers=self._headers, timeout=30, transport=self._transport
        )

    async def test(self) -> str:
        async with self._client() as c:
            resp = await c.get("/account-info/v3/details")
            resp.raise_for_status()
            data = resp.json()
            return f"HubSpot portal {data.get('portalId')} ({data.get('uiDomain', '?')})"

    async def lists(self) -> list[CRMListRef]:
        out: list[CRMListRef] = []
        offset = 0
        async with self._client() as c:
            while True:
                resp = await c.post(
                    "/crm/v3/lists/search", json={"count": PAGE, "offset": offset}
                )
                resp.raise_for_status()
                data = resp.json()
                for item in data.get("lists", []):
                    size = item.get("additionalProperties", {}).get("hs_list_size")
                    out.append(
                        CRMListRef(
                            remote_id=str(item["listId"]),
                            name=item.get("name", ""),
                            size=int(size) if size is not None else None,
                        )
                    )
                if not data.get("hasMore"):
                    return out
                offset = data.get("offset", offset + PAGE)

    async def contacts(self, list_id: str) -> list[CRMContact]:
        record_ids: list[str] = []
        async with self._client() as c:
            after: str | None = None
            while True:
                params: dict = {"limit": PAGE}
                if after:
                    params["after"] = after
                resp = await c.get(f"/crm/v3/lists/{list_id}/memberships", params=params)
                resp.raise_for_status()
                data = resp.json()
                record_ids += [str(r["recordId"]) for r in data.get("results", [])]
                after = data.get("paging", {}).get("next", {}).get("after")
                if not after:
                    break

            contacts: list[CRMContact] = []
            for i in range(0, len(record_ids), PAGE):
                batch = record_ids[i:i + PAGE]
                resp = await c.post(
                    "/crm/v3/objects/contacts/batch/read",
                    json={
                        "inputs": [{"id": rid} for rid in batch],
                        "properties": CONTACT_PROPERTIES,
                    },
                )
                resp.raise_for_status()
                contacts += [
                    CRMContact(
                        remote_id=str(r["id"]),
                        remote_type="contact",
                        fields=r.get("properties", {}) or {},
                    )
                    for r in resp.json().get("results", [])
                ]
        return contacts

    async def log_activity(self, activity: CRMActivity) -> None:
        await self._create_engagement(
            "notes",
            {"hs_timestamp": _ts(activity.occurred_at), "hs_note_body": _note_body(activity)},
            _NOTE_TO_CONTACT,
            activity.remote_id,
        )

    async def log_meeting(self, activity: CRMActivity) -> None:
        await self._create_engagement(
            "meetings",
            {
                "hs_timestamp": _ts(activity.occurred_at),
                "hs_meeting_title": activity.summary,
                "hs_meeting_body": activity.detail,
            },
            _MEETING_TO_CONTACT,
            activity.remote_id,
        )

    async def _create_engagement(
        self, object_type: str, properties: dict, assoc_type_id: int, contact_id: str
    ) -> None:
        async with self._client() as c:
            resp = await c.post(
                f"/crm/v3/objects/{object_type}",
                json={
                    "properties": properties,
                    "associations": [
                        {
                            "to": {"id": contact_id},
                            "types": [
                                {
                                    "associationCategory": "HUBSPOT_DEFINED",
                                    "associationTypeId": assoc_type_id,
                                }
                            ],
                        }
                    ],
                },
            )
            resp.raise_for_status()


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _note_body(activity: CRMActivity) -> str:
    return f"[Craftsman {activity.kind}] {activity.summary}\n\n{activity.detail}"
