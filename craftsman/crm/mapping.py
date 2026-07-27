"""CRM contact-field mapping (M5.2): provider defaults + per-connection overlay.

A field map is {crm_attribute_name: leadrow_field}. The connection's
`field_map` JSONB overlays the provider default — set a key to remap it, set
its value to null/"" to drop the default. The result of applying a map to a
CRMContact is LeadRow kwargs, which is the whole point: past this module, a
CRM import is indistinguishable from a CSV import and flows through the same
ingest gate (dedupe, suppression, verification) with no special cases.

`preview()` is the dry-run: what WOULD each contact become, and — for contacts
matching an existing lead by normalized email — which lead fields would change.
Per the source-of-truth doctrine (CRM wins contact fields), a committed import
overwrites those fields; the preview shows exactly that diff first.
"""

from dataclasses import dataclass, field

from craftsman.ingest.gate import LeadRow

# LeadRow fields a map may target (email is required; the rest optional)
TARGETS: tuple[str, ...] = (
    "email",
    "first_name",
    "last_name",
    "title",
    "company_name",
    "company_domain",
    "linkedin_url",
    "timezone",
)

DEFAULT_MAPS: dict[str, dict[str, str]] = {
    "hubspot": {
        "email": "email",
        "firstname": "first_name",
        "lastname": "last_name",
        "jobtitle": "title",
        "company": "company_name",
        "website": "company_domain",
        "linkedin_url": "linkedin_url",
        "hs_timezone": "timezone",
    },
    "salesforce": {
        "Email": "email",
        "FirstName": "first_name",
        "LastName": "last_name",
        "Title": "title",
        "Company": "company_name",
        "Website": "company_domain",
        "LinkedIn__c": "linkedin_url",
    },
}


class MappingError(ValueError):
    """A field map that can't work: unknown target, or no source for email."""


def effective_map(provider: str, overlay: dict | None) -> dict[str, str]:
    """Provider default + connection overlay, validated. Raises MappingError
    on a target outside TARGETS or a map that never produces an email."""
    if provider not in DEFAULT_MAPS:
        raise MappingError(f"unknown CRM provider: {provider!r}")
    merged = dict(DEFAULT_MAPS[provider])
    for src, target in (overlay or {}).items():
        if not target:  # null/"" tombstones the default
            merged.pop(src, None)
            continue
        if target not in TARGETS:
            raise MappingError(f"unknown mapping target: {target!r} (from {src!r})")
        merged[src] = target
    if "email" not in merged.values():
        raise MappingError("field map has no source for 'email'")
    return merged


def _domain_from(value: str | None) -> str | None:
    """CRM 'website' values arrive as URLs; company_domain wants a bare host."""
    if not value:
        return None
    v = value.strip().lower()
    for prefix in ("https://", "http://"):
        if v.startswith(prefix):
            v = v[len(prefix):]
    v = v.split("/", 1)[0]
    return v.removeprefix("www.") or None


def to_lead_row(fields: dict, fmap: dict[str, str]) -> LeadRow | None:
    """Apply a validated map to one contact's raw fields. Returns None when
    the contact has no email — the gate would reject it anyway, but skipping
    here keeps the dry-run tally honest ('no_email', not 'invalid')."""
    kwargs: dict[str, str] = {}
    for src, target in fmap.items():
        raw = fields.get(src)
        if raw is None or str(raw).strip() == "":
            continue
        value = str(raw).strip()
        if target == "company_domain":
            value = _domain_from(value) or ""
            if not value:
                continue
        kwargs[target] = value
    if not kwargs.get("email"):
        return None
    return LeadRow(**kwargs)


# Lead columns the CRM overwrites on a committed re-import (contact fields
# only — never status/scores/engagement, which are Craftsman's).
CRM_OWNED_LEAD_FIELDS: tuple[str, ...] = (
    "first_name",
    "last_name",
    "title",
    "linkedin_url",
    "timezone",
)


@dataclass
class PreviewRow:
    email: str
    action: str  # create | update | unchanged | suppressed | no_email
    changes: dict = field(default_factory=dict)  # field -> {from, to} on update


def diff_existing(row: LeadRow, lead) -> dict:
    """CRM-owned fields that a committed import would overwrite on `lead`."""
    changes: dict = {}
    for f in CRM_OWNED_LEAD_FIELDS:
        new = getattr(row, f, None)
        if new is None:
            continue
        old = getattr(lead, f, None)
        if (old or None) != new:
            changes[f] = {"from": old, "to": new}
    return changes
