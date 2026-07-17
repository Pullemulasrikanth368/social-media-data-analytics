from copy import deepcopy
from datetime import datetime, timedelta, timezone
import logging
import threading

from django.conf import settings
from pymongo import MongoClient

from . import registry
from .analytics_utils import (
    aggregate_by_period,
    aggregate_metrics,
    build_comparisons,
    build_dashboard_rows,
    ctr_percent,
    default_date_range,
    engagement_rate,
    METRIC_FIELDS,
    canonical_content_type,
    normalize_audience_dashboard_row,
    parse_date,
    rows_between,
    total_engagements,
)

logger = logging.getLogger(__name__)

client = MongoClient(settings.MONGO_URI)
# The database keeps its historical name for backward compatibility (existing
# LinkedIn data lives here); it is now the shared multi-platform analytics DB,
# with a ``platform`` field discriminating rows. Override via MONGO_DB_NAME.
db = client[getattr(settings, "MONGO_DB_NAME", "linkedin_db")]

analytics_collection = db["analytics"]
tokens_collection = db["tokens"]
dashboard_cache_collection = db["dashboard_cache"]

# Legacy rows written before multi-platform support have no ``platform`` field;
# treat them as LinkedIn so the existing dashboard keeps reading them.
LEGACY_PLATFORM = registry.DEFAULT_PLATFORM


def _platform_query(platform):
    """Mongo filter selecting one platform's rows. For the default (LinkedIn)
    platform it also matches legacy rows that predate the ``platform`` field."""
    platform = registry.normalize_platform(platform)
    if platform == LEGACY_PLATFORM:
        return {"$or": [{"platform": platform}, {"platform": {"$exists": False}}]}
    return {"platform": platform}


def _ensure_indexes():
    """Create indexes needed by the read/write paths. Idempotent and best-effort:
    a MongoDB outage at import time must not crash Django startup."""
    # The dashboard-cache uniqueness now includes platform + account; drop the
    # legacy (start,end,granularity) unique index if it lingers so the new key
    # can be created without a conflict. The cache is ephemeral, so rebuilding
    # it costs at most a cache miss.
    try:
        for name, spec in (dashboard_cache_collection.index_information() or {}).items():
            keys = [k for k, _ in spec.get("key", [])]
            if spec.get("unique") and keys == ["start", "end", "granularity"]:
                dashboard_cache_collection.drop_index(name)
    except Exception:
        logger.exception("Could not reconcile legacy dashboard_cache index")

    specs = [
        (analytics_collection, [("platform", 1), ("organization_id", 1), ("date", 1)], {}),
        (analytics_collection, [("date", 1)], {}),
        (tokens_collection, [("platform", 1), ("created_at", -1)], {}),
        (dashboard_cache_collection,
         [("platform", 1), ("account_id", 1), ("start", 1), ("end", 1), ("granularity", 1)],
         {"unique": True}),
    ]
    for collection, keys, opts in specs:
        try:
            collection.create_index(keys, **opts)
        except Exception:
            logger.exception("Could not create MongoDB index on %s %s", collection.name, keys)


def _utcnow():
    return datetime.now(timezone.utc)


def _clean(document):
    if not document:
        return document
    cleaned = dict(document)
    cleaned.pop("_id", None)
    return cleaned


def _collection(name):
    if hasattr(db, name):
        return getattr(db, name)
    try:
        return db[name]
    except TypeError:
        return globals()[f"{name}_collection"]


def save_token(token_data, platform=None):
    document = deepcopy(token_data)
    document["platform"] = registry.normalize_platform(platform or document.get("platform"))
    document["created_at"] = _utcnow()
    # Persist a resolved absolute expiry alongside the token so refresh logic can
    # act proactively (extend before the token lapses) instead of only reacting to
    # an auth failure. Both Meta and LinkedIn return ``expires_in`` (seconds from
    # now); an explicit ``expires_at`` passed in is respected as-is.
    if "expires_at" not in document:
        try:
            expires_in = int(document.get("expires_in"))
        except (TypeError, ValueError):
            expires_in = None
        if expires_in:
            document["expires_at"] = document["created_at"] + timedelta(seconds=expires_in)
    _collection("tokens").insert_one(document)
    return _clean(document)


