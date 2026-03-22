"""
apps/buyers/tasks.py

All Celery tasks for the buyers app.

Task groups:
  1. Order email notifications  — confirmed, dispatched, cancelled
  2. Payment email notification — payment confirmed
  3. Webhook processing         — offloaded from the webhook view
  4. In-app notifications       — BuyerNotification record creation

All tasks share the same reliability configuration:
  bind=True            — self available for retry()
  max_retries=3        — with exponential back-off (60s → 120s → 240s)
  acks_late=True       — ACK only after completion; safe re-delivery on crash
  reject_on_worker_lost — re-queue if worker process dies mid-task

Email rendering delegates to accounts.tasks.dispatch_email so all mail
goes through the same template engine and retry logic.

Template files expected at:
  templates/buyers/order_confirmed_subject.txt   / _message.txt / .html
  templates/buyers/order_dispatched_subject.txt  / _message.txt / .html
  templates/buyers/order_cancelled_subject.txt   / _message.txt / .html
  templates/buyers/payment_confirmed_subject.txt / _message.txt / .html
"""
from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger("apps.buyers")

_RETRY_KWARGS = dict(max_retries=3, acks_late=True, reject_on_worker_lost=True)


# =============================================================================
# 1. ORDER EMAIL TASKS
# =============================================================================

@shared_task(bind=True, **_RETRY_KWARGS)
def send_order_confirmed_email(
    self, *, order_pk: str, buyer_email: str, buyer_name: str,
) -> None:
    from apps.accounts.tasks import dispatch_email

    try:
        dispatch_email(
            template_prefix = "buyers/order_confirmed",
            to_email        = buyer_email,
            context         = {"buyer_name": buyer_name, "order_pk": order_pk},
        )
        logger.info("order_confirmed_email_sent | order_pk=%s", order_pk)
    except Exception as exc:
        logger.exception("send_order_confirmed_email_failed | order_pk=%s", order_pk)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@shared_task(bind=True, **_RETRY_KWARGS)
def send_order_dispatched_email(
    self, *,
    order_pk: str,
    buyer_email: str,
    buyer_name: str,
    tracking_number: str,
    carrier: str,
) -> None:
    from apps.accounts.tasks import dispatch_email

    try:
        dispatch_email(
            template_prefix = "buyers/order_dispatched",
            to_email        = buyer_email,
            context         = {
                "buyer_name":      buyer_name,
                "order_pk":        order_pk,
                "tracking_number": tracking_number,
                "carrier":         carrier,
            },
        )
        logger.info("order_dispatched_email_sent | order_pk=%s", order_pk)
    except Exception as exc:
        logger.exception("send_order_dispatched_email_failed | order_pk=%s", order_pk)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@shared_task(bind=True, **_RETRY_KWARGS)
def send_order_cancelled_email(
    self, *,
    order_pk: str,
    buyer_email: str,
    buyer_name: str,
    reason: str,
) -> None:
    from apps.accounts.tasks import dispatch_email

    try:
        dispatch_email(
            template_prefix = "buyers/order_cancelled",
            to_email        = buyer_email,
            context         = {
                "buyer_name": buyer_name,
                "order_pk":   order_pk,
                "reason":     reason,
            },
        )
        logger.info("order_cancelled_email_sent | order_pk=%s", order_pk)
    except Exception as exc:
        logger.exception("send_order_cancelled_email_failed | order_pk=%s", order_pk)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


# =============================================================================
# 2. PAYMENT EMAIL TASK
# =============================================================================

@shared_task(bind=True, **_RETRY_KWARGS)
def send_payment_confirmed_email(
    self, *,
    payment_pk: str,
    buyer_email: str,
    buyer_name: str,
    amount: str,
    currency: str,
) -> None:
    from apps.accounts.tasks import dispatch_email

    try:
        dispatch_email(
            template_prefix = "buyers/payment_confirmed",
            to_email        = buyer_email,
            context         = {
                "buyer_name": buyer_name,
                "payment_pk": payment_pk,
                "amount":     amount,
                "currency":   currency,
            },
        )
        logger.info("payment_confirmed_email_sent | payment_pk=%s", payment_pk)
    except Exception as exc:
        logger.exception("send_payment_confirmed_email_failed | payment_pk=%s", payment_pk)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


# =============================================================================
# 3. WEBHOOK PROCESSING TASK
# =============================================================================

@shared_task(bind=True, **_RETRY_KWARGS)
def process_payment_webhook(self, *, log_pk: str) -> None:
    """
    Process a stored PaymentWebhookLog asynchronously.
    Called by the webhook view immediately after signature validation.
    Keeps the HTTP response time under ~50ms regardless of event complexity.
    """
    from apps.buyers.services import process_webhook_event

    try:
        process_webhook_event(log_pk)
    except Exception as exc:
        logger.exception("process_payment_webhook_failed | log_pk=%s", log_pk)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


# =============================================================================
# 4. IN-APP NOTIFICATION TASK
# =============================================================================

@shared_task(bind=True, **_RETRY_KWARGS)
def create_buyer_notification(
    self, *,
    buyer_pk: str,
    notification_type: str,
    title: str,
    body: str,
    reference_id: str = "",
) -> None:
    """
    Create a BuyerNotification record for the in-app notification centre.
    Dispatched from service layer via transaction.on_commit so notifications
    are never created for rolled-back transactions.
    """
    from apps.buyers.models import Buyer, BuyerNotification

    try:
        buyer = Buyer.objects.get(pk=buyer_pk)
        BuyerNotification.objects.create(
            buyer             = buyer,
            notification_type = notification_type,
            title             = title,
            body              = body,
            reference_id      = reference_id,
            created_at        = timezone.now(),
        )
        logger.info(
            "buyer_notification_created | buyer_pk=%s | type=%s",
            buyer_pk, notification_type,
        )
    except Buyer.DoesNotExist:
        logger.error("create_buyer_notification | buyer_not_found | pk=%s", buyer_pk)
    except Exception as exc:
        logger.exception("create_buyer_notification_failed | buyer_pk=%s", buyer_pk)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))