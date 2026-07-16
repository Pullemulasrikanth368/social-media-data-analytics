import logging

from django.core.management.base import BaseCommand

from apps.analytics.services import registry
from apps.analytics.services.mongo_service import save_analytics

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Sync analytics data for a given platform (linkedin, instagram, ...)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--platform",
            default="linkedin",
            help=f"Platform to sync. One of: {', '.join(registry.supported_platforms())}",
        )
        parser.add_argument("--start-date", help="Start date in YYYY-MM-DD format")
        parser.add_argument("--end-date", help="End date in YYYY-MM-DD format")
        parser.add_argument(
            "--granularity",
            default="DAY",
            choices=["DAY", "MONTH"],
            help="Time interval granularity",
        )

    def handle(self, *args, **options):
        platform = registry.normalize_platform(options["platform"])
        if not registry.is_supported(platform):
            self.stdout.write(self.style.ERROR(f"Unsupported platform: {options['platform']}"))
            return

        try:
            collect = registry.get_collector(platform)
            analytics_data = collect(
                start_date=options.get("start_date"),
                end_date=options.get("end_date"),
                granularity=options["granularity"],
                warn=lambda msg: self.stdout.write(self.style.WARNING(msg)),
            )
            sync_range = analytics_data.get("sync_range", {})
            self.stdout.write(
                f"Starting {platform} analytics sync for "
                f"{sync_range.get('start_date')} to {sync_range.get('end_date')}..."
            )

            save_analytics(analytics_data, platform=platform)

            self.stdout.write(
                self.style.SUCCESS(
                    f"{platform} analytics synced successfully: "
                    f"{len(analytics_data.get('daily_metrics', []))} daily rows, "
                    f"{len(analytics_data.get('post_analytics', []))} post rows, "
                    f"{len(analytics_data.get('audience_analytics', []))} audience rows"
                )
            )
        except Exception as exc:
            logger.exception("Error syncing %s analytics", platform)
            self.stdout.write(self.style.ERROR(f"Error syncing analytics: {str(exc)}"))