def get_latest_access_token(platform=None):
    token_doc = _collection("tokens").find_one(
        _platform_query(platform), {"_id": 0}, sort=[("created_at", -1)])
    if not token_doc:
        return None
    return token_doc.get("access_token")


def get_latest_token_expiry(platform=None):
    """Absolute expiry (tz-aware UTC ``datetime``) of the most recently stored
    token for ``platform``, or ``None`` when there is no token or its expiry is
    unknown (older rows written before expiry tracking)."""
    token_doc = _collection("tokens").find_one(
        _platform_query(platform), {"_id": 0}, sort=[("created_at", -1)])
    if not token_doc:
        return None
    expires_at = token_doc.get("expires_at")
    if expires_at is None:
        return None
    # pymongo returns naive UTC datetimes by default; make comparisons tz-safe.
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at


def get_latest_refresh_token(platform=None):
    token_doc = _collection("tokens").find_one(
        {**_platform_query(platform),
         "refresh_token": {"$exists": True, "$nin": [None, "", "your_refresh_token"]}},
        {"_id": 0},
        sort=[("created_at", -1)],
    )
    if not token_doc:
        return None
    return token_doc.get("refresh_token")


def _latest_before_today(organization_id, today, platform=None):
    query = {**_platform_query(platform), "date": {"$lt": today}}
    if organization_id:
        query["organization_id"] = organization_id
    collection = _collection("analytics")
    if not hasattr(collection, "find_one"):
        return None
    return collection.find_one(query, sort=[("date", -1)])


def _legacy_daily_deltas(data, previous):
    previous = previous or {}
    return {
        "daily_impressions": data.get("impressions", 0) - previous.get("impressions", 0),
        "daily_clicks": data.get("clicks", 0) - previous.get("clicks", 0),
        "daily_followers": data.get("followers", 0) - previous.get("followers", 0),
        "daily_likes": data.get("likes", 0) - previous.get("likes", 0),
    }


def _daily_rows_from_lifetime_snapshot(rows, current, previous):
    if not rows:
        return rows

    previous = previous or {}
    output = []
    for row in rows:
        daily_row = dict(row)
        for field in METRIC_FIELDS:
            current_value = current.get(field, row.get(field, 0))
            previous_value = previous.get(field, 0)
            daily_row[field] = max(current_value - previous_value, 0)

        daily_row["followers_gained"] = max(current.get("followers", 0) - previous.get("followers", 0), 0)
        daily_row["engagement_rate"] = engagement_rate(daily_row)
        daily_row["ctr_percent"] = ctr_percent(daily_row)
        daily_row["total_engagements"] = total_engagements(daily_row)
        daily_row["metric_mode"] = "daily_delta_from_lifetime_snapshot"
        output.append(daily_row)
    return output


def _canonical_post_rows(rows):
    output = []
    for row in rows or []:
        post = dict(row)
        post_id = str(post.get("postId") or post.get("post_id") or "")
        content_type = str(post.get("contentType") or post.get("content_type") or "UNKNOWN")
        if not post_id:
            logger.warning("Post analytics row missing postId: %s", post)
        if not content_type:
            logger.warning("Post analytics row missing contentType: %s", post)
            content_type = "UNKNOWN"
        post["postId"] = post["post_id"] = post_id
        post["contentType"] = post["content_type"] = canonical_content_type(content_type)
        post["postDate"] = str(post.get("postDate") or "")
        post["postText"] = str(post.get("postText") or "")
        output.append(post)
    return output


def _add_organization_id(rows, organization_id):
    output = []
    for row in rows or []:
        enriched = dict(row)
        enriched.setdefault("organization_id", organization_id or "")
        output.append(enriched)
    return output


def _canonical_audience_rows(rows, organization_id="", report_date=""):
    return [
        normalize_audience_dashboard_row(row, organization_id=organization_id, report_date=report_date)
        for row in rows or []
    ]


