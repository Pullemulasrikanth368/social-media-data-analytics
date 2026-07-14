import logging
import urllib.parse

import requests
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect

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


def linkedin_analytics(request):
    try:
        start_date = _validate_date(request.GET.get("start_date"), "start_date")
        end_date = _validate_date(request.GET.get("end_date"), "end_date")
        response_format = request.GET.get("format", "snapshots")
        granularity = request.GET.get("granularity", "DAY").upper()
        if granularity not in ("DAY", "MONTH"):
            granularity = "DAY"

        if response_format == "dashboard":
            return JsonResponse(
                get_dashboard_analytics(start_date, end_date, granularity), safe=False
            )

        return JsonResponse(get_all_analytics(start_date, end_date), safe=False)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception:
        logger.exception("Could not load LinkedIn analytics")
        return JsonResponse({"error": "Could not load LinkedIn analytics"}, status=500)


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

