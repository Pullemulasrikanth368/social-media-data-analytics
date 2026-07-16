"""Backward-compatible alias for ``sync_analytics --platform linkedin``.

The existing cron (scripts/sync_linkedin_daily.sh) and scheduler call
``manage.py sync_linkedin``; keep that entry point working by delegating to the
generic multi-platform command.
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Sync LinkedIn analytics data (alias for `sync_analytics --platform linkedin`)"

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
        call_command(
            "sync_analytics",
            platform="linkedin",
            start_date=options.get("start_date"),
            end_date=options.get("end_date"),
            granularity=options["granularity"],
        )
