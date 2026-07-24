"""Escalation rules engine (M4.2, G9): when a human is pulled in — as data, not code.

Pure evaluation: rules in, decision out. No I/O here — the pipeline executes the
decision (Slack, suppression, review queue); M4.4's Autopilot consumes
`block_autopilot`. Two design choices worth stating:

- **Union, not first-match.** Every enabled rule is evaluated and the decision is the
  union of all matching rules' actions. First-match-by-priority would let a benign
  notify rule accidentally shadow the legal-threat suppression — a footgun in a
  safety-critical path. Priority orders evaluation/reporting only.
- **Built-in defaults are always active.** They ship matching pre-M4.2 behavior
  exactly (confident interested → Slack ping) plus the legal/GDPR ruleset promoted
  from TESTING.md §3.4 fixtures. DB rules add to them; they cannot remove them — a
  fork that wants weaker safety must edit source, visibly.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Rule:
    """One escalation rule. `match` conditions are AND-ed; None = wildcard.
    Shape mirrors the escalation_rules table so DB rows load 1:1."""

    name: str
    classifications: tuple[str, ...] | None = None
    min_confidence: float | None = None
    max_confidence: float | None = None
    keywords_any: tuple[str, ...] | None = None
    notify: bool = False
    urgent_notify: bool = False
    suppress: bool = False
    review_queue: bool = False
    block_draft: bool = False
    block_autopilot: bool = False
    priority: int = 100
    enabled: bool = True


@dataclass
class EscalationDecision:
    matched_rules: list[str] = field(default_factory=list)
    notify: bool = False
    urgent_notify: bool = False
    suppress: bool = False
    review_queue: bool = False
    block_draft: bool = False
    block_autopilot: bool = False

    @property
    def any_action(self) -> bool:
        return bool(
            self.notify or self.urgent_notify or self.suppress
            or self.review_queue or self.block_draft or self.block_autopilot
        )


# Promoted from TESTING.md §3.4 fixtures: a legal threat or GDPR demand must
# suppress AND alert a human — never a draft, never "not interested", regardless
# of what the classifier said. Case-insensitive substring, fail-toward-escalation.
LEGAL_KEYWORDS: tuple[str, ...] = (
    "gdpr",
    "data protection",
    "right to erasure",
    "delete my data",
    "delete all my data",
    "data subject",
    "lawyer",
    "attorney",
    "legal action",
    "legal team",
    "lawsuit",
    "sue you",
    "cease and desist",
    "harassment",
    "harassing",
    "report you",
    "spam complaint",
    "can-spam",
    "ftc",
)


def default_rules(classifier_confidence_threshold: float) -> list[Rule]:
    """The built-in baseline. Rule 1 reproduces pre-M4.2 behavior byte-for-byte
    (only a CONFIDENT interested classification pings Slack). Rule 0 is the
    legal/GDPR tripwire — it matches on keywords alone, so it fires even when the
    classifier was unconfident or wrong."""
    return [
        Rule(
            name="builtin:legal-threat",
            keywords_any=LEGAL_KEYWORDS,
            urgent_notify=True,
            suppress=True,
            review_queue=True,
            block_draft=True,
            block_autopilot=True,
            priority=0,
        ),
        Rule(
            name="builtin:interested-notify",
            classifications=("interested",),
            min_confidence=classifier_confidence_threshold,
            notify=True,
            priority=10,
        ),
    ]


def _matches(rule: Rule, *, label: str, confidence: float, reply_text: str) -> bool:
    if rule.classifications is not None and label not in rule.classifications:
        return False
    if rule.min_confidence is not None and confidence < rule.min_confidence:
        return False
    if rule.max_confidence is not None and confidence > rule.max_confidence:
        return False
    if rule.keywords_any is not None:
        text = reply_text.lower()
        if not any(k.lower() in text for k in rule.keywords_any):
            return False
    return True


def evaluate(
    rules: list[Rule],
    *,
    label: str,
    confidence: float,
    reply_text: str,
) -> EscalationDecision:
    """Union of every enabled, matching rule's actions, in priority order."""
    decision = EscalationDecision()
    for rule in sorted(rules, key=lambda r: r.priority):
        if not rule.enabled:
            continue
        if not _matches(rule, label=label, confidence=confidence, reply_text=reply_text):
            continue
        decision.matched_rules.append(rule.name)
        decision.notify = decision.notify or rule.notify
        decision.urgent_notify = decision.urgent_notify or rule.urgent_notify
        decision.suppress = decision.suppress or rule.suppress
        decision.review_queue = decision.review_queue or rule.review_queue
        decision.block_draft = decision.block_draft or rule.block_draft
        decision.block_autopilot = decision.block_autopilot or rule.block_autopilot
    return decision


def rule_from_row(row) -> Rule:
    """EscalationRule ORM row → pure Rule. match/actions JSONB, None-tolerant."""
    match = row.match or {}
    actions = row.actions or {}
    classifications = match.get("classifications")
    keywords = match.get("keywords_any")
    return Rule(
        name=row.name,
        classifications=tuple(classifications) if classifications else None,
        min_confidence=match.get("min_confidence"),
        max_confidence=match.get("max_confidence"),
        keywords_any=tuple(keywords) if keywords else None,
        notify=bool(actions.get("notify")),
        urgent_notify=bool(actions.get("urgent_notify")),
        suppress=bool(actions.get("suppress")),
        review_queue=bool(actions.get("review_queue")),
        block_draft=bool(actions.get("block_draft")),
        block_autopilot=bool(actions.get("block_autopilot")),
        priority=row.priority,
        enabled=row.enabled,
    )


def load_rules(db, campaign_id, classifier_confidence_threshold: float) -> list[Rule]:
    """Effective ruleset: built-in defaults + global DB rules (campaign_id NULL) +
    this campaign's DB rules."""
    from sqlalchemy import or_, select

    from craftsman.core.models import EscalationRule

    rows = db.scalars(
        select(EscalationRule).where(
            or_(
                EscalationRule.campaign_id.is_(None),
                EscalationRule.campaign_id == campaign_id,
            )
        )
    ).all()
    return default_rules(classifier_confidence_threshold) + [rule_from_row(r) for r in rows]
