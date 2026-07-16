import json
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from bson import ObjectId
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from apps.analytics.management.commands import refresh_instagram_token as refresh_instagram_token_cmd
from apps.analytics.services import instagram_auth, mongo_service, registry
from apps.analytics.services.analytics_utils import build_dashboard_rows, normalize_audience_rows, normalize_post_rows
from apps.analytics.services.instagram_sync import collect_instagram_analytics
from apps.analytics.services.instagram_utils import INSTAGRAM_METRIC_FIELDS, instagram_engagement_rate

_FIXTURES = Path(__file__).parent / "sample_responses" / "instagram"


def _fixture(name):
    with open(_FIXTURES / name) as handle:
        return json.load(handle)


class MutatingCollection:
    def __init__(self):
        self.inserted = None

    def insert_one(self, document):
        document["_id"] = ObjectId()
        self.inserted = document


class FakeDb:
    def __init__(self):
        self.analytics = MutatingCollection()
        self.tokens = MutatingCollection()


class MongoServiceTests(SimpleTestCase):
    def test_save_token_does_not_mutate_original_data(self):
        fake_db = FakeDb()
        token_data = {"access_token": "token", "expires_in": 3600}

        with patch.object(mongo_service, "db", fake_db):
            mongo_service.save_token(token_data)

        self.assertNotIn("_id", token_data)
        self.assertNotIn("created_at", token_data)
        self.assertIn("_id", fake_db.tokens.inserted)
        self.assertIn("created_at", fake_db.tokens.inserted)

    def test_save_analytics_does_not_mutate_original_data(self):
        fake_db = FakeDb()
        analytics_data = {"elements": [], "insight": "Posts are performing well"}

        with patch.object(mongo_service, "db", fake_db):
            mongo_service.save_analytics(analytics_data)

        self.assertNotIn("_id", analytics_data)
        self.assertNotIn("fetched_at", analytics_data)
        self.assertIn("_id", fake_db.analytics.inserted)
        self.assertIn("fetched_at", fake_db.analytics.inserted)


class AnalyticsNormalizationTests(SimpleTestCase):
    def test_audience_rows_are_chart_friendly(self):
        rows = normalize_audience_rows([{
            "followerCountsByGeoCountry": [{
                "geo": "urn:li:geo:102713980",
                "followerCounts": {
                    "organicFollowerCount": 7,
                    "paidFollowerCount": 3,
                },
            }]
        }])

        self.assertEqual(rows[0]["segment_type"], "Followers by Country")
        self.assertEqual(rows[0]["segment"], "urn:li:geo:102713980")
        self.assertEqual(rows[0]["segment_label"], "India")
        self.assertEqual(rows[0]["country_name"], "India")
        self.assertEqual(rows[0]["industry_name"], "")
        self.assertEqual(rows[0]["followers"], 10)
        self.assertEqual(rows[0]["organic_followers"], 7)
        self.assertEqual(rows[0]["paid_followers"], 3)

    def test_industry_audience_rows_use_readable_label(self):
        rows = normalize_audience_rows([{
            "followerCountsByIndustry": [{
                "industry": "urn:li:industry:96",
                "followerCounts": {
                    "organicFollowerCount": 4,
                    "paidFollowerCount": 1,
                },
            }]
        }])

        self.assertEqual(rows[0]["segment_type"], "Followers by Industry")
        self.assertEqual(rows[0]["segment"], "urn:li:industry:96")
        self.assertEqual(rows[0]["segment_label"], "Information Technology & Services")
        self.assertEqual(rows[0]["country_name"], "")
        self.assertEqual(rows[0]["industry_name"], "Information Technology & Services")
        self.assertEqual(rows[0]["followers"], 5)

    def test_post_rows_keep_post_id_and_content_type_as_strings(self):
        rows = normalize_post_rows(
            [{
                "share": "urn:li:share:123",
                "totalShareStatistics": {
                    "impressionCount": 50,
                    "clickCount": 5,
                    "likeCount": 2,
                },
            }],
            [{
                "id": "urn:li:share:123",
                "contentType": 2,
                "commentary": "Launch post",
                "publishedAt": 1779235200000,
            }],
        )

        self.assertEqual(rows[0]["postId"], "urn:li:share:123")
        self.assertIsInstance(rows[0]["contentType"], str)
        self.assertEqual(rows[0]["postText"], "Launch post")
        self.assertEqual(rows[0]["total_engagements"], 7)

    def test_dashboard_rows_include_audience_and_post_row_types(self):
        rows = build_dashboard_rows(
            [{"date": "2026-05-20", "impressions": 10}],
            [{"segment_type": "Followers by Industry", "segment": "urn:li:industry:96", "followers": 4}],
            [{"postId": "urn:li:share:123", "contentType": 3}],
            "2026-05-21",
        )

        self.assertEqual([row["row_type"] for row in rows], ["daily", "audience", "post"])
        self.assertEqual(rows[1]["metric_mode"], "audience")
        self.assertEqual(rows[1]["date"], "2026-05-21")
        self.assertEqual(rows[1]["segment_label"], "Information Technology & Services")
        self.assertEqual(rows[2]["postId"], "urn:li:share:123")
        self.assertEqual(rows[2]["contentType"], "UNKNOWN")


class FakeInstagramService:
    """Serves committed Graph API fixtures instead of hitting Meta — the bulk of
    Instagram tests run offline so we never touch live rate limits."""

    org_id = "17841400000000000"

    def get_account(self):
        return _fixture("account.json")

    def get_account_timeseries(self, start_date, end_date):
        return _fixture("account_timeseries.json")

    def get_account_totals(self, start_date, end_date):
        return _fixture("account_totals.json")

    def get_follower_demographics(self, breakdown="country"):
        return _fixture(f"follower_demographics_{breakdown}.json")

    def get_media(self, start_date, end_date):
        return _fixture("media.json")

    def get_media_insights(self, media_id, product_type="FEED"):
        return _fixture(f"media_insights_{str(product_type).lower()}.json")


class InstagramCollectionTests(SimpleTestCase):
    def setUp(self):
        self.data = collect_instagram_analytics(
            start_date="2026-07-01", end_date="2026-07-02", instagram=FakeInstagramService())

    def test_platform_and_account(self):
        self.assertEqual(self.data["platform"], "instagram")
        self.assertEqual(self.data["organization_id"], "17841400000000000")
        self.assertEqual(self.data["followers"], 15000)

    def test_daily_rows_pivot_reach_time_series(self):
        # Only reach is a per-day series on the live API.
        daily = self.data["daily_metrics"]
        self.assertEqual([r["date"] for r in daily], ["2026-07-01", "2026-07-02"])
        self.assertEqual(daily[0]["reach"], 1000)
        self.assertEqual(daily[1]["reach"], 1200)

    def test_overview_uses_window_totals(self):
        # Overview comes from total_value metrics (deduped reach), NOT summed daily.
        overview = self.data["overview"]
        self.assertEqual(overview["reach"], 2000)
        self.assertEqual(overview["views"], 6500)
        self.assertEqual(overview["total_interactions"], 330)
        self.assertEqual(overview["accounts_engaged"], 260)
        self.assertEqual(overview["followers_gained"], 45)
        self.assertEqual(self.data["followers"], 15000)

    def test_media_rows_tagged_by_product_type(self):
        by_type = {r["row_type"]: r for r in self.data["post_analytics"]}
        self.assertEqual(set(by_type), {"post", "reel", "story"})
        self.assertEqual(by_type["reel"]["views"], 12000)
        self.assertEqual(by_type["reel"]["avg_watch_time"], 4200)
        self.assertEqual(by_type["story"]["replies"], 8)
        self.assertEqual(by_type["story"]["navigation"], 640)
        # Removed metrics must never appear.
        self.assertNotIn("impressions", by_type["post"])
        self.assertNotIn("video_views", by_type["reel"])

    def test_demographics_become_country_and_city_rows(self):
        segments = {r["segment_type"] for r in self.data["audience_analytics"]}
        self.assertEqual(segments, {"Followers by Country", "Followers by City"})
        us = next(r for r in self.data["audience_analytics"] if r["segment"] == "US")
        self.assertEqual(us["country_name"], "United States")
        self.assertEqual(us["followers"], 9000)

    def test_dashboard_rows_include_all_row_types(self):
        row_types = {r["row_type"] for r in self.data["dashboard_rows"]}
        self.assertTrue({"daily", "post", "reel", "story", "country", "city"}.issubset(row_types))

    def test_engagement_rate_is_computed_from_reach(self):
        # No API engagement metric exists; verify our reach-based formula.
        self.assertEqual(instagram_engagement_rate({"reach": 1000, "total_interactions": 150}), 0.15)
        self.assertEqual(instagram_engagement_rate({"reach": 0, "total_interactions": 150}), 0.0)


class PlatformRegistryTests(SimpleTestCase):
    def test_supported_platforms(self):
        self.assertTrue(registry.is_supported("instagram"))
        self.assertTrue(registry.is_supported("LinkedIn"))
        self.assertFalse(registry.is_supported("tiktok"))

    def test_normalize_and_default(self):
        self.assertEqual(registry.normalize_platform("INSTAGRAM"), "instagram")
        self.assertEqual(registry.normalize_platform(None), "linkedin")

    def test_collector_and_metric_config_dispatch(self):
        self.assertIs(registry.get_collector("instagram"), collect_instagram_analytics)
        fields, engagement_fn = registry.get_metric_config("instagram")
        self.assertEqual(fields, INSTAGRAM_METRIC_FIELDS)
        self.assertIs(engagement_fn, instagram_engagement_rate)

    def test_unknown_platform_raises(self):
        with self.assertRaises(ValueError):
            registry.get_platform("myspace")


class CacheKeyIsolationTests(SimpleTestCase):
    def test_platform_is_part_of_cache_key(self):
        li = mongo_service._cache_key("linkedin", "org1", "2026-07-01", "2026-07-02", "DAY")
        ig = mongo_service._cache_key("instagram", "org1", "2026-07-01", "2026-07-02", "DAY")
        # Same window, different platform → must not collide.
        self.assertNotEqual(li, ig)
        self.assertEqual(ig["platform"], "instagram")


class InstagramSaveAnalyticsTests(SimpleTestCase):
    def test_instagram_post_rows_keep_product_type_and_platform(self):
        fake_db = FakeDb()
        data = collect_instagram_analytics(
            start_date="2026-07-01", end_date="2026-07-02", instagram=FakeInstagramService())

        with patch.object(mongo_service, "db", fake_db):
            saved = mongo_service.save_analytics(data, platform="instagram")

        self.assertEqual(saved["platform"], "instagram")
        content_types = {r["row_type"]: r.get("contentType") for r in saved["post_analytics"]}
        # LinkedIn canonicalization must NOT run — REELS/STORY must survive.
        self.assertEqual(content_types["reel"], "REELS")
        self.assertEqual(content_types["story"], "STORY")
        # Collector-provided dashboard rows (with reel/story) are preserved.
        self.assertTrue(any(r["row_type"] == "reel" for r in saved["dashboard_rows"]))


class _FindOneCollection:
    """Minimal token collection stub exposing find_one for expiry reads."""

    def __init__(self, doc):
        self._doc = doc

    def find_one(self, *args, **kwargs):
        return self._doc


class TokenExpiryStorageTests(SimpleTestCase):
    def test_save_token_computes_expires_at_from_expires_in(self):
        fake_db = FakeDb()
        with patch.object(mongo_service, "db", fake_db):
            saved = mongo_service.save_token(
                {"access_token": "t", "expires_in": 5184000}, platform="instagram")

        inserted = fake_db.tokens.inserted
        self.assertIn("expires_at", inserted)
        # expires_at is created_at + expires_in seconds (~60 days).
        self.assertEqual(inserted["expires_at"] - inserted["created_at"], timedelta(seconds=5184000))
        self.assertEqual(saved["expires_at"], inserted["expires_at"])

    def test_save_token_without_expires_in_has_no_expiry(self):
        fake_db = FakeDb()
        with patch.object(mongo_service, "db", fake_db):
            mongo_service.save_token({"access_token": "t"}, platform="instagram")

        self.assertNotIn("expires_at", fake_db.tokens.inserted)

    def test_get_latest_token_expiry_is_tz_aware(self):
        naive = datetime(2026, 9, 1, 12, 0, 0)  # pymongo returns naive UTC
        collection = _FindOneCollection({"expires_at": naive})
        with patch.object(mongo_service, "_collection", lambda name: collection):
            expiry = mongo_service.get_latest_token_expiry(platform="instagram")

        self.assertEqual(expiry, naive.replace(tzinfo=timezone.utc))

    def test_get_latest_token_expiry_none_when_unknown(self):
        collection = _FindOneCollection({"access_token": "t"})  # no expires_at
        with patch.object(mongo_service, "_collection", lambda name: collection):
            self.assertIsNone(mongo_service.get_latest_token_expiry(platform="instagram"))


@override_settings(META_APP_ID="app", META_APP_SECRET="secret", INSTAGRAM_TOKEN_REFRESH_LEAD_DAYS=7)
class InstagramProactiveRefreshTests(SimpleTestCase):
    """ensure_fresh_token extends the long-lived token before it lapses."""

    def test_no_refresh_when_token_comfortably_valid(self):
        far = datetime.now(timezone.utc) + timedelta(days=30)
        with patch.object(instagram_auth, "_latest_token_expiry", return_value=far), \
                patch.object(instagram_auth, "refresh_access_token") as refresh:
            result = instagram_auth.ensure_fresh_token(current_token="cur")

        refresh.assert_not_called()
        self.assertEqual(result, "cur")

    def test_refresh_when_within_lead_window(self):
        soon = datetime.now(timezone.utc) + timedelta(days=3)
        with patch.object(instagram_auth, "_latest_token_expiry", return_value=soon), \
                patch.object(instagram_auth, "refresh_access_token", return_value="new") as refresh:
            result = instagram_auth.ensure_fresh_token(current_token="cur")

        refresh.assert_called_once()
        self.assertEqual(result, "new")

    def test_refresh_when_expiry_unknown(self):
        with patch.object(instagram_auth, "_latest_token_expiry", return_value=None), \
                patch.object(instagram_auth, "refresh_access_token", return_value="new") as refresh:
            result = instagram_auth.ensure_fresh_token(current_token="cur")

        refresh.assert_called_once()
        self.assertEqual(result, "new")

    def test_keeps_current_token_when_refresh_fails(self):
        soon = datetime.now(timezone.utc) + timedelta(days=1)
        with patch.object(instagram_auth, "_latest_token_expiry", return_value=soon), \
                patch.object(instagram_auth, "refresh_access_token", return_value=None) as refresh:
            result = instagram_auth.ensure_fresh_token(current_token="cur")

        refresh.assert_called_once()
        self.assertEqual(result, "cur")  # degrade gracefully, don't drop the token

    @override_settings(META_APP_ID=None, META_APP_SECRET=None)
    def test_skips_refresh_without_app_credentials(self):
        with patch.object(instagram_auth, "refresh_access_token") as refresh:
            result = instagram_auth.ensure_fresh_token(current_token="cur")

        refresh.assert_not_called()
        self.assertEqual(result, "cur")

    def test_masked_token_never_exposes_full_value(self):
        masked = instagram_auth._mask("supersecrettoken1234")
        self.assertNotIn("supersecrettoken", masked)
        self.assertTrue(masked.endswith("1234 (len 20)"))


class IsRefreshDueTests(SimpleTestCase):
    """is_refresh_due is the single source of truth both ensure_fresh_token and
    the refresh_instagram_token command rely on for the refresh decision."""

    @override_settings(INSTAGRAM_TOKEN_REFRESH_LEAD_DAYS=7)
    def test_not_due_when_comfortably_valid(self):
        far = datetime.now(timezone.utc) + timedelta(days=30)
        self.assertFalse(instagram_auth.is_refresh_due(far))

    @override_settings(INSTAGRAM_TOKEN_REFRESH_LEAD_DAYS=7)
    def test_due_when_within_lead_window(self):
        soon = datetime.now(timezone.utc) + timedelta(days=3)
        self.assertTrue(instagram_auth.is_refresh_due(soon))

    def test_due_when_expiry_unknown(self):
        with patch.object(instagram_auth, "_latest_token_expiry", return_value=None):
            self.assertTrue(instagram_auth.is_refresh_due(None))

    def test_reads_stored_expiry_when_not_passed(self):
        far = datetime.now(timezone.utc) + timedelta(days=30)
        with patch.object(instagram_auth, "_latest_token_expiry", return_value=far):
            self.assertFalse(instagram_auth.is_refresh_due())


@override_settings(META_APP_ID="app", META_APP_SECRET="secret")
class RefreshInstagramTokenCommandTests(SimpleTestCase):
    def _run(self, force=False):
        out = StringIO()
        err = StringIO()
        try:
            call_command("refresh_instagram_token", force=force, stdout=out, stderr=err)
            exited = None
        except SystemExit as exc:
            exited = exc.code
        return out.getvalue(), err.getvalue(), exited

    def test_skips_when_not_due_and_not_forced(self):
        far = datetime.now(timezone.utc) + timedelta(days=30)
        with patch.object(refresh_instagram_token_cmd, "get_latest_token_expiry", return_value=far), \
                patch.object(instagram_auth, "refresh_access_token") as refresh:
            out, err, exited = self._run(force=False)

        refresh.assert_not_called()
        self.assertIn("no refresh needed", out)
        self.assertIsNone(exited)

    def test_force_reports_refreshed_even_when_new_expiry_ties_or_regresses(self):
        # Regression: Meta's expires_in for two exchanges made moments apart can
        # tie, or even land a fraction of a second *earlier*, than the previous
        # expiry. The command must still report success because a token WAS
        # returned — it must not infer "refreshed" from comparing timestamps.
        before = datetime(2026, 9, 13, 7, 28, 4, 45000, tzinfo=timezone.utc)
        after = datetime(2026, 9, 13, 7, 28, 3, 266000, tzinfo=timezone.utc)  # earlier than before
        expiries = iter([before, after])

        with patch.object(refresh_instagram_token_cmd, "get_latest_token_expiry",
                           side_effect=lambda platform=None: next(expiries)), \
                patch.object(instagram_auth, "refresh_access_token", return_value="new-token") as refresh:
            out, err, exited = self._run(force=True)

        refresh.assert_called_once()
        self.assertIn("refreshed", out)
        self.assertNotIn("no refresh needed", out)
        self.assertIsNone(exited)

    def test_force_reports_failure_on_refresh_error(self):
        far = datetime.now(timezone.utc) + timedelta(days=30)
        with patch.object(refresh_instagram_token_cmd, "get_latest_token_expiry", return_value=far), \
                patch.object(instagram_auth, "refresh_access_token", return_value=None):
            out, err, exited = self._run(force=True)

        self.assertIn(instagram_auth.REAUTH_ROUTE, err)
        self.assertEqual(exited, 1)
