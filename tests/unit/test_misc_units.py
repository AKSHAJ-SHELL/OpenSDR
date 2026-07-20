import asyncio

from craftsman.compliance.suppression import gdpr_blocked
from craftsman.ingest.verify import domain_of, syntax_ok
from craftsman.scoring.embeddings import HashEmbedder
from craftsman.scoring.icp import cosine_sim, icp_score, rule_score
from craftsman.sender.warmup import effective_daily_limit


def test_warmup_caps_ramp():
    assert effective_daily_limit(40, 0) == 10
    assert effective_daily_limit(40, 1) == 20
    assert effective_daily_limit(40, 2) == 30
    assert effective_daily_limit(40, 3) == 40
    assert effective_daily_limit(100, 4) == 100
    assert effective_daily_limit(15, 2) == 15  # never exceeds configured limit


def test_email_syntax():
    assert syntax_ok("jane@acme.com")
    assert not syntax_ok("jane@")
    assert not syntax_ok("not-an-email")
    assert domain_of("Jane@Acme.COM") == "acme.com"


def test_gdpr_blocking():
    assert gdpr_blocked("hans@firma.de", gdpr_mode=True)
    assert not gdpr_blocked("hans@firma.de", gdpr_mode=True, list_is_opt_in=True)
    assert not gdpr_blocked("hans@firma.de", gdpr_mode=False)
    assert not gdpr_blocked("bob@acme.com", gdpr_mode=True)


def test_rule_score_seniority():
    assert rule_score("VP of Operations") >= 0.9
    assert rule_score("Founder & CEO") == 1.0
    assert rule_score("Software Engineer") == 0.0
    assert rule_score(None) == 0.3


def test_hash_embedder_similarity_is_sane():
    emb = HashEmbedder()
    vecs = asyncio.run(
        emb.embed(
            [
                "VP of Warehouse Operations at a logistics company",
                "Head of Warehouse Operations at a 3PL logistics firm",
                "Pastry chef at a bakery",
            ]
        )
    )
    sim_close = cosine_sim(vecs[0], vecs[1])
    sim_far = cosine_sim(vecs[0], vecs[2])
    assert sim_close > sim_far
    assert len(vecs[0]) == 1024


def test_icp_score_bounds():
    emb = HashEmbedder()
    v = asyncio.run(emb.embed(["warehouse ops leader", "warehouse operations leader"]))
    s = icp_score(v[0], v[1], "VP Operations")
    assert 0.0 <= s <= 1.0
