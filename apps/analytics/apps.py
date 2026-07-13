import os

from django.apps import AppConfig


class AnalyticsConfig(AppConfig):

    default_auto_field = 'django.db.models.BigAutoField'

    name = 'apps.analytics'

    def ready(self):

        # Prevent duplicate scheduler in development
        if os.environ.get('RUN_MAIN') == 'true':

            from .services.scheduler import start

            start()