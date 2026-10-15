import json
import os
import sys
import threading
import time
import unittest
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import emby


class CollectionTests(unittest.TestCase):
    def test_config_validation_and_blank_password(self):
        old = emby.validate_config(
            {
                "primary_url": "https://example.com",
                "username": "u",
                "password": "secret",
            }
        )
        self.assertEqual(
            emby.validate_config({**old, "password": ""}, old)["password"], "secret"
        )
        for url in [
            "http://example.com",
            "https://u:p@example.com",
            "https://example.com/?token=s",
        ]:
            with self.assertRaises(ValueError):
                emby.validate_config({**old, "primary_url": url})

    def test_invalid_ports_and_types(self):
        base = {
            "primary_url": "https://example.com",
            "username": "u",
            "password": "secret",
        }
        for body in (
            {**base, "primary_url": "https://example.com:0"},
            {**base, "primary_url": "https://example.com:70000"},
            {**base, "user_agent": {}},
            {**base, "password": 42},
        ):
            with self.assertRaises(ValueError):
                emby.validate_config(body)

    def test_multiple_lines_and_legacy_migration(self):
        base = {
            "primary_url": "https://main.example.com",
            "username": "u",
            "password": "secret",
            "secondary_url": "https://cf.example.com",
        }
        old = emby.validate_config(base)
        self.assertEqual(
            old["secondary_lines"],
            [
                {
                    "name": "CF 线路",
                    "url": "https://cf.example.com",
                    "slot": 0,
                    "use_proxy": False,
                }
            ],
        )
        config = emby.validate_config(
            {
                **old,
                "display_name": " My Emby ",
                "secondary_lines": [
                    {"name": "CF", "url": "https://cf.example.com"},
                    {"name": "备用", "url": "https://backup.example.com"},
                ],
            },
            old,
        )
        self.assertEqual(config["display_name"], "My Emby")
        self.assertEqual(len(config["secondary_lines"]), 2)
        slot = emby.validate_config(
            {
                **old,
                "secondary_lines": [
                    {"name": "第二条", "url": "https://third.example.com", "slot": 1}
                ],
            },
            old,
        )
        self.assertEqual(slot["secondary_lines"][0]["slot"], 1)
        with self.assertRaises(ValueError):
            emby.validate_config(
                {
                    **old,
                    "secondary_lines": [
                        {"name": str(i), "url": f"https://{i}.example.com"}
                        for i in range(3)
                    ],
                },
                old,
            )
        for lines in [
            [{"name": "", "url": "https://backup.example.com"}],
            [{"name": "same", "url": base["primary_url"]}],
            [{"name": "bad", "url": "http://bad.example.com"}],
        ]:
            with self.assertRaises(ValueError):
                emby.validate_config({**config, "secondary_lines": lines})

    def test_user_agent_configuration(self):
        config = emby.validate_config(
            {
                "primary_url": "https://example.com",
                "username": "u",
                "password": "secret",
                "user_agent": "Forward",
            }
        )
        client = emby.Client("https://example.com", config, "test")
        self.assertEqual(client.headers["User-Agent"], "Forward")
        self.assertIn('Client="Forward"', client.headers["X-Emby-Authorization"])
        with self.assertRaises(ValueError):
            emby.validate_config({**config, "user_agent": "Forward\r\nInjected: value"})

    def test_client_auth_identity(self):
        config = emby.validate_config(
            {
                "primary_url": "https://example.com",
                "username": "u",
                "password": "secret",
                "client_name": "Infuse",
                "user_agent": "Infuse",
            }
        )
        client = emby.Client("https://example.com", config, "test")
        self.assertIn(
            'MediaBrowser Client="Infuse"', client.headers["X-Emby-Authorization"]
        )
        self.assertEqual(
            emby.validate_config({**config, "client_name": "Forward"}, config)[
                "tokens"
            ],
            {},
        )
        with self.assertRaises(ValueError):
            emby.validate_config({**config, "client_name": 'Bad"client'})

    def test_identity_does_not_cycle_on_403(self):
        config = emby.validate_config(
            {
                "primary_url": "https://example.com",
                "username": "u",
                "password": "secret",
            }
        )
        client = emby.Client(config["primary_url"], config, "test")
        with patch.object(
            client,
            "request",
            side_effect=emby.urllib.error.HTTPError(
                "https://example.com", 403, "blocked", {}, None
            ),
        ) as request:
            with self.assertRaises(emby.urllib.error.HTTPError):
                client.login()
            self.assertEqual(request.call_count, 1)
        config["client_name"] = "auto"
        config["tokens"][config["primary_url"]] = {
            "token": "t",
            "uid": "u",
            "client_name": "Infuse",
            "user_agent": "Infuse",
        }
        cached = emby.Client(config["primary_url"], config, "test")
        self.assertEqual(cached.client_name, "Infuse")
        self.assertEqual(cached.token, "t")
        del config["client_name"]
        legacy = emby.Client(config["primary_url"], config, "test")
        self.assertEqual(legacy.client_name, "Infuse")
        self.assertEqual(
            emby.validate_config(config, config)["tokens"], config["tokens"]
        )

    def test_route_proxy_selection_and_validation(self):
        base = {
            "primary_url": "https://main.example.com",
            "username": "u",
            "password": "p",
        }
        with patch.dict(os.environ, {"OUTBOUND_PROXY_URL": ""}):
            with self.assertRaises(emby.ProxyConfigurationError):
                emby.validate_config({**base, "primary_use_proxy": True})
        with patch.dict(os.environ, {"OUTBOUND_PROXY_URL": "http://localhost:7897"}):
            cfg = emby.validate_config(
                {
                    **base,
                    "secondary_lines": [
                        {
                            "name": "backup",
                            "url": "https://backup.example.com",
                            "use_proxy": True,
                        }
                    ],
                }
            )
            self.assertFalse(cfg["primary_use_proxy"])
            self.assertTrue(cfg["secondary_lines"][0]["use_proxy"])
            for value in ["yes", 1, None]:
                with self.assertRaises(ValueError):
                    emby.validate_config({**base, "primary_use_proxy": value})
        with patch.dict(os.environ, {"OUTBOUND_PROXY_URL": "socks5://localhost:7897"}):
            with self.assertRaises(emby.ProxyConfigurationError):
                emby.validate_config({**base, "primary_use_proxy": True})

    def test_explicit_proxy_transport_and_direct_ignore_environment(self):
        paths = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                paths.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        address = "http://127.0.0.1:" + str(server.server_port)
        cfg = {"primary_url": "http://unreachable.invalid", "primary_use_proxy": True}
        try:
            with patch.dict(
                os.environ,
                {"OUTBOUND_PROXY_URL": address, "NO_PROXY": "*", "no_proxy": "*"},
            ):
                self.assertTrue(
                    emby.Client(cfg["primary_url"], cfg, "test").request("/test")["ok"]
                )
                self.assertEqual(paths[-1], "http://unreachable.invalid/emby/test")
            cfg = {"primary_url": address}
            with patch.dict(
                os.environ,
                {
                    "http_proxy": "http://127.0.0.1:1",
                    "HTTP_PROXY": "http://127.0.0.1:1",
                    "NO_PROXY": "",
                    "no_proxy": "",
                },
            ):
                self.assertTrue(
                    emby.Client(address, cfg, "test").request("/direct")["ok"]
                )
                self.assertEqual(paths[-1], "/emby/direct")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_cloudflare_block_has_specific_diagnostic(self):
        blocked = emby.urllib.error.HTTPError(
            "https://example.com",
            403,
            "blocked",
            {"Server": "cloudflare", "Content-Type": "text/html; charset=UTF-8"},
            None,
        )
        self.assertIn("Cloudflare", emby.error_label(blocked))
        blocked.headers["Content-Type"] = "application/json"
        self.assertEqual(emby.error_label(blocked), "访问受限（HTTP 403）")

    def test_auto_identity_does_not_retry_invalid_password(self):
        config = emby.validate_config(
            {
                "primary_url": "https://example.com",
                "username": "u",
                "password": "secret",
            }
        )
        client = emby.Client(config["primary_url"], config, "test")
        with patch.object(
            client,
            "request",
            side_effect=emby.urllib.error.HTTPError(
                "https://example.com", 401, "invalid", {}, None
            ),
        ) as request:
            with self.assertRaises(emby.urllib.error.HTTPError):
                client.login()
            self.assertEqual(request.call_count, 1)

    def test_daily_pagination_and_timezone(self):
        today = datetime.now(emby.CST).replace(
            hour=0, minute=1, second=0, microsecond=0
        )
        yesterday = today - timedelta(days=1)
        calls = []

        def items(kind, limit=1, start=0):
            calls.append((kind, start))
            if limit == 1:
                return {
                    "TotalRecordCount": {"Movie": 123, "Series": 45, "Episode": 678}[
                        kind
                    ]
                }
            date = today if start == 0 else yesterday
            return {
                "Items": [
                    {"Name": "item", "Type": "Episode", "DateCreated": date.isoformat()}
                    for _ in range(200 if start == 0 else 1)
                ]
            }

        c = object.__new__(emby.Client)
        c.items = items
        result = c.stats()
        self.assertEqual(result["today"], 200)
        self.assertEqual(result["total"], 801)
        self.assertIn(("Movie,Episode", 200), calls)

    def test_episode_query_disconnect_fallback(self):
        def items(kind, limit=1, start=0):
            if limit != 1:
                return {"Items": []}
            if kind == "Episode":
                raise ConnectionError("server disconnected")
            return {
                "TotalRecordCount": {"Movie": 12, "Series": 3, "Movie,Episode": 57}[
                    kind
                ]
            }

        c = object.__new__(emby.Client)
        c.items = items
        result = c.stats()
        self.assertEqual(result["episodes"], 45)
        self.assertEqual(result["total"], 57)
        self.assertEqual(result["movies"], 12)

    def test_daily_collection_has_no_400_limit(self):
        calls = []
        today = datetime.now(emby.CST).isoformat()

        def items(kind, limit=1, start=0):
            if limit == 1:
                return {"TotalRecordCount": 1000}
            calls.append(start)
            return (
                {"Items": [{"DateCreated": today} for _ in range(200)]}
                if start < 1000
                else {"Items": []}
            )

        c = object.__new__(emby.Client)
        c.items = items
        result = c.stats()
        self.assertEqual(calls, [0, 200, 400, 600, 800, 1000])
        self.assertEqual(result["today"], 1000)
        self.assertTrue(result["today_complete"])

    def test_repeated_pages_fail_instead_of_looping_forever(self):
        c = object.__new__(emby.Client)

        def items(kind, limit=1, start=0):
            if limit == 1:
                return {"TotalRecordCount": 1000}
            return {
                "Items": [
                    {"Id": str(i), "DateCreated": datetime.now(emby.CST).isoformat()}
                    for i in range(200)
                ]
            }

        c.items = items
        with self.assertRaisesRegex(ValueError, "重复"):
            c.stats()

    def test_blocked_stats_does_not_fallback(self):
        c = object.__new__(emby.Client)
        with patch.object(
            c,
            "items",
            create=True,
            side_effect=[
                {"TotalRecordCount": 1},
                {"TotalRecordCount": 1},
                emby.urllib.error.HTTPError("https://a", 429, "limited", {}, None),
            ],
        ) as items:
            with self.assertRaises(emby.urllib.error.HTTPError):
                c.stats()
            self.assertEqual(items.call_count, 3)

    def test_auth_failure_pauses_until_save(self):
        config = {"primary_url": "https://a", "tokens": {}}
        blocked = {
            "line_control": {
                "https://a": {
                    "auth_failed": True,
                    "line": {"label": "主线路", "state": "error", "error": "认证失败"},
                }
            }
        }
        with patch.object(emby, "Client") as client:
            emby.collect(config, blocked, "test")
            client.assert_not_called()

    def test_rate_limit_retry_after(self):
        exc = emby.urllib.error.HTTPError(
            "https://a", 429, "limited", {"Retry-After": "7200"}, None
        )
        state = emby.failure_state(exc, {})
        self.assertGreaterEqual(state["retry_at"], time.time() + 7199)

    def test_failure_keeps_previous_statistics(self):
        class Client:
            def __init__(self, *args):
                pass

            def probe(self):
                raise TimeoutError()

        with patch.object(emby, "Client", Client), patch.object(
            emby.time, "time", return_value=725
        ):
            old = {"stats": {"movies": 22}, "stats_at": 123}
            result = emby.collect(
                {"primary_url": "https://a", "secondary_url": "https://b"}, old, "test"
            )
        self.assertEqual(result["next_check_at"], 900)
        self.assertEqual(result["stats"]["movies"], 22)
        self.assertEqual(result["state"], "error")

    def test_failover_and_identity_check(self):
        class Client:
            def __init__(self, base, *args):
                self.base = base
                self.token = "private-token"
                self.uid = "private-user"

            def response(self):
                if self.base == "https://a":
                    raise TimeoutError()
                return {"Id": "same", "ServerName": "Actual Emby", "Version": "1"}, 400

            headers = {"User-Agent": "test"}

            def ensure_account(self, validate=False):
                pass

            def stats(self):
                return {"movies": 42, "day": str(datetime.now(emby.CST).date())}

        with patch.object(emby, "Client", Client):
            result = emby.collect(
                {
                    "primary_url": "https://a",
                    "secondary_lines": [
                        {"name": "备用一", "url": "https://b"},
                        {"name": "备用二", "url": "https://c"},
                    ],
                    "tokens": {},
                },
                {},
                "test",
            )
        self.assertEqual(len(result["lines"]), 3)
        self.assertEqual(result["lines"][2]["label"], "备用二")
        self.assertEqual(result["name"], "Actual Emby")
        self.assertEqual(result["stats"]["movies"], 42)
        self.assertEqual(result["state"], "slow")
        self.assertNotIn("private-token", json.dumps(result))

    def test_fast_response_classification_skips_hourly_and_daily_queries(self):
        calls = []

        class Client:
            token, uid = "cached-token", "cached-user"
            client_name = "test"
            headers = {"User-Agent": "test"}

            def __init__(self, base, *args):
                self.base = base

            def response(self):
                calls.append((self.base, "response"))
                ms = {"https://green": 2000, "https://amber": 2001,
                      "https://red": 4001}[self.base]
                return {"Id": "same"}, ms

            def ensure_account(self, validate=False):
                calls.append((self.base, "account"))

            def stats(self):
                calls.append((self.base, "stats"))

        now = 100000
        config = {
            "primary_url": "https://green",
            "secondary_lines": [
                {"name": "黄", "url": "https://amber"},
                {"name": "红", "url": "https://red"},
            ],
            "tokens": {base: {"token": "cached-token", "checked_at": now}
                       for base in ("https://green", "https://amber", "https://red")},
        }
        old = {"details_at": now, "stats": {"day": str(datetime.now(emby.CST).date())}}
        with patch.object(emby, "Client", Client), patch.object(
            emby.time, "time", return_value=now
        ):
            result = emby.collect(config, old, "test")
        self.assertEqual([line["state"] for line in result["lines"]],
                         ["healthy", "slow", "degraded"])
        self.assertEqual(result["state"], "slow")
        self.assertEqual(result["next_check_at"], 100200)
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(kind == "response" for _, kind in calls))

    def test_hourly_details_and_daily_account_validation(self):
        calls = []

        class Client:
            token, uid = "cached-token", "cached-user"
            client_name = "test"
            headers = {"User-Agent": "test"}

            def __init__(self, *args):
                pass

            def response(self):
                calls.append("response")
                return {"Id": "server", "ServerName": "Hourly", "Version": "2"}, 300

            def ensure_account(self, validate=False):
                calls.append(("account", validate))

            def stats(self):
                calls.append("stats")
                return {"movies": 42, "day": str(datetime.now(emby.CST).date())}

        now = 100000
        config = {"primary_url": "https://main", "tokens": {
            "https://main": {"token": "cached-token", "checked_at": now - 100}
        }}
        old = {"details_at": now - 3600, "stats": {
            "day": str(datetime.now(emby.CST).date())}}
        with patch.object(emby, "Client", Client), patch.object(
            emby.time, "time", return_value=now
        ):
            result = emby.collect(config, old, "test")
        self.assertEqual(calls, ["response", ("account", False), "stats"])
        self.assertEqual(result["name"], "Hourly")
        self.assertEqual(result["version"], "2")
        self.assertEqual(result["stats"]["movies"], 42)
        calls.clear()
        with patch.object(emby, "Client", Client), patch.object(
            emby.time, "time", return_value=now + 86400
        ):
            emby.collect(config, result, "test")
        self.assertIn(("account", True), calls)


if __name__ == "__main__":
    unittest.main()
