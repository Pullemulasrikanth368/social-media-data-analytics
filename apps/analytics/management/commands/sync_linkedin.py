import logging

from django.core.management.base import BaseCommand

from apps.analytics.services.analytics_sync import collect_linkedin_analytics
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

    def handle(self, *args, **options):
        granularity = options["granularity"]

        try:
            analytics_data = collect_linkedin_analytics(
                start_date=options.get("start_date"),
                end_date=options.get("end_date"),
                granularity=granularity,
                warn=lambda msg: self.stdout.write(self.style.WARNING(msg)),
            )
            sync_range = analytics_data.get("sync_range", {})
            self.stdout.write(
                f"Starting LinkedIn Analytics Sync for "
                f"{sync_range.get('start_date')} to {sync_range.get('end_date')}..."
            )

            save_analytics(analytics_data)

            self.stdout.write(
                self.style.SUCCESS(
                    f"LinkedIn analytics synced successfully: "
                    f"{len(analytics_data.get('daily_metrics', []))} daily rows, "
                    f"{len(analytics_data.get('post_analytics', []))} post rows, "
                    f"{len(analytics_data.get('audience_analytics', []))} audience rows"
                )
            )

        except Exception as exc:
            logger.exception("Error syncing LinkedIn analytics")
            self.stdout.write(self.style.ERROR(f"Error syncing analytics: {str(exc)}"))
