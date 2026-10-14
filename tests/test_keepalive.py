import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import app
import emby


class KeepaliveTests(unittest.TestCase):
    def test_boundaries_unknown_disabled_and_inaccessible(self):
        config = {"keepalive_days": 30}
        snapshot = {"last_played_at": 1000}
        for days, state in [
            (0, "healthy"),
            (14, "healthy"),
            (15, "warning"),
            (29, "warning"),
            (30, "error"),
            (40, "error"),
        ]:
            result = app.keepalive_status(config, snapshot, 1000 + days * 86400)
            self.assertEqual(result["state"], state)
            self.assertEqual(result["days"], int(days))
        self.assertIsNone(app.keepalive_status({}, snapshot, 1000))
        self.assertEqual(app.keepalive_status(config, {}, 1000)["state"], "unknown")
        result = app.keepalive_status(config, snapshot, 1000, True)
        self.assertEqual(result["state"], "error")
        self.assertIsNone(result["days"])

    def test_days_count_complete_24_hour_periods(self):
        played = emby.datetime.fromisoformat("2026-10-13T23:59:00+08:00").timestamp()
        for hours, days in [(0, 0), (0.1, 0), (23.99, 0), (24, 1), (47.99, 1), (48, 2)]:
            result = app.keepalive_status(
                {"keepalive_days": 1}, {"last_played_at": played}, played + hours * 3600
            )
            self.assertEqual(result["days"], days)
            self.assertEqual(result["state"], "healthy" if days == 0 else "error")

    def test_statistics_failure_does_not_mark_accessible_server_unhealthy(self):
        class Client:
            token, uid = "token", "user"

            def __init__(self, *args):
                pass

            def probe(self):
                return {"Id": "server", "ServerName": "test"}

            def stats(self):
                raise TimeoutError()

        with patch.object(emby, "Client", Client):
            result = emby.collect(
                {"primary_url": "https://test"}, {"stats": {"movies": 12}}, "test"
            )
        self.assertEqual(result["state"], "healthy")
        self.assertEqual(result["stats"]["movies"], 12)
        self.assertIn("超时", result["stats_error"])

    def test_statistics_http_error_does_not_poison_line_checks(self):
        import urllib.error

        class Client:
            token, uid = "token", "user"

            def __init__(self, *args):
                pass

            def probe(self):
                return {"Id": "server", "ServerName": "test"}

            def stats(self):
                raise urllib.error.HTTPError(
                    "https://test", 429, "rate limit", {"Retry-After": "7200"}, None
                )

        with patch.object(emby, "Client", Client), patch.object(
            emby.time, "time", return_value=100000
        ):
            result = emby.collect({"primary_url": "https://test"}, {}, "test")
        self.assertEqual(result["state"], "healthy")
        self.assertEqual(result["line_control"]["https://test"], {})
        self.assertGreaterEqual(result["stats_retry_at"], 107200)
        self.assertLessEqual(result["stats_retry_at"], 107230)

    def test_statistics_requests_have_longer_timeout(self):
        client = object.__new__(emby.Client)
        client.uid = "user"
        calls = []
        client.request = lambda *args, **kwargs: calls.append(kwargs) or {}
        client.items("Movie")
        self.assertEqual(calls[0]["timeout"], 60)

    def test_days_validation(self):
        body = {
            "primary_url": "https://example.com",
            "username": "user",
            "password": "password",
        }
        for days in ("", 0, 1, 30, 3650):
            self.assertEqual(
                emby.validate_config({**body, "keepalive_days": days}, {})[
                    "keepalive_days"
                ],
                days or 0,
            )
        for days in (-1, 1.5, True, "30", 3651):
            with self.assertRaises(ValueError):
                emby.validate_config({**body, "keepalive_days": days}, {})

    def test_last_played_is_user_scoped_read_only_query(self):
        client = object.__new__(emby.Client)
        client.uid = "my-user"
        calls = []

        def request(path, params):
            calls.append((path, params))
            return {"Items": [{"UserData": {"LastPlayedDate": "2026-01-01T12:00:00Z"}}]}

        client.request = request
        self.assertIsNotNone(client.last_played())
        self.assertEqual(calls[0][0], "/Users/my-user/Items")
        self.assertEqual(calls[0][1]["SortBy"], "DatePlayed")
        self.assertEqual(calls[0][1]["Limit"], 1)
        self.assertNotIn("IsPlayed", calls[0][1])

    def test_list_missing_date_uses_single_item_details(self):
        client = object.__new__(emby.Client)
        client.uid = "my-user"
        calls = []

        def request(path, params=None):
            calls.append(path)
            if params is not None:
                return {"Items": [{"Id": "item", "UserData": {"PlayCount": 0}}]}
            return {"UserData": {"LastPlayedDate": "2026-01-01T12:00:00Z"}}

        client.request = request
        self.assertIsNotNone(client.last_played())
        self.assertEqual(calls, ["/Users/my-user/Items", "/Users/my-user/Items/item"])

    def test_playback_query_only_once_per_day(self):
        calls = []

        class Client:
            token = "token"
            uid = "user"

            def __init__(self, *args):
                pass

            def probe(self):
                return {"Id": "server", "ServerName": "test"}

            def last_played(self):
                calls.append(1)
                return 900

        config = {"primary_url": "https://test", "keepalive_days": 30}
        old = {
            "stats_at": 100000,
            "stats": {"day": str(emby.datetime.now(emby.CST).date())},
        }
        with patch.object(emby, "Client", Client), patch.object(
            emby.time, "time", return_value=100000
        ):
            result = emby.collect(config, old, "test")
            again = emby.collect(config, result, "test")
        self.assertEqual(len(calls), 1)
        self.assertEqual(again["last_played_at"], 900)
        with patch.object(emby, "Client", Client), patch.object(
            emby.time, "time", return_value=186400
        ):
            emby.collect(config, again, "test")
        self.assertEqual(len(calls), 2)
