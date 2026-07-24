"""M4.2: escalation engine — pure rule evaluation."""

from craftsman.inbox.escalation import (
    LEGAL_KEYWORDS,
    Rule,
    default_rules,
    evaluate,
    rule_from_row,
)


def _eval(rules, label="interested", confidence=0.95, text="Sounds great!"):
    return evaluate(rules, label=label, confidence=confidence, reply_text=text)


# ---------------------------------------------------------------- matching


def test_classification_match():
    r = Rule(name="r", classifications=("objection",), notify=True)
    assert not _eval([r]).notify
    assert _eval([r], label="objection").notify


def test_confidence_bounds():
    r = Rule(name="r", min_confidence=0.7, max_confidence=0.9, review_queue=True)
    assert not _eval([r], confidence=0.5).review_queue
    assert _eval([r], confidence=0.8).review_queue
    assert not _eval([r], confidence=0.95).review_queue


def test_keywords_case_insensitive():
    r = Rule(name="r", keywords_any=("lawyer",), suppress=True)
    assert _eval([r], text="I will contact my LAWYER about this.").suppress
    assert not _eval([r], text="Sounds good.").suppress


def test_conditions_are_anded():
    r = Rule(
        name="r", classifications=("objection",), keywords_any=("pricing",), notify=True
    )
    assert not _eval([r], label="objection", text="too busy").notify
    assert not _eval([r], label="interested", text="what's the pricing?").notify
    assert _eval([r], label="objection", text="what's the pricing?").notify


def test_none_is_wildcard():
    r = Rule(name="r", urgent_notify=True)  # matches everything
    d = _eval([r], label="bounce_or_auto", confidence=0.1, text="")
    assert d.urgent_notify and d.matched_rules == ["r"]


def test_disabled_rule_never_matches():
    r = Rule(name="r", notify=True, enabled=False)
    assert not _eval([r]).any_action


# ---------------------------------------------------------------- union semantics


def test_actions_union_across_matches():
    rules = [
        Rule(name="a", classifications=("interested",), notify=True, priority=5),
        Rule(name="b", keywords_any=("lawyer",), suppress=True, block_draft=True, priority=1),
    ]
    d = _eval(rules, text="my lawyer says hi")
    assert d.matched_rules == ["b", "a"]  # priority order
    assert d.notify and d.suppress and d.block_draft


def test_no_shadowing_possible():
    # a rule with priority 0 cannot prevent the legal rule from acting — union
    rules = default_rules(0.7) + [
        Rule(name="benign", classifications=("unsubscribe",), notify=True, priority=0)
    ]
    d = _eval(rules, label="unsubscribe", text="remove me or my attorney gets involved")
    assert d.suppress and d.urgent_notify and d.block_draft and d.block_autopilot


# ---------------------------------------------------------------- defaults


def test_default_interested_parity():
    """Confident interested → notify only (the pre-M4.2 Slack ping, nothing else)."""
    d = _eval(default_rules(0.7), label="interested", confidence=0.9)
    assert d.notify
    assert not (d.urgent_notify or d.suppress or d.block_draft or d.block_autopilot)


def test_default_interested_requires_confidence():
    d = _eval(default_rules(0.7), label="interested", confidence=0.5)
    assert not d.any_action


def test_legal_threat_fires_regardless_of_label_and_confidence():
    for label in ("interested", "objection", "not_now", "unsubscribe", "bounce_or_auto"):
        d = _eval(
            default_rules(0.7), label=label, confidence=0.1,
            text="This is harassment. Delete my data per GDPR or I take legal action.",
        )
        assert d.suppress and d.urgent_notify and d.review_queue
        assert d.block_draft and d.block_autopilot, label


def test_legal_keywords_cover_testing_md_fixtures():
    # TESTING.md §3.4: legal threat / GDPR demand → suppress + alert, never a draft
    for phrase in ("GDPR", "cease and desist", "spam complaint", "delete my data"):
        assert any(k in phrase.lower() for k in LEGAL_KEYWORDS), phrase


# ---------------------------------------------------------------- row loading


class _Row:
    def __init__(self, **kw):
        self.name = kw.get("name", "row")
        self.match = kw.get("match", {})
        self.actions = kw.get("actions", {})
        self.priority = kw.get("priority", 100)
        self.enabled = kw.get("enabled", True)


def test_rule_from_row_roundtrip():
    r = rule_from_row(_Row(
        name="pricing-objections",
        match={"classifications": ["objection"], "keywords_any": ["pricing"], "min_confidence": 0.8},
        actions={"review_queue": True, "block_autopilot": True},
        priority=42,
    ))
    assert r.classifications == ("objection",)
    assert r.keywords_any == ("pricing",)
    assert r.min_confidence == 0.8
    assert r.review_queue and r.block_autopilot and not r.notify
    assert r.priority == 42


def test_rule_from_row_empty_jsonb_is_wildcard_noop():
    r = rule_from_row(_Row(match=None, actions=None))
    assert r.classifications is None and r.keywords_any is None
    d = _eval([r])
    assert d.matched_rules == ["row"] and not d.any_action
