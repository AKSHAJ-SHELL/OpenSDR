"""ICP scoring: 0.7 * cosine_sim(lead, icp) + 0.3 * rule_score.

Leads under the threshold are disqualified and never touch the LLM —
cost control and spam control in one.
"""

import math

SENIORITY_KEYWORDS = {
    "founder": 1.0, "ceo": 1.0, "cto": 1.0, "coo": 1.0, "cfo": 1.0, "cmo": 1.0,
    "chief": 1.0, "president": 1.0, "owner": 1.0, "partner": 0.9,
    "vp": 0.9, "vice president": 0.9, "head": 0.8, "director": 0.7,
    "principal": 0.6, "lead": 0.5, "senior": 0.4, "manager": 0.4,
}


def cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


def rule_score(title: str | None) -> float:
    """Hard-filter style rules; currently seniority keywords. 0..1."""
    if not title:
        return 0.3  # unknown title: neutral-low, don't hard-zero
    t = title.lower()
    best = 0.0
    for kw, score in SENIORITY_KEYWORDS.items():
        if kw in t:
            best = max(best, score)
    return best


def lead_text(title: str | None, company_name: str | None, company_description: str | None) -> str:
    return f"{title or 'Unknown title'} at {company_name or 'unknown company'}: {company_description or ''}"


def icp_score(lead_embedding: list[float], icp_embedding: list[float], title: str | None) -> float:
    sim = cosine_sim(lead_embedding, icp_embedding)
    sim01 = (sim + 1.0) / 2.0  # map [-1,1] → [0,1]
    return round(0.7 * sim01 + 0.3 * rule_score(title), 4)
