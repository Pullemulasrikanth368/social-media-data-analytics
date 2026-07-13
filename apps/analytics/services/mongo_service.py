from copy import deepcopy
from datetime import datetime, timezone
import logging

from django.conf import settings
from pymongo import MongoClient

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
db = client["linkedin_db"]

analytics_collection = db["analytics"]
tokens_collection = db["tokens"]


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


def save_token(token_data):
    document = deepcopy(token_data)
    document["created_at"] = _utcnow()
    _collection("tokens").insert_one(document)
    return _clean(document)


def get_latest_access_token():
    token_doc = _collection("tokens").find_one({}, {"_id": 0}, sort=[("created_at", -1)])
    if not token_doc:
        return None
    return token_doc.get("access_token")


def _latest_before_today(organization_id, today):
    query = {"date": {"$lt": today}}
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


def save_analytics(data):
    source = deepcopy(data)
    today = source.get("date") or _utcnow().date().isoformat()
    organization_id = source.get("organization_id")
    daily_rows = source.get("daily_metrics", [])
    post_rows = _canonical_post_rows(source.get("post_analytics", []))
    audience_rows = source.get("audience_analytics", [])
    current_period = aggregate_metrics(daily_rows) if daily_rows else {}
    previous = _latest_before_today(organization_id, today)

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
        current_period = aggregate_metrics(daily_rows)
        source["weekly_metrics"] = aggregate_by_period(daily_rows, "week")
        source["monthly_metrics"] = aggregate_by_period(daily_rows, "month")
        source["comparisons"] = build_comparisons(daily_rows, today)

    daily_rows = _add_organization_id(daily_rows, organization_id)
    audience_rows = _canonical_audience_rows(audience_rows, organization_id, today)
    post_rows = _add_organization_id(post_rows, organization_id)

    if not audience_rows:
        logger.warning("Saving analytics without audience_analytics rows")
    if not post_rows:
        logger.warning("Saving analytics without post_analytics rows")

    analytics_doc = {
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

        # Dashboard-ready normalized structures.
        "daily_metrics": daily_rows,
        "weekly_metrics": source.get("weekly_metrics", []),
        "monthly_metrics": source.get("monthly_metrics", []),
        "post_analytics": post_rows,
        "audience_analytics": audience_rows,
        "comparisons": source.get("comparisons") or build_comparisons(daily_rows),
        "performance_insights": source.get("performance_insights", {}),
        "dashboard_rows": build_dashboard_rows(daily_rows, audience_rows, post_rows, today),

        # Optional raw payloads help debug LinkedIn API changes without another sync.
        "raw": source.get("raw", {}),
    }
    analytics_doc.update(_legacy_daily_deltas(analytics_doc, previous))

    collection = _collection("analytics")
    if hasattr(collection, "update_one"):
        collection.update_one(
            {"organization_id": organization_id, "date": today},
            {"$set": analytics_doc},
            upsert=True,
        )
    else:
        collection.insert_one(analytics_doc)
    return _clean(analytics_doc)


def get_all_analytics(start_date=None, end_date=None):
    query = {}
    if start_date or end_date:
        date_query = {}
        if start_date:
            date_query["$gte"] = parse_date(start_date).isoformat()
        if end_date:
            date_query["$lte"] = parse_date(end_date).isoformat()
        query["date"] = date_query

    return list(_collection("analytics").find(query, {"_id": 0}).sort("date", 1))


def get_latest_analytics():
    return _collection("analytics").find_one({}, {"_id": 0}, sort=[("date", -1)])


def get_dashboard_analytics(start_date=None, end_date=None):
    if not start_date or not end_date:
        default_start, default_end = default_date_range()
        start_date = start_date or default_start.isoformat()
        end_date = end_date or default_end.isoformat()

    latest = get_latest_analytics() or {}
    historical = get_all_analytics(start_date, end_date)

    daily_rows = []
    audience_rows = []
    post_rows = []

    for snapshot in historical or [latest]:
        organization_id = snapshot.get("organization_id") or latest.get("organization_id") or ""
        daily_rows.extend(_add_organization_id(
            rows_between(snapshot.get("daily_metrics", []), start_date, end_date),
            organization_id,
        ))
        audience_rows.extend(_canonical_audience_rows(
            snapshot.get("audience_analytics", []),
            organization_id,
            snapshot.get("date") or end_date,
        ))
        post_rows.extend(_add_organization_id(
            _canonical_post_rows(snapshot.get("post_analytics", [])),
            organization_id,
        ))

    if not audience_rows:
        logger.warning("Dashboard analytics response has no audience_analytics rows")
    if not post_rows:
        logger.warning("Dashboard analytics response has empty post_analytics")

    overview = aggregate_metrics(daily_rows)
    overview["followers"] = latest.get("followers", 0)
    overview["organization_id"] = latest.get("organization_id", "")

    return {
        "organization_id": latest.get("organization_id", ""),
        "start_date": parse_date(start_date).isoformat(),
        "end_date": parse_date(end_date).isoformat(),
        "overview": overview,
        "comparisons": build_comparisons(daily_rows),
        "performance_insights": latest.get("performance_insights", {}),
        "daily_metrics": daily_rows,
        "audience_analytics": audience_rows,
        "post_analytics": post_rows,
        "dashboard_rows": build_dashboard_rows(daily_rows, audience_rows, post_rows, end_date),
        "snapshots": historical,
    }
