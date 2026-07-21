import logging
import urllib.parse

import requests
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect

from .services import registry
from .services.analytics_utils import parse_date
from .services.mongo_service import get_all_analytics, get_dashboard_analytics, save_token

logger = logging.getLogger(__name__)


def _validate_date(value, field):
    if value in (None, ""):
        return None
    try:
        return parse_date(value).isoformat()
    except (ValueError, TypeError):
        raise ValueError(f"Invalid {field}; expected YYYY-MM-DD")


def _analytics_response(request, platform):
    """Shared analytics endpoint for every platform. ``?format=dashboard`` serves
    the connector-ready payload (fetch-through cached); otherwise raw snapshots."""
    try:
        start_date = _validate_date(request.GET.get("start_date"), "start_date")
        end_date = _validate_date(request.GET.get("end_date"), "end_date")
        response_format = request.GET.get("format", "snapshots")
        granularity = request.GET.get("granularity", "DAY").upper()
        if granularity not in ("DAY", "MONTH"):
            granularity = "DAY"

        if response_format == "dashboard":
            return JsonResponse(
                get_dashboard_analytics(platform, start_date, end_date, granularity), safe=False
            )

        return JsonResponse(get_all_analytics(start_date, end_date, platform), safe=False)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception:
        logger.exception("Could not load %s analytics", platform)
        return JsonResponse({"error": f"Could not load {platform} analytics"}, status=500)


def linkedin_analytics(request):
    return _analytics_response(request, "linkedin")


def instagram_analytics(request):
    return _analytics_response(request, "instagram")


def platform_analytics(request, platform):
    """Generic, extensible route: /api/<platform>/analytics/."""
    if not registry.is_supported(platform):
        return JsonResponse({"error": f"Unsupported platform: {platform}"}, status=404)
    return _analytics_response(request, registry.normalize_platform(platform))


def instagram_login(request):
    """Begin the Facebook-Login (for Business) flow to mint a user token that can
    read Instagram insights. Redirects to Facebook's OAuth dialog; Facebook then
    calls back to META_REDIRECT_URI with a ?code=."""
    version = settings.META_GRAPH_API_VERSION
    scopes = [
        "instagram_basic",
        "instagram_manage_insights",
        "pages_show_list",
        "pages_read_engagement",
    ]
    params = {
        "client_id": settings.META_APP_ID,
        "redirect_uri": settings.META_REDIRECT_URI,
        "state": "instagram_oauth",
        "response_type": "code",
        "scope": ",".join(scopes),
    }
    url = f"https://www.facebook.com/{version}/dialog/oauth?{urllib.parse.urlencode(params)}"
    return redirect(url)


def instagram_callback(request):
    """Exchange the OAuth code for a short-lived token, upgrade it to a ~60-day
    long-lived token, persist it (platform=instagram), and report the IG Business
    Account id(s) linked to the user's Page(s)."""
    error = request.GET.get("error")
    if error:
        return JsonResponse({
            "status": "error",
            "error_type": "instagram_callback_error",
            "details": {"error": error, "description": request.GET.get("error_description")},
        }, status=400)

    code = request.GET.get("code")
    if not code:
        return JsonResponse({"status": "error", "message": "No authorization code provided by Facebook."}, status=400)

    version = settings.META_GRAPH_API_VERSION
    try:
        token_resp = requests.get(
            f"https://graph.facebook.com/{version}/oauth/access_token",
            params={
                "client_id": settings.META_APP_ID,
                "client_secret": settings.META_APP_SECRET,
                "redirect_uri": settings.META_REDIRECT_URI,
                "code": code,
            },
            timeout=15,
        )
        short_lived = token_resp.json()
        if token_resp.status_code != 200 or "access_token" not in short_lived:
            logger.error("Instagram token exchange failed: %s", short_lived)
            return JsonResponse({
                "status": "error",
                "message": "Facebook rejected the token exchange.",
                "facebook_response": short_lived,
                "debug_info": {"sent_redirect_uri": settings.META_REDIRECT_URI},
            }, status=token_resp.status_code)

        # Upgrade to a long-lived token and persist it, then discover the account.
        from .services.instagram_auth import discover_ig_business_account, exchange_long_lived_token

        long_lived = exchange_long_lived_token(short_lived["access_token"]) or short_lived["access_token"]
        accounts = discover_ig_business_account(long_lived)

        return JsonResponse({
            "status": "success",
            "message": "Instagram access token generated and saved successfully.",
            "ig_business_accounts": accounts,
            "next_step": "Copy an ig_business_account_id into INSTAGRAM_BUSINESS_ACCOUNT_ID in .env",
        })
    except requests.exceptions.RequestException as exc:
        logger.exception("Connection error during Instagram token exchange")
        return JsonResponse({
            "status": "error",
            "message": "Could not connect to Facebook servers.",
            "details": str(exc),
        }, status=500)


