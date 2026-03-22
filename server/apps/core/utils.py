"""
apps/core/utils.py  —  FarmicleGrow-Trace Platform

Pure, stateless utility functions. No model imports.
Safe to import from any app without circular dependency risk.

FIX vs original
───────────────
  ─ generate_code() used random.randint — not cryptographically secure.
    Now uses secrets.randbelow() — unpredictable, safe for public codes.
  ─ generate_otp() used random.choices — now uses secrets.choice().
  ─ secrets_token() was separate from generate_access_token() — merged.
  ─ calculate_completeness() used dict with lambda keys — lambda functions
    as dict keys have unstable identity across calls and can't be pickled.
    Now accepts a list of (condition, points) tuples for reliability.

New vs original
───────────────
  ─ generate_batch_code()    FMR-BCH-AS-2025-83421 format for traceability
  ─ generate_trace_code()    TRC-GH-2025-47823 format for QR codes
  ─ format_currency()        "GHS 1,234.50" display formatting
  ─ mask_phone()             "+233241234***" PII masking
  ─ mask_email()             "kw***@gmail.com" PII masking
  ─ chunk_list()             split list into sub-lists of size n
  ─ deep_merge()             recursive dict merge
  ─ retry()                  decorator for flaky external calls
  ─ flatten()                flatten nested list one level deep
  ─ truncate_text()          safe text truncation with ellipsis
  ─ is_valid_uuid()          validate UUID string format
  ─ safe_int() / safe_float() silent cast with default
"""
from __future__ import annotations

import csv
import hashlib
import io
import math
import secrets
import string
import time
import uuid
from decimal import Decimal, ROUND_HALF_UP
from functools import wraps
from typing import Any, Callable, Iterable, Optional
from django.utils import timezone


# =============================================================================
# CODE GENERATION  (FIX: secrets.randbelow replaces random.randint)
# =============================================================================

def generate_code(prefix: str, region: str = "", digits: int = 5) -> str:
    """
    Generate a human-readable, cryptographically random unique code.

    FIX vs original: random.randint → secrets.randbelow (unpredictable).

    Examples:
        generate_code("FMR", "Ashanti") → "FMR-AS-83421"
        generate_code("ORD")            → "ORD-73912"
    """
    region_part = f"-{region[:2].upper()}" if region else ""
    low         = 10 ** (digits - 1)
    high        = 10 ** digits
    number      = low + secrets.randbelow(high - low)
    return f"{prefix}{region_part}-{number}"


def generate_ref(prefix: str, year: bool = True, digits: int = 6) -> str:
    """
    Generate a reference number with optional year component.

    Examples:
        generate_ref("PAY")             → "PAY-2025-847392"
        generate_ref("BCH", year=False) → "BCH-394827"
    """
    year_part = f"-{timezone.now().year}" if year else ""
    low       = 10 ** (digits - 1)
    high      = 10 ** digits
    number    = low + secrets.randbelow(high - low)
    return f"{prefix}{year_part}-{number}"


def generate_otp(length: int = 6) -> str:
    """
    Generate a numeric OTP using cryptographically secure randomness.
    FIX: random.choices → secrets.choice
    """
    return "".join(secrets.choice(string.digits) for _ in range(length))


def generate_access_token(length: int = 32) -> str:
    """Generate a secure random URL-safe access token."""
    return secrets.token_urlsafe(length)


# =============================================================================
# TRACEABILITY-SPECIFIC CODE GENERATORS  (new)
# =============================================================================

def generate_batch_code(
    batch_type: str,
    region: str = "",
    farmer_code: str = "",
) -> str:
    """
    Generate a farmer / warehouse / product batch code for traceability.

    Format:
        Farmer batch:    FMR-BCH-AS-2025-83421
        Warehouse batch: WH-BCH-AS-2025-73912
        Product batch:   PRD-BCH-2025-47823

    batch_type: "farmer" | "warehouse" | "product"
    region:     2-char region initials (optional)
    farmer_code: appended to farmer batches for direct traceability
    """
    year       = timezone.now().year
    region_tag = f"-{region[:2].upper()}" if region else ""
    suffix     = 10000 + secrets.randbelow(90000)

    prefix_map = {
        "farmer":    "FMR-BCH",
        "warehouse": "WH-BCH",
        "product":   "PRD-BCH",
    }
    prefix = prefix_map.get(batch_type, "BCH")
    code   = f"{prefix}{region_tag}-{year}-{suffix}"

    if batch_type == "farmer" and farmer_code:
        code = f"{code}-{farmer_code}"

    return code


