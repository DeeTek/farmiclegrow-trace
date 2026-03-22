"""
apps/analytics/tasks.py  —  FarmicleGrow-Trace Platform

Celery beat tasks for analytics data refresh.

refresh_platform_snapshot   — refreshes PlatformSnapshot + RegionalSummary rows
                               (runs every 15 minutes via beat schedule)

Add to Celery beat settings:
    from celery.schedules import crontab
    CELERY_BEAT_SCHEDULE = {
        "refresh-analytics": {
            "task":     "apps.analytics.tasks.refresh_platform_snapshot",
            "schedule": crontab(minute="*/15"),
        },
    }
"""
from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger("apps.analytics")


@shared_task(
    bind                  = True,
    max_retries           = 3,
    acks_late             = True,
    reject_on_worker_lost = True,
)
def refresh_platform_snapshot(self) -> None:
    """
    Refresh PlatformSnapshot singleton and rebuild RegionalSummary rows
    for the current month. Runs every 15 minutes via Celery beat.
    """
    try:
        from apps.analytics.models import PlatformSnapshot
        from apps.analytics.services import build_regional_summaries

        # Refresh global snapshot
        snapshot = PlatformSnapshot.get_or_create_singleton()
        snapshot.refresh()

        # Rebuild regional summaries for current month
        count = build_regional_summaries()

        logger.info(
            "refresh_platform_snapshot | done | regions=%s | refreshed_at=%s",
            count, snapshot.last_refreshed_at,
        )

    except Exception as exc:
        logger.exception("refresh_platform_snapshot | failed: %s", exc)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))