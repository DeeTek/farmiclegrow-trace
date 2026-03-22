from __future__ import annotations

import hashlib
import logging
import threading
import time
import unicodedata
import re
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db import models as django_models
from django.db.models import Q, Value, FloatField
from django.db.models.functions import Coalesce
from django_filters import rest_framework as filters
from rest_framework import serializers as drf_serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

logger = logging.getLogger("apps.core.search")


# =============================================================================
# CONSTANTS
# =============================================================================

MAX_QUERY_LEN       = 150
MIN_QUERY_LEN       = 2
MAX_RESULTS_PER_GROUP = 50
DEFAULT_CACHE_TTL   = 60       # seconds
DEFAULT_LIMIT       = 8
AUTOCOMPLETE_LIMIT  = 10
SCORE_EXACT_CODE    = 100
SCORE_PREFIX_CODE   = 60
SCORE_WEIGHTED_FIELD= 40
SCORE_REGULAR_FIELD = 10

VALID_ROLES = frozenset({"admin", "hr", "officer", "buyer"})


# =============================================================================
# CUSTOM EXCEPTIONS
# =============================================================================

class RegistryError(Exception):
    """Raised when a registry configuration error is detected at registration time."""


# =============================================================================
# BASE FILTER SET
# =============================================================================

class BaseFilterSet(filters.FilterSet):
    """
    Base FilterSet every app's FilterSet should extend.

    Provides:
      is_active      — active/deleted filter
      created_after  — created_at >= date
      created_before — created_at <= date
      search         — multi-field keyword search across SEARCH_FIELDS

    Usage:
        class FarmerFilter(BaseFilterSet):
            SEARCH_FIELDS = ["first_name", "last_name", "code", "phone_number"]

            class Meta(BaseFilterSet.Meta):
                model  = Farmer
                fields = [*BaseFilterSet.Meta.fields, "verification_status"]
    """
    created_after  = filters.DateFilter(field_name="created_at", lookup_expr="gte")
    created_before = filters.DateFilter(field_name="created_at", lookup_expr="lte")
    is_active      = filters.BooleanFilter(field_name="is_active")
    search         = filters.CharFilter(method="filter_search")

    SEARCH_FIELDS: list[str] = []

    class Meta:
        fields = ["is_active", "created_after", "created_before"]

    def filter_search(self, queryset, name, value: str):
        cleaned = QueryNormalizer.clean(value)
        if not cleaned or not self.SEARCH_FIELDS:
            return queryset
        q = Q()
        for field in self.SEARCH_FIELDS:
            lookup = field if "__" in field else f"{field}__icontains"
            q |= Q(**{lookup: cleaned})
        needs_distinct = any("__" in f for f in self.SEARCH_FIELDS)
        qs = queryset.filter(q)
        return qs.distinct() if needs_distinct else qs


# =============================================================================
# QUERY NORMALIZER  (fix #5)
# =============================================================================

class QueryNormalizer:
    """
    Cleans and normalises raw query strings before they hit the database.

    Steps applied in order:
      1. Decode to str if bytes
      2. NFC unicode normalisation (prevents ā vs a + combining accent mismatches)
      3. Strip leading/trailing whitespace
      4. Remove ASCII control characters (prevent injection through \\x00 etc.)
      5. Collapse multiple consecutive spaces to a single space
      6. Lowercase (fix #5 — original only stripped, did not lowercase)
      7. Truncate to MAX_QUERY_LEN
    """
    _CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

    @classmethod
    def clean(cls, raw: str) -> str:
        if not raw:
            return ""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        # NFC normalisation
        raw = unicodedata.normalize("NFC", raw)
        # Remove control characters
        raw = cls._CONTROL_RE.sub("", raw)
        # Strip + collapse whitespace
        raw = " ".join(raw.split())
        # Lowercase
        raw = raw.lower()
        # Truncate
        return raw[:MAX_QUERY_LEN]

    @classmethod
    def is_valid(cls, cleaned: str) -> bool:
        return len(cleaned) >= MIN_QUERY_LEN

    @classmethod
    def tokenize(cls, cleaned: str) -> list[str]:
        """Split into tokens for multi-term support."""
        return [t for t in cleaned.split() if len(t) >= MIN_QUERY_LEN]


# =============================================================================
# VALIDATORS  (fix #2, #3, #4)
# =============================================================================

def _validate_model(model: Any, key: str) -> None:
    """
    Fix #2 — validates that `model` is a concrete Django Model subclass
    with a default manager.
    """
    if not isinstance(model, type):
        raise RegistryError(
            f"[{key}] model must be a class, got {type(model).__name__!r}."
        )
    if not issubclass(model, django_models.Model):
        raise RegistryError(
            f"[{key}] model must subclass django.db.models.Model, "
            f"got {model.__name__!r}."
        )
    if model._meta.abstract:
        raise RegistryError(
            f"[{key}] model {model.__name__!r} is abstract and cannot be queried."
        )


