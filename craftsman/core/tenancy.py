"""Central multi-tenancy enforcement (M5.1) — the third structural guarantee.

The first two design commitments gate what the LLM may say. This module gates
what a query may see: **no ORM result ever crosses an org boundary**, enforced
at the session layer — not per-router — so a forgotten filter in any endpoint,
worker, or future fork contribution fails closed instead of leaking.

Mechanics:

- ``current org`` is a ``contextvars.ContextVar``. The API's auth dependency
  sets it from the authenticated key's org for the request; workers enter it
  per task from the row they load; beat sweeps iterate orgs explicitly.
- A class-level ``do_orm_execute`` listener injects ``with_loader_criteria``
  (``org_id == current``) into every SELECT that touches an :class:`OrgScoped`
  entity — including relationship/lazy loads — and appends the same predicate
  to ORM UPDATE/DELETE statements.
- **Fail closed:** touching an org-scoped entity with no org context raises
  :class:`TenancyError` instead of returning cross-tenant rows.
- A ``before_flush`` listener stamps ``org_id`` on new org-scoped objects from
  the context, and refuses writes that would create, mutate, or delete another
  org's row (or move a row between orgs).

The one escape hatch is :func:`unscoped_context`. It exists because a few
system paths must run before or across org boundaries, and it is deliberately
loud and grep-able — every use site must justify itself:

- API-key lookup (authentication is how we *discover* the org),
- unsubscribe-token / signed-webhook bootstrap (the token or signature is the
  credential; the row it resolves to supplies the org, which is entered
  immediately after),
- beat sweeps enumerating orgs, and ``/metrics`` (infra-wide by design),
- OIDC subject lookup (the issuer/subject pair is the credential).

Everything inside ``unscoped_context`` can read across orgs, so code there
must never echo row contents to a caller — resolve, enter ``org_context``,
and get out.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import event
from sqlalchemy.orm import Session, with_loader_criteria

# The org every pre-M5 row is backfilled into (migration 0016) and the org
# single-tenant self-hosters keep using without ever thinking about tenancy.
DEFAULT_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

_UNSCOPED = object()  # sentinel: deliberate system-wide access

_org: ContextVar[object] = ContextVar("craftsman_org", default=None)


class TenancyError(RuntimeError):
    """An org-scoped operation ran without an org context, or tried to touch
    another org's row. This is a programming error — never catch it to proceed."""


def current_org_id() -> uuid.UUID | None:
    """The active org, or None when no context is set. Returns None (not the
    sentinel) under ``unscoped_context`` — callers who need to distinguish
    should use :func:`is_unscoped`."""
    val = _org.get()
    return val if isinstance(val, uuid.UUID) else None


def is_unscoped() -> bool:
    return _org.get() is _UNSCOPED


def require_org_id() -> uuid.UUID:
    val = _org.get()
    if not isinstance(val, uuid.UUID):
        raise TenancyError(
            "org-scoped operation with no org context — enter org_context(...) "
            "(or, for a justified system path, unscoped_context())"
        )
    return val


@contextmanager
def org_context(org_id: uuid.UUID) -> Iterator[None]:
    """Scope all ORM access inside the block to one org."""
    if not isinstance(org_id, uuid.UUID):
        org_id = uuid.UUID(str(org_id))
    token = _org.set(org_id)
    try:
        yield
    finally:
        _org.reset(token)


@contextmanager
def no_org_context() -> Iterator[None]:
    """Force 'no org context set' — exists so tests can PROVE the guard fails
    closed. Production code has no reason to use this."""
    token = _org.set(None)
    try:
        yield
    finally:
        _org.reset(token)


@contextmanager
def unscoped_context() -> Iterator[None]:
    """Deliberate system-wide access. See the module docstring for the complete
    list of legitimate uses; adding a new one is a design decision, not a fix."""
    token = _org.set(_UNSCOPED)
    try:
        yield
    finally:
        _org.reset(token)


def set_request_org(org_id: uuid.UUID):
    """Set the org for the remainder of the current context (no reset) — used by
    the API auth dependency, where the request task's context dies with the
    request. Returns the token for callers that do want to reset."""
    if not isinstance(org_id, uuid.UUID):
        org_id = uuid.UUID(str(org_id))
    return _org.set(org_id)


# ─── session-level enforcement ──────────────────────────────────────────────


def _org_scoped_class():
    # local import: models imports nothing from here at module import time,
    # so this direction is the cycle-free one
    from craftsman.core.models import OrgScoped

    return OrgScoped