def _linkedin_post_url(post_id):
    """A viewable LinkedIn URL for a share/ugcPost URN, or "" if not a URN."""
    return f"https://www.linkedin.com/feed/update/{post_id}" if str(post_id).startswith("urn:li:") else ""


def _post_urn_id(post_id):
    """Numeric id embedded in a LinkedIn URN (e.g. urn:li:share:748... -> 748...).
    It increases monotonically with time, so it orders same-day posts correctly.
    Returns 0 when no numeric id can be parsed."""
    tail = str(post_id).rsplit(":", 1)[-1]
    return int(tail) if tail.isdigit() else 0


def _reject_without_api_key(request):
    """Return a JsonResponse to send back when the request is not authorized, or
    ``None`` when a valid ``X-API-Key`` header (or ``?api_key=``) is present."""
    provided = request.headers.get("X-API-Key") or request.GET.get("api_key")
    if not settings.ANALYTICS_API_KEYS:
        logger.error("ANALYTICS_API_KEYS is not set; refusing key-protected request")
        return JsonResponse(
            {"status": "error", "message": "API access is not configured."}, status=503
        )
    if provided not in settings.ANALYTICS_API_KEYS:
        return JsonResponse(
            {"status": "error", "message": "Invalid or missing API key."}, status=401
        )
    return None


def linkedin_new_posts(request):
    """Read-only feed of recently-published LinkedIn posts for external websites.

    Protected by ``X-API-Key``. Unlike ``/api/linkedin/posts/`` this has NO side
    effects: it never triggers a sync and never touches the internal new-post
    baseline, so external consumers can poll it freely without disturbing the
    dashboard poller. "New" = published on/after ``?since=YYYY-MM-DD``; if
    ``since`` is omitted it defaults to the last ``?days=N`` days (default 7)."""
    unauthorized = _reject_without_api_key(request)
    if unauthorized:
        return unauthorized

    from datetime import date, timedelta

    from .services.analytics_utils import normalize_post_rows
    from .services.linkedin_service import LinkedInAPIError, LinkedInService

    try:
        since = request.GET.get("since")
        if since:
            cutoff = _validate_date(since, "since")[:10]
        else:
            try:
                days = max(int(request.GET.get("days", "7")), 0)
            except ValueError:
                days = 7
            cutoff = (date.today() - timedelta(days=days)).isoformat()
    except ValueError as exc:
        return JsonResponse({"status": "error", "message": str(exc)}, status=400)

    try:
        metadata = LinkedInService().get_organization_posts()
    except LinkedInAPIError as exc:
        logger.error("Could not fetch LinkedIn posts for external feed: %s", exc)
        return JsonResponse(
            {"status": "error", "message": "Could not fetch LinkedIn posts."}, status=502
        )

    posts = normalize_post_rows([], metadata.get("elements", []))
    new_posts = [p for p in posts if p.get("postDate") and p["postDate"] >= cutoff]
    # Sort newest-first. Primary key: publish date. Tiebreaker for same-day posts:
    # the numeric id inside the share URN, which increases monotonically with time.
    new_posts.sort(key=lambda p: (p.get("postDate", ""), _post_urn_id(p.get("post_id"))), reverse=True)
    for post in new_posts:
        post["post_url"] = _linkedin_post_url(post.get("post_id"))

    return JsonResponse({
        "status": "success",
        "since": cutoff,
        "count": len(new_posts),
        # Convenience: the single most recent post (or null if none in the window).
        "latest_post": new_posts[0] if new_posts else None,
        "posts": new_posts,
    })


