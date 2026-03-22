"""
apps/staff/tasks.py

Email dispatch for the staff app.

Template naming convention (flat files, no subfolders):
  {name}_subject.txt   — one line subject, rendered and stripped
  {name}_message.txt   — plain-text body (fallback for all email clients)
  {name}.html          — HTML body (shown by Gmail, Outlook, Apple Mail, etc.)

Full paths:
  templates/staff/emails/approval_subject.txt
  templates/staff/emails/approval_message.txt
  templates/staff/emails/approval.html

  templates/staff/emails/rejection_subject.txt
  templates/staff/emails/rejection_message.txt
  templates/staff/emails/rejection.html

Django's EmailMultiAlternatives sends both parts in one email.
Email clients that can render HTML show approval.html / rejection.html.
Everything else falls back to approval_message.txt / rejection_message.txt.

Context variables available in all templates:
  {{ platform }}    — from settings.PLATFORM_NAME

Approval templates also receive:
  {{ full_name }}   — applicant's full name
  {{ staff_id }}    — e.g. FG-ASH-2024-0001
  {{ role }}        — e.g. "Field Officer"
  {{ setup_link }}  — one-time signed URL to set password

Rejection templates also receive:
  {{ full_name }}   — applicant's full name
  {{ reason }}      — the reason the admin typed
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

logger   = logging.getLogger("apps.staff.email")
_BASE    = "staff/emails"
_PLATFORM = getattr(settings, "PLATFORM_NAME", "FarmicleGrow-Trace")


# ─────────────────────────────────────────────────────────────────────────────
# SHARED HELPER
# ─────────────────────────────────────────────────────────────────────────────

def _send(*, name: str, context: dict, to_email: str) -> None:
    """
    Render and send one email using the flat three-file template convention.

    name="approval"  renders:
      staff/emails/approval_subject.txt
      staff/emails/approval_message.txt
      staff/emails/approval.html

    name="rejection" renders:
      staff/emails/rejection_subject.txt
      staff/emails/rejection_message.txt
      staff/emails/rejection.html
    """
    context = {**context, "platform": _PLATFORM}

    subject  = render_to_string(f"{_BASE}/{name}_subject.txt",  context).strip()
    body_txt = render_to_string(f"{_BASE}/{name}_message.txt",  context)
    body_html = render_to_string(f"{_BASE}/{name}.html",        context)

    email = EmailMultiAlternatives(
        subject    = subject,
        body       = body_txt,
        from_email = settings.DEFAULT_FROM_EMAIL,
        to         = [to_email],
    )
    email.attach_alternative(body_html, "text/html")
    email.send(fail_silently=False)

    logger.info("email_sent | name=%s | to=%s", name, to_email)


# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL EMAIL
# ─────────────────────────────────────────────────────────────────────────────

def send_approval_email(
    *,
    to_email:   str,
    full_name:  str,
    staff_id:   str,
    role:       str,
    setup_link: str,
) -> None:
    """
    Sent when admin approves a StaffApplication.
    Called inside transaction.on_commit() — never fires on a rolled-back transaction.
    """
    _send(
        name     = "approval",
        to_email = to_email,
        context  = {
            "full_name":  full_name,
            "staff_id":   staff_id,
            "role":       role,
            "setup_link": setup_link,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# REJECTION EMAIL
# ─────────────────────────────────────────────────────────────────────────────

def send_rejection_email(
    *,
    to_email:  str,
    full_name: str,
    reason:    str,
) -> None:
    """
    Sent when admin rejects a StaffApplication.
    Called inside transaction.on_commit() — never fires on a rolled-back transaction.
    """
    _send(
        name     = "rejection",
        to_email = to_email,
        context  = {
            "full_name": full_name,
            "reason":    reason,
        },
    )