def save_analytics(data, platform=None):
    source = deepcopy(data)
    platform = registry.normalize_platform(platform or source.get("platform"))
    is_linkedin = platform == "linkedin"
    metric_fields, engagement_fn = registry.get_metric_config(platform)

    today = source.get("date") or _utcnow().date().isoformat()
    organization_id = source.get("organization_id")
    daily_rows = source.get("daily_metrics", [])
    # LinkedIn post rows carry URN/content-type quirks that need canonicalizing;
    # other platforms already emit normalized post rows (with their own row_type
    # for reels/stories) and must not be forced through LinkedIn's mapping.
    post_rows = _canonical_post_rows(source.get("post_analytics", [])) if is_linkedin \
        else list(source.get("post_analytics", []))
    audience_rows = source.get("audience_analytics", [])
    current_period = aggregate_metrics(daily_rows, metric_fields, engagement_fn) if daily_rows else {}
    previous = _latest_before_today(organization_id, today, platform)

    top_level_current = {
        "impressions": source.get("impressions", current_period.get("impressions", 0)),
        "unique_impressions": source.get("unique_impressions", current_period.get("unique_impressions", 0)),
        "reach": source.get("reach", current_period.get("reach", 0)),
        "clicks": source.get("clicks", current_period.get("clicks", 0)),
        "likes": source.get("likes", current_period.get("likes", 0)),
        "comments": source.get("comments", current_period.get("comments", 0)),
        "shares": source.get("shares", current_period.get("shares", 0)),
        "followers": source.get("followers", 0),
        "page_views": source.get("page_views", current_period.get("page_views", 0)),
        "careers_page_views": source.get("careers_page_views", current_period.get("careers_page_views", 0)),
    }

    if source.get("metric_mode") == "lifetime_snapshot":
        daily_rows = _daily_rows_from_lifetime_snapshot(daily_rows, top_level_current, previous)
        current_period = aggregate_metrics(daily_rows, metric_fields, engagement_fn)
        source["weekly_metrics"] = aggregate_by_period(daily_rows, "week", metric_fields, engagement_fn)
        source["monthly_metrics"] = aggregate_by_period(daily_rows, "month", metric_fields, engagement_fn)
        source["comparisons"] = build_comparisons(daily_rows, today, metric_fields, engagement_fn)

    daily_rows = _add_organization_id(daily_rows, organization_id)
    if is_linkedin:
        audience_rows = _canonical_audience_rows(audience_rows, organization_id, today)
    else:
        audience_rows = _add_organization_id(audience_rows, organization_id)
    post_rows = _add_organization_id(post_rows, organization_id)

    if not audience_rows:
        logger.warning("Saving analytics without audience_analytics rows")
    if not post_rows:
        logger.warning("Saving analytics without post_analytics rows")

    analytics_doc = {
        "platform": platform,
        "organization_id": organization_id,
        "date": today,
        "fetched_at": source.get("fetched_at") or _utcnow(),
        "created_at": _utcnow(),
        "sync_range": source.get("sync_range", {}),

        # Backward-compatible top-level fields used by the current connector.
        "impressions": top_level_current["impressions"],
        "unique_impressions": top_level_current["unique_impressions"],
        "reach": top_level_current["reach"],
        "clicks": top_level_current["clicks"],
        "engagement": source.get("engagement", current_period.get("engagement_rate", 0)),
        "engagement_rate": source.get("engagement_rate", current_period.get("engagement_rate", 0)),
        "ctr_percent": source.get("ctr_percent", current_period.get("ctr_percent", 0)),
        "total_engagements": source.get("total_engagements", current_period.get("total_engagements", 0)),
        "likes": top_level_current["likes"],
        "comments": top_level_current["comments"],
        "shares": top_level_current["shares"],
        "followers": top_level_current["followers"],
        "followers_gained": source.get("followers_gained", current_period.get("followers_gained", 0)),
        "page_views": top_level_current["page_views"],
        "careers_page_views": top_level_current["careers_page_views"],
        "metric_mode": source.get("metric_mode", "time_bound"),

        # Platform-neutral / Instagram metric fields (0 for LinkedIn). Meta
        # consolidated impressions/plays/video_views into ``views``.
        "views": source.get("views", current_period.get("views", 0)),
        "saved": source.get("saved", current_period.get("saved", 0)),
        "total_interactions": source.get("total_interactions", current_period.get("total_interactions", 0)),
        "accounts_engaged": source.get("accounts_engaged", current_period.get("accounts_engaged", 0)),

        # Dashboard-ready normalized structures.
        "daily_metrics": daily_rows,
        "weekly_metrics": source.get("weekly_metrics", []),
        "monthly_metrics": source.get("monthly_metrics", []),
        "post_analytics": post_rows,
        "audience_analytics": audience_rows,
        "comparisons": source.get("comparisons") or build_comparisons(daily_rows, None, metric_fields, engagement_fn),
        "performance_insights": source.get("performance_insights", {}),
        "overview": source.get("overview", {}),
        # Prefer collector-provided rows (they carry platform-specific row types
        # like reel/story); fall back to the generic builder for LinkedIn.
        "dashboard_rows": source.get("dashboard_rows")
        or build_dashboard_rows(daily_rows, audience_rows, post_rows, today),

        # Optional raw payloads help debug API changes without another sync.
        "raw": source.get("raw", {}),
    }
    analytics_doc.update(_legacy_daily_deltas(analytics_doc, previous))

    collection = _collection("analytics")
    if hasattr(collection, "update_one"):
        collection.update_one(
            {"platform": platform, "organization_id": organization_id, "date": today},
            {"$set": analytics_doc},
            upsert=True,
        )
    else:
        collection.insert_one(analytics_doc)
    return _clean(analytics_doc)


