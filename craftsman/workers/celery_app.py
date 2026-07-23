import logging

from celery import Celery
from celery.signals import setup_logging, task_failure

from craftsman.core.config import get_settings

settings = get_settings()
log = logging.getLogger(__name__)


@setup_logging.connect
def _configure_worker_logging(**_kwargs):
    """Use our JSON logging instead of Celery's default (connecting to this signal
    tells Celery not to hijack the root logger)."""
    from craftsman.core.logging import configure_logging

    configure_logging()


@task_failure.connect
def _record_dead_letter(sender=None, task_id=None, exception=None, args=None,
                        kwargs=None, einfo=None, **_):
    """Record a terminally-failed task (retries raise Retry, not a failure, so this
    fires only after retries are exhausted). Best-effort — never mask the real error."""
    from craftsman.core.db import session_scope
    from craftsman.core.models import DeadLetter

    try:
        with session_scope() as db:
            db.add(DeadLetter(
                task_name=getattr(sender, "name", None) or "unknown",
                task_id=task_id,
                args=list(args) if args else None,
                kwargs=dict(kwargs) if kwargs else None,
                exception=repr(exception) if exception is not None else None,
                traceback=str(einfo) if einfo is not None else None,
                # our tasks take enrollment_id / lead_id as the first positional arg
                enrollment_id=str(args[0]) if args else None,
            ))
    except Exception as e:  # noqa: BLE001
        log.warning("failed to record dead letter for %s: %s", getattr(sender, "name", "?"), e)

app = Celery(
    "craftsman",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["craftsman.workers.tasks"],
)

app.conf.update(
    task_routes={
        "craftsman.workers.tasks.enrich_lead": {"queue": "enrich"},
        "craftsman.workers.tasks.research_enrollment": {"queue": "research"},
        "craftsman.workers.tasks.run_dry_run": {"queue": "research"},
        "craftsman.workers.tasks.generate_and_send": {"queue": "send"},
        "craftsman.workers.tasks.poll_inboxes": {"queue": "inbox"},
        "craftsman.workers.tasks.settle_bandit": {"queue": "settle"},
        "craftsman.workers.tasks.redrive_unsent": {"queue": "settle"},
        "craftsman.workers.tasks.sequencer_tick": {"queue": "send"},
    },
    beat_schedule={
        "sequencer-tick": {"task": "craftsman.workers.tasks.sequencer_tick", "schedule": 60.0},
        "poll-inboxes": {"task": "craftsman.workers.tasks.poll_inboxes", "schedule": 120.0},
        "settle-bandit": {"task": "craftsman.workers.tasks.settle_bandit", "schedule": 3600.0},
        "redrive-unsent": {"task": "craftsman.workers.tasks.redrive_unsent", "schedule": 600.0},
        "reset-daily-counters": {
            "task": "craftsman.workers.tasks.reset_daily_counters",
            "schedule": 86400.0,
        },
    },
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    timezone="UTC",
)
