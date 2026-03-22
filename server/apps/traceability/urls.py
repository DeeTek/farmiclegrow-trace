"""
apps/traceability/urls.py

URL configuration for the traceability app.
Registered under api/v1/ in the project root urls.py.

──────────────────────────────────────────────────────────────────────────────
Generated URL patterns:
──────────────────────────────────────────────────────────────────────────────

  GET    /api/v1/scan/<code>/                          QR code resolver (public)

  GET    /api/v1/batches/                              list
  POST   /api/v1/batches/                              create (field officer / admin)
  GET    /api/v1/batches/<id>/                         detail
  PATCH  /api/v1/batches/<id>/                         update
  DELETE /api/v1/batches/<id>/                         soft-delete (admin)
  GET    /api/v1/batches/<id>/trace/                   full chain for batch
  POST   /api/v1/batches/<id>/assign/                  assign to trace record

  GET    /api/v1/warehouse-intakes/                    list
  POST   /api/v1/warehouse-intakes/                    create (warehouse manager)
  GET    /api/v1/warehouse-intakes/<id>/               detail
  PATCH  /api/v1/warehouse-intakes/<id>/               update
  POST   /api/v1/warehouse-intakes/<id>/accept/        QC pass
  POST   /api/v1/warehouse-intakes/<id>/reject/        QC fail

  GET    /api/v1/trace-records/                        list
  POST   /api/v1/trace-records/                        create (admin)
  GET    /api/v1/trace-records/<id>/                   detail
  PATCH  /api/v1/trace-records/<id>/                   update (admin)
  DELETE /api/v1/trace-records/<id>/                   soft-delete (admin)
  POST   /api/v1/trace-records/<id>/update-status/     status transition (admin)
  GET    /api/v1/trace-records/<id>/chain/             full chain dict
  POST   /api/v1/trace-records/<id>/certify/           attach certification (admin)
  GET    /api/v1/trace-records/pipeline/               status counts
  GET    /api/v1/trace-records/destination-summary/    export volumes by country

──────────────────────────────────────────────────────────────────────────────
QR scan endpoint:
  QRScanView lives here because resolving trace_code, farmer_batch_code, and
  product_batch_code are traceability-domain concerns. It imports only from
  this app's own models and serializers — no circular import risk.
──────────────────────────────────────────────────────────────────────────────
"""

from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    BatchViewSet,
    QRScanView,
    TraceRecordViewSet,
    WarehouseIntakeViewSet,
)

router = DefaultRouter()
router.register(r"batches",           BatchViewSet,           basename="batch")
router.register(r"warehouse-intakes", WarehouseIntakeViewSet, basename="warehouse-intake")
router.register(r"trace-records",     TraceRecordViewSet,     basename="trace-record")

urlpatterns = [
    # ── QR code resolver (public — no auth required) ──────────────────────────
    path("scan/<str:code>/", QRScanView.as_view(), name="qr-scan"),

    # ── ViewSet routes ────────────────────────────────────────────────────────
    *router.urls,
]
