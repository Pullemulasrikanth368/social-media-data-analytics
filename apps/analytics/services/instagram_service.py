"""Instagram (Meta Graph API, Facebook-Login path) API client.

Builds on ``BaseAnalyticsClient`` for the shared retry/backoff/refresh transport;
adds Meta specifics: base URL + version, access-token-as-query-param auth,
cursor-based pagination (``paging.next``), Unix ``since``/``until`` windows, and
the account/media/insights/demographics endpoints.

Targets Graph API v24.0+ and uses the ``views``-based metric set (Meta removed
impressions / video_views / plays and the profile-action click metrics — see
docs/instagram-insights-analysis.md §2).
"""

import logging

from django.conf import settings

from .analytics_utils import parse_date
from .base_service import AnalyticsAPIError, BaseAnalyticsClient
from .instagram_utils import (
    ACCOUNT_TIMESERIES_METRICS,
    ACCOUNT_TOTAL_METRICS,
    MEDIA_INSIGHT_METRICS,
    string_value,
)

logger = logging.getLogger(__name__)

DEFAULT_META_API_VERSION = "v24.0"
MEDIA_FIELDS = "id,caption,media_type,media_product_type,timestamp,permalink,like_count,comments_count"


class InstagramAPIError(AnalyticsAPIError):
    pass


def _to_unix(value):
    from datetime import datetime, time, timezone

    day = parse_date(value)
    return int(datetime.combine(day, time.min, tzinfo=timezone.utc).timestamp())


# Seconds in a day — `until` is made exclusive (end + 1 day) so that a single-day
# window still satisfies Meta's "since must be less than until" rule.
_ONE_DAY = 86400


