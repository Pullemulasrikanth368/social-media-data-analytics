from django.urls import path

from .views import (
    instagram_analytics,
    instagram_callback,
    instagram_login,
    linkedin_analytics,
    linkedin_callback,
    linkedin_login,
    linkedin_new_posts,
    linkedin_posts,
    platform_analytics,
)

urlpatterns = [
    # LinkedIn OAuth — unchanged.
    path("login/", linkedin_login),
    path("callback/", linkedin_callback),
    # Instagram (Facebook-Login) OAuth. Register the callback URL below as a
    # Valid OAuth Redirect URI in the Meta app.
    path("instagram/login/", instagram_login),
    path("instagram/callback/", instagram_callback),
    # Latest posts + new-post detection (poll this on a schedule).
    path("linkedin/posts/", linkedin_posts),
    # External, API-key-protected, read-only new-posts feed for other websites.
    path("linkedin/new-posts/", linkedin_new_posts),
    # Data endpoints consumed by the Looker Studio connectors.
    path("linkedin-analytics/", linkedin_analytics),
    path("instagram-analytics/", instagram_analytics),
    # Generic, future-proof route: /api/<platform>/analytics/.
    path("<str:platform>/analytics/", platform_analytics),
]
