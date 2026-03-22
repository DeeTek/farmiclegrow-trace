"""
config/celery.py  —  FarmicleGrow-Trace Platform

Celery application entry point.

Usage:
  Start worker:
    celery -A config worker -l info

  Start beat (periodic tasks):
    celery -A config beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler

  Start both together (dev only):
    celery -A config worker --beat -l info

  Check worker is alive:
    celery -A config inspect ping
"""
import os

from celery import Celery
from django.conf import settings

# Tell Celery which Django settings module to use
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("farmiclegrow_trace")

# Read all CELERY_* settings from Django settings.py
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks.py in every INSTALLED_APP
app.autodiscover_tasks()


# =============================================================================
# DEBUG TASK  — confirms worker is running
# =============================================================================

@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
