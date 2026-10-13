"""User-scoped Emby collection. Credentials never enter public snapshots."""

import json
import random
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlencode, urlsplit

CST = timezone(timedelta(hours=8))


def secondary_lines(config):
    if "secondary_lines" in config:
        return config["secondary_lines"]
    return (
        [{"name": "CF 线路", "url": config["secondary_url"]}]
        if config.get("secondary_url")
        else []
    )


def validate_config(body, old=None):
    old = old or {}

    def url(value, required=True):
        if not isinstance(value, str):
            raise ValueError("线路地址格式错误")
        value = value.strip().rstrip("/")
        u = urlsplit(value)
        if not value and not required:
            return ""
        try:
            port = u.port
        except ValueError:
            raise ValueError("线路端口无效")
        if (
            port == 0
            or u.scheme != "https"
            or not u.hostname
            or u.username
            or u.password
            or u.query
            or u.fragment
        ):
            raise ValueError("线路地址必须是无凭证和查询参数的 HTTPS 地址")
        return value

    result = {"primary_url": url(body.get("primary_url", ""))}
    name = body.get("display_name", "")
    if not isinstance(name, str) or len(name) > 200:
        raise ValueError("服务器名称应在 200 字符以内")
    result["display_name"] = name.strip()
    lines = (
        secondary_lines(body)
        if "secondary_lines" in body or "secondary_url" in body
        else secondary_lines(old)
    )
    if not isinstance(lines, list) or len(lines) > 2:
        raise ValueError("最多添加 2 个备用服务器")
    result["secondary_lines"] = []
    seen = {result["primary_url"]}
    slots = set()
    for index, line in enumerate(lines):
        if not isinstance(line, dict):
            raise ValueError("副线路格式错误")
        slot = line.get("slot", index)
        if type(slot) is not int or not 0 <= slot < 2 or slot in slots:
            raise ValueError("副线路位置无效")
        slots.add(slot)
        label = line.get("name", "")
        if not isinstance(label, str) or not label.strip() or len(label) > 100:
            raise ValueError("请填写副线路名称（100 字符以内）")
        address = url(line.get("url", ""))
        if address in seen:
            raise ValueError("线路地址不能重复")
        seen.add(address)
        result["secondary_lines"].append(
            {"name": label.strip(), "url": address, "slot": slot}
        )
    agent = body.get("user_agent", old.get("user_agent", "Forward"))
    if not isinstance(agent, str):
        raise ValueError("请求标识格式错误")
    result["user_agent"] = agent.strip() or "Forward"
    if len(result["user_agent"]) > 200 or any(
        ord(c) < 32 or ord(c) > 126 for c in result["user_agent"]
    ):
        raise ValueError("请求标识应为 200 字符以内的 ASCII 文本")
    client_name = body.get("client_name", old.get("client_name", "Forward"))
    if (
        not isinstance(client_name, str)
        or not client_name
        or len(client_name) > 100
        or any(ord(c) < 32 or ord(c) > 126 or c in '"\\' for c in client_name)
    ):
        raise ValueError("客户端名称格式错误")
    result["client_name"] = client_name
    username = body.get("username", "")
    password = body.get("password") or old.get("password", "")
    if not isinstance(username, str) or not isinstance(password, str):
        raise ValueError("账号或密码格式错误")
    result["username"] = username.strip()
    result["password"] = password
    if not result["username"] or not result["password"]:
        raise ValueError("请填写 Emby 账号和密码")
    if len(result["username"]) > 200 or len(result["password"]) > 1024:
        raise ValueError("账号或密码过长")
    result["tokens"] = (
        {k: v for k, v in old.get("tokens", {}).items() if k in seen}
        if all(
            result[k] == old.get(k)
            for k in ("username", "password", "user_agent", "client_name")
        )
        else {}
    )
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not forward credentials or tokens to a redirected host.
        raise urllib.error.HTTPError(
            req.full_url, code, "redirect refused", headers, fp
        )


