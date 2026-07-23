"""ICP scoring.

- No signals (company has none): 2-way blend  `w_cos·cosine + w_rule·rule`  (0.7/0.3).
- With signals (M2.3): 3-way blend  `w_cos_s·cosine + w_rule_s·rule + w_sig·signal_boost`
  (0.6/0.25/0.15).

Choosing the blend by *whether signals exist* (a `signal_boost` of `None` selects the
2-way path) means an operator with no signals sees byte-identical scores to before — the
⛔ Gate M2 'renormalized' decision. Weights are config knobs (changing a default is a ⛔
human decision per TESTING.md §0). Leads under the threshold are disqualified and never
touch the LLM — cost and spam control in one.
"""

import math
from dataclasses import dataclass

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


@dataclass(frozen=True)
class ScoreBreakdown:
    """The parts that produced a score, so the dashboard can explain it without
    re-deriving anything. Persisted alongside the score (M1.3; `signal` added M2.3).
    `signal` is None when the company had no signals (2-way blend was used)."""

    score: float
    cosine: float  # sim01, already mapped to [0,1]
    rule: float
    matched_keyword: str | None
    signal: float | None = None


def matched_seniority_keyword(title: str | None) -> str | None:
    """The keyword that set rule_score, for the score explanation popover."""
    if not title:
        return None
    t = title.lower()
    best, best_kw = 0.0, None
    for kw, score in SENIORITY_KEYWORDS.items():
        if kw in t and score > best:
            best, best_kw = score, kw
    return best_kw


def score_breakdown(
    lead_embedding: list[float],
    icp_embedding: list[float],
    title: str | None,
    signal_boost: float | None = None,
) -> ScoreBreakdown:
    """`signal_boost=None` ⇒ the company had no signals ⇒ 2-way blend (unchanged from
    pre-M2.3). Any value (even 0.0) ⇒ the company had signals ⇒ 3-way blend."""
    from craftsman.core.config import get_settings

    s = get_settings()
    sim = cosine_sim(lead_embedding, icp_embedding)
    sim01 = (sim + 1.0) / 2.0  # map [-1,1] → [0,1]
    rule = rule_score(title)

    if signal_boost is None:
        score = s.icp_cosine_weight * sim01 + s.icp_rule_weight * rule
        signal_component = None
    else:
        boost = max(0.0, min(1.0, signal_boost))
        score = (
            s.icp_signal_cosine_weight * sim01
            + s.icp_signal_rule_weight * rule
            + s.icp_signal_weight * boost
        )
        signal_component = round(boost, 4)

    return ScoreBreakdown(
        score=round(score, 4),
        cosine=round(sim01, 4),
        rule=round(rule, 4),
        matched_keyword=matched_seniority_keyword(title),
        signal=signal_component,
    )


def icp_score(
    lead_embedding: list[float],
    icp_embedding: list[float],
    title: str | None,
    signal_boost: float | None = None,
) -> float:
    return score_breakdown(lead_embedding, icp_embedding, title, signal_boost).score