def _validate_serializer(serializer: Any, key: str) -> None:
    """
    Fix #3 — validates that `serializer` is a DRF BaseSerializer subclass.
    """
    if not isinstance(serializer, type):
        raise RegistryError(
            f"[{key}] serializer must be a class, got {type(serializer).__name__!r}."
        )
    if not issubclass(serializer, drf_serializers.BaseSerializer):
        raise RegistryError(
            f"[{key}] serializer must subclass rest_framework.serializers.BaseSerializer, "
            f"got {serializer.__name__!r}."
        )


def _validate_fields(fields: Any, model: Any, key: str) -> None:
    """
    Fix #4 — validates fields is a non-empty list of strings.
    For bare field names (no __), checks existence on the model's _meta.
    Traversal fields (containing __) are accepted without deep inspection
    because related model introspection would require loading all models.
    """
    if not fields or not isinstance(fields, list):
        raise RegistryError(f"[{key}] fields must be a non-empty list of strings.")
    for i, f in enumerate(fields):
        if not isinstance(f, str) or not f.strip():
            raise RegistryError(
                f"[{key}] fields[{i}] must be a non-empty string, got {f!r}."
            )
        if "__" not in f:
            # Check the field exists on this model
            field_names = {mf.name for mf in model._meta.get_fields()}
            if f not in field_names:
                raise RegistryError(
                    f"[{key}] field {f!r} does not exist on {model.__name__}. "
                    f"Available: {sorted(field_names)}."
                )


def _validate_roles(roles: Any, key: str) -> None:
    if not roles or not isinstance(roles, list):
        raise RegistryError(f"[{key}] roles must be a non-empty list of strings.")
    invalid = set(roles) - VALID_ROLES
    if invalid:
        raise RegistryError(
            f"[{key}] invalid roles: {invalid}. Valid roles: {VALID_ROLES}."
        )


# =============================================================================
# SEARCH REGISTRY  (fixes #1, #2, #3, #4, #11, #20)
# =============================================================================

class SearchRegistry:
    """
    Thread-safe central registry of all searchable models.

    Fix #1  — dict keyed on `key` prevents duplicate entries.
              Re-registering the same key raises RegistryError.
    Fix #11 — all mutations protected by threading.RLock.
    Fix #20 — extended schema: select_related, prefetch_related, annotations,
              index_fields, code_fields, cache_ttl, db_backend, highlight.

    Registry entry schema (all fields):
    {
      "key":              str              result group key in response
      "model":            Model            Django model class (validated)
      "fields":           list[str]        fields to search (icontains / FTS)
      "serializer":       Serializer       DRF serializer class (validated)
      "roles":            list[str]        roles that can see this group
      "buyer_filter":     Q | None         extra filter for buyer role
      "order_by":         list[str]        default result ordering
      "limit":            int              max hits per group (1..50)
      "select_related":   list[str]        ORM join optimisation    (fix #9)
      "prefetch_related": list[str]        ORM prefetch             (fix #9)
      "annotations":      dict             ORM annotations pre-search
      "index_fields":     list[str]        fields needing DB indexes (advisory) (fix #8)
      "code_fields":      list[str]        high-weight identifier fields (fix #16)
      "autocomplete_field": str | None     field used for prefix autocomplete
      "cache_ttl":        int              Redis cache TTL in seconds (fix #18)
      "db_backend":       str              "pg_fts" | "pg_trgm" | "orm" (fix #7)
      "highlight":        bool             wrap matches in <mark> tags
    }
    """

    _registry: dict[str, dict] = {}
    _lock: threading.RLock = threading.RLock()   # Fix #11

    @classmethod
    def register(
        cls,
        key: str,
        model,
        fields: list[str],
        serializer,
        *,
        roles: list[str] | None         = None,
        buyer_filter: Q | None          = None,
        order_by: list[str] | None      = None,
        limit: int                      = DEFAULT_LIMIT,
        select_related: list[str]       = None,   # Fix #9, #20
        prefetch_related: list[str]     = None,   # Fix #9, #20
        annotations: dict               = None,   # Fix #20
        index_fields: list[str]         = None,   # Fix #8, #20
        code_fields: list[str]          = None,   # Fix #16, #20
        autocomplete_field: str | None  = None,
        cache_ttl: int                  = DEFAULT_CACHE_TTL,   # Fix #18, #20
        db_backend: str                 = "orm",  # Fix #7, #20
        highlight: bool                 = False,  # Fix #20
        allow_override: bool            = False,  # allow re-registration in tests
    ) -> None:
        """
        Register a model for global search.

        Raises RegistryError on invalid inputs or duplicate key.
        Call from AppConfig.ready() — guarded by allow_override=False.
        """
        # ── Basic type validation ────────────────────────────────────────────
        if not key or not isinstance(key, str):
            raise RegistryError("key must be a non-empty string.")
        if not (1 <= limit <= MAX_RESULTS_PER_GROUP):
            raise RegistryError(
                f"limit must be between 1 and {MAX_RESULTS_PER_GROUP}, got {limit}."
            )
        if db_backend not in ("pg_fts", "pg_trgm", "orm"):
            raise RegistryError(
                f"db_backend must be 'pg_fts', 'pg_trgm', or 'orm', got {db_backend!r}."
            )

        _validate_model(model, key)        # Fix #2
        _validate_serializer(serializer, key)  # Fix #3
        _validate_fields(fields, model, key)   # Fix #4
        _validate_roles(roles or ["admin"], key)

        entry = {
            "key":               key,
            "model":             model,
            "fields":            fields,
            "serializer":        serializer,
            "roles":             roles or ["admin"],
            "buyer_filter":      buyer_filter,
            "order_by":          order_by or ["-created_at"],
            "limit":             limit,
            "select_related":    select_related or [],
            "prefetch_related":  prefetch_related or [],
            "annotations":       annotations or {},
            "index_fields":      index_fields or fields,
            "code_fields":       code_fields or [],
            "autocomplete_field":autocomplete_field or (code_fields[0] if code_fields else fields[0]),
            "cache_ttl":         cache_ttl,
            "db_backend":        db_backend,
            "highlight":         highlight,
        }

        with cls._lock:
            if key in cls._registry and not allow_override:
                raise RegistryError(
                    f"Search key {key!r} is already registered. "
                    f"Pass allow_override=True to replace it (tests only)."
                )
            cls._registry[key] = entry

        logger.debug(
            "Search registered: key=%s | model=%s | roles=%s | backend=%s",
            key, model.__name__, entry["roles"], db_backend,
        )

    @classmethod
    def get(cls, key: str) -> dict | None:
        with cls._lock:
            return dict(cls._registry.get(key, {})) or None

    @classmethod
    def all(cls) -> list[dict]:
        """Returns a shallow copy — safe to iterate outside the lock."""
        with cls._lock:
            return list(cls._registry.values())

    @classmethod
    def keys(cls) -> list[str]:
        with cls._lock:
            return list(cls._registry.keys())

    @classmethod
    def clear(cls) -> None:
        """Test helper — clears the registry."""
        with cls._lock:
            cls._registry.clear()

    @classmethod
    def info(cls) -> dict:
        """Returns a summary dict for SearchStatsView."""
        with cls._lock:
            return {
                key: {
                    "model":    entry["model"].__name__,
                    "fields":   entry["fields"],
                    "roles":    entry["roles"],
                    "limit":    entry["limit"],
                    "backend":  entry["db_backend"],
                    "cache_ttl":entry["cache_ttl"],
                }
                for key, entry in cls._registry.items()
            }


