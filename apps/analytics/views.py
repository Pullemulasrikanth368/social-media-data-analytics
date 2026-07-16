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
        "redirect_uri": "https://unnamed-anew-frays.ngrok-free.dev/api/callback",
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

    redirect_uri = "http://localhost:8001/api/callback/"
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
        return JsonResponse({
            "status": "success",
            "message": "Access token generated and saved successfully",
            "access_token": token_data.get("access_token"),
            "expires_in": token_data.get("expires_in"),
            "scope": token_data.get("scope"),
        })

    except requests.exceptions.RequestException as exc:
        logger.exception("Connection error during LinkedIn token exchange")
        return JsonResponse({
            "status": "error",
            "message": "Could not connect to LinkedIn servers.",
            "details": str(exc),
        }, status=500)

