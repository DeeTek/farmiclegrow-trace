from django.contrib import admin
from django.urls import path, re_path, include
from drf_yasg import openapi
from drf_yasg.views import get_schema_view
from rest_framework import permissions

schema_view = get_schema_view(
  openapi.Info(
    title="Farmiclegrow Trace System API",
    default_version='v1',
    description="API documentation for Farmiclegrow Trace",
    terms_of_services='http://famiclegrow-trace.com/terms',
    contact=openapi.Contact(email='support@famiclegrow-trace.com'),
    license=openapi.License(name='MIT License'),
  ),
  public=True,
  permission_classes = [permissions.AllowAny]
)


urlpatterns = [
    path('admin/', admin.site.urls),
    
    # ==== Account ====
    
    path('account/', include('apps.accounts.urls')),
    
    # ==== API ====
    
    path("api/v1/buyers/", include("apps.buyers.urls")),
    path("api/v1/farmers/", include("apps.farmers.urls")),
    path("api/v1/traceability/", include("apps.traceability.urls")),
    path("api/v1/staff/", include("apps.staff.urls")),
    path("api/v1/reports/", include("apps.reports.urls")),
    path("api/v1/analytics/", include("apps.analytics.urls")),
    path("api/v1/core/", include("apps.core.urls")),
    
    
    
    # ======== Swagger API urls ========
    
    re_path(r'^swagger(?P<format>\.json|\.yaml)$', schema_view.without_ui(cache_timeout=0), name='swagger-json'),
    path('swagger/', schema_view.with_ui('swagger', cache_timeout=0), name='swagger-schema-ui'),
    path('redoc/', schema_view.with_ui('redoc', cache_timeout=0), name='swagger-redoc'),
    
]


