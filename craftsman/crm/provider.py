"""CRM provider protocol (M5.2, G10) — the extension point forks add adapters to.

Everything a CRM adapter produces or consumes is one of the dataclasses below;
sync.py and the API never see provider-specific JSON. Credentials travel as a
plain dict (provider-specific shape, documented per adapter) and are stored
Fernet-encrypted on the connection row — an adapter receives the decrypted
dict at construction and must never log it.

Conflict doctrine (roadmap M5.2): the CRM is the source of truth for contact
fields, Craftsman for engagement history. Concretely: inbound import may
create/update leads; outbound sync only ever appends activity — no adapter
method exists to mutate a remote contact's fields.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass
class CRMContact:
    """A remote contact, already reduced to the provider's raw field dict.
    `fields` keys are provider attribute names — crm/mapping.py turns them
    into LeadRow kwargs via the connection's field map."""

    remote_id: str
    remote_type: str  # e.g. "contact" (HubSpot), "Contact"/"Lead" (Salesforce)
    fields: dict = field(default_factory=dict)


@dataclass
class CRMListRef:
    """An importable list/segment (HubSpot list, Salesforce campaign/report)."""

    remote_id: str
    name: str
    size: int | None = None


@dataclass
class CRMActivity:
    """One engagement-history item to append to a remote contact's timeline.
    `kind` is one of: send | reply | classification | meeting. Meetings are
    additionally created as first-class CRM events by adapters that support
    them (`log_meeting`); everything else lands as a timeline note."""

    remote_id: str
    remote_type: str
    kind: str
    summary: str
    detail: str
    occurred_at: datetime


class CRMProvider(Protocol):
    """What sync.py needs from an adapter. All methods are async (raw httpx
    inside, like the enrichment providers — no vendor SDKs)."""

    provider: str  # registry key: "hubspot" | "salesforce"

    async def test(self) -> str:
        """Cheap authenticated call; returns a human-readable identity string
        (portal/org name) or raises. Backs POST /crm/connections/{id}/test."""
        ...

    async def lists(self) -> list[CRMListRef]:
        """Importable lists/segments, for the mapping UI's dropdown."""
        ...

    async def contacts(self, list_id: str) -> list[CRMContact]:
        """All contacts in a list, paged internally until exhausted."""
        ...

    async def log_activity(self, activity: CRMActivity) -> None:
        """Append one timeline note to the contact. Idempotency is the
        caller's job (sync.py pushes strictly past the connection watermark)."""
        ...

    async def log_meeting(self, activity: CRMActivity) -> None:
        """Create a meeting/event object linked to the contact. Adapters
        without a meeting object fall back to log_activity."""
        ...
