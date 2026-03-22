"""apps/buyers/signals.py"""
from django.db.models.signals import post_save
from django.dispatch import receiver
from apps.core.signals import (
    buyer_registered, buyer_verified, order_confirmed,
    order_dispatched, order_delivered, payment_completed, review_submitted,
)
import logging
logger = logging.getLogger("apps.buyers.signals")


@receiver(buyer_registered)
def on_buyer_registered(sender, **kwargs):
    instance = kwargs.get("instance")
    if instance:
        logger.info("Buyer registered: %s", getattr(instance, "code", ""))


@receiver(order_confirmed)
def on_order_confirmed(sender, **kwargs):
    instance = kwargs.get("instance")
    if not instance:
        return
    try:
        from .models import BuyerNotification
        BuyerNotification.objects.create(
            buyer              = instance.buyer,
            notification_type  = BuyerNotification.NotificationType.ORDER_CONFIRMED,
            title              = f"Order {instance.code} confirmed",
            message            = f"Your order {instance.code} has been confirmed and is being processed.",
            related_object_type= "order",
            related_object_id  = str(instance.pk),
        )
    except Exception as exc:
        logger.warning("Failed to create order notification: %s", exc)


@receiver(order_dispatched)
def on_order_dispatched(sender, **kwargs):
    instance = kwargs.get("instance")
    if not instance:
        return
    try:
        from .models import BuyerNotification
        BuyerNotification.objects.create(
            buyer              = instance.buyer,
            notification_type  = BuyerNotification.NotificationType.ORDER_DISPATCHED,
            title              = f"Order {instance.code} dispatched",
            message            = (
                f"Your order {instance.code} has been dispatched. "
                f"Tracking: {instance.tracking_number or 'N/A'}"
            ),
            related_object_type= "order",
            related_object_id  = str(instance.pk),
        )
    except Exception as exc:
        logger.warning("Failed to create dispatch notification: %s", exc)


@receiver(order_delivered)
def on_order_delivered(sender, **kwargs):
    instance = kwargs.get("instance")
    if not instance:
        return
    try:
        from .models import BuyerNotification
        BuyerNotification.objects.create(
            buyer              = instance.buyer,
            notification_type  = BuyerNotification.NotificationType.ORDER_DELIVERED,
            title              = f"Order {instance.code} delivered",
            message            = f"Your order {instance.code} has been delivered.",
            related_object_type= "order",
            related_object_id  = str(instance.pk),
        )
    except Exception as exc:
        logger.warning("Failed to create delivery notification: %s", exc)


@receiver(payment_completed)
def on_payment_completed(sender, **kwargs):
    instance = kwargs.get("instance")
    if not instance:
        return
    try:
        from .models import BuyerNotification
        BuyerNotification.objects.create(
            buyer              = instance.buyer,
            notification_type  = BuyerNotification.NotificationType.PAYMENT_SUCCESS,
            title              = f"Payment {instance.code} confirmed",
            message            = f"Payment of {instance.currency} {instance.amount} received.",
            related_object_type= "payment",
            related_object_id  = str(instance.pk),
        )
    except Exception as exc:
        logger.warning("Failed to create payment notification: %s", exc)


@receiver(review_submitted)
def on_review_helpful_count_sync(sender, **kwargs):
    """Sync helpful_count after a ReviewHelpful is saved/deleted."""
    pass  # Handled inline in views using F() expressions