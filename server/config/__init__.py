# config/__init__.py
# Load the Celery app when Django starts so @shared_task decorators work
# across all apps without needing to import the app directly.

from .celery import app as celery_app

__all__ = ("celery_app",)
