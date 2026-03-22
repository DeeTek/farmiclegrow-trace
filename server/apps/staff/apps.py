from django.apps import AppConfig

class StaffConfig(AppConfig):
    name            = "apps.staff"
    default_auto_field = "django.db.models.BigAutoField"
    _registered     = False

    def ready(self):
      pass
      """
        if self.__class__._registered:
            return
        self.__class__._registered = True

        from apps.core.search import register_search
        from apps.staff.models import FieldOfficer
        from apps.staff.serializers import FieldOfficerListSerializer

        register_search(
            key                = "officers",
            model              = FieldOfficer,
            fields             = [
                "user__first_name", "user__last_name",
                "user__phone_number", "assigned_region", "assigned_district",
            ],
            serializer         = FieldOfficerListSerializer,
            roles              = ["admin", "hr"],
            code_fields        = ["code"],
            index_fields       = ["code", "assigned_region"],
            autocomplete_field = "code",
            select_related     = ["user"],
            order_by           = ["-created_at"],
            db_backend         = "orm",
            cache_ttl          = 120,
        )
      """