def get_all_analytics(start_date=None, end_date=None, platform=None):
    query = dict(_platform_query(platform))
    if start_date or end_date:
        date_query = {}
        if start_date:
            date_query["$gte"] = parse_date(start_date).isoformat()
        if end_date:
            date_query["$lte"] = parse_date(end_date).isoformat()
        query["date"] = date_query

    return list(_collection("analytics").find(query, {"_id": 0}).sort("date", 1))


def get_latest_analytics(platform=None):
    return _collection("analytics").find_one(_platform_query(platform), {"_id": 0}, sort=[("date", -1)])


def _dedupe_audience_rows(rows):
    """Keep one row per (segment_type, segment); later rows win."""
    deduped = {}
    for row in rows:
        key = (row.get("segment_type", ""), row.get("segment", ""))
        deduped[key] = row
    return list(deduped.values())


def _dedupe_post_rows(rows):
    """Keep one row per postId; later rows win."""
    deduped = {}
    for row in rows:
        deduped[str(row.get("postId") or row.get("post_id") or id(row))] = row
    return list(deduped.values())


def _dashboard_payload(platform, organization_id, start_date, end_date, daily_rows, audience_rows,
                       post_rows, comparisons, performance_insights, followers, snapshots,
                       dashboard_rows=None, overview=None, sync_warnings=None):
    metric_fields, engagement_fn = registry.get_metric_config(platform)
    if overview is None:
        overview = aggregate_metrics(daily_rows, metric_fields, engagement_fn)
    overview = dict(overview)
    overview["followers"] = followers
    overview["organization_id"] = organization_id
    if dashboard_rows is None:
        dashboard_rows = registry.get_dashboard_row_builder(platform)(
            daily_rows, post_rows, audience_rows, end_date)
    return {
        "platform": platform,
        "organization_id": organization_id,
        "start_date": parse_date(start_date).isoformat(),
        "end_date": parse_date(end_date).isoformat(),
        "overview": overview,
        "comparisons": comparisons,
        "performance_insights": performance_insights,
        "daily_metrics": daily_rows,
        "audience_analytics": audience_rows,
        "post_analytics": post_rows,
        "dashboard_rows": dashboard_rows,
        "snapshots": snapshots,
        "sync_warnings": list(sync_warnings or []),
    }


def _cache_key(platform, account_id, start_date, end_date, granularity):
    # Platform + account MUST be part of the key: without them a LinkedIn and an
    # Instagram request for the same date window would collide in the cache.
    return {
        "platform": registry.normalize_platform(platform),
        "account_id": str(account_id or ""),
        "start": start_date,
        "end": end_date,
        "granularity": granularity,
    }