def generate_trace_code(
    farmer_code: str = "",
    region: str = "",
) -> str:
    """
    Generate a master traceability code embedded in QR codes.

    Format: TRC-GH-{year}-{5digits}[-{farmer_code}]

    Example:
        generate_trace_code("FMR-AS-83421", "Ashanti")
        → "TRC-GH-2025-47823-FMR-AS-83421"
    """
    year    = timezone.now().year
    country = "GH"
    suffix  = 10000 + secrets.randbelow(90000)
    code    = f"TRC-{country}-{year}-{suffix}"
    if farmer_code:
        code = f"{code}-{farmer_code}"
    return code


def generate_farmer_code(region: str = "", district: str = "") -> str:
    """
    Generate a farmer code: FMR-{region}-{district}-{5digits}

    Example:
        generate_farmer_code("Ashanti", "Kumasi") → "FMR-AS-KU-83421"
    """
    r = f"-{region[:2].upper()}"   if region   else ""
    d = f"-{district[:2].upper()}" if district else ""
    n = 10000 + secrets.randbelow(90000)
    return f"FMR{r}{d}-{n}"


# =============================================================================
# GEO UTILITIES
# =============================================================================

def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in metres between two GPS points."""
    R   = 6_371_000
    φ1  = math.radians(lat1)
    φ2  = math.radians(lat2)
    Δφ  = math.radians(lat2 - lat1)
    Δλ  = math.radians(lon2 - lon1)
    a   = math.sin(Δφ / 2) ** 2 + math.cos(φ1) * math.cos(φ2) * math.sin(Δλ / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def validate_polygon(polygon: list) -> tuple[bool, str]:
    """
    Validate a GeoJSON polygon ([lon, lat] pairs).
    Returns (is_valid: bool, error_message: str).
    """
    if not polygon or not isinstance(polygon, list):
        return False, "Polygon must be a non-empty list."
    if len(polygon) < 4:
        return False, "Polygon must have at least 4 points (including closing point)."
    if polygon[0] != polygon[-1]:
        return False, "Polygon ring must be closed (first and last points must be equal)."
    for i, pt in enumerate(polygon):
        if not isinstance(pt, (list, tuple)) or len(pt) != 2:
            return False, f"Point {i} must be [longitude, latitude]."
        lon, lat = pt
        if not (-180 <= lon <= 180):
            return False, f"Point {i}: longitude {lon} out of range [-180, 180]."
        if not (-90 <= lat <= 90):
            return False, f"Point {i}: latitude {lat} out of range [-90, 90]."
    return True, ""


def bbox_from_polygon(polygon: list) -> Optional[dict]:
    """Bounding box of a GeoJSON polygon."""
    is_valid, _ = validate_polygon(polygon)
    if not is_valid:
        return None
    lons = [pt[0] for pt in polygon]
    lats = [pt[1] for pt in polygon]
    return {"min_lon": min(lons), "max_lon": max(lons),
            "min_lat": min(lats), "max_lat": max(lats)}


def polygon_area_m2(polygon: list) -> float:
    """Approximate polygon area in m² using Shoelace formula."""
    is_valid, _ = validate_polygon(polygon)
    if not is_valid:
        return 0.0
    DEG_TO_M = 111_319.9
    n = len(polygon) - 1
    area = 0.0
    for i in range(n):
        x0, y0 = polygon[i][0] * DEG_TO_M,       polygon[i][1] * DEG_TO_M
        x1, y1 = polygon[(i + 1) % n][0] * DEG_TO_M, polygon[(i + 1) % n][1] * DEG_TO_M
        area  += x0 * y1 - x1 * y0
    return abs(area) / 2.0


def polygon_area_hectares(polygon: list) -> float:
    return polygon_area_m2(polygon) / 10_000.0


def polygon_centroid(polygon: list) -> tuple[float, float] | None:
    """Return (lon, lat) centroid of a GeoJSON polygon."""
    is_valid, _ = validate_polygon(polygon)
    if not is_valid:
        return None
    pts = polygon[:-1]   # exclude closing duplicate
    lon = sum(pt[0] for pt in pts) / len(pts)
    lat = sum(pt[1] for pt in pts) / len(pts)
    return (lon, lat)


# =============================================================================
# CSV UTILITIES
# =============================================================================

def build_csv(headers: list[str], rows: list[list[Any]]) -> str:
    """Build a CSV string from headers and rows."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)
    return output.getvalue()


def build_csv_response(headers: list[str], rows: list[list[Any]], filename: str):
    """Return a Django HttpResponse with CSV content + BOM for Excel."""
    from django.http import HttpResponse
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("\ufeff")   # BOM
    response.write(build_csv(headers, rows))
    return response


# =============================================================================
# QR CODE UTILITIES
# =============================================================================

def build_qr_payload(code: str, base_url: str, extra: dict = None) -> dict:
    """Build a standardised QR code data payload."""
    payload = {
        "code":         code,
        "url":          f"{base_url.rstrip('/')}/{code}/",
        "system":       "FarmicleGrow-Trace",
        "generated_at": timezone.now().isoformat(),
    }
    if extra:
        payload.update(extra)
    return payload


def generate_qr_image(data: str) -> bytes:
    """
    Generate a QR code PNG image.
    Requires: pip install qrcode Pillow
    """
    try:
        import qrcode
        from io import BytesIO
        qr  = qrcode.QRCode(box_size=4, border=2)
        qr.add_data(data)
        qr.make(fit=True)
        img    = qr.make_image(fill_color="black", back_color="white")
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        return buffer.getvalue()
    except ImportError:
        raise ImportError("Install qrcode and Pillow: pip install qrcode Pillow")


# =============================================================================
# HASHING & TOKENS
# =============================================================================

def hash_value(value: str, salt: str = "") -> str:
    """SHA-256 hash a value — for anonymising PII in exports."""
    return hashlib.sha256(f"{salt}{value}".encode()).hexdigest()


# =============================================================================
# DATE / TIME HELPERS
# =============================================================================

def get_month_range(month: int, year: int) -> tuple:
    """Return (start_datetime, end_datetime) for a given month/year."""
    import calendar
    from datetime import datetime
    start    = timezone.make_aware(datetime(year, month, 1))
    last_day = calendar.monthrange(year, month)[1]
    end      = timezone.make_aware(datetime(year, month, last_day, 23, 59, 59))
    return start, end


def get_current_week_range() -> tuple:
    """Return (Monday 00:00, Sunday 23:59:59) for the current week."""
    from datetime import timedelta, datetime
    now   = timezone.now()
    start = now - timedelta(days=now.weekday())
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    end   = start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    return start, end


def human_readable_size(bytes_size: int) -> str:
    """Convert bytes to human-readable string."""
    for unit in ["B", "KB", "MB", "GB"]:
        if bytes_size < 1024:
            return f"{bytes_size:.1f} {unit}"
        bytes_size //= 1024
    return f"{bytes_size:.1f} TB"


# =============================================================================
# PHONE / EMAIL NORMALISATION & MASKING  (new masking helpers)
# =============================================================================

def normalise_phone(phone: str, country_code: str = "+233") -> str:
    """
    Normalise to E.164 format.
    "0241234567" → "+233241234567"
    """
    phone = phone.strip().replace(" ", "").replace("-", "")
    if phone.startswith("+"):
        return phone
    if phone.startswith("0"):
        return f"{country_code}{phone[1:]}"
    return f"{country_code}{phone}"


def mask_phone(phone: str, visible_chars: int = 4) -> str:
    """
    Mask a phone number for safe display.
    "+233241234567" → "+233241234***" (last 3 masked)
    """
    if not phone or len(phone) <= visible_chars:
        return "***"
    return phone[:-visible_chars] + "*" * visible_chars


def mask_email(email: str) -> str:
    """
    Mask an email for safe display.
    "kwame.asante@gmail.com" → "kw***@gmail.com"
    """
    if not email or "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        masked_local = "*" * len(local)
    else:
        masked_local = local[:2] + "***"
    return f"{masked_local}@{domain}"


# =============================================================================
# CURRENCY FORMATTING  (new)
# =============================================================================

def format_currency(
    amount: float | Decimal,
    currency: str = "GHS",
    decimal_places: int = 2,
) -> str:
    """
    Format a monetary amount with currency symbol.

    Examples:
        format_currency(1234.5)           → "GHS 1,234.50"
        format_currency(500, "USD")       → "USD 500.00"
        format_currency(1000000, "GHS")   → "GHS 1,000,000.00"
    """
    try:
        quantize_str = "0." + "0" * decimal_places
        amount_d     = Decimal(str(amount)).quantize(
            Decimal(quantize_str), rounding=ROUND_HALF_UP
        )
        # Format with thousands separator
        formatted = f"{amount_d:,.{decimal_places}f}"
        return f"{currency} {formatted}"
    except Exception:
        return f"{currency} {amount}"


# =============================================================================
# COMPLETENESS SCORE  (FIX: list of tuples instead of dict with lambda keys)
# =============================================================================

def calculate_completeness(
    obj,
    field_weights: list[tuple[Any, int]] | dict[Any, int],
) -> int:
    """
    Calculate a profile completeness score (0–100).

    FIX vs original:
        Original used dict keys with lambda functions. Lambda functions as
        dict keys have unstable identity — two equal lambdas are not the same
        key. Also, dicts with lambda keys cannot be pickled (breaks caching).
        Now accepts a list of (condition, points) tuples — more explicit
        and serialisable.

    condition can be:
        "field_name"     — truthy check on getattr(obj, field_name)
        callable(obj)    — any callable that returns bool

    Example:
        calculate_completeness(farmer, [
            ("first_name",  10),
            ("national_id", 20),
            ("profile_photo", 10),
            (lambda f: f.farms.exists(), 20),
        ])
    """
    # Normalise dict input to list of tuples for backward compatibility
    if isinstance(field_weights, dict):
        field_weights = list(field_weights.items())

    score = 0
    for condition, points in field_weights:
        try:
            if callable(condition):
                if condition(obj):
                    score += points
            else:
                if getattr(obj, condition, None):
                    score += points
        except Exception:
            pass
    return min(score, 100)


# =============================================================================
# LIST UTILITIES  (new)
# =============================================================================

def chunk_list(lst: list, size: int) -> list[list]:
    """
    Split a list into sub-lists of at most `size` elements.

        chunk_list([1,2,3,4,5], 2) → [[1,2], [3,4], [5]]
    """
    return [lst[i: i + size] for i in range(0, len(lst), size)]


def flatten(nested: Iterable[Iterable]) -> list:
    """
    Flatten one level of nesting.

        flatten([[1,2], [3,4], [5]]) → [1,2,3,4,5]
    """
    return [item for sublist in nested for item in sublist]


def unique_preserve_order(lst: list) -> list:
    """Remove duplicates while preserving insertion order."""
    seen = set()
    return [x for x in lst if not (x in seen or seen.add(x))]


def deep_merge(base: dict, override: dict) -> dict:
    """
    Recursively merge `override` into `base`.
    `override` values win on conflict.

        deep_merge({"a": {"b": 1, "c": 2}}, {"a": {"c": 99, "d": 3}})
        → {"a": {"b": 1, "c": 99, "d": 3}}
    """
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = deep_merge(result[key], val)
        else:
            result[key] = val
    return result


# =============================================================================
# RETRY DECORATOR  (new)
# =============================================================================

def retry(
    max_attempts: int = 3,
    delay_seconds: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
):
    """
    Decorator that retries a function on exception with exponential backoff.
    Used for flaky external calls: SMS gateway, payment API, etc.

    Usage:
        @retry(max_attempts=3, delay_seconds=0.5, exceptions=(RequestException,))
        def send_sms(phone, message):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            delay   = delay_seconds
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt < max_attempts:
                        import logging as _log
                        _log.getLogger(__name__).warning(
                            "retry: %s failed (attempt %d/%d), retrying in %.1fs: %s",
                            func.__name__, attempt, max_attempts, delay, exc,
                        )
                        time.sleep(delay)
                        delay *= backoff
            raise last_exc
        return wrapper
    return decorator


# =============================================================================
# STRING UTILITIES  (new)
# =============================================================================

def truncate_text(text: str, max_length: int = 100, suffix: str = "…") -> str:
    """Safely truncate a string with a trailing ellipsis."""
    if not text or len(text) <= max_length:
        return text or ""
    return text[:max_length - len(suffix)] + suffix


def slugify_code(text: str) -> str:
    """Convert a text to a URL-safe code slug: 'Upper West' → 'upper-west'."""
    import re
    return re.sub(r"[^a-z0-9]+", "-", text.lower().strip()).strip("-")


def is_valid_uuid(value: str) -> bool:
    """Check if a string is a valid UUID."""
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError):
        return False


def safe_int(value: Any, default: int = 0) -> int:
    """Cast to int silently, returning default on failure."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    """Cast to float silently, returning default on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_decimal(value: Any, default: Decimal = Decimal("0.00")) -> Decimal:
    """Cast to Decimal silently."""
    try:
        return Decimal(str(value))
    except Exception:
        return default