class InstagramService(BaseAnalyticsClient):
    error_class = InstagramAPIError

    def __init__(self, access_token=None, ig_user_id=None, api_version=None):
        resolved_token = (
            access_token
            or self._latest_saved_access_token()
            or getattr(settings, "INSTAGRAM_ACCESS_TOKEN", None)
        )
        # Proactive refresh: when we rely on the stored/bootstrap token, extend it
        # first if it is close to expiring so we never issue a request with an
        # about-to-lapse token. An explicitly injected token is left untouched —
        # it is the caller's to manage. Best-effort; never blocks construction.
        if access_token is None:
            from .instagram_auth import ensure_fresh_token

            resolved_token = ensure_fresh_token(current_token=resolved_token) or resolved_token
        super().__init__(
            access_token=resolved_token,
            timeout=getattr(settings, "META_REQUEST_TIMEOUT", 20),
            page_size=getattr(settings, "META_PAGE_SIZE", 100),
        )
        # The IG Business Account ID (a.k.a. ig-user-id) is the analytics subject,
        # analogous to LinkedIn's organization id.
        self.ig_user_id = ig_user_id or getattr(settings, "INSTAGRAM_BUSINESS_ACCOUNT_ID", None)
        self.api_version = api_version or getattr(settings, "META_GRAPH_API_VERSION", DEFAULT_META_API_VERSION)

    # account id exposed under the same name the storage layer uses everywhere.
    @property
    def org_id(self):
        return self.ig_user_id

    @property
    def base_url(self):
        return f"https://graph.facebook.com/{self.api_version}"

    @staticmethod
    def _latest_saved_access_token():
        try:
            from .mongo_service import get_latest_access_token

            return get_latest_access_token(platform="instagram")
        except Exception:
            logger.exception("Could not load latest Instagram access token from MongoDB")
            return None

    def headers(self):
        # Meta authenticates via the access_token query param (added in _get), so
        # no auth header is required here.
        return {"Accept": "application/json"}

    def _is_auth_error(self, response):
        # Meta signals an expired/invalid token as HTTP 400 with an OAuthException
        # (error code 190), not 401.
        if response.status_code == 401:
            return True
        if response.status_code == 400:
            try:
                return (response.json().get("error") or {}).get("code") == 190
            except ValueError:
                return False
        return False

    def _refresh_access_token(self):
        try:
            from .instagram_auth import refresh_access_token

            new_token = refresh_access_token()
        except Exception:
            logger.exception("Instagram token refresh raised")
            return False
        if new_token:
            self.access_token = new_token
            logger.info("Refreshed Instagram access token after auth failure")
            return True
        return False

    def _get(self, path, params=None):
        params = dict(params or {})
        params.setdefault("access_token", self.access_token)
        return self._request("GET", f"{self.base_url}/{path.lstrip('/')}", params=params)

    def _get_paginated(self, path, params=None):
        """Follow Meta cursor pagination (``paging.next``) and accumulate ``data``."""
        params = dict(params or {})
        params.setdefault("access_token", self.access_token)
        params.setdefault("limit", self.page_size)
        elements = []
        last_payload = {}
        page = 0
        while True:
            payload = self._request("GET", f"{self.base_url}/{path.lstrip('/')}", params=params)
            last_payload = payload
            elements.extend(payload.get("data", []) or [])

            after = (((payload.get("paging") or {}).get("cursors")) or {}).get("after")
            has_next = bool((payload.get("paging") or {}).get("next"))
            page += 1
            if not after or not has_next or page > 100:
                break
            params["after"] = after

        last_payload["data"] = elements
        return last_payload

    # --- endpoints ---------------------------------------------------------

    def _require_account(self):
        if not self.ig_user_id:
            raise InstagramAPIError(
                "INSTAGRAM_BUSINESS_ACCOUNT_ID is not configured; cannot query Instagram insights"
            )

    def get_account(self):
        """Account profile fields, incl. total ``followers_count``."""
        self._require_account()
        return self._get(self.ig_user_id, params={"fields": "id,username,followers_count,media_count"})

    def get_account_timeseries(self, start_date, end_date):
        """Per-day account metrics (only `reach` supports a daily series on v25).
        The window must be <= 30 days — callers chunk longer ranges."""
        self._require_account()
        params = {
            "metric": ",".join(ACCOUNT_TIMESERIES_METRICS),
            "period": "day",
            "metric_type": "time_series",
            "since": _to_unix(start_date),
            "until": _to_unix(end_date) + _ONE_DAY,
        }
        return self._get(f"{self.ig_user_id}/insights", params=params)

    def get_account_totals(self, start_date, end_date):
        """Window-total account metrics (views, interactions, accounts engaged,
        net follows) — total_value-only on v25. Window must be <= 30 days."""
        self._require_account()
        params = {
            "metric": ",".join(ACCOUNT_TOTAL_METRICS),
            "period": "day",
            "metric_type": "total_value",
            "since": _to_unix(start_date),
            "until": _to_unix(end_date) + _ONE_DAY,
        }
        return self._get(f"{self.ig_user_id}/insights", params=params)

    def get_follower_demographics(self, breakdown="country"):
        """Lifetime follower demographics for a single breakdown (country|city)."""
        self._require_account()
        params = {
            "metric": "follower_demographics",
            "period": "lifetime",
            "metric_type": "total_value",
            "breakdown": breakdown,
        }
        return self._get(f"{self.ig_user_id}/insights", params=params)

    def get_media(self, start_date, end_date):
        """Media published in [start, end] with the fields the dashboard needs."""
        self._require_account()
        params = {
            "fields": MEDIA_FIELDS,
            "since": _to_unix(start_date),
            "until": _to_unix(end_date) + _ONE_DAY,
        }
        return self._get_paginated(f"{self.ig_user_id}/media", params=params)

    def get_media_insights(self, media_id, product_type="FEED"):
        """Insights for one media, using the metric set valid for its product type."""
        metrics = MEDIA_INSIGHT_METRICS.get(string_value(product_type).upper(), MEDIA_INSIGHT_METRICS["FEED"])
        return self._get(f"{media_id}/insights", params={"metric": ",".join(metrics)})