def _get_cached_dashboard(platform, account_id, start_date, end_date, granularity, ttl_seconds, allow_stale=False):
    collection = _collection("dashboard_cache")
    if not hasattr(collection, "find_one"):
        return None
    doc = collection.find_one({**_cache_key(platform, account_id, start_date, end_date, granularity)}, {"_id": 0})
    if not doc:
        return None
    payload = doc.get("payload")
    if allow_stale:
        return payload
    built_at = doc.get("built_at")
    if not built_at:
        return None
    # pymongo returns naive UTC datetimes by default; make the comparison tz-safe.
    if built_at.tzinfo is None:
        built_at = built_at.replace(tzinfo=timezone.utc)
    if (_utcnow() - built_at).total_seconds() > ttl_seconds:
        return None
    return payload


def _store_cached_dashboard(platform, account_id, start_date, end_date, granularity, payload):
    collection = _collection("dashboard_cache")
    if not hasattr(collection, "update_one"):
        return
    key = _cache_key(platform, account_id, start_date, end_date, granularity)
    collection.update_one(
        {**key},
        {"$set": {**key, "built_at": _utcnow(), "payload": payload}},
        upsert=True,
    )


def _dashboard_from_fresh(data, start_date, end_date, platform, sync_warnings=None):
    """Build a dashboard payload from a single freshly collected analytics doc.

    Because it comes from one collection run there is no cross-snapshot
    duplication of audience/post rows.
    """
    is_linkedin = platform == "linkedin"
    metric_fields, engagement_fn = registry.get_metric_config(platform)
    organization_id = data.get("organization_id") or ""
    daily_rows = _add_organization_id(
        rows_between(data.get("daily_metrics", []), start_date, end_date), organization_id)
    if is_linkedin:
        audience_rows = _canonical_audience_rows(
            data.get("audience_analytics", []), organization_id, data.get("date") or end_date)
        post_rows = _add_organization_id(
            _canonical_post_rows(data.get("post_analytics", [])), organization_id)
    else:
        audience_rows = _add_organization_id(data.get("audience_analytics", []), organization_id)
        post_rows = _add_organization_id(data.get("post_analytics", []), organization_id)
    return _dashboard_payload(
        platform, organization_id, start_date, end_date, daily_rows, audience_rows, post_rows,
        data.get("comparisons") or build_comparisons(daily_rows, None, metric_fields, engagement_fn),
        data.get("performance_insights", {}),
        data.get("followers", 0),
        snapshots=[],
        # Prefer collector-provided rows/overview: they carry platform-specific
        # row types (reel/story) the generic builder can't reconstruct.
        dashboard_rows=data.get("dashboard_rows"),
        overview=data.get("overview"),
        sync_warnings=sync_warnings,
    )


def _dashboard_from_storage(start_date, end_date, platform):
    """Fallback path: assemble the dashboard from already-stored snapshots."""
    is_linkedin = platform == "linkedin"
    metric_fields, engagement_fn = registry.get_metric_config(platform)
    latest = get_latest_analytics(platform) or {}
    historical = get_all_analytics(start_date, end_date, platform)

    daily_rows = []
    audience_rows = []
    post_rows = []
    for snapshot in historical or [latest]:
        organization_id = snapshot.get("organization_id") or latest.get("organization_id") or ""
        daily_rows.extend(_add_organization_id(
            rows_between(snapshot.get("daily_metrics", []), start_date, end_date), organization_id))
        if is_linkedin:
            audience_rows.extend(_canonical_audience_rows(
                snapshot.get("audience_analytics", []), organization_id, snapshot.get("date") or end_date))
            post_rows.extend(_add_organization_id(
                _canonical_post_rows(snapshot.get("post_analytics", [])), organization_id))
        else:
            audience_rows.extend(_add_organization_id(snapshot.get("audience_analytics", []), organization_id))
            post_rows.extend(_add_organization_id(snapshot.get("post_analytics", []), organization_id))

    audience_rows = _dedupe_audience_rows(audience_rows)
    post_rows = _dedupe_post_rows(post_rows)

    if not audience_rows:
        logger.warning("Dashboard analytics response has no audience_analytics rows")
    if not post_rows:
        logger.warning("Dashboard analytics response has empty post_analytics")

    return _dashboard_payload(
        platform, latest.get("organization_id", ""), start_date, end_date, daily_rows, audience_rows, post_rows,
        build_comparisons(daily_rows, None, metric_fields, engagement_fn), latest.get("performance_insights", {}),
        latest.get("followers", 0), snapshots=historical,
    )


