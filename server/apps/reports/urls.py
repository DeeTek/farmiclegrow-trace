"""
apps/reports/urls.py

URL configuration for the reports app.

Registered under api/v1/ in the project root urls.py:
    path("api/v1/", include("apps.reports.urls")),

Generated URL patterns:
  GET    /api/v1/reports/                        paginated list (staff / admin)
  POST   /api/v1/reports/queue/                  queue a new report
  GET    /api/v1/reports/<id>/                   report detail
  DELETE /api/v1/reports/<id>/                   delete report record (admin)
  GET    /api/v1/reports/<id>/download/          download generated file
  POST   /api/v1/reports/<id>/retry/             retry a failed report (admin)

  GET    /api/v1/report-schedules/               list all schedules (admin)
  POST   /api/v1/report-schedules/               create schedule (admin)
  GET    /api/v1/report-schedules/<id>/          schedule detail
  PATCH  /api/v1/report-schedules/<id>/          update schedule
  DELETE /api/v1/report-schedules/<id>/          delete schedule
  POST   /api/v1/report-schedules/<id>/toggle/   enable / disable schedule
"""

from rest_framework.routers import DefaultRouter

from .views import ReportViewSet, ReportScheduleViewSet

router = DefaultRouter()
router.register(r"",          ReportViewSet,         basename="report")
router.register(r"report-schedules", ReportScheduleViewSet, basename="report-schedule")

urlpatterns = router.urls