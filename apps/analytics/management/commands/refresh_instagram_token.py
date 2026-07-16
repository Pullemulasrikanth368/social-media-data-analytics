"""Proactively extend the long-lived Instagram (Meta) token before it expires.

This is the background-refresh entry point: the nightly scheduler
(``services/scheduler.py``) calls it daily, so the token is kept fresh whether or
not anyone opens the dashboard. It can also be run by hand:

    ./venv/bin/python manage.py refresh_instagram_token          # refresh only if due
    ./venv/bin/python manage.py refresh_instagram_token --force  # refresh now

Refreshing is only *due* when the stored token is within
INSTAGRAM_TOKEN_REFRESH_LEAD_DAYS of expiring (default 7); otherwise this is a
cheap no-op. On failure it exits non-zero with an actionable re-auth message and
never prints the token itself.
"""

from django.core.management.base import BaseCommand

from apps.analytics.services import instagram_auth
from apps.analytics.services.mongo_service import get_latest_token_expiry


class Command(BaseCommand):
    help = "Proactively refresh (extend) the long-lived Instagram access token when near expiry"

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Refresh now regardless of how far off expiry is",
        )

    def handle(self, *args, **options):
        before = get_latest_token_expiry(platform="instagram")
        due = options["force"] or instagram_auth.is_refresh_due(before)

        if not due:
            # is_refresh_due only returns False when `before` is a known,
            # comfortably-future expiry, so this is always a real timestamp.
            self.stdout.write(self.style.SUCCESS(
                f"Instagram token still valid; no refresh needed (expires at {before.isoformat()})."
            ))
            return

        new_token = instagram_auth.refresh_access_token()

        if not new_token:
            self.stderr.write(self.style.ERROR(
                "Instagram token refresh failed. The token may be expired or its "
                f"permissions revoked — re-authenticate via {instagram_auth.REAUTH_ROUTE}."
            ))
            # Signal failure to cron/monitoring without printing token values.
            raise SystemExit(1)

        after = get_latest_token_expiry(platform="instagram")
        expiry_note = after.isoformat() if after else "unknown"
        self.stdout.write(self.style.SUCCESS(f"Instagram token refreshed; now expires at {expiry_note}."))