_fetch_locks = {}
_fetch_locks_guard = threading.Lock()


def _fetch_lock(key):
    with _fetch_locks_guard:
        lock = _fetch_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _fetch_locks[key] = lock
        return lock


def _best_available_dashboard(platform, account_id, start_date, end_date, granularity, ttl_seconds):
    """Serve the freshest thing we have without hitting the platform API: a stale
    cache entry if present, otherwise whatever is already stored in MongoDB."""
    stale = _get_cached_dashboard(
        platform, account_id, start_date, end_date, granularity, ttl_seconds, allow_stale=True)
    if stale is not None:
        return stale
    return _dashboard_from_storage(start_date, end_date, platform)


def get_dashboard_analytics(platform=None, start_date=None, end_date=None, granularity="DAY"):
    platform = registry.normalize_platform(platform)
    account_id = registry.get_account_id(platform) or ""

    if not start_date or not end_date:
        default_start, default_end = default_date_range()
        start_date = start_date or default_start.isoformat()
        end_date = end_date or default_end.isoformat()

    start_date = parse_date(start_date).isoformat()
    end_date = parse_date(end_date).isoformat()

    live_fetch = getattr(settings, "DASHBOARD_LIVE_FETCH", True)
    ttl_seconds = int(getattr(settings, "DASHBOARD_CACHE_TTL_SECONDS", 6 * 3600))

    if not live_fetch:
        return _dashboard_from_storage(start_date, end_date, platform)

    cached = _get_cached_dashboard(platform, account_id, start_date, end_date, granularity, ttl_seconds)
    if cached is not None:
        return cached

    # Prevent a cache stampede: BI tools (Looker Studio) fire several concurrent
    # requests for the same window. Only one thread should hit the platform API;
    # the others serve the best already-available data instead of piling on
    # duplicate fetches (which also burn API rate/privacy budgets).
    key = f"{platform}|{account_id}|{start_date}|{end_date}|{granularity}"
    lock = _fetch_lock(key)
    if not lock.acquire(blocking=False):
        logger.info("Dashboard fetch already in progress for %s; serving cached/stored data", key)
        return _best_available_dashboard(platform, account_id, start_date, end_date, granularity, ttl_seconds)

    try:
        # Re-check: another thread may have populated the cache while we waited.
        cached = _get_cached_dashboard(platform, account_id, start_date, end_date, granularity, ttl_seconds)
        if cached is not None:
            return cached

        # Every degraded endpoint inside the collector (`_fetch_or_empty`) calls
        # this instead of only logging, so callers can see what silently failed
        # without digging through Django logs.
        warnings = []
        collector = registry.get_collector(platform)
        data = collector(start_date, end_date, granularity, warn=warnings.append)
        save_analytics(data, platform=platform)
        # Preserve order, drop exact duplicates (the same endpoint can fail
        # identically across multiple date-chunked requests).
        deduped_warnings = list(dict.fromkeys(warnings))
        payload = _dashboard_from_fresh(data, start_date, end_date, platform, sync_warnings=deduped_warnings)
        _store_cached_dashboard(platform, account_id, start_date, end_date, granularity, payload)
        return payload
    except Exception as exc:
        logger.exception("Live %s dashboard fetch failed; serving cached/stored data", platform)
        fallback = dict(_best_available_dashboard(
            platform, account_id, start_date, end_date, granularity, ttl_seconds))
        fallback["sync_warnings"] = list(fallback.get("sync_warnings") or []) + [
            f"Live {platform} dashboard fetch failed ({exc}); serving best-available cached/stored data"
        ]
        return fallback
    finally:
        lock.release()
