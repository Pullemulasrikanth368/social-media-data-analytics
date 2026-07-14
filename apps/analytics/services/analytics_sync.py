"""Reusable LinkedIn analytics collection.

The orchestration that turns raw LinkedIn API responses into a normalized
analytics document used to live only inside the ``sync_linkedin`` management
command. It is extracted here so both the command *and* the on-demand
dashboard fetch-through path (``mongo_service.get_dashboard_analytics``) can
reuse the exact same logic instead of duplicating it.
"""

import logging

from .analytics_utils import (
    aggregate_by_period,
    aggregate_metrics,
    build_comparisons,
    ctr_percent,
    default_date_range,
    engagement_rate,
    extract_page_metrics,
    extract_share_metrics,
    merge_daily_rows,
    normalize_audience_rows,
    normalize_follower_time_series,
    normalize_page_time_series,
    normalize_post_rows,
    normalize_share_time_series,
    parse_date,
    summarize_performance,
)
from .linkedin_service import LinkedInAPIError, LinkedInService

logger = logging.getLogger(__name__)


def _fetch_or_empty(label, fetcher, *args, warn=None):
    try:
        return fetcher(*args)
    except LinkedInAPIError as exc:
        message = f"LinkedIn {label} unavailable: {exc}"
        logger.warning(message)
        if warn:
            warn(message)
        return {}


def _post_ids(post_payload):
    post_ids = []
    for item in post_payload.get("elements", []) or []:
        post_id = item.get("id") or item.get("post") or item.get("ugcPost") or item.get("share")
        if post_id:
            post_ids.append(str(post_id))
    return post_ids


def _snapshot_daily_row(day, share_data, page_data):
    share_elements = share_data.get("elements", [])
    page_elements = page_data.get("elements", [])
    share_stats = {}
    page_stats = {}

    if share_elements:
        share_stats = extract_share_metrics(share_elements[0].get("totalShareStatistics") or {})
    if page_elements:
        page_stats = extract_page_metrics(page_elements[0].get("totalPageStatistics") or {})

    row = {
        "date": day.isoformat(),
        "impressions": share_stats.get("impressions", 0),
        "unique_impressions": share_stats.get("unique_impressions", 0),
        "reach": share_stats.get("reach", share_stats.get("impressions", 0)),
        "clicks": share_stats.get("clicks", 0),
        "likes": share_stats.get("likes", 0),
        "comments": share_stats.get("comments", 0),
        "shares": share_stats.get("shares", 0),
        "page_views": page_stats.get("page_views", 0),
        "careers_page_views": page_stats.get("careers_page_views", 0),
        "followers_gained": 0,
    }
    row["engagement_rate"] = share_stats.get("engagement") or engagement_rate(row)
    row["ctr_percent"] = ctr_percent(row)
    return row


