"""apps/traceability/apps.py"""
from django.apps import AppConfig


class TraceabilityConfig(AppConfig):
  default_auto_field = "django.db.models.BigAutoField"
  name = "apps.traceability"
  verbose_name = "Traceability"

  def ready(self):
    pass