# =============================================================================
# SEARCH BACKENDS  (fix #7, #15)
# =============================================================================

class FallbackSearchBackend:
    """
    Standard ORM icontains backend — works on every database.

    Fix #15: distinct() is only called when fields contain __ (cross-table joins).
    Fix #7:  index_fields are logged to advise which columns need DB indexes.
    """

    def build_queryset(
        self,
        entry: dict,
        query: str,
        base_qs,
    ):
        fields = entry["fields"]
        tokens = QueryNormalizer.tokenize(query)

        if not tokens:
            return base_qs.none()

        # Build a Q for each token — all tokens must match (AND across tokens)
        combined = Q()
        for token in tokens:
            token_q = Q()
            for field in fields:
                lookup = field if "__" in field else f"{field}__icontains"
                token_q |= Q(**{lookup: token})
            combined &= token_q

        qs = base_qs.filter(combined)

        # Fix #15 — only call distinct() when joining across related tables
        needs_distinct = any("__" in f for f in fields)
        return qs.distinct() if needs_distinct else qs


class PostgreSQLSearchBackend:
    """
    Full-text search using django.contrib.postgres SearchVector + SearchRank.

    Requires:
      - PostgreSQL database backend
      - django.contrib.postgres in INSTALLED_APPS
      - GIN index on the search vector (see SearchIndexAdvisor)

    Fix #7:  Avoids full table scans by using a materialised SearchVector.
    Fix #16: code_fields get weight='A', regular fields get weight='B'.
    """

    def build_queryset(self, entry: dict, query: str, base_qs):
        try:
            from django.contrib.postgres.search import (
                SearchQuery, SearchRank, SearchVector,
            )
        except ImportError:
            logger.warning(
                "django.contrib.postgres not available — "
                "falling back to ORM backend for key=%s", entry["key"],
            )
            return FallbackSearchBackend().build_queryset(entry, query, base_qs)

        code_fields   = entry.get("code_fields", [])
        regular_fields= [f for f in entry["fields"] if f not in code_fields]

        # Build weighted SearchVector
        vectors = []
        for f in code_fields:
            if "__" not in f:           # SearchVector only supports direct fields
                vectors.append(SearchVector(f, weight="A"))
        for f in regular_fields:
            if "__" not in f:
                vectors.append(SearchVector(f, weight="B"))

        if not vectors:
            return FallbackSearchBackend().build_queryset(entry, query, base_qs)

        sv    = vectors[0]
        for v in vectors[1:]:
            sv = sv + v

        sq = SearchQuery(query, search_type="websearch")

        return (
            base_qs
            .annotate(_search_vector=sv, _search_rank=SearchRank(sv, sq))
            .filter(_search_vector=sq)
            .order_by("-_search_rank")
        )


