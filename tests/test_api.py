import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

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