class Client:
    def __init__(self, base, config, sid):
        self.base = base.rstrip("/")
        if not self.base.endswith("/emby"):
            self.base += "/emby"
        self.config = config
        self.sid = sid
        cached = config.setdefault("tokens", {}).get(base, {})
        configured = config.get("client_name", "Forward")
        self.automatic = (
            configured == "auto"
        )  # legacy profiles reuse only, never cycle identities
        self.headers = {"Content-Type": "application/json"}
        name = cached.get("client_name", "EmbyLens") if self.automatic else configured
        agent = (
            cached.get("user_agent", config.get("user_agent", "EmbyLens/0.1.0"))
            if self.automatic
            else config.get("user_agent", "EmbyLens/0.1.0")
        )
        self.set_identity(name, agent)
        ca = (
            "/etc/ssl/cert.pem"
            if ssl.get_default_verify_paths().cafile is None
            and Path("/etc/ssl/cert.pem").is_file()
            else None
        )
        self.last_request_at = 0
        self.opener = urllib.request.build_opener(
            NoRedirect,
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=ca)),
        )
        self.token = cached.get("token")
        self.uid = cached.get("uid")
        if self.token:
            self.headers["X-Emby-Token"] = self.token

    def set_identity(self, name, agent):
        self.client_name = name
        prefix = "Emby" if name == "EmbyLens" else "MediaBrowser"
        device = "EmbyLens" if name == "EmbyLens" else "iPhone"
        version = "0.1.0" if name == "EmbyLens" else "1.0.0"
        self.headers["User-Agent"] = agent
        self.headers["X-Emby-Authorization"] = (
            f'{prefix} Client="{name}", Device="{device}", DeviceId="embylens-{self.sid}", Version="{version}"'
        )

    def request(self, path, params=None, body=None):
        wait = 0.5 - (time.monotonic() - self.last_request_at)
        if wait > 0:
            time.sleep(wait)
        self.last_request_at = time.monotonic()
        url = self.base + path + ("?" + urlencode(params) if params else "")
        req = urllib.request.Request(
            url,
            headers=self.headers,
            data=json.dumps(body).encode() if body is not None else None,
        )
        with self.opener.open(req, timeout=15) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise ValueError("response too large")
            return json.loads(raw)

    def login(self):
        self.headers.pop("X-Emby-Token", None)
        auth = self.request(
            "/Users/AuthenticateByName",
            body={"Username": self.config["username"], "Pw": self.config["password"]},
        )
        self.token = auth["AccessToken"]
        self.uid = auth["User"]["Id"]
        self.headers["X-Emby-Token"] = self.token

    def probe(self):
        if not self.token:
            self.login()
        try:
            # Verify the account token, not only HTTP availability.
            self.request("/Users/" + self.uid)
        except urllib.error.HTTPError as exc:
            if exc.code != 401:
                raise
            self.login()
            self.request("/Users/" + self.uid)
        info = self.request("/System/Info/Public")
        return info

    def items(self, kind, limit=1, start=0):
        return self.request(
            "/Users/" + self.uid + "/Items",
            {
                "Recursive": "true",
                "IncludeItemTypes": kind,
                "Limit": limit,
                "StartIndex": start,
                "Fields": "DateCreated",
                "SortBy": "DateCreated",
                "SortOrder": "Descending",
                "EnableImages": "false",
                "EnableTotalRecordCount": "true",
                "GroupItemsIntoCollections": "false",
            },
        )

    def stats(self):
        result = {}
        for kind, key in [("Movie", "movies"), ("Series", "series")]:
            result[key] = self.items(kind)["TotalRecordCount"]
        try:
            result["episodes"] = self.items("Episode")["TotalRecordCount"]
            result["total"] = result["movies"] + result["episodes"]
        except Exception as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code in (401, 403, 429):
                raise
            # Some servers disconnect on Episode-only queries but accept the union.
            total = self.items("Movie,Episode")["TotalRecordCount"]
            if type(total) is not int or total < result["movies"]:
                raise ValueError("媒体总数无效")
            result["total"] = total
            result["episodes"] = total - result["movies"]
        today = datetime.now(CST).date()
        count = 0
        recent = []
        complete = False
        unknown_date = False
        deadline = time.monotonic() + 30
        # Read at most 400 recent records; do not scan the library for an exact daily count.
        for start in range(0, 400, 200):
            if time.monotonic() > deadline:
                break
            batch = self.items("Movie,Episode", 200, start).get("Items", [])
            if start == 0:
                recent = [
                    {
                        "name": i.get("Name"),
                        "type": i.get("Type"),
                        "series": i.get("SeriesName"),
                        "added": i.get("DateCreated"),
                    }
                    for i in batch[:8]
                ]
            reached = False
            for item in batch:
                value = item.get("DateCreated")
                if not value:
                    unknown_date = True
                    continue
                try:
                    date = (
                        datetime.fromisoformat(value.replace("Z", "+00:00"))
                        .astimezone(CST)
                        .date()
                    )
                except ValueError:
                    unknown_date = True
                    continue
                if date == today:
                    count += 1
                if date < today:
                    reached = True
            if reached or len(batch) < 200:
                complete = not unknown_date
                break
        result.update(
            today=count if complete else None,
            today_complete=complete,
            recent=recent,
            day=str(today),
        )
        return result


