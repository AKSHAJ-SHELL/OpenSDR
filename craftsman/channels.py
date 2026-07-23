"""Channel registry — the single source of truth for what a sequence step's
`channel` means (M3.1).

This is the fork-friendly seam: adding a channel means adding an entry here
(name, assisted?, slot vocabulary) plus a generator in the worker. Nothing else
hardcodes channel names.

Design constraint (ROADMAP M3, binding): email is the only autonomous channel.
Assisted channels generate *validated* content and queue it as a human task —
Craftsman never performs the outreach itself. No browser automation, no
session-cookie handling, ever.
"""

from dataclasses import dataclass, field

EMAIL = "email"
LINKEDIN_TASK = "linkedin_task"
CALL_TASK = "call_task"


@dataclass(frozen=True)
class ChannelSpec:
    name: str
    assisted: bool  # True = generates a human task; False = autonomous send
    uses_skeleton: bool  # True = variants (skeleton + slots) required on the step
    llm_slots: frozenset[str] = field(default_factory=frozenset)
    static_slots: frozenset[str] = field(default_factory=frozenset)
    # outcomes a human may record on completion; first entry is the default
    outcomes: tuple[str, ...] = ()


def _email_spec() -> ChannelSpec:
    # import here: fill.py imports are cheap but keep the registry import-light
    from craftsman.copywriter.fill import LLM_SLOTS, STATIC_SLOTS

    return ChannelSpec(
        name=EMAIL,
        assisted=False,
        uses_skeleton=True,
        llm_slots=LLM_SLOTS,
        static_slots=STATIC_SLOTS,
        outcomes=(),
    )


_LINKEDIN_SPEC = ChannelSpec(
    name=LINKEDIN_TASK,
    assisted=True,
    uses_skeleton=True,
    # matches LinkedInSlotFill (core/schemas.py); no signature — LinkedIn shows
    # the sender's real profile, faking a signature would be dishonest noise
    llm_slots=frozenset({"personalization_hook", "value_bridge", "cta_question"}),
    static_slots=frozenset({"first_name"}),
    outcomes=("sent",),
)

_CALL_SPEC = ChannelSpec(
    name=CALL_TASK,
    assisted=True,
    # a call gets a structured brief (CallBrief), not a skeleton script — there is
    # deliberately no template for fake rapport
    uses_skeleton=False,
    llm_slots=frozenset(),
    static_slots=frozenset(),
    outcomes=("connected", "voicemail", "no_answer"),
)


def get_channel(name: str) -> ChannelSpec:
    if name == EMAIL:
        return _email_spec()
    if name == LINKEDIN_TASK:
        return _LINKEDIN_SPEC
    if name == CALL_TASK:
        return _CALL_SPEC
    raise KeyError(f"unknown channel: {name!r}")


ALL_CHANNELS: tuple[str, ...] = (EMAIL, LINKEDIN_TASK, CALL_TASK)
ASSISTED_CHANNELS: frozenset[str] = frozenset({LINKEDIN_TASK, CALL_TASK})


def is_assisted(name: str) -> bool:
    return name in ASSISTED_CHANNELS
