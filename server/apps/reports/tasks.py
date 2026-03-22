"""
apps/reports/tasks.py  —  FarmicleGrow-Trace Platform

Celery tasks for report generation and schedule processing.

generate_report_task   — generate a single report (queued by queue_report())
process_report_schedules — Celery beat task that fires due schedules
send_report_email_task — email the generated file to schedule recipients
"""
from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger("apps.reports")

_RETRY = dict(max_retries=3, acks_late=True, reject_on_worker_lost=True)


@shared_task(bind=True, **_RETRY)
def generate_report_task(self, report_pk: str) -> None:
    """
    Generate a single report identified by report_pk.

    Called by reports.services.queue_report() via transaction.on_commit.
    Delegates all logic to reports.services.generate_report().
    Retries up to 3 times on transient errors (DB blip, storage timeout).
    """
    from apps.reports.services import generate_report

    try:
        generate_report(report_pk)
    except Exception as exc:
        logger.exception(
            "generate_report_task_failed | pk=%s | attempt=%s",
            report_pk, self.request.retries + 1,
        )
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@shared_task
def process_report_schedules() -> None:
    """
    Celery beat task — runs every 15 minutes.
    Finds all enabled schedules whose next_run_at has passed and
    enqueues generate_report_task for each one.

    Add to Celery beat settings:
        CELERY_BEAT_SCHEDULE = {
            "process-report-schedules": {
                "task": "apps.reports.tasks.process_report_schedules",
                "schedule": crontab(minute="*/15"),
            },
        }
    """
    from django.utils import timezone
    from apps.reports.models import ReportSchedule
    from apps.reports.services import queue_report

    due = ReportSchedule.objects.filter(
        is_enabled=True,
        next_run_at__lte=timezone.now(),
    ).select_related("created_by")

    for schedule in due:
        try:
            queue_report(
                report_type   = schedule.report_type,
                title         = schedule.title,
                output_format = schedule.output_format,
                filters       = schedule.filters,
                requested_by  = schedule.created_by,
            )
            schedule.advance_next_run()

            if schedule.recipients:
                # Will be called once the report task completes — triggered
                # from generate_report() after status is set to "ready"
                logger.info(
                    "schedule_fired | schedule_pk=%s | type=%s",
                    schedule.pk, schedule.report_type,
                )
        except Exception as exc:
            logger.error(
                "schedule_fire_failed | schedule_pk=%s | error=%s",
                schedule.pk, exc,
            )


@shared_task(bind=True, **_RETRY)
def send_report_email_task(self, report_pk: str, recipients: list[str]) -> None:
    """
    Email the generated report file to the given recipients.
    Called by generate_report() after status=ready, when the report
    was triggered by a schedule with non-empty recipients.

    Uses accounts.tasks.send_email_task for the actual delivery.
    """
    from apps.reports.models import Report

    try:
        report = Report.objects.get(pk=report_pk, status="ready")
    except Report.DoesNotExist:
        logger.error("send_report_email | Report not found or not ready: pk=%s", report_pk)
        return

    if not report.file:
        logger.warning("send_report_email | No file attached: pk=%s", report_pk)
        return

    from apps.accounts.tasks import dispatch_email

    for email in recipients:
        dispatch_email(
            to       = email,
            subject  = f"FarmicleGrow Report: {report.title}",
            template = "reports/email/report_ready.html",
            context  = {
                "report_title":    report.title,
                "report_type":     report.report_type,
                "row_count":       report.row_count,
                "file_size":       report.file_size_display,
                "download_url":    report.file.url if report.file else "",
                "completed_at":    report.completed_at,
            },
        )

    logger.info(
        "report_email_sent | pk=%s | recipients=%s",
        report_pk, len(recipients),
    )