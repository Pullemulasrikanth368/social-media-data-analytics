import requests
from django.conf import settings

def get_access_token():
    url = "https://www.linkedin.com/oauth/v2/accessToken"

    payload = {
        "grant_type": "refresh_token",
        "refresh_token": settings.LINKEDIN_REFRESH_TOKEN,
        "client_id": settings.LINKEDIN_CLIENT_ID,
        "client_secret": settings.LINKEDIN_CLIENT_SECRET,
    }

    res = requests.post(url, data=payload)
    data = res.json()

    return data.get("access_token")