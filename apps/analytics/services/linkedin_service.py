import logging
import time
from urllib.parse import quote, urlencode

import requests
from django.conf import settings

from .analytics_utils import linkedin_time_interval_params, linkedin_time_intervals

logger = logging.getLogger(__name__)
DEFAULT_LINKEDIN_API_VERSION = "202604"


class LinkedInAPIError(Exception):
    pass


class LinkedInService:
    REST_BASE_URL = "https://api.linkedin.com/rest"
    V2_BASE_URL = "https://api.linkedin.com/v2"

    def __init__(self, access_token=None, org_id=None, api_version=None):
        self.access_token = access_token or self._latest_saved_access_token() or settings.LINKEDIN_ACCESS_TOKEN
        self.org_id = org_id or settings.LINKEDIN_ORG_ID
        configured_version = api_version or getattr(settings, "LINKEDIN_API_VERSION", DEFAULT_LINKEDIN_API_VERSION)
        self.api_version = self._normalize_api_version(configured_version)
        self.timeout = getattr(settings, "LINKEDIN_REQUEST_TIMEOUT", 20)
        self.page_size = getattr(settings, "LINKEDIN_PAGE_SIZE", 100)

    @staticmethod
    def _latest_saved_access_token():
        try:
            from .mongo_service import get_latest_access_token

            return get_latest_access_token()
        except Exception:
            logger.exception("Could not load latest LinkedIn access token from MongoDB")
            return None

    @staticmethod
    def _normalize_api_version(version):
        # Honor any valid 6-digit YYYYMM version from settings/.env so the API
        # version can be bumped without a code change; fall back to the known-good
        # default only when the configured value is malformed.
        version = str(version or DEFAULT_LINKEDIN_API_VERSION).strip()
        if len(version) >= 6 and version[:6].isdigit():
            return version[:6]
        return DEFAULT_LINKEDIN_API_VERSION

    @property
    def organization_urn(self):
        if str(self.org_id).startswith("urn:li:"):
            return self.org_id
        return f"urn:li:organization:{self.org_id}"

    @property
    def encoded_organization_urn(self):
        return quote(self.organization_urn, safe="")

    def headers(self):
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Linkedin-Version": self.api_version,
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
        }

    def _refresh_access_token(self):
        """Attempt a one-shot token refresh; update self.access_token on success."""
        try:
            from .auth_service import refresh_access_token

            new_token = refresh_access_token()
        except Exception:
            logger.exception("LinkedIn token refresh raised")
            return False
        if new_token:
            self.access_token = new_token
            logger.info("Refreshed LinkedIn access token after auth failure")
            return True
        return False

    @staticmethod
    def _is_privacy_cost_error(response):
        # LinkedIn's differential-privacy budget error. It is NOT a transient rate
        # limit: it only clears at a future refresh window, so retrying wastes time
        # and consumes more budget. Fail fast and let the caller degrade gracefully.
        if response.status_code != 429:
            return False
        if "TABLE_MAX_PRIVACY_COST_EXCEEDED" in (response.text or ""):
            return True
        try:
            return response.json().get("code") == "TABLE_MAX_PRIVACY_COST_EXCEEDED"
        except ValueError:
            return False

    def _request(self, method, url, params=None, retries=3):
        token_refreshed = False
        for attempt in range(retries):
            response = requests.request(
                method,
                url,
                headers=self.headers(),
                params=params,
                timeout=self.timeout,
            )

            # Privacy-budget exhaustion: do not retry.
            if self._is_privacy_cost_error(response):
                logger.warning("LinkedIn privacy budget exhausted (no retry): %s", response.text[:200])
                raise LinkedInAPIError(f"LinkedIn API returned {response.status_code}: {response.text[:300]}")

            if response.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                retry_after = response.headers.get("Retry-After")
                sleep_for = int(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt
                logger.warning("LinkedIn API retry in %s seconds: %s", sleep_for, response.text[:300])
                time.sleep(sleep_for)
                continue

            # A revoked/expired token surfaces as 401; try one refresh then retry.
            if response.status_code == 401 and not token_refreshed:
                token_refreshed = True
                if self._refresh_access_token():
                    continue

            if not response.ok:
                logger.error("LinkedIn API error %s: %s", response.status_code, response.text[:1000])
                raise LinkedInAPIError(f"LinkedIn API returned {response.status_code}: {response.text[:300]}")

            if not response.content:
                return {}
            return response.json()

        raise LinkedInAPIError("LinkedIn API request failed after retries")

    def _get(self, path, params=None, use_rest=True):
        base_url = self.REST_BASE_URL if use_rest else self.V2_BASE_URL
        return self._request("GET", f"{base_url}/{path.lstrip('/')}", params=params or {})

    def _get_raw_query(self, path, query, use_rest=True):
        base_url = self.REST_BASE_URL if use_rest else self.V2_BASE_URL
        return self._request("GET", f"{base_url}/{path.lstrip('/')}?{query}")

    def _get_paginated(self, path, params=None, use_rest=True):
        params = dict(params or {})
        params.setdefault("count", self.page_size)
        params.setdefault("start", 0)
        elements = []
        last_payload = {}

        while True:
            payload = self._get(path, params=params, use_rest=use_rest)
            last_payload = payload
            page_elements = payload.get("elements", [])
            elements.extend(page_elements)

            paging = payload.get("paging") or {}
            count = int(paging.get("count") or len(page_elements) or 0)
            start = int(paging.get("start") or params.get("start") or 0)
            if not page_elements or len(page_elements) < count:
                break
            params["start"] = start + count

        last_payload["elements"] = elements
        return last_payload

    def _time_bound_get(self, path, params, start_date, end_date, granularity, use_rest=True):
        query = urlencode(params)
        query = f"{query}&timeIntervals={linkedin_time_intervals(start_date, end_date, granularity)}"
        try:
            return self._get_raw_query(path, query, use_rest=use_rest)
        except LinkedInAPIError:
            dotted_params = dict(params)
            dotted_params.update(linkedin_time_interval_params(start_date, end_date, granularity))
            return self._get(path, params=dotted_params, use_rest=use_rest)

    SHARE_STATS_BATCH = 20

    def get_share_statistics(self, start_date=None, end_date=None, granularity="DAY"):
        params = {
            "q": "organizationalEntity",
            "organizationalEntity": self.organization_urn,
        }
        if start_date and end_date:
            return self._time_bound_get(
                "organizationalEntityShareStatistics",
                params,
                start_date,
                end_date,
                granularity,
            )
        return self._get("organizationalEntityShareStatistics", params=params)

    def get_post_share_statistics(self, post_ids):
        """Per-post lifetime share statistics.

        ``organizationalEntityShareStatistics`` accepts share URNs under the
        ``shares`` parameter and UGC-post URNs under ``ugcPosts`` — mixing them
        (or sending UGC URNs as ``shares``) yields a 400. Split by URN type,
        batch to keep URLs short, and merge the elements.
        """
        shares = [i for i in post_ids if str(i).startswith("urn:li:share:")]
        ugc_posts = [i for i in post_ids if str(i).startswith("urn:li:ugcPost:")]

        elements = []
        for param_name, urns in (("shares", shares), ("ugcPosts", ugc_posts)):
            for start in range(0, len(urns), self.SHARE_STATS_BATCH):
                batch = urns[start:start + self.SHARE_STATS_BATCH]
                if not batch:
                    continue
                list_value = "List(" + ",".join(quote(str(u), safe="") for u in batch) + ")"
                query = (
                    f"q=organizationalEntity"
                    f"&organizationalEntity={self.encoded_organization_urn}"
                    f"&{param_name}={list_value}"
                )
                payload = self._get_raw_query("organizationalEntityShareStatistics", query)
                elements.extend(payload.get("elements", []))
        return {"elements": elements}

    def get_followers_count(self):
        path = f"networkSizes/{self.encoded_organization_urn}"
        params = {"edgeType": "CompanyFollowedByMember"}
        return self._get(path, params=params, use_rest=False)

    def get_follower_statistics(self, start_date=None, end_date=None, granularity="DAY"):
        params = {
            "q": "organizationalEntity",
            "organizationalEntity": self.organization_urn,
        }
        if start_date and end_date:
            return self._time_bound_get(
                "organizationalEntityFollowerStatistics",
                params,
                start_date,
                end_date,
                granularity,
            )
        return self._get_paginated("organizationalEntityFollowerStatistics", params=params)

    def get_page_statistics(self, start_date=None, end_date=None, granularity="DAY"):
        params = {
            "q": "organization",
            "organization": self.organization_urn,
        }
        if start_date and end_date:
            return self._time_bound_get(
                "organizationPageStatistics",
                params,
                start_date,
                end_date,
                granularity,
            )
        return self._get_paginated("organizationPageStatistics", params=params)

    def get_follower_demographics(self):
        return self.get_follower_statistics()

    def get_organization_posts(self):
        params = {
            "q": "author",
            "author": self.organization_urn,
            "sortBy": "LAST_MODIFIED",
        }
        try:
            return self._get_paginated("posts", params=params)
        except LinkedInAPIError:
            legacy_params = {
                "q": "authors",
                "authors": f"List({self.organization_urn})",
            }
            return self._get_paginated("ugcPosts", params=legacy_params, use_rest=False)
