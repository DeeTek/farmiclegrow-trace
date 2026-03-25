# apps/core/signals.py  —  FarmicleGrow-Trace Platform

import logging
from django.dispatch import Signal, receiver
from django.db.models.signals import post_save, pre_save
from django.contrib.contenttypes.models import ContentType

logger = logging.getLogger("apps.core.signals")


# =============================================================================
# CUSTOM SIGNALS
# =============================================================================

status_changed    = Signal()   # kwargs: instance, old_status, new_status
approval_decision = Signal()   # kwargs: instance, decision, decided_by
model_changed     = Signal()   # kwargs: instance, action, changed_fields, user, request

domain_event      = Signal()   # kwargs: event_type, instance, **extra
# ^^ General-purpose domain event bus.
# Fired by send_event(); consumed by audit, notifications, analytics listeners.
# Any app can receive it:
#   @receiver(domain_event)
#   def my_handler(sender, event_type, instance, **kwargs): ...


# =============================================================================
# DOMAIN EVENT DISPATCHER
# =============================================================================

def send_event(event_type: str, instance, **kwargs) -> None:
    """
    Dispatch a named domain event to all registered listeners.

    Usage (from any service layer):
        send_event("order.created",    order,   buyer=buyer)
        send_event("order.cancelled",  order,   cancelled_by=user)
        send_event("payment.initiated", payment, buyer=buyer)
        send_event("payment.completed", payment)
        send_event("payment.refunded",  payment, refunded_by=user)

    The signal is sent synchronously inside the current transaction.
    Heavy side-effects (email, push, webhooks) should be deferred via
    transaction.on_commit() + Celery in the calling service, NOT here.

    Args:
        event_type: Dot-namespaced event name, e.g. "order.created".
        instance:   The primary model instance the event concerns.
        **kwargs:   Optional actor / context fields (buyer, cancelled_by, …).
    """
    try:
        domain_event.send(
            sender     = type(instance),
            event_type = event_type,
            instance   = instance,
            **kwargs,
        )
        logger.debug(
            "[Event] %s | model=%s | pk=%s",
            event_type,
            type(instance).__name__,
            getattr(instance, "pk", "?"),
        )
    except Exception as exc:
        # Never let signal dispatch crash a service call.
        logger.error(
            "[Event] send_event failed | event=%s | model=%s | pk=%s | err=%s",
            event_type,
            type(instance).__name__,
            getattr(instance, "pk", "?"),
            exc,
            exc_info=True,
        )


# =============================================================================
# DOMAIN EVENT AUDIT LOGGER
# Writes every domain event as an AuditLog entry so the event stream is
# fully queryable without a separate event store.
# =============================================================================

@receiver(domain_event)
def log_domain_event(sender, event_type: str, instance, **kwargs) -> None:
    """
    Persist every domain event in the AuditLog table.

    The entry uses action="event" and stores event_type in changed_fields
    so it is distinguishable from ordinary create/update audit rows.
    """
    try:
        from apps.core.models.concrete import AuditLog

        # Best-effort actor resolution: services may pass user/buyer/etc.
        actor = (
            kwargs.get("user")
            or kwargs.get("buyer")
            or kwargs.get("cancelled_by")
            or kwargs.get("refunded_by")
            or kwargs.get("decided_by")
            or kwargs.get("dispatched_by")
            or kwargs.get("delivered_by")
            or getattr(instance, "updated_by", None)
            or getattr(instance, "created_by", None)
        )
        # Resolve buyer → user when actor is a buyer profile
        if actor and not hasattr(actor, "username"):
            actor = getattr(actor, "user", actor)

        AuditLog.objects.create(
            user          = actor if actor and actor.pk else None,
            branch        = getattr(instance, "branch", None),
            action        = "event",
            content_type  = ContentType.objects.get_for_model(instance),
            object_id     = str(instance.pk),
            object_repr   = str(instance)[:255],
            changed_fields= {"event_type": event_type},
        )
    except Exception as exc:
        logger.error("[Event Audit] Failed for %s: %s", event_type, exc, exc_info=True)


# =============================================================================
# SNAPSHOT BEFORE SAVE  (only for TimeStampedModel subclasses)
# =============================================================================

@receiver(pre_save)
def capture_snapshot(sender, instance, **kwargs):
    from apps.core.models.abstract import TimeStampedModel

    if not issubclass(sender, TimeStampedModel):
        return

    if not instance.pk:
        instance._pre_save_snapshot = {}
        return

    try:
        db_obj = sender.objects.filter(pk=instance.pk).first()
        instance._pre_save_snapshot = (
            {f.attname: getattr(db_obj, f.attname)
             for f in sender._meta.concrete_fields}
            if db_obj else {}
        )
    except Exception:
        instance._pre_save_snapshot = {}


# =============================================================================
# EMIT model_changed AFTER SAVE
# =============================================================================

