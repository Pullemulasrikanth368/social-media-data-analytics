"""Instagram (Meta, Facebook-Login path) token lifecycle.

Mirrors ``auth_service`` (LinkedIn) but for Meta's model:

- Long-lived tokens last ~60 days and are *extended* (not rotated via a separate
  refresh token) by re-exchanging them with ``grant_type=fb_exchange_token``.
- A token not extended within 60 days expires unrecoverably → the user must
  re-authenticate. Extend well before expiry (≈ day 50).

MongoDB (``tokens`` collection, ``platform="instagram"``) is the source of truth;
``.env`` provides only the bootstrap ``INSTAGRAM_ACCESS_TOKEN`` for first use.

Also includes one-time setup helpers used during manual onboarding: exchanging a
short-lived token for a long-lived one, and discovering the IG Business Account
ID connected to the user's Facebook Page.
"""

import logging
from datetime import datetime, timedelta, timezone

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20

# The Meta app dashboard is where the user re-authorizes; name the route so
# failure logs are actionable rather than just "refresh failed".
REAUTH_ROUTE = "/api/instagram/login/"


def _utcnow():
    return datetime.now(timezone.utc)


def _refresh_lead():
    """Refresh once the token is within this many days of expiring."""
    return timedelta(days=int(getattr(settings, "INSTAGRAM_TOKEN_REFRESH_LEAD_DAYS", 7)))


def _mask(token):
    """Render a token safe for logs: never emit the full value (requirement 5)."""
    if not token:
        return "<none>"
    return f"…{token[-4:]} (len {len(token)})"


def _graph_base():
    version = getattr(settings, "META_GRAPH_API_VERSION", "v24.0")
    return f"https://graph.facebook.com/{version}"


def _current_access_token():
    # Mongo first (extended tokens), then the bootstrap value from settings/.env.
    try:
        from .mongo_service import get_latest_access_token

        stored = get_latest_access_token(platform="instagram")
        if stored:
            return stored
    except Exception:
        logger.exception("Could not read latest Instagram access token from MongoDB")
    return getattr(settings, "INSTAGRAM_ACCESS_TOKEN", None)


def _latest_token_expiry():
    # Absolute expiry of the stored long-lived token, or None if unknown.
    try:
        from .mongo_service import get_latest_token_expiry

        return get_latest_token_expiry(platform="instagram")
    except Exception:
        logger.exception("Could not read Instagram token expiry from MongoDB")
        return None


def _persist(token_data):
    try:
        from .mongo_service import save_token

        save_token(token_data, platform="instagram")
    except Exception:
        logger.exception("Obtained Instagram token but failed to persist it to MongoDB")


def _meta_get(path, params):
    try:
        response = requests.get(f"{_graph_base()}/{path.lstrip('/')}", params=params, timeout=REQUEST_TIMEOUT)
    except requests.exceptions.RequestException:
        logger.exception("Network error calling Meta Graph API %s", path)
        return None
    try:
        data = response.json()
    except ValueError:
        logger.error("Meta Graph API returned non-JSON (%s) for %s", response.status_code, path)
        return None
    if response.status_code != 200:
        logger.error("Meta Graph API error (%s) for %s: %s", response.status_code, path, data.get("error"))
        return None
    return data


def refresh_access_token():
    """Extend the current long-lived token via ``fb_exchange_token``.

    Persists the new token to MongoDB and returns it, or ``None`` on failure.
    """
    token = _current_access_token()
    if not token:
        logger.error("No usable Instagram access token available; re-authorize via %s", REAUTH_ROUTE)
        return None
    if not (settings.META_APP_ID and settings.META_APP_SECRET):
        logger.error("META_APP_ID/META_APP_SECRET not configured; cannot extend Instagram token")
        return None

    data = _meta_get("oauth/access_token", {
        "grant_type": "fb_exchange_token",
        "client_id": settings.META_APP_ID,
        "client_secret": settings.META_APP_SECRET,
        "fb_exchange_token": token,
    })
    if not data or "access_token" not in data:
        return None

    _persist(data)
    logger.info("Extended Instagram long-lived token %s", _mask(data.get("access_token")))
    return data.get("access_token")


def is_refresh_due(expiry=None):
    """True when the stored token is close enough to expiring (or its expiry is
    unknown) that it should be proactively refreshed.

    Shared by ``ensure_fresh_token`` and the ``refresh_instagram_token``
    management command so "is a refresh needed" is decided in exactly one place
    — a command that wants to report *whether it actually refreshed* must not
    re-derive this from comparing before/after expiry timestamps, since Meta's
    ``expires_in`` for two exchanges made moments apart can tie or even come out
    a hair earlier than the previous one.
    """
    if expiry is None:
        expiry = _latest_token_expiry()
    if expiry is None:
        return True
    return (expiry - _utcnow()) <= _refresh_lead()


def ensure_fresh_token(current_token=None):
    """Proactively extend the long-lived token *before* it expires.

    Meta lets a still-valid long-lived token be re-exchanged for a fresh ~60-day
    one, so we refresh once the stored token is within
    ``INSTAGRAM_TOKEN_REFRESH_LEAD_DAYS`` of expiring (default 7) rather than
    waiting for an API call to fail. Returns the freshest valid access token
    (newly refreshed when due), or the existing token unchanged when no refresh
    is needed or a refresh fails. Never raises — safe to call before any request.
    """
    token = current_token or _current_access_token()

    # Nothing stored and no bootstrap token: leave it to the reactive path / a
    # fresh OAuth authorization. Also skip when the app credentials needed to
    # extend the token aren't configured (avoids noisy no-op refresh attempts).
    if not token or not (settings.META_APP_ID and settings.META_APP_SECRET):
        return token

    if not is_refresh_due():
        return token  # comfortably valid; no refresh needed

    expiry = _latest_token_expiry()
    if expiry is None:
        logger.info("Instagram token expiry unknown; refreshing proactively to establish it")
    else:
        logger.info("Instagram token expires at %s (within refresh window); refreshing proactively",
                    expiry.isoformat())

    new_token = refresh_access_token()
    if new_token:
        return new_token

    logger.error(
        "Proactive Instagram token refresh failed; the token may be expired or its "
        "permissions revoked. Re-authenticate via %s. Dashboards will serve the last "
        "cached data until then.", REAUTH_ROUTE,
    )
    return token


def exchange_long_lived_token(short_lived_token):
    """One-time setup: turn a short-lived user token into a ~60-day long-lived one."""
    data = _meta_get("oauth/access_token", {
        "grant_type": "fb_exchange_token",
        "client_id": settings.META_APP_ID,
        "client_secret": settings.META_APP_SECRET,
        "fb_exchange_token": short_lived_token,
    })
    if not data or "access_token" not in data:
        return None
    _persist(data)
    return data.get("access_token")


def discover_ig_business_account(access_token=None):
    """One-time setup: return the IG Business Account ID linked to the user's
    Facebook Page(s). Use the returned id as INSTAGRAM_BUSINESS_ACCOUNT_ID."""
    token = access_token or _current_access_token()
    data = _meta_get("me/accounts", {
        "fields": "name,instagram_business_account{id,username}",
        "access_token": token,
    })
    if not data:
        return []
    accounts = []
    for page in data.get("data", []) or []:
        ig = page.get("instagram_business_account")
        if ig:
            accounts.append({
                "page_name": page.get("name"),
                "ig_business_account_id": ig.get("id"),
                "ig_username": ig.get("username"),
            })
    return accounts