def _statement_entities(execute_state) -> list[type]:
    """Mapped classes a statement touches (best effort, fail-closed callers)."""
    entities: list[type] = []
    stmt = execute_state.statement
    for desc in getattr(stmt, "column_descriptions", []) or []:
        ent = desc.get("entity")
        if isinstance(ent, type):
            entities.append(ent)
    # ORM update/delete: the target is on the statement, not column_descriptions
    ent = getattr(stmt, "entity_description", None)
    if ent is not None and isinstance(ent.get("entity"), type):
        entities.append(ent["entity"])
    return entities


@event.listens_for(Session, "do_orm_execute")
def _tenancy_filter(execute_state) -> None:
    val = _org.get()
    if val is _UNSCOPED:
        return
    OrgScoped = _org_scoped_class()

    touches_scoped = any(
        isinstance(ent, type) and issubclass(ent, OrgScoped)
        for ent in _statement_entities(execute_state)
    )

    if execute_state.is_select:
        # relationship / lazy loads carry their target via bind_mapper
        if not touches_scoped and execute_state.bind_mapper is not None:
            touches_scoped = issubclass(execute_state.bind_mapper.class_, OrgScoped)
        if not touches_scoped:
            return
        if not isinstance(val, uuid.UUID):
            raise TenancyError(
                "org-scoped SELECT with no org context (fail-closed) — "
                "enter org_context(...) or a justified unscoped_context()"
            )
        execute_state.statement = execute_state.statement.options(
            with_loader_criteria(
                OrgScoped,
                lambda cls: cls.org_id == val,
                include_aliases=True,
                track_closure_variables=False,
            )
        )
    elif execute_state.is_update or execute_state.is_delete:
        for ent in _statement_entities(execute_state):
            if issubclass(ent, OrgScoped):
                if not isinstance(val, uuid.UUID):
                    raise TenancyError(
                        "org-scoped UPDATE/DELETE with no org context (fail-closed)"
                    )
                execute_state.statement = execute_state.statement.where(
                    ent.org_id == val
                )


def register_init_stamp() -> None:
    """Stamp org_id at object CONSTRUCTION, not at flush.

    Construction always happens inside the caller's org context; a flush can be
    triggered later from anywhere — including query-invoked autoflush inside an
    unscoped_context block (e.g. the auth key lookup flushing objects a router
    just created), where flush-time stamping would silently skip. The
    before_flush listener below remains as the verifier and backstop.

    Registered from models.py after OrgScoped exists (propagate=True covers all
    subclasses). ORM row loading bypasses __init__, so this fires only for
    user-constructed objects.
    """
    from craftsman.core.models import OrgScoped

    @event.listens_for(OrgScoped, "init", propagate=True)
    def _stamp_on_init(target, args, kwargs) -> None:
        if kwargs.get("org_id") is None:
            val = _org.get()
            if isinstance(val, uuid.UUID):
                kwargs["org_id"] = val


@event.listens_for(Session, "before_flush")
def _tenancy_stamp(session: Session, flush_context, instances) -> None:
    val = _org.get()
    if val is _UNSCOPED:
        return
    OrgScoped = _org_scoped_class()

    for obj in session.new:
        if isinstance(obj, OrgScoped):
            if obj.org_id is None:
                if not isinstance(val, uuid.UUID):
                    raise TenancyError(
                        f"new {type(obj).__name__} with no org context to stamp"
                    )
                obj.org_id = val
            elif isinstance(val, uuid.UUID) and obj.org_id != val:
                raise TenancyError(
                    f"new {type(obj).__name__} carries org_id {obj.org_id}, "
                    f"but the active org is {val}"
                )
    for obj in session.dirty:
        if isinstance(obj, OrgScoped):
            from sqlalchemy import inspect as sa_inspect

            history = sa_inspect(obj).attrs.org_id.history
            if history.has_changes():
                raise TenancyError(
                    f"{type(obj).__name__}.org_id may never change (no org moves)"
                )
            if isinstance(val, uuid.UUID) and obj.org_id not in (None, val):
                raise TenancyError(
                    f"update to {type(obj).__name__} owned by org {obj.org_id} "
                    f"under active org {val}"
                )
    for obj in session.deleted:
        if isinstance(obj, OrgScoped):
            if isinstance(val, uuid.UUID) and obj.org_id not in (None, val):
                raise TenancyError(
                    f"delete of {type(obj).__name__} owned by org {obj.org_id} "
                    f"under active org {val}"
                )
