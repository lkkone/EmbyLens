import http.client
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import app


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        app.DATA = Path(cls.temp.name)
        app.ADMIN_USERNAME = "test"
        app.ADMIN_PASSWORD = "test-only-password"
        app.SECURITY_CODE = "test-security-code"
        app.init_db()
        cls.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp.cleanup()

    def req(self, path, method="GET", body=None, cookie="", csrf=True):
        conn = http.client.HTTPConnection(*self.server.server_address)
        headers = {"Content-Type": "application/json", "Cookie": cookie}
        if csrf:
            headers["X-EmbyLens-Request"] = "1"
        conn.request(
            method, path, json.dumps(body) if body is not None else None, headers
        )
        response = conn.getresponse()
        value = json.loads(response.read())
        status = response.status
        head = dict(response.getheaders())
        conn.close()
        return status, value, head

    def test_legacy_route_toggle_keeps_existing_statistics(self):
        _, _, headers = self.req(
            "/api/login", "POST", {"username": "test", "password": "test-only-password"}
        )
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        body = {
            "primary_url": "https://legacy.example.com",
            "username": "u",
            "password": "p",
        }
        _, created, _ = self.req("/api/servers", "POST", body, cookie)
        sid = created["id"]
        with app.connect() as db:
            row = db.execute("SELECT config FROM servers WHERE id=?", (sid,)).fetchone()
            cfg = json.loads(row["config"])
            cfg.pop("client_name")
            db.execute(
                "UPDATE servers SET config=?, snapshot=? WHERE id=?",
                (
                    json.dumps(cfg),
                    json.dumps({"name": "Legacy name", "stats": {"total": 123}}),
                    sid,
                ),
            )
        with patch.dict(os.environ, {"OUTBOUND_PROXY_URL": "http://127.0.0.1:7897"}):
            self.assertEqual(
                self.req(
                    "/api/servers/" + sid,
                    "PATCH",
                    {
                        **body,
                        "password": "",
                        "client_name": "auto",
                        "primary_use_proxy": True,
                    },
                    cookie,
                )[0],
                200,
            )
        with app.connect() as db:
            row = db.execute(
                "SELECT snapshot FROM servers WHERE id=?", (sid,)
            ).fetchone()
            snapshot = json.loads(row["snapshot"])
            self.assertEqual(snapshot["name"], "Legacy name")
            self.assertEqual(snapshot["stats"]["total"], 123)
            self.assertTrue(snapshot["refresh_stats"])
        self.req("/api/servers/" + sid, "DELETE", {}, cookie)

    def test_metadata_save_is_immediate_and_current_history_matches(self):
        _, _, headers = self.req(
            "/api/login", "POST", {"username": "test", "password": "test-only-password"}
        )
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        body = {
            "primary_url": "https://main.example.com",
            "username": "u",
            "password": "p",
            "secondary_lines": [
                {"name": "Old backup", "url": "https://backup.example.com"}
            ],
        }
        _, created, _ = self.req("/api/servers", "POST", body, cookie)
        sid = created["id"]
        now = time.time()
        bucket = int(now // 600) * 600
        with app.connect() as db:
            db.execute(
                "UPDATE servers SET snapshot=? WHERE id=?",
                (
                    json.dumps(
                        {
                            "name": "Server",
                            "state": "healthy",
                            "checked_at": now,
                            "next_check_at": now + 300,
                            "stats": {"total": 123},
                            "lines": [
                                {"label": "主线路", "state": "healthy"},
                                {"label": "Old backup", "state": "healthy"},
                            ],
                        }
                    ),
                    sid,
                ),
            )
            for at in [bucket - 10, bucket + 0.1]:
                db.execute(
                    "INSERT INTO samples VALUES(?,?,?,?)", (sid, at, "slow", 100)
                )
        body["secondary_lines"][0]["name"] = "New backup"
        self.assertEqual(self.req("/api/servers/" + sid, "PATCH", body, cookie)[0], 200)
        _, public, _ = self.req("/api/status")
        server = next(s for s in public["servers"] if s["id"] == sid)
        self.assertEqual(server["lines"][1]["label"], "New backup")
        self.assertEqual(server["checked_at"], now)
        self.assertEqual(len(server["history"]), 30)
        self.assertEqual(server["history"][-1]["state"], "healthy")
        self.assertEqual(server["history"][-2]["state"], "slow")
        with app.connect() as db:
            snapshot = json.loads(
                db.execute(
                    "SELECT snapshot FROM servers WHERE id=?", (sid,)
                ).fetchone()["snapshot"]
            )
            self.assertNotIn("refresh_stats", snapshot)
            self.assertEqual(snapshot["next_check_at"], now + 300)
        self.req("/api/servers/" + sid, "DELETE", {}, cookie)

    def test_auth_crud_and_credential_redaction(self):
        self.assertEqual(self.req("/api/servers")[0], 401)
        self.assertEqual(
            self.req(
                "/api/login",
                "POST",
                {"username": "test", "password": "test-only-password"},
                csrf=False,
            )[0],
            403,
        )
        status, _, headers = self.req(
            "/api/login", "POST", {"username": "test", "password": "test-only-password"}
        )
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"].split(";")[0]
        status, value, _ = self.req(
            "/api/servers",
            "POST",
            {
                "primary_url": "https://example.com",
                "secondary_url": "",
                "username": "private-user",
                "password": "private-password",
                "group": "Test",
                "notes": "private-note",
            },
            cookie,
        )
        self.assertEqual(status, 201)
        sid = value["id"]
        endpoint = "/api/servers/" + sid + "/secret"
        self.assertEqual(
            self.req(
                endpoint,
                "POST",
                {"security_code": "test-security-code", "action": "password"},
            )[0],
            401,
        )
        self.assertEqual(
            self.req(
                endpoint,
                "POST",
                {"security_code": "wrong", "action": "password"},
                cookie,
            )[0],
            403,
        )
        status, secret, _ = self.req(
            endpoint,
            "POST",
            {"security_code": "test-security-code", "action": "password"},
            cookie,
        )
        self.assertEqual(status, 200)
        self.assertEqual(secret["password"], "private-password")
        status, export, _ = self.req(
            endpoint,
            "POST",
            {"security_code": "test-security-code", "action": "export"},
            cookie,
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            export["content"],
            "主路线:https://example.com:443\n副路线:\n副路线:\n用户名:private-user\n密码:private-password\n",
        )
        self.assertNotIn("test-security-code", export["content"])
        for _ in range(5):
            self.assertEqual(
                self.req(
                    endpoint,
                    "POST",
                    {"security_code": "wrong", "action": "password"},
                    cookie,
                )[0],
                403,
            )
        self.assertEqual(
            self.req(
                endpoint,
                "POST",
                {"security_code": "test-security-code", "action": "password"},
                cookie,
            )[0],
            429,
        )
        app.SECRET_FAILURES.clear()

        with app.connect() as db:
            db.execute(
                "UPDATE servers SET snapshot=? WHERE id=?",
                (
                    json.dumps(
                        {
                            "name": "Real name",
                            "stats": {
                                "movies": 12,
                                "series": 3,
                                "episodes": 45,
                                "total": 57,
                            },
                            "state": "healthy",
                            "checked_at": __import__("time").time(),
                        }
                    ),
                    sid,
                ),
            )
        _, public, _ = self.req("/api/status")
        serialized = json.dumps(public)
        for secret in [
            "private-user",
            "private-password",
            "private-note",
            "example.com",
        ]:
            self.assertNotIn(secret, serialized)
        self.assertEqual(public["servers"][0]["stats"]["total"], 57)
        _, private, _ = self.req("/api/servers", cookie=cookie)
        self.assertTrue(private["servers"][0]["has_password"])
        self.assertNotIn("password", private["servers"][0]["config"])
        self.assertEqual(
            self.req(
                "/api/servers/" + sid,
                "PATCH",
                {
                    "primary_url": "https://example.com",
                    "username": "private-user",
                    "password": "",
                    "group": "Test",
                },
                cookie,
            )[0],
            200,
        )
        with app.connect() as db:
            self.assertEqual(
                json.loads(
                    db.execute(
                        "SELECT config FROM servers WHERE id=?", (sid,)
                    ).fetchone()["config"]
                )["password"],
                "private-password",
            )
        self.assertEqual(
            self.req(
                "/api/servers/" + sid,
                "PATCH",
                {
                    "primary_url": "https://example.com",
                    "username": "private-user",
                    "display_name": "Custom name",
                    "secondary_lines": [
                        {"name": "Backup", "url": "https://backup.example.com"}
                    ],
                },
                cookie,
            )[0],
            200,
        )
        _, public, _ = self.req("/api/status")
        self.assertEqual(public["servers"][0]["name"], "Custom name")
        self.assertEqual(public["servers"][0]["automatic_name"], "Real name")
        self.assertEqual(
            self.req(
                "/api/servers/" + sid,
                "PATCH",
                {
                    "primary_url": "https://example.com",
                    "username": "private-user",
                    "display_name": "",
                    "secondary_lines": [],
                },
                cookie,
            )[0],
            200,
        )
        _, public, _ = self.req("/api/status")
        self.assertEqual(public["servers"][0]["name"], "Real name")
        self.assertEqual(
            self.req(
                "/api/servers/" + sid + "/secret",
                "PATCH",
                {"primary_url": "https://bad.example.com", "username": "x"},
                cookie,
            )[0],
            404,
        )
        self.assertEqual(
            self.req("/api/servers/" + sid + "/unknown", "DELETE", {}, cookie)[0], 404
        )
        with app.connect() as db:
            db.execute(
                "UPDATE servers SET snapshot=? WHERE id=?",
                (
                    json.dumps(
                        {
                            "name": "Real name",
                            "private_future_field": "should-stay-private",
                            "line_control": {"https://example.com": {"retry_at": 123}},
                        }
                    ),
                    sid,
                ),
            )
        _, public, _ = self.req("/api/status")
        self.assertNotIn("should-stay-private", json.dumps(public))
        self.assertNotIn("example.com", json.dumps(public))
        self.assertEqual(self.req("/api/servers/" + sid, "DELETE", {}, cookie)[0], 200)
        self.assertEqual(self.req("/api/logout", "POST", {}, cookie)[0], 200)
        self.assertEqual(self.req("/api/servers", cookie=cookie)[0], 401)


if __name__ == "__main__":
    unittest.main()
