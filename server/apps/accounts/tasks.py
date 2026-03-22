"""
apps/accounts/tasks.py

Base email Celery task and shared dispatcher.

Each app (staff, farmers) imports dispatch_email from here to send its
own emails. The core send_email_task lives here because the email
infrastructure (SMTP config, template rendering, retry logic) is the
same regardless of which app triggered the send.

Template location convention:
  templates/<app_label>/<prefix>_subject.txt    (required)
  templates/<app_label>/<prefix>_message.txt    (required)
  templates/<app_label>/<prefix>.html           (optional)

  e.g. templates/staff/field_officer_approved_subject.txt
       templates/farmers/farmer_onboarded_subject.txt
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string, TemplateDoesNotExist
from django.conf import settings

logger = logging.getLogger(__name__)


@shared_task(
    bind                  = True,
    max_retries           = 3,
    acks_late             = True,
    reject_on_worker_lost = True,
)
def send_email_task(self, *, template_prefix: str, to_email: str, context: dict) -> None:
    """
    Render and send a transactional email.

    Args:
        template_prefix:  Full template path prefix, e.g. "staff/field_officer_approved"
                          Looks for:
                            <prefix>_subject.txt   (required)
                            <prefix>_message.txt   (required)
                            <prefix>.html          (optional)
        to_email:         Recipient address.
        context:          Template context — must be JSON-serialisable.
                          Pass plain dicts / strings, never model instances.

    Retries with exponential back-off: 60s → 120s → 240s.
    Missing templates are logged as errors and NOT retried.
    """
    context.setdefault("frontend_url", getattr(settings, "FRONTEND_BASE_URL", ""))

    try:
        subject   = render_to_string(f"{template_prefix}_subject.txt", context).strip()
        text_body = render_to_string(f"{template_prefix}_message.txt", context)
    except TemplateDoesNotExist:
        logger.error("email_template_missing | prefix=%s", template_prefix)
        return  # don't retry — a missing template won't fix itself

    msg = EmailMultiAlternatives(
        subject    = subject,
        body       = text_body,
        from_email = settings.DEFAULT_FROM_EMAIL,
        to         = [to_email],
    )

    try:
        html_body = render_to_string(f"{template_prefix}.html", context)
        msg.attach_alternative(html_body, "text/html")
    except TemplateDoesNotExist:
        pass  # HTML is optional — plaintext always sent

    try:
        msg.send()
        logger.info("email_sent | template=%s", template_prefix)
    except Exception as exc:
        logger.exception(
            "email_send_failed | template=%s | attempt=%s",
            template_prefix, self.request.retries + 1,
        )
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


def dispatch_email(*, template_prefix: str, to_email: str, context: dict) -> None:
    """
    Schedule send_email_task to fire after the current DB transaction commits.

    on_commit guarantees email is never sent for a transaction that rolls back.
    Outside a transaction (management commands, shell), on_commit fires
    immediately — giving synchronous-equivalent behaviour.

    Imported by apps/staff/tasks.py and apps/farmers/tasks.py, which wrap
    it with app-specific template prefixes.
    """
    from django.db import transaction

    transaction.on_commit(
        lambda: send_email_task.delay(
            template_prefix = template_prefix,
            to_email        = to_email,
            context         = context,
        )
    )