"""Chain semantics (M2.1): precedence, first-writer-wins, provenance. No network,
no database — fake providers only."""

from craftsman.core.config import Settings
from craftsman.ingest.enrichment import (
    ApolloProvider,
    EnrichmentInput,
    EnrichmentResult,
    HunterProvider,
    NullProvider,
    build_enrichment_chain,
    chain_enrich,
)

INP = EnrichmentInput(email="dana@acme.com")


class Fake:
    def __init__(self, name, fields, confidence=0.5):
        self.name = name
        self._result = EnrichmentResult(name, confidence, fields) if fields else None

    async def enrich(self, inp):
        return self._result


async def test_first_writer_wins_per_field():
    first = Fake("first", {"title": "VP Ops", "phone": "+1111"})
    second = Fake("second", {"title": "SHOULD LOSE", "linkedin_url": "in/dana"})
    merged, prov = await chain_enrich([first, second], INP)

    assert merged == {"title": "VP Ops", "phone": "+1111", "linkedin_url": "in/dana"}
    # provenance: exactly one record per merged field, crediting the actual winner
    by_field = {p.field: p.source for p in prov}
    assert by_field == {"title": "first", "phone": "first", "linkedin_url": "second"}


async def test_lower_precedence_fills_gaps_only():
    merged, prov = await chain_enrich(
        [Fake("a", {"title": "T"}), Fake("b", {"title": "X", "seniority": "vp"})], INP
    )
    assert merged["title"] == "T" and merged["seniority"] == "vp"
    assert len(prov) == 2


async def test_none_result_is_skipped():
    merged, prov = await chain_enrich([Fake("empty", {}), Fake("b", {"title": "T"})], INP)
    assert merged == {"title": "T"}
    assert [p.source for p in prov] == ["b"]


async def test_empty_chain_is_a_noop():
    assert await chain_enrich([], INP) == ({}, [])


async def test_provenance_carries_confidence():
    merged, prov = await chain_enrich([Fake("a", {"title": "T"}, confidence=0.77)], INP)
    assert prov[0].confidence == 0.77 and prov[0].value == "T"


# ---------------------------------------------------------------- chain factory


def test_factory_respects_order_and_keys():
    s = Settings(
        enrichment_providers="hunter,apollo",
        apollo_api_key="ak",
        hunter_api_key="hk",
        _env_file=None,
    )
    chain = build_enrichment_chain(s)
    assert [type(p) for p in chain] == [HunterProvider, ApolloProvider]


def test_factory_skips_keyless_and_unknown():
    s = Settings(
        enrichment_providers="apollo,hunter,null,clearbit",
        apollo_api_key="ak",
        hunter_api_key="",  # listed but keyless → skipped
        _env_file=None,
    )
    chain = build_enrichment_chain(s)
    assert [type(p) for p in chain] == [ApolloProvider, NullProvider]


def test_factory_empty_config_disables_enrichment():
    s = Settings(enrichment_providers="", apollo_api_key="ak", _env_file=None)
    assert build_enrichment_chain(s) == []
