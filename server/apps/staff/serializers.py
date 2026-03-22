"""
apps/staff/serializers.py

Serializers for the current scope:

  StaffApplicationSubmitSerializer
      Public form the applicant fills in. No authentication needed.
      Validates that the email and Ghana Card are not already taken.

  StaffApplicationReadSerializer
      Admin read view. Shows everything including who reviewed it.

  StaffApplicationApproveSerializer
      Body for the approve action. Nothing required — all profile data
      comes from the application itself. Optional admin note for future use.

  StaffApplicationRejectSerializer
      Body for the reject action. reason is required.

  StaffProfileSerializer
      Read-only view of a StaffProfile. Includes the linked user's
      email and the source application's intended_role for context.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model

from rest_framework import serializers

from apps.staff.models import (
    ApplicationStatus,
    StaffApplication,
    StaffProfile,
    StaffRole,
)

User = get_user_model()


# ─────────────────────────────────────────────────────────────────────────────
# STAFF APPLICATION
# ─────────────────────────────────────────────────────────────────────────────

class StaffApplicationSubmitSerializer(serializers.ModelSerializer):
    """
    Public application form — no authentication required.

    The applicant fills this in. No account is created at this point.
    Validation catches duplicate email and duplicate Ghana Card before
    the record even reaches admin.
    """

    class Meta:
        model  = StaffApplication
        fields = [
            "id",
            "full_name",
            "email",
            "phone",
            "ghana_card_number",
            "intended_role",
            "educational_level",
            "preferred_region",
        ]
        read_only_fields = ["id"]

    def validate_email(self, value: str) -> str:
        value = value.lower().strip()

        # Email already has a live account
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError(
                "An account with this email address already exists."
            )

        # Email already has a pending or approved application
        if StaffApplication.objects.filter(
            email__iexact=value,
            status__in=[ApplicationStatus.SUBMITTED, ApplicationStatus.APPROVED],
        ).exists():
            raise serializers.ValidationError(
                "An application with this email is already under review or has been approved."
            )

        return value

    def validate_ghana_card_number(self, value: str) -> str:
        # Card already registered to an active profile
        if StaffProfile.objects.filter(ghana_card_number=value).exists():
            raise serializers.ValidationError(
                "A staff member with this Ghana Card number is already registered."
            )

        # Card already on a pending application
        if StaffApplication.objects.filter(
            ghana_card_number=value,
            status=ApplicationStatus.SUBMITTED,
        ).exists():
            raise serializers.ValidationError(
                "An application with this Ghana Card number is already pending review."
            )

        return value


class StaffApplicationReadSerializer(serializers.ModelSerializer):
    """
    Admin read view — full detail.

    reviewed_by_name is a convenience field so the frontend does not need
    a second request to look up the reviewer's name.
    """
    reviewed_by_name  = serializers.SerializerMethodField()
    has_profile       = serializers.SerializerMethodField()

    class Meta:
        model  = StaffApplication
        fields = [
            "id",
            "full_name",
            "email",
            "phone",
            "ghana_card_number",
            "intended_role",
            "educational_level",
            "preferred_region",
            "status",
            "reviewed_by",
            "reviewed_by_name",
            "has_profile",
            "created_at",
        ]
        read_only_fields = fields

    def get_reviewed_by_name(self, obj) -> str:
        if obj.reviewed_by:
            return obj.reviewed_by.get_full_name() or obj.reviewed_by.email
        return ""

    def get_has_profile(self, obj) -> bool:
        """True if approval has already created a StaffProfile for this application."""
        return hasattr(obj, "resulting_profile")


class StaffApplicationApproveSerializer(serializers.Serializer):
    """
    Body for the approve action.

    No fields are required — all profile data comes from the application.
    An optional note field is here for future use (e.g. storing an
    internal comment alongside the approval decision).
    """
    note = serializers.CharField(
        max_length    = 500,
        required      = False,
        allow_blank   = True,
        help_text     = "Optional internal note stored alongside the approval.",
    )


class StaffApplicationRejectSerializer(serializers.Serializer):
    """
    Body for the reject action. reason is mandatory.

    The reason is sent to the applicant in the rejection email, so it
    should be written in plain language the applicant can understand.
    """
    reason = serializers.CharField(max_length=1000)

    def validate_reason(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError(
                "A rejection reason is required. "
                "It will be sent to the applicant."
            )
        return value.strip()


# ─────────────────────────────────────────────────────────────────────────────
# STAFF PROFILE
# ─────────────────────────────────────────────────────────────────────────────

class StaffProfileSerializer(serializers.ModelSerializer):
    """
    Read-only profile serializer.

    user_email       — the email of the linked User account.
    role_display     — human-readable role label (e.g. "Field Officer").
    status_display   — human-readable status label (e.g. "Pending").
    source_application_id — the PK of the StaffApplication this profile
                            was created from, so the frontend can link back.
    """
    user_email            = serializers.SerializerMethodField()
    role_display          = serializers.SerializerMethodField()
    status_display        = serializers.SerializerMethodField()
    source_application_id = serializers.SerializerMethodField()

    class Meta:
        model  = StaffProfile
        fields = [
            "id",
            "staff_id",
            "role",
            "role_display",
            "ghana_card_number",
            "gender",
            "phone",
            "date_of_birth",
            "profile_photo",
            "status",
            "status_display",
            "user",
            "user_email",
            "source_application_id",
            "created_at",
        ]
        read_only_fields = fields

    def get_user_email(self, obj) -> str:
        return obj.user.email

    def get_role_display(self, obj) -> str:
        return obj.get_role_display()

    def get_status_display(self, obj) -> str:
        return obj.get_status_display()

    def get_source_application_id(self, obj) -> str:
        return str(obj.source_application_id)