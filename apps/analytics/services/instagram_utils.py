"""Instagram (Meta Graph API) response normalization.

Kept beside — not inside — ``analytics_utils`` so each platform's parsing stays
isolated. The platform-agnostic math (aggregation, comparisons, period buckets)
is imported from ``analytics_utils`` and reused unchanged; only the Graph API
response shapes, the metric set, the engagement formula, and the media-type →
row-type mapping are Instagram-specific.

Metric note (verified against Meta docs, 2026-07): impressions / video_views /
plays were removed from the Graph API and consolidated into ``views``; profile
views and website/email/phone/text-message clicks were removed with no
replacement. This module therefore never references those — it uses ``views``
and does not fabricate the removed columns. Engagement rate is not provided by
the API and is computed here.
"""

from .analytics_utils import parse_date, safe_int, safe_number, string_value, tag_rows

# The numeric columns Instagram actually exposes (post-2025 metric consolidation).
INSTAGRAM_METRIC_FIELDS = (
    "reach",
    "views",
    "likes",
    "comments",
    "shares",
    "saved",
    "total_interactions",
    "accounts_engaged",
    "followers_gained",
)

# Verified live against Graph API v25 (2026-07): only `reach` accepts
# metric_type=time_series (a per-day series). `views`, `total_interactions`,
# `accounts_engaged`, and `follows_and_unfollows` accept ONLY total_value (a
# single number for the whole window). Requesting a total_value-only metric as
# time_series makes Meta reject the ENTIRE batch, so the two groups must be
# fetched in separate calls.
ACCOUNT_TIMESERIES_METRICS = ("reach",)
ACCOUNT_TOTAL_METRICS = ("reach", "views", "total_interactions", "accounts_engaged", "follows_and_unfollows")

# media_product_type → dashboard row_type. AD creatives are folded into "post".
MEDIA_PRODUCT_ROW_TYPE = {
    "FEED": "post",
    "REELS": "reel",
    "STORY": "story",
    "AD": "post",
}

# Insight metrics to request per media product type (all "views"-based; no
# impressions/plays/video_views — those were removed by Meta).
MEDIA_INSIGHT_METRICS = {
    "FEED": ("reach", "views", "likes", "comments", "shares", "saved", "total_interactions"),
    "REELS": ("reach", "views", "likes", "comments", "shares", "saved", "total_interactions",
              "ig_reels_avg_watch_time"),
    "STORY": ("reach", "views", "replies", "shares", "total_interactions", "navigation"),
}

# Minimal ISO country-code → display-name map; unknown codes fall back to the
# code itself so nothing is dropped.
INSTAGRAM_COUNTRY_NAMES = {
    "US": "United States", "IN": "India", "GB": "United Kingdom", "CA": "Canada",
    "AU": "Australia", "DE": "Germany", "FR": "France", "BR": "Brazil",
    "JP": "Japan", "MX": "Mexico", "ES": "Spain", "IT": "Italy",
    "NL": "Netherlands", "AE": "United Arab Emirates", "SG": "Singapore",
}


def instagram_engagement_rate(metrics):
    """Instagram has no clicks/impressions; engagement is interactions over reach.

    Falls back to summing the interaction components when ``total_interactions``
    is absent. Returns 0 when reach is unknown/zero (small-account suppression).
    """
    reach = safe_number(metrics.get("reach"))
    if reach <= 0:
        return 0.0
    interactions = safe_number(metrics.get("total_interactions"))
    if interactions <= 0:
        interactions = (
            safe_number(metrics.get("likes"))
            + safe_number(metrics.get("comments"))
            + safe_number(metrics.get("shares"))
            + safe_number(metrics.get("saved"))
        )
    return round(interactions / reach, 6)


def resolve_country_name(code):
    code = string_value(code).strip()
    if not code:
        return ""
    return INSTAGRAM_COUNTRY_NAMES.get(code.upper(), code)


def _metric_value(entry):
    """Pull a scalar out of one Graph API insight ``data[]`` entry, tolerating the
    two shapes Meta returns: ``values:[{value: ...}]`` and ``total_value``."""
    values = entry.get("values")
    if isinstance(values, list) and values:
        return safe_number(values[0].get("value"))
    total = entry.get("total_value")
    if isinstance(total, dict) and "value" in total:
        return safe_number(total.get("value"))
    return 0


def extract_metric_values(insights_payload):
    """metric name → scalar for any /insights response (media OR account totals),
    tolerating both the ``values:[{value}]`` and ``total_value`` shapes."""
    metrics = {}
    for entry in (insights_payload or {}).get("data", []) or []:
        name = entry.get("name")
        if name:
            metrics[name] = _metric_value(entry)
    return metrics


# Back-compat alias (media insights use the same extraction).
extract_media_insights = extract_metric_values
extract_account_totals = extract_metric_values


def _end_time_to_date(end_time):
    if not end_time:
        return None
    try:
        return parse_date(str(end_time)[:10]).isoformat()
    except (ValueError, TypeError):
        return None


def normalize_account_timeseries(insights_payload):
    """Pivot the account time_series insights into one row per day.

    Only ``reach`` is available as a daily series (the trend line); the other
    account metrics are window totals and belong in the overview, not per-day.
    """
    by_day = {}
    for entry in (insights_payload or {}).get("data", []) or []:
        name = entry.get("name")
        if name not in ACCOUNT_TIMESERIES_METRICS:
            continue
        for point in entry.get("values", []) or []:
            day = _end_time_to_date(point.get("end_time"))
            if not day:
                continue
            by_day.setdefault(day, {"date": day})[name] = safe_number(point.get("value"))

    rows = []
    for day in sorted(by_day):
        row = by_day[day]
        for field in ACCOUNT_TIMESERIES_METRICS:
            row.setdefault(field, 0)
        row.setdefault("followers_gained", 0)
        row["engagement_rate"] = instagram_engagement_rate(row)
        rows.append(row)
    return rows


def _media_row(media, insights):
    product_type = string_value(media.get("media_product_type")).upper()
    row_type = MEDIA_PRODUCT_ROW_TYPE.get(product_type, "post")
    reach = safe_number(insights.get("reach"))
    total_interactions = safe_number(insights.get("total_interactions"))
    likes = safe_number(insights.get("likes", media.get("like_count")))
    comments = safe_number(insights.get("comments", media.get("comments_count")))
    row = {
        "row_type": row_type,
        "postId": string_value(media.get("id")),
        "post_id": string_value(media.get("id")),
        "media_type": string_value(media.get("media_type")),
        "media_product_type": product_type,
        # Reuse the connector's existing content-type field for filtering.
        "contentType": product_type or string_value(media.get("media_type")),
        "content_type": product_type or string_value(media.get("media_type")),
        "postText": string_value(media.get("caption")),
        "postDate": _end_time_to_date(media.get("timestamp")) or "",
        "permalink": string_value(media.get("permalink")),
        "reach": reach,
        "views": safe_number(insights.get("views")),
        "likes": likes,
        "comments": comments,
        "shares": safe_number(insights.get("shares")),
        "saved": safe_number(insights.get("saved")),
        "replies": safe_number(insights.get("replies")),
        "navigation": safe_number(insights.get("navigation")),
        "avg_watch_time": safe_number(insights.get("ig_reels_avg_watch_time")),
        "total_interactions": total_interactions or (likes + comments
                                                     + safe_number(insights.get("shares"))
                                                     + safe_number(insights.get("saved"))),
    }
    row["engagement_rate"] = instagram_engagement_rate(row)
    return row


def normalize_media_rows(media_list, insights_by_id):
    """One normalized row per media, tagged post/reel/story, newest first."""
    rows = [_media_row(media, insights_by_id.get(str(media.get("id")), {}))
            for media in media_list or []]
    return sorted(rows, key=lambda r: r.get("postDate", ""), reverse=True)


def _demographic_results(insights_payload, metric_name):
    for entry in (insights_payload or {}).get("data", []) or []:
        if entry.get("name") != metric_name:
            continue
        total = entry.get("total_value") or {}
        for breakdown in total.get("breakdowns", []) or []:
            for result in breakdown.get("results", []) or []:
                dim_values = result.get("dimension_values") or []
                if dim_values:
                    yield string_value(dim_values[0]), safe_int(result.get("value"))


def normalize_country_rows(country_payload):
    """Audience rows for the follower_demographics country breakdown."""
    rows = []
    for code, followers in _demographic_results(country_payload, "follower_demographics"):
        name = resolve_country_name(code)
        rows.append({
            "segment_type": "Followers by Country",
            "segment": code,
            "segment_label": name,
            "country_name": name,
            "city": "",
            "followers": followers,
        })
    return sorted(rows, key=lambda r: r["followers"], reverse=True)


def normalize_city_rows(city_payload):
    """Audience rows for the follower_demographics city breakdown."""
    rows = []
    for city, followers in _demographic_results(city_payload, "follower_demographics"):
        rows.append({
            "segment_type": "Followers by City",
            "segment": city,
            "segment_label": city,
            "country_name": "",
            "city": city,
            "followers": followers,
        })
    return sorted(rows, key=lambda r: r["followers"], reverse=True)


def build_instagram_dashboard_rows(daily_rows, media_rows, audience_rows, report_date=""):
    """Flat, connector-ready rows tagged by row_type (daily/post/reel/story/country/city)."""
    rows = list(tag_rows(daily_rows, "daily"))
    # Media rows already carry their own row_type (post/reel/story).
    for row in media_rows or []:
        row = dict(row)
        row.setdefault("row_type", "post")
        rows.append(row)
    for row in audience_rows or []:
        row = dict(row)
        row_type = "city" if row.get("segment_type") == "Followers by City" else "country"
        rows.append({"row_type": row_type, "date": string_value(report_date), **row})
    return rows