def linkedin_posts(request):
    """Return the organization's latest posts and flag any published since the
    last check.

    LinkedIn has no push/webhook for new posts, so this is a *poll*: call it on a
    schedule (dashboard auto-refresh or cron). When a new post is detected it
    triggers a fresh analytics sync so the dashboard reflects the post, then
    reports the new post(s). ``?refresh=false`` skips the sync (detect only)."""
    from .services.analytics_utils import normalize_post_rows
    from .services.linkedin_service import LinkedInAPIError, LinkedInService
    from .services.mongo_service import get_seen_post_ids, set_seen_post_ids

    try:
        linkedin = LinkedInService()
        metadata = linkedin.get_organization_posts()
    except LinkedInAPIError as exc:
        logger.error("Could not fetch LinkedIn posts: %s", exc)
        return JsonResponse(
            {"status": "error", "message": "Could not fetch LinkedIn posts.", "details": str(exc)},
            status=502,
        )

    posts = normalize_post_rows([], metadata.get("elements", []))
    for post in posts:
        post["post_url"] = _linkedin_post_url(post.get("post_id"))
    current_ids = [p["post_id"] for p in posts if p.get("post_id")]

    seen = get_seen_post_ids("linkedin")
    # On the very first run everything looks "new"; only treat ids as new once we
    # have a prior baseline, so we don't trigger a needless sync on cold start.
    new_posts = [p for p in posts if p.get("post_id") not in seen] if seen else []

    analytics_refreshed = False
    if new_posts and request.GET.get("refresh", "true").lower() != "false":
        try:
            from .services.analytics_sync import collect_linkedin_analytics
            from .services.mongo_service import save_analytics

            save_analytics(collect_linkedin_analytics(linkedin=linkedin))
            analytics_refreshed = True
        except Exception:
            logger.exception("Failed to refresh analytics after detecting a new LinkedIn post")

    set_seen_post_ids(current_ids, "linkedin")

    return JsonResponse({
        "status": "success",
        "post_count": len(posts),
        "new_post_count": len(new_posts),
        "has_new_posts": bool(new_posts),
        "analytics_refreshed": analytics_refreshed,
        "new_posts": new_posts,
        "posts": posts,
    })


def linkedin_login(request):
    auth_base_url = "https://www.linkedin.com/oauth/v2/authorization"
    scopes = [
        "openid",
        "profile",
        "email",
        "w_member_social",
        "r_organization_admin",
        "r_organization_social",
        "w_organization_social",
        "rw_organization_admin",
    ]
    params = {
        "response_type": "code",
        "client_id": settings.LINKEDIN_CLIENT_ID,
        "redirect_uri": settings.LINKEDIN_REDIRECT_URI,
        "state": "random123",
        "scope": " ".join(scopes),
    }
    url_params = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    return redirect(f"{auth_base_url}?{url_params}")


def linkedin_callback(request):
    code = request.GET.get("code")
    error = request.GET.get("error")
    error_description = request.GET.get("error_description")

    if error:
        return JsonResponse({
            "status": "error",
            "error_type": "linkedin_callback_error",
            "details": {
                "error": error,
                "description": error_description,
            },
        }, status=400)

    if not code:
        return JsonResponse({
            "status": "error",
            "message": "No authorization code provided by LinkedIn.",
        }, status=400)

    redirect_uri = settings.LINKEDIN_REDIRECT_URI
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": settings.LINKEDIN_CLIENT_ID,
        "client_secret": settings.LINKEDIN_CLIENT_SECRET,
    }

    try:
        response = requests.post(
            "https://www.linkedin.com/oauth/v2/accessToken",
            data=payload,
            timeout=10,
        )
        token_data = response.json()

        if response.status_code != 200 or "access_token" not in token_data:
            logger.error("LinkedIn token exchange failed: %s", token_data)
            return JsonResponse({
                "status": "error",
                "message": "LinkedIn rejected the token exchange.",
                "linkedin_response": token_data,
                "debug_info": {
                    "sent_redirect_uri": redirect_uri,
                    "sent_client_id": settings.LINKEDIN_CLIENT_ID,
                },
            }, status=response.status_code)

        save_token(token_data)
        refresh_token = token_data.get("refresh_token")
        return JsonResponse({
            "status": "success",
            "message": "Access token generated and saved successfully",
            "access_token": token_data.get("access_token"),
            "expires_in": token_data.get("expires_in"),
            "scope": token_data.get("scope"),
            "refresh_token": refresh_token,
            "refresh_token_expires_in": token_data.get("refresh_token_expires_in"),
            "refresh_token_issued": bool(refresh_token),
            "note": (
                "If refresh_token_issued is false, this LinkedIn app is not approved "
                "for refresh tokens — you must re-run /api/login/ before the access "
                "token expires. Do NOT re-authorize again now; each new login revokes "
                "this token."
            ),
        })

    except requests.exceptions.RequestException as exc:
        logger.exception("Connection error during LinkedIn token exchange")
        return JsonResponse({
            "status": "error",
            "message": "Could not connect to LinkedIn servers.",
            "details": str(exc),
        }, status=500)