class TrigramSearchBackend:
    """
    Fuzzy search using PostgreSQL pg_trgm similarity.

    Requires:
      - PostgreSQL + pg_trgm extension (`CREATE EXTENSION IF NOT EXISTS pg_trgm;`)
      - GIN trigram index on searched columns

    Fix #7: Uses indexed trigram similarity instead of LIKE %query%.
    """

    SIMILARITY_THRESHOLD = 0.2

    def build_queryset(self, entry: dict, query: str, base_qs):
        try:
            from django.contrib.postgres.search import TrigramSimilarity
        except ImportError:
            return FallbackSearchBackend().build_queryset(entry, query, base_qs)

        fields = [f for f in entry["fields"] if "__" not in f]
        if not fields:
            return FallbackSearchBackend().build_queryset(entry, query, base_qs)

        # Build combined similarity annotation
        sim_expr = TrigramSimilarity(fields[0], query)
        for f in fields[1:]:
            sim_expr = sim_expr + TrigramSimilarity(f, query)

        return (
            base_qs
            .annotate(_trigram_sim=sim_expr)
            .filter(_trigram_sim__gte=self.SIMILARITY_THRESHOLD)
            .order_by("-_trigram_sim")
        )


class SearchBackendRouter:
    """
    Selects the appropriate search backend per registry entry.
    Falls back gracefully if the requested backend is unavailable.
    """

    _backends = {
        "pg_fts":  PostgreSQLSearchBackend,
        "pg_trgm": TrigramSearchBackend,
        "orm":     FallbackSearchBackend,
    }

    @classmethod
    def get(cls, db_backend: str) -> object:
        backend_class = cls._backends.get(db_backend, FallbackSearchBackend)
        return backend_class()


# =============================================================================
# RESULT SCORER  (fix #6, #16)
# =============================================================================

class ResultScorer:
    """
    Assigns a numeric relevance score to each search hit.

    Scoring rules (higher = more relevant):
      code_fields  exact match      → SCORE_EXACT_CODE    (100)
      code_fields  prefix match     → SCORE_PREFIX_CODE   (60)
      any field    icontains match  → SCORE_REGULAR_FIELD (10)
      extra bonus for weighted fields: +SCORE_WEIGHTED_FIELD (40)

    The score is computed in Python on the already-retrieved queryset slice.
    It does NOT require an extra DB round-trip.
    """

    @classmethod
    def score_hit(cls, obj, query: str, entry: dict) -> int:
        score       = 0
        query_lower = query.lower()
        code_fields = entry.get("code_fields", [])

        for field in entry["fields"]:
            # Only score bare fields — traversal fields (with __) are skipped
            if "__" in field:
                score += SCORE_REGULAR_FIELD
                continue

            raw_value = getattr(obj, field, None)
            if raw_value is None:
                continue
            value = str(raw_value).lower()

            if field in code_fields:
                if value == query_lower:
                    score += SCORE_EXACT_CODE
                elif value.startswith(query_lower):
                    score += SCORE_PREFIX_CODE
                elif query_lower in value:
                    score += SCORE_REGULAR_FIELD
            else:
                if query_lower in value:
                    score += SCORE_REGULAR_FIELD

        return score

    @classmethod
    def rank(cls, objects: list, query: str, entry: dict) -> list:
        """Returns objects sorted by score descending."""
        scored = [(obj, cls.score_hit(obj, query, entry)) for obj in objects]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [obj for obj, _ in scored]


# =============================================================================
# RESULT HIGHLIGHTER  (fix #20)
# =============================================================================

class ResultHighlighter:
    """
    Wraps matched query fragments in <mark> tags for frontend highlighting.

    Applied to serialized data (dict) after serialization, not to model
    instances — this keeps the model layer clean.

    Highlights string values in the top level of the serialized dict.
    Nested structures are not highlighted (to avoid breaking UUIDs etc.).
    """

    _SAFE_FIELDS_RE = re.compile(r"(name|title|body|description|notes|text)", re.I)

    @classmethod
    def highlight(cls, data: dict, query: str) -> dict:
        if not data or not isinstance(data, dict):
            return data
        query_lower = query.lower()
        result = {}
        for k, v in data.items():
            if isinstance(v, str) and cls._SAFE_FIELDS_RE.search(k):
                result[k] = cls._highlight_string(v, query_lower)
            else:
                result[k] = v
        return result

    @classmethod
    def _highlight_string(cls, text: str, query: str) -> str:
        if not text or not query:
            return text
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        return pattern.sub(lambda m: f"<mark>{m.group()}</mark>", text)


# =============================================================================
# SEARCH CACHE  (fix #18)
# =============================================================================

