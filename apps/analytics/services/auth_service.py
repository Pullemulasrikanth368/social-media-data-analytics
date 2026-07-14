"""LinkedIn OAuth token lifecycle.

LinkedIn access tokens expire after ~60 days and are *revoked* the moment a new
token is generated for the same member/app. LinkedIn rotates refresh tokens on
use (each refresh returns a new refresh token), so the newest token must always
be persisted. MongoDB (``tokens`` collection) is the source of truth; ``.env``
only provides the bootstrap refresh token for the very first refresh.
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

ACCESS_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
REQUEST_TIMEOUT = 20


def _current_refresh_token():
    # Mongo first (rotated tokens), then the bootstrap value from settings/.env.
    try:
        from .mongo_service import get_latest_refresh_token

        stored = get_latest_refresh_token()
        if stored:
            return stored
    except Exception:
        logger.exception("Could not read latest refresh token from MongoDB")
    return settings.LINKEDIN_REFRESH_TOKEN


def refresh_access_token():
    """Exchange the current refresh token for a fresh access token.

    Persists the full token response (including the rotated refresh token) to
    MongoDB and returns the new access token, or ``None`` on failure.
    """
    refresh_token = _current_refresh_token()
    if not refresh_token or refresh_token == "your_refresh_token":
        logger.error("No usable LinkedIn refresh token available; re-authorize via /api/login/")
        return None

    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": settings.LINKEDIN_CLIENT_ID,
        "client_secret": settings.LINKEDIN_CLIENT_SECRET,
    }

    try:
        response = requests.post(ACCESS_TOKEN_URL, data=payload, timeout=REQUEST_TIMEOUT)
    except requests.exceptions.RequestException:
        logger.exception("Network error while refreshing LinkedIn access token")
        return None

    token_data = {}
    try:
        token_data = response.json()
    except ValueError:
        logger.error("LinkedIn refresh returned non-JSON response (%s)", response.status_code)
        return None

    if response.status_code != 200 or "access_token" not in token_data:
        logger.error("LinkedIn refresh failed (%s): %s", response.status_code, token_data.get("error_description"))
        return None

    # Carry the previous refresh token forward if LinkedIn did not return a new one.
    token_data.setdefault("refresh_token", refresh_token)
    try:
        from .mongo_service import save_token

        save_token(token_data)
    except Exception:
        logger.exception("Refreshed LinkedIn token but failed to persist it to MongoDB")

    return token_data.get("access_token")


def get_access_token():
    """Backward-compatible helper: return a freshly refreshed access token."""
    return refresh_access_token()
