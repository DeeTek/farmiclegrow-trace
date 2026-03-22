"""
apps/staff/urls.py

Registered in project root:
    path("api/v1/", include("apps.staff.urls"))

All staff routes live under /api/v1/staff/.

Current endpoints:

  Applications
  ─────────────────────────────────────────────────────────
  POST   /api/v1/staff/applications/               submit (public)
  GET    /api/v1/staff/applications/               list   (admin)
  GET    /api/v1/staff/applications/<id>/          detail (admin)
  POST   /api/v1/staff/applications/<id>/approve/  approve (admin)
  POST   /api/v1/staff/applications/<id>/reject/   reject  (admin)

  Profiles
  ─────────────────────────────────────────────────────────
  GET    /api/v1/staff/profiles/                   list   (admin)
  GET    /api/v1/staff/profiles/<id>/              detail (admin or self)
"""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import StaffApplicationViewSet, StaffProfileViewSet

router = DefaultRouter()
router.register(r"applications", StaffApplicationViewSet, basename="application")
router.register(r"profiles",     StaffProfileViewSet,     basename="profile")

urlpatterns = [
    path("", include(router.urls)),
]