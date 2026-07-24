"""Prometheus metrics for Craftsman.

Pull-based: the collector queries Postgres + Redis at scrape time. This is the honest
design for separate api/worker processes — in-process counters live in the worker that
did the send and would be invisible to the API that serves /metrics. The only event not
otherwise persisted (a send rejection) is counted in Redis by the worker and read here.
"""

import logging

import redis
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

from craftsman.core.config import get_settings

log = logging.getLogger(__name__)

REGISTRY = CollectorRegistry()  # dedicated — /metrics exposes only Craftsman metrics
_QUEUES = ("enrich", "research", "send", "inbox", "settle")


def _redis() -> redis.Redis:
    return redis.Redis.from_url(get_settings().redis_url, decode_responses=True)


def record_rejection(reason: str) -> None:
    """Count a send rejection by reason. Best-effort — never block a send on metrics."""
    try:
        _redis().incr(f"metrics:rejections:{reason}")
    except Exception:  # noqa: BLE001 - metrics must never raise into the send path
        pass


class CraftsmanCollector:
    """Yields fresh gauges/counters on every scrape from the shared stores."""

    def collect(self):
        yield from self._db_metrics()
        yield from self._redis_metrics()

    def _db_metrics(self):
        from sqlalchemy import func, select

        from craftsman.core.db import session_scope
        from craftsman.core.models import Enrollment, Lead, Message, ReviewQueueItem
        from craftsman.core.tenancy import unscoped_context

        try:
            # /metrics is infrastructure-wide by design (Prometheus scrapes the
            # install, not a tenant): counts aggregate across orgs and carry no
            # row contents. Justified unscoped read (M5.1).
            with unscoped_context(), session_scope() as db:
                enrollments = GaugeMetricFamily(
                    "craftsman_enrollments", "Enrollments by state", labels=["state"]
                )
                for state, n in db.execute(
                    select(Enrollment.state, func.count()).group_by(Enrollment.state)
                ).all():
                    enrollments.add_metric([state], n)
                yield enrollments

                leads = GaugeMetricFamily(
                    "craftsman_leads", "Leads by status", labels=["status"]
                )
                for status, n in db.execute(
                    select(Lead.status, func.count()).group_by(Lead.status)
                ).all():
                    leads.add_metric([status], n)
                yield leads

                replies = GaugeMetricFamily(
                    "craftsman_replies", "Inbound replies by classification",
                    labels=["classification"],
                )
                for label, n in db.execute(
                    select(Message.classification, func.count())
                    .where(Message.direction == "inbound", Message.classification.isnot(None))
                    .group_by(Message.classification)
                ).all():
                    replies.add_metric([label], n)
                yield replies

                sent = db.scalar(
                    select(func.count(Message.id)).where(Message.direction == "outbound")
                ) or 0
                outbound = GaugeMetricFamily(
                    "craftsman_outbound_total", "Total outbound messages sent"
                )
                outbound.add_metric([], sent)
                yield outbound

                review = GaugeMetricFamily(
                    "craftsman_review_queue", "Unresolved review items by kind",
                    labels=["kind"],
                )
                for kind, n in db.execute(
                    select(ReviewQueueItem.kind, func.count())
                    .where(ReviewQueueItem.resolved.is_(False))
                    .group_by(ReviewQueueItem.kind)
                ).all():
                    review.add_metric([kind], n)
                yield review

                # dead_letters lands in Phase 3; tolerate its absence until then
                try:
                    from craftsman.core.models import DeadLetter

                    dl = db.scalar(select(func.count(DeadLetter.id))) or 0
                    dead = GaugeMetricFamily("craftsman_dead_letters", "Dead-letter records")
                    dead.add_metric([], dl)
                    yield dead
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001 - a scrape must not 500 on a DB blip
            log.warning("metrics: DB collect failed: %s", e)

    def _redis_metrics(self):
        try:
            r = _redis()
            depth = GaugeMetricFamily(
                "craftsman_queue_depth", "Pending tasks per Celery queue", labels=["queue"]
            )
            for q in _QUEUES:
                try:
                    depth.add_metric([q], r.llen(q))
                except Exception:  # noqa: BLE001
                    pass
            yield depth

            rejections = CounterMetricFamily(
                "craftsman_send_rejections", "Send rejections by reason", labels=["reason"]
            )
            for key in r.scan_iter("metrics:rejections:*"):
                try:
                    rejections.add_metric([key.rsplit(":", 1)[-1]], float(r.get(key) or 0))
                except Exception:  # noqa: BLE001
                    pass
            yield rejections
        except Exception as e:  # noqa: BLE001
            log.warning("metrics: Redis collect failed: %s", e)


_collector: CraftsmanCollector | None = None


def register_metrics() -> None:
    """Register the collector once. Idempotent."""
    global _collector
    if _collector is None:
        _collector = CraftsmanCollector()
        REGISTRY.register(_collector)


def metrics_payload() -> tuple[bytes, str]:
    register_metrics()
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