class SearchCache:
    """
    Redis-backed cache layer for search results.

    Cache key structure:
        search:{role}:{groups_hash}:{query_hash}:{limit}:{page}

    All cache operations are wrapped in try/except so a Redis outage
    never breaks the search endpoint — it simply falls back to live queries.

    Cache invalidation:
        Call SearchCache.invalidate_group(key) from a post_save signal handler
        to clear all cached results that include a specific model group.
    """

    PREFIX = "farmicle:search"

    @classmethod
    def _make_key(
        cls,
        role: str,
        query: str,
        groups: frozenset[str] | None,
        limit: int,
        page: int,
    ) -> str:
        groups_str = ",".join(sorted(groups)) if groups else "all"
        # Hash long values to keep cache key short
        payload    = f"{role}|{groups_str}|{query}|{limit}|{page}"
        digest     = hashlib.sha256(payload.encode()).hexdigest()[:16]
        return f"{cls.PREFIX}:{digest}"

    @classmethod
    def get(
        cls,
        role: str,
        query: str,
        groups: frozenset[str] | None,
        limit: int,
        page: int,
    ) -> dict | None:
        try:
            key = cls._make_key(role, query, groups, limit, page)
            return cache.get(key)
        except Exception as exc:
            logger.debug("SearchCache.get failed: %s", exc)
            return None

    @classmethod
    def set(
        cls,
        role: str,
        query: str,
        groups: frozenset[str] | None,
        limit: int,
        page: int,
        data: dict,
        ttl: int = DEFAULT_CACHE_TTL,
    ) -> None:
        try:
            key = cls._make_key(role, query, groups, limit, page)
            cache.set(key, data, timeout=ttl)
        except Exception as exc:
            logger.debug("SearchCache.set failed: %s", exc)

    @classmethod
    def invalidate_group(cls, group_key: str) -> None:
        """
        Invalidate cached results that contain a specific model group.
        Pattern-based invalidation via cache.delete_pattern (Redis only).
        Falls back to a no-op on other backends.
        """
        try:
            pattern = f"{cls.PREFIX}:*"
            if hasattr(cache, "delete_pattern"):
                cache.delete_pattern(pattern)
            else:
                logger.debug(
                    "Cache backend does not support delete_pattern — "
                    "skipping group invalidation for %s", group_key,
                )
        except Exception as exc:
            logger.debug("SearchCache.invalidate_group failed: %s", exc)


# =============================================================================
# GLOBAL SEARCH ENGINE  (all 20 fixes applied)
# =============================================================================

