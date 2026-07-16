"""Reusable Instagram analytics collection.

The Instagram analogue of ``analytics_sync.collect_linkedin_analytics``: fetch the
Graph API endpoints (each degrading to empty on failure), normalize via
``instagram_utils``, and assemble the same analytics-document shape the storage
layer and connector expect. Registered in ``registry.py`` so the on-demand
dashboard fetch-through path and the ``sync_analytics`` command share it.
"""

import logging
from datetime import timedelta

from .analytics_utils import (
    aggregate_by_period,
    build_comparisons,
    default_date_range,
    parse_date,
    safe_number,
    summarize_performance,
    utc_today,
)
from .instagram_service import InstagramAPIError, InstagramService
from .instagram_utils import (
    INSTAGRAM_METRIC_FIELDS,
    build_instagram_dashboard_rows,
    extract_account_totals,
    extract_media_insights,
    instagram_engagement_rate,
    normalize_account_timeseries,
    normalize_city_rows,
    normalize_country_rows,
    normalize_media_rows,
)

logger = logging.getLogger(__name__)

# Meta serves Instagram insights only for roughly the last 2 years; a `since`
# older than that makes the whole insights call 400. Kept a little under 730 for
# timezone/margin safety.
INSIGHTS_MAX_AGE_DAYS = 725
# Account insights reject a window wider than 30 days, so longer ranges are
# fetched in <=30-day chunks and merged.
INSIGHTS_MAX_WINDOW_DAYS = 30


def _date_chunks(start_date, end_date, days=INSIGHTS_MAX_WINDOW_DAYS):
    """Yield inclusive (start, end) sub-windows no wider than ``days``."""
    cur = start_date
    while cur <= end_date:
        chunk_end = min(cur + timedelta(days=days - 1), end_date)
        yield cur, chunk_end
        cur = chunk_end + timedelta(days=1)


def _clamp_window(start_date, end_date, warn=None):
    """Clamp [start, end] into Meta's insights-availability window so an old
    Looker date filter degrades to empty data instead of erroring the call."""
    today = utc_today()
    floor = today - timedelta(days=INSIGHTS_MAX_AGE_DAYS)
    clamped_end = min(end_date, today)
    if clamped_end < floor:
        clamped_end = floor
    clamped_start = max(min(start_date, clamped_end), floor)
    if (clamped_start, clamped_end) != (start_date, end_date):
        msg = (f"Instagram date window clamped to Meta's ~2-year insights window: "
               f"{clamped_start.isoformat()}..{clamped_end.isoformat()} "
               f"(requested {start_date.isoformat()}..{end_date.isoformat()})")
        logger.warning(msg)
        if warn:
            warn(msg)
    return clamped_start, clamped_end


def _fetch_or_empty(label, fetcher, *args, warn=None):
    try:
        return fetcher(*args)
    except InstagramAPIError as exc:
        message = f"Instagram {label} unavailable: {exc}"
        logger.warning(message)
        if warn:
            warn(message)
        return {}


def _media_insights_by_id(instagram, media_list, warn=None):
    insights = {}
    for media in media_list or []:
        media_id = str(media.get("id") or "")
        if not media_id:
            continue
        payload = _fetch_or_empty(
            f"media insights ({media_id})",
            instagram.get_media_insights,
            media_id,
            media.get("media_product_type", "FEED"),
            warn=warn,
        )
        insights[media_id] = extract_media_insights(payload)
    return insights


def collect_instagram_analytics(start_date=None, end_date=None, granularity="DAY", instagram=None, warn=None):
    """Fetch + normalize Instagram analytics for a window into an analytics doc.

    Returns a dict in the same shape ``save_analytics`` / the dashboard payload
    consume (``daily_metrics``, ``post_analytics``, ``audience_analytics``,
    ``dashboard_rows``, ``overview``, ``comparisons`` …). Individual endpoint
    failures degrade to empty payloads rather than aborting the whole sync.
    """
    default_start, default_end = default_date_range()
    start_date = parse_date(start_date, default_start)
    end_date = parse_date(end_date, default_end)
    start_date, end_date = _clamp_window(start_date, end_date, warn=warn)

    instagram = instagram or InstagramService()

    account = _fetch_or_empty("account profile", instagram.get_account, warn=warn)

    # Account insights are capped at 30-day windows: fetch each chunk and merge.
    # `reach` daily rows concatenate cleanly; the total_value metrics are summed
    # across chunks (exact for additive counts like views/interactions; reach
    # across multiple chunks is a sum of per-window uniques, an accepted approx).
    daily_metrics = []
    totals = {}
    for chunk_start, chunk_end in _date_chunks(start_date, end_date):
        ts = _fetch_or_empty(
            "account time series", instagram.get_account_timeseries, chunk_start, chunk_end, warn=warn)
        daily_metrics.extend(normalize_account_timeseries(ts))
        tot = _fetch_or_empty(
            "account totals", instagram.get_account_totals, chunk_start, chunk_end, warn=warn)
        for key, value in extract_account_totals(tot).items():
            totals[key] = totals.get(key, 0) + safe_number(value)

    country_demo = _fetch_or_empty(
        "country demographics", instagram.get_follower_demographics, "country", warn=warn)
    city_demo = _fetch_or_empty(
        "city demographics", instagram.get_follower_demographics, "city", warn=warn)
    media_payload = _fetch_or_empty("media", instagram.get_media, start_date, end_date, warn=warn)

    media_list = media_payload.get("data", []) if isinstance(media_payload, dict) else []
    media_insights = _media_insights_by_id(instagram, media_list, warn=warn)

    media_rows = normalize_media_rows(media_list, media_insights)
    audience_rows = normalize_country_rows(country_demo) + normalize_city_rows(city_demo)

    if not daily_metrics:
        logger.warning("Instagram daily reach series empty; account insights may be unavailable")
    if not media_rows:
        logger.warning("Instagram post/reel/story rows empty; no media in window or insights unavailable")

    mf, ef = INSTAGRAM_METRIC_FIELDS, instagram_engagement_rate
    followers = account.get("followers_count", 0) if isinstance(account, dict) else 0

    # Overview uses the window TOTALS, not a sum of the daily reach series.
    overview = {field: 0 for field in mf}
    for field in ("reach", "views", "total_interactions", "accounts_engaged"):
        overview[field] = safe_number(totals.get(field))
    overview["followers_gained"] = safe_number(totals.get("follows_and_unfollows"))
    overview["total_engagements"] = overview["total_interactions"]
    overview["engagement_rate"] = instagram_engagement_rate(overview)
    overview["followers"] = followers

    dashboard_rows = build_instagram_dashboard_rows(
        daily_metrics, media_rows, audience_rows, end_date.isoformat())

    return {
        "platform": "instagram",
        "organization_id": instagram.org_id,
        "date": end_date.isoformat(),
        "sync_range": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "granularity": granularity,
        },
        "reach": overview.get("reach", 0),
        "views": overview.get("views", 0),
        "likes": overview.get("likes", 0),
        "comments": overview.get("comments", 0),
        "shares": overview.get("shares", 0),
        "saved": overview.get("saved", 0),
        "total_interactions": overview.get("total_interactions", 0),
        "accounts_engaged": overview.get("accounts_engaged", 0),
        "engagement_rate": overview.get("engagement_rate", 0),
        "total_engagements": overview.get("total_engagements", 0),
        "followers": followers,
        "followers_gained": overview.get("followers_gained", 0),
        "overview": overview,
        "daily_metrics": daily_metrics,
        "weekly_metrics": aggregate_by_period(daily_metrics, "week", mf, ef),
        "monthly_metrics": aggregate_by_period(daily_metrics, "month", mf, ef),
        "post_analytics": media_rows,
        "audience_analytics": audience_rows,
        "dashboard_rows": dashboard_rows,
        "comparisons": build_comparisons(daily_metrics, end_date, mf, ef),
        "performance_insights": summarize_performance(daily_metrics, media_rows, reach_metric="views"),
        "metric_mode": "time_bound",
        "raw": {
            "account": account,
            "account_totals_merged": totals,
            "country_demographics": country_demo,
            "city_demographics": city_demo,
            "media": media_payload,
        },
    }