def error_label(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return {
            401: "认证失败，保存配置后重试",
            403: "访问受限（HTTP 403）",
            404: "接口不存在",
            429: "请求频率受限",
        }.get(exc.code, f"HTTP {exc.code}")
    if isinstance(exc, (TimeoutError, urllib.error.URLError)):
        return "连接失败或超时"
    return "接口返回异常"


def failure_state(exc, previous):
    now = time.time()
    failures = previous.get("failures", 0) + 1
    status = exc.code if isinstance(exc, urllib.error.HTTPError) else None
    if status == 401:
        return {"auth_failed": True, "retry_at": None, "failures": failures}
    delay = 300 if status == 403 else min(3600, 300 * 2 ** min(failures - 1, 4))
    if status == 429:
        delay = 900
        value = exc.headers.get("Retry-After", "") if exc.headers else ""
        try:
            delay = max(60, int(value))
        except ValueError:
            try:
                delay = max(60, parsedate_to_datetime(value).timestamp() - now)
            except (ValueError, TypeError, OverflowError):
                pass
    return {"retry_at": now + delay + random.uniform(0, 30), "failures": failures}


def collect(config, old, sid):
    bases = [("主线路", config["primary_url"])] + [
        (line["name"], line["url"]) for line in secondary_lines(config)
    ]
    previous = old.get("line_control", {})

    def check(pair):
        label, base = pair
        control = previous.get(base, {})
        if control.get("auth_failed") or control.get("retry_at", 0) > time.time():
            prior = control.get(
                "line", {"label": label, "state": "error", "error": "等待重试"}
            )
            return {**prior, "label": label}, None, None, control
        client = Client(base, config, sid)
        t = time.monotonic()
        try:
            info = client.probe()
            elapsed = round((time.monotonic() - t) * 1000)
            config.setdefault("tokens", {})[base] = {
                "token": client.token,
                "uid": client.uid,
                "client_name": getattr(client, "client_name", "EmbyLens"),
                "user_agent": (
                    client.headers["User-Agent"]
                    if hasattr(client, "headers")
                    else config.get("user_agent", "EmbyLens/0.1.0")
                ),
            }
            return (
                {"label": label, "state": "healthy", "delay_ms": elapsed},
                client,
                info,
                {},
            )
        except Exception as exc:
            line = {"label": label, "state": "error", "error": error_label(exc)}
            control = {**failure_state(exc, control), "line": line}
            if control.get("auth_failed"):
                config.setdefault("tokens", {}).pop(base, None)
            return line, None, None, control

    # Serial checks avoid simultaneous login bursts against a single server.
    checks = [check(pair) for pair in bases]
    clients = [(client, info) for line, client, info, control in checks if client]
    lines = [line for line, _, _, _ in checks]
    result = {
        **old,
        "lines": lines,
        "line_control": {base: checks[i][3] for i, (_, base) in enumerate(bases)},
        "checked_at": time.time(),
        "next_check_at": time.time() + 300 + random.uniform(0, 30),
        "state": (
            "healthy" if len(clients) == len(bases) else "slow" if clients else "error"
        ),
    }
    result.pop("error", None)
    if clients:
        client, info = clients[0]
        result["name"] = info.get("ServerName") or old.get("name") or "Emby"
        result["version"] = info.get("Version")
        result["server_id"] = info.get("Id")
        if any(i.get("Id") != info.get("Id") for _, i in clients[1:]):
            result.update(state="error", error="主副线路指向不同服务器")
            return result
        due = (
            old.get("refresh_stats")
            or time.time() - old.get("stats_at", 0) > 3600
            or old.get("stats", {}).get("day") != str(datetime.now(CST).date())
        )
        if due and time.time() >= old.get("stats_retry_at", 0):
            try:
                result["stats"] = client.stats()
                result["stats_at"] = time.time()
                result.pop("stats_error", None)
                result.pop("refresh_stats", None)
                result.pop("stats_retry_at", None)
            except Exception as exc:
                result["stats_error"] = error_label(exc)
                result["stats_retry_at"] = time.time() + 3600
                if isinstance(exc, urllib.error.HTTPError) and exc.code in (
                    401,
                    403,
                    429,
                ):
                    base = next(
                        base
                        for i, (_, base) in enumerate(bases)
                        if checks[i][1] is client
                    )
                    result["line_control"][base] = {
                        **failure_state(exc, {}),
                        "line": {
                            "label": next(label for label, url in bases if url == base),
                            "state": "error",
                            "error": error_label(exc),
                        },
                    }
                    result["stats_retry_at"] = (
                        result["line_control"][base].get("retry_at")
                        or time.time() + 86400
                    )
        if result.get("stats_error") and result["state"] == "healthy":
            result["state"] = "slow"
    return result
