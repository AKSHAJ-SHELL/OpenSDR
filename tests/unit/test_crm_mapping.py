"""CRM field mapping units (M5.2): overlay semantics, validation, LeadRow
production, domain normalization, and the CRM-owned-field diff."""

import pytest

from craftsman.crm.mapping import (
    CRM_OWNED_LEAD_FIELDS,
    MappingError,
    diff_existing,
    effective_map,
    to_lead_row,
)


class FakeLead:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def test_default_map_passes_validation_for_both_providers():
    for provider in ("hubspot", "salesforce"):
        fmap = effective_map(provider, None)
        assert "email" in fmap.values()


def test_overlay_remaps_and_tombstones():
    fmap = effective_map(
        "hubspot",
        {"custom_role__c": "title", "jobtitle": None, "hs_timezone": ""},
    )
    assert fmap["custom_role__c"] == "title"
    assert "jobtitle" not in fmap
    assert "hs_timezone" not in fmap


def test_overlay_unknown_target_refused():
    with pytest.raises(MappingError, match="unknown mapping target"):
        effective_map("hubspot", {"anything": "icp_score"})  # not a mappable field


def test_map_without_email_refused():
    with pytest.raises(MappingError, match="no source for 'email'"):
        effective_map("hubspot", {"email": None})


def test_unknown_provider_refused():
    with pytest.raises(MappingError, match="unknown CRM provider"):
        effective_map("pipedrive", None)


def test_to_lead_row_applies_map_and_skips_blanks():
    fmap = effective_map("hubspot", None)
    row = to_lead_row(
        {"email": " Jane@Acme.com ", "firstname": "Jane", "lastname": "", "company": "Acme"},
        fmap,
    )
    assert row is not None
    assert row.normalized_email() == "jane@acme.com"
    assert row.first_name == "Jane"
    assert row.last_name is None
    assert row.company_name == "Acme"


def test_to_lead_row_no_email_returns_none():
    fmap = effective_map("salesforce", None)
    assert to_lead_row({"FirstName": "No", "LastName": "Email"}, fmap) is None


@pytest.mark.parametrize(
    ("website", "expected"),
    [
        ("https://www.acme.com/about", "acme.com"),
        ("http://acme.io", "acme.io"),
        ("acme.dev", "acme.dev"),
        ("https://", None),
    ],
)
def test_website_normalizes_to_bare_domain(website, expected):
    fmap = effective_map("hubspot", None)
    row = to_lead_row({"email": "a@b.co", "website": website}, fmap)
    assert row.company_domain == expected


def test_diff_reports_only_crm_owned_changed_fields():
    fmap = effective_map("hubspot", None)
    row = to_lead_row(
        {"email": "a@b.co", "firstname": "Janet", "jobtitle": "VP Eng"}, fmap
    )
    lead = FakeLead(
        first_name="Jane", last_name="Doe", title="VP Eng", linkedin_url=None,
        timezone="America/Los_Angeles", status="verified", icp_score=0.9,
    )
    changes = diff_existing(row, lead)
    assert changes == {"first_name": {"from": "Jane", "to": "Janet"}}
    # engagement/status fields are structurally not diffable
    assert "status" not in CRM_OWNED_LEAD_FIELDS
    assert "icp_score" not in CRM_OWNED_LEAD_FIELDS


def test_diff_never_clears_a_field_the_crm_left_blank():
    fmap = effective_map("hubspot", None)
    row = to_lead_row({"email": "a@b.co"}, fmap)  # CRM sent nothing but email
    lead = FakeLead(
        first_name="Jane", last_name="Doe", title="VP", linkedin_url="x",
        timezone="UTC",
    )
    assert diff_existing(row, lead) == {}
