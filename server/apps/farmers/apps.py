from django.apps import AppConfig

class FarmersConfig(AppConfig):
    name            = "apps.farmers"
    default_auto_field = "django.db.models.BigAutoField"
    _registered     = False

    def ready(self):
        if self.__class__._registered:
            return
        self.__class__._registered = True

        from django.db.models import Q
        from apps.core.search import register_search
        from .models import Farmer, Farm, Product, FarmVisit
        from .serializers import (
            FarmerListSerializer,
            FarmListSerializer,
            ProductListSerializer,
            FarmVisitListSerializer,
        )

        # ── Farmer ────────────────────────────────────────────────────────────
        register_search(
            key                = "farmers",
            model              = Farmer,
            fields             = [
                "first_name", "last_name", "phone_number",
                "community", "district", "region",
                "ghana_card_number", "cooperative_name",
            ],
            serializer         = FarmerListSerializer,
            roles              = ["officer", "admin", "buyer"],
            buyer_filter       = Q(verification_status="verified"),
            code_fields        = ["code", "ghana_card_number"],
            index_fields       = ["code", "phone_number", "ghana_card_number", "community"],
            autocomplete_field = "code",
            select_related     = ["user"],
            order_by           = ["-created_at"],
            limit              = 8,
            db_backend         = "pg_fts",
            cache_ttl          = 120,
            highlight          = True,
        )

        # ── Farm ──────────────────────────────────────────────────────────────
        register_search(
            key                = "farms",
            model              = Farm,
            fields             = [
                "code", "name", "district",
                "farmer__first_name", "farmer__last_name",
                "farmer__region",
            ],
            serializer         = FarmListSerializer,
            roles              = ["officer", "admin"],
            code_fields        = ["code"],
            index_fields       = ["code"],
            autocomplete_field = "code",
            select_related     = ["farmer", "farmer__user"],
            order_by           = ["-created_at"],
            db_backend         = "orm",
            cache_ttl          = 60,
        )

        # ── Product ───────────────────────────────────────────────────────────
        register_search(
            key                = "products",
            model              = Product,
            fields             = ["name", "description", "origin_country", "origin_region"],
            serializer         = ProductListSerializer,
            roles              = ["buyer", "officer", "admin"],
            buyer_filter       = Q(is_available=True),
            code_fields        = ["code"],
            index_fields       = ["name", "origin_country"],
            autocomplete_field = "name",
            select_related     = ["farmer"],
            order_by           = ["-created_at"],
            db_backend         = "pg_fts",
            cache_ttl          = 300,
            highlight          = True,
        )

        # ── Farm Visit ────────────────────────────────────────────────────────
        register_search(
            key            = "farm_visits",
            model          = FarmVisit,
            fields         = ["farm__code", "field_officer__first_name", "purpose"],
            serializer     = FarmVisitListSerializer,
            roles          = ["officer", "admin"],
            select_related = ["farm", "field_officer"],
            order_by       = ["-visited_at"],
            db_backend     = "orm",
            cache_ttl      = 30,
        )
