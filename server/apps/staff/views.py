"""
apps/staff/views.py

Five endpoints for the current scope:

  StaffApplicationViewSet
    POST   /api/v1/staff/applications/            submit (public, no auth)
    GET    /api/v1/staff/applications/            list   (admin only)
    GET    /api/v1/staff/applications/<id>/       detail (admin only)
    POST   /api/v1/staff/applications/<id>/approve/  admin approves
    POST   /api/v1/staff/applications/<id>/reject/   admin rejects

  StaffProfileViewSet
    GET    /api/v1/staff/profiles/<id>/           profile detail (admin or self)

Permissions:
  Submit application → anyone (AllowAny)
  List / detail applications → admin only (IsAdminUser)
  Approve / reject → admin only (IsAdminUser)
  Profile detail → admin OR the staff member themselves (IsAdminOrProfileOwner)
"""
from __future__ import annotations

import logging

from django_filters.rest_framework import DjangoFilterBackend

from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.response import Response

from apps.staff.models import ApplicationStatus, StaffApplication, StaffProfile
from apps.staff.serializers import (
    StaffApplicationApproveSerializer,
    StaffApplicationReadSerializer,
    StaffApplicationRejectSerializer,
    StaffApplicationSubmitSerializer,
    StaffProfileSerializer,
)
from apps.staff.services import approve_application, reject_application

logger = logging.getLogger("apps.staff")


# ─────────────────────────────────────────────────────────────────────────────
# PERMISSIONS
# ─────────────────────────────────────────────────────────────────────────────

class IsAdminUser(permissions.BasePermission):
    """Only users with role == 'admin' (or is_staff) can proceed."""
    message = "Admin access required."

    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and (
                getattr(request.user, "role", None) == "admin"
                or request.user.is_staff
            )
        )


class IsAdminOrProfileOwner(permissions.BasePermission):
    """
    Admin sees any profile.
    A staff member can only see their own profile.
    """
    message = "You can only view your own profile."

    def has_object_permission(self, request, view, obj: StaffProfile):
        if request.user.is_staff or getattr(request.user, "role", None) == "admin":
            return True
        return obj.user_id == request.user.pk


# ─────────────────────────────────────────────────────────────────────────────
# STAFF APPLICATION VIEWSET
# ─────────────────────────────────────────────────────────────────────────────

class StaffApplicationViewSet(viewsets.ModelViewSet):
    """
    submit   POST   /applications/          — no auth
    list     GET    /applications/          — admin
    detail   GET    /applications/<id>/     — admin
    approve  POST   /applications/<id>/approve/  — admin
    reject   POST   /applications/<id>/reject/   — admin
    """

    filter_backends  = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["status", "intended_role"]
    search_fields    = ["full_name", "email", "ghana_card_number", "preferred_region"]
    ordering_fields  = ["created_at", "status"]
    ordering         = ["-created_at"]

    # ── Permission routing ────────────────────────────────────────────────────

    def get_permissions(self):
        if self.action == "create":
            # Public — anyone can submit
            return [permissions.AllowAny()]
        # Everything else (list, detail, approve, reject) → admin only
        return [IsAdminUser()]

    # ── Queryset + serializer routing ─────────────────────────────────────────

    def get_queryset(self):
        return (
            StaffApplication.objects
            .select_related("reviewed_by")
            .order_by("-created_at")
        )

    def get_serializer_class(self):
        if self.action == "create":   return StaffApplicationSubmitSerializer
        if self.action == "approve":  return StaffApplicationApproveSerializer
        if self.action == "reject":   return StaffApplicationRejectSerializer
        return StaffApplicationReadSerializer

    # ── CREATE — public submit ────────────────────────────────────────────────

    def create(self, request, *args, **kwargs):
        """
        POST /api/v1/staff/applications/

        Anyone can submit. No account is created at this point.
        Returns a simple confirmation — not the full application object —
        so the applicant knows their submission was received.
        """
        ser = StaffApplicationSubmitSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        application = ser.save()

        return Response(
            {
                "detail": (
                    "Your application has been submitted. "
                    "You will receive an email when it has been reviewed."
                ),
                "application_id": str(application.pk),
            },
            status=status.HTTP_201_CREATED,
        )

    # ── Block unsafe operations on applications ───────────────────────────────

    def update(self, request, *args, **kwargs):
        raise ValidationError(
            {"detail": "Applications cannot be edited. Use /approve/ or /reject/."}
        )

    def partial_update(self, request, *args, **kwargs):
        raise ValidationError(
            {"detail": "Applications cannot be edited. Use /approve/ or /reject/."}
        )

    def destroy(self, request, *args, **kwargs):
        raise ValidationError(
            {"detail": "Applications are kept permanently. They cannot be deleted."}
        )

    # ── APPROVE ───────────────────────────────────────────────────────────────

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """
        POST /api/v1/staff/applications/<id>/approve/

        Approves the application.
        Creates the User account and StaffProfile from the application data.
        Sends the applicant a setup-link email so they can set their password.

        Body (optional):
          { "note": "Internal note about this approval" }
        """
        application = self.get_object()

        # Guard — cannot approve what is already decided
        if application.status == ApplicationStatus.APPROVED:
            raise ValidationError(
                {"detail": "This application has already been approved."}
            )
        if application.status == ApplicationStatus.REJECTED:
            raise ValidationError(
                {"detail": "A rejected application cannot be approved."}
            )

        # Validate optional body
        ser = StaffApplicationApproveSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        # Run the service
        try:
            profile = approve_application(application, request.user)
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        return Response(
            {
                "detail":   "Application approved. Setup email sent to the applicant.",
                "staff_id": profile.staff_id,
                "role":     profile.get_role_display(),
                "profile":  StaffProfileSerializer(profile).data,
            },
            status=status.HTTP_201_CREATED,
        )

    # ── REJECT ────────────────────────────────────────────────────────────────

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        """
        POST /api/v1/staff/applications/<id>/reject/

        Rejects the application. No account is created.
        Sends the applicant a rejection email with the admin's reason.

        Body (required):
          { "reason": "Plain-language reason sent to the applicant." }
        """
        application = self.get_object()

        # Guard
        if application.status == ApplicationStatus.APPROVED:
            raise ValidationError(
                {"detail": "An approved application cannot be rejected."}
            )

        # Validate body — reason is required
        ser = StaffApplicationRejectSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        try:
            reject_application(
                application,
                request.user,
                ser.validated_data["reason"],
            )
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        return Response({"detail": "Application rejected. Notification sent to the applicant."})


# ─────────────────────────────────────────────────────────────────────────────
# STAFF PROFILE VIEWSET
# ─────────────────────────────────────────────────────────────────────────────

class StaffProfileViewSet(viewsets.ReadOnlyModelViewSet):
    """
    GET /api/v1/staff/profiles/        — admin: all profiles
    GET /api/v1/staff/profiles/<id>/   — admin or profile owner
    """

    serializer_class = StaffProfileSerializer
    filter_backends  = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["status", "role"]
    search_fields    = ["staff_id", "ghana_card_number", "user__email"]
    ordering         = ["-created_at"]

    def get_permissions(self):
        if self.action == "retrieve":
            # Admin OR the staff member themselves
            return [permissions.IsAuthenticated(), IsAdminOrProfileOwner()]
        # List → admin only
        return [IsAdminUser()]

    def get_queryset(self):
        return (
            StaffProfile.objects
            .select_related("user", "source_application")
            .order_by("-created_at")
        )