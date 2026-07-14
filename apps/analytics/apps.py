import os

from django.apps import AppConfig


class AnalyticsConfig(AppConfig):

    default_auto_field = 'django.db.models.BigAutoField'

    name = 'apps.analytics'

    def ready(self):

        # Best-effort index creation; never block startup on a MongoDB outage.
        try:
            from .services.mongo_service import _ensure_indexes

            _ensure_indexes()
        except Exception:
            pass

        # Prevent duplicate scheduler in development
        if os.environ.get('RUN_MAIN') == 'true':

            from .services.scheduler import start

            start()