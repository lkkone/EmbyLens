"""EmbyLens HTTP API. Standard library only; SQLite is the durable source of truth."""

import hashlib
import hmac
import json
import logging
import mimetypes
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from emby import collect, secondary_lines, validate_config

DATA = Path(os.environ.get("DATA_DIR", "/data"))
WEB = Path(__file__).parent / "web"
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() == "true"
SECURITY_CODE = os.environ.get("SECURITY_CODE", "")
SECRET_FAILURES = {}
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "30"))
DB_LOCK = threading.Lock()
LOGIN_LOCK = threading.Lock()
LOGIN_FAILURES = {}
logger = logging.getLogger("embylens")


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt):
    return hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1
    ).hex()


@contextmanager
def connect():
    db = sqlite3.connect(DATA / "embylens.db", timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        with db:
            yield db
    finally:
        db.close()


def init_db():
    DATA.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(hash TEXT PRIMARY KEY, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS servers(
          id TEXT PRIMARY KEY, group_name TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
          config TEXT NOT NULL, snapshot TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL,
          generation INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS samples(
          server_id TEXT NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
          at REAL NOT NULL, state TEXT NOT NULL, delay REAL);
        CREATE INDEX IF NOT EXISTS samples_time ON samples(server_id,at);
        """)
        if not ADMIN_USERNAME.strip() or len(ADMIN_PASSWORD) < 8:
            raise ValueError(
                "请通过 ADMIN_USERNAME 和 ADMIN_PASSWORD 配置账号与至少 8 位密码"
            )
        account = db.execute(
            "SELECT value FROM settings WHERE key='username'"
        ).fetchone()
        stored = db.execute(
            "SELECT value FROM settings WHERE key='password'"
        ).fetchone()
        password = json.loads(stored["value"]) if stored else None
        unchanged = (
            account
            and account["value"] == ADMIN_USERNAME
            and password
            and hmac.compare_digest(
                password_hash(ADMIN_PASSWORD, password["salt"]), password["hash"]
            )
        )
        if not unchanged:
            salt = secrets.token_hex(16)
            db.execute(
                "INSERT OR REPLACE INTO settings VALUES(?,?)",
                (
                    "password",
                    json.dumps(
                        {"salt": salt, "hash": password_hash(ADMIN_PASSWORD, salt)}
                    ),
                ),
            )
            db.execute(
                "INSERT OR REPLACE INTO settings VALUES(?,?)",
                ("username", ADMIN_USERNAME),
            )
            db.execute("DELETE FROM sessions")


def text(value, limit=200):
    if not isinstance(value, str):
        raise TypeError("文本字段格式错误")
    return value[:limit]


WAKE = threading.Event()


def serialize(db, row, now, private=False):
    snapshot = json.loads(row["snapshot"])
    state = snapshot.get("state", "pending")
    if snapshot.get("checked_at") and now - snapshot["checked_at"] > 1800:
        state = "offline"
    rows = db.execute(
        "SELECT at,state,delay FROM samples WHERE server_id=? AND at>=? AND at<=? ORDER BY at DESC LIMIT 60",
        (row["id"], now - 18000, now),
    ).fetchall()
    history = [
        {
            "at": r["at"],
            "sample_at": r["at"],
            "state": r["state"],
            "delay_ms": r["delay"],
        }
        for r in reversed(rows)
    ]
    public_fields = (
        "name",
        "state",
        "lines",
        "checked_at",
        "stats",
        "stats_at",
        "stats_error",
        "version",
        "server_id",
        "error",
    )
    result = {
        "id": row["id"],
        "group": row["group_name"],
        "history": history,
        **{k: snapshot[k] for k in public_fields if k in snapshot},
    }
    result["state"] = state
    config = json.loads(row["config"])
    result["automatic_name"] = snapshot.get("name") or ""
    result["name"] = (
        config.get("display_name") or snapshot.get("name") or "等待获取名称"
    )
    if private:
        config = {
            **config,
            "display_name": config.get("display_name", ""),
            "client_name": config.get("client_name", "auto"),
            "secondary_lines": secondary_lines(config),
        }
        config.pop("secondary_url", None)
        result.update(
            notes=row["notes"],
            config={k: v for k, v in config.items() if k not in ("password", "tokens")},
            has_password=bool(config.get("password")),
        )
    return result


def worker():
    while True:
        with DB_LOCK, connect() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM servers")]
            for row in rows:
                row["snapshot"] = json.loads(row["snapshot"])
            rows.sort(
                key=lambda row: (
                    not (
                        row["snapshot"].get("refresh_stats")
                        or not row["snapshot"].get("checked_at")
                    ),
                    -row["created"],
                )
            )
        for row in rows:
            config = json.loads(row["config"])
            old = row["snapshot"]
            if time.time() < old.get("next_check_at", 0):
                continue
            try:
                snapshot = collect(config, old, row["id"])
            except Exception:
                logger.exception("Collection failed for server %s", row["id"])
                snapshot = {
                    **old,
                    "state": "error",
                    "checked_at": time.time(),
                    "error": "采集失败",
                }
            with DB_LOCK, connect() as db:
                current = db.execute(
                    "SELECT generation FROM servers WHERE id=?", (row["id"],)
                ).fetchone()
                if not current or current["generation"] != row["generation"]:
                    continue
                db.execute(
                    "UPDATE servers SET config=?,snapshot=? WHERE id=?",
                    (json.dumps(config), json.dumps(snapshot), row["id"]),
                )
                main = snapshot.get("lines", [])
                db.execute(
                    "INSERT INTO samples VALUES(?,?,?,?)",
                    (
                        row["id"],
                        time.time(),
                        snapshot["state"],
                        main[0].get("delay_ms") if main else None,
                    ),
                )
                db.execute(
                    "DELETE FROM samples WHERE at<?",
                    (time.time() - RETENTION_DAYS * 86400,),
                )
            if WAKE.is_set():
                break
        WAKE.wait(5)
        WAKE.clear()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # Don't log bodies, authentication headers, query strings or credentials.
        logger.info(
            "%s %s %s",
            self.command,
            urlsplit(self.path).path,
            args[1] if len(args) > 1 else "",
        )

    def response(
        self, status, body, content_type="application/json; charset=utf-8", headers=None
    ):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
        )
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("无效请求长度")
        if not 0 < size <= 1024 * 1024:
            raise ValueError("请求大小应在 1MB 以内")
        data = json.loads(self.rfile.read(size))
        if not isinstance(data, dict):
            raise TypeError("请求必须为 JSON 对象")
        return data

    def session(self, db):
        cookies = {}
        for part in self.headers.get("Cookie", "").split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                cookies[k] = v
        token = cookies.get("embylens_session", "")
        return (
            db.execute(
                "SELECT 1 FROM sessions WHERE hash=? AND expires>?",
                (digest(token), time.time()),
            ).fetchone()
            is not None
        )

    def cookie(self, token, max_age=604800):
        return (
            f"embylens_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={max_age}"
            + ("; Secure" if COOKIE_SECURE else "")
        )

    def login_session(self, db):
        token = secrets.token_urlsafe(32)
        db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
        db.execute(
            "INSERT INTO sessions VALUES(?,?)", (digest(token), time.time() + 604800)
        )
        return {"Set-Cookie": self.cookie(token)}

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PATCH(self):
        self.dispatch("PATCH")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def dispatch(self, method):
        try:
            self.connection.settimeout(15)
            self.route(method)
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            json.JSONDecodeError,
        ) as exc:
            self.response(400, {"error": str(exc)[:200]})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True
        except Exception:
            logger.exception("Request failed")
            self.response(500, {"error": "服务端错误，请查看容器日志"})

    def route(self, method):
        path = urlsplit(self.path).path
        if path == "/healthz" and method == "GET":
            with connect() as db:
                db.execute("SELECT 1").fetchone()
            return self.response(200, {"status": "ok", "version": "0.1.0"})
        if not path.startswith("/api/") and method == "GET":
            assets = {
                "/": "index.html",
                "/admin": "index.html",
                "/admin/": "index.html",
            }
            relative_path = assets.get(path, path.lstrip("/"))
            file = (WEB / relative_path).resolve()
            if not file.is_relative_to(WEB.resolve()):
                return self.response(404, {"error": "页面不存在"})
            if file.is_file():
                return self.response(
                    200,
                    file.read_bytes(),
                    mimetypes.guess_type(str(file))[0] or "application/octet-stream",
                )
            return self.response(404, {"error": "页面不存在"})
        # Serialize configuration changes and session validation.
        with DB_LOCK, connect() as db:
            if path in ("/api/status", "/api/servers") and method == "GET":
                private = path == "/api/servers"
                if private and not self.session(db):
                    return self.response(401, {"error": "请先登录"})
                now = time.time()
                rows = [
                    serialize(db, r, now, private)
                    for r in db.execute(
                        "SELECT * FROM servers ORDER BY group_name,created"
                    )
                ]
                return self.response(200, {"servers": rows, "server_time": now})
            if path == "/api/auth" and method == "GET":
                return self.response(200, {"authenticated": self.session(db)})
            if method in ("POST", "PATCH", "DELETE"):
                if self.headers.get("X-EmbyLens-Request") != "1":
                    return self.response(403, {"error": "请求验证失败"})
                origin = self.headers.get("Origin")
                if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                    return self.response(403, {"error": "来源验证失败"})
            if path == "/api/login" and method == "POST":
                ip = self.client_address[0]
                now = time.time()
                with LOGIN_LOCK:
                    failures = [t for t in LOGIN_FAILURES.get(ip, []) if t > now - 300]
                    if len(failures) >= 10:
                        return self.response(
                            429, {"error": "尝试过于频繁，请 5 分钟后再试"}
                        )
                body = self.body()
                password = text(body.get("password", ""), 1024)
                username = text(body.get("username", ""), 200)
                stored = db.execute(
                    "SELECT value FROM settings WHERE key='password'"
                ).fetchone()
                p = json.loads(stored["value"]) if stored else None
                account = db.execute(
                    "SELECT value FROM settings WHERE key='username'"
                ).fetchone()
                if (
                    not p
                    or not account
                    or not hmac.compare_digest(
                        username.encode(), account["value"].encode()
                    )
                    or not hmac.compare_digest(
                        password_hash(password, p["salt"]), p["hash"]
                    )
                ):
                    with LOGIN_LOCK:
                        LOGIN_FAILURES[ip] = failures + [now]
                    return self.response(401, {"error": "账号或密码错误"})
                with LOGIN_LOCK:
                    LOGIN_FAILURES.pop(ip, None)
                headers = self.login_session(db)
                db.commit()
                return self.response(200, {"ok": True}, headers=headers)
            if path == "/api/logout" and method == "POST":
                cookie = self.headers.get("Cookie", "")
                for part in cookie.split(";"):
                    if part.strip().startswith("embylens_session="):
                        db.execute(
                            "DELETE FROM sessions WHERE hash=?",
                            (digest(part.strip().split("=", 1)[1]),),
                        )
                db.commit()
                return self.response(
                    200, {"ok": True}, headers={"Set-Cookie": self.cookie("", 0)}
                )
            if not self.session(db):
                return self.response(401, {"error": "请先登录"})
            if path == "/api/servers" and method == "POST":
                body = self.body()
                config = validate_config(body)
                sid = secrets.token_hex(8)
                db.execute(
                    "INSERT INTO servers(id,group_name,notes,config,created) VALUES(?,?,?,?,?)",
                    (
                        sid,
                        text(body.get("group", "我的 Emby")).strip() or "我的 Emby",
                        text(body.get("notes", ""), 1000),
                        json.dumps(config),
                        time.time(),
                    ),
                )
                db.commit()
                WAKE.set()
                return self.response(201, {"id": sid})
            if path.startswith("/api/servers/"):
                sid = path.split("/")[3]
                row = db.execute("SELECT * FROM servers WHERE id=?", (sid,)).fetchone()
                if not row:
                    return self.response(404, {"error": "服务器不存在"})
                if path == f"/api/servers/{sid}/secret" and method == "POST":
                    body = self.body()
                    now = time.time()
                    ip = self.client_address[0]
                    failures = [t for t in SECRET_FAILURES.get(ip, []) if t > now - 300]
                    if len(failures) >= 5:
                        return self.response(
                            429, {"error": "安全码错误次数过多，请 5 分钟后再试"}
                        )
                    if not SECURITY_CODE:
                        return self.response(503, {"error": "服务端尚未配置安全码"})
                    code = text(body.get("security_code", ""), 200)
                    if not hmac.compare_digest(code.encode(), SECURITY_CODE.encode()):
                        SECRET_FAILURES[ip] = failures + [now]
                        return self.response(403, {"error": "安全码错误"})
                    SECRET_FAILURES.pop(ip, None)
                    config = json.loads(row["config"])
                    if body.get("action") == "password":
                        return self.response(200, {"password": config["password"]})
                    if body.get("action") != "export":
                        raise ValueError("操作无效")

                    def address(value):

                        if not value:
                            return ""
                        parsed = urlsplit(value)
                        host = parsed.netloc if parsed.port else parsed.netloc + ":443"
                        return urlunsplit(
                            (
                                parsed.scheme,
                                host,
                                parsed.path,
                                parsed.query,
                                parsed.fragment,
                            )
                        )

                    backups = {
                        line.get("slot", index): line["url"]
                        for index, line in enumerate(secondary_lines(config))
                    }
                    fields = [
                        ("主路线", address(config["primary_url"])),
                        ("副路线", address(backups.get(0, ""))),
                        ("副路线", address(backups.get(1, ""))),
                        ("用户名", config["username"]),
                        ("密码", config["password"]),
                    ]
                    content = (
                        "\n".join(f"{key}:{value}" for key, value in fields) + "\n"
                    )
                    return self.response(200, {"content": content})
                if path != f"/api/servers/{sid}":
                    return self.response(404, {"error": "接口不存在"})
                if method == "PATCH":
                    body = self.body()
                    config = validate_config(body, json.loads(row["config"]))
                    previous = json.loads(row["config"])
                    snapshot = (
                        json.loads(row["snapshot"])
                        if all(
                            config[k]
                            == previous.get(k, "auto" if k == "client_name" else None)
                            for k in (
                                "username",
                                "password",
                                "primary_url",
                                "user_agent",
                                "client_name",
                            )
                        )
                        else {}
                    )

                    def routes(value):
                        return [
                            (
                                value["primary_url"],
                                value.get("primary_use_proxy", False),
                                "主线路",
                            )
                        ] + [
                            (line["url"], line.get("use_proxy", False), line["name"])
                            for line in secondary_lines(value)
                        ]

                    previous_routes = routes(previous)
                    current_routes = routes(config)
                    measured = {
                        (url, proxy): line
                        for (url, proxy, _), line in zip(
                            previous_routes, snapshot.get("lines", [])
                        )
                    }
                    if snapshot.get("lines"):
                        snapshot["lines"] = [
                            {
                                **measured.get((url, proxy), {"state": "pending"}),
                                "label": label,
                            }
                            for url, proxy, label in current_routes
                        ]
                    connection_changed = not snapshot.get("checked_at") or [
                        r[:2] for r in previous_routes
                    ] != [r[:2] for r in current_routes]
                    if connection_changed:
                        for field in (
                            "line_control",
                            "next_check_at",
                            "stats_retry_at",
                        ):
                            snapshot.pop(field, None)
                        snapshot["refresh_stats"] = True
                        states = [line["state"] for line in snapshot.get("lines", [])]
                        if states:
                            snapshot["state"] = (
                                "pending"
                                if "pending" in states
                                else (
                                    "healthy"
                                    if all(state == "healthy" for state in states)
                                    else "slow" if "healthy" in states else "error"
                                )
                            )
                    db.execute(
                        "UPDATE servers SET group_name=?,notes=?,config=?,snapshot=?,generation=generation+1 WHERE id=?",
                        (
                            text(body.get("group", "我的 Emby")).strip() or "我的 Emby",
                            text(body.get("notes", ""), 1000),
                            json.dumps(config),
                            json.dumps(snapshot),
                            sid,
                        ),
                    )
                    db.commit()
                    WAKE.set()
                    return self.response(200, {"ok": True})
                if method == "DELETE":
                    db.execute("DELETE FROM servers WHERE id=?", (sid,))
                    db.commit()
                    return self.response(200, {"ok": True})
            return self.response(404, {"error": "接口不存在"})


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    init_db()
    (DATA / "embylens.db").chmod(0o600)
    if os.environ.get("COLLECTOR_ENABLED", "true").lower() == "true":
        threading.Thread(target=worker, daemon=True).start()
    server = ThreadingHTTPServer(
        (
            os.environ.get("BIND_ADDRESS", "127.0.0.1"),
            int(os.environ.get("PORT", "18791")),
        ),
        Handler,
    )
    server.daemon_threads = True
    logger.info("EmbyLens listening on %s", server.server_address)
    server.serve_forever()