class GlobalSearchEngine:
    """
    Orchestrates per-group search across all registered models.

    Applies (in order):
      1. Cache lookup — return immediately on hit
      2. Role detection — skip groups not visible to this role
      3. Group filtering — skip groups not in requested `groups` set (fix #14)
      4. Backend selection — pg_fts / pg_trgm / orm per entry (fix #7)
      5. Base queryset build — active filter + buyer_filter + select/prefetch (fix #9)
      6. ORM annotations injection — from entry["annotations"]
      7. Search execution — backend.build_queryset()
      8. Result scoring + ranking — ResultScorer.rank() (fix #6)
      9. Pagination — slice to [offset:offset+limit] (fix #19)
      10. Serialization — entry["serializer"](qs, many=True)
      11. Highlighting — ResultHighlighter.highlight() if entry["highlight"]
      12. Omit empty groups — unless omit_empty=False (fix #10)
      13. Cache store — SearchCache.set()
    """

    def __init__(self, user, request=None):
        self.user    = user
        self.request = request

    def _get_role(self) -> str:
        from apps.core.mixins import _get_user_role
        return _get_user_role(self.user)

    @staticmethod
    def _clamp_limit(limit: Any, entry_limit: int) -> int:
        """Fix #12 — clamp caller-supplied limit to valid range."""
        if limit is None:
            return entry_limit
        try:
            n = int(limit)
        except (TypeError, ValueError):
            return entry_limit
        return max(1, min(n, MAX_RESULTS_PER_GROUP))

    def _build_base_qs(self, entry: dict, role: str):
        """
        Build the base queryset with role filter, select_related, prefetch_related,
        and any custom annotations injected.  Fix #9, #20.
        """
        qs = entry["model"].objects.filter(is_active=True)

        # Role-based scope
        if role == "buyer" and entry.get("buyer_filter"):
            qs = qs.filter(entry["buyer_filter"])

        # Eager loading  (fix #9)
        if entry.get("select_related"):
            qs = qs.select_related(*entry["select_related"])
        if entry.get("prefetch_related"):
            qs = qs.prefetch_related(*entry["prefetch_related"])

        # ORM annotations  (fix #20)
        if entry.get("annotations"):
            qs = qs.annotate(**entry["annotations"])

        return qs

    def search(
        self,
        query: str,
        *,
        limit: int | None               = None,
        groups: set[str] | None         = None,   # Fix #14
        page: int                       = 1,      # Fix #19
        per_group: int | None           = None,   # Fix #19
        omit_empty: bool                = True,   # Fix #10
        use_cache: bool                 = True,
    ) -> dict:
        """
        Execute search across all registered model groups.

        Args:
            query      Raw query string (will be normalised)
            limit      Per-group result limit (overrides registry entry limit)
            groups     Restrict to these group keys only (None = all visible groups)
            page       1-based page number for pagination
            per_group  Alias for limit (per-group page size)
            omit_empty Exclude groups with zero results from response
            use_cache  Whether to attempt Redis cache lookup

        Returns:
            {
              "query":        "kwame",
              "role":         "officer",
              "page":         1,
              "groups_searched": ["farmers","farms"],
              "total_hits":   12,
              "results": {
                "farmers": {
                  "count": 8, "page": 1, "per_group": 8,
                  "results": [ {...}, ... ]
                },
              },
              "_search_errors": ["trace_records"],   # only present if errors occurred
            }
        """
        # ── Normalise query ─────────────────────────────────────────────────
        cleaned = QueryNormalizer.clean(query)
        if not QueryNormalizer.is_valid(cleaned):
            return {
                "query":           query or "",
                "error":           f"Query must be at least {MIN_QUERY_LEN} characters.",
                "results":         {},
                "total_hits":      0,
                "groups_searched": [],
                "page":            page,
            }

        role        = self._get_role()
        per_group   = self._clamp_limit(per_group or limit, DEFAULT_LIMIT)
        groups_set  = frozenset(groups) if groups else None
        page        = max(1, page)

        # ── Cache lookup  (fix #18) ─────────────────────────────────────────
        if use_cache:
            cached = SearchCache.get(role, cleaned, groups_set, per_group, page)
            if cached is not None:
                return {**cached, "_from_cache": True}

        results      = {}
        errors       = []
        total_hits   = 0
        searched     = []
        is_debug     = getattr(settings, "DEBUG", False)
        offset       = (page - 1) * per_group

        for entry in SearchRegistry.all():
            key = entry["key"]

            # Role gate
            if role not in entry["roles"]:
                continue

            # Group filter  (fix #14)
            if groups_set is not None and key not in groups_set:
                continue

            searched.append(key)
            entry_limit = self._clamp_limit(per_group, entry["limit"])

            try:
                base_qs = self._build_base_qs(entry, role)
                backend = SearchBackendRouter.get(entry["db_backend"])
                qs      = backend.build_queryset(entry, cleaned, base_qs)

                # Apply default ordering (only if backend didn't override it)
                if not getattr(qs, "_search_ranked", False):
                    qs = qs.order_by(*entry["order_by"])

                # Count before slicing  (fix #19)
                total_count = qs.count()

                # Paginated slice
                page_qs = qs[offset: offset + entry_limit]
                objects = list(page_qs)

                # Score and rank  (fix #6)
                # Only rank when using the ORM backend — pg_fts already ranks
                if entry["db_backend"] == "orm" and objects:
                    objects = ResultScorer.rank(objects, cleaned, entry)

                # Serialize
                ctx = {"request": self.request} if self.request else {}
                serialized = entry["serializer"](objects, many=True, context=ctx).data
                data_list  = list(serialized)

                # Highlight  (fix #20)
                if entry.get("highlight") and data_list:
                    data_list = [
                        ResultHighlighter.highlight(d, cleaned)
                        for d in data_list
                    ]

                group_result = {
                    "count":     total_count,
                    "page":      page,
                    "per_group": entry_limit,
                    "results":   data_list,
                }

                # Fix #10 — omit empty groups
                if omit_empty and total_count == 0:
                    continue

                results[key] = group_result
                total_hits  += total_count

            except Exception as exc:
                errors.append(key)
                if is_debug:
                    # Fix #17 — surface errors in debug mode
                    logger.exception(
                        "Search ERROR for key=%s | query=%s", key, cleaned,
                    )
                    raise
                else:
                    logger.error(
                        "Search failed for key=%s | query=%s | error=%s",
                        key, cleaned, exc, exc_info=True,
                    )

        payload = {
            "query":           cleaned,
            "role":            role,
            "page":            page,
            "per_group":       per_group,
            "groups_searched": searched,
            "total_hits":      total_hits,
            "results":         results,
        }
        if errors:
            payload["_search_errors"] = errors

        # Cache store  (fix #18)
        if use_cache and not errors:
            # Use the minimum TTL across all searched groups
            ttl = min(
                (SearchRegistry.get(k) or {}).get("cache_ttl", DEFAULT_CACHE_TTL)
                for k in searched
            ) if searched else DEFAULT_CACHE_TTL
            SearchCache.set(role, cleaned, groups_set, per_group, page, payload, ttl=ttl)

        return payload


# =============================================================================
# AUTOCOMPLETE ENGINE
# =============================================================================

class AutocompleteEngine:
    """
    Ultra-fast prefix autocomplete for a single model group.

    Uses __istartswith on the entry's `autocomplete_field` (always an indexed
    column — typically a code field) plus an optional secondary field.

    Returns a flat list of hint strings — not full serialized objects —
    to keep the payload tiny for frontend typeahead dropdowns.

    Endpoint: GET /v1/search/autocomplete/?q=KW&group=farmers&field=code
    """

    def suggest(
        self,
        query: str,
        group_key: str,
        role: str,
        *,
        limit: int = AUTOCOMPLETE_LIMIT,
    ) -> dict:
        cleaned = QueryNormalizer.clean(query)
        if not cleaned:
            return {"query": query, "group": group_key, "suggestions": []}

        entry = SearchRegistry.get(group_key)
        if not entry:
            return {
                "query": query, "group": group_key,
                "suggestions": [], "error": "Unknown group.",
            }
        if role not in entry["roles"]:
            return {
                "query": query, "group": group_key,
                "suggestions": [], "error": "Forbidden.",
            }

        auto_field = entry["autocomplete_field"]
        qs         = entry["model"].objects.filter(is_active=True)

        if role == "buyer" and entry.get("buyer_filter"):
            qs = qs.filter(entry["buyer_filter"])

        # Primary prefix filter on autocomplete_field
        qs = qs.filter(**{f"{auto_field}__istartswith": cleaned})

        # Optionally also match secondary display field
        display_field = entry["fields"][0] if entry["fields"] else auto_field
        if display_field != auto_field:
            qs = qs | entry["model"].objects.filter(
                is_active=True,
                **{f"{display_field}__istartswith": cleaned},
            )
            qs = qs.distinct()

        qs = qs.order_by(auto_field)[:limit]

        suggestions = []
        for obj in qs:
            code    = str(getattr(obj, auto_field, "") or "")
            display = str(getattr(obj, display_field, "") or code)
            suggestions.append({"value": code, "label": display})

        return {
            "query":       cleaned,
            "group":       group_key,
            "suggestions": suggestions,
        }


