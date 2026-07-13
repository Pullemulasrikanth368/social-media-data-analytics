import logging

from django.core.management.base import BaseCommand

from apps.analytics.services.analytics_utils import (
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
from apps.analytics.services.linkedin_service import LinkedInAPIError, LinkedInService
from apps.analytics.services.mongo_service import save_analytics

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Sync LinkedIn analytics data"

    def add_arguments(self, parser):
        parser.add_argument("--start-date", help="Start date in YYYY-MM-DD format")
        parser.add_argument("--end-date", help="End date in YYYY-MM-DD format")
        parser.add_argument(
            "--granularity",
            default="DAY",
            choices=["DAY", "MONTH"],
            help="LinkedIn time interval granularity",
        )

    def _fetch_or_empty(self, label, fetcher, *args):
        try:
            return fetcher(*args)
        except LinkedInAPIError as exc:
            self.stdout.write(self.style.WARNING(f"LinkedIn {label} unavailable: {exc}"))
            return {}

    def _post_ids(self, post_payload):
        post_ids = []
        for item in post_payload.get("elements", []) or []:
            post_id = item.get("id") or item.get("post") or item.get("ugcPost") or item.get("share")
            if post_id:
                post_ids.append(str(post_id))
        return post_ids

    def _snapshot_daily_row(self, day, share_data, page_data):
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

    def handle(self, *args, **options):
        default_start, default_end = default_date_range()
        start_date = parse_date(options.get("start_date"), default_start)
        end_date = parse_date(options.get("end_date"), default_end)
        granularity = options["granularity"]

        self.stdout.write(f"Starting LinkedIn Analytics Sync for {start_date} to {end_date}...")

        try:
            linkedin = LinkedInService()

            share_data = self._fetch_or_empty(
                "time-bound share statistics",
                linkedin.get_share_statistics,
                start_date,
                end_date,
                granularity,
            )
            follower_time_data = self._fetch_or_empty(
                "time-bound follower statistics",
                linkedin.get_follower_statistics,
                start_date,
                end_date,
                granularity,
            )
            page_data = self._fetch_or_empty(
                "time-bound page statistics",
                linkedin.get_page_statistics,
                start_date,
                end_date,
                granularity,
            )
            followers_data = self._fetch_or_empty("follower count", linkedin.get_followers_count)
            audience_data = self._fetch_or_empty("audience demographics", linkedin.get_follower_demographics)
            post_metadata = self._fetch_or_empty("organization posts", linkedin.get_organization_posts)

            lifetime_share_data = self._fetch_or_empty("lifetime share statistics", linkedin.get_share_statistics)
            lifetime_page_data = self._fetch_or_empty("lifetime page statistics", linkedin.get_page_statistics)
            post_share_data = {}
            post_ids = self._post_ids(post_metadata)
            if post_ids:
                post_share_data = self._fetch_or_empty(
                    "post-level share statistics",
                    linkedin.get_share_statistics,
                    None,
                    None,
                    granularity,
                    post_ids,
                )

            daily_metrics = merge_daily_rows(
                normalize_share_time_series(share_data.get("elements", [])),
                normalize_page_time_series(page_data.get("elements", [])),
                normalize_follower_time_series(follower_time_data.get("elements", [])),
            )
            if not daily_metrics:
                daily_metrics = [self._snapshot_daily_row(end_date, lifetime_share_data, lifetime_page_data)]
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
            for post in post_analytics:
                if not post.get("postId"):
                    logger.warning("LinkedIn post analytics row missing postId: %s", post)
                if not post.get("contentType"):
                    logger.warning("LinkedIn post analytics row missing contentType: %s", post)

            followers = followers_data.get("firstDegreeSize", 0)
            analytics_data = {
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

            save_analytics(analytics_data)

            self.stdout.write(
                self.style.SUCCESS(
                    f"LinkedIn analytics synced successfully: {len(daily_metrics)} daily rows, "
                    f"{len(post_analytics)} post rows, {len(audience_analytics)} audience rows"
                )
            )

        except Exception as exc:
            logger.exception("Error syncing LinkedIn analytics")
            self.stdout.write(self.style.ERROR(f"Error syncing analytics: {str(exc)}"))
