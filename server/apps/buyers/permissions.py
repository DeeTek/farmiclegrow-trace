"""
apps/buyers/permissions.py

All custom DRF permission classes for the buyers app.
Replaces every inline permission check that was in views.py.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from rest_framework import permissions

User = get_user_model()


class IsBuyerOwner(permissions.BasePermission):
    """
    Object-level: the request user must own the buyer profile associated
    with the object. Works for Buyer, Order, Cart, Payment, Review, etc.
    """

    def has_object_permission(self, request, view, obj):
        from apps.buyers.models import Buyer

        buyer = obj if isinstance(obj, Buyer) else getattr(obj, "buyer", None)
        return bool(buyer and buyer.user_id == request.user.pk)


class IsVerifiedBuyer(permissions.BasePermission):
    """Buyer must have passed KYC before checkout and payment initiation."""

    message = "Your buyer account must be verified before placing orders."

    def has_permission(self, request, view):
        buyer = getattr(request.user, "buyer_profile", None)
        return bool(buyer and buyer.verification_status == "verified")


class IsOrderOwnerOrAdmin(permissions.BasePermission):
    """Buyer owns the order OR the user is staff/admin."""

    def has_object_permission(self, request, view, obj):
        if request.user.is_staff or request.user.is_superuser:
            return True
        buyer = getattr(request.user, "buyer_profile", None)
        return bool(buyer and obj.buyer_id == buyer.pk)


class IsReviewOwnerOrAdmin(permissions.BasePermission):
    """Buyer owns the review OR the user is staff/admin."""

    def has_object_permission(self, request, view, obj):
        if request.user.is_staff or request.user.is_superuser:
            return True
        buyer = getattr(request.user, "buyer_profile", None)
        return bool(buyer and obj.buyer_id == buyer.pk)