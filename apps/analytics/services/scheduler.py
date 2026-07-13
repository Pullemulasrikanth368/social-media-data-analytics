from apscheduler.schedulers.background import BackgroundScheduler

from django.core.management import call_command

from datetime import datetime


def run_linkedin_sync():

    print("Running LinkedIn Sync:", datetime.now())

    try:

        call_command("sync_linkedin")

        print("LinkedIn Sync Completed")

    except Exception as e:

        print("LinkedIn Sync Failed:", str(e))


def start():

    scheduler = BackgroundScheduler()

    # Everyday midnight
    scheduler.add_job(
        run_linkedin_sync,
        'cron',
        hour=0,
        minute=2
    )

    scheduler.start()

    print("LinkedIn Scheduler Started")