"""Exchange a short-lived Instagram (Meta) user token for a ~60-day long-lived
token, persist it to MongoDB, and write it into .env.

Graph API Explorer tokens live only ~1-2 hours. Generate a fresh one, then run:

    ./venv/bin/python manage.py extend_instagram_token --token <FRESH_TOKEN>

Omit --token to try extending whatever is already in INSTAGRAM_ACCESS_TOKEN
(only works if it hasn't expired yet).
"""

import datetime
import os

import requests
from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Exchange a short-lived Instagram user token for a long-lived (~60-day) one"

    def add_arguments(self, parser):
        parser.add_argument("--token", help="Fresh short-lived user token (defaults to INSTAGRAM_ACCESS_TOKEN)")
        parser.add_argument("--no-env-write", action="store_true", help="Do not modify .env (Mongo only)")

    def handle(self, *args, **options):
        short = options.get("token") or settings.INSTAGRAM_ACCESS_TOKEN
        if not short:
            self.stdout.write(self.style.ERROR("No token provided and INSTAGRAM_ACCESS_TOKEN is empty."))
            return
        if not (settings.META_APP_ID and settings.META_APP_SECRET):
            self.stdout.write(self.style.ERROR("META_APP_ID / META_APP_SECRET are not set."))
            return

        ver = settings.META_GRAPH_API_VERSION
        resp = requests.get(f"https://graph.facebook.com/{ver}/oauth/access_token", params={
            "grant_type": "fb_exchange_token",
            "client_id": settings.META_APP_ID,
            "client_secret": settings.META_APP_SECRET,
            "fb_exchange_token": short,
        }, timeout=20)
        data = resp.json()
        if "access_token" not in data:
            self.stdout.write(self.style.ERROR(f"Exchange failed: {data.get('error', data)}"))
            self.stdout.write(self.style.WARNING(
                "Generate a NEW token in Graph API Explorer and re-run immediately (they expire in ~1-2h)."))
            return
        long_token = data["access_token"]

        # Persist to MongoDB (the source of truth the service reads first).
        try:
            from apps.analytics.services.mongo_service import save_token

            save_token({"access_token": long_token, "token_type": data.get("token_type"),
                        "expires_in": data.get("expires_in")}, platform="instagram")
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f"Could not persist token to MongoDB: {exc}"))

        # Verify expiry (does not print the token).
        app_tok = f"{settings.META_APP_ID}|{settings.META_APP_SECRET}"
        dbg = requests.get(f"https://graph.facebook.com/{ver}/debug_token",
                           params={"input_token": long_token, "access_token": app_tok}, timeout=20).json().get("data", {})
        exp = dbg.get("expires_at")
        exp_str = "never" if not exp else datetime.datetime.fromtimestamp(exp, datetime.UTC).isoformat()

        if not options["no_env_write"]:
            env_path = os.path.join(settings.BASE_DIR, ".env")
            try:
                with open(env_path) as f:
                    lines = f.readlines()
                found = False
                for i, line in enumerate(lines):
                    if line.startswith("INSTAGRAM_ACCESS_TOKEN="):
                        lines[i] = f"INSTAGRAM_ACCESS_TOKEN={long_token}\n"
                        found = True
                if not found:
                    lines.append(f"INSTAGRAM_ACCESS_TOKEN={long_token}\n")
                with open(env_path, "w") as f:
                    f.writelines(lines)
                self.stdout.write(self.style.SUCCESS("Wrote long-lived token to .env"))
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f"Could not update .env: {exc}"))

        self.stdout.write(self.style.SUCCESS(f"Long-lived token obtained. expires_at={exp_str}, valid={dbg.get('is_valid')}"))