@receiver(post_save)
def emit_model_changed(sender, instance, created, **kwargs):
    from apps.core.models.abstract import TimeStampedModel

    if not issubclass(sender, TimeStampedModel):
        return

    skip     = {"updated_at", "search_vector"}
    snapshot = getattr(instance, "_pre_save_snapshot", {})
    changed  = {}

    if not created:
        for f in sender._meta.concrete_fields:
            if f.attname in skip:
                continue
            old = snapshot.get(f.attname)
            new = getattr(instance, f.attname, None)
            if old != new:
                changed[f.name] = {
                    "old": str(old) if old is not None else None,
                    "new": str(new) if new is not None else None,
                }

    if created or changed:
        model_changed.send(
            sender         = sender,
            instance       = instance,
            action         = "create" if created else "update",
            changed_fields = changed,
            user           = (
                getattr(instance, "created_by", None)
                or getattr(instance, "updated_by", None)
            ),
            request        = None,
        )


# =============================================================================
# AUDIT LOGGER  (model_changed → AuditLog)
# =============================================================================

@receiver(model_changed)
def write_audit_log(sender, instance, action, changed_fields, user, request=None, **kwargs):
    try:
        from apps.core.models.concrete import AuditLog
        AuditLog.objects.create(
            user          = user,
            branch        = getattr(instance, "branch", None),
            action        = action,
            content_type  = ContentType.objects.get_for_model(instance),
            object_id     = str(instance.pk),
            object_repr   = str(instance)[:255],
            changed_fields= changed_fields,
            ip_address    = _get_ip_address(request),
            user_agent    = _get_user_agent(request),
        )
    except Exception as exc:
        logger.error("[Audit] Failed: %s", exc, exc_info=True)


# =============================================================================
# STATUS CHANGE NOTIFICATIONS
# =============================================================================

@receiver(status_changed)
def notify_on_status_change(sender, instance, old_status, new_status, **kwargs):
    try:
        from apps.core.tasks.notifications import queue_status_notification
        queue_status_notification.delay(
            model_name = sender.__name__,
            object_id  = str(instance.pk),
            old_status = old_status,
            new_status = new_status,
        )
    except Exception as exc:
        logger.warning("[Status Notify] Failed: %s", exc)


# =============================================================================
# APPROVAL NOTIFICATIONS
# =============================================================================

@receiver(approval_decision)
def notify_on_approval(sender, instance, decision, decided_by, **kwargs):
    try:
        from apps.core.tasks.notifications import queue_approval_notification
        queue_approval_notification.delay(
            model_name    = sender.__name__,
            object_id     = str(instance.pk),
            decision      = decision,
            decided_by_id = str(decided_by.pk) if decided_by else None,
        )
    except Exception as exc:
        logger.warning("[Approval Notify] Failed: %s", exc)


# =============================================================================
# SEARCH CACHE INVALIDATION
# Invalidates the SearchCache for any registered model on post_save.
# No SearchableMixin dependency — works with the registry in search.py.
# =============================================================================

@receiver(post_save)
def invalidate_search_cache(sender, instance, **kwargs):
    """
    Invalidate search cache for registered models (list-safe version).
    """
    # Skip models you don't want cached
    if sender.__name__ in {"User"}:
        return

    try:
        from apps.core.search import SearchRegistry, SearchCache

        registry = SearchRegistry.all()

        if not isinstance(registry, list):
            logger.warning(
                "[Search Cache] Unexpected registry type: %s",
                type(registry),
            )
            return

        for entry in registry:
            model_cls = entry.get("model")
            key = entry.get("key")

            if model_cls is sender:
                try:
                    SearchCache.invalidate_group(key)
                    logger.debug(
                        "[Search Cache] Invalidated '%s' for %s",
                        key, sender.__name__,
                    )
                except Exception as exc_inner:
                    logger.warning(
                        "[Search Cache] Failed for '%s': %s",
                        key, exc_inner,
                    )

    except Exception as exc:
        logger.warning(
            "[Search Cache] General error for %s: %s",
            sender.__name__, exc
        )

# =============================================================================
# SEARCH SIGNAL CONNECTOR  (called from each app's AppConfig.ready())
# =============================================================================

def connect_search_signal(model_class):
    """
    Wire the search cache invalidation signal for a specific model.

    Call this in each app's AppConfig.ready() AFTER register_search():

        from apps.core.signals import connect_search_signal
        from apps.farmers.models import Farmer
        connect_search_signal(Farmer)

    This is optional — invalidate_search_cache() above already handles all
    registered models automatically via the post_save receiver.
    Only use this if you need custom per-model invalidation logic.
    """
    if model_class._meta.abstract:
        return

    def _handler(sender, instance, **kwargs):
        try:
            from apps.core.search import SearchRegistry, SearchCache
            registry = SearchRegistry.all()
            for key, entry in registry.items():
                if entry["model"] is sender:
                    SearchCache.invalidate_group(key)
        except Exception as exc:
            logger.warning(
                "[Search] Cache invalidation failed for %s: %s",
                model_class.__name__, exc,
            )

    post_save.connect(_handler, sender=model_class, weak=False)
    logger.debug("[Search] post_save signal connected for %s", model_class.__name__)


# =============================================================================
# HELPERS
# =============================================================================

def _get_ip_address(request):
    if not request:
        return None
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    return xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR")


def _get_user_agent(request):
    if not request:
        return ""
    return request.META.get("HTTP_USER_AGENT", "")[:500]
