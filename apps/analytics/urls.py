from django.urls import path
from .views import linkedin_analytics, linkedin_callback,linkedin_login

urlpatterns = [
    path("login/", linkedin_login),
    path("linkedin-analytics/", linkedin_analytics),
    path("callback/", linkedin_callback),
]