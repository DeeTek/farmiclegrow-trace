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
def send_email_task(self, *, template_prefix: str, to_email: str, url: str, context: dict) -> None:
   
    context["confirmation_url"] = url

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
        logger.error("email_html_template_missing | prefix=%s | falling back to text only", template_prefix,)

    try:
        msg.send()
        logger.info("email_sent | template=%s | has_html=%s", template_prefix, msg.alternatives != [],)
    except Exception as exc:
        logger.exception(
            "email_send_failed | template=%s | attempt=%s",
            template_prefix, self.request.retries + 1,
        )
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


def dispatch_email(*, template_prefix: str, to_email: str, url: str, context: dict) -> None:

    from django.db import transaction

    transaction.on_commit(
        lambda: send_email_task.delay(
            template_prefix = template_prefix,
            to_email = to_email,
            url = url,
            context = context,
        )
    )