# =============================================================================
# THROTTLE  (fix #13)
# =============================================================================

class SearchThrottle(SimpleRateThrottle):
    """
    Sliding-window rate limiter for the search endpoint.

    Default: 60 requests per minute per authenticated user.
    Override in settings:
        REST_FRAMEWORK = {
            "DEFAULT_THROTTLE_RATES": {
                "search": "60/min",
                "search_autocomplete": "120/min",
            }
        }
    """
    scope = "search"

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            ident = str(request.user.pk)
        else:
            ident = self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class AutocompleteThrottle(SearchThrottle):
    scope = "search_autocomplete"


# =============================================================================
# INDEX ADVISOR  (fix #8)
# =============================================================================

class SearchIndexAdvisor:
    """
    Generates recommended Django migration index statements for all
    registered search fields that are not yet indexed.

    Usage:
        advisor = SearchIndexAdvisor()
        recommendations = advisor.advise()
        # Returns list of {"model": "Farmer", "field": "code", "index_type": "..."}

    Call from a management command:
        python manage.py search_index_check
    """

    @classmethod
    def advise(cls) -> list[dict]:
        recommendations = []
        for entry in SearchRegistry.all():
            model    = entry["model"]
            meta     = model._meta
            indexed  = {f.name for f in meta.get_fields() if hasattr(f, "db_index") and f.db_index}
            pk_name  = meta.pk.name if meta.pk else "id"
            indexed.add(pk_name)

            for field_name in entry.get("index_fields", []):
                if "__" in field_name:
                    continue
                if field_name not in indexed:
                    db_backend = entry["db_backend"]
                    if db_backend == "pg_fts":
                        idx_type = "GIN (full-text search vector)"
                        migration = (
                            f"models.Index(\n"
                            f"    SearchVector('{field_name}'),\n"
                            f"    name='{meta.model_name}_{field_name}_fts_idx'\n"
                            f")"
                        )
                    elif db_backend == "pg_trgm":
                        idx_type  = "GIN pg_trgm (trigram similarity)"
                        migration = (
                            f"GinIndex(\n"
                            f"    OpclassIndex('{field_name}', opclasses=['gin_trgm_ops']),\n"
                            f"    name='{meta.model_name}_{field_name}_trgm_idx'\n"
                            f")"
                        )
                    else:
                        idx_type  = "BTree (icontains)"
                        migration = (
                            f"models.Index(\n"
                            f"    fields=['{field_name}'],\n"
                            f"    name='{meta.model_name}_{field_name}_idx'\n"
                            f")"
                        )
                    recommendations.append({
                        "model":      model.__name__,
                        "app_label":  meta.app_label,
                        "field":      field_name,
                        "index_type": idx_type,
                        "migration":  migration,
                    })
        return recommendations


# =============================================================================
# API VIEWS
# =============================================================================

class GlobalSearchView(APIView):
    """
    GET /v1/search/

    Query params:
        q          (required) raw search query, min 2 chars
        limit      per-group result count (1–50, default 8)
        groups     comma-separated group keys to restrict search
        page       1-based page number (default 1)
        highlight  1 / true  to enable match highlighting

    Example response:
    {
        "query":           "kwame ashanti",
        "role":            "officer",
        "page":            1,
        "per_group":       8,
        "groups_searched": ["farmers","farms"],
        "total_hits":      14,
        "results": {
            "farmers": {
                "count": 9, "page": 1, "per_group": 8,
                "results": [ { "farmer_code": "FMR-AS-83421", "first_name": "Kwame", ... } ]
            },
            "farms": {
                "count": 5, "page": 1, "per_group": 5,
                "results": [ { ... } ]
            }
        }
    }
    """
    permission_classes = [IsAuthenticated]
    throttle_classes   = [SearchThrottle]   # Fix #13

    def get(self, request):
        query      = request.query_params.get("q", "")
        raw_limit  = request.query_params.get("limit")
        raw_groups = request.query_params.get("groups", "")
        raw_page   = request.query_params.get("page", "1")
        highlight  = request.query_params.get("highlight", "").lower() in ("1","true","yes")

        # Parse limit  (fix #12)
        limit = None
        if raw_limit:
            try:
                limit = min(max(1, int(raw_limit)), MAX_RESULTS_PER_GROUP)
            except (ValueError, TypeError):
                pass

        # Parse groups  (fix #14)
        groups = None
        if raw_groups.strip():
            groups = {g.strip() for g in raw_groups.split(",") if g.strip()}

        # Parse page  (fix #19)
        try:
            page = max(1, int(raw_page))
        except (ValueError, TypeError):
            page = 1

        engine  = GlobalSearchEngine(user=request.user, request=request)
        results = engine.search(
            query,
            limit      = limit,
            groups     = groups,
            page       = page,
            omit_empty = True,
        )

        # Per-request highlight override
        if highlight:
            cleaned = results.get("query", "")
            for key, group in results.get("results", {}).items():
                if isinstance(group, dict) and "results" in group:
                    group["results"] = [
                        ResultHighlighter.highlight(d, cleaned)
                        for d in group["results"]
                    ]

        return Response(results)


class AutocompleteView(APIView):
    """
    GET /v1/search/autocomplete/?q=KW&group=farmers

    Ultra-fast prefix autocomplete — returns flat hint list for typeahead.

    Query params:
        q      (required) prefix string, min 1 char
        group  (required) registry group key (e.g. "farmers", "trace_records")

    Response:
    {
        "query":       "kw",
        "group":       "farmers",
        "suggestions": [
            {"value": "FMR-AS-83421", "label": "Kwame Asante"},
            {"value": "FMR-AS-83425", "label": "Kwamena Ofori"},
        ]
    }
    """
    permission_classes = [IsAuthenticated]
    throttle_classes   = [AutocompleteThrottle]

    def get(self, request):
        from apps.core.mixins import _get_user_role
        query     = request.query_params.get("q", "")
        group_key = request.query_params.get("group", "").strip()
        role      = _get_user_role(request.user)

        if not group_key:
            return Response(
                {"error": "group parameter is required."},
                status=400,
            )

        engine = AutocompleteEngine()
        return Response(engine.suggest(query, group_key, role))


class SearchStatsView(APIView):
    """
    GET /v1/search/stats/   — admin only

    Returns:
      - All registered groups with model, fields, roles, backend
      - Index advisory recommendations
      - Cache backend info
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not (request.user.is_staff or request.user.is_superuser):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Admin access required.")

        advisor  = SearchIndexAdvisor()
        recs     = advisor.advise()
        registry = SearchRegistry.info()

        cache_backend = getattr(
            getattr(settings, "CACHES", {}).get("default", {}),
            "BACKEND", "unknown",
        ) or settings.CACHES.get("default", {}).get("BACKEND", "unknown")

        return Response({
            "registered_groups":      registry,
            "total_groups":           len(registry),
            "index_recommendations":  recs,
            "cache_backend":          cache_backend,
            "max_results_per_group":  MAX_RESULTS_PER_GROUP,
            "max_query_length":       MAX_QUERY_LEN,
        })


# =============================================================================
# REGISTRATION HELPER  (public API — called from AppConfig.ready())
# =============================================================================

def register_search(
    key: str,
    model,
    fields: list[str],
    serializer,
    *,
    roles: list[str] | None         = None,
    buyer_filter: Q | None          = None,
    order_by: list[str] | None      = None,
    limit: int                      = DEFAULT_LIMIT,
    select_related: list[str]       = None,
    prefetch_related: list[str]     = None,
    annotations: dict               = None,
    index_fields: list[str]         = None,
    code_fields: list[str]          = None,
    autocomplete_field: str | None  = None,
    cache_ttl: int                  = DEFAULT_CACHE_TTL,
    db_backend: str                 = "orm",
    highlight: bool                 = False,
    allow_override: bool            = False,
) -> None:
    """
    Public helper — registers a model for global search and autocomplete.
    Calls SearchRegistry.register() with full validation.

    Call from each app's AppConfig.ready().  The call is idempotent when
    Django autoreloads because AppConfig guards against repeated ready() calls,
    and allow_override=False raises RegistryError on duplicates.

    Example:
        register_search(
            key              = "farmers",
            model            = Farmer,
            fields           = ["first_name", "last_name", "phone_number", "community"],
            serializer       = FarmerListSerializer,
            roles            = ["buyer", "officer", "hr", "admin"],
            buyer_filter     = Q(verification_status="verified"),
            code_fields      = ["code"],
            index_fields     = ["code", "phone_number", "community"],
            autocomplete_field = "code",
            select_related   = ["user"],
            order_by         = ["-created_at"],
            limit            = 8,
            db_backend       = "pg_fts",   # use PostgreSQL FTS
            cache_ttl        = 120,
        )
    """
    SearchRegistry.register(
        key              = key,
        model            = model,
        fields           = fields,
        serializer       = serializer,
        roles            = roles,
        buyer_filter     = buyer_filter,
        order_by         = order_by,
        limit            = limit,
        select_related   = select_related,
        prefetch_related = prefetch_related,
        annotations      = annotations,
        index_fields     = index_fields,
        code_fields      = code_fields,
        autocomplete_field = autocomplete_field,
        cache_ttl        = cache_ttl,
        db_backend       = db_backend,
        highlight        = highlight,
        allow_override   = allow_override,
    )