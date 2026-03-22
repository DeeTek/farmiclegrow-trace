"""
apps/farmers/urls.py

URL configuration for the farmers app.

Registered under api/v1/ in the project root urls.py.

Router registrations:
  farmers/     FarmerViewSet
  farms/       FarmViewSet
  products/    ProductViewSet

Generated URL patterns:

  Farmers
  ─────────────────────────────────────────────────────────────
  GET    /api/v1/farmers/                         list (role-scoped)
  POST   /api/v1/farmers/                         create (field agent / admin)
  GET    /api/v1/farmers/<id>/                    detail
  PUT    /api/v1/farmers/<id>/                    update
  PATCH  /api/v1/farmers/<id>/                    partial update
  DELETE /api/v1/farmers/<id>/                    soft-delete (admin)
  POST   /api/v1/farmers/<id>/verify/             admin verify
  POST   /api/v1/farmers/<id>/reject/             admin reject
  POST   /api/v1/farmers/<id>/suspend/            admin suspend
  POST   /api/v1/farmers/onboard/                 field officer onboard
  POST   /api/v1/farmers/<id>/password-reset/     admin reset credential
  POST   /api/v1/farmers/<id>/impersonate/        admin impersonate
  GET    /api/v1/farmers/<id>/farms/              farmer's farm plots
  GET    /api/v1/farmers/<id>/profile-score/      completeness score
  GET    /api/v1/farmers/verification-stats/      aggregate counts
  GET    /api/v1/farmers/export-csv/              CSV export (admin)

  Farms
  ─────────────────────────────────────────────────────────────
  GET    /api/v1/farms/                           list
  POST   /api/v1/farms/                           create (field agent / admin)
  GET    /api/v1/farms/<id>/                      detail
  PUT    /api/v1/farms/<id>/                      update
  DELETE /api/v1/farms/<id>/                      soft-delete (admin)
  POST   /api/v1/farms/<id>/visit/                log field visit
  GET    /api/v1/farms/<id>/visits/               list visits
  GET    /api/v1/farms/<id>/crop-seasons/         list crop seasons
  POST   /api/v1/farms/<id>/crop-seasons/         add crop season

  Products (marketplace)
  ─────────────────────────────────────────────────────────────
  GET    /api/v1/products/                        list (public)
  POST   /api/v1/products/                        create (admin)
  GET    /api/v1/products/<id>/                   detail (public)
  PUT    /api/v1/products/<id>/                   update (admin)
  DELETE /api/v1/products/<id>/                   soft-delete (admin)
  GET    /api/v1/products/categories/             distinct categories
  GET    /api/v1/products/low-stock/              below threshold (admin)

  Product Reviews
  ─────────────────────────────────────────────────────────────
  GET    /api/v1/reviews/                         list (public, filter by ?product=<id>)
  POST   /api/v1/reviews/                         submit review (verified buyer)
  GET    /api/v1/reviews/<id>/                    detail (public)
  DELETE /api/v1/reviews/<id>/                    soft-delete own review
  POST   /api/v1/reviews/<id>/helpful/            mark helpful
  POST   /api/v1/reviews/<id>/unhelpful/          remove helpful vote
"""

from rest_framework.routers import DefaultRouter

from .views import FarmerViewSet, FarmViewSet, ProductViewSet, ProductReviewViewSet

router = DefaultRouter()
router.register(r"",  FarmerViewSet,       basename="farmer")
router.register(r"farms",    FarmViewSet,         basename="farm")
router.register(r"products", ProductViewSet,      basename="product")
router.register(r"reviews",  ProductReviewViewSet, basename="review")

urlpatterns = router.urls