from celery import Celery

from craftsman.core.config import get_settings

settings = get_settings()

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
        "craftsman.workers.tasks.generate_and_send": {"queue": "send"},
        "craftsman.workers.tasks.poll_inboxes": {"queue": "inbox"},
        "craftsman.workers.tasks.settle_bandit": {"queue": "settle"},
        "craftsman.workers.tasks.sequencer_tick": {"queue": "send"},
    },
    beat_schedule={
        "sequencer-tick": {"task": "craftsman.workers.tasks.sequencer_tick", "schedule": 60.0},
        "poll-inboxes": {"task": "craftsman.workers.tasks.poll_inboxes", "schedule": 120.0},
        "settle-bandit": {"task": "craftsman.workers.tasks.settle_bandit", "schedule": 3600.0},
        "reset-daily-counters": {
            "task": "craftsman.workers.tasks.reset_daily_counters",
            "schedule": 86400.0,
        },
    },
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    timezone="UTC",
)
