"""Platform-agnostic HTTP transport for analytics API clients.

The retry/backoff loop, one-shot 401→refresh, and timeout handling are identical
for every social platform we integrate; only the auth header, the set of
"do-not-retry" responses, and the exception type differ. Those are the hooks a
subclass overrides. LinkedIn and Instagram clients both build on this so the
resilient-request behavior lives in exactly one place.
"""

import logging
import time

import requests

logger = logging.getLogger(__name__)


class AnalyticsAPIError(Exception):
    """Base error for any analytics API client."""


class BaseAnalyticsClient:
    # Transient statuses worth retrying with backoff.
    RETRY_STATUS = (429, 500, 502, 503, 504)
    # Subclasses set a more specific error type so callers can catch per-platform.
    error_class = AnalyticsAPIError

    def __init__(self, access_token=None, timeout=20, page_size=100):
        self.access_token = access_token
        self.timeout = timeout
        self.page_size = page_size

    # --- hooks a subclass is expected to override -------------------------

    def headers(self):
        """Auth/version headers for every request."""
        return {}

    def _refresh_access_token(self):
        """Attempt a one-shot token refresh; return True and update
        ``self.access_token`` on success. Default: no refresh available."""
        return False

    def _should_not_retry(self, response):
        """Return True for error responses that must fail fast rather than retry
        (e.g. a budget/quota error that only clears at a future window)."""
        return False

    def _is_auth_error(self, response):
        """Return True when a response indicates an expired/revoked token that a
        refresh could fix. Defaults to HTTP 401; override for APIs that signal
        auth failures differently (e.g. Meta's 400 + OAuthException)."""
        return response.status_code == 401

    # --- shared transport --------------------------------------------------

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

            if self._should_not_retry(response):
                logger.warning("%s fast-fail response: %s", type(self).__name__, response.text[:200])
                raise self.error_class(
                    f"{type(self).__name__} returned {response.status_code}: {response.text[:300]}"
                )

            if response.status_code in self.RETRY_STATUS and attempt < retries - 1:
                retry_after = response.headers.get("Retry-After")
                sleep_for = int(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt
                logger.warning("%s retry in %s seconds: %s", type(self).__name__, sleep_for, response.text[:300])
                time.sleep(sleep_for)
                continue

            # A revoked/expired token surfaces as 401 (or 400/OAuthException on
            # Meta); try one refresh then retry.
            if self._is_auth_error(response) and not token_refreshed:
                token_refreshed = True
                if self._refresh_access_token():
                    continue

            if not response.ok:
                logger.error("%s error %s: %s", type(self).__name__, response.status_code, response.text[:1000])
                raise self.error_class(
                    f"{type(self).__name__} returned {response.status_code}: {response.text[:300]}"
                )

            if not response.content:
                return {}
            return response.json()

        raise self.error_class(f"{type(self).__name__} request failed after retries")
