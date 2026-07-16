"""Platform registry — the single place that maps a platform name to the
collector, client, account id, metric-field set, and engagement formula backing
it.

Adding a new platform (Facebook, X, YouTube …) is: write its sibling
``*_service`` / ``*_sync`` / ``*_utils`` modules, then add one entry here. The
storage, view, cache, and scheduling layers stay platform-agnostic and never
grow per-platform ``if`` branches.

Platform factories import their modules lazily so importing this registry (e.g.
from ``mongo_service``) never drags in every platform's dependencies or risks a
circular import.
"""

from django.conf import settings


# Uniform dashboard-row builder signature across platforms:
#   builder(daily_rows, post_rows, audience_rows, report_date) -> [rows]
# so the storage layer can rebuild rows without knowing platform specifics.
def _linkedin_dashboard_rows(daily_rows, post_rows, audience_rows, report_date):
    from .analytics_utils import build_dashboard_rows

    return build_dashboard_rows(daily_rows, audience_rows, post_rows, report_date)


def _linkedin():
    from . import analytics_utils
    from .analytics_sync import collect_linkedin_analytics
    from .linkedin_service import LinkedInService

    return {
        "name": "linkedin",
        "collector": collect_linkedin_analytics,
        "service": LinkedInService,
        "account_id": getattr(settings, "LINKEDIN_ORG_ID", None),
        "metric_fields": analytics_utils.METRIC_FIELDS,
        "engagement_fn": analytics_utils.engagement_rate,
        "dashboard_row_builder": _linkedin_dashboard_rows,
    }


def _instagram():
    from .instagram_service import InstagramService
    from .instagram_sync import collect_instagram_analytics
    from .instagram_utils import (
        INSTAGRAM_METRIC_FIELDS,
        build_instagram_dashboard_rows,
        instagram_engagement_rate,
    )

    return {
        "name": "instagram",
        "collector": collect_instagram_analytics,
        "service": InstagramService,
        "account_id": getattr(settings, "INSTAGRAM_BUSINESS_ACCOUNT_ID", None),
        "metric_fields": INSTAGRAM_METRIC_FIELDS,
        "engagement_fn": instagram_engagement_rate,
        # build_instagram_dashboard_rows already matches the uniform signature
        # (daily, media/post, audience, report_date).
        "dashboard_row_builder": build_instagram_dashboard_rows,
    }


_PLATFORMS = {
    "linkedin": _linkedin,
    "instagram": _instagram,
}

DEFAULT_PLATFORM = "linkedin"


def supported_platforms():
    return tuple(_PLATFORMS)


def normalize_platform(platform):
    return (platform or DEFAULT_PLATFORM).strip().lower()


def is_supported(platform):
    return normalize_platform(platform) in _PLATFORMS


def get_platform(platform=None):
    name = normalize_platform(platform)
    factory = _PLATFORMS.get(name)
    if not factory:
        raise ValueError(f"Unsupported analytics platform: {platform!r}")
    return factory()


def get_collector(platform=None):
    return get_platform(platform)["collector"]


def get_account_id(platform=None):
    return get_platform(platform).get("account_id")


def get_metric_config(platform=None):
    """(metric_fields, engagement_fn) for a platform; falls back to LinkedIn's
    defaults when the platform can't be resolved (degraded read paths)."""
    try:
        entry = get_platform(platform)
        return entry["metric_fields"], entry["engagement_fn"]
    except Exception:
        from .analytics_utils import METRIC_FIELDS, engagement_rate

        return METRIC_FIELDS, engagement_rate


def get_dashboard_row_builder(platform=None):
    """The platform's dashboard-row builder with the uniform signature
    ``builder(daily_rows, post_rows, audience_rows, report_date)``."""
    return get_platform(platform)["dashboard_row_builder"]
