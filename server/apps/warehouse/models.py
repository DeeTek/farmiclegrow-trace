# apps/warehouse/models.py

import uuid
from django.conf import settings
from django.db import models


# -------------------------------------------------------------------
# WAREHOUSE
# -------------------------------------------------------------------

class Warehouse(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50, unique=True)

    region = models.CharField(max_length=100)
    district = models.CharField(max_length=100)
    community = models.CharField(max_length=100, blank=True)

    latitude = models.FloatField()
    longitude = models.FloatField()

    capacity_kg = models.FloatField(null=True, blank=True)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.code})"


# -------------------------------------------------------------------
# WAREHOUSE USERS (LABOUR + MANAGEMENT)
# -------------------------------------------------------------------

class WarehouseUser(models.Model):
    class Role(models.TextChoices):
        MANAGER = "manager", "Manager"
        SUPERVISOR = "supervisor", "Supervisor"
        QC_OFFICER = "qc_officer", "Quality Control Officer"
        OPERATOR = "operator", "Operator"
        LOADER = "loader", "Loader"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.CASCADE,
        related_name="workers"
    )

    role = models.CharField(max_length=30, choices=Role.choices)

    full_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.full_name} - {self.role}"


# -------------------------------------------------------------------
# WAREHOUSE BATCH (AGGREGATED RAW MATERIAL)
# -------------------------------------------------------------------

class WarehouseBatch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    batch_code = models.CharField(max_length=50, unique=True)

    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.CASCADE,
        related_name="batches"
    )

    farmer_batches = models.ManyToManyField(
        "farmers.FarmerBatch",
        related_name="warehouse_batches"
    )

    total_weight_kg = models.FloatField()

    received_at = models.DateTimeField()

    created_by = models.ForeignKey(
        WarehouseUser,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_batches"
    )

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["batch_code"]),
            models.Index(fields=["warehouse", "received_at"]),
        ]

    def __str__(self):
        return self.batch_code


# -------------------------------------------------------------------
# QUALITY CHECK (QC)
# -------------------------------------------------------------------

class QualityCheck(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    warehouse_batch = models.OneToOneField(
        WarehouseBatch,
        on_delete=models.CASCADE,
        related_name="quality_check"
    )

    moisture_percent = models.FloatField(null=True, blank=True)
    impurities_percent = models.FloatField(null=True, blank=True)

    grade = models.CharField(max_length=50, blank=True)

    temperature = models.FloatField(null=True, blank=True)

    passed = models.BooleanField(default=True)

    notes = models.TextField(blank=True)

    checked_by = models.ForeignKey(
        WarehouseUser,
        on_delete=models.SET_NULL,
        null=True,
        related_name="quality_checks"
    )

    checked_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"QC - {self.warehouse_batch.batch_code}"


# -------------------------------------------------------------------
# PRODUCT BATCH (FINAL PROCESSED PRODUCT)
# -------------------------------------------------------------------

class ProductBatch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    product_batch_code = models.CharField(max_length=50, unique=True)

    warehouse_batch = models.ForeignKey(
        WarehouseBatch,
        on_delete=models.CASCADE,
        related_name="product_batches"
    )

    product_name = models.CharField(max_length=255)
    category = models.CharField(max_length=100)

    total_weight_kg = models.FloatField()

    grade = models.CharField(max_length=50, blank=True)

    processing_method = models.CharField(max_length=255, blank=True)

    processed_at = models.DateTimeField()

    created_by = models.ForeignKey(
        WarehouseUser,
        on_delete=models.SET_NULL,
        null=True,
        related_name="processed_batches"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["product_batch_code"]),
        ]

    def __str__(self):
        return self.product_batch_code


# -------------------------------------------------------------------
# DISPATCH (TO BUYERS)
# -------------------------------------------------------------------

class Dispatch(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_TRANSIT = "in_transit", "In Transit"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    product_batch = models.ForeignKey(
        ProductBatch,
        on_delete=models.CASCADE,
        related_name="dispatches"
    )

    buyer = models.ForeignKey(
        "buyers.Buyer",
        on_delete=models.CASCADE,
        related_name="dispatches"
    )

    quantity_kg = models.FloatField()

    dispatched_by = models.ForeignKey(
        WarehouseUser,
        on_delete=models.SET_NULL,
        null=True,
        related_name="dispatches"
    )

    dispatched_at = models.DateTimeField()
    delivered_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.product_batch.product_batch_code} → {self.buyer_id}"


# -------------------------------------------------------------------
# WAREHOUSE ACTION LOG (AUDIT)
# -------------------------------------------------------------------

class WarehouseActionLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    worker = models.ForeignKey(
        WarehouseUser,
        on_delete=models.CASCADE,
        related_name="actions"
    )

    action_type = models.CharField(max_length=100)

    description = models.TextField(blank=True)

    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)