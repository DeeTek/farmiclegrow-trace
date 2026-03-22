"""apps/buyers/apps.py"""
from django.apps import AppConfig


class BuyersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name               = "apps.buyers"
    verbose_name       = "Buyers & E-commerce"

    def ready(self):
      pass