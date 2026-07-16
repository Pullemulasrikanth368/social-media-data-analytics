from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from django.conf import settings
from django.core.management import call_command


def run_analytics_sync():
    """Sync every platform listed in ANALYTICS_SYNC_PLATFORMS (default: linkedin)."""
    platforms = getattr(settings, "ANALYTICS_SYNC_PLATFORMS", ["linkedin"])
    for platform in platforms:
        print(f"Running {platform} sync:", datetime.now())
        try:
            call_command("sync_analytics", platform=platform)
            print(f"{platform} sync completed")
        except Exception as e:
            print(f"{platform} sync failed:", str(e))


def run_instagram_token_refresh():
    """Keep the long-lived Instagram token fresh independently of dashboard
    traffic. Meta tokens expire ~60 days after issue and must be re-exchanged
    while still valid; a daily proactive check means they never lapse just
    because nobody opened Looker Studio near the expiry date.

    (LinkedIn is intentionally not refreshed here: it uses a rotating refresh
    token and is renewed reactively on the next API call, so it needs no
    scheduled extension.)
    """
    print("Running Instagram token refresh:", datetime.now())
    try:
        call_command("refresh_instagram_token")
        print("Instagram token refresh completed")
    except SystemExit:
        # The command exits non-zero when a refresh fails; it already logged an
        # actionable re-auth message. Don't let it kill the scheduler thread.
        print("Instagram token refresh reported failure; see logs for re-auth steps")
    except Exception as e:
        print("Instagram token refresh failed:", str(e))


def start():

    scheduler = BackgroundScheduler()

    # Everyday midnight
    scheduler.add_job(
        run_analytics_sync,
        'cron',
        hour=0,
        minute=2
    )

    # Daily proactive Instagram token refresh (well before the ~60-day expiry).
    scheduler.add_job(
        run_instagram_token_refresh,
        'cron',
        hour=0,
        minute=10
    )

    scheduler.start()

    print("Analytics scheduler started")