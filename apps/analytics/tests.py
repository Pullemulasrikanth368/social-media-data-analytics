from unittest.mock import patch

from bson import ObjectId
from django.test import SimpleTestCase

from apps.analytics.services import mongo_service
from apps.analytics.services.analytics_utils import build_dashboard_rows, normalize_audience_rows, normalize_post_rows


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