def collect_linkedin_analytics(start_date=None, end_date=None, granularity="DAY", linkedin=None, warn=None):
    """Fetch + normalize LinkedIn analytics for a window into an analytics doc.

    Returns the same dict shape the ``sync_linkedin`` command previously built
    inline and handed to ``save_analytics``. ``warn`` is an optional callback
    (e.g. ``self.stdout.write``) used only for surfacing soft failures to a CLI.
    Raises ``LinkedInAPIError`` only for unexpected client construction issues;
    individual endpoint failures degrade to empty payloads.
    """
    default_start, default_end = default_date_range()
    start_date = parse_date(start_date, default_start)
    end_date = parse_date(end_date, default_end)

    linkedin = linkedin or LinkedInService()

    share_data = _fetch_or_empty(
        "time-bound share statistics", linkedin.get_share_statistics, start_date, end_date, granularity, warn=warn
    )
    follower_time_data = _fetch_or_empty(
        "time-bound follower statistics", linkedin.get_follower_statistics, start_date, end_date, granularity, warn=warn
    )
    page_data = _fetch_or_empty(
        "time-bound page statistics", linkedin.get_page_statistics, start_date, end_date, granularity, warn=warn
    )
    followers_data = _fetch_or_empty("follower count", linkedin.get_followers_count, warn=warn)
    audience_data = _fetch_or_empty("audience demographics", linkedin.get_follower_demographics, warn=warn)
    post_metadata = _fetch_or_empty("organization posts", linkedin.get_organization_posts, warn=warn)

    post_share_data = {}
    post_ids = _post_ids(post_metadata)
    if post_ids:
        post_share_data = _fetch_or_empty(
            "post-level share statistics",
            linkedin.get_post_share_statistics,
            post_ids,
            warn=warn,
        )

    daily_metrics = merge_daily_rows(
        normalize_share_time_series(share_data.get("elements", [])),
        normalize_page_time_series(page_data.get("elements", [])),
        normalize_follower_time_series(follower_time_data.get("elements", [])),
    )

    # Lifetime snapshots are only a fallback for when time-bound data is empty.
    # Fetching them eagerly wastes LinkedIn's differential-privacy budget (the
    # page-statistics table in particular returns TABLE_MAX_PRIVACY_COST_EXCEEDED),
    # so defer these calls until they are actually needed.
    lifetime_share_data = {}
    lifetime_page_data = {}
    if not daily_metrics:
        lifetime_share_data = _fetch_or_empty(
            "lifetime share statistics", linkedin.get_share_statistics, warn=warn)
        lifetime_page_data = _fetch_or_empty(
            "lifetime page statistics", linkedin.get_page_statistics, warn=warn)
        daily_metrics = [_snapshot_daily_row(end_date, lifetime_share_data, lifetime_page_data)]
        metric_mode = "lifetime_snapshot"
    else:
        metric_mode = "time_bound"

    overview = aggregate_metrics(daily_metrics)
    post_source_elements = post_share_data.get("elements") or lifetime_share_data.get("elements", [])
    post_analytics = normalize_post_rows(post_source_elements, post_metadata.get("elements", []))
    audience_analytics = normalize_audience_rows(audience_data.get("elements", []))

    if not audience_analytics:
        logger.warning("LinkedIn audience_analytics is empty or missing")
    if not post_analytics:
        logger.warning("LinkedIn post_analytics is empty; post metadata/statistics may be unavailable")

    followers = followers_data.get("firstDegreeSize", 0)
    return {
        "organization_id": linkedin.org_id,
        "date": end_date.isoformat(),
        "sync_range": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "granularity": granularity,
        },
        "impressions": overview.get("impressions", 0),
        "unique_impressions": overview.get("unique_impressions", 0),
        "reach": overview.get("reach", overview.get("impressions", 0)),
        "clicks": overview.get("clicks", 0),
        "engagement": overview.get("engagement_rate", 0),
        "engagement_rate": overview.get("engagement_rate", 0),
        "ctr_percent": overview.get("ctr_percent", 0),
        "total_engagements": overview.get("total_engagements", 0),
        "likes": overview.get("likes", 0),
        "comments": overview.get("comments", 0),
        "shares": overview.get("shares", 0),
        "followers": followers,
        "followers_gained": overview.get("followers_gained", 0),
        "page_views": overview.get("page_views", 0),
        "careers_page_views": overview.get("careers_page_views", 0),
        "daily_metrics": daily_metrics,
        "weekly_metrics": aggregate_by_period(daily_metrics, "week"),
        "monthly_metrics": aggregate_by_period(daily_metrics, "month"),
        "post_analytics": post_analytics,
        "audience_analytics": audience_analytics,
        "comparisons": build_comparisons(daily_metrics, end_date),
        "performance_insights": summarize_performance(daily_metrics, post_analytics),
        "metric_mode": metric_mode,
        "raw": {
            "share_statistics": share_data,
            "page_statistics": page_data,
            "lifetime_page_statistics": lifetime_page_data,
            "post_share_statistics": post_share_data,
            "post_metadata": post_metadata,
            "follower_statistics": follower_time_data,
            "follower_demographics": audience_data,
            "followers": followers_data,
        },
    }
