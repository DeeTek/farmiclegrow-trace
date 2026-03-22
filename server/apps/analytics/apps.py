"""apps/analytics/apps.py"""
from django.apps import AppConfig


class AnalyticsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name               = "apps.analytics"
    verbose_name       = "Analytics"

    def ready(self):
        pass  # Analytics reads from other apps — no models to register for search