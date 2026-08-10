#!/usr/bin/env python3
import argparse
import base64
import datetime as dt
import hmac
import hashlib
import html
import http.client
import json
import os
import re
import secrets
import select
import signal
import socket
import ssl
import sqlite3
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from pywebpush import WebPushException, webpush
except Exception:
    WebPushException = None
    webpush = None


APP_NAME = "OpenWrt Remote Hub"
RAW_REPO_BASE = "https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main"
STATE_DIR = Path(os.environ.get("OWRT_REMOTE_STATE_DIR", "/var/lib/owrt-remote"))
DB_PATH = Path(os.environ.get("OWRT_REMOTE_DB", str(STATE_DIR / "hub.db")))
AUTH_FILE = STATE_DIR / "hub-auth.json"
SESSION_TOKEN_FILE = STATE_DIR / "hub-session.token"
SESSIONS_FILE = STATE_DIR / "hub-sessions.json"
NOTIFICATIONS_FILE = STATE_DIR / "hub-notifications.json"
PUSH_SUBSCRIPTIONS_FILE = STATE_DIR / "hub-push-subscriptions.json"
VAPID_PRIVATE_KEY_FILE = STATE_DIR / "hub-vapid-private.pem"
VAPID_PUBLIC_KEY_FILE = STATE_DIR / "hub-vapid-public.txt"
BOOT_ID_FILE = STATE_DIR / "hub-boot.id"
AGENT_TOKEN_FILE = STATE_DIR / "agent.token"
XRAY_WAN_RECONNECT_FILE = STATE_DIR / "xray-wan-reconnect-restart.json"
ACME_WEBROOT = STATE_DIR / "acme-webroot"
ONLINE_AFTER_SECONDS = int(os.environ.get("OWRT_REMOTE_ONLINE_AFTER", "75"))
DEFAULT_VLESS_PORT = int(os.environ.get("OWRT_REMOTE_VLESS_PORT", "8443"))
REQUEST_QUEUE_SIZE = int(os.environ.get("OWRT_REMOTE_REQUEST_QUEUE_SIZE", "128"))
ROUTER_PROXY_LIMIT = max(1, int(os.environ.get("OWRT_REMOTE_ROUTER_PROXY_LIMIT", "4")))
PROXY_TIMEOUT = float(os.environ.get("OWRT_REMOTE_PROXY_TIMEOUT", "25"))
STATIC_CACHE_TTL = int(os.environ.get("OWRT_REMOTE_STATIC_CACHE_TTL", "3600"))
STATIC_CACHE_MAX_BYTES = int(os.environ.get("OWRT_REMOTE_STATIC_CACHE_MAX_BYTES", str(8 * 1024 * 1024)))
PBKDF2_ITERATIONS = 240000
MIN_PASSWORD_LENGTH = 4
SESSION_COOKIE = "owrt_remote_session"
ROUTER_COOKIE = "owrt_remote_router"
SESSION_TTL_SECONDS = int(os.environ.get("OWRT_REMOTE_SESSION_TTL", str(30 * 24 * 60 * 60)))
CAPTCHA_TTL_SECONDS = 600
NOTIFICATIONS_MAX = 220
LUCI_ABSOLUTE_ROOTS = ("/ubus", "/cgi-bin/luci", "/luci-static")
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
VPS_TERMINAL_ID = "__vps__"
SSH_HTTP_SESSIONS = {}
SSH_HTTP_LOCK = threading.Lock()
ROUTER_PROXY_LOCK = threading.Lock()
ROUTER_PROXY_LIMITERS = {}
STATIC_CACHE_LOCK = threading.Lock()
NOTIFICATIONS_LOCK = threading.Lock()
PUSH_LOCK = threading.Lock()
STATIC_CACHE = {}
STATIC_CACHE_BYTES = 0
BACKUP_VERSION = 1
BACKUP_STATE_FILES = (
    AUTH_FILE,
    SESSION_TOKEN_FILE,
    SESSIONS_FILE,
    NOTIFICATIONS_FILE,
    PUSH_SUBSCRIPTIONS_FILE,
    VAPID_PRIVATE_KEY_FILE,
    VAPID_PUBLIC_KEY_FILE,
    BOOT_ID_FILE,
    AGENT_TOKEN_FILE,
    XRAY_WAN_RECONNECT_FILE,
)


def now_ts():
    return int(time.time())


def atomic_write_text(path, text, mode=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)


def iso_time(ts):
    if not ts:
        return ""
    return dt.datetime.fromtimestamp(int(ts), tz=dt.timezone.utc).isoformat()


def router_proxy_limiter(router_id):
    with ROUTER_PROXY_LOCK:
        limiter = ROUTER_PROXY_LIMITERS.get(router_id)
        if limiter is None:
            limiter = threading.BoundedSemaphore(ROUTER_PROXY_LIMIT)
            ROUTER_PROXY_LIMITERS[router_id] = limiter
        return limiter


def is_luci_static_target(target):
    return target.split("?", 1)[0].startswith("/luci-static/")


def static_cache_key(router_id, target):
    return f"{router_id}\0{target}"


def static_cache_get(key):
    global STATIC_CACHE_BYTES
    if STATIC_CACHE_TTL <= 0 or STATIC_CACHE_MAX_BYTES <= 0:
        return None
    with STATIC_CACHE_LOCK:
        item = STATIC_CACHE.get(key)
        if not item:
            return None
        if time.time() - item["ts"] > STATIC_CACHE_TTL:
            STATIC_CACHE_BYTES -= len(item["body"])
            STATIC_CACHE.pop(key, None)
            return None
        return (
            item["status"],
            item["body"],
            item["content_type"],
            list(item["headers"]),
        )


def static_cache_put(key, status, body, content_type, headers):
    global STATIC_CACHE_BYTES
    if STATIC_CACHE_TTL <= 0 or STATIC_CACHE_MAX_BYTES <= 0:
        return
    if status != 200 or len(body) > STATIC_CACHE_MAX_BYTES:
        return
    with STATIC_CACHE_LOCK:
        old = STATIC_CACHE.get(key)
        if old:
            STATIC_CACHE_BYTES -= len(old["body"])
        STATIC_CACHE[key] = {
            "ts": time.time(),
            "status": status,
            "body": body,
            "content_type": content_type,
            "headers": list(headers),
        }
        STATIC_CACHE_BYTES += len(body)
        while STATIC_CACHE_BYTES > STATIC_CACHE_MAX_BYTES and STATIC_CACHE:
            oldest_key = min(STATIC_CACHE, key=lambda item_key: STATIC_CACHE[item_key]["ts"])
            oldest = STATIC_CACHE.pop(oldest_key)
            STATIC_CACHE_BYTES -= len(oldest["body"])


def static_cache_headers(headers):
    skip = {
        "cache-control",
        "connection",
        "content-length",
        "content-type",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "set-cookie",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
    return [(key, value) for key, value in headers if key.lower() not in skip]


def ensure_state():
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def read_or_make_token(path):
    ensure_state()
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(32)
    path.write_text(token + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return token


def agent_token():
    return os.environ.get("OWRT_REMOTE_AGENT_TOKEN") or read_or_make_token(AGENT_TOKEN_FILE)


def session_token():
    return read_or_make_token(SESSION_TOKEN_FILE)


def password_digest(password, salt=None, iterations=PBKDF2_ITERATIONS):
    salt = salt or secrets.token_hex(16)
    raw = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        int(iterations),
    )
    return {
        "salt": salt,
        "hash": raw.hex(),
        "iterations": int(iterations),
    }


def write_json_private(path, data):
    ensure_state()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def save_auth(username, password):
    username = clean_username(username)
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    data = {
        "username": username,
        "password": password_digest(password),
        "updated_at": now_ts(),
    }
    write_json_private(AUTH_FILE, data)
    return data


def load_auth():
    ensure_state()
    if AUTH_FILE.exists():
        return json.loads(AUTH_FILE.read_text(encoding="utf-8"))
    username = os.environ.get("OWRT_REMOTE_ADMIN_USER", "admin")
    password = os.environ.get("OWRT_REMOTE_ADMIN_PASSWORD") or "admin"
    data = save_auth(username, password)
    login_hint = STATE_DIR / "hub-login.txt"
    login_hint.write_text(
        f"username: {data['username']}\npassword: {password}\n",
        encoding="utf-8",
    )
    try:
        os.chmod(login_hint, 0o600)
    except OSError:
        pass
    return data


def clean_username(value):
    value = (value or "").strip()
    if not (1 <= len(value) <= 64):
        raise ValueError("username length must be 1..64")
    for ch in value:
        if not (ch.isalnum() or ch in "._-@"):
            raise ValueError("username may contain only letters, digits, . _ - @")
    return value


def verify_login(username, password):
    try:
        auth = load_auth()
        stored_user = auth.get("username", "")
        stored = auth.get("password", {})
        digest = password_digest(password or "", stored.get("salt"), stored.get("iterations", PBKDF2_ITERATIONS))
    except Exception:
        return False
    return secrets.compare_digest(username or "", stored_user) and secrets.compare_digest(
        digest["hash"],
        stored.get("hash", ""),
    )


def current_username():
    try:
        return load_auth().get("username", "admin")
    except Exception:
        return "admin"


def session_hash(token):
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def load_sessions():
    ensure_state()
    if not SESSIONS_FILE.exists():
        return []
    try:
        data = json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("sessions", [])
    if not isinstance(data, list):
        return []
    now = now_ts()
    return [item for item in data if isinstance(item, dict) and int(item.get("expires_at") or 0) > now]


def save_sessions(sessions):
    write_json_private(SESSIONS_FILE, {"sessions": sessions})


def short_user_agent(value):
    value = " ".join(str(value or "").split())
    if not value:
        return "unknown"
    if len(value) > 160:
        return value[:157] + "..."
    return value


def client_label(user_agent):
    ua = (user_agent or "").lower()
    if "iphone" in ua or "ipad" in ua:
        device = "iPhone/iPad"
    elif "android" in ua:
        device = "Android"
    elif "windows" in ua:
        device = "Windows"
    elif "mac os" in ua or "macintosh" in ua:
        device = "Mac"
    elif "linux" in ua:
        device = "Linux"
    else:
        device = "Устройство"
    if "telegram" in ua:
        browser = "Telegram"
    elif "edg/" in ua:
        browser = "Edge"
    elif "chrome/" in ua and "chromium" not in ua:
        browser = "Chrome"
    elif "safari/" in ua and "chrome/" not in ua:
        browser = "Safari"
    elif "firefox/" in ua:
        browser = "Firefox"
    else:
        browser = "браузер"
    return f"{device} · {browser}"


def make_hub_session(username, ip, user_agent):
    token = secrets.token_urlsafe(36)
    ts = now_ts()
    session = {
        "id": secrets.token_hex(8),
        "token_hash": session_hash(token),
        "username": username or current_username(),
        "ip": ip or "",
        "user_agent": short_user_agent(user_agent),
        "client": client_label(user_agent),
        "created_at": ts,
        "last_seen": ts,
        "expires_at": ts + SESSION_TTL_SECONDS,
    }
    sessions = load_sessions()
    sessions.append(session)
    save_sessions(sessions[-60:])
    return token, session


def verify_hub_session(token, touch=True, ip="", user_agent=""):
    if not token:
        return None
    wanted = session_hash(token)
    sessions = load_sessions()
    changed = False
    result = None
    ts = now_ts()
    current_ip = str(ip or "").strip()
    current_user_agent = short_user_agent(user_agent or "")
    current_client = client_label(user_agent or "")
    for session in sessions:
        if secrets.compare_digest(session.get("token_hash", ""), wanted):
            result = session
            if touch:
                old_ip = str(session.get("ip") or "").strip()
                if current_ip and old_ip and current_ip != old_ip:
                    known_ips = [str(item).strip() for item in session.get("known_ips", []) if str(item).strip()]
                    for value in (old_ip, current_ip):
                        if value not in known_ips:
                            known_ips.append(value)
                    session["known_ips"] = known_ips[-10:]
                    session["ip"] = current_ip
                    session["ip_changed_at"] = ts
                    if current_user_agent:
                        session["user_agent"] = current_user_agent
                    if current_client:
                        session["client"] = current_client
                    add_notification(
                        "session-ip",
                        "Новый IP в активной сессии",
                        f"{session.get('client', 'устройство')} · {old_ip} -> {current_ip}",
                        "warn",
                        [current_user_agent] if current_user_agent else [],
                        {"session_id": session.get("id", ""), "old_ip": old_ip, "ip": current_ip},
                        dedupe_seconds=45,
                    )
                    session["last_seen"] = ts
                    session["expires_at"] = ts + SESSION_TTL_SECONDS
                    changed = True
                elif current_ip and not old_ip:
                    session["ip"] = current_ip
                    changed = True
                old_user_agent = str(session.get("user_agent") or "").strip()
                if current_user_agent and old_user_agent and current_user_agent != old_user_agent:
                    old_client = session.get("client") or client_label(old_user_agent)
                    session["user_agent"] = current_user_agent
                    session["client"] = current_client or client_label(current_user_agent)
                    add_notification(
                        "session-client",
                        "Новое устройство в активной сессии",
                        f"{old_client} -> {session.get('client', 'устройство')} · IP {current_ip or session.get('ip', 'unknown')}",
                        "warn",
                        [current_user_agent],
                        {"session_id": session.get("id", ""), "ip": current_ip or session.get("ip", "")},
                        dedupe_seconds=60,
                    )
                    changed = True
                if ts - int(session.get("last_seen") or 0) > 60:
                    session["last_seen"] = ts
                    session["expires_at"] = ts + SESSION_TTL_SECONDS
                    changed = True
                elif changed:
                    session["expires_at"] = ts + SESSION_TTL_SECONDS
            break
    if changed:
        save_sessions(sessions)
    return result


def revoke_hub_session(session_id="", token=""):
    sessions = load_sessions()
    wanted_hash = session_hash(token) if token else ""
    kept = []
    removed = 0
    for session in sessions:
        if session_id and session.get("id") == session_id:
            removed += 1
            continue
        if wanted_hash and secrets.compare_digest(session.get("token_hash", ""), wanted_hash):
            removed += 1
            continue
        kept.append(session)
    if removed:
        save_sessions(kept)
    return removed


def list_hub_sessions(current_token=""):
    current_hash = session_hash(current_token) if current_token else ""
    rows = []
    for session in sorted(load_sessions(), key=lambda item: int(item.get("last_seen") or 0), reverse=True):
        rows.append(
            {
                "id": session.get("id", ""),
                "username": session.get("username", ""),
                "ip": session.get("ip", ""),
                "client": session.get("client") or client_label(session.get("user_agent", "")),
                "user_agent": session.get("user_agent", ""),
                "created_at": int(session.get("created_at") or 0),
                "last_seen": int(session.get("last_seen") or 0),
                "expires_at": int(session.get("expires_at") or 0),
                "current": bool(current_hash and secrets.compare_digest(session.get("token_hash", ""), current_hash)),
            }
        )
    return rows


def load_notifications():
    ensure_state()
    if not NOTIFICATIONS_FILE.exists():
        return []
    try:
        data = json.loads(NOTIFICATIONS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("notifications", [])
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def save_notifications(items):
    items = sorted(items, key=lambda item: int(item.get("ts") or 0), reverse=True)[:NOTIFICATIONS_MAX]
    write_json_private(NOTIFICATIONS_FILE, {"notifications": items})


def add_notification(kind, title, body="", level="info", details=None, data=None, dedupe_seconds=0):
    ts = now_ts()
    title = str(title or "").strip()[:120]
    body = str(body or "").strip()[:500]
    details = details or []
    if isinstance(details, str):
        details = [details]
    details = [str(line).strip()[:260] for line in details if str(line).strip()][:12]
    item = {
        "id": secrets.token_hex(8),
        "kind": str(kind or "info")[:40],
        "level": str(level or "info")[:20],
        "title": title,
        "body": body,
        "details": details,
        "data": data if isinstance(data, dict) else {},
        "ts": ts,
        "iso": iso_time(ts),
    }
    with NOTIFICATIONS_LOCK:
        items = load_notifications()
        if dedupe_seconds:
            for old in items:
                if (
                    old.get("kind") == item["kind"]
                    and old.get("title") == item["title"]
                    and old.get("body") == item["body"]
                    and ts - int(old.get("ts") or 0) <= int(dedupe_seconds)
                ):
                    return old
        items.insert(0, item)
        save_notifications(items)
    queue_web_push_notification(item)
    return item


def list_notifications(after=0, limit=60):
    try:
        after = int(after or 0)
    except (TypeError, ValueError):
        after = 0
    try:
        limit = max(1, min(120, int(limit or 60)))
    except (TypeError, ValueError):
        limit = 60
    items = [item for item in load_notifications() if int(item.get("ts") or 0) > after]
    return sorted(items, key=lambda item: int(item.get("ts") or 0), reverse=True)[:limit]


def clear_notifications():
    with NOTIFICATIONS_LOCK:
        save_notifications([])


def b64url(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def load_push_subscriptions():
    ensure_state()
    if not PUSH_SUBSCRIPTIONS_FILE.exists():
        return []
    try:
        data = json.loads(PUSH_SUBSCRIPTIONS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("subscriptions", [])
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict) and item.get("endpoint")]


def save_push_subscriptions(items):
    cleaned = []
    seen = set()
    for item in items:
        endpoint = str(item.get("endpoint") or "").strip()
        keys = item.get("keys") if isinstance(item.get("keys"), dict) else {}
        if not endpoint or endpoint in seen:
            continue
        seen.add(endpoint)
        cleaned.append(
            {
                "id": item.get("id") or hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:16],
                "endpoint": endpoint,
                "keys": {
                    "p256dh": str(keys.get("p256dh") or ""),
                    "auth": str(keys.get("auth") or ""),
                },
                "client": str(item.get("client") or "браузер")[:120],
                "ip": str(item.get("ip") or "")[:80],
                "user_agent": str(item.get("user_agent") or "")[:260],
                "created_at": int(item.get("created_at") or now_ts()),
                "last_seen": int(item.get("last_seen") or now_ts()),
            }
        )
    write_json_private(PUSH_SUBSCRIPTIONS_FILE, {"subscriptions": cleaned[-80:]})


def vapid_public_key():
    ensure_state()
    if VAPID_PUBLIC_KEY_FILE.exists() and VAPID_PRIVATE_KEY_FILE.exists():
        value = VAPID_PUBLIC_KEY_FILE.read_text(encoding="utf-8").strip()
        if value:
            return value
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
    except Exception:
        return ""
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_numbers = private_key.public_key().public_numbers()
    public_raw = b"\x04" + public_numbers.x.to_bytes(32, "big") + public_numbers.y.to_bytes(32, "big")
    public_value = b64url(public_raw)
    VAPID_PRIVATE_KEY_FILE.write_bytes(private_pem)
    VAPID_PUBLIC_KEY_FILE.write_text(public_value + "\n", encoding="utf-8")
    try:
        os.chmod(VAPID_PRIVATE_KEY_FILE, 0o600)
        os.chmod(VAPID_PUBLIC_KEY_FILE, 0o600)
    except OSError:
        pass
    return public_value


def web_push_ready():
    return webpush is not None and WebPushException is not None and bool(vapid_public_key())


def save_push_subscription(subscription, username="", ip="", user_agent=""):
    if not isinstance(subscription, dict):
        raise ValueError("subscription must be object")
    endpoint = str(subscription.get("endpoint") or "").strip()
    keys = subscription.get("keys") if isinstance(subscription.get("keys"), dict) else {}
    p256dh = str(keys.get("p256dh") or "").strip()
    auth = str(keys.get("auth") or "").strip()
    if not endpoint or not p256dh or not auth:
        raise ValueError("bad push subscription")
    ts = now_ts()
    item = {
        "id": hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:16],
        "endpoint": endpoint,
        "keys": {"p256dh": p256dh, "auth": auth},
        "client": client_label(user_agent),
        "ip": ip or "",
        "user_agent": short_user_agent(user_agent),
        "username": username or current_username(),
        "created_at": ts,
        "last_seen": ts,
    }
    with PUSH_LOCK:
        items = [old for old in load_push_subscriptions() if old.get("endpoint") != endpoint]
        items.append(item)
        save_push_subscriptions(items)
    return item


def remove_push_subscription(endpoint):
    endpoint = str(endpoint or "").strip()
    if not endpoint:
        return 0
    with PUSH_LOCK:
        items = load_push_subscriptions()
        kept = [item for item in items if item.get("endpoint") != endpoint]
        if len(kept) != len(items):
            save_push_subscriptions(kept)
            return len(items) - len(kept)
    return 0


def push_payload_for_notification(item):
    return {
        "title": item.get("title") or APP_NAME,
        "body": item.get("body") or "",
        "tag": "owrt-" + str(item.get("id") or item.get("kind") or now_ts()),
        "url": "/",
        "kind": item.get("kind") or "info",
        "ts": int(item.get("ts") or now_ts()),
    }


def send_web_push(subscription, payload):
    if not web_push_ready():
        return "unavailable"
    try:
        webpush(
            subscription_info={
                "endpoint": subscription.get("endpoint"),
                "keys": subscription.get("keys") or {},
            },
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            vapid_private_key=str(VAPID_PRIVATE_KEY_FILE),
            vapid_claims={"sub": os.environ.get("OWRT_REMOTE_VAPID_SUB", "mailto:admin@localhost")},
            timeout=10,
            ttl=86400,
        )
        return "ok"
    except Exception as exc:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        if status_code in (404, 410):
            return "gone"
        return f"error:{status_code or exc.__class__.__name__}"


def queue_web_push_payload(payload, subscriptions=None):
    if subscriptions is None:
        subscriptions = load_push_subscriptions()
    if not subscriptions:
        return

    def worker():
        gone = []
        for subscription in subscriptions:
            result = send_web_push(subscription, payload)
            if result == "gone":
                gone.append(subscription.get("endpoint", ""))
        for endpoint in gone:
            remove_push_subscription(endpoint)

    threading.Thread(target=worker, daemon=True).start()


def queue_web_push_notification(item):
    if not item:
        return
    queue_web_push_payload(push_payload_for_notification(item))


def service_worker_js():
    return r"""
self.addEventListener('install', event => {
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('push', event => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = {title: 'OpenWrt Remote Hub', body: event.data ? event.data.text() : ''};
  }
  const title = data.title || 'OpenWrt Remote Hub';
  const options = {
    body: data.body || '',
    tag: data.tag || 'owrt-remote-hub',
    renotify: true,
    data: {url: data.url || '/'},
    badge: data.badge || undefined,
    icon: data.icon || undefined
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil((async () => {
    const allClients = await self.clients.matchAll({type: 'window', includeUncontrolled: true});
    for (const client of allClients) {
      if ('focus' in client) {
        await client.focus();
        if ('navigate' in client) {
          try { await client.navigate(url); } catch (e) {}
        }
        return;
      }
    }
    if (self.clients.openWindow) await self.clients.openWindow(url);
  })());
});
""".strip() + "\n"


def web_manifest_json():
    return json.dumps(
        {
            "name": "OpenWrt Remote Hub",
            "short_name": "Wrt Hub",
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "background_color": "#10081c",
            "theme_color": "#7c3aed",
            "description": "Удаленный доступ к OpenWrt через свой VPS",
            "icons": [
                {
                    "src": "/favicon.svg",
                    "sizes": "any",
                    "type": "image/svg+xml",
                    "purpose": "any maskable",
                }
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def favicon_svg():
    return """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#120a24"/>
      <stop offset="55%" stop-color="#28124a"/>
      <stop offset="100%" stop-color="#07040f"/>
    </linearGradient>
    <linearGradient id="halo" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#22d3ee"/>
      <stop offset="55%" stop-color="#7c3aed"/>
      <stop offset="100%" stop-color="#f59e0b"/>
    </linearGradient>
  </defs>
  <rect x="4" y="4" width="56" height="56" rx="16" fill="url(#bg)"/>
  <rect x="5" y="5" width="54" height="54" rx="15" fill="none" stroke="#ffffff24"/>
  <path d="M24 22c3-6 13-6 16 0" fill="none" stroke="#a5f3fc" stroke-linecap="round" stroke-width="3"/>
  <path d="M20 18c6-11 18-11 24 0" fill="none" stroke="url(#halo)" stroke-linecap="round" stroke-width="3"/>
  <rect x="16" y="25" width="32" height="18" rx="6" fill="none" stroke="#fbbf24" stroke-width="3"/>
  <circle cx="26" cy="34" r="3" fill="#22c55e"/>
  <circle cx="38" cy="34" r="3" fill="#22c55e"/>
  <rect x="22" y="47" width="20" height="4" rx="2" fill="#7c3aed"/>
</svg>
""".strip() + "\n"


def run_quiet(args, timeout=2.5):
    try:
        return subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
            check=False,
        ).stdout.strip()
    except Exception:
        return ""


def current_boot_id():
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def previous_boot_reason():
    journal = run_quiet(["journalctl", "-b", "-1", "-n", "180", "--no-pager", "-o", "short-iso"], timeout=3.0)
    last_log = run_quiet(["last", "-x", "-n", "8", "reboot", "shutdown"], timeout=2.0)
    text = "\n".join([journal, last_log]).lower()
    reason = "причина не найдена в журнале"
    if any(word in text for word in ("out of memory", "oom-killer", "killed process", "memory cgroup out of memory")):
        reason = "похоже на нехватку памяти / OOM"
    elif any(word in text for word in ("kernel panic", "panic:")):
        reason = "похоже на kernel panic"
    elif any(word in text for word in ("watchdog", "hard lockup", "soft lockup")):
        reason = "похоже на watchdog/зависание"
    elif any(word in text for word in ("power key", "poweroff", "powering off")):
        reason = "похоже на выключение питания"
    elif any(word in text for word in ("the system will reboot now", "system reboot", "reboot.target", "systemd-reboot")):
        reason = "штатная перезагрузка командой reboot/systemctl"
    elif "shutdown" in text:
        reason = "штатное выключение/shutdown"

    details = []
    for line in (journal + "\n" + last_log).splitlines():
        low = line.lower()
        if any(
            marker in low
            for marker in (
                "reboot",
                "shutdown",
                "panic",
                "oom",
                "out of memory",
                "watchdog",
                "power",
                "killed process",
                "systemd-logind",
                "systemd-reboot",
            )
        ):
            details.append(" ".join(line.split()))
        if len(details) >= 8:
            break
    return reason, details


def record_hub_start_event():
    ensure_state()
    boot_id = current_boot_id()
    previous_boot_id = ""
    try:
        previous_boot_id = BOOT_ID_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    if boot_id:
        try:
            BOOT_ID_FILE.write_text(boot_id + "\n", encoding="utf-8")
            os.chmod(BOOT_ID_FILE, 0o600)
        except OSError:
            pass

    if previous_boot_id and boot_id and previous_boot_id != boot_id:
        reason, details = previous_boot_reason()
        add_notification(
            "vps_up",
            "VPS снова онлайн",
            f"Hub запущен после перезагрузки. Причина: {reason}.",
            "warn",
            details,
            {"boot_id": boot_id},
            dedupe_seconds=60,
        )
    elif previous_boot_id:
        add_notification(
            "hub_restart",
            "Hub перезапущен",
            "Служба OpenWrt Remote Hub снова запущена.",
            "info",
            [],
            {"boot_id": boot_id},
            dedupe_seconds=45,
        )
    else:
        add_notification(
            "hub_start",
            "Hub запущен",
            "OpenWrt Remote Hub стартовал первый раз на этом VPS.",
            "info",
            [],
            {"boot_id": boot_id},
            dedupe_seconds=60,
        )


def captcha_challenge():
    code = str(secrets.randbelow(9000) + 1000)
    issued = str(now_ts())
    nonce = secrets.token_urlsafe(8)
    body = f"{issued}:{nonce}:{code}"
    sig = hmac.new(session_token().encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
    token = base64.urlsafe_b64encode(f"{body}:{sig}".encode("utf-8")).decode("ascii")
    return code, token


def verify_captcha(token, answer):
    try:
        raw = base64.urlsafe_b64decode((token or "").encode("ascii")).decode("utf-8")
        issued, nonce, code, sig = raw.split(":", 3)
        body = f"{issued}:{nonce}:{code}"
        expected = hmac.new(session_token().encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
        if not secrets.compare_digest(sig, expected):
            return False
        if now_ts() - int(issued) > CAPTCHA_TTL_SECONDS:
            return False
        return secrets.compare_digest(str(answer or "").strip(), code)
    except Exception:
        return False


def clean_router_id(value):
    value = (value or "").strip()
    out = []
    for ch in value:
        if ch.isalnum() or ch in "._-":
            out.append(ch)
        else:
            out.append("-")
    result = "".join(out).strip(".-_")
    if not result:
        raise ValueError("router id is empty")
    return result[:64]


def clean_router_name(value):
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split()).strip()
    if not text:
        raise ValueError("router name is empty")
    return text[:80]


def connect(db_path=DB_PATH):
    ensure_state()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn):
    conn.execute(
        """
        create table if not exists routers (
            id text primary key,
            name text not null,
            role text not null default 'node',
            entry_port integer not null default 0,
            vps_host text not null default '',
            vless_port integer not null default 8443,
            vless_uuid text not null,
            vless_encryption text not null default 'none',
            vless_decryption text not null default 'none',
            vless_flow text not null default '',
            reverse_tag text not null default 'reverse-in',
            public_url text not null default '',
            admin_host text not null default '127.0.0.1',
            admin_port integer not null default 80,
            ssh_entry_port integer not null default 0,
            ssh_vless_uuid text not null default '',
            ssh_reverse_tag text not null default '',
            ssh_host text not null default '127.0.0.1',
            ssh_port integer not null default 22,
            created_at integer not null,
            updated_at integer not null,
            deleted_at integer not null default 0,
            last_seen integer,
            status_json text not null default '{}'
        )
        """
    )
    ensure_column(conn, "routers", "custom_name", "text not null default ''")
    ensure_column(conn, "routers", "deleted_at", "integer not null default 0")
    ensure_column(conn, "routers", "ssh_entry_port", "integer not null default 0")
    ensure_column(conn, "routers", "ssh_vless_uuid", "text not null default ''")
    ensure_column(conn, "routers", "ssh_reverse_tag", "text not null default ''")
    ensure_column(conn, "routers", "ssh_host", "text not null default '127.0.0.1'")
    ensure_column(conn, "routers", "ssh_port", "integer not null default 22")
    conn.execute(
        """
        update routers
        set ssh_entry_port = entry_port + 1000
        where entry_port > 0 and (ssh_entry_port is null or ssh_entry_port = 0)
        """
    )
    for row in conn.execute("select id, reverse_tag, ssh_vless_uuid, ssh_reverse_tag from routers").fetchall():
        ssh_uuid = row["ssh_vless_uuid"] or str(uuid.uuid4())
        ssh_tag = row["ssh_reverse_tag"] or f"{row['reverse_tag'] or 'reverse-in'}-ssh"
        if ssh_uuid != row["ssh_vless_uuid"] or ssh_tag != row["ssh_reverse_tag"]:
            conn.execute(
                "update routers set ssh_vless_uuid = ?, ssh_reverse_tag = ? where id = ?",
                (ssh_uuid, ssh_tag, row["id"]),
            )
    conn.commit()


def ensure_column(conn, table, column, definition):
    cols = {row["name"] for row in conn.execute(f"pragma table_info({table})")}
    if column not in cols:
        conn.execute(f"alter table {table} add column {column} {definition}")


def get_router(conn, router_id):
    return conn.execute("select * from routers where id = ?", (router_id,)).fetchone()


def router_deleted(row):
    if not row:
        return False
    try:
        return int(row["deleted_at"] or 0) > 0
    except (IndexError, KeyError, TypeError, ValueError):
        return False


def get_active_router(conn, router_id):
    row = get_router(conn, router_id)
    if router_deleted(row):
        return None
    return row


def get_router_by_entry_port(conn, entry_port, exclude_id=""):
    return conn.execute(
        "select * from routers where coalesce(deleted_at, 0) = 0 and (entry_port = ? or ssh_entry_port = ?) and id != ?",
        (int(entry_port), int(entry_port), exclude_id),
    ).fetchone()


def get_router_by_any_port(conn, port, exclude_id=""):
    return conn.execute(
        "select * from routers where coalesce(deleted_at, 0) = 0 and (entry_port = ? or ssh_entry_port = ?) and id != ?",
        (int(port), int(port), exclude_id),
    ).fetchone()


def normalize_internal_ssh_port(ssh_port, entry_port=0, ssh_entry_port=0):
    try:
        port = int(ssh_port or 22)
    except (TypeError, ValueError):
        return 22
    if port <= 0 or port in {int(entry_port or 0), int(ssh_entry_port or 0)}:
        return 22
    return port


def list_router_rows(conn):
    return conn.execute(
        """
        select * from routers
        where coalesce(deleted_at, 0) = 0
        order by case role when 'main' then 0 else 1 end, lower(id)
        """
    ).fetchall()


def row_to_router(row):
    data = dict(row)
    try:
        status = json.loads(data.get("status_json") or "{}")
    except json.JSONDecodeError:
        status = {}
    custom_name = str(data.get("custom_name") or "").strip()
    if custom_name:
        data["name"] = custom_name
    last_seen = data.get("last_seen")
    service_enabled = str(status.get("service") or "").lower() != "disabled"
    online = bool(last_seen and service_enabled and now_ts() - int(last_seen) <= ONLINE_AFTER_SECONDS)
    data["status"] = status
    data["online"] = online
    data["last_seen_iso"] = iso_time(last_seen)
    data["access_url"] = f"/access/{urllib.parse.quote(data['id'])}/"
    data["ssh_url"] = f"/ssh/{urllib.parse.quote(data['id'])}/"
    data["config_url"] = f"/router/{urllib.parse.quote(data['id'])}/config"
    data["xray_client_url"] = f"/router/{urllib.parse.quote(data['id'])}/xray-client.json"
    data.pop("status_json", None)
    return data


def backup_filename():
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"owrt-remote-hub-backup-{stamp}.tar.gz"


def copy_sqlite_backup(src_path, dst_path):
    src_path = Path(src_path)
    dst_path = Path(dst_path)
    src_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    if not src_path.exists():
        conn = connect(src_path)
        try:
            init_db(conn)
        finally:
            conn.close()
    source = sqlite3.connect(str(src_path))
    dest = sqlite3.connect(str(dst_path))
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()


def write_private_bytes(path, data, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    try:
        os.chmod(tmp, mode)
    except OSError:
        pass
    os.replace(tmp, path)


def create_hub_backup(out_path, db_path=DB_PATH):
    ensure_state()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="owrt-hub-backup-") as tmp_name:
        tmp_dir = Path(tmp_name)
        state_dir = tmp_dir / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        db_copy = state_dir / "hub.db"
        copy_sqlite_backup(db_path, db_copy)
        files = ["state/hub.db"]
        for path in BACKUP_STATE_FILES:
            path = Path(path)
            if not path.exists() or not path.is_file():
                continue
            arcname = f"state/{path.name}"
            (state_dir / path.name).write_bytes(path.read_bytes())
            files.append(arcname)
        manifest = {
            "app": APP_NAME,
            "version": BACKUP_VERSION,
            "created_at": iso_time(now_ts()),
            "hostname": socket.gethostname(),
            "state_dir": str(STATE_DIR),
            "db_path": str(db_path),
            "files": files,
        }
        (tmp_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_archive = out_path.with_name(f".{out_path.name}.{os.getpid()}.tmp")
        with tarfile.open(tmp_archive, "w:gz") as tar:
            tar.add(tmp_dir / "manifest.json", arcname="manifest.json")
            for item in state_dir.iterdir():
                if item.is_file():
                    tar.add(item, arcname=f"state/{item.name}")
        try:
            os.chmod(tmp_archive, 0o600)
        except OSError:
            pass
        os.replace(tmp_archive, out_path)
    return {"path": str(out_path), "filename": out_path.name, "files": files}


def safe_tar_member_name(name):
    value = str(name or "").replace("\\", "/")
    if not value or value.startswith("/") or ":" in value:
        return False
    parts = value.split("/")
    return all(part and part not in {".", ".."} for part in parts)


def extract_hub_backup(archive_path, tmp_dir):
    with tarfile.open(archive_path, "r:gz") as tar:
        members = tar.getmembers()
        for member in members:
            if not safe_tar_member_name(member.name):
                raise ValueError(f"unsafe backup member: {member.name}")
            if not (member.isfile() or member.isdir()):
                raise ValueError(f"unsupported backup member: {member.name}")
        tar.extractall(tmp_dir, members)


def restore_hub_backup(archive_path, db_path=DB_PATH, vps_host="", public_url=""):
    ensure_state()
    archive_path = Path(archive_path)
    restored_files = []
    warnings = []
    with tempfile.TemporaryDirectory(prefix="owrt-hub-restore-") as tmp_name:
        tmp_dir = Path(tmp_name)
        extract_hub_backup(archive_path, tmp_dir)
        manifest_path = tmp_dir / "manifest.json"
        if not manifest_path.exists():
            raise ValueError("backup manifest.json not found")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if int(manifest.get("version") or 0) > BACKUP_VERSION:
            raise ValueError(f"backup version {manifest.get('version')} is newer than this Hub")
        state_dir = tmp_dir / "state"
        db_src = state_dir / "hub.db"
        if not db_src.exists():
            raise ValueError("backup has no state/hub.db")
        db_path = Path(db_path)
        if db_path.exists():
            old = db_path.with_name(f"{db_path.name}.before-restore-{now_ts()}")
            write_private_bytes(old, db_path.read_bytes())
            warnings.append(f"old DB copy saved: {old}")
        write_private_bytes(db_path, db_src.read_bytes())
        restored_files.append(str(db_path))
        for target in BACKUP_STATE_FILES:
            src = state_dir / Path(target).name
            if not src.exists():
                continue
            write_private_bytes(target, src.read_bytes())
            restored_files.append(str(target))
    rewritten = {}
    conn = connect(db_path)
    try:
        init_db(conn)
        if vps_host:
            conn.execute("update routers set vps_host = ?, updated_at = ?", (str(vps_host).strip(), now_ts()))
            rewritten["vps_host"] = str(vps_host).strip()
        if public_url:
            conn.execute("update routers set public_url = ?, updated_at = ?", (str(public_url).strip(), now_ts()))
            rewritten["public_url"] = str(public_url).strip()
        conn.commit()
    finally:
        conn.close()
    xray = None
    try:
        xray = reload_vps_xray(db_path)
    except Exception as exc:
        warnings.append(f"xray reload skipped: {exc}")
    if os.environ.get("OWRT_REMOTE_AGENT_TOKEN"):
        warnings.append("OWRT_REMOTE_AGENT_TOKEN is set in environment, restored agent.token file may be ignored")
    return {
        "manifest": manifest,
        "restored_files": restored_files,
        "rewritten": rewritten,
        "xray": xray,
        "warnings": warnings,
        "restart_required": True,
    }


def upsert_router(conn, values):
    router_id = clean_router_id(values.get("id"))
    current = get_router(conn, router_id)
    ts = now_ts()

    def keep_str(key, default=""):
        value = values.get(key)
        if value not in (None, ""):
            return value
        if current:
            return current[key]
        return default

    def keep_int(key, default=0):
        value = values.get(key)
        if value not in (None, ""):
            return int(value)
        if current:
            return int(current[key] or default)
        return default

    reverse_tag = keep_str("reverse_tag", "reverse-in")
    ssh_vless_uuid = values.get("ssh_vless_uuid") or (current["ssh_vless_uuid"] if current and current["ssh_vless_uuid"] else str(uuid.uuid4()))
    ssh_reverse_tag = values.get("ssh_reverse_tag") or (current["ssh_reverse_tag"] if current and current["ssh_reverse_tag"] else f"{reverse_tag}-ssh")

    payload = {
        "id": router_id,
        "name": values.get("name") or router_id,
        "role": values.get("role") or "node",
        "entry_port": keep_int("entry_port", 0),
        "vps_host": keep_str("vps_host", ""),
        "vless_port": keep_int("vless_port", DEFAULT_VLESS_PORT),
        "vless_uuid": keep_str("vless_uuid", str(uuid.uuid4())),
        "vless_encryption": keep_str("vless_encryption", "none"),
        "vless_decryption": keep_str("vless_decryption", "none"),
        "vless_flow": keep_str("vless_flow", ""),
        "reverse_tag": reverse_tag,
        "public_url": keep_str("public_url", ""),
        "admin_host": keep_str("admin_host", "127.0.0.1"),
        "admin_port": keep_int("admin_port", 80),
        "ssh_entry_port": keep_int("ssh_entry_port", int(values.get("entry_port") or 0) + 1000 if values.get("entry_port") else 0),
        "ssh_vless_uuid": ssh_vless_uuid,
        "ssh_reverse_tag": ssh_reverse_tag,
        "ssh_host": keep_str("ssh_host", "127.0.0.1"),
        "ssh_port": keep_int("ssh_port", 22),
        "updated_at": ts,
        "deleted_at": 0,
    }
    payload["ssh_port"] = normalize_internal_ssh_port(
        payload["ssh_port"],
        payload["entry_port"],
        payload["ssh_entry_port"],
    )
    if current:
        conn.execute(
            """
            update routers set
                name = :name,
                role = :role,
                entry_port = :entry_port,
                vps_host = :vps_host,
                vless_port = :vless_port,
                vless_uuid = :vless_uuid,
                vless_encryption = :vless_encryption,
                vless_decryption = :vless_decryption,
                vless_flow = :vless_flow,
                reverse_tag = :reverse_tag,
                public_url = :public_url,
                admin_host = :admin_host,
                admin_port = :admin_port,
                ssh_entry_port = :ssh_entry_port,
                ssh_vless_uuid = :ssh_vless_uuid,
                ssh_reverse_tag = :ssh_reverse_tag,
                ssh_host = :ssh_host,
                ssh_port = :ssh_port,
                deleted_at = :deleted_at,
                updated_at = :updated_at
            where id = :id
            """,
            payload,
        )
    else:
        payload["created_at"] = ts
        conn.execute(
            """
            insert into routers (
                id, name, role, entry_port, vps_host, vless_port, vless_uuid,
                vless_encryption, vless_decryption, vless_flow, reverse_tag,
                public_url, admin_host, admin_port, ssh_entry_port, ssh_vless_uuid,
                ssh_reverse_tag, ssh_host, ssh_port,
                created_at, updated_at, deleted_at
            ) values (
                :id, :name, :role, :entry_port, :vps_host, :vless_port, :vless_uuid,
                :vless_encryption, :vless_decryption, :vless_flow, :reverse_tag,
                :public_url, :admin_host, :admin_port, :ssh_entry_port, :ssh_vless_uuid,
                :ssh_reverse_tag, :ssh_host, :ssh_port,
                :created_at, :updated_at, :deleted_at
            )
            """,
            payload,
        )
    conn.commit()
    return get_router(conn, router_id)


def heartbeat(conn, payload):
    router_id = clean_router_id(payload.get("id"))
    row = get_router(conn, router_id)
    ts = now_ts()
    status_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    was_deleted = router_deleted(row)
    old_entry_port = int(row["entry_port"] or 0) if row else 0
    old_ssh_entry_port = int(row["ssh_entry_port"] or 0) if row else 0
    created_with_xray_ports = False
    if row:
        entry_port = int(row["entry_port"] or 0)
        ssh_entry_port = int(row["ssh_entry_port"] or 0)
    else:
        entry_port = int(payload.get("entry_port") or 0) if str(payload.get("entry_port", "")).isdigit() else 0
        ssh_entry_port = 0
    ssh_port = normalize_internal_ssh_port(payload.get("ssh_port") or 22, entry_port, ssh_entry_port)
    if not row:
        row = upsert_router(
            conn,
            {
                "id": router_id,
                "name": payload.get("name") or router_id,
                "role": payload.get("role") or "node",
                "entry_port": payload.get("entry_port") or 0,
                "vps_host": payload.get("vps_host") or "",
                "admin_host": payload.get("admin_host") or "127.0.0.1",
                "admin_port": payload.get("admin_port") or 80,
                "ssh_host": payload.get("ssh_host") or "127.0.0.1",
                "ssh_port": ssh_port,
            },
        )
        created_with_xray_ports = bool(int(row["entry_port"] or 0) or int(row["ssh_entry_port"] or 0))
        ssh_entry_port = int(row["ssh_entry_port"] or 0)
        ssh_port = normalize_internal_ssh_port(payload.get("ssh_port") or 22, int(row["entry_port"] or 0), ssh_entry_port)
    conn.execute(
        """
        update routers set
            name = case
                when nullif(custom_name, '') is not null then custom_name
                else coalesce(nullif(?, ''), name)
            end,
            role = coalesce(nullif(?, ''), role),
            public_url = coalesce(nullif(?, ''), public_url),
            admin_host = coalesce(nullif(?, ''), admin_host),
            admin_port = coalesce(?, admin_port),
            ssh_host = coalesce(nullif(?, ''), ssh_host),
            ssh_port = coalesce(?, ssh_port),
            last_seen = ?,
            status_json = ?,
            updated_at = ?,
            deleted_at = 0
        where id = ?
        """,
        (
            payload.get("name") or "",
            payload.get("role") or "",
            payload.get("public_url") or "",
            payload.get("admin_host") or "",
            int(payload["admin_port"]) if str(payload.get("admin_port", "")).isdigit() else None,
            payload.get("ssh_host") or "",
            ssh_port,
            ts,
            status_json,
            ts,
            router_id,
        ),
    )
    conn.commit()
    router = row_to_router(get_router(conn, router_id))
    entry_now = int(router.get("entry_port") or 0)
    ssh_entry_now = int(router.get("ssh_entry_port") or 0)
    router["_xray_reload_required"] = bool(
        (entry_now or ssh_entry_now)
        and (
            was_deleted
            or created_with_xray_ports
            or old_entry_port != entry_now
            or old_ssh_entry_port != ssh_entry_now
        )
    )
    return router


def rename_router(conn, router_id, name):
    router_id = clean_router_id(router_id)
    clean_name = clean_router_name(name)
    row = get_router(conn, router_id)
    if not row:
        raise ValueError("router not found")
    conn.execute(
        "update routers set name = ?, custom_name = ?, updated_at = ? where id = ?",
        (clean_name, clean_name, now_ts(), router_id),
    )
    conn.commit()
    return get_router(conn, router_id)


def make_server_xray_config(rows, listen_host="0.0.0.0", listen_port=DEFAULT_VLESS_PORT, decryption="none"):
    clients = []
    inbounds = [
        {
            "tag": "owrt-remote-vless",
            "listen": listen_host,
            "port": int(listen_port),
            "protocol": "vless",
            "settings": {
                "clients": clients,
                "decryption": decryption,
            },
            "streamSettings": {
                "network": "tcp",
                "security": "none",
            },
        }
    ]
    rules = []

    for row in rows:
        router_id = clean_router_id(row["id"])
        entry_port = int(row["entry_port"] or 0)
        if entry_port <= 0:
            continue
        ssh_entry_port = int(row["ssh_entry_port"] or 0)
        reverse_out = f"reverse-{router_id}"
        ssh_reverse_out = f"{reverse_out}-ssh"
        portal_in = f"entry-{router_id}"
        client = {
            "id": row["vless_uuid"],
            "email": f"{router_id}@owrt-remote",
            "reverse": {
                "tag": reverse_out,
            },
        }
        if row["vless_flow"]:
            client["flow"] = row["vless_flow"]
        clients.append(client)
        if ssh_entry_port > 0:
            ssh_client = {
                "id": row["ssh_vless_uuid"],
                "email": f"{router_id}-ssh@owrt-remote",
                "reverse": {
                    "tag": ssh_reverse_out,
                },
            }
            if row["vless_flow"]:
                ssh_client["flow"] = row["vless_flow"]
            clients.append(ssh_client)
        inbounds.append(
            {
                "tag": portal_in,
                "listen": "127.0.0.1",
                "port": entry_port,
                "protocol": "tunnel",
                "settings": {
                    "allowedNetwork": "tcp",
                    "rewriteAddress": row["admin_host"],
                    "rewritePort": int(row["admin_port"]),
                },
            }
        )
        rules.append(
            {
                "type": "field",
                "inboundTag": [portal_in],
                "outboundTag": reverse_out,
            }
        )
        if ssh_entry_port > 0:
            ssh_in = f"ssh-{router_id}"
            inbounds.append(
                {
                    "tag": ssh_in,
                    "listen": "127.0.0.1",
                    "port": ssh_entry_port,
                    "protocol": "tunnel",
                    "settings": {
                        "allowedNetwork": "tcp",
                        "rewriteAddress": row["ssh_host"] or "127.0.0.1",
                        "rewritePort": int(row["ssh_port"] or 22),
                    },
                }
            )
            rules.append(
                {
                    "type": "field",
                    "inboundTag": [ssh_in],
                    "outboundTag": ssh_reverse_out,
                }
            )

    return {
        "log": {"loglevel": "warning"},
        "inbounds": inbounds,
        "outbounds": [{"tag": "direct", "protocol": "freedom"}],
        "routing": {"rules": rules},
        "remarks": "OpenWrt Remote Hub server config",
    }


def make_router_xray_config(row):
    bridge_tag = row["reverse_tag"]
    ssh_bridge_tag = row["ssh_reverse_tag"] or f"{bridge_tag}-ssh"
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [],
        "outbounds": [
            {
                "tag": "router-admin",
                "protocol": "freedom",
                "settings": {
                    "redirect": f"{row['admin_host']}:{int(row['admin_port'])}",
                },
            },
            {
                "tag": "router-ssh",
                "protocol": "freedom",
                "settings": {
                    "redirect": f"{row['ssh_host'] or '127.0.0.1'}:{int(row['ssh_port'] or 22)}",
                },
            },
            {
                "tag": "vps-interconn",
                "protocol": "vless",
                "settings": {
                    "address": row["vps_host"],
                    "port": int(row["vless_port"]),
                    "id": row["vless_uuid"],
                    "encryption": row["vless_encryption"],
                    "flow": row["vless_flow"],
                    "reverse": {"tag": bridge_tag},
                },
                "streamSettings": {"network": "tcp", "security": "none"},
            },
            {
                "tag": "vps-ssh-interconn",
                "protocol": "vless",
                "settings": {
                    "address": row["vps_host"],
                    "port": int(row["vless_port"]),
                    "id": row["ssh_vless_uuid"],
                    "encryption": row["vless_encryption"],
                    "flow": row["vless_flow"],
                    "reverse": {"tag": ssh_bridge_tag},
                },
                "streamSettings": {"network": "tcp", "security": "none"},
            },
        ],
        "routing": {
            "rules": [
                {
                    "type": "field",
                    "inboundTag": [bridge_tag],
                    "outboundTag": "router-admin",
                },
                {
                    "type": "field",
                    "inboundTag": [ssh_bridge_tag],
                    "outboundTag": "router-ssh",
                }
            ]
        },
        "remarks": f"OpenWrt Remote client for {row['id']}",
    }


def reload_vps_xray(db_path=DB_PATH):
    out = Path(os.environ.get("OWRT_REMOTE_XRAY_CONFIG", "/etc/xray/owrt-remote.json"))
    service = os.environ.get("OWRT_REMOTE_XRAY_SERVICE", "owrt-remote-xray")
    conn = connect(db_path)
    try:
        init_db(conn)
        rows = list_router_rows(conn)
    finally:
        conn.close()
    config = make_server_xray_config(rows)
    atomic_write_text(out, json.dumps(config, ensure_ascii=False, indent=2) + "\n", mode=0o600)
    try:
        os.chmod(out, 0o600)
    except OSError:
        pass
    result = subprocess.run(
        ["systemctl", "restart", service],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"systemctl restart {service} failed: {detail}")
    return {"config": str(out), "service": service, "routers": len(rows)}


def restart_vps_xray():
    service = os.environ.get("OWRT_REMOTE_XRAY_SERVICE", "owrt-remote-xray")
    result = subprocess.run(
        ["systemctl", "restart", service],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"systemctl restart {service} failed: {detail}")
    return {"service": service}


def active_ssh_http_sessions(router_id=None):
    with SSH_HTTP_LOCK:
        sessions = list(SSH_HTTP_SESSIONS.values())
    count = 0
    for session in sessions:
        if is_vps_terminal_id(session.get("router_id")):
            continue
        if router_id and str(session.get("router_id") or "") != str(router_id):
            continue
        with session.get("lock", threading.Lock()):
            alive = bool(session.get("alive"))
        if alive:
            count += 1
    return count


def maybe_restart_vps_xray_after_wan_reconnect(router_id):
    if str(os.environ.get("OWRT_REMOTE_RESTART_XRAY_ON_WAN_RECONNECT", "1")).lower() in {"0", "no", "false", "off"}:
        return {"skipped": "disabled"}
    active_sessions = active_ssh_http_sessions(router_id)
    if active_sessions:
        return {"skipped": "active-ssh-terminal", "active_sessions": active_sessions}
    cooldown = int(os.environ.get("OWRT_REMOTE_WAN_RECONNECT_XRAY_COOLDOWN", "90"))
    now = now_ts()
    last = 0
    try:
        data = json.loads(XRAY_WAN_RECONNECT_FILE.read_text(encoding="utf-8"))
        last = int(data.get("ts") or 0)
    except Exception:
        pass
    if now - last < cooldown:
        return {"skipped": "cooldown", "remaining": cooldown - (now - last)}
    result = restart_vps_xray()
    XRAY_WAN_RECONNECT_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        XRAY_WAN_RECONNECT_FILE,
        json.dumps({"ts": now, "router_id": router_id, "result": result}, ensure_ascii=False) + "\n",
        mode=0o600,
    )
    result["reason"] = "wan-reconnect"
    return result


def make_openwrt_config(row, hub_url):
    lines = [
        "uci -q delete owrtremote.main",
        "uci set owrtremote.main=remote",
        "uci set owrtremote.main.enabled='1'",
        f"uci set owrtremote.main.router_id='{sh_quote(row['id'])}'",
        f"uci set owrtremote.main.router_name='{sh_quote(row['name'])}'",
        f"uci set owrtremote.main.role='{sh_quote(row['role'])}'",
        f"uci set owrtremote.main.hub_url='{sh_quote(hub_url)}'",
        f"uci set owrtremote.main.hub_token='{sh_quote(agent_token())}'",
        "uci set owrtremote.main.heartbeat_interval='30'",
        "uci set owrtremote.main.xray_bin='/usr/bin/xray'",
        "uci set owrtremote.main.xray_config='/etc/xray/owrt-remote-client.json'",
        f"uci set owrtremote.main.vps_host='{sh_quote(row['vps_host'])}'",
        f"uci set owrtremote.main.vps_port='{int(row['vless_port'])}'",
        f"uci set owrtremote.main.vless_uuid='{sh_quote(row['vless_uuid'])}'",
        f"uci set owrtremote.main.vless_encryption='{sh_quote(row['vless_encryption'])}'",
        f"uci set owrtremote.main.vless_flow='{sh_quote(row['vless_flow'])}'",
        f"uci set owrtremote.main.reverse_tag='{sh_quote(row['reverse_tag'])}'",
        f"uci set owrtremote.main.ssh_vless_uuid='{sh_quote(row['ssh_vless_uuid'])}'",
        f"uci set owrtremote.main.ssh_reverse_tag='{sh_quote(row['ssh_reverse_tag'] or (row['reverse_tag'] + '-ssh'))}'",
        f"uci set owrtremote.main.admin_host='{sh_quote(row['admin_host'])}'",
        f"uci set owrtremote.main.admin_port='{int(row['admin_port'])}'",
        f"uci set owrtremote.main.ssh_host='{sh_quote(row['ssh_host'] or '127.0.0.1')}'",
        f"uci set owrtremote.main.ssh_port='{int(row['ssh_port'] or 22)}'",
        f"uci set owrtremote.main.public_url='{sh_quote(row['public_url'])}'",
        "uci commit owrtremote",
        "owrt-remote render-client",
        "/etc/init.d/owrt-remote enable",
        "/etc/init.d/owrt-remote restart",
        "owrt-remote heartbeat",
    ]
    return "\n".join(lines) + "\n"


def sh_quote(value):
    return str(value).replace("'", "'\"'\"'")


def parse_cookies(header):
    cookies = {}
    for chunk in (header or "").split(";"):
        if "=" in chunk:
            key, value = chunk.split("=", 1)
            cookies[key.strip()] = value.strip()
    return cookies


def clean_forward_cookie(cookie_header):
    parts = []
    for chunk in (cookie_header or "").split(";"):
        if not chunk.strip():
            continue
        if (
            chunk.strip().startswith("owrt_remote_admin=")
            or chunk.strip().startswith(f"{SESSION_COOKIE}=")
            or chunk.strip().startswith(f"{ROUTER_COOKIE}=")
        ):
            continue
        parts.append(chunk.strip())
    return "; ".join(parts)


def current_router_cookie(router_id):
    return f"{ROUTER_COOKIE}={urllib.parse.quote(router_id)}; HttpOnly; SameSite=Lax; Path=/"


def is_vps_terminal_id(router_id):
    return router_id == VPS_TERMINAL_ID


def vps_terminal_row():
    return {"id": VPS_TERMINAL_ID, "name": "VPS"}


def vps_terminal_commands(host):
    host = (host or "").strip() or "YOUR_VPS_IP"
    return [
        {
            "title": "Обновить Hub",
            "note": "Свежий hub.py из main; обновление уходит в отдельный systemd-run",
            "command": f'u=owrt-remote-selfupdate-$(date +%s); systemd-run --unit=\"$u\" --collect /bin/sh -lc \'v=$(date +%s); curl -fsSL -o /opt/owrt-remote/owrt-remote-hub.py \"{RAW_REPO_BASE}/vps/owrt-remote-hub.py?v=$v\" && chmod +x /opt/owrt-remote/owrt-remote-hub.py && systemctl restart owrt-remote\' && echo \"$u started; reopen VPS terminal in 3-5 sec\"',
        },
        {
            "title": "Статус Hub",
            "note": "Проверка сервиса owrt-remote",
            "command": "systemctl status owrt-remote --no-pager -l",
        },
        {
            "title": "Логи Hub",
            "note": "Последние строки journald по Hub",
            "command": "journalctl -u owrt-remote -n 80 --no-pager -l",
        },
        {
            "title": "Проверка портов",
            "note": "Слушают ли 80/443/8088/8443",
            "command": "ss -lntp | grep -E ':(80|443|8088|8443)'",
        },
        {
            "title": "Health Hub",
            "note": "Локальная health-проверка Hub",
            "command": "curl -sS http://127.0.0.1:8088/health",
        },
        {
            "title": "Включить HTTPS",
            "note": "Обновить https-конфиг из репо для текущего host",
            "command": f'curl -fsSL "{RAW_REPO_BASE}/vps/enable-https.sh?v=$(date +%s)" | sh -s -- {host}',
        },
        {
            "title": "Установить VPS заново",
            "note": "install-vps.sh из репо",
            "command": f'curl -fsSL "{RAW_REPO_BASE}/vps/install-vps.sh?v=$(date +%s)" | sh',
        },
    ]


def vps_quick_commands_html(host):
    cards = []
    for item in vps_terminal_commands(host):
        title = html.escape(item["title"])
        note = html.escape(item["note"])
        command = item["command"]
        safe_cmd = html.escape(command, quote=True)
        preview = html.escape(command)
        cards.append(
            f"""
      <article class="cmdCard">
        <div class="cmdHead">
          <strong>{title}</strong>
          <span>{note}</span>
        </div>
        <pre class="cmdBody">{preview}</pre>
        <div class="cmdActions">
          <button class="cmdBtn js-copy-cmd" type="button" data-cmd="{safe_cmd}">Копировать</button>
          <button class="cmdBtn run js-run-cmd" type="button" data-cmd="{safe_cmd}">В терминал</button>
        </div>
      </article>""".strip()
        )
    joined = "\n".join(cards)
    return f"""
  <section class="quickPanel">
    <div class="quickHead">
      <h2>Быстрые команды</h2>
      <p>Команды взяты из репозитория luci-app-owrt-remote. Можно копировать или сразу отправлять в VPS terminal.</p>
    </div>
    <div class="quickGrid">
{joined}
    </div>
  </section>""".strip()


def ssh_ws_token(secret, router_id):
    return hmac.new(
        secret.encode("utf-8"),
        f"ssh:{router_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def strip_access_prefix(path, router_id):
    prefix = f"/access/{urllib.parse.quote(router_id)}"
    if path == prefix:
        return "/"
    if path.startswith(prefix + "/"):
        return path[len(prefix):] or "/"
    return path or "/"


def rewrite_forward_url(value, router_id, port):
    if not value:
        return value
    parsed = urllib.parse.urlsplit(value)
    path = strip_access_prefix(parsed.path, router_id)
    return urllib.parse.urlunsplit(("http", f"127.0.0.1:{port}", path, parsed.query, parsed.fragment))


def ws_accept_value(key):
    raw = hashlib.sha1((key + WS_GUID).encode("ascii")).digest()
    return base64.b64encode(raw).decode("ascii")


def recv_exact(sock, size):
    chunks = []
    left = size
    while left > 0:
        chunk = sock.recv(left)
        if not chunk:
            raise ConnectionError("websocket closed")
        chunks.append(chunk)
        left -= len(chunk)
    return b"".join(chunks)


def ws_read_frame(sock):
    head = recv_exact(sock, 2)
    first, second = head[0], head[1]
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", recv_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", recv_exact(sock, 8))[0]
    mask = recv_exact(sock, 4) if masked else b""
    payload = recv_exact(sock, length) if length else b""
    if masked and payload:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, payload


def ws_send_frame(sock, payload, opcode=1):
    if isinstance(payload, str):
        payload = payload.encode("utf-8", errors="replace")
    length = len(payload)
    if length < 126:
        head = struct.pack("!BB", 0x80 | opcode, length)
    elif length < 65536:
        head = struct.pack("!BBH", 0x80 | opcode, 126, length)
    else:
        head = struct.pack("!BBQ", 0x80 | opcode, 127, length)
    sock.sendall(head + payload)


def set_pty_size(fd, rows, cols):
    try:
        rows = max(8, min(80, int(rows or 24)))
        cols = max(20, min(260, int(cols or 80)))
    except Exception:
        return False
    try:
        import fcntl
        import termios

        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        return True
    except Exception:
        return False


def write_pty_all(fd, data, timeout=10.0, chunk_size=1024):
    if isinstance(data, str):
        data = data.encode("utf-8", errors="replace")
    view = memoryview(data or b"")
    total = 0
    deadline = time.monotonic() + timeout
    while total < len(view):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("terminal input write timed out")
        _, writable, _ = select.select([], [fd], [], min(1.0, remaining))
        if fd not in writable:
            continue
        written = os.write(fd, view[total : total + chunk_size])
        if written <= 0:
            raise OSError("terminal input write returned 0 bytes")
        total += written
    return total


def parse_resize_payload(payload):
    try:
        message = json.loads(payload.decode("utf-8", errors="ignore"))
    except Exception:
        return None
    if not isinstance(message, dict) or message.get("type") != "resize":
        return None
    return message.get("rows"), message.get("cols")


def dashboard_html(routers, username, sessions=None, notifications=None):
    routers_json = json.dumps(routers, ensure_ascii=False)
    sessions_json = json.dumps(sessions or [], ensure_ascii=False)
    notifications_json = json.dumps(notifications or [], ensure_ascii=False)
    safe_username = html.escape(username, quote=True)
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#7c3aed">
<title>{APP_NAME}</title>
<style>
:root{{color-scheme:dark;--bg:#07040f;--panel:rgba(19,14,32,.88);--panel2:rgba(255,255,255,.07);--text:#f7f2ff;--muted:#b9adc9;--line:rgba(169,126,255,.25);--blue:#7c3aed;--green:#22c55e;--red:#fb7185;--amber:#f59e0b;--cyan:#22d3ee;--teal:#a855f7;--grid:rgba(168,85,247,.14)}}
*{{box-sizing:border-box}}body{{position:relative;min-height:100vh;margin:0;overflow-x:hidden;background-color:var(--bg);background-image:radial-gradient(circle at 12% 8%,rgba(168,85,247,.46),transparent 31%),radial-gradient(circle at 82% 12%,rgba(79,70,229,.38),transparent 30%),radial-gradient(circle at 50% 105%,rgba(236,72,153,.26),transparent 35%),linear-gradient(145deg,#07040f,#120a24 48%,#05030a),repeating-linear-gradient(0deg,transparent 0 30px,var(--grid) 31px),repeating-linear-gradient(90deg,transparent 0 30px,var(--grid) 31px);background-size:130% 130%,140% 140%,135% 135%,100% 100%,31px 31px,31px 31px;background-attachment:fixed;color:var(--text);font:14px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;animation:bgFlow 28s ease-in-out infinite alternate}}
body::before{{content:"";position:fixed;inset:-25%;z-index:0;pointer-events:none;background:conic-gradient(from 0deg at 50% 50%,rgba(168,85,247,.05),rgba(236,72,153,.34),rgba(59,130,246,.22),rgba(245,158,11,.13),rgba(168,85,247,.05));filter:blur(54px);opacity:.7;animation:auraSpin 38s linear infinite}}
@keyframes bgFlow{{0%{{background-position:0% 0%,100% 0%,50% 100%,0 0,0 0,0 0}}50%{{background-position:28% 18%,62% 26%,38% 82%,0 0,15px 24px,24px 15px}}100%{{background-position:48% 28%,42% 42%,74% 62%,0 0,30px 0,0 30px}}}}
@keyframes auraSpin{{from{{transform:rotate(0deg) scale(1)}}to{{transform:rotate(360deg) scale(1.08)}}}}
.wrap{{position:relative;z-index:1;max-width:1220px;margin:0 auto;padding:22px}}.top{{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;border-bottom:1px solid var(--line);padding:20px 0 18px}}
.brand{{display:flex;align-items:center;gap:14px}}
h1{{margin:0;font-size:29px;line-height:1.2;letter-spacing:0}}.appBanner{{position:relative;display:inline-flex;align-items:center;justify-content:center;gap:8px;min-height:36px;min-width:132px;padding:8px 14px;border:1px solid rgba(34,211,238,.38);border-radius:999px;background:linear-gradient(110deg,rgba(34,211,238,.14),rgba(124,58,237,.24),rgba(236,72,153,.14));color:#f3e8ff;text-decoration:none;font-weight:800;font-size:13px;line-height:1;white-space:nowrap;box-shadow:0 10px 24px rgba(124,58,237,.16),inset 0 1px 0 rgba(255,255,255,.10);overflow:hidden}}.appBanner::before{{content:"";position:absolute;inset:0;background:linear-gradient(90deg,transparent,rgba(255,255,255,.20),transparent);transform:translateX(-120%);animation:bannerShine 6.2s ease-in-out infinite}}.appBanner span{{position:relative}}.muted{{color:var(--muted)}}.top p{{margin:4px 0 0}}.links,.headerActions{{display:flex;align-items:center;gap:8px}}.links{{margin-top:8px;flex-wrap:wrap}}.links a,.badge{{position:relative;display:inline-flex;align-items:center;justify-content:center;gap:8px;min-height:36px;min-width:132px;padding:8px 14px;border:1px solid var(--line);border-radius:999px;background:rgba(255,255,255,.08);color:#f3e8ff;text-decoration:none;font-weight:800;font-size:13px;line-height:1;white-space:nowrap;overflow:hidden}}.headerActions{{position:relative;align-self:flex-end;justify-content:flex-end;align-content:flex-end;flex:1 1 auto;min-width:0;flex-wrap:nowrap;gap:6px;padding-top:42px;max-width:none}}.headerActions .badge,.headerActions .btn{{min-height:36px;min-width:132px;padding:8px 12px;border-radius:999px;font-weight:800;font-size:13px;line-height:1;white-space:nowrap}}.headerActions .btn[href="/logout"]{{margin-left:0}}.badge{{background:rgba(255,255,255,.08);color:#f3e8ff;box-shadow:inset 0 1px 0 rgba(255,255,255,.06)}}.nethavenTop{{border-color:rgba(34,211,238,.46);background:linear-gradient(110deg,rgba(14,165,233,.20),rgba(168,85,247,.22),rgba(34,197,94,.14));color:#ecfeff;box-shadow:0 10px 24px rgba(14,165,233,.14),inset 0 1px 0 rgba(255,255,255,.10)}}.nethavenTop::before{{content:"";position:absolute;inset:0;background:linear-gradient(90deg,transparent,rgba(255,255,255,.24),transparent);transform:translateX(-120%);animation:bannerShine 6.2s ease-in-out infinite;pointer-events:none}}.authToggle{{cursor:pointer}}.dot{{width:9px;height:9px;border-radius:999px;background:var(--red);box-shadow:0 0 13px rgba(251,113,133,.72)}}.dot.on{{background:var(--green);box-shadow:0 0 13px rgba(34,197,94,.75)}}.dot.warn{{background:var(--amber);box-shadow:0 0 13px rgba(245,158,11,.75)}}
 .toolbar{{display:grid;grid-template-columns:1fr 1fr 110px 110px 150px auto;gap:10px;margin:18px 0;padding:14px;background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.045)),var(--panel);border:1px solid var(--line);border-radius:8px;box-shadow:0 18px 46px rgba(0,0,0,.20);backdrop-filter:blur(10px)}}.toolbar input,.toolbar select{{background:rgba(255,255,255,.08);border-color:var(--line);color:#f3e8ff;box-shadow:inset 0 1px 0 rgba(255,255,255,.05)}}.toolbar input::placeholder{{color:#b9adc9}}
.authMenu{{position:absolute;right:0;top:calc(100% + 10px);z-index:60;width:min(640px,calc(100vw - 44px));max-height:min(820px,calc(100svh - 120px));overflow:auto;padding:16px;background:linear-gradient(180deg,rgba(255,255,255,.09),rgba(255,255,255,.05)),rgba(19,14,32,.96);border:1px solid var(--line);border-radius:8px;box-shadow:0 24px 70px rgba(0,0,0,.36);backdrop-filter:blur(12px);scrollbar-width:thin}}.authMenu[hidden]{{display:none}}.authMenu h2{{margin:0 0 4px;font-size:18px}}.authMenu p{{margin:0 0 12px;color:var(--muted)}}.authGrid{{display:grid;grid-template-columns:1fr;gap:10px}}.authGrid .wide{{grid-column:1/-1}}.msg{{margin-top:10px;color:#bbf7d0;font-weight:750}}.msg.bad{{color:#fecdd3}}.formMsg{{margin:-8px 0 18px;padding:10px 12px;border:1px solid rgba(34,197,94,.34);border-radius:8px;background:rgba(34,197,94,.12);color:#bbf7d0;font-weight:800}}.formMsg.bad{{border-color:rgba(251,113,133,.4);background:rgba(251,113,133,.13);color:#fecdd3}}
.sessionBox{{margin-top:14px;padding-top:12px;border-top:1px solid var(--line)}}.sessionHead{{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:8px}}.sessionHead h3{{margin:0;font-size:15px}}.sessionList{{display:grid;gap:8px;max-height:260px;overflow:auto;padding-right:2px}}.sessionRow{{display:grid;grid-template-columns:1fr auto;gap:8px;align-items:center;border:1px solid var(--line);border-radius:8px;background:rgba(255,255,255,.055);padding:9px;text-align:left}}.sessionTitle{{display:flex;gap:7px;align-items:center;flex-wrap:wrap;font-weight:900}}.sessionMeta{{margin-top:3px;color:var(--muted);font-size:12px;line-height:1.35;word-break:break-word}}.sessionCurrent{{border:1px solid rgba(34,197,94,.38);border-radius:999px;padding:2px 7px;color:#bbf7d0;background:rgba(34,197,94,.13);font-size:11px}}.sessionBtn{{padding:7px 9px;font-size:12px;border-radius:999px}}.sessionEmpty{{padding:10px;border:1px dashed var(--line);border-radius:8px;color:var(--muted);text-align:center}}
.notifyBox{{margin-top:14px;padding-top:12px;border-top:1px solid var(--line)}}.notifyActions{{display:flex;gap:7px;align-items:center;justify-content:flex-end;flex-wrap:wrap}}.notifyHint{{margin:-4px 0 10px;color:var(--muted);font-size:12px;line-height:1.35}}.notifyList{{display:grid;gap:8px;max-height:260px;overflow:auto;padding-right:2px}}.notifyRow{{border:1px solid var(--line);border-radius:8px;background:rgba(255,255,255,.055);padding:9px;text-align:left}}.notifyTitle{{display:flex;align-items:center;justify-content:space-between;gap:8px;font-weight:950}}.notifyTitle span:first-child{{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.notifyTime{{color:var(--muted);font-size:11px;font-weight:750;white-space:nowrap}}.notifyBody{{margin-top:4px;color:#ddd6fe;font-size:12px;line-height:1.35;word-break:break-word}}.notifyDetails{{margin:7px 0 0;padding:8px;border:1px solid rgba(255,255,255,.08);border-radius:7px;background:rgba(0,0,0,.18);color:#c4b5fd;font:11px/1.35 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;max-height:92px;overflow:auto}}.notifyRow.warn{{border-color:rgba(245,158,11,.34);background:rgba(245,158,11,.08)}}.notifyRow.bad{{border-color:rgba(251,113,133,.38);background:rgba(251,113,133,.09)}}.notifyBtn.on{{border-color:rgba(34,197,94,.36);background:rgba(34,197,94,.15);color:#bbf7d0}}
.backupBox{{margin-top:14px;padding-top:12px;border-top:1px solid var(--line)}}.backupGrid{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}.backupGrid .wide{{grid-column:1/-1}}.backupGrid input[type=file]{{padding:8px;background:rgba(8,5,18,.72);border:1px solid var(--line);border-radius:8px;color:#ddd6fe;min-width:0}}.backupHint{{margin:0 0 10px;color:var(--muted);font-size:12px;line-height:1.35}}.backupCmds{{display:grid;gap:8px;margin-top:10px}}.backupCmd{{border:1px solid rgba(255,255,255,.10);border-radius:8px;background:rgba(0,0,0,.18);padding:9px}}.backupCmd strong{{display:block;margin-bottom:5px;color:#f7f2ff;font-size:12px}}.backupCmd pre{{margin:0;white-space:pre-wrap;word-break:break-word;color:#c4b5fd;font:11px/1.35 ui-monospace,SFMono-Regular,Consolas,monospace}}.backupMsg{{margin-top:9px;color:#bbf7d0;font-size:12px;font-weight:800;line-height:1.35}}.backupMsg.bad{{color:#fecdd3}}
input,select{{min-width:0;border:1px solid var(--line);border-radius:8px;padding:10px 11px;background:rgba(8,5,18,.72);color:var(--text)}}button,.btn{{border:1px solid rgba(255,255,255,.10);border-radius:8px;padding:10px 13px;background:rgba(255,255,255,.10);color:#f7f2ff;font-weight:850;text-decoration:none;cursor:pointer;display:inline-flex;justify-content:center;align-items:center}}.authToggle{{border-radius:999px;padding:8px 14px;background:rgba(255,255,255,.08);color:#f3e8ff}}button.primary,.btn.primary{{background:var(--blue);color:#fff;box-shadow:0 10px 22px rgba(124,58,237,.22)}}button.bad,.btn.bad{{background:rgba(251,113,133,.16);color:#fecdd3}}.btn.good{{background:rgba(34,197,94,.16);color:#bbf7d0}}.btn.disabled{{opacity:.45;cursor:not-allowed}}
.summary{{display:flex;gap:10px;flex-wrap:wrap;justify-content:flex-end}}.miniStat{{display:inline-flex;align-items:center;justify-content:center;gap:8px;min-height:36px;min-width:132px;border:1px solid var(--line);border-radius:999px;background:rgba(255,255,255,.07);padding:8px 12px;color:#ddd6fe;font-weight:800;font-size:13px;line-height:1;white-space:nowrap}}
.routerStats{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));align-items:start;gap:8px;margin:0 0 10px}}.statCard{{position:relative;min-height:64px;overflow:hidden;border:1px solid var(--line);border-radius:8px;padding:10px 10px 9px;background:linear-gradient(180deg,rgba(255,255,255,.075),rgba(255,255,255,.04)),var(--panel);box-shadow:0 8px 20px rgba(0,0,0,.14);text-align:center;box-sizing:border-box}}.statCard::before{{content:"";position:absolute;inset:0 0 auto 0;height:2px;background:var(--cyan)}}.statCard span{{display:block;color:var(--muted);font-size:10px;font-weight:850;text-transform:uppercase;letter-spacing:.035em}}.statCard strong{{display:block;margin-top:0;font-size:24px;line-height:1;color:#f7f2ff}}.statCard em{{display:block;min-width:0;margin-top:3px;padding:0 2px;color:#c4b5fd;font-style:normal;font-size:10px;line-height:1.2}}.statCard.online::before{{background:var(--green)}}.statCard.online strong{{color:#bbf7d0}}.statCard.offline::before{{background:var(--red)}}.statCard.offline strong{{color:#fecdd3}}.statCard.total::before{{background:var(--cyan)}}.statCard.total strong{{color:#a5f3fc}}.statCardHead{{display:flex;align-items:center;justify-content:center;min-height:18px}}.statCard.has-popover{{overflow:visible;z-index:12}}.statValueRow,.offlineStatRow{{position:relative;display:flex;align-items:center;justify-content:center;min-height:24px;margin-top:5px}}.offlineMoreBtn{{position:absolute;right:6px;top:50%;transform:translateY(-50%);display:inline-flex;align-items:center;justify-content:center;min-height:22px;padding:0 8px;border:1px solid rgba(251,113,133,.34);border-radius:999px;background:rgba(251,113,133,.12);color:#fecdd3;font-size:10px;font-weight:900;line-height:1;cursor:pointer;white-space:nowrap}}.offlineMoreBtn[aria-expanded="true"]{{background:rgba(251,113,133,.18);border-color:rgba(251,113,133,.48)}}.offlinePopover{{position:absolute;top:calc(100% + 8px);right:6px;width:min(340px,calc(100vw - 30px));padding:10px;border:1px solid rgba(251,113,133,.28);border-radius:12px;background:linear-gradient(180deg,rgba(49,28,44,.98),rgba(30,18,40,.98));box-shadow:0 22px 50px rgba(0,0,0,.34);text-align:left;z-index:25}}.offlinePopover[hidden]{{display:none!important}}.offlineList{{display:grid;gap:6px;max-height:170px;overflow:auto;padding-right:2px}}.offlineItem{{border:1px solid rgba(251,113,133,.18);border-radius:8px;padding:7px 8px;background:rgba(251,113,133,.08)}}.offlineName{{display:block;color:#fdf2f8;font-size:11px;font-weight:900;line-height:1.25;text-transform:none;letter-spacing:0}}
.cards{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}}.card{{position:relative;min-height:246px;overflow:hidden;background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.045)),var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px;box-shadow:0 18px 46px rgba(0,0,0,.28);backdrop-filter:blur(10px)}}.card::before{{content:"";position:absolute;inset:0 0 auto 0;height:3px;background:var(--green)}}.card.online{{border-color:rgba(34,197,94,.45);box-shadow:0 18px 46px rgba(0,0,0,.28),0 0 0 1px rgba(34,197,94,.10),0 0 34px rgba(34,197,94,.10)}}.card.off{{border-color:rgba(251,113,133,.42);box-shadow:0 18px 46px rgba(0,0,0,.28),0 0 0 1px rgba(251,113,133,.08),0 0 30px rgba(251,113,133,.08)}}.card.off::before{{background:var(--red)}}.card.warn::before{{background:var(--amber)}}.card.main{{grid-column:span 1}}
@keyframes onlineGlow{{0%,100%{{transform:scale(.9);opacity:.55}}50%{{transform:scale(1.08);opacity:1}}}}@keyframes offlineGlow{{0%,100%{{transform:scale(.88);opacity:.34}}50%{{transform:scale(1.08);opacity:.9}}}}
@keyframes bannerShine{{0%,45%{{transform:translateX(-120%)}}72%,100%{{transform:translateX(120%)}}}}
.cardTop{{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}}
.status{{display:inline-flex;align-items:center;gap:7px;border-radius:999px;border:1px solid rgba(34,197,94,.36);background:rgba(34,197,94,.14);padding:7px 10px;font-weight:900;font-size:12px;color:#bbf7d0}}.status i{{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 13px var(--green);animation:statusPulse 1.6s ease-in-out infinite}}.status.off{{border-color:rgba(251,113,133,.36);background:rgba(251,113,133,.12);color:#fecdd3}}.status.off i{{background:var(--red);box-shadow:0 0 13px var(--red);animation:offlinePulse 1.9s ease-in-out infinite}}.status.warn i{{background:var(--amber);box-shadow:0 0 13px var(--amber)}}@keyframes statusPulse{{0%,100%{{transform:scale(1);opacity:.75}}50%{{transform:scale(1.45);opacity:1}}}}@keyframes offlinePulse{{0%,100%{{transform:scale(1);opacity:.5}}50%{{transform:scale(1.42);opacity:1}}}}.nameRow{{display:inline-flex;align-items:center;justify-content:center;gap:8px;max-width:100%;margin-top:12px;vertical-align:top}}.nameRow::before{{content:"";display:block;flex:0 0 28px;width:28px;height:28px}}.name{{margin:0;font-size:19px;font-weight:900;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:220px}}.nameEditBtn{{position:static;display:inline-flex;align-items:center;justify-content:center;flex:0 0 28px;width:28px;height:28px;padding:0;border:1px solid rgba(251,191,36,.38);border-radius:999px;background:rgba(251,191,36,.12);color:#fde68a;box-shadow:inset 0 1px 0 rgba(255,255,255,.08);cursor:pointer;opacity:.5;transition:background .15s ease,border-color .15s ease,color .15s ease,transform .15s ease,opacity .15s ease}}.nameEditBtn:hover,.nameEditBtn:focus-visible{{opacity:1;border-color:rgba(34,211,238,.55);background:rgba(34,211,238,.14);color:#cffafe}}.nameEditBtn:active{{transform:scale(.96)}}.nameEditBtn svg{{width:13px;height:13px;display:block}}.mobilePanelToggle,.routerFormToggle{{display:none;width:100%;margin:14px 0 10px;border-radius:999px}}.routerFormWrap{{display:block}}[hidden],.headerActions[hidden],.routerStats[hidden],.routerFormWrap[hidden]{{display:none!important}}.metaLine{{margin-top:3px;color:var(--muted)}}.tagRow{{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}}.tag{{border:1px solid var(--line);border-radius:999px;padding:5px 9px;background:rgba(255,255,255,.06);color:#ddd6fe;font-size:12px;font-weight:750}}
.metrics{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:14px}}.metric{{display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;border:1px solid var(--line);border-radius:8px;background:rgba(255,255,255,.055);padding:9px}}.metric.span2{{grid-column:span 2}}.metric.temp-ok strong,.metric.flash-ok strong,.metric.memory-ok strong{{color:#bbf7d0}}.metric.temp-warn strong,.metric.flash-warn strong,.metric.memory-warn strong{{color:#fde68a}}.metric.temp-bad strong,.metric.flash-bad strong,.metric.memory-bad strong{{color:#fecdd3}}.metric>span{{display:block;width:100%;color:var(--muted);font-size:11px;text-align:center}}.metric strong{{display:block;width:100%;margin-top:2px;font-size:14px;word-break:break-word;text-align:center}}.metric.metric-compact strong{{font-size:14px;line-height:1.3;white-space:pre-line;word-break:normal}}.metric.temp-unavailable strong{{font-size:14px;line-height:1.3;white-space:pre-line;word-break:normal;color:#f3e8ff}}.actionToggle{{display:none}}.actions{{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}}.wolPanel{{display:grid;gap:10px;margin-top:12px;padding:12px;border:1px solid rgba(34,211,238,.20);border-radius:12px;background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.045)),rgba(18,14,30,.82);text-align:left}}.wolPanel[hidden]{{display:none!important}}.wolHeader{{display:flex;align-items:center;justify-content:space-between;gap:8px}}.wolTitle{{margin:0;color:#f3e8ff;font-size:13px;font-weight:900}}.wolMeta{{min-height:16px;color:#c4b5fd;font-size:12px;line-height:1.35}}.wolMeta.bad{{color:#fecdd3}}.wolControls{{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,.95fr) auto auto;gap:8px;align-items:end}}.wolField{{display:grid;gap:6px;min-width:0;color:#ddd6fe;font-size:11px;font-weight:850}}.wolField select,.wolField input{{width:100%;min-width:0;min-height:38px;padding:0 12px;border:1px solid rgba(34,211,238,.24);border-radius:10px;background:linear-gradient(180deg,rgba(52,38,74,.96),rgba(34,26,52,.96));color:#f5f3ff;outline:none;box-shadow:inset 0 1px 0 rgba(255,255,255,.04)}}.wolField select option{{background:#221a34;color:#f5f3ff}}.wolField select option:checked{{background:#3b82f6;color:#ffffff}}.wolField select:focus,.wolField input:focus{{border-color:rgba(34,211,238,.60);box-shadow:0 0 0 3px rgba(34,211,238,.12),inset 0 1px 0 rgba(255,255,255,.05)}}.wolField input::placeholder{{color:rgba(221,214,254,.52)}}.wolControls .btn,.wolControls button{{min-height:38px}}.empty{{grid-column:1/-1;border:1px dashed var(--line);border-radius:8px;padding:30px;background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.045)),var(--panel);text-align:center;color:var(--muted)}}.hint{{margin-top:16px;padding:13px;border:1px solid var(--line);border-radius:8px;background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.045)),var(--panel);color:var(--muted)}}.diagnosticPanel{{margin:16px 0 4px;padding:14px;border:1px solid rgba(34,211,238,.22);border-radius:8px;background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.045)),var(--panel);box-shadow:0 18px 46px rgba(0,0,0,.20);text-align:center}}.diagnosticPanel[hidden]{{display:none!important}}.diagnosticTop{{display:grid;grid-template-columns:1fr auto 1fr;gap:12px;align-items:flex-start}}.diagnosticTop>div{{grid-column:2;text-align:center}}.diagnosticTop .btn{{grid-column:3;justify-self:end;align-self:start;width:auto;min-width:118px;max-width:none;padding-left:18px;padding-right:18px}}.diagnosticTop h2{{margin:0;font-size:18px}}.diagnosticLead{{margin:4px 0 0;color:var(--muted);font-size:12px;line-height:1.4}}.diagnosticGrid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-top:12px}}.diagnosticGrid label{{display:grid;gap:6px;color:#ddd6fe;font-size:12px;font-weight:850;text-align:center}}.diagnosticGrid textarea{{min-height:92px;resize:vertical;border:1px solid rgba(167,139,250,.24);border-radius:10px;padding:12px 13px;background:linear-gradient(180deg,rgba(52,38,74,.96),rgba(34,26,52,.96));color:#f5f3ff;box-shadow:inset 0 1px 0 rgba(255,255,255,.04);outline:none;text-align:left}}.diagnosticGrid textarea::placeholder{{color:rgba(221,214,254,.42)}}.diagnosticGrid textarea:focus{{border-color:rgba(34,211,238,.62);box-shadow:0 0 0 3px rgba(34,211,238,.12),inset 0 1px 0 rgba(255,255,255,.05)}}.diagnosticActions{{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;justify-content:center}}.diagSummary{{margin-top:12px;padding:10px 12px;border-radius:8px;font-weight:850}}.diagSummary.good{{border:1px solid rgba(34,197,94,.35);background:rgba(34,197,94,.12);color:#bbf7d0}}.diagSummary.warn{{border:1px solid rgba(245,158,11,.34);background:rgba(245,158,11,.10);color:#fde68a}}.diagSummary.bad{{border:1px solid rgba(251,113,133,.38);background:rgba(251,113,133,.10);color:#fecdd3}}.diagList{{margin:0;padding-left:18px;color:#ddd6fe}}.diagList li{{margin:2px 0}}.diagBlocks{{display:grid;gap:8px;margin-top:10px;text-align:left}}.diagBlock{{border:1px solid rgba(167,139,250,.16);border-radius:8px;padding:10px;background:linear-gradient(180deg,rgba(57,43,82,.55),rgba(33,26,48,.74));box-shadow:inset 0 1px 0 rgba(255,255,255,.03)}}.diagBlock strong{{display:block;margin-bottom:4px}}.diagBody{{display:grid;gap:8px}}.diagTextLine{{white-space:pre-line;line-height:1.45}}.diagCmdLine{{display:grid;gap:5px}}.diagCmdLabel{{line-height:1.4}}.diagCmdRow{{display:flex;align-items:center;gap:8px;min-width:0}}.diagCode{{flex:1;min-width:0;margin:0;padding:7px 11px;border:1px solid rgba(255,255,255,.08);border-radius:8px;background:rgba(0,0,0,.20);color:#c4b5fd;font:12px/1.35 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:nowrap;overflow:auto;scrollbar-width:thin}}.diagCopyBtn{{flex:0 0 36px;width:36px;height:36px;padding:0;border:1px solid rgba(255,255,255,.12);border-radius:8px;background:rgba(255,255,255,.08);color:#f3e8ff;display:inline-flex;align-items:center;justify-content:center;cursor:pointer;transition:transform .15s ease,background .15s ease,border-color .15s ease}}.diagCopyBtn:hover{{border-color:rgba(34,211,238,.52);background:rgba(34,211,238,.12)}}.diagCopyBtn:active{{transform:scale(.96)}}.diagCopyBtn.copied{{border-color:rgba(34,197,94,.42);background:rgba(34,197,94,.16);color:#bbf7d0}}.diagCopyBtn svg{{width:16px;height:16px;display:block}}.diagCopyBtn span{{position:absolute;left:-9999px}}.diagBlock.good strong{{color:#bbf7d0}}.diagBlock.warn strong{{color:#fde68a}}.diagBlock.bad strong{{color:#fecdd3}}code{{background:rgba(255,255,255,.10);border-radius:6px;padding:2px 5px;color:#f3e8ff}}
.wolField input,.wolPickerToggle{{width:100%;min-width:0;min-height:38px;padding:0 12px;border:1px solid rgba(34,211,238,.24);border-radius:10px;background:linear-gradient(180deg,rgba(52,38,74,.96),rgba(34,26,52,.96));color:#f5f3ff;outline:none;box-shadow:inset 0 1px 0 rgba(255,255,255,.04)}}.wolField input:focus,.wolPickerToggle:focus,.wolPickerToggle:focus-visible{{border-color:rgba(34,211,238,.60);box-shadow:0 0 0 3px rgba(34,211,238,.12),inset 0 1px 0 rgba(255,255,255,.05)}}.wolDeviceField{{grid-column:1/-1;width:100%}}.wolPickerToggle{{display:flex;align-items:center;justify-content:space-between;gap:10px;text-align:left;cursor:pointer}}.wolPickerToggle[disabled]{{opacity:.55;cursor:not-allowed}}.wolPickerValue{{display:grid;gap:2px;min-width:0;flex:1}}.wolPickerValue strong{{display:block;min-width:0;color:#f5f3ff;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.wolPickerValue small{{display:block;min-width:0;color:#c4b5fd;font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.wolPickerChevron{{flex:0 0 auto;color:#c4b5fd;font-size:15px;line-height:1}}.wolPickerList{{display:grid;gap:8px;max-height:240px;overflow:auto;padding:8px;border:1px solid rgba(34,211,238,.24);border-radius:12px;background:rgba(17,12,29,.88);box-shadow:inset 0 1px 0 rgba(255,255,255,.04)}}.wolDeviceBtn{{width:100%;display:grid;gap:3px;padding:10px 12px;border:1px solid rgba(167,139,250,.18);border-radius:10px;background:rgba(255,255,255,.04);color:#f5f3ff;text-align:left;cursor:pointer;transition:border-color .15s ease,background .15s ease,transform .15s ease}}.wolDeviceBtn:hover,.wolDeviceBtn:focus-visible{{border-color:rgba(34,211,238,.48);background:rgba(34,211,238,.12)}}.wolDeviceBtn:active{{transform:scale(.99)}}.wolDeviceBtn.active{{border-color:rgba(59,130,246,.62);background:rgba(59,130,246,.18);box-shadow:0 0 0 1px rgba(59,130,246,.16)}}.wolDeviceName{{display:block;color:#f5f3ff;font-size:13px;font-weight:800;line-height:1.3}}.wolDeviceMeta{{display:block;color:#c4b5fd;font-size:11px;line-height:1.35;word-break:break-word}}.wolPickerEmpty{{padding:14px 12px;border:1px dashed rgba(167,139,250,.20);border-radius:10px;background:rgba(255,255,255,.03);color:#c4b5fd;text-align:center}}.metric.memory-ok strong,.metric.flash-ok strong,.metric.memory-warn strong,.metric.flash-warn strong,.metric.memory-bad strong,.metric.flash-bad strong{{color:#f3e8ff}}.metric.memory-ok .metric-accent,.metric.flash-ok .metric-accent{{color:#bbf7d0}}.metric.memory-warn .metric-accent,.metric.flash-warn .metric-accent{{color:#fde68a}}.metric.memory-bad .metric-accent,.metric.flash-bad .metric-accent{{color:#fecdd3}}.metric-line{{display:block}}.metric-accent{{font-weight:inherit}}
.brandPanel{{display:grid;grid-template-columns:132px 132px 168px;gap:8px;width:448px;max-width:100%}}.brandPanel .appBanner{{grid-column:1/span 2;width:100%;min-width:0}}.brandPanel .links{{grid-column:1/-1;display:grid;grid-template-columns:132px 132px 168px;gap:8px;margin-top:0}}.brandPanel .links a{{width:100%;min-width:0}}.routerSearchDock{{position:relative;display:block;grid-column:3;grid-row:1;align-self:start;width:168px;max-width:100%}}.mobileSearchDock{{display:none;width:100%;position:relative}}.routerSearchToggle{{position:relative;display:inline-flex;align-items:center;justify-content:center;gap:8px;min-height:36px;width:100%;padding:8px 14px;border:1px solid rgba(34,211,238,.30);border-radius:999px;background:linear-gradient(110deg,rgba(34,211,238,.12),rgba(124,58,237,.22),rgba(236,72,153,.12));color:#f3e8ff;font-size:13px;font-weight:800;line-height:1;box-shadow:0 10px 24px rgba(124,58,237,.16),inset 0 1px 0 rgba(255,255,255,.10)}}.routerSearchToggle[data-active="true"],.routerSearchToggle[aria-expanded="true"]{{border-color:rgba(34,211,238,.52);box-shadow:0 14px 30px rgba(34,211,238,.14),inset 0 1px 0 rgba(255,255,255,.12)}}.routerSearchToggleIcon{{flex:0 0 auto;color:#c4b5fd;font-size:14px;line-height:1}}.routerSearchPanel{{position:absolute;top:calc(100% + 10px);left:0;z-index:55;width:min(296px,calc(100vw - 24px))}}.routerSearchPanel[hidden]{{display:none!important}}.routerSearchCard{{display:grid;grid-template-rows:auto auto auto;align-content:start;gap:8px;width:100%;min-height:82px;padding:12px;border:1px solid rgba(34,211,238,.26);border-radius:18px;background:linear-gradient(180deg,rgba(255,255,255,.09),rgba(255,255,255,.045)),rgba(19,14,32,.96);box-shadow:0 18px 42px rgba(0,0,0,.22),inset 0 1px 0 rgba(255,255,255,.06);backdrop-filter:blur(10px)}}.routerSearchHead{{display:flex;align-items:center;justify-content:space-between;gap:8px}}.routerSearchTitle{{display:block;color:#f3e8ff;font-size:12px;font-weight:900;line-height:1.1}}.routerSearchClear{{min-height:24px;padding:0 9px;border:1px solid rgba(167,139,250,.24);border-radius:999px;background:rgba(255,255,255,.06);color:#ddd6fe;font-size:11px;font-weight:850;cursor:pointer}}.routerSearchClear[disabled]{{opacity:.42;cursor:not-allowed}}.routerSearchField{{display:flex;align-items:center;gap:8px;min-height:38px;padding:0 12px;border:1px solid rgba(34,211,238,.26);border-radius:999px;background:linear-gradient(110deg,rgba(34,211,238,.08),rgba(124,58,237,.14),rgba(236,72,153,.08));box-shadow:inset 0 1px 0 rgba(255,255,255,.06)}}.routerSearchField:focus-within{{border-color:rgba(34,211,238,.54);box-shadow:0 0 0 3px rgba(34,211,238,.10),inset 0 1px 0 rgba(255,255,255,.08)}}.routerSearchField input{{width:100%;padding:0;border:0;background:transparent;color:#f7f2ff;box-shadow:none;outline:none;font-size:13px;font-weight:700}}.routerSearchField input::placeholder{{color:#b9adc9}}.routerSearchIcon{{flex:0 0 auto;color:#c4b5fd;font-size:14px;line-height:1}}.routerSearchMeta{{min-height:14px;color:#c4b5fd;font-size:11px;font-weight:800;line-height:1.2}}.card{{text-align:center}}.cardTop{{align-items:center;justify-content:center;flex-direction:column}}.tagRow,.actions{{justify-content:center}}.name{{display:inline-flex;align-items:center;justify-content:center;max-width:220px;min-height:34px;margin:0;padding:7px 10px;border:1px solid rgba(251,191,36,.48);border-radius:999px;background:linear-gradient(135deg,rgba(251,191,36,.32),rgba(245,158,11,.22),rgba(255,255,255,.07));white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#fff;font-size:13px;line-height:1;font-weight:900;text-shadow:0 0 16px rgba(251,191,36,.42);box-shadow:0 10px 24px rgba(245,158,11,.10),inset 0 1px 0 rgba(255,255,255,.12)}}.metric{{text-align:center}}.metric.span2{{grid-column:1/-1}}
@media(max-width:980px){{.cards{{grid-template-columns:repeat(2,minmax(0,1fr))}}.toolbar{{grid-template-columns:1fr 1fr}}.card.main{{grid-column:span 1}}.top{{flex-direction:column}}.brandPanel{{width:448px}}.headerActions{{align-self:flex-start;flex-wrap:wrap;padding-top:0;justify-content:flex-start}}}}
@media(max-width:680px){{body{{font-size:13px;background-attachment:scroll}}.wrap{{padding:10px}}.top{{gap:12px;padding:14px 0;align-items:flex-start;flex-direction:column}}.brand,.brand>div{{width:100%}}h1{{font-size:22px;line-height:1.18}}.appBanner{{width:auto;max-width:100%;justify-content:center;min-height:36px;padding:8px 12px}}.links,.headerActions,.summary{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));width:100%;gap:8px;max-width:none}}.links{{margin-top:10px}}.links a,.badge,.headerActions .btn,.miniStat{{width:100%;min-width:0;padding:9px 10px;font-size:12px}}.authMenu{{position:fixed;left:10px;right:10px;top:74px;width:auto;max-height:calc(100svh - 90px);overflow:auto}}.cards,.toolbar,.authGrid{{grid-template-columns:1fr}}.routerStats{{grid-template-columns:repeat(3,minmax(0,1fr));gap:5px;margin-bottom:7px}}.statCard{{min-height:52px;padding:7px 6px}}.statCard span{{font-size:8px;letter-spacing:.015em}}.statCard strong{{font-size:20px}}.statCard em{{display:none}}.statCardHead{{min-height:16px}}.statValueRow,.offlineStatRow{{min-height:20px;margin-top:4px}}.offlineMoreBtn{{right:2px;min-height:16px;padding:0 5px;font-size:7px}}.offlinePopover{{right:3px;width:min(230px,calc(100vw - 20px));padding:8px}}.offlineItem{{padding:6px 7px}}.offlineName{{font-size:10px}}.toolbar{{padding:10px;margin:12px 0}}.card.main{{grid-column:span 1}}.card{{padding:12px;min-height:0}}.card>.metaLine,.card>.tagRow{{display:none}}.nameRow{{gap:6px;margin-top:10px}}.nameRow::before{{flex-basis:26px;width:26px;height:26px}}.name{{font-size:12px;max-width:190px}}.nameEditBtn{{flex-basis:26px;width:26px;height:26px}}.mobilePanelToggle,.routerFormToggle{{display:inline-flex}}.routerSearchDock{{width:100%}}.routerSearchToggle{{width:100%}}.routerSearchPanel{{position:static;width:100%;margin-top:8px}}.routerSearchCard{{width:100%;min-height:0;padding:10px 11px;border-radius:16px}}.routerSearchTitle{{font-size:11px}}.routerSearchField{{min-height:36px}}.routerSearchMeta{{text-align:center}}.metrics{{grid-template-columns:repeat(2,minmax(0,1fr));gap:7px}}.metric{{padding:8px}}.diagnosticPanel{{padding:12px}}.diagnosticTop{{gap:8px}}.diagnosticTop h2{{font-size:17px}}.diagnosticLead{{font-size:11px}}.diagnosticGrid{{grid-template-columns:1fr;gap:10px}}.diagnosticGrid label{{font-size:13px}}.diagnosticGrid textarea{{min-height:118px;padding:12px 13px;font-size:15px;line-height:1.35}}.diagBlock{{padding:11px}}.actions{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}}.actions .btn,.actions button{{width:100%;min-width:0;padding:9px 8px;font-size:12px}}.wolControls{{grid-template-columns:1fr}}.wolField span,.wolMeta{{text-align:center}}.wolControls .btn,.wolControls button{{width:100%}}}}
@media(max-width:680px){{.brandPanel{{display:none}}.top{{padding:6px 0 8px;gap:8px}}.mobileSearchDock{{display:block;margin:0 0 2px}}.mobilePanelToggle,.routerFormToggle,.actionToggle{{width:100%;min-height:38px;margin:7px 0;padding:9px 12px;border-radius:999px;font-size:12px;line-height:1}}#hubMenuToggle{{margin-top:0}}.actionToggle{{display:inline-flex}}body.preload-mobile-panels .headerActions,body.preload-mobile-panels .routerFormWrap,body.preload-mobile-panels .routerStats{{display:none!important}}.card .actions.mobileCollapsed:not(.open){{display:none}}.cardTop{{gap:0}}}}
@media(max-width:680px){{.headerActions .badge,.headerActions .btn{{width:100%;min-width:0;max-width:none}}}}
@media(max-width:420px){{.links,.headerActions,.summary,.actions{{grid-template-columns:1fr}}.metrics{{grid-template-columns:1fr}}.metric.span2{{grid-column:span 1}}}}
@media(max-width:680px){{.brandPanel{{grid-template-columns:repeat(2,minmax(0,1fr));width:100%}}.brandPanel .appBanner{{width:100%}}.summary{{justify-content:center}}}}
</style>
</head>
<body class="preload-mobile-panels">
<main class="wrap">
  <section class="top">
    <div class="brand">
      <div class="brandPanel">
        <h1 class="appBanner"><span>OpenWrt Remote Hub</span></h1>
        <div class="routerSearchDock" id="routerSearchDock">
          <button class="routerSearchToggle" id="routerSearchToggle" type="button" aria-expanded="false" aria-controls="routerSearchPanel" data-active="false">
            <span class="routerSearchToggleIcon" aria-hidden="true">⌕</span>
            <span>Поиск роутеров</span>
          </button>
          <section class="routerSearchPanel" id="routerSearchPanel" aria-label="Поиск роутеров" hidden>
            <div class="routerSearchCard">
              <div class="routerSearchHead">
                <strong class="routerSearchTitle">Поиск по роутерам</strong>
                <button class="routerSearchClear" id="routerSearchClear" type="button" disabled>Очистить</button>
              </div>
              <label class="routerSearchField" for="routerSearchInput">
                <span class="routerSearchIcon" aria-hidden="true">⌕</span>
                <input id="routerSearchInput" type="search" placeholder="Имя или router id" autocomplete="off" spellcheck="false">
              </label>
              <div class="routerSearchMeta" id="routerSearchMeta">Всего роутеров: 0</div>
            </div>
          </section>
        </div>
        <div class="links">
          <a href="https://t.me/kzolotarev95" target="_blank" rel="noopener noreferrer">Telegram</a>
          <a href="https://github.com/kzolotarev95" target="_blank" rel="noopener noreferrer">GitHub</a>
          <a class="nethavenTop" href="https://t.me/+LZDsQJhUfcNhYWEy" target="_blank" rel="noopener noreferrer">NetHaven VPN</a>
        </div>
      </div>
    </div>
    <div class="mobileSearchDock" id="mobileRouterSearchDock">
      <button class="routerSearchToggle mobilePanelToggle primary" id="mobileRouterSearchToggle" type="button" aria-expanded="false" aria-controls="mobileRouterSearchPanel" data-active="false">
        <span class="routerSearchToggleIcon" aria-hidden="true">⌕</span>
        <span>Поиск роутеров</span>
      </button>
      <section class="routerSearchPanel" id="mobileRouterSearchPanel" aria-label="Поиск роутеров" hidden>
        <div class="routerSearchCard">
          <div class="routerSearchHead">
            <strong class="routerSearchTitle">Поиск по роутерам</strong>
            <button class="routerSearchClear" id="mobileRouterSearchClear" type="button" disabled>Очистить</button>
          </div>
          <label class="routerSearchField" for="mobileRouterSearchInput">
            <span class="routerSearchIcon" aria-hidden="true">⌕</span>
            <input id="mobileRouterSearchInput" type="search" placeholder="Имя или router id" autocomplete="off" spellcheck="false">
          </label>
          <div class="routerSearchMeta" id="mobileRouterSearchMeta">Всего роутеров: 0</div>
        </div>
      </section>
    </div>
    <button class="mobilePanelToggle primary" id="hubMenuToggle" type="button" hidden>Открыть меню хаба</button>
    <div class="headerActions">
      <a class="badge" href="/vps-terminal/" target="_blank" rel="noopener noreferrer">Терминал VPS</a>
      <button class="badge" id="xrayReload" type="button">Обновить Xray CFG</button>
      <button class="badge" id="xrayRestart" type="button">Рестарт Xray VPS</button>
      <button class="badge authToggle" id="authToggle" type="button">{safe_username}</button>
      <a class="btn" href="/logout">Выйти</a>
      <div class="authMenu" id="authMenu" hidden>
        <h2>Доступ к Hub</h2>
        <p>Смена логина и пароля входа.</p>
        <form id="authForm" class="authGrid">
          <input class="wide" name="username" value="{safe_username}" placeholder="Логин" autocomplete="username" required>
          <input name="current_password" type="password" placeholder="Текущий пароль" autocomplete="current-password" required>
          <input name="password" type="password" placeholder="Новый пароль" autocomplete="new-password">
          <input name="password_confirm" type="password" placeholder="Повтор пароля" autocomplete="new-password">
          <button class="primary wide">Сохранить</button>
        </form>
        <div id="authMsg" class="msg" hidden></div>
        <div class="sessionBox">
          <div class="sessionHead">
            <h3>Управление сессиями</h3>
            <button class="sessionBtn bad" id="revokeOtherSessions" type="button">Завершить остальные</button>
          </div>
          <div id="sessionList" class="sessionList"></div>
        </div>
        <div class="notifyBox">
          <div class="sessionHead">
            <h3>Уведомления</h3>
            <div class="notifyActions">
              <button class="sessionBtn notifyBtn" id="notifyEnable" type="button">Включить</button>
              <button class="sessionBtn" id="notifyClear" type="button">Очистить</button>
            </div>
          </div>
          <div class="notifyHint">Web Push для входов в панель, смены IP и запуска VPS/Hub. На iOS включай из приложения Hub с экрана Домой.</div>
          <div id="notifyList" class="notifyList"></div>
        </div>
        <div class="backupBox">
          <div class="sessionHead">
            <h3>Backup VPS</h3>
            <a class="sessionBtn btn good" id="backupDownload" href="/api/backup/download">Скачать</a>
          </div>
          <div class="backupHint">Архив переносит БД роутеров, логин, токены Hub/агента, push-ключи и уведомления. Для переезда на другой IP укажи новый VPS host/Public URL перед восстановлением.</div>
          <div class="backupCmds">
            <div class="backupCmd">
              <strong>1. Сделать backup на старой VPS</strong>
              <pre>/opt/owrt-remote/owrt-remote-hub.py backup --out /root/owrt-hub-backup.tar.gz</pre>
            </div>
            <div class="backupCmd">
              <strong>2. Поднять Hub на новой VPS</strong>
              <pre>curl -fsSL "https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main/vps/install-vps.sh?v=$(date +%s)" | sh</pre>
            </div>
            <div class="backupCmd">
              <strong>3. Перенести архив на новую VPS</strong>
              <pre>scp /root/owrt-hub-backup.tar.gz root@NEW_IP:/root/owrt-hub-backup.tar.gz</pre>
            </div>
            <div class="backupCmd">
              <strong>4. Восстановить на новой VPS с новым IP/domain</strong>
              <pre>/opt/owrt-remote/owrt-remote-hub.py restore --file /root/owrt-hub-backup.tar.gz --vps-host NEW_IP_OR_DOMAIN --public-url https://NEW_DOMAIN</pre>
            </div>
            <div class="backupCmd">
              <strong>5. Применить после restore</strong>
              <pre>systemctl restart owrt-remote
systemctl restart owrt-remote-xray</pre>
            </div>
          </div>
          <div class="backupGrid">
            <input class="wide" id="backupFile" type="file" accept=".gz,.tgz,.tar.gz,application/gzip,application/x-gzip">
            <input id="backupVpsHost" placeholder="Новый VPS host/IP">
            <input id="backupPublicUrl" placeholder="Новый Public URL">
            <button class="primary wide" id="backupRestore" type="button">Восстановить из backup</button>
          </div>
          <div id="backupMsg" class="backupMsg" hidden></div>
        </div>
      </div>
    </div>
  </section>

  <button class="routerFormToggle primary" id="routerFormToggle" type="button" hidden>Открыть добавление</button>
  <div class="routerFormWrap" id="routerFormWrap">
  <form class="toolbar" id="routerForm">
    <input name="id" placeholder="router id: node-2" autocomplete="off" required>
    <input name="name" placeholder="Название роутера" required>
    <select name="role"><option value="node">node</option><option value="main">main</option></select>
    <input name="entry_port" placeholder="18080" inputmode="numeric" required>
    <input name="vps_host" placeholder="VPS IP/domain" required>
    <button class="primary">Добавить</button>
  </form>
  <div id="routerMsg" class="formMsg" hidden></div>
  </div>

  <button class="mobilePanelToggle primary" id="routerStatsToggle" type="button" hidden>Открыть статистику</button>
  <section id="routerStats" class="routerStats" aria-label="Статистика роутеров"></section>
  <section id="cards" class="cards"></section>
  <section id="diagnosticPanel" class="diagnosticPanel" hidden>
    <div class="diagnosticTop">
      <div>
        <h2 id="diagnosticTitle">Диагностика</h2>
        <p id="diagnosticLead" class="diagnosticLead">Выбери роутер и напиши проблему своими словами. Можно свободно: 502, пустая панель, не работает Xray, не открывается терминал.</p>
      </div>
      <button class="btn" id="diagnosticClose" type="button">Скрыть</button>
    </div>
    <div class="diagnosticGrid">
      <label>Что работает<textarea id="diagnosticWorks" placeholder="Например: роутер онлайн, SSH открывается, Wi-Fi есть."></textarea></label>
      <label>Какие проблемы<textarea id="diagnosticProblems" placeholder="Пиши как есть: 502 Bad Gateway, пустые карточки, терминал не открывается, Xray не стартует, нет интернета."></textarea></label>
      <label>Что уже пробовал<textarea id="diagnosticTried" placeholder="Например: обновлял страницу, перезапускал Xray, проверял heartbeat, заново вставлял OpenWrt config."></textarea></label>
    </div>
    <div class="diagnosticActions">
      <button class="primary" id="diagnosticBuild" type="button">Запустить диагностику</button>
    </div>
    <div id="diagnosticSummary" class="diagSummary" hidden></div>
    <div id="diagnosticBlocks" class="diagBlocks"></div>
  </section>
</main>
<script>
window.ROUTERS = {routers_json};
window.HUB_SESSIONS = {sessions_json};
window.HUB_NOTIFICATIONS = {notifications_json};
const mobileLayoutMq = window.matchMedia('(max-width: 680px)');
const mobileCardsMq = mobileLayoutMq;
const cards = document.getElementById('cards');
const routerStats = document.getElementById('routerStats');
const routerStatsToggle = document.getElementById('routerStatsToggle');
const headerActions = document.querySelector('.headerActions');
const hubMenuToggle = document.getElementById('hubMenuToggle');
const routerFormWrap = document.getElementById('routerFormWrap');
const routerForm = document.getElementById('routerForm');
const routerFormToggle = document.getElementById('routerFormToggle');
const routerMsg = document.getElementById('routerMsg');
const routerSearchDock = document.getElementById('routerSearchDock');
const routerSearchToggle = document.getElementById('routerSearchToggle');
const routerSearchPanel = document.getElementById('routerSearchPanel');
const routerSearchInput = document.getElementById('routerSearchInput');
const routerSearchClear = document.getElementById('routerSearchClear');
const routerSearchMeta = document.getElementById('routerSearchMeta');
const mobileRouterSearchDock = document.getElementById('mobileRouterSearchDock');
const mobileRouterSearchToggle = document.getElementById('mobileRouterSearchToggle');
const mobileRouterSearchPanel = document.getElementById('mobileRouterSearchPanel');
const mobileRouterSearchInput = document.getElementById('mobileRouterSearchInput');
const mobileRouterSearchClear = document.getElementById('mobileRouterSearchClear');
const mobileRouterSearchMeta = document.getElementById('mobileRouterSearchMeta');
const routerIdInput = routerForm && routerForm.elements ? routerForm.elements.namedItem('id') : null;
const routerNameInput = routerForm && routerForm.elements ? routerForm.elements.namedItem('name') : null;
const routerRoleInput = routerForm && routerForm.elements ? routerForm.elements.namedItem('role') : null;
const routerEntryPortInput = routerForm && routerForm.elements ? routerForm.elements.namedItem('entry_port') : null;
const routerVpsHostInput = routerForm && routerForm.elements ? routerForm.elements.namedItem('vps_host') : null;
const diagnosticPanel = document.getElementById('diagnosticPanel');
const diagnosticTitle = document.getElementById('diagnosticTitle');
const diagnosticLead = document.getElementById('diagnosticLead');
const diagnosticClose = document.getElementById('diagnosticClose');
const diagnosticWorks = document.getElementById('diagnosticWorks');
const diagnosticProblems = document.getElementById('diagnosticProblems');
const diagnosticTried = document.getElementById('diagnosticTried');
const diagnosticBuild = document.getElementById('diagnosticBuild');
const diagnosticSummary = document.getElementById('diagnosticSummary');
const diagnosticBlocks = document.getElementById('diagnosticBlocks');
const expandedActionPanels = new Set();
const diagnosticDrafts = new Map();
const wolStateByRouter = new Map();
let pendingRouterRender = false;
let activeDiagnosticRouterId = '';
let offlineStatsExpanded = false;
let routerSearchQuery = '';
let lastRouterSearchTrigger = null;

const routerSearchToggles = [routerSearchToggle, mobileRouterSearchToggle].filter(Boolean);
const routerSearchPanels = [routerSearchPanel, mobileRouterSearchPanel].filter(Boolean);
const routerSearchInputs = [routerSearchInput, mobileRouterSearchInput].filter(Boolean);
const routerSearchClears = [routerSearchClear, mobileRouterSearchClear].filter(Boolean);
const routerSearchMetas = [routerSearchMeta, mobileRouterSearchMeta].filter(Boolean);
const routerSearchDocks = [routerSearchDock, mobileRouterSearchDock].filter(Boolean);

function actionPanelId(routerId) {{
  return 'router-actions-' + String(routerId || '');
}}

function wolPanelId(routerId) {{
  return 'router-wol-' + String(routerId || '');
}}

function wolPasswordKey(routerId) {{
  return 'owrtRemote:sshPassword:' + String(routerId || '');
}}

function loadWolPassword(routerId) {{
  try {{
    return localStorage.getItem(wolPasswordKey(routerId)) || '';
  }} catch (e) {{
    return '';
  }}
}}

function saveWolPassword(routerId, value) {{
  try {{
    const text = String(value || '');
    if (text) localStorage.setItem(wolPasswordKey(routerId), text);
    else localStorage.removeItem(wolPasswordKey(routerId));
  }} catch (e) {{}}
}}

function syncExpandedActionPanels(list) {{
  const validIds = new Set((Array.isArray(list) ? list : []).map(r => actionPanelId(r.id)));
  for (const id of Array.from(expandedActionPanels)) {{
    if (!validIds.has(id)) expandedActionPanels.delete(id);
  }}
}}

function syncActionToggleStates(root = cards) {{
  const toggles = root.querySelectorAll('[data-actions-toggle]');
  toggles.forEach((toggle) => {{
    const actionBox = document.getElementById(toggle.dataset.actionsToggle || '');
    const open = Boolean(actionBox && actionBox.classList.contains('open'));
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    toggle.textContent = open ? 'Скрыть действия' : 'Открыть действия';
  }});
}}

function getDiagnosticDraft(routerId) {{
  const key = String(routerId || '');
  if (!diagnosticDrafts.has(key)) {{
    diagnosticDrafts.set(key, {{works: '', problems: '', tried: ''}});
  }}
  return diagnosticDrafts.get(key);
}}

function setDiagnosticDraft(routerId, patch) {{
  const key = String(routerId || '');
  const next = Object.assign({{}}, getDiagnosticDraft(key), patch || {{}});
  diagnosticDrafts.set(key, next);
  return next;
}}

function getWolState(routerId) {{
  const key = String(routerId || '');
  if (!wolStateByRouter.has(key)) {{
    wolStateByRouter.set(key, {{
      open: false,
      pickerOpen: false,
      loaded: false,
      loading: false,
      waking: false,
      error: '',
      message: '',
      sshPassword: loadWolPassword(key),
      selectedMac: '',
      devices: []
    }});
  }}
  return wolStateByRouter.get(key);
}}

function setWolState(routerId, patch) {{
  const key = String(routerId || '');
  const current = getWolState(key);
  const next = Object.assign({{}}, current, patch || {{}});
  const devices = Array.isArray(next.devices) ? next.devices : [];
  if (devices.length) {{
    const selectedExists = devices.some((item) => String(item.mac || '') === String(next.selectedMac || ''));
    if (!selectedExists) next.selectedMac = '';
  }} else {{
    next.selectedMac = '';
  }}
  wolStateByRouter.set(key, next);
  return next;
}}

function hasOpenWolPicker() {{
  for (const state of wolStateByRouter.values()) {{
    if (state && state.open && state.pickerOpen) return true;
  }}
  return false;
}}

function flushDeferredRouterRender() {{
  if (!pendingRouterRender) return;
  pendingRouterRender = false;
  renderRouterView();
}}

function selectedRouter(routerId = activeDiagnosticRouterId) {{
  const key = String(routerId || '');
  return (window.ROUTERS || []).find((router) => String(router.id || '') === key) || null;
}}

function routerSupportsWol(router) {{
  const status = router && router.status ? router.status : {{}};
  return Boolean(router && router.online && String(status.ssh || '') === 'running' && Number(router.ssh_entry_port || 0) > 0);
}}

function wolOptionLabel(device) {{
  if (!device) return '';
  const parts = [];
  if (device.name) parts.push(device.name);
  if (device.ip) parts.push(device.ip);
  parts.push(device.mac || '');
  return parts.filter(Boolean).join(' | ');
}}

function wolDeviceMeta(device) {{
  if (!device) return '';
  return [device.iface, device.source].filter(Boolean).join(' | ');
}}

function wolMetaText(state) {{
  if (!state) return '';
  if (state.loading) return 'Собираю список устройств с роутера...';
  if (state.waking) return 'Отправляю Wake-on-LAN пакет...';
  if (state.error) return state.error;
  if (state.message) return state.message;
  const count = Array.isArray(state.devices) ? state.devices.length : 0;
  if (state.loaded && !count) return 'Устройства не найдены. Проверь DHCP leases или ARP на роутере.';
  if (count) return `Найдено устройств: ${{count}}`;
  return 'Открой список устройств для пробуждения.';
}}

function renderWolPanel(router) {{
  const state = getWolState(router.id);
  const panelId = wolPanelId(router.id);
  const devices = Array.isArray(state.devices) ? state.devices : [];
  const options = devices.length
    ? devices.map((device) => {{
        const selected = String(device.mac || '') === String(state.selectedMac || '') ? ' selected' : '';
        const meta = [device.iface, device.source].filter(Boolean).join(' | ');
        const label = meta ? `${{wolOptionLabel(device)}} (${{meta}})` : wolOptionLabel(device);
        return `<option value="${{escapeAttr(device.mac || '')}}" data-iface="${{escapeAttr(device.iface || '')}}"${{selected}}>${{escapeHtml(label)}}</option>`;
      }}).join('')
    : '<option value="">Устройств пока нет</option>';
  const wakeDisabled = state.loading || state.waking || !devices.length || !state.selectedMac;
  const metaClass = state.error ? 'wolMeta bad' : 'wolMeta';
  return `<div class="wolPanel" id="${{escapeAttr(panelId)}}"${{state.open ? '' : ' hidden'}}>
    <div class="wolHeader">
      <h3 class="wolTitle">Wake-on-LAN</h3>
    </div>
    <div class="wolControls">
      <label class="wolField">
        <span>Устройство</span>
        <select data-wol-select="${{escapeAttr(router.id)}}"${{state.loading || state.waking ? ' disabled' : ''}}>
          ${{options}}
        </select>
      </label>
      <label class="wolField">
        <span>SSH пароль</span>
        <input type="password" data-wol-password="${{escapeAttr(router.id)}}" value="${{escapeAttr(state.sshPassword || '')}}" placeholder="Если SSH по паролю"${{state.loading || state.waking ? ' disabled' : ''}}>
      </label>
      <button class="btn" type="button" data-wol-refresh="${{escapeAttr(router.id)}}"${{state.loading || state.waking ? ' disabled' : ''}}>Обновить</button>
      <button class="btn primary" type="button" data-wol-send="${{escapeAttr(router.id)}}"${{wakeDisabled ? ' disabled' : ''}}>Разбудить</button>
    </div>
    <div class="${{metaClass}}">${{escapeHtml(wolMetaText(state))}}</div>
  </div>`;
}}

function renderWolPanelRich(router) {{
  const state = getWolState(router.id);
  const panelId = wolPanelId(router.id);
  const devices = Array.isArray(state.devices) ? state.devices : [];
  const selectedDevice = devices.find((device) => String(device.mac || '') === String(state.selectedMac || '')) || null;
  const pickerTitle = selectedDevice ? wolOptionLabel(selectedDevice) : (devices.length ? 'Выбери устройство из списка' : 'Устройств пока нет');
  const pickerMeta = selectedDevice
    ? (wolDeviceMeta(selectedDevice) || 'Нажми "Разбудить", когда выберешь нужный ПК.')
    : (devices.length ? `Найдено устройств: ${{devices.length}}` : 'Сначала нажми "Обновить" или поставь WOL tool на роутер.');
  const deviceList = devices.length
    ? devices.map((device) => {{
        const selected = String(device.mac || '') === String(state.selectedMac || '') ? ' active' : '';
        const title = wolOptionLabel(device);
        const meta = wolDeviceMeta(device);
        return `<button class="wolDeviceBtn${{selected}}" type="button" data-wol-pick="${{escapeAttr(router.id)}}" data-wol-mac="${{escapeAttr(device.mac || '')}}">
          <span class="wolDeviceName">${{escapeHtml(title)}}</span>
          <span class="wolDeviceMeta">${{escapeHtml(meta || 'Без доп. данных')}}</span>
        </button>`;
      }}).join('')
    : '<div class="wolPickerEmpty">Устройства пока не найдены.</div>';
  const wakeDisabled = state.loading || state.waking || !devices.length || !state.selectedMac;
  const metaClass = state.error ? 'wolMeta bad' : 'wolMeta';
  return `<div class="wolPanel" id="${{escapeAttr(panelId)}}"${{state.open ? '' : ' hidden'}}>
    <div class="wolHeader">
      <h3 class="wolTitle">Wake-on-LAN</h3>
    </div>
    <div class="wolControls">
      <label class="wolField wolDeviceField">
        <span>Устройство</span>
        <button class="wolPickerToggle" type="button" data-wol-picker-toggle="${{escapeAttr(router.id)}}" aria-expanded="${{state.pickerOpen ? 'true' : 'false'}}"${{state.loading || state.waking ? ' disabled' : ''}}>
          <span class="wolPickerValue">
            <strong>${{escapeHtml(pickerTitle)}}</strong>
            <small>${{escapeHtml(pickerMeta)}}</small>
          </span>
          <span class="wolPickerChevron">${{state.pickerOpen ? '&#9650;' : '&#9660;'}}</span>
        </button>
        ${{state.pickerOpen ? `<div class="wolPickerList">${{deviceList}}</div>` : ''}}
      </label>
      <label class="wolField">
        <span>SSH пароль</span>
        <input type="password" data-wol-password="${{escapeAttr(router.id)}}" value="${{escapeAttr(state.sshPassword || '')}}" placeholder="Если SSH по паролю"${{state.loading || state.waking ? ' disabled' : ''}}>
      </label>
      <button class="btn" type="button" data-wol-refresh="${{escapeAttr(router.id)}}"${{state.loading || state.waking ? ' disabled' : ''}}>Обновить</button>
      <button class="btn primary" type="button" data-wol-send="${{escapeAttr(router.id)}}"${{wakeDisabled ? ' disabled' : ''}}>Разбудить</button>
    </div>
    <div class="${{metaClass}}">${{escapeHtml(wolMetaText(state))}}</div>
  </div>`;
}}

function normalizeRouterSearch(value) {{
  return String(value || '').toLowerCase().trim().replace(/\s+/g, ' ');
}}

function syncRouterSearchToggleState() {{
  const active = routerSearchQuery.trim() ? 'true' : 'false';
  routerSearchToggles.forEach((toggle) => {{
    toggle.dataset.active = active;
  }});
}}

function syncRouterSearchInputs() {{
  routerSearchInputs.forEach((input) => {{
    if (input.value !== routerSearchQuery) input.value = routerSearchQuery;
  }});
}}

function syncRouterSearchClears() {{
  const disabled = !routerSearchQuery.trim();
  routerSearchClears.forEach((button) => {{
    button.disabled = disabled;
  }});
}}

function setRouterSearchOpen(next, options = {{}}) {{
  if (!routerSearchPanels.length || !routerSearchToggles.length) return;
  const open = Boolean(next);
  routerSearchPanels.forEach((panel) => {{
    panel.hidden = !open;
  }});
  routerSearchToggles.forEach((toggle) => {{
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  }});
  syncRouterSearchToggleState();
  if (open && options.focusInput !== false) {{
    const preferredInput = options.preferredInput && routerSearchInputs.includes(options.preferredInput)
      ? options.preferredInput
      : (lastRouterSearchTrigger === mobileRouterSearchToggle ? mobileRouterSearchInput : routerSearchInput) || routerSearchInputs[0];
    if (preferredInput) {{
      window.requestAnimationFrame(() => preferredInput.focus());
    }}
  }}
}}

function routerMatchesSearch(router, query) {{
  if (!query) return true;
  const sample = normalizeRouterSearch([
    router && router.name,
    router && router.id,
    router && router.status && router.status.model,
    router && router.status && router.status.board
  ].filter(Boolean).join(' '));
  return sample.includes(query);
}}

function filteredRouters(list = window.ROUTERS || []) {{
  const query = normalizeRouterSearch(routerSearchQuery);
  if (!query) return list;
  return list.filter((router) => routerMatchesSearch(router, query));
}}

function updateRouterSearchMeta(total, visible) {{
  if (!routerSearchMetas.length) return;
  const text = !routerSearchQuery.trim()
    ? `Всего роутеров: ${{total}}`
    : (visible ? `Найдено: ${{visible}} из ${{total}}` : 'Ничего не найдено');
  routerSearchMetas.forEach((meta) => {{
    meta.textContent = text;
  }});
}}

function numericMetric(value) {{
  const match = String(value ?? '').replace(',', '.').match(/-?\\d+(?:\\.\\d+)?/);
  return match ? Number(match[0]) : NaN;
}}

function memoryUsagePercent(value) {{
  const text = String(value || '');
  const matchKb = text.match(/(\\d+)\\s*\\/\\s*(\\d+)\\s*kB/i);
  if (matchKb) {{
    const freeKb = Number(matchKb[1]);
    const totalKb = Number(matchKb[2]);
    if (Number.isFinite(freeKb) && Number.isFinite(totalKb) && totalKb > 0) {{
      return Math.max(0, Math.min(100, (totalKb - freeKb) * 100 / totalKb));
    }}
  }}
  const numbers = text.match(/\\d+(?:\\.\\d+)?/g) || [];
  if (numbers.length >= 2) {{
    const used = Number(numbers[0]);
    const total = Number(numbers[1]);
    if (Number.isFinite(used) && Number.isFinite(total) && total > 0) {{
      return Math.max(0, Math.min(100, used * 100 / total));
    }}
  }}
  return NaN;
}}

function flashUsagePercent(value) {{
  const text = String(value || '');
  if (/mounted\s+used/i.test(text)) return NaN;
  const explicit = text.match(/(\\d+(?:\\.\\d+)?)\\s*%\\s*used/i);
  if (explicit) return Number(explicit[1]);
  const numbers = text.match(/\\d+(?:\\.\\d+)?/g) || [];
  if (numbers.length >= 2) {{
    const free = Number(numbers[0]);
    const total = Number(numbers[1]);
    if (Number.isFinite(free) && Number.isFinite(total) && total > 0) {{
      return Math.max(0, Math.min(100, 100 - free * 100 / total));
    }}
  }}
  return NaN;
}}

function flashMetricLooksBroken(value) {{
  const text = String(value || '').trim();
  if (!text) return false;
  if (/mounted\s+used/i.test(text)) return true;
  return /^0(?:\\.0+)?\\s*[KMGTP]?B\\s*free\\s*\\/\\s*0(?:\\.0+)?\\s*[KMGTP]?B/i.test(text);
}}

function flashValueForRouter(router) {{
  const raw = String(router && router.status && router.status.flash || '').trim();
  const text = raw.toLowerCase();
  if (raw && text !== 'unknown' && !flashMetricLooksBroken(raw)) return raw;
  if (router && router.online && flashMetricLooksBroken(raw)) return 'system flash unavailable';
  return raw || 'unknown';
}}

function pushUnique(items, text) {{
  if (!text) return;
  if (!items.includes(text)) items.push(text);
}}

function containsAny(text, values) {{
  return values.some((value) => text.includes(value));
}}

function normalizeDiagnosticText(value) {{
  const allowed = 'abcdefghijklmnopqrstuvwxyzабвгдежзийклмнопрстуфхцчшщъыьэюя0123456789%./:-';
  let text = String(value || '').toLowerCase().split('ё').join('е');
  let cleaned = '';
  for (const ch of text) {{
    const code = ch.charCodeAt(0);
    if (code === 13 || code === 10 || code === 9) {{
      cleaned += ' ';
    }} else if (allowed.includes(ch)) {{
      cleaned += ch;
    }} else {{
      cleaned += ' ';
    }}
  }}
  return cleaned.split(' ').filter(Boolean).join(' ').trim();
}}

function keywordScore(text, keywords) {{
  let score = 0;
  keywords.forEach((keyword) => {{
    const sample = normalizeDiagnosticText(keyword);
    if (!sample) return;
    if (text.includes(sample)) score += sample.length >= 7 ? 2 : 1;
  }});
  return score;
}}

function isPanelRelatedDiagnosticText(text) {{
  const sample = String(text || '');
  const relatedTerms = [
    'панел', 'хаб', 'hub', 'роут', 'router', 'openwrt', 'xray', 'ssh', 'dropbear',
    'vps', 'nginx', 'wan', 'heartbeat', 'интернет', 'терминал', 'web terminal',
    'web-terminal', 'shell', 'console', 'pty', 'socket', 'туннел', 'vpn', 'vless',
    'reality', 'homeproxy', 'passwall', 'nekobox', 'конфиг', 'config', 'cfg',
    'кнопк', 'карточ', 'статус', 'status', 'метрик', 'температур', 'памят',
    'flash', 'ram', 'cpu', 'логи', 'logread', 'journalctl', 'systemctl', 'ping',
    'порт', 'port', 'доступ', 'подключ', 'авторизац', 'api', 'json', 'fetch',
    'network', 'console', 'devtools', 'браузер', 'страниц', 'экран', '502',
    '403', '404', '500', 'timeout', 'offline', 'online', 'heartbeat', 'drop',
    'restart', 'reload', 'обнов', 'завис', 'не работ', 'не открыва', 'не груз'
  ];
  const hits = relatedTerms.reduce((count, term) => count + (sample.includes(term) ? 1 : 0), 0);
  return hits >= 1;
}}

function numberedLines(items) {{
  return items.filter(Boolean).map((item, index) => (index + 1) + '. ' + item).join('\\n');
}}

function looksLikeShellCommand(text) {{
  const sample = String(text || '').trim();
  if (!sample) return false;
  if (/^(LuCI|DevTools)\s*:/i.test(sample)) return false;
  return /^(?:\/etc\/init\.d\/|systemctl|journalctl|ss|scp|cp|chmod|logread|pgrep|ping|grep|curl|wget|cat|tail|head|ps|ip|uci|ubus|opkg|service|netstat|sh|ash|bash|python|node|nginx|xray)\b/i.test(sample)
    || sample.includes(' | ')
    || sample.includes(' --');
}}

function renderDiagnosticBody(body) {{
  return String(body || '').split('\\n').map((rawLine) => {{
    const line = rawLine.trim();
    if (!line) return '';
    const numbered = line.match(/^(\\d+\\.)\\s+(.*)$/);
    const prefix = numbered ? numbered[1] : '';
    const content = numbered ? numbered[2] : line;
    const withLabel = content.match(/^(.*?:)\\s+(.+)$/);
    if (withLabel && looksLikeShellCommand(withLabel[2])) {{
      return `<div class="diagCmdLine">
        <div class="diagCmdLabel">${{escapeHtml(prefix ? prefix + ' ' + withLabel[1] : withLabel[1])}}</div>
        <div class="diagCmdRow">
          <pre class="diagCode">${{escapeHtml(withLabel[2])}}</pre>
          <button type="button" class="diagCopyBtn" data-copy="${{escapeAttr(withLabel[2])}}" title="Копировать команду" aria-label="Копировать команду">
            <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
              <rect x="9" y="9" width="10" height="10" rx="2" fill="none" stroke="currentColor" stroke-width="2"></rect>
              <rect x="5" y="5" width="10" height="10" rx="2" fill="none" stroke="currentColor" stroke-width="2"></rect>
            </svg>
            <span>Copy</span>
          </button>
        </div>
      </div>`;
    }}
    if (looksLikeShellCommand(content)) {{
      return `<div class="diagCmdLine">
        ${{prefix ? `<div class="diagCmdLabel">${{escapeHtml(prefix)}}</div>` : ''}}
        <div class="diagCmdRow">
          <pre class="diagCode">${{escapeHtml(content)}}</pre>
          <button type="button" class="diagCopyBtn" data-copy="${{escapeAttr(content)}}" title="Копировать команду" aria-label="Копировать команду">
            <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
              <rect x="9" y="9" width="10" height="10" rx="2" fill="none" stroke="currentColor" stroke-width="2"></rect>
              <rect x="5" y="5" width="10" height="10" rx="2" fill="none" stroke="currentColor" stroke-width="2"></rect>
            </svg>
            <span>Copy</span>
          </button>
        </div>
      </div>`;
    }}
    return `<div class="diagTextLine">${{escapeHtml(line)}}</div>`;
  }}).join('');
}}

async function copyDiagnosticCommand(btn) {{
  const text = String(btn && btn.dataset && btn.dataset.copy || '');
  if (!text) return;
  let ok = false;
  try {{
    await navigator.clipboard.writeText(text);
    ok = true;
  }} catch (err) {{
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', 'readonly');
    area.style.position = 'fixed';
    area.style.left = '-1000px';
    document.body.appendChild(area);
    area.select();
    try {{
      ok = document.execCommand('copy');
    }} catch (copyErr) {{}}
    area.remove();
  }}
  if (!ok) return;
  btn.classList.add('copied');
  btn.title = 'Скопировано';
  btn.setAttribute('aria-label', 'Скопировано');
  clearTimeout(Number(btn.dataset.copyTimer || 0));
  btn.dataset.copyTimer = String(setTimeout(() => {{
    btn.classList.remove('copied');
    btn.title = 'Копировать команду';
    btn.setAttribute('aria-label', 'Копировать команду');
    delete btn.dataset.copyTimer;
  }}, 1200));
}}

function genericPanelAdvice(router) {{
  const entryPort = Number(router && router.entry_port || 0);
  const sshEntryPort = Number(router && router.ssh_entry_port || 0);
  return numberedLines([
    'Обнови страницу через Ctrl+F5. Если карточек нет, сразу проверь в браузере запрос /api/routers и Console.',
    'На VPS выполни: systemctl status owrt-remote nginx --no-pager -l',
    'Что должно быть: у owrt-remote и nginx статус Active: active (running). Если failed, inactive или exit-code, значит сервис не запущен нормально и хаб сам не поднимется.',
    'Если панель пустая или есть 502, открой лог: journalctl -u owrt-remote -n 50 --no-pager -l',
    'Что должно быть: без Traceback и без повторяющихся error. Если есть Traceback, permission denied, 502 или address already in use, значит хаб падает при запуске или порт уже занят.',
    entryPort ? ('Если проблема в Xray, проверь порт ' + entryPort + ' на VPS: ss -lntp | grep ' + entryPort) : 'Если проблема в Xray, проверь на VPS, что нужный входной порт реально слушается через ss -lntp.',
    entryPort ? ('Что должно быть: строка LISTEN на порту ' + entryPort + '. Если вывод пустой, значит Xray на VPS не слушает этот порт.') : 'Что должно быть: в выводе ss должен быть нужный входной порт. Если вывода нет, значит Xray не открыл нужный порт.',
    sshEntryPort ? ('Если проблема в SSH, проверь внешний порт ' + sshEntryPort + ' и перезапусти dropbear на роутере: /etc/init.d/dropbear restart') : 'Если проблема в SSH, перезапусти dropbear на роутере: /etc/init.d/dropbear restart'
  ]);
}}

function detailedPanelFallback(router) {{
  const entryPort = Number(router && router.entry_port || 0);
  const sshEntryPort = Number(router && router.ssh_entry_port || 0);
  return [
    {{
      level: 'warn',
      title: 'Если проблема в самой панели хаба',
      body: numberedLines([
        'Сделай Ctrl+F5, чтобы браузер не держал старый JS и старый HTML.',
        'Если панель открывается пустой или с ошибкой, на VPS выполни: systemctl status owrt-remote nginx --no-pager -l',
        'Что должно быть: у owrt-remote и nginx статус Active: active (running). Если нет, значит сама панель или nginx сейчас не работают нормально.',
        'Открой лог панели: journalctl -u owrt-remote -n 50 --no-pager -l',
        'Что должно быть: без Traceback, permission denied и address already in use. Если такие строки есть, проблема уже внутри backend панели.'
      ])
    }},
    {{
      level: 'warn',
      title: 'Если проблема в карточке роутера, статусе или heartbeat',
      body: numberedLines([
        'Проверь, что роутер реально online и у WAN есть IP адрес в LuCI: Network -> Interfaces -> WAN.',
        'На роутере выполни: ping -c 4 1.1.1.1',
        'Что должно быть: 4 received и packet loss 0%. Если нет, роутер не сможет отправлять heartbeat и карточка будет offline или пустой.',
        'Если карточка не обновляется, заново открой OpenWrt config и вставь команды на роутер целиком.',
        'Если пропал только один роутер, сверь router id, entry port, SSH port и время последнего heartbeat.'
      ])
    }},
    {{
      level: 'warn',
      title: 'Если проблема в web-terminal или SSH',
      body: numberedLines([
        'Проверь, что dropbear включен: LuCI -> System -> Startup -> dropbear.',
        'На роутере выполни: /etc/init.d/dropbear restart',
        'Что должно быть: команда проходит без failed и SSH снова отвечает.',
        sshEntryPort ? ('На VPS проверь внешний SSH порт: ss -lntp | grep ' + sshEntryPort) : 'Проверь, что в карточке хаба вообще указан внешний SSH порт.',
        sshEntryPort ? ('Что должно быть: строка LISTEN на порту ' + sshEntryPort + '. Если пусто, внешний SSH туннель сейчас не поднят.') : 'Что должно быть: в карточке должен быть указан внешний SSH порт, иначе web-terminal не откроется.'
      ])
    }},
    {{
      level: 'warn',
      title: 'Если проблема в Xray, конфиге или VPS части',
      body: numberedLines([
        'На роутере проверь службу Xray через LuCI: Services -> HomeProxy / PassWall / Nekobox.',
        'На роутере выполни: pgrep -fa xray',
        'Что должно быть: хотя бы одна строка с процессом xray. Если пусто, Xray на роутере не запущен.',
        entryPort ? ('На VPS проверь входной порт Xray: ss -lntp | grep ' + entryPort) : 'На VPS проверь, что Xray слушает нужный входной порт через ss -lntp.',
        entryPort ? ('Что должно быть: строка LISTEN на порту ' + entryPort + '. Если пусто, VPS Xray не слушает нужный порт.') : 'Что должно быть: в выводе ss должен быть нужный порт Xray.',
        'Если после обновления конфига ничего не меняется, нажми Обновить Xray CFG и затем Рестарт Xray VPS.'
      ])
    }}
  ];
}}

function diagnosticGuides(router) {{
  const entryPort = Number(router && router.entry_port || 0);
  const sshEntryPort = Number(router && router.ssh_entry_port || 0);
  return [
    {{
      level: 'bad',
      title: 'SSH закрыт, web-terminal не открывается или доступ зависает',
      keywords: ['ssh', 'dropbear', '22 порт', '22port', 'терминал', 'web terminal', 'web-terminal', 'консоль', 'подключение ssh', 'не подключается', 'туннель ssh', 'webssh', 'shell', 'pty', 'socket', 'подвис терминал', 'не открывается терминал'],
      body: numberedLines([
        'LuCI: System -> Startup -> dropbear. Если выключен, нажми Enable и Start.',
        'Команда на роутере: /etc/init.d/dropbear restart',
        'Что должно быть: команда завершается без ошибки и SSH снова отвечает. Если видишь failed, not found или permission denied, значит dropbear не запускается нормально.',
        'Лог на роутере: logread -e dropbear',
        'Что должно быть: строки про запуск dropbear и порт 22 или внешний порт. Если есть bind, address already in use, refused или failed, значит SSH не может открыть порт или падает при старте.',
        sshEntryPort ? ('Проверь с VPS, слушается ли внешний SSH порт ' + sshEntryPort + ': ss -lntp | grep ' + sshEntryPort) : 'Проверь, что в карточке хаба вообще заполнен внешний SSH порт.',
        sshEntryPort ? ('Что должно быть: строка LISTEN на порту ' + sshEntryPort + '. Если пусто, значит внешний SSH порт на VPS сейчас не слушается.') : 'Что должно быть: в карточке хаба должен быть указан внешний SSH порт.',
        'Если web-terminal пустой, сделай Ctrl+F5 и открой его заново после перезапуска dropbear.'
      ])
    }},
    {{
      level: 'bad',
      title: 'Xray на роутере или VPS работает неправильно',
      keywords: ['xray', 'reality', 'vless', 'proxy', 'homeproxy', 'passwall', 'nekobox', 'туннель', 'vpn не работает', 'vps xray', 'не поднимается xray', 'не работает прокси', 'маршрут', 'конфиг xray'],
      body: numberedLines([
        'Нажми OpenWrt config и заново вставь команды на роутер целиком.',
        'LuCI: Services -> HomeProxy / PassWall / Nekobox. Служба должна быть в статусе Running.',
        'Команда на роутере: logread -e xray',
        'Что должно быть: строки запуска Xray без failed и panic. Если есть invalid, failed, not found, rejected или no such file, значит Xray не стартует из-за битого конфига или отсутствующего файла.',
        'Команда на роутере: pgrep -fa xray',
        'Что должно быть: хотя бы одна строка с процессом xray. Если вывод пустой, значит Xray на роутере сейчас не запущен.',
        entryPort ? ('Проверь, что нужный порт ' + entryPort + ' слушается: ss -lntp | grep ' + entryPort) : 'Проверь, что Xray слушает тот же порт, который выдал хаб.',
        entryPort ? ('Что должно быть: строка LISTEN на порту ' + entryPort + '. Если пусто, значит Xray не открыл этот порт или запущен с другим конфигом.') : 'Что должно быть: в выводе ss должен быть тот же порт, который выдал хаб.',
        'На VPS нажми Обновить Xray CFG, потом Рестарт Xray VPS.',
        'Если не помогло, открой лог на VPS: journalctl -u xray -n 50 --no-pager -l'
        ,'Что должно быть: строки started, listening или без красных ошибок. Если есть failed, rejected, bind, invalid или tls, значит VPS Xray не поднялся или не может принять соединения.'
      ])
    }},
    {{
      level: 'warn',
      title: 'Роутер не выходит в интернет, offline или не отправляет heartbeat',
      keywords: ['нет интернета', 'интернет', 'wan', 'dns', 'offline', 'heartbeat', 'timeout', 'connection reset', 'router offline', 'не в сети', 'не онлайн', 'wan не поднимается', 'pppoe', 'dhcp', 'нет связи', 'обрыв интернета'],
      body: numberedLines([
        'LuCI: Network -> Interfaces -> WAN. У WAN должен быть IP адрес.',
        'Если IP пустой, нажми Restart у WAN.',
        'Команда на роутере: ping -c 4 1.1.1.1',
        'Что должно быть: 4 received и packet loss 0%. Если network unreachable, bad address или 100% packet loss, значит у роутера нет нормального выхода в интернет.',
        'Команда на роутере: logread | tail -n 50',
        'Что должно быть: последние строки без бесконечных ошибок netifd, wan, ppp, xray. Если есть timeout, failed, error, denied или restart loop, значит WAN, heartbeat или Xray зациклились на ошибке.',
        'Если интернет вернулся, подожди 20-30 секунд и обнови страницу хаба.'
      ])
    }},
    {{
      level: 'warn',
      title: 'Панель хаба открывается с ошибкой, 502 или пустым экраном',
      keywords: ['502', 'bad gateway', '403', '404', 'панель', 'хаб не открывается', 'не открывается панель', 'пустой экран', 'белый экран', 'ничего не показывает', 'кнопки не работают', 'страница пустая', 'backend', 'frontend', 'api', 'json', 'fetch', 'console', 'devtools', 'браузерная ошибка'],
      body: numberedLines([
        'Обнови страницу через Ctrl+F5.',
        'На VPS проверь сервисы: systemctl status owrt-remote nginx --no-pager -l',
        'Что должно быть: у обоих сервисов Active: active (running). Если нет, значит один из сервисов не поднялся и панель не сможет работать нормально.',
        'Если есть 502 или пустой экран, открой лог: journalctl -u owrt-remote -n 50 --no-pager -l',
        'Что должно быть: без Traceback и без падения Python. Если есть Traceback, 502, permission denied или address already in use, значит backend хаба падает или упирается в права/порт.',
        'Если шапка есть, а карточек нет, в браузере проверь DevTools -> Network -> /api/routers и DevTools -> Console.',
        'Если менялся файл хаба, после правки снова сделай Ctrl+F5.'
      ])
    }},
    {{
      level: 'warn',
      title: 'Карточка роутера пустая, роутер пропал или не появляется в списке',
      keywords: ['карточка пустая', 'пустые карточки', 'роутер пропал', 'не видно роутер', 'не появляется роутер', 'пустой список', 'нет карточек', 'карточки пропали', 'нет статуса', 'нет метрик', 'heartbeat пропал', 'не обновляется карточка'],
      body: numberedLines([
        'Проверь, что router id не пустой и роутер реально добавлен в хаб.',
        'На роутере проверь интернет: ping -c 4 1.1.1.1',
        'Что должно быть: 4 received и packet loss 0%. Если интернет не ходит, значит роутер не сможет отправить heartbeat и не появится в хабе.',
        'Открой OpenWrt config и заново вставь команды на роутер.',
        'Если карточек нет вообще, в браузере проверь запрос /api/routers и Console.',
        'Если не виден только один роутер, сверь router id, entry port и SSH port.'
      ])
    }},
    {{
      level: 'warn',
      title: 'OpenWrt config, кнопки панели или обновление конфига работают не так',
      keywords: ['openwrt config', 'конфиг', 'cfg', 'команды', 'добавить роутер', 'не сохраняется', 'не обновляется', 'обновить xray cfg', 'кнопка', 'не нажимается', 'не срабатывает', 'копировать команду', 'модалка', 'popup', 'не открывается окно', 'быстрые команды'],
      body: numberedLines([
        'Сделай Ctrl+F5, чтобы убрать старый JS из браузера.',
        'Если проблема в OpenWrt config, скопируй команды заново и вставь их целиком.',
        'После замены конфига нажми Обновить Xray CFG и Рестарт Xray VPS.',
        'Если кнопка нажимается, но ничего не происходит, в браузере проверь DevTools -> Network и Console.',
        'Если запрос падает, проверь лог на VPS: journalctl -u owrt-remote -n 50 --no-pager -l'
        ,'Что должно быть: запросы без 500 и без Traceback. Если видишь 4xx/5xx или ошибку в Console, значит проблема уже в самом хабе, API или фронтенде браузера.'
      ])
    }},
    {{
      level: 'warn',
      title: 'Роутер перегрет, забита память или закончилось место',
      keywords: ['температура', 'горячий', 'греется', 'перегрев', 'память', 'ram', 'flash', 'место', 'storage', 'cpu', 'тормозит', 'виснет'],
      body: numberedLines([
        'Посмотри метрики температуры, RAM и flash прямо в карточке роутера.',
        'Если температура высокая, убери роутер из закрытого места, проверь питание и охлаждение.',
        'Если память RAM почти забита, выключи лишние службы и перезапусти Xray только после этого.',
        'Если flash почти закончилось, удали старые логи и ненужные пакеты, иначе новые конфиги и статусы могут не сохраняться.',
        'После разгрузки роутера снова обнови heartbeat и запусти диагностику повторно.'
      ])
    }}
  ];
}}

function noviceProblemDetails(problemText, router) {{
  const text = normalizeDiagnosticText(problemText);
  const matches = diagnosticGuides(router)
    .map((guide) => ({{guide, score: keywordScore(text, guide.keywords || [])}}))
    .filter((item) => item.score > 0)
    .sort((left, right) => right.score - left.score)
    .slice(0, 3)
    .map((item) => ({{
      level: item.guide.level || 'warn',
      title: item.guide.title || 'Диагностика',
      body: item.guide.body || ''
    }}));
  if (matches.length) return matches;
  if (!text) return [];
  if (!isPanelRelatedDiagnosticText(text)) {{
    return [{{
      level: 'bad',
      title: 'Вопрос не распознан',
      body: 'Введи вопрос, связанный с панелью, роутером, SSH, Xray, интернетом или кнопками хаба.\\nПример: не открывается web-terminal, роутер offline, Xray не запущен, кнопка OpenWrt config не работает.'
    }}];
  }}
  return [
    {{
      level: 'warn',
      title: 'Куда смотреть дальше',
      body: genericPanelAdvice(router)
    }},
    ...detailedPanelFallback(router)
  ];
}}

function diagnoseRouter(router, draft) {{
  const works = [];
  const problems = [];
  const checks = [];
  const blocks = [];
  const online = Boolean(router && router.online);
  const xray = String(router && router.status && router.status.xray || 'unknown').toLowerCase();
  const ssh = String(router && router.status && router.status.ssh || 'unknown').toLowerCase();
  const tempValue = numericMetric(router && router.status && router.status.temperature);
  const memoryPercent = memoryUsagePercent(router && router.status && router.status.memory);
  const flashPercent = flashUsagePercent(router && router.status && router.status.flash);
  const allProblemText = [draft.problems, draft.tried].filter(Boolean).join(' ').toLowerCase();

  if (online) pushUnique(works, 'Роутер выходит на связь с хабом.');
  else pushUnique(problems, 'Хаб не видит роутер онлайн. Обычно это значит, что нет интернета, не уходит heartbeat или роутер выключен.');

  if (xray === 'running') pushUnique(works, 'Xray на роутере запущен.');
  else if (xray === 'enabled') pushUnique(checks, 'Xray включен, но нет подтверждения, что он реально запущен. Стоит сделать перезапуск службы.');
  else pushUnique(problems, 'Xray не запущен или роутер не прислал его статус.');

  if (ssh === 'running' && Number(router && router.ssh_entry_port || 0) > 0) {{
    pushUnique(works, 'SSH доступ должен идти через внешний порт ' + router.ssh_entry_port + '.');
  }} else if (Number(router && router.ssh_entry_port || 0) > 0) {{
    pushUnique(problems, 'SSH не подтвержден. Проверь службу dropbear и внешний порт ' + router.ssh_entry_port + '.');
  }} else {{
    pushUnique(problems, 'Для SSH не задан внешний порт. Без него удаленная проверка не откроется.');
  }}

  if (Number.isFinite(tempValue) && tempValue >= 75) {{
    pushUnique(problems, 'Температура высокая: ' + tempValue + 'C. Роутер может тупить, зависать или резать скорость.');
  }} else if (Number.isFinite(tempValue) && tempValue >= 60) {{
    pushUnique(checks, 'Температура уже повышена: ' + tempValue + 'C. Лучше проверить охлаждение.');
  }}

  if (Number.isFinite(memoryPercent) && memoryPercent >= 90) {{
    pushUnique(problems, 'Память почти забита: ' + Math.round(memoryPercent) + '%. Из-за этого Xray и другие службы могут падать.');
  }} else if (Number.isFinite(memoryPercent) && memoryPercent >= 75) {{
    pushUnique(checks, 'Память загружена примерно на ' + Math.round(memoryPercent) + '%. Если есть подвисания, начни проверку с лишних служб.');
  }}

  if (Number.isFinite(flashPercent) && flashPercent >= 85) {{
    pushUnique(problems, 'Память flash почти закончилась: ' + Math.round(flashPercent) + '% занято. Новые конфиги и логи могут не сохраняться.');
  }} else if (Number.isFinite(flashPercent) && flashPercent >= 70) {{
    pushUnique(checks, 'Flash уже заполнен примерно на ' + Math.round(flashPercent) + '%. Стоит удалить старые логи и ненужные пакеты.');
  }}

  if (draft.works) {{
    blocks.push({{
      level: 'good',
      title: 'Что работает по твоим словам',
      body: draft.works
    }});
  }}

  if (works.length) {{
    blocks.push({{
      level: 'good',
      title: 'Что уже подтверждено',
      body: works.map((item, index) => `${{index + 1}}. ${{item}}`).join('\\n')
    }});
  }}

  if (draft.problems) {{
    blocks.push({{
      level: 'bad',
      title: 'Какие проблемы ты указал',
      body: draft.problems
    }});
  }}

  if (problems.length) {{
    blocks.push({{
      level: 'bad',
      title: 'Что уже видно по данным роутера',
      body: problems.map((item, index) => `${{index + 1}}. ${{item}}`).join('\\n')
    }});
  }}

  if (draft.problems || problems.length) {{
    noviceProblemDetails(allProblemText || problems.join(' ').toLowerCase(), router).forEach((item) => blocks.push(item));
  }}

  if (checks.length) {{
    blocks.push({{
      level: 'warn',
      title: 'Что проверить сейчас',
      body: checks.map((item, index) => `${{index + 1}}. ${{item}}`).join('\\n')
    }});
  }}

  if (draft.tried) {{
    blocks.push({{
      level: 'warn',
      title: 'Что ты уже пробовал',
      body: draft.tried
    }});
  }}

  let level = 'good';
  let summary = '';
  if (problems.length) {{
    level = 'bad';
    summary = 'Нашлось проблем: ' + problems.length + '. Сначала смотри блок с проблемами, потом выполняй команды ниже.';
  }} else if (draft.problems) {{
    level = 'warn';
    summary = 'По описанию есть проблема. Ниже дал конкретные шаги и команды.';
  }} else if (checks.length) {{
    level = 'warn';
    summary = 'Критичных проблем по данным роутера не видно, но есть моменты, которые стоит проверить.';
  }} else if (works.length) {{
    level = 'good';
    summary = 'По данным роутера явных проблем не видно. Ниже показал, что уже подтверждено.';
  }}

  return {{level, summary, blocks}};
}}

function renderDiagnosticPanel(routerId = activeDiagnosticRouterId) {{
  const router = selectedRouter(routerId);
  if (!router) {{
    activeDiagnosticRouterId = '';
    diagnosticPanel.hidden = true;
    diagnosticSummary.hidden = true;
    diagnosticBlocks.innerHTML = '';
    return;
  }}
  activeDiagnosticRouterId = String(router.id || '');
  const draft = getDiagnosticDraft(activeDiagnosticRouterId);
  diagnosticTitle.textContent = 'Диагностика: ' + (router.name || router.id);
  diagnosticLead.textContent = 'Роутер: ' + router.id + '. Опиши проблему простыми словами, потом нажми "Запустить диагностику".';
  diagnosticWorks.value = draft.works || '';
  diagnosticProblems.value = draft.problems || '';
  diagnosticTried.value = draft.tried || '';
  const report = diagnoseRouter(router, draft);
  diagnosticSummary.hidden = !report.summary;
  diagnosticSummary.className = 'diagSummary ' + report.level;
  diagnosticSummary.textContent = report.summary || '';
  diagnosticBlocks.innerHTML = report.blocks.map((block) => `
    <article class="diagBlock ${{escapeAttr(block.level || 'warn')}}">
      <strong>${{escapeHtml(block.title || 'Диагностика')}}</strong>
      <div class="diagBody">${{renderDiagnosticBody(block.body || '')}}</div>
    </article>
  `).join('');
}}

function openDiagnosticPanel(routerId) {{
  const fallbackRouter = (window.ROUTERS || [])[0] || null;
  const router = selectedRouter(routerId) || (fallbackRouter ? selectedRouter(fallbackRouter.id) : null);
  if (!router) return;
  activeDiagnosticRouterId = String(router.id || '');
  diagnosticPanel.hidden = false;
  renderDiagnosticPanel(activeDiagnosticRouterId);
  diagnosticPanel.scrollIntoView({{behavior: 'smooth', block: 'start'}});
}}

function closeDiagnosticPanel() {{
  diagnosticPanel.hidden = true;
}}

function refreshDiagnosticPanel() {{
  if (!activeDiagnosticRouterId) return;
  if (!selectedRouter(activeDiagnosticRouterId)) {{
    activeDiagnosticRouterId = '';
    diagnosticPanel.hidden = true;
    diagnosticSummary.hidden = true;
    diagnosticBlocks.innerHTML = '';
    return;
  }}
  renderDiagnosticPanel(activeDiagnosticRouterId);
}}

diagnosticBlocks.addEventListener('click', (event) => {{
  const button = event.target.closest('.diagCopyBtn');
  if (!button) return;
  copyDiagnosticCommand(button);
}});

function ago(iso) {{
  if (!iso) return 'Никогда';
  const diff = Math.max(0, Math.floor((Date.now() - Date.parse(iso)) / 1000));
  if (diff < 60) return diff + ' сек назад';
  if (diff < 3600) return Math.floor(diff / 60) + ' мин назад';
  return Math.floor(diff / 3600) + ' ч назад';
}}

function duration(seconds) {{
  let value = Number(seconds || 0);
  if (!value) return 'Неизвестно';
  const days = Math.floor(value / 86400);
  value %= 86400;
  const hours = Math.floor(value / 3600);
  value %= 3600;
  const mins = Math.floor(value / 60);
  if (days) return days + ' д ' + hours + ' ч';
  if (hours) return hours + ' ч ' + mins + ' мин';
  return mins + ' мин';
}}

function metricPlaceholder(value) {{
  const raw = String(value || '').trim();
  const text = raw.toLowerCase();
  if (!raw || text === 'unknown') return 'Неизвестно';
  if (text === 'never') return 'Никогда';
  if (text === 'waiting heartbeat') return 'Ожидаем первый отклик';
  return raw;
}}

function metric(label, value, cls = '') {{
  return `<div class="metric ${{cls}}"><span>${{escapeHtml(label)}}</span><strong>${{escapeHtml(metricPlaceholder(value))}}</strong></div>`;
}}

function metricHtml(label, valueHtml, cls = '') {{
  return `<div class="metric ${{cls}}"><span>${{escapeHtml(label)}}</span><strong>${{valueHtml || escapeHtml('Неизвестно')}}</strong></div>`;
}}

function formatTemperatureHtml(value) {{
  const raw = String(value || '').trim();
  const text = raw.toLowerCase();
  const match = raw.replace(',', '.').match(/-?\\d+(\\.\\d+)?/);
  if (match) return escapeHtml(raw);
  if (!raw || text === 'unknown') return escapeHtml(metricPlaceholder(value));
  if (/(sensor|thermal|temp).*?(absent|missing|not found|unavailable)|absent.*sensor|no sensor/i.test(raw)) {{
    return escapeHtml('Недоступно\\nвнешний датчик отсутствует');
  }}
  return escapeHtml(metricPlaceholder(value));
}}

function tempClass(value) {{
  const raw = String(value || '').trim();
  const text = raw.toLowerCase();
  const n = Number(raw.replace(',', '.').match(/-?\\d+(\\.\\d+)?/)?.[0]);
  if (!raw || text === 'unknown') return 'metric-compact';
  if (/(sensor|thermal|temp).*?(absent|missing|not found|unavailable)|absent.*sensor|no sensor/i.test(raw)) {{
    return 'temp-unavailable metric-compact';
  }}
  if (!Number.isFinite(n)) return 'temp-unavailable metric-compact';
  if (n >= 75) return 'temp-bad';
  if (n >= 60) return 'temp-warn';
  return 'temp-ok';
}}

function routerMissingTemperatureSensor(router) {{
  const model = String(router && router.status && (router.status.model || router.status.board) || '').toLowerCase();
  const board = String(router && router.status && router.status.board || '').toLowerCase();
  const sample = (model + ' ' + board).trim();
  return /xiaomi.*mi router 3g|mi router 3g|mir3g|xiaomi,mi-router-3g|xiaomi mir3g/.test(sample);
}}

function temperatureValueForRouter(router) {{
  const raw = String(router && router.status && router.status.temperature || '').trim();
  const text = raw.toLowerCase();
  if (raw && text !== 'unknown') return raw;
  if (router && router.online && routerMissingTemperatureSensor(router)) return 'external sensor absent';
  return raw || 'unknown';
}}

function memoryClass(value) {{
  const used = memoryUsagePercent(value);
  if (!Number.isFinite(used)) return '';
  if (used >= 90) return 'memory-bad';
  if (used >= 75) return 'memory-warn';
  return 'memory-ok';
}}

function flashClass(value) {{
  const used = flashUsagePercent(value);
  if (!Number.isFinite(used)) return '';
  if (used >= 85) return 'flash-bad';
  if (used >= 70) return 'flash-warn';
  return 'flash-ok';
}}

function statusRu(value) {{
  const text = String(value || 'unknown').toLowerCase();
  const map = {{
    running: 'Запущен',
    enabled: 'Включен',
    disabled: 'Выключен',
    stopped: 'Остановлен',
    stop: 'Остановлен',
    online: 'Онлайн',
    offline: 'Оффлайн',
    unknown: 'Неизвестно'
  }};
  return map[text] || value || 'Неизвестно';
}}

function formatMemory(value) {{
  const text = String(value || '');
  const match = text.match(/(\\d+)\\s*\\/\\s*(\\d+)\\s*kB/i);
  if (!match) return metricPlaceholder(value);
  const freeKb = Number(match[1]);
  const totalKb = Number(match[2]);
  const free = Math.max(0, Math.round(freeKb / 1024));
  const used = Math.max(0, Math.round((totalKb - freeKb) / 1024));
  const total = Math.max(0, Math.round(totalKb / 1024));
  return 'Свободно: ' + free + ' МБ\\nЗанято: ' + used + ' МБ\\nВсего: ' + total + ' МБ';
}}

function formatFlash(value) {{
  const text = String(value || '');
  if (/system flash unavailable/i.test(text) || flashMetricLooksBroken(text)) {{
    return 'Системная flash недоступна';
  }}
  const match = text.match(/(\\d+(?:\\.\\d+)?)\\s*([KMGTP]?B)\\s*free\\s*\\/\\s*(\\d+(?:\\.\\d+)?)\\s*([KMGTP]?B)/i);
  if (!match) return metricPlaceholder(value);
  const free = Number(match[1]);
  const freeUnit = String(match[2] || 'MB').toUpperCase();
  const total = Number(match[3]);
  const totalUnit = String(match[4] || freeUnit).toUpperCase();
  if (!Number.isFinite(free) || !Number.isFinite(total) || total <= 0) return metricPlaceholder(value);
  const used = Math.max(0, total - free);
  const ruUnit = (unit) => unit.replace('KB', 'КБ').replace('MB', 'МБ').replace('GB', 'ГБ').replace('TB', 'ТБ').replace('PB', 'ПБ');
  const show = (num) => Math.abs(num - Math.round(num)) < 0.05 ? String(Math.round(num)) : num.toFixed(1);
  return 'Свободно: ' + show(free) + ' ' + ruUnit(freeUnit) + '\\nЗанято: ' + show(used) + ' ' + ruUnit(totalUnit) + '\\nВсего: ' + show(total) + ' ' + ruUnit(totalUnit);
}}

function formatMetricBreakdownHtml(freeLabel, freeValue, usedText, totalText) {{
  return [
    `<span class="metric-line">${{escapeHtml(freeLabel)}} <span class="metric-accent">${{escapeHtml(freeValue)}}</span></span>`,
    `<span class="metric-line">${{escapeHtml(usedText)}}</span>`,
    `<span class="metric-line">${{escapeHtml(totalText)}}</span>`
  ].join('');
}}

function formatMemoryHtml(value) {{
  const text = String(value || '');
  const match = text.match(/(\\d+)\\s*\\/\\s*(\\d+)\\s*kB/i);
  if (!match) return escapeHtml(metricPlaceholder(value));
  const freeKb = Number(match[1]);
  const totalKb = Number(match[2]);
  const free = Math.max(0, Math.round(freeKb / 1024));
  const used = Math.max(0, Math.round((totalKb - freeKb) / 1024));
  const total = Math.max(0, Math.round(totalKb / 1024));
  return formatMetricBreakdownHtml(
    'Свободно:',
    free + ' МБ',
    'Занято: ' + used + ' МБ',
    'Всего: ' + total + ' МБ'
  );
}}

function formatFlashHtml(value) {{
  const text = String(value || '');
  if (/system flash unavailable/i.test(text) || flashMetricLooksBroken(text)) {{
    return escapeHtml('Недоступно\\nподключенный диск не считается системной flash');
  }}
  const match = text.match(/(\\d+(?:\\.\\d+)?)\\s*([KMGTP]?B)\\s*free\\s*\\/\\s*(\\d+(?:\\.\\d+)?)\\s*([KMGTP]?B)/i);
  if (!match) return escapeHtml(metricPlaceholder(value));
  const free = Number(match[1]);
  const freeUnit = String(match[2] || 'MB').toUpperCase();
  const total = Number(match[3]);
  const totalUnit = String(match[4] || freeUnit).toUpperCase();
  if (!Number.isFinite(free) || !Number.isFinite(total) || total <= 0) return escapeHtml(metricPlaceholder(value));
  const used = Math.max(0, total - free);
  const ruUnit = (unit) => unit.replace('KB', 'КБ').replace('MB', 'МБ').replace('GB', 'ГБ').replace('TB', 'ТБ').replace('PB', 'ПБ');
  const show = (num) => Math.abs(num - Math.round(num)) < 0.05 ? String(Math.round(num)) : num.toFixed(1);
  return formatMetricBreakdownHtml(
    'Свободно:',
    show(free) + ' ' + ruUnit(freeUnit),
    'Занято: ' + show(used) + ' ' + ruUnit(totalUnit),
    'Всего: ' + show(total) + ' ' + ruUnit(totalUnit)
  );
}}

function formatLoad(value) {{
  const parts = String(value || '').match(/\\d+(?:\\.\\d+)?/g) || [];
  if (parts.length >= 3) {{
    return '1 мин: ' + parts[0] + ' · 5 мин: ' + parts[1] + ' · 15 мин: ' + parts[2];
  }}
  return value || 'Неизвестно';
}}

function render(list) {{
  syncExpandedActionPanels(list);
  if (!list.length) {{
    cards.innerHTML = '<div class="empty">Пока нет роутеров. Добавь первый, например <b>main</b>.</div>';
    return;
  }}
  cards.innerHTML = list.map(r => {{
    const role = String(r.role || 'node');
    const isMain = role === 'main';
    const online = Boolean(r.online);
    const stateClass = online ? 'on' : 'off';
    const stateText = online ? 'Онлайн' : 'Оффлайн';
    const model = (r.status && (r.status.model || r.status.board)) || 'OpenWrt';
    const release = metricPlaceholder((r.status && r.status.release) || 'waiting heartbeat');
    const xray = (r.status && r.status.xray) || 'unknown';
    const ssh = (r.status && r.status.ssh) || 'unknown';
    const uptime = r.status && r.status.uptime ? duration(r.status.uptime) : 'unknown';
    const load = formatLoad(metricPlaceholder((r.status && r.status.load) || 'unknown'));
    const memory = formatMemory((r.status && r.status.memory) || 'unknown');
    const flash = flashValueForRouter(r);
    const flashDisplay = formatFlash(flash);
    const temperature = temperatureValueForRouter(r);
    const access = r.access_url || r.public_url;
    const adminButton = online
      ? `<a class="btn" href="${{escapeAttr(access)}}">Админка</a>`
      : `<span class="btn disabled">Админка</span>`;
    const sshReady = online && ssh === 'running' && Number(r.ssh_entry_port || 0) > 0;
    const sshButton = sshReady
      ? `<a class="btn" href="${{escapeAttr(r.ssh_url || ('/ssh/' + encodeURIComponent(r.id) + '/'))}}" target="_blank" rel="noopener noreferrer">SSH</a>`
      : `<span class="btn disabled">SSH</span>`;
    const wolReady = routerSupportsWol(r);
    const wolState = getWolState(r.id);
    const wolButton = wolReady
      ? `<button class="btn" data-wol-toggle="${{escapeAttr(r.id)}}" type="button">${{wolState.open ? 'Скрыть Wake-on-LAN' : 'Wake-on-LAN'}}</button>`
      : `<span class="btn disabled">Wake-on-LAN</span>`;
    const renameLabel = 'Переименовать ' + (r.name || r.id || 'роутер');
    const renameButton = `<button class="nameEditBtn" type="button" data-rename="${{escapeAttr(r.id)}}" aria-label="${{escapeAttr(renameLabel)}}" title="${{escapeAttr(renameLabel)}}"><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path fill="currentColor" d="M3 17.25V21h3.75l11-11.03-3.75-3.75zm17.71-10.04a1.003 1.003 0 0 0 0-1.42l-2.5-2.5a1.003 1.003 0 0 0-1.42 0l-1.96 1.96 3.75 3.75z"/></svg></button>`;
    const actionsId = actionPanelId(r.id);
    const actionsOpen = expandedActionPanels.has(actionsId);
    const metricsHtml = [
      metric('Модель', model, 'span2'),
      metric('Система', release),
      metric('Xray', statusRu(xray)),
      metric('SSH', statusRu(ssh)),
      metric('В сети уже', uptime),
      metric('Был на связи', ago(r.last_seen_iso)),
      metricHtml('Оперативная память', formatMemoryHtml((r.status && r.status.memory) || 'unknown'), memoryClass((r.status && r.status.memory) || 'unknown') + ' metric-compact'),
      metricHtml('Системная Flash', formatFlashHtml(flash), flashClass(flash) + ' metric-compact metric-flash'),
      metricHtml('Температура', formatTemperatureHtml(temperature), tempClass(temperature)),
      metric('Нагрузка', load, 'span2')
    ].join('');
    return `<article class="card ${{isMain ? 'main' : ''}} ${{online ? 'online' : 'off'}}">
      <div class="cardTop">
        <div class="status ${{stateClass}}"><i></i>${{stateText}}</div>
      </div>
      <div class="nameRow"><div class="name">${{escapeHtml(r.name)}}</div>${{renameButton}}</div>
      <button class="mobileToggle" type="button" data-card-toggle="${{escapeAttr(detailsId)}}" aria-expanded="${{collapseCards ? 'false' : 'true'}}">${{collapseCards ? 'Открыть' : 'Скрыть'}}</button>
      <div class="cardBody" id="${{escapeAttr(detailsId)}}"${{collapseCards ? ' hidden' : ''}}>
      <div class="metrics">
        ${{metricsHtml}}
      </div>
      <button class="actionToggle primary" type="button" data-actions-toggle="${{escapeAttr(actionsId)}}" aria-expanded="${{actionsOpen ? 'true' : 'false'}}">${{actionsOpen ? 'Скрыть действия' : 'Открыть действия'}}</button>
      <div class="actions mobileCollapsed${{actionsOpen ? ' open' : ''}}" id="${{escapeAttr(actionsId)}}">
        ${{adminButton}}
        ${{sshButton}}
        ${{wolButton}}
        <a class="btn" href="${{escapeAttr(r.config_url)}}">OpenWrt config</a>
        <a class="btn" href="${{escapeAttr(r.xray_client_url)}}">Client JSON</a>
        <button class="btn" data-diagnose="${{escapeAttr(r.id)}}" type="button">Запустить диагностику</button>
        <button class="btn" data-delete="${{escapeAttr(r.id)}}">Удалить</button>
      </div>
      ${{wolReady ? renderWolPanelRich(r) : ''}}
      </div>
    </article>`;
  }}).join('');
  syncActionToggleStates();
}}

function escapeHtml(s) {{
  return String(s ?? '').replace(/[&<>"']/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));
}}

function escapeAttr(s) {{
  return escapeHtml(s);
}}

function normalizeRouterName(value) {{
  return String(value || '').trim().replace(/\s+/g, ' ').slice(0, 80);
}}

function renderRouterStats(list) {{
  const total = list.length;
  const online = list.filter(r => Boolean(r.online)).length;
  const offlineRouters = list.filter(r => !Boolean(r.online));
  const offline = offlineRouters.length;
  const showOfflineDetails = offline > 1;
  if (!showOfflineDetails) offlineStatsExpanded = false;
  const offlineHint = !offline
    ? 'ждём связь'
    : offlineRouters.map(r => String(r.name || r.id || 'unknown')).join(' | ');
  const offlineItems = offlineRouters.map(r => `<div class="offlineItem"><span class="offlineName">${{escapeHtml(r.name || r.id || 'unknown')}}</span></div>`).join('');
  const offlineMeta = !offline
    ? 'ждём связь'
    : showOfflineDetails
      ? 'список роутеров'
      : String((offlineRouters[0] && (offlineRouters[0].name || offlineRouters[0].id)) || 'unknown');
  const offlineCard = `<article class="statCard offline${{showOfflineDetails ? ' has-popover' : ''}}" data-offline-card>
    <div class="statCardHead">
      <span>Не в сети</span>
    </div>
    <div class="offlineStatRow">
      <strong>${{offline}}</strong>
      ${{showOfflineDetails ? `<button class="offlineMoreBtn" type="button" data-offline-toggle aria-expanded="${{offlineStatsExpanded ? 'true' : 'false'}}">Подробно</button>` : ''}}
    </div>
    <em title="${{escapeAttr(offlineHint)}}">${{escapeHtml(offlineMeta)}}</em>
    ${{showOfflineDetails ? `<div class="offlinePopover" data-offline-popover${{offlineStatsExpanded ? '' : ' hidden'}}><div class="offlineList">${{offlineItems}}</div></div>` : ''}}
  </article>`;
  routerStats.innerHTML = [
    `<article class="statCard total"><div class="statCardHead"><span>Всего роутеров</span></div><div class="statValueRow"><strong>${{total}}</strong></div><em>в хабе</em></article>`,
    `<article class="statCard online"><div class="statCardHead"><span>В сети</span></div><div class="statValueRow"><strong>${{online}}</strong></div><em>heartbeat активен</em></article>`,
    offlineCard
  ].join('');
}}

render = function(list) {{
  syncExpandedActionPanels(list);
  if (!list.length) {{
    cards.innerHTML = '<div class="empty">Пока нет роутеров. Добавь первый, например <b>main</b>.</div>';
    return;
  }}
  cards.innerHTML = list.map(r => {{
    const role = String(r.role || 'node');
    const isMain = role === 'main';
    const online = Boolean(r.online);
    const stateClass = online ? 'on' : 'off';
    const stateText = online ? 'Онлайн' : 'Оффлайн';
    const model = (r.status && (r.status.model || r.status.board)) || 'OpenWrt';
    const release = metricPlaceholder((r.status && r.status.release) || 'waiting heartbeat');
    const xray = (r.status && r.status.xray) || 'unknown';
    const ssh = (r.status && r.status.ssh) || 'unknown';
    const uptime = r.status && r.status.uptime ? duration(r.status.uptime) : 'unknown';
    const load = formatLoad(metricPlaceholder((r.status && r.status.load) || 'unknown'));
    const memory = formatMemory((r.status && r.status.memory) || 'unknown');
    const flash = flashValueForRouter(r);
    const flashDisplay = formatFlash(flash);
    const temperature = temperatureValueForRouter(r);
    const access = r.access_url || r.public_url;
    const adminButton = online
      ? `<a class="btn" href="${{escapeAttr(access)}}">Админка</a>`
      : `<span class="btn disabled">Админка</span>`;
    const sshReady = online && ssh === 'running' && Number(r.ssh_entry_port || 0) > 0;
    const sshButton = sshReady
      ? `<a class="btn" href="${{escapeAttr(r.ssh_url || ('/ssh/' + encodeURIComponent(r.id) + '/'))}}" target="_blank" rel="noopener noreferrer">SSH</a>`
      : `<span class="btn disabled">SSH</span>`;
    const wolReady = routerSupportsWol(r);
    const wolState = getWolState(r.id);
    const wolButton = wolReady
      ? `<button class="btn" data-wol-toggle="${{escapeAttr(r.id)}}" type="button">${{wolState.open ? 'Скрыть Wake-on-LAN' : 'Wake-on-LAN'}}</button>`
      : `<span class="btn disabled">Wake-on-LAN</span>`;
    const renameLabel = 'Переименовать ' + (r.name || r.id || 'роутер');
    const renameButton = `<button class="nameEditBtn" type="button" data-rename="${{escapeAttr(r.id)}}" aria-label="${{escapeAttr(renameLabel)}}" title="${{escapeAttr(renameLabel)}}"><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path fill="currentColor" d="M3 17.25V21h3.75l11-11.03-3.75-3.75zm17.71-10.04a1.003 1.003 0 0 0 0-1.42l-2.5-2.5a1.003 1.003 0 0 0-1.42 0l-1.96 1.96 3.75 3.75z"/></svg></button>`;
    const actionsId = actionPanelId(r.id);
    const actionsOpen = expandedActionPanels.has(actionsId);
    const metricsHtml = [
      metric('Модель', model, 'span2'),
      metric('Система', release),
      metric('Xray', statusRu(xray)),
      metric('SSH', statusRu(ssh)),
      metric('В сети уже', uptime),
      metric('Был на связи', ago(r.last_seen_iso)),
      metricHtml('Оперативная память', formatMemoryHtml((r.status && r.status.memory) || 'unknown'), memoryClass((r.status && r.status.memory) || 'unknown') + ' metric-compact'),
      metricHtml('Системная Flash', formatFlashHtml(flash), flashClass(flash) + ' metric-compact metric-flash'),
      metricHtml('Температура', formatTemperatureHtml(temperature), tempClass(temperature)),
      metric('Нагрузка', load, 'span2')
    ].join('');
    return `<article class="card ${{isMain ? 'main' : ''}} ${{online ? 'online' : 'off'}}">
      <div class="cardTop">
        <div class="status ${{stateClass}}"><i></i>${{stateText}}</div>
      </div>
      <div class="nameRow"><div class="name">${{escapeHtml(r.name)}}</div>${{renameButton}}</div>
      <div class="metrics">
        ${{metricsHtml}}
      </div>
      <button class="actionToggle primary" type="button" data-actions-toggle="${{escapeAttr(actionsId)}}" aria-expanded="${{actionsOpen ? 'true' : 'false'}}">${{actionsOpen ? 'Скрыть действия' : 'Открыть действия'}}</button>
      <div class="actions mobileCollapsed${{actionsOpen ? ' open' : ''}}" id="${{escapeAttr(actionsId)}}">
        ${{adminButton}}
        ${{sshButton}}
        ${{wolButton}}
        <a class="btn" href="${{escapeAttr(r.config_url)}}">OpenWrt config</a>
        <a class="btn" href="${{escapeAttr(r.xray_client_url)}}">Client JSON</a>
        <button class="btn" data-diagnose="${{escapeAttr(r.id)}}" type="button">Запустить диагностику</button>
        <button class="btn" data-delete="${{escapeAttr(r.id)}}">Удалить</button>
      </div>
      ${{wolReady ? renderWolPanelRich(r) : ''}}
    </article>`;
  }}).join('');
  syncActionToggleStates();
}};

function renderRouterView() {{
  const allRouters = window.ROUTERS || [];
  const visibleRouters = filteredRouters(allRouters);
  renderRouterStats(allRouters);
  updateRouterSearchMeta(allRouters.length, visibleRouters.length);
  if (!visibleRouters.length) {{
    cards.innerHTML = routerSearchQuery.trim()
      ? '<div class="empty">По этому запросу роутеры не найдены. Попробуй имя, router id или часть модели.</div>'
      : '<div class="empty">Пока нет роутеров. Добавь первый, например <b>main</b>.</div>';
    return;
  }}
  render(visibleRouters);
}}

async function loadWolDevices(routerId, force = false) {{
  const key = String(routerId || '');
  const router = selectedRouter(key);
  if (!router) return;
  const state = getWolState(key);
  if (state.loading || (state.loaded && !force)) return;
  setWolState(key, {{loading: true, error: '', message: ''}});
  renderRouterView();
  try {{
    const res = await fetch('/api/router/' + encodeURIComponent(key) + '/wol/devices', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{
        ssh_password: state.sshPassword || ''
      }})
    }});
    const data = await res.json().catch(() => ({{ok: false, error: 'bad json', devices: []}}));
    if (!res.ok || !data.ok) {{
      throw new Error(data.error || 'Не удалось получить список устройств');
    }}
    const devices = Array.isArray(data.devices) ? data.devices : [];
    setWolState(key, {{
      loaded: true,
      loading: false,
      devices,
      error: '',
      message: devices.length ? `Список обновлен: ${{devices.length}} устройств.` : 'Список обновлен, но устройства не найдены.'
    }});
  }} catch (err) {{
    setWolState(key, {{
      loaded: false,
      loading: false,
      devices: [],
      error: err && err.message ? err.message : 'Не удалось получить список устройств'
    }});
  }}
  renderRouterView();
}}

async function wakeSelectedDevice(routerId) {{
  const key = String(routerId || '');
  const state = getWolState(key);
  const selected = (Array.isArray(state.devices) ? state.devices : []).find((device) => String(device.mac || '') === String(state.selectedMac || ''));
  if (!selected) {{
    setWolState(key, {{error: 'Сначала выбери устройство из списка.', message: ''}});
    renderRouterView();
    return;
  }}
  setWolState(key, {{waking: true, error: '', message: ''}});
  renderRouterView();
  try {{
    const res = await fetch('/api/router/' + encodeURIComponent(key) + '/wol', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{
        mac: selected.mac || '',
        iface: selected.iface || '',
        ssh_password: state.sshPassword || ''
      }})
    }});
    const data = await res.json().catch(() => ({{ok: false, error: 'bad json'}}));
    if (!res.ok || !data.ok) {{
      throw new Error(data.error || 'Не удалось отправить Wake-on-LAN пакет');
    }}
    const label = selected.name || selected.ip || selected.mac || 'устройство';
    setWolState(key, {{
      waking: false,
      error: '',
      message: `Magic packet отправлен: ${{label}}.`
    }});
  }} catch (err) {{
    setWolState(key, {{
      waking: false,
      error: err && err.message ? err.message : 'Не удалось отправить Wake-on-LAN пакет'
    }});
  }}
  renderRouterView();
}}

function nextEntryPort(list) {{
  const used = new Set();
  list.forEach(r => {{
    const entry = Number(r.entry_port || 0);
    const sshEntry = Number(r.ssh_entry_port || 0);
    if (entry) used.add(entry);
    if (sshEntry) used.add(sshEntry);
  }});
  let port = 18080;
  while (used.has(port) || used.has(port + 1000)) port += 10;
  return port;
}}

function nextRouterId(list) {{
  const used = new Set(list.map(r => String(r.id || '')));
  if (!used.has('main')) return 'main';
  let idx = 2;
  while (used.has('node-' + idx)) idx += 1;
  return 'node-' + idx;
}}

function defaultRouterName(id) {{
  if (id === 'main') return 'Главный роутер';
  return 'Роутер ' + id;
}}

function defaultVpsHost(list) {{
  const withHost = list.find(r => r.vps_host);
  return withHost ? withHost.vps_host : window.location.hostname;
}}

function updateMobilePanelToggle(toggle, panel, openText, closeText) {{
  if (!toggle || !panel) return;
  const mobile = mobileLayoutMq.matches;
  if (!mobile) {{
    toggle.hidden = true;
    panel.hidden = false;
    toggle.setAttribute('aria-expanded', 'true');
    return;
  }}
  toggle.hidden = false;
  const open = !panel.hidden;
  toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  toggle.textContent = open ? closeText : openText;
}}

function initMobilePanelToggle(toggle, panel, openText, closeText, closedOnMobile = true) {{
  if (!toggle || !panel) return;
  if (mobileLayoutMq.matches && closedOnMobile) {{
    panel.hidden = true;
  }}
  updateMobilePanelToggle(toggle, panel, openText, closeText);
  toggle.addEventListener('click', () => {{
    panel.hidden = !panel.hidden;
    updateMobilePanelToggle(toggle, panel, openText, closeText);
  }});
}}

function updateRouterFormToggle() {{
  if (!routerFormToggle || !routerFormWrap) return;
  const mobile = mobileLayoutMq.matches;
  if (!mobile) {{
    routerFormToggle.hidden = true;
    routerFormWrap.hidden = false;
    routerFormToggle.setAttribute('aria-expanded', 'true');
    return;
  }}
  routerFormToggle.hidden = false;
  const open = !routerFormWrap.hidden;
  routerFormToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  routerFormToggle.textContent = open ? 'Скрыть добавление' : 'Открыть добавление';
}}

function initRouterFormToggle() {{
  if (!routerFormToggle || !routerFormWrap) return;
  if (mobileLayoutMq.matches) {{
    routerFormWrap.hidden = true;
  }}
  updateRouterFormToggle();
}}

function fillRouterForm(force = false) {{
  const list = window.ROUTERS || [];
  const id = nextRouterId(list);
  if (routerIdInput && (force || !routerIdInput.value)) routerIdInput.value = id;
  if (routerNameInput && (force || !routerNameInput.value)) routerNameInput.value = defaultRouterName((routerIdInput && routerIdInput.value) || id);
  if (routerEntryPortInput && (force || !routerEntryPortInput.value)) routerEntryPortInput.value = String(nextEntryPort(list));
  if (routerVpsHostInput && (force || !routerVpsHostInput.value)) routerVpsHostInput.value = defaultVpsHost(list);
  if (routerRoleInput && (force || !routerRoleInput.value)) routerRoleInput.value = id === 'main' ? 'main' : 'node';
}}

function showRouterMsg(text, bad = false) {{
  routerMsg.hidden = false;
  routerMsg.className = bad ? 'formMsg bad' : 'formMsg';
  routerMsg.textContent = text;
}}

if (routerIdInput) {{
  routerIdInput.addEventListener('input', () => {{
    if (routerNameInput && !routerNameInput.dataset.touched) {{
      routerNameInput.value = defaultRouterName(routerIdInput.value.trim());
    }}
  }});
}}
if (routerNameInput) {{
  routerNameInput.addEventListener('input', () => {{
    routerNameInput.dataset.touched = '1';
  }});
}}
routerSearchInputs.forEach((input) => {{
  input.addEventListener('input', () => {{
    routerSearchQuery = input.value || '';
    syncRouterSearchInputs();
    syncRouterSearchClears();
    syncRouterSearchToggleState();
    renderRouterView();
  }});
}});
routerSearchClears.forEach((button) => {{
  button.addEventListener('click', () => {{
    routerSearchQuery = '';
    syncRouterSearchInputs();
    syncRouterSearchClears();
    syncRouterSearchToggleState();
    renderRouterView();
    const focusTarget = button === mobileRouterSearchClear ? mobileRouterSearchInput : routerSearchInput;
    if (focusTarget) focusTarget.focus();
  }});
}});
routerSearchToggles.forEach((toggle) => {{
  toggle.addEventListener('click', (ev) => {{
    ev.stopPropagation();
    lastRouterSearchTrigger = toggle;
    const panel = toggle === mobileRouterSearchToggle ? mobileRouterSearchPanel : routerSearchPanel;
    const input = toggle === mobileRouterSearchToggle ? mobileRouterSearchInput : routerSearchInput;
    setRouterSearchOpen(panel ? panel.hidden : true, {{preferredInput: input}});
  }});
}});
routerSearchPanels.forEach((panel) => {{
  panel.addEventListener('click', (ev) => ev.stopPropagation());
}});
document.addEventListener('click', (ev) => {{
  if (!routerSearchPanels.length || routerSearchPanels.every((panel) => panel.hidden)) return;
  if (routerSearchDocks.some((dock) => dock && dock.contains(ev.target))) return;
  setRouterSearchOpen(false, {{focusInput: false}});
}});
document.addEventListener('keydown', (ev) => {{
  if (ev.key !== 'Escape' || !routerSearchPanels.length || routerSearchPanels.every((panel) => panel.hidden)) return;
  setRouterSearchOpen(false, {{focusInput: false}});
  if (lastRouterSearchTrigger) {{
    lastRouterSearchTrigger.focus();
  }} else if (routerSearchToggle) {{
    routerSearchToggle.focus();
  }}
}});
diagnosticClose.addEventListener('click', closeDiagnosticPanel);
diagnosticWorks.addEventListener('input', () => {{
  if (!activeDiagnosticRouterId) return;
  setDiagnosticDraft(activeDiagnosticRouterId, {{works: diagnosticWorks.value}});
}});
diagnosticProblems.addEventListener('input', () => {{
  if (!activeDiagnosticRouterId) return;
  setDiagnosticDraft(activeDiagnosticRouterId, {{problems: diagnosticProblems.value}});
}});
diagnosticTried.addEventListener('input', () => {{
  if (!activeDiagnosticRouterId) return;
  setDiagnosticDraft(activeDiagnosticRouterId, {{tried: diagnosticTried.value}});
}});
diagnosticBuild.addEventListener('click', () => {{
  if (!activeDiagnosticRouterId) return;
  setDiagnosticDraft(activeDiagnosticRouterId, {{
    works: diagnosticWorks.value,
    problems: diagnosticProblems.value,
    tried: diagnosticTried.value
  }});
  renderDiagnosticPanel(activeDiagnosticRouterId);
}});
if (routerFormToggle) {{
  routerFormToggle.addEventListener('click', () => {{
    routerFormWrap.hidden = !routerFormWrap.hidden;
    updateRouterFormToggle();
  }});
}}
initRouterFormToggle();
initMobilePanelToggle(hubMenuToggle, headerActions, 'Открыть меню хаба', 'Скрыть меню хаба');
initMobilePanelToggle(routerStatsToggle, routerStats, 'Открыть статистику', 'Скрыть статистику');
document.body.classList.remove('preload-mobile-panels');

document.getElementById('xrayReload').addEventListener('click', async () => {{
  showRouterMsg('Обновляю Xray на VPS...');
  const res = await fetch('/api/xray/reload', {{method: 'POST'}});
  const text = await res.text();
  if (res.ok) {{
    let message = 'Xray VPS обновлён. Теперь кнопка Админка должна идти в свежие порты.';
    try {{
      const data = JSON.parse(text);
      message = `Xray VPS обновлён: ${{data.config}}, роутеров в конфиге: ${{data.routers}}.`;
    }} catch (e) {{}}
    showRouterMsg(message);
  }} else {{
    showRouterMsg(text || 'Не удалось обновить Xray VPS', true);
  }}
}});

document.getElementById('xrayRestart').addEventListener('click', async () => {{
  showRouterMsg('Перезапускаю Xray на VPS...');
  const res = await fetch('/api/xray/restart', {{method: 'POST'}});
  const text = await res.text();
  if (res.ok) {{
    let message = 'Xray VPS перезапущен.';
    try {{
      const data = JSON.parse(text);
      message = `Xray VPS перезапущен: ${{data.service}}.`;
    }} catch (e) {{}}
    showRouterMsg(message);
  }} else {{
    showRouterMsg(text || 'Не удалось перезапустить Xray VPS', true);
  }}
}});

async function loadRouters() {{
  const res = await fetch('/api/routers', {{cache: 'no-store'}});
  if (res.ok) {{
    const data = await res.json();
    window.ROUTERS = data.routers;
    if (hasOpenWolPicker()) pendingRouterRender = true;
    else renderRouterView();
    fillRouterForm(false);
    refreshDiagnosticPanel();
  }}
}}

routerForm.addEventListener('submit', async (ev) => {{
  ev.preventDefault();
  routerMsg.hidden = true;
  const body = new URLSearchParams(new FormData(ev.currentTarget));
  const id = String(body.get('id') || '').trim();
  const entryPort = Number(body.get('entry_port') || 0);
  const sshEntryPort = entryPort + 1000;
  const duplicateId = (window.ROUTERS || []).find(r => String(r.id) === id);
  const duplicatePort = (window.ROUTERS || []).find(r => Number(r.entry_port || 0) === entryPort || Number(r.ssh_entry_port || 0) === entryPort);
  const duplicateSshPort = (window.ROUTERS || []).find(r => Number(r.entry_port || 0) === sshEntryPort || Number(r.ssh_entry_port || 0) === sshEntryPort);
  if (duplicateId) {{
    showRouterMsg(`Router ID "${{id}}" уже есть. Для второго роутера оставь предложенный ID или напиши новый.`, true);
    return;
  }}
  if (duplicatePort) {{
    showRouterMsg(`Порт ${{entryPort}} уже занят роутером "${{duplicatePort.id}}". Поставь следующий свободный порт.`, true);
    if (routerEntryPortInput) routerEntryPortInput.value = String(nextEntryPort(window.ROUTERS || []));
    return;
  }}
  if (duplicateSshPort) {{
    showRouterMsg(`SSH-порт ${{sshEntryPort}} уже занят роутером "${{duplicateSshPort.id}}". Поставь другой entry port.`, true);
    if (routerEntryPortInput) routerEntryPortInput.value = String(nextEntryPort(window.ROUTERS || []));
    return;
  }}
  const res = await fetch('/api/router', {{method: 'POST', body}});
  if (res.ok) {{
    ev.currentTarget.reset();
    if (routerNameInput) routerNameInput.dataset.touched = '';
    await loadRouters();
    fillRouterForm(true);
    showRouterMsg('Роутер добавлен. Теперь открой OpenWrt config в его карточке и вставь команды на роутер.');
  }} else {{
    showRouterMsg(await res.text(), true);
  }}
}});

routerStats.addEventListener('click', (ev) => {{
  const toggle = ev.target.closest('[data-offline-toggle]');
  if (!toggle) return;
  ev.preventDefault();
  ev.stopPropagation();
  offlineStatsExpanded = !offlineStatsExpanded;
  renderRouterStats(window.ROUTERS || []);
}});

cards.addEventListener('input', (ev) => {{
  const routerId = ev.target?.dataset?.wolPassword;
  if (!routerId) return;
  const value = ev.target.value || '';
  saveWolPassword(routerId, value);
  setWolState(routerId, {{sshPassword: value, error: '', message: ''}});
}});

cards.addEventListener('click', async (ev) => {{
  const toggleId = ev.target?.dataset?.cardToggle;
  if (toggleId) {{
    const body = document.getElementById(toggleId);
    if (!body) return;
    body.hidden = !body.hidden;
    ev.target.setAttribute('aria-expanded', body.hidden ? 'false' : 'true');
    ev.target.textContent = body.hidden ? 'Открыть' : 'Скрыть';
    return;
  }}
  const actionsToggleId = ev.target?.dataset?.actionsToggle;
  if (actionsToggleId) {{
    const actionBox = document.getElementById(actionsToggleId);
    if (!actionBox) return;
    const open = actionBox.classList.toggle('open');
    if (open) expandedActionPanels.add(actionsToggleId);
    else expandedActionPanels.delete(actionsToggleId);
    ev.target.setAttribute('aria-expanded', open ? 'true' : 'false');
    ev.target.textContent = open ? 'Скрыть действия' : 'Открыть действия';
    return;
  }}
  const wolToggleId = ev.target?.dataset?.wolToggle;
  if (wolToggleId) {{
    const state = getWolState(wolToggleId);
    const open = !state.open;
    setWolState(wolToggleId, {{open, pickerOpen: open ? state.pickerOpen : false, error: '', message: open ? state.message : ''}});
    renderRouterView();
    if (!open) flushDeferredRouterRender();
    if (open) {{
      await loadWolDevices(wolToggleId, false);
    }}
    return;
  }}
  const wolPickerToggle = ev.target.closest('[data-wol-picker-toggle]')?.dataset?.wolPickerToggle;
  if (wolPickerToggle) {{
    const state = getWolState(wolPickerToggle);
    const pickerOpen = !state.pickerOpen;
    setWolState(wolPickerToggle, {{pickerOpen}});
    renderRouterView();
    if (!pickerOpen) flushDeferredRouterRender();
    return;
  }}
  const wolPickButton = ev.target.closest('[data-wol-pick]');
  if (wolPickButton) {{
    const routerId = wolPickButton.dataset.wolPick;
    const mac = wolPickButton.dataset.wolMac || '';
    setWolState(routerId, {{selectedMac: mac, pickerOpen: false, error: '', message: ''}});
    renderRouterView();
    flushDeferredRouterRender();
    return;
  }}
  const wolRefreshId = ev.target?.dataset?.wolRefresh;
  if (wolRefreshId) {{
    await loadWolDevices(wolRefreshId, true);
    return;
  }}
  const wolSendId = ev.target?.dataset?.wolSend;
  if (wolSendId) {{
    await wakeSelectedDevice(wolSendId);
    return;
  }}
  const renameId = ev.target.closest('[data-rename]')?.dataset?.rename;
  if (renameId) {{
    const router = (window.ROUTERS || []).find(item => String(item.id) === String(renameId));
    const currentName = normalizeRouterName((router && router.name) || renameId);
    const nextName = prompt('Новое имя роутера:', currentName);
    if (nextName === null) return;
    const cleanName = normalizeRouterName(nextName);
    if (!cleanName) {{
      alert('Имя роутера не может быть пустым.');
      return;
    }}
    if (cleanName === currentName) return;
    const res = await fetch('/api/router/' + encodeURIComponent(renameId) + '/rename', {{
      method: 'POST',
      body: new URLSearchParams({{name: cleanName}})
    }});
    if (!res.ok) {{
      alert(await res.text() || 'Не удалось переименовать роутер.');
      return;
    }}
    await loadRouters();
    showRouterMsg('Имя роутера обновлено.');
    return;
  }}
  const diagnoseId = ev.target?.dataset?.diagnose;
  if (diagnoseId) {{
    openDiagnosticPanel(diagnoseId);
    return;
  }}
  const id = ev.target?.dataset?.delete;
  if (!id) return;
  if (!confirm('Удалить роутер ' + id + '?')) return;
  const res = await fetch('/api/router/' + encodeURIComponent(id) + '/delete', {{method: 'POST'}});
  if (res.ok) await loadRouters();
}});

if (typeof mobileCardsMq.addEventListener === 'function') {{
  mobileCardsMq.addEventListener('change', () => renderRouterView());
}} else if (typeof mobileCardsMq.addListener === 'function') {{
  mobileCardsMq.addListener(() => renderRouterView());
}}
function updateMobileChromeToggles() {{
  updateRouterFormToggle();
  updateMobilePanelToggle(hubMenuToggle, headerActions, 'Открыть меню хаба', 'Скрыть меню хаба');
  updateMobilePanelToggle(routerStatsToggle, routerStats, 'Открыть статистику', 'Скрыть статистику');
}}
if (typeof mobileLayoutMq.addEventListener === 'function') {{
  mobileLayoutMq.addEventListener('change', updateMobileChromeToggles);
}} else if (typeof mobileLayoutMq.addListener === 'function') {{
  mobileLayoutMq.addListener(updateMobileChromeToggles);
}}

const authToggle = document.getElementById('authToggle');
const authMenu = document.getElementById('authMenu');
const sessionList = document.getElementById('sessionList');
const revokeOtherSessions = document.getElementById('revokeOtherSessions');
const notifyList = document.getElementById('notifyList');
const notifyEnable = document.getElementById('notifyEnable');
const notifyClear = document.getElementById('notifyClear');
const backupFile = document.getElementById('backupFile');
const backupVpsHost = document.getElementById('backupVpsHost');
const backupPublicUrl = document.getElementById('backupPublicUrl');
const backupRestore = document.getElementById('backupRestore');
const backupMsg = document.getElementById('backupMsg');
let authHideTimer;
function hideOfflineStats() {{
  if (!offlineStatsExpanded) return;
  offlineStatsExpanded = false;
  renderRouterStats(window.ROUTERS || []);
}}
function showAuthMenu() {{
  clearTimeout(authHideTimer);
  hideOfflineStats();
  authMenu.hidden = false;
}}
function scheduleHideAuthMenu() {{
  clearTimeout(authHideTimer);
  authHideTimer = setTimeout(() => {{
    authMenu.hidden = true;
  }}, 180);
}}
authToggle.addEventListener('click', (ev) => {{
  ev.stopPropagation();
  if (authMenu.hidden) {{
    showAuthMenu();
  }} else {{
    authMenu.hidden = true;
  }}
}});
authToggle.addEventListener('mouseenter', showAuthMenu);
authToggle.addEventListener('focus', showAuthMenu);
authToggle.addEventListener('mouseleave', scheduleHideAuthMenu);
authMenu.addEventListener('mouseenter', showAuthMenu);
authMenu.addEventListener('focusin', showAuthMenu);
authMenu.addEventListener('mouseleave', scheduleHideAuthMenu);
authMenu.addEventListener('click', (ev) => ev.stopPropagation());
document.addEventListener('click', () => {{
  authMenu.hidden = true;
}});
document.addEventListener('click', (ev) => {{
  if (!offlineStatsExpanded) return;
  if (ev.target.closest('[data-offline-card]')) return;
  hideOfflineStats();
}});

function formatSessionTime(ts) {{
  if (!ts) return '—';
  try {{ return new Date(Number(ts) * 1000).toLocaleString('ru-RU'); }} catch (e) {{ return '—'; }}
}}

function renderSessions(list) {{
  const sessions = Array.isArray(list) ? list : [];
  if (!sessions.length) {{
    sessionList.innerHTML = '<div class="sessionEmpty">Активных сессий пока нет</div>';
    return;
  }}
  sessionList.innerHTML = sessions.map(s => `
    <div class="sessionRow">
      <div>
        <div class="sessionTitle">
          <span>${{escapeHtml(s.client || 'Устройство')}}</span>
          ${{s.current ? '<span class="sessionCurrent">сейчас</span>' : ''}}
        </div>
        <div class="sessionMeta">
          IP: ${{escapeHtml(s.ip || 'unknown')}}<br>
          Вход: ${{formatSessionTime(s.created_at)}}<br>
          Активность: ${{formatSessionTime(s.last_seen)}}<br>
          До: ${{formatSessionTime(s.expires_at)}}
        </div>
      </div>
      <button class="sessionBtn bad" data-session-revoke="${{escapeAttr(s.id || '')}}" ${{s.current ? 'disabled title="Текущую сессию заверши кнопкой Выйти"' : ''}}>Завершить</button>
    </div>
  `).join('');
}}

async function loadSessions() {{
  const res = await fetch('/api/sessions', {{cache: 'no-store'}});
  if (!res.ok) return;
  const data = await res.json();
  window.HUB_SESSIONS = data.sessions || [];
  renderSessions(window.HUB_SESSIONS);
}}

function notifyTime(ts) {{
  if (!ts) return '';
  try {{ return new Date(Number(ts) * 1000).toLocaleString('ru-RU'); }} catch (e) {{ return ''; }}
}}

function renderNotifications(list) {{
  const items = Array.isArray(list) ? list : [];
  if (!items.length) {{
    notifyList.innerHTML = '<div class="sessionEmpty">Пока нет событий</div>';
    return;
  }}
  notifyList.innerHTML = items.slice(0, 30).map(n => {{
    const details = Array.isArray(n.details) && n.details.length
      ? `<pre class="notifyDetails">${{escapeHtml(n.details.join('\\n'))}}</pre>`
      : '';
    return `<div class="notifyRow ${{escapeAttr(n.level || '')}}">
      <div class="notifyTitle"><span>${{escapeHtml(n.title || 'Событие')}}</span><span class="notifyTime">${{notifyTime(n.ts)}}</span></div>
      <div class="notifyBody">${{escapeHtml(n.body || '')}}</div>
      ${{details}}
    </div>`;
  }}).join('');
}}

function isIOSDevice() {{
  const ua = navigator.userAgent || '';
  return /iPhone|iPad|iPod/i.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
}}

function isStandalonePwa() {{
  return !!(window.navigator.standalone || (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches));
}}

function webPushSupportInfo() {{
  const secure = !!window.isSecureContext;
  const hasServiceWorker = 'serviceWorker' in navigator;
  const hasPushManager = 'PushManager' in window;
  const hasNotification = 'Notification' in window;
  let reason = '';
  if (!secure) reason = 'https';
  else if (!hasServiceWorker) reason = 'serviceWorker';
  else if (!hasPushManager) reason = 'pushManager';
  else if (!hasNotification) reason = 'notification';
  if (isIOSDevice() && !isStandalonePwa()) reason = 'ios-home-screen';
  return {{secure, hasServiceWorker, hasPushManager, hasNotification, reason}};
}}

function webPushSupported() {{
  const support = webPushSupportInfo();
  return support.secure && support.hasServiceWorker && support.hasPushManager && support.hasNotification;
}}

function notificationPermissionText() {{
  const support = webPushSupportInfo();
  if (!webPushSupported()) {{
    if (support.reason === 'https') return 'Push: нужен HTTPS';
    if (isIOSDevice()) return isStandalonePwa() ? 'Push недоступен' : 'iOS: добавь на экран';
    return 'Push недоступен';
  }}
  if (localStorage.getItem('owrtPushEnabled') === '1' && Notification.permission === 'granted') return 'Push включён';
  if (Notification.permission === 'granted') return 'Включено';
  if (Notification.permission === 'denied') return 'Запрещено';
  return 'Включить Push';
}}

function updateNotifyButton() {{
  notifyEnable.textContent = notificationPermissionText();
  notifyEnable.classList.toggle('on', localStorage.getItem('owrtPushEnabled') === '1' && webPushSupported() && Notification.permission === 'granted');
}}

function urlBase64ToUint8Array(value) {{
  const padding = '='.repeat((4 - value.length % 4) % 4);
  const base64 = (value + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  const output = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) output[i] = raw.charCodeAt(i);
  return output;
}}

async function registerPushSubscription() {{
  const reg = await navigator.serviceWorker.register('/sw.js', {{scope: '/'}});
  const ready = await navigator.serviceWorker.ready;
  const keyRes = await fetch('/api/push/vapid-public-key', {{cache: 'no-store'}});
  const keyData = await keyRes.json();
  if (!keyRes.ok || !keyData.ok || !keyData.publicKey) {{
    throw new Error(keyData.error || 'Web Push на VPS не готов. Обнови установку Hub.');
  }}
  let subscription = await ready.pushManager.getSubscription();
  if (!subscription) {{
    subscription = await ready.pushManager.subscribe({{
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(keyData.publicKey)
    }});
  }}
  const res = await fetch('/api/push/subscribe', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify(subscription)
  }});
  const data = await res.json().catch(() => ({{}}));
  if (!res.ok || !data.ok) throw new Error(data.error || 'Не смог сохранить push-подписку');
  return data;
}}

async function enableNotifications() {{
  if (!webPushSupported()) {{
    localStorage.setItem('owrtNotifyEnabled', '0');
    localStorage.setItem('owrtPushEnabled', '0');
    updateNotifyButton();
    const support = webPushSupportInfo();
    if (support.reason === 'https') {{
      showRouterMsg('Web Push работает только по HTTPS. Сейчас Hub открыт по обычному HTTP, поэтому Chrome не даёт service worker и push-подписку.', false);
      return;
    }}
    const message = isIOSDevice()
      ? (isStandalonePwa()
        ? 'Этот iOS-браузер не дал Push API. Проверь iOS 16.4+, разрешения уведомлений для веб-приложений и открой Hub именно с экрана Домой.'
        : 'На iOS открой Hub через Safari, нажми Поделиться -> На экран Домой, потом зайди из иконки и включи Push.')
      : 'Этот браузер не даёт Web Push. Попробуй Chrome/Edge/Firefox или проверь разрешения уведомлений.';
    showRouterMsg(message, false);
    return;
  }}
  if (Notification.permission === 'default') {{
    await Notification.requestPermission();
  }}
  if (Notification.permission !== 'granted') {{
    localStorage.setItem('owrtNotifyEnabled', '0');
    localStorage.setItem('owrtPushEnabled', '0');
    updateNotifyButton();
    showRouterMsg('Браузер не дал разрешение на уведомления. Проверь замочек возле адреса сайта и разреши уведомления.', true);
    return;
  }}
  try {{
    showRouterMsg('Включаю настоящий Web Push для этого устройства...');
    await registerPushSubscription();
    localStorage.setItem('owrtNotifyEnabled', '1');
    localStorage.setItem('owrtPushEnabled', '1');
    showRouterMsg('Push включён. Теперь уведомления должны приходить даже когда вкладка закрыта.');
  }} catch (e) {{
    localStorage.setItem('owrtPushEnabled', '0');
    localStorage.setItem('owrtNotifyEnabled', '0');
    showRouterMsg(e.message || 'Не удалось включить Web Push', true);
  }}
  updateNotifyButton();
}}

function showBrowserNotification(item) {{
  if (localStorage.getItem('owrtPushEnabled') === '1') return;
  if (!item || !('Notification' in window) || Notification.permission !== 'granted') return;
  if (localStorage.getItem('owrtNotifyEnabled') !== '1') return;
  try {{
    new Notification(item.title || 'OpenWrt Remote Hub', {{
      body: item.body || '',
      tag: 'owrt-' + (item.id || item.kind || item.ts || Date.now()),
      renotify: false
    }});
  }} catch (e) {{}}
}}

function initialNotificationTs() {{
  const stored = Number(localStorage.getItem('owrtLastNotificationTs') || 0);
  const initial = (window.HUB_NOTIFICATIONS || []).reduce((max, n) => Math.max(max, Number(n.ts || 0)), 0);
  const result = Math.max(stored, initial);
  localStorage.setItem('owrtLastNotificationTs', String(result));
  return result;
}}

let lastNotificationTs = initialNotificationTs();

async function loadNotifications({{initial = false}} = {{}}) {{
  const res = await fetch('/api/notifications?after=' + encodeURIComponent(initial ? 0 : lastNotificationTs), {{cache: 'no-store'}});
  if (!res.ok) return;
  const data = await res.json();
  const items = data.notifications || [];
  if (initial) {{
    window.HUB_NOTIFICATIONS = items;
    renderNotifications(window.HUB_NOTIFICATIONS);
    return;
  }}
  if (!items.length) return;
  const known = new Set((window.HUB_NOTIFICATIONS || []).map(n => n.id));
  const fresh = items.filter(n => !known.has(n.id)).sort((a, b) => Number(a.ts || 0) - Number(b.ts || 0));
  window.HUB_NOTIFICATIONS = [...items, ...(window.HUB_NOTIFICATIONS || [])]
    .filter((item, idx, arr) => arr.findIndex(other => other.id === item.id) === idx)
    .sort((a, b) => Number(b.ts || 0) - Number(a.ts || 0))
    .slice(0, 60);
  for (const item of fresh) {{
    lastNotificationTs = Math.max(lastNotificationTs, Number(item.ts || 0));
    showBrowserNotification(item);
  }}
  localStorage.setItem('owrtLastNotificationTs', String(lastNotificationTs));
  renderNotifications(window.HUB_NOTIFICATIONS);
}}

function bytesToBase64(bytes) {{
  let binary = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {{
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }}
  return btoa(binary);
}}

function setBackupMsg(text, bad = false) {{
  backupMsg.hidden = false;
  backupMsg.className = 'backupMsg' + (bad ? ' bad' : '');
  backupMsg.textContent = text;
}}

backupRestore.addEventListener('click', async () => {{
  const file = backupFile.files && backupFile.files[0];
  if (!file) {{
    setBackupMsg('Выбери backup-архив .tar.gz', true);
    return;
  }}
  if (!confirm('Восстановить Hub из backup? Текущая БД будет заменена, старая копия сохранится рядом с hub.db.')) return;
  backupRestore.disabled = true;
  setBackupMsg('Читаю архив и восстанавливаю Hub...');
  try {{
    const bytes = new Uint8Array(await file.arrayBuffer());
    const res = await fetch('/api/backup/restore', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{
        filename: file.name,
        archive_b64: bytesToBase64(bytes),
        vps_host: backupVpsHost.value.trim(),
        public_url: backupPublicUrl.value.trim()
      }})
    }});
    const data = await res.json().catch(() => ({{}}));
    if (!res.ok || !data.ok) throw new Error(data.error || res.status);
    const rewritten = data.rewritten || {{}};
    const changed = Object.keys(rewritten).length ? ' Переписано: ' + Object.entries(rewritten).map(([k, v]) => `${{k}}=${{v}}`).join(', ') + '.' : '';
    const warn = Array.isArray(data.warnings) && data.warnings.length ? ' Предупреждения: ' + data.warnings.join('; ') : '';
    setBackupMsg('Backup восстановлен.' + changed + ' Перезапусти owrt-remote на VPS и Xray, затем проверь роутеры.' + warn);
    await loadRouters();
  }} catch (e) {{
    setBackupMsg('Restore не удался: ' + (e.message || e), true);
  }} finally {{
    backupRestore.disabled = false;
  }}
}});

notifyEnable.addEventListener('click', enableNotifications);
notifyClear.addEventListener('click', async () => {{
  if (!confirm('Очистить все уведомления?')) return;
  const res = await fetch('/api/notifications/clear', {{method: 'POST'}});
  if (!res.ok) {{
    showRouterMsg(await res.text() || 'Не удалось очистить уведомления', true);
    return;
  }}
  window.HUB_NOTIFICATIONS = [];
  lastNotificationTs = Math.floor(Date.now() / 1000);
  localStorage.setItem('owrtLastNotificationTs', String(lastNotificationTs));
  renderNotifications(window.HUB_NOTIFICATIONS);
}});

sessionList.addEventListener('click', async (ev) => {{
  const id = ev.target?.dataset?.sessionRevoke;
  if (!id) return;
  const body = new URLSearchParams({{id}});
  const res = await fetch('/api/session/revoke', {{method: 'POST', body}});
  if (res.ok) await loadSessions();
}});

revokeOtherSessions.addEventListener('click', async () => {{
  const res = await fetch('/api/session/revoke-others', {{method: 'POST'}});
  if (res.ok) await loadSessions();
}});

document.getElementById('authForm').addEventListener('submit', async (ev) => {{
  ev.preventDefault();
  const msg = document.getElementById('authMsg');
  msg.hidden = true;
  msg.className = 'msg';
  const body = new URLSearchParams(new FormData(ev.currentTarget));
  const res = await fetch('/api/auth', {{method: 'POST', body}});
  const text = await res.text();
  msg.hidden = false;
  if (res.ok) {{
    msg.textContent = text || 'Доступ обновлен';
    ev.currentTarget.current_password.value = '';
    ev.currentTarget.password.value = '';
    ev.currentTarget.password_confirm.value = '';
  }} else {{
    msg.className = 'msg bad';
    msg.textContent = text || 'Не удалось сохранить';
  }}
}});

renderSessions(window.HUB_SESSIONS);
renderNotifications(window.HUB_NOTIFICATIONS);
updateNotifyButton();
syncRouterSearchToggleState();
renderRouterView();
fillRouterForm(true);
setInterval(loadRouters, 5000);
setInterval(loadNotifications, 9000);
</script>
</body>
</html>"""


def ssh_terminal_html(row, ws_token):
    router_id = row["id"]
    safe_id = html.escape(router_id, quote=True)
    safe_name = html.escape(row["name"] or router_id, quote=True)
    ssh_port = int(row["ssh_entry_port"] or 0)
    quoted_id = urllib.parse.quote(router_id)
    ws_path = f"/ssh-ws/{quoted_id}?t={urllib.parse.quote(ws_token)}"
    check_path = f"/api/ssh/{quoted_id}/check?t={urllib.parse.quote(ws_token)}"
    session_path = f"/api/ssh/{quoted_id}/session?t={urllib.parse.quote(ws_token)}"
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<title>SSH {safe_name}</title>
<style>
:root{{color-scheme:dark;--bg:#07040f;--panel:rgba(19,14,32,.92);--text:#f7f2ff;--muted:#b9adc9;--line:rgba(169,126,255,.28);--green:#22c55e;--blue:#7c3aed;--red:#fb7185;--grid:rgba(168,85,247,.13)}}
*{{box-sizing:border-box}}
body{{min-height:100vh;margin:0;background-color:var(--bg);background-image:radial-gradient(circle at 16% 10%,rgba(168,85,247,.45),transparent 30%),radial-gradient(circle at 88% 16%,rgba(59,130,246,.28),transparent 32%),linear-gradient(145deg,#07040f,#120a24 48%,#05030a),repeating-linear-gradient(0deg,transparent 0 30px,var(--grid) 31px),repeating-linear-gradient(90deg,transparent 0 30px,var(--grid) 31px);background-attachment:fixed;color:var(--text);font:14px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;padding:18px}}
.wrap{{width:100%;max-width:1180px;margin:0 auto;display:flex;flex-direction:column;gap:10px;min-width:0}}
.top{{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:42px}}
.sshTitle{{display:flex;align-items:center;gap:10px;flex-wrap:wrap}}h1{{margin:0;font-size:18px;line-height:1.15}}.muted{{color:var(--muted)}}
.badge,.btn{{display:inline-flex;align-items:center;justify-content:center;gap:8px;border:1px solid var(--line);border-radius:999px;padding:7px 12px;background:rgba(255,255,255,.08);color:#f3e8ff;text-decoration:none;font-weight:850;font-size:13px}}
.dot{{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 13px var(--green)}}
.termBox{{width:100%;max-width:100%;min-width:0;height:min(520px,calc(100vh - 112px));min-height:320px;display:flex;flex-direction:column;border:1px solid var(--line);border-radius:8px;background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.045)),var(--panel);box-shadow:0 22px 64px rgba(0,0,0,.38);overflow:hidden}}
.bar{{display:flex;align-items:center;justify-content:flex-start;gap:10px;padding:7px 10px;border-bottom:1px solid var(--line);background:rgba(255,255,255,.05)}}
.keySink{{position:fixed;left:-1000px;top:-1000px;width:1px;height:1px;opacity:.01;border:0;padding:0;background:transparent;color:transparent;resize:none;outline:none}}
.mobileInput{{display:none;gap:8px;padding:8px;border-top:1px solid var(--line);background:rgba(255,255,255,.045)}}.mobileInput input{{flex:1;min-width:0;border:1px solid var(--line);border-radius:8px;padding:11px 12px;background:rgba(8,5,18,.76);color:var(--text);font:14px/1.2 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;outline:none}}.mobileInput input:focus{{border-color:rgba(34,211,238,.62);box-shadow:0 0 0 3px rgba(34,211,238,.12)}}.mobileInput button{{border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:11px 12px;background:linear-gradient(135deg,#7c3aed,#a855f7);color:#fff;font-weight:950;white-space:nowrap}}
#term{{flex:1 1 auto;min-width:0;min-height:0;margin:0;padding:12px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;outline:none;cursor:text;background:rgba(0,0,0,.42);font:13px/1.34 "Cascadia Mono","Consolas","Liberation Mono",monospace;color:#e9d5ff;user-select:text;-webkit-user-select:text;touch-action:pan-y;scrollbar-width:thin;scrollbar-color:rgba(168,85,247,.72) rgba(255,255,255,.06)}}#term::selection,#term *::selection{{background:rgba(34,211,238,.34);color:#fff}}#term:focus{{box-shadow:inset 0 0 0 1px rgba(34,211,238,.30)}}#term::-webkit-scrollbar{{width:12px;height:12px}}#term::-webkit-scrollbar-track{{background:rgba(255,255,255,.06)}}#term::-webkit-scrollbar-thumb{{background:linear-gradient(180deg,#7c3aed,#22d3ee);border-radius:999px;border:3px solid rgba(10,6,18,.96)}}#term::-webkit-scrollbar-thumb:hover{{background:linear-gradient(180deg,#a855f7,#67e8f9)}}.term-error{{color:#fb7185;font-weight:900}}.term-warn{{color:#fde68a;font-weight:850}}.term-ok{{color:#bbf7d0;font-weight:850}}.term-info{{color:#67e8f9;font-weight:850}}.term-prompt{{color:#86efac;font-weight:900}}.term-metric{{color:#93c5fd;font-weight:850}}.term-muted{{color:#c4b5fd}}.term-inverse{{display:inline-block;background:#ddd6fe;color:#13091f;border-radius:3px;padding:0 3px;font-weight:900}}
.bad{{color:#fecdd3}}
@media(max-width:980px),(pointer:coarse){{html{{min-height:100%;overflow-x:hidden}}body{{min-height:100svh;padding:4px;font-size:13px;background-attachment:scroll;overflow-x:hidden;overflow-y:auto;overscroll-behavior-y:contain}}.wrap{{width:100%;max-width:none;gap:5px;min-height:0;overflow:visible}}.top{{align-items:stretch;flex-direction:column;gap:5px;min-height:0}}.sshTitle{{align-items:flex-start;flex-direction:column;gap:4px}}h1{{font-size:15px}}.btn{{width:100%;min-width:0;padding:7px 9px}}.termBox{{height:calc(100svh - 128px);min-height:520px;max-height:720px;border-radius:6px}}.bar{{flex:0 0 auto;padding:5px}}.badge{{width:100%;justify-content:center;padding:6px 9px;font-size:12px}}#term{{font-size:11.5px;line-height:1.23;padding:8px;min-height:0;overflow-x:hidden}}.mobileInput{{flex:0 0 auto;display:grid;grid-template-columns:1fr 72px 84px;gap:5px;padding:5px}}.mobileInput input{{grid-column:1/-1;padding:9px 10px;font-size:15px}}.mobileInput .pasteBtn,.mobileInput .enterBtn,.mobileInput .sendBtn{{grid-column:auto;padding:9px 7px;font-size:12px}}}}
@media(max-width:420px){{.mobileInput{{grid-template-columns:1fr 70px 82px}}.mobileInput button{{width:100%}}}}
</style>
</head>
<body>
<main class="wrap">
  <div class="top">
    <div class="sshTitle">
      <h1>SSH · {safe_name}</h1>
    </div>
    <a class="btn" href="/">Назад в Hub</a>
  </div>
__QUICK_COMMANDS_HTML__
  <section class="termBox">
    <div class="bar">
      <span class="badge"><i class="dot"></i>Terminal</span>
    </div>
    <pre id="term" tabindex="0"></pre>
    <textarea id="keySink" class="keySink" autocomplete="off" autocapitalize="off" spellcheck="false"></textarea>
    <div class="mobileInput">
      <input id="cmdInput" autocomplete="off" autocapitalize="off" spellcheck="false" enterkeyhint="send" placeholder="Команда или пароль">
      <button class="pasteBtn" id="cmdPaste" type="button">Вставить</button>
      <button class="enterBtn" id="cmdEnter" type="button">Enter</button>
      <button class="sendBtn" id="cmdSend" type="button">Отправить</button>
    </div>
  </section>
</main>
<script>
const term = document.getElementById('term');
const cmdInput = document.getElementById('cmdInput');
const cmdSend = document.getElementById('cmdSend');
const cmdPaste = document.getElementById('cmdPaste');
const cmdEnter = document.getElementById('cmdEnter');
const keySink = document.getElementById('keySink');
let ws;
let httpSid = '';
let httpPollTimer = 0;
let terminalMode = 'ws';
let terminalPlain = '';
const isMobileTerminal = window.matchMedia('(max-width: 680px)').matches || /Android|iPhone|iPad|iPod|Mobile|Telegram/i.test(navigator.userAgent);
let lastTerminalSelection = '';
let lastTerminalSelectionAt = 0;
function settleMobileTerminal() {{
  if (!isMobileTerminal) return;
  cmdInput.blur();
  window.setTimeout(() => {{
    term.scrollTop = term.scrollHeight;
  }}, 120);
}}
function escapeHtmlText(text) {{
  return String(text ?? '').replace(/[&<>"']/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));
}}
function cleanTerminal(text) {{
  let clearScreen = false;
  let value = String(text || '')
    .replace(/\\x1b\\][^\\x07]*(?:\\x07|\\x1b\\\\)/g, '')
    .replace(/\\x1b\\[\\?2004[hl]/g, '');
  value = value.replace(/\\x1bc/g, () => {{ clearScreen = true; return ''; }});
  value = value.replace(/(?:\\x1b\\[H)?\\x1b\\[J|\\x1b\\[2J(?:\\x1b\\[H)?|\\x1b\\[H\\x1b\\[J/g, () => {{
    clearScreen = true;
    return '';
  }});
  value = value.replace(/\\x1b\\[7m([\\s\\S]*?)\\x1b\\[m/g, (_, inner) => `\\uE000${{inner}}\\uE001`);
  value = value.replace(/\\x1b\\[[0-9;?]*[ -/]*[@-~]/g, '');
  value = value.replace(/(?:\\r?\\n){{3,}}/g, '\\r\\n\\r\\n');
  return {{text: value, clearScreen}};
}}
function applyTerminalControls(current, chunk) {{
  let out = String(current || '');
  const text = String(chunk || '').replace(/\\r\\n/g, '\\n').replace(/\\r/g, '\\n');
  for (const ch of text) {{
    if (ch === '\\b' || ch === '\\x7f') {{
      if (out && !out.endsWith('\\n')) out = out.slice(0, -1);
      continue;
    }}
    out += ch;
  }}
  return out;
}}
function highlightTerminal(text) {{
  let html = escapeHtmlText(text);
  html = html.replace(/\\uE000([\\s\\S]*?)\\uE001/g, '<span class="term-inverse">$1</span>');
  html = html.replace(/(Permission denied[^\\n]*|command-line line \\d+:[^\\n]*|\\[[^\\n]*(?:error|ошибка|closed|закрыто)[^\\n]*\\])/gi, '<span class="term-error">$1</span>');
  html = html.replace(/((?:root@)?127\\.0\\.0\\.1[^\\n]*password:|password:|пароль:)/gi, '<span class="term-warn">$1</span>');
  html = html.replace(/(^|\\n)(root@[^\\n#]+[#>$])/g, '$1<span class="term-prompt">$2</span>');
  html = html.replace(/(BusyBox v[^\\n]*|OpenWrt [^\\n]*|W I R E L E S S\\s+F R E E D O M)/g, '<span class="term-info">$1</span>');
  html = html.replace(/\\b(Mem:|CPU:|Load average:)\\b/g, '<span class="term-metric">$1</span>');
  html = html.replace(/\\b(OK|running|enabled|online)\\b/g, '<span class="term-ok">$1</span>');
  html = html.replace(/\\b(failed|disabled|offline|refused|denied)\\b/gi, '<span class="term-error">$1</span>');
  return html;
}}
function write(text) {{
  const cleaned = cleanTerminal(text);
  if (cleaned.clearScreen) terminalPlain = '';
  terminalPlain = applyTerminalControls(terminalPlain, cleaned.text);
  term.innerHTML = highlightTerminal(terminalPlain);
  term.scrollTop = term.scrollHeight;
}}
function replaceTerminal(text) {{
  terminalPlain = '';
  term.innerHTML = '';
  write(text);
}}
function send(text) {{
  if (ws && ws.readyState === WebSocket.OPEN) {{
    ws.send(text);
    return Promise.resolve(true);
  }}
  if (terminalMode === 'http' && httpSid) {{
    const body = new URLSearchParams({{sid: httpSid, data: text}});
    return fetch('/api/ssh-session-write', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
      body
    }}).then(async (res) => {{
      let data = {{}};
      try {{ data = await res.json(); }} catch (e) {{}}
      if (!res.ok || !data.ok) {{
        return fetch('/api/ssh-session/' + encodeURIComponent(httpSid) + '/write', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
          body: new URLSearchParams({{data: text}})
        }}).then(async (fallbackRes) => {{
          let fallbackData = {{}};
          try {{ fallbackData = await fallbackRes.json(); }} catch (e) {{}}
          if (!fallbackRes.ok || !fallbackData.ok) {{
            write('\\r\\n[HTTP-terminal: ввод не принят: ' + (fallbackData.error || data.error || fallbackRes.status || res.status) + ']\\r\\n');
            return false;
          }}
          window.setTimeout(pollHttpTerminal, 90);
          return true;
        }});
      }}
      window.setTimeout(pollHttpTerminal, 90);
      return true;
    }}).catch(() => {{
      write('\\r\\n[не смог отправить ввод в HTTP-terminal]\\r\\n');
      return false;
    }});
  }}
  write('\\r\\n[terminal еще подключается, повтори ввод через секунду]\\r\\n');
  return Promise.resolve(false);
}}
function normalizePaste(text) {{
  return String(text || '').replace(/\\r\\n/g, '\\r').replace(/\\n/g, '\\r');
}}
function isEditableTarget(target) {{
  if (!target) return false;
  const tag = String(target.tagName || '').toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select' || target.isContentEditable;
}}
function terminalSelectionText() {{
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0) return '';
  const text = String(sel.toString() || '');
  if (!text) return '';
  for (let i = 0; i < sel.rangeCount; i++) {{
    const range = sel.getRangeAt(i);
    if (term.contains(range.commonAncestorContainer) || range.intersectsNode(term)) {{
      return text;
    }}
  }}
  return '';
}}
function rememberTerminalSelection() {{
  const text = terminalSelectionText();
  if (text) {{
    lastTerminalSelection = text;
    lastTerminalSelectionAt = Date.now();
  }}
}}
function recentTerminalSelection() {{
  return terminalSelectionText() || (Date.now() - lastTerminalSelectionAt < 15000 ? lastTerminalSelection : '');
}}
async function copyText(text) {{
  const value = String(text || '');
  if (!value) return false;
  try {{
    await navigator.clipboard.writeText(value);
    return true;
  }} catch (e) {{
    const area = document.createElement('textarea');
    area.value = value;
    area.setAttribute('readonly', 'readonly');
    area.style.position = 'fixed';
    area.style.left = '-1000px';
    area.style.top = '-1000px';
    document.body.appendChild(area);
    area.select();
    let ok = false;
    try {{ ok = document.execCommand('copy'); }} catch (err) {{}}
    area.remove();
    return ok;
  }}
}}
function flashCopyLabel(ok, selected) {{
  if (!ok) return;
}}
function focusTerminal() {{
  term.focus({{preventScroll: true}});
  if (isMobileTerminal) {{
    return;
  }}
  keySink.focus({{preventScroll: true}});
}}
function handleTerminalKey(ev) {{
  const key = ev.key;
  const lower = String(key || '').toLowerCase();
  if ((ev.ctrlKey || ev.metaKey) && lower === 'v') {{
    return;
  }}
  if ((ev.ctrlKey || ev.metaKey) && lower === 'c') {{
    const selected = terminalSelectionText();
    if (selected) {{
      copyText(selected).then(ok => flashCopyLabel(ok, true));
      ev.preventDefault();
      ev.stopPropagation();
      return;
    }}
    send('\\x03');
    ev.preventDefault();
    ev.stopPropagation();
    return;
  }}
  if ((ev.ctrlKey || ev.metaKey) && lower === 'd') {{
    send('\\x04');
    ev.preventDefault();
    ev.stopPropagation();
    return;
  }}
  if ((ev.ctrlKey || ev.metaKey) && lower === 'l') {{
    send('\\x0c');
    ev.preventDefault();
    ev.stopPropagation();
    return;
  }}
  if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
  const keys = {{
    Enter: '\\r',
    Backspace: '\\x7f',
    Tab: '\\t',
    ArrowUp: '\\x1b[A',
    ArrowDown: '\\x1b[B',
    ArrowRight: '\\x1b[C',
    ArrowLeft: '\\x1b[D',
    Delete: '\\x1b[3~',
    Home: '\\x1b[H',
    End: '\\x1b[F',
    PageUp: '\\x1b[5~',
    PageDown: '\\x1b[6~'
  }};
  if (keys[key]) {{
    send(keys[key]);
    ev.preventDefault();
    ev.stopPropagation();
    return;
  }}
  if (key && key.length === 1) {{
    send(key);
    ev.preventDefault();
    ev.stopPropagation();
  }}
}}
function handleTerminalPaste(ev) {{
  const text = (ev.clipboardData || window.clipboardData)?.getData('text') || '';
  if (!text) return;
  send(normalizePaste(text));
  if (keySink) keySink.value = '';
  ev.preventDefault();
  ev.stopPropagation();
}}
async function sendCommandInput() {{
  const value = cmdInput.value;
  if (!value) return;
  await send(normalizePaste(value) + '\\r');
  cmdInput.value = '';
  if (isMobileTerminal) {{
    settleMobileTerminal();
  }} else {{
    cmdInput.focus();
  }}
  window.setTimeout(pollHttpTerminal, 120);
}}
async function pasteIntoInput() {{
  cmdInput.focus();
  try {{
    const text = await navigator.clipboard.readText();
    if (!text) return;
    const start = cmdInput.selectionStart ?? cmdInput.value.length;
    const end = cmdInput.selectionEnd ?? cmdInput.value.length;
    cmdInput.value = cmdInput.value.slice(0, start) + text + cmdInput.value.slice(end);
    const pos = start + text.length;
    cmdInput.setSelectionRange(pos, pos);
  }} catch (e) {{
    cmdInput.placeholder = 'Зажми поле и выбери Вставить';
    cmdInput.focus();
  }}
}}
let wsOpened = false;
let receivedTerminalData = false;
let diagnosticStarted = false;
async function pollHttpTerminal() {{
  if (!httpSid) return;
  try {{
    const res = await fetch('/api/ssh-session/' + encodeURIComponent(httpSid) + '/read', {{cache: 'no-store'}});
    const data = await res.json();
    if (data.data) write(data.data);
    if (data.alive) {{
      httpPollTimer = window.setTimeout(pollHttpTerminal, 650);
    }} else {{
      httpSid = '';
    }}
  }} catch (e) {{
    write('\\r\\n[HTTP-terminal: потеряна связь с Hub]\\r\\n');
    httpSid = '';
  }}
}}
async function startHttpTerminal(reason) {{
  if (terminalMode === 'http' || httpSid) return;
  terminalMode = 'http';
  try {{
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) ws.close();
  }} catch (e) {{}}
  if (reason === 'mobile') {{
    write('\\r\\nHTTP-terminal подключается...\\r\\n');
  }} else {{
    write(`\\r\\n[${{reason}}]\\r\\nWebSocket не открылся, включаю запасной HTTP-terminal...\\r\\n`);
  }}
  try {{
    const res = await fetch('{session_path}', {{cache: 'no-store'}});
    const data = await res.json();
    if (!res.ok || !data.ok) {{
      write(`HTTP-terminal не стартовал: ${{data.error || res.status}}\\r\\n`);
      return;
    }}
    httpSid = data.sid;
    if (isMobileTerminal) {{
      write('HTTP-terminal подключен. На телефоне вводи через поле снизу.\\r\\n');
    }} else {{
      write('HTTP-terminal подключен. Кликни в терминал и вводи или вставляй Ctrl+V.\\r\\n');
      focusTerminal();
    }}
    pollHttpTerminal();
  }} catch (e) {{
    write('HTTP-terminal не стартовал: ' + e + '\\r\\n');
  }}
}}
async function explainTerminalError(source) {{
  if (diagnosticStarted) return;
  diagnosticStarted = true;
  try {{
    const res = await fetch('{check_path}', {{cache: 'no-store'}});
    const data = await res.json();
    if (data.tcp_ok) {{
      await startHttpTerminal(source);
    }} else {{
      write(`\\r\\n[${{source}}]\\r\\nSSH-туннель на VPS не отвечает: ${{data.error || 'порт закрыт'}}\\r\\nНажми в Hub: Обновить Xray VPS, потом Рестарт Xray VPS, и проверь heartbeat роутера.\\r\\n`);
    }}
  }} catch (e) {{
    write(`\\r\\n[${{source}}]\\r\\nНе смог проверить SSH-туннель. Если страница открыта с мобильного интернета, проверь доступ к http://VPS_IP:8088/ и firewall VPS.\\r\\n`);
  }}
}}
function connect() {{
  replaceTerminal('Подключение к SSH...\\r\\n');
  if (isMobileTerminal) {{
    startHttpTerminal('mobile');
    return;
  }}
  const proto = location.protocol === 'https:' ? 'wss://' : 'ws://';
  ws = new WebSocket(proto + location.host + '{ws_path}');
  ws.onopen = () => {{ wsOpened = true; }};
  ws.onmessage = (ev) => {{
    if (terminalMode === 'http') return;
    if (!receivedTerminalData) replaceTerminal('');
    receivedTerminalData = true;
    write(ev.data);
  }};
  ws.onerror = () => explainTerminalError('ошибка web-terminal');
  ws.onclose = () => {{
    if (wsOpened) write('\\r\\n[SSH соединение закрыто]\\r\\n');
    else explainTerminalError('SSH соединение закрыто');
  }};
  window.setTimeout(() => {{
    if (!receivedTerminalData && !httpSid) startHttpTerminal('SSH молчит больше 3 секунд');
  }}, 3000);
}}
window.addEventListener('beforeunload', () => {{
  if (httpSid) {{
    navigator.sendBeacon('/api/ssh-session/' + encodeURIComponent(httpSid) + '/close');
  }}
}});
term.addEventListener('click', () => {{
  if (!terminalSelectionText()) window.setTimeout(focusTerminal, 0);
}});
term.addEventListener('keydown', handleTerminalKey);
term.addEventListener('paste', handleTerminalPaste);
keySink.addEventListener('keydown', handleTerminalKey);
keySink.addEventListener('paste', handleTerminalPaste);
document.addEventListener('selectionchange', () => window.setTimeout(rememberTerminalSelection, 0));
document.addEventListener('copy', (ev) => {{
  const selected = terminalSelectionText();
  if (!selected || !ev.clipboardData) return;
  ev.clipboardData.setData('text/plain', selected);
  ev.preventDefault();
  flashCopyLabel(true, true);
}});
document.addEventListener('paste', (ev) => {{
  if (isEditableTarget(ev.target)) return;
  focusTerminal();
  handleTerminalPaste(ev);
}});
document.addEventListener('keydown', (ev) => {{
  if (isEditableTarget(ev.target)) return;
  if ((ev.ctrlKey || ev.metaKey) && String(ev.key || '').toLowerCase() === 'c' && terminalSelectionText()) {{
    handleTerminalKey(ev);
    return;
  }}
  focusTerminal();
  handleTerminalKey(ev);
}});
cmdSend.addEventListener('click', sendCommandInput);
cmdEnter.addEventListener('click', async () => {{
  await send('\\r');
  if (isMobileTerminal) {{
    settleMobileTerminal();
  }} else {{
    cmdInput.focus();
  }}
  window.setTimeout(pollHttpTerminal, 120);
}});
cmdPaste.addEventListener('click', pasteIntoInput);
cmdInput.addEventListener('keydown', (ev) => {{
  if (ev.key === 'Enter') {{
    sendCommandInput();
    ev.preventDefault();
  }}
}});
connect();
window.setTimeout(focusTerminal, 80);
</script>
</body>
</html>"""


def ssh_terminal_html_v2(row, ws_token, quick_commands_html=""):
    router_id = row["id"]
    safe_name = html.escape(row["name"] or router_id, quote=True)
    quoted_id = urllib.parse.quote(router_id)
    ws_path = f"/ssh-ws/{quoted_id}?t={urllib.parse.quote(ws_token)}"
    check_path = f"/api/ssh/{quoted_id}/check?t={urllib.parse.quote(ws_token)}"
    session_path = f"/api/ssh/{quoted_id}/session?t={urllib.parse.quote(ws_token)}"
    is_vps_terminal = is_vps_terminal_id(router_id)
    title_prefix = "VPS" if is_vps_terminal else "SSH"
    page_title = "VPS terminal" if is_vps_terminal else f"{title_prefix} {row['name'] or router_id}"
    header_title = f"{title_prefix} · {row['name'] or router_id}"
    connect_label = "VPS terminal" if is_vps_terminal else "SSH"
    closed_label = "VPS terminal закрыт" if is_vps_terminal else "SSH соединение закрыто"
    silent_label = "VPS terminal молчит больше 3 секунд" if is_vps_terminal else "SSH молчит больше 3 секунд"
    ready_label = "VPS terminal запущен. Это root shell самого VPS. Команды ниже можно копировать или сразу отправлять в терминал." if is_vps_terminal else ""
    force_http_only = str(os.environ.get("OWRT_REMOTE_TERMINAL_WS", "")).lower() not in {"1", "yes", "true", "on"}
    if is_vps_terminal:
        header_title = "VPS terminal"
    page = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<title>SSH __SAFE_NAME__</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.10.0/lib/addon-fit.min.js"></script>
<style>
:root{color-scheme:dark;--bg:#07040f;--panel:rgba(19,14,32,.92);--text:#f7f2ff;--muted:#b9adc9;--line:rgba(169,126,255,.30);--green:#22c55e;--blue:#7c3aed;--cyan:#22d3ee;--grid:rgba(168,85,247,.13)}
*{box-sizing:border-box}html,body{min-height:100%;margin:0;overflow-x:hidden}
body{min-height:100vh;overflow-y:auto;background-color:var(--bg);background-image:radial-gradient(circle at 16% 10%,rgba(168,85,247,.45),transparent 30%),radial-gradient(circle at 88% 16%,rgba(59,130,246,.28),transparent 32%),linear-gradient(145deg,#07040f,#120a24 48%,#05030a),repeating-linear-gradient(0deg,transparent 0 30px,var(--grid) 31px),repeating-linear-gradient(90deg,transparent 0 30px,var(--grid) 31px);background-attachment:fixed;color:var(--text);font:14px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;padding:14px}
.wrap{width:100%;max-width:1180px;min-height:calc(100vh - 28px);margin:0 auto;display:flex;flex-direction:column;gap:10px;min-width:0}
.top{display:flex;align-items:center;justify-content:space-between;gap:12px;flex:0 0 auto;min-height:38px}.sshTitle{display:flex;align-items:center;gap:10px;min-width:0}
h1{margin:0;font-size:18px;line-height:1.15;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.btn,.badge,.toolBtn{display:inline-flex;align-items:center;justify-content:center;gap:8px;border:1px solid var(--line);border-radius:999px;padding:7px 12px;background:rgba(255,255,255,.08);color:#f3e8ff;text-decoration:none;font-weight:850;font-size:13px;white-space:nowrap}.toolBtn{cursor:pointer;font:inherit}.toolBtn:hover,.btn:hover{border-color:rgba(34,211,238,.52);background:rgba(255,255,255,.12)}
.dot{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 13px var(--green)}
.quickPanel{display:grid;gap:10px;padding:12px;border:1px solid var(--line);border-radius:8px;background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.045)),var(--panel);box-shadow:0 18px 46px rgba(0,0,0,.22);flex:0 0 auto}.quickHead h2{margin:0;font-size:16px}.quickHead p{margin:4px 0 0;color:var(--muted)}.quickGrid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.cmdCard{display:grid;grid-template-rows:auto minmax(0,1fr) auto;gap:8px;padding:10px;border:1px solid var(--line);border-radius:8px;background:rgba(255,255,255,.05)}.cmdHead{display:grid;gap:2px}.cmdHead strong{font-size:13px}.cmdHead span{color:var(--muted);font-size:12px}.cmdBody{margin:0;padding:10px;border:1px solid rgba(255,255,255,.08);border-radius:8px;background:rgba(0,0,0,.24);color:#ddd6fe;font:12px/1.4 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;word-break:break-word;max-height:112px;overflow:auto}.cmdActions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;align-items:stretch}.cmdBtn{width:100%;min-height:38px;border:1px solid rgba(255,255,255,.12);border-radius:999px;padding:7px 11px;background:rgba(255,255,255,.08);color:#f7f2ff;font-weight:850;cursor:pointer}.cmdBtn.run{background:linear-gradient(135deg,#7c3aed,#a855f7)}
.termBox{width:100%;min-width:0;min-height:420px;flex:1 1 auto;display:flex;flex-direction:column;border:1px solid var(--line);border-radius:8px;background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.045)),var(--panel);box-shadow:0 22px 64px rgba(0,0,0,.38);overflow:hidden}.bar{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:8px 10px;border-bottom:1px solid var(--line);background:rgba(255,255,255,.05);flex:0 0 auto}.tools{display:flex;align-items:center;gap:7px;min-width:0;flex-wrap:wrap;justify-content:flex-end}
#terminal{flex:1 1 auto;min-height:0;min-width:0;background:#0b0714}#terminal.loading{display:flex;align-items:center;justify-content:center;color:var(--muted);font-weight:800}#terminal .xterm{height:100%;padding:10px}#terminal .xterm-viewport{background:transparent!important;scrollbar-width:thin;scrollbar-color:rgba(168,85,247,.72) rgba(255,255,255,.06);scroll-behavior:auto;overscroll-behavior:contain}body.mobile #terminal .xterm-viewport{-webkit-overflow-scrolling:touch;touch-action:pan-y;contain:content}#terminal .xterm-screen{height:100%}.xterm .xterm-viewport::-webkit-scrollbar{width:12px;height:12px}.xterm .xterm-viewport::-webkit-scrollbar-track{background:rgba(255,255,255,.06)}.xterm .xterm-viewport::-webkit-scrollbar-thumb{background:linear-gradient(180deg,#7c3aed,#22d3ee);border-radius:999px;border:3px solid rgba(10,6,18,.96)}
.mobileInput{display:none;gap:7px;padding:8px;border-top:1px solid var(--line);background:rgba(255,255,255,.045);flex:0 0 auto}.mobileInput textarea{flex:1;min-width:0;min-height:44px;max-height:96px;resize:vertical;border:1px solid var(--line);border-radius:8px;padding:11px 12px;background:rgba(8,5,18,.76);color:var(--text);font:14px/1.25 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;outline:none}.mobileInput textarea:focus{border-color:rgba(34,211,238,.62);box-shadow:0 0 0 3px rgba(34,211,238,.12)}.mobileInput button{border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:11px 12px;background:linear-gradient(135deg,#7c3aed,#a855f7);color:#fff;font-weight:950;white-space:nowrap}
body.mobile .mobileInput{display:grid;grid-template-columns:1fr 104px}body.mobile .mobileInput textarea{grid-column:auto}
@supports(height:100svh){body{min-height:100svh}.wrap{min-height:calc(100svh - 28px)}}
@media(max-width:680px),(pointer:coarse){body{padding:4px;background-attachment:scroll;background-image:linear-gradient(145deg,#07040f,#120a24 54%,#05030a)}.wrap{height:auto;min-height:calc(100svh - 8px);max-width:none;gap:5px}.top{gap:6px;align-items:stretch;flex-direction:column}.sshTitle{min-height:28px}h1{font-size:15px}.btn{width:100%;padding:8px 10px}.quickGrid{grid-template-columns:1fr}.cmdActions{display:grid;grid-template-columns:1fr 1fr}.bar{padding:5px;align-items:stretch;flex-direction:column}.badge{width:100%;padding:7px 10px}.tools{width:100%;display:grid;grid-template-columns:1fr 1fr 1fr}.toolBtn{padding:7px 8px;font-size:12px}.termBox{border-radius:6px;background:rgba(19,14,32,.96);box-shadow:none;min-height:72svh}#terminal .xterm{padding:5px}body.mobile #terminal .xterm-viewport{scrollbar-width:none}body.mobile .xterm .xterm-viewport::-webkit-scrollbar{display:none}.mobileInput{grid-template-columns:1fr 104px;padding:5px;gap:5px}.mobileInput textarea{font-size:15px;padding:10px}.mobileInput button{padding:10px 8px;font-size:12px}}
</style>
</head>
<body>
<main class="wrap">
  <div class="top">
    <div class="sshTitle"><h1>SSH · __SAFE_NAME__</h1></div>
    <a class="btn" href="/">Назад в Hub</a>
  </div>
  __QUICK_COMMANDS_HTML__
  <section class="termBox">
    <div class="bar">
      <span class="badge"><i class="dot"></i>Terminal</span>
      <div class="tools">
        <button class="toolBtn" id="copyBtn" type="button">Копировать</button>
        <button class="toolBtn" id="clearBtn" type="button">Очистить</button>
        <button class="toolBtn" id="reconnectBtn" type="button">Переподключить</button>
      </div>
    </div>
    <div id="terminal" class="loading">Загрузка терминала...</div>
    <div class="mobileInput">
      <textarea id="cmdInput" rows="2" autocomplete="off" autocapitalize="off" spellcheck="false" enterkeyhint="send" placeholder="Команды или пароль"></textarea>
      <button id="cmdSend" type="button">Отправить</button>
    </div>
  </section>
</main>
<script>
const WS_PATH = __WS_PATH_JSON__;
const CHECK_PATH = __CHECK_PATH_JSON__;
const SESSION_PATH = __SESSION_PATH_JSON__;
const ROUTER_ID = __ROUTER_ID_JSON__;
const IS_VPS_TERMINAL = __IS_VPS_TERMINAL_JSON__;
const FORCE_HTTP_ONLY = __FORCE_HTTP_ONLY_JSON__;
const CONNECT_LABEL = __CONNECT_LABEL_JSON__;
const CLOSED_LABEL = __CLOSED_LABEL_JSON__;
const SILENT_LABEL = __SILENT_LABEL_JSON__;
const READY_LABEL = __READY_LABEL_JSON__;
const terminalEl = document.getElementById('terminal');
const cmdInput = document.getElementById('cmdInput');
const cmdSend = document.getElementById('cmdSend');
const copyBtn = document.getElementById('copyBtn');
const clearBtn = document.getElementById('clearBtn');
const reconnectBtn = document.getElementById('reconnectBtn');
const quickCopyButtons = Array.from(document.querySelectorAll('.js-copy-cmd'));
const quickRunButtons = Array.from(document.querySelectorAll('.js-run-cmd'));
const isMobileTerminal = window.matchMedia('(max-width: 680px)').matches || /Android|iPhone|iPad|iPod|Mobile|Telegram/i.test(navigator.userAgent);
const encoder = new TextEncoder();
const decoder = new TextDecoder();
let ws, term, fitAddon, httpSid = '', httpPollTimer = 0, httpReconnectTimer = 0, terminalMode = 'ws';
let wsOpened = false, receivedTerminalData = false, diagnosticStarted = false, httpPollFailures = 0;
let httpPollInFlight = false, httpRecovering = false, httpRecoveryNoticeShown = false;
let inputQueue = '', inputFlushTimer = 0;
const SSH_PASSWORD_KEY = 'owrtRemote:sshPassword:' + ROUTER_ID;
let passwordPromptActive = false, passwordBuffer = '', autoPasswordUsed = false;
let pendingHubRestartAt = 0;
document.body.classList.toggle('mobile', isMobileTerminal);
function appendQuery(url, params){const sep=url.indexOf('?')===-1?'?':'&';return url+sep+new URLSearchParams(params).toString();}
function normalizePaste(text){return String(text||'').replace(/\r\n/g,'\r').replace(/\n/g,'\r');}
function notice(text,color='36'){if(term)term.write('\r\n\x1b[' + color + 'm' + text + '\x1b[0m\r\n');}
function savedSshPassword(){try{return localStorage.getItem(SSH_PASSWORD_KEY)||'';}catch(e){return '';}}
function saveSshPassword(value){if(IS_VPS_TERMINAL||!value)return;try{localStorage.setItem(SSH_PASSWORD_KEY,value);}catch(e){}}
function isSshPasswordPrompt(text){return /(?:password:|пароль:)/i.test(String(text||''));}
function maybeAutoSendPassword(text){if(IS_VPS_TERMINAL||!isSshPasswordPrompt(text))return;passwordPromptActive=true;passwordBuffer='';const saved=savedSshPassword();if(saved&&!autoPasswordUsed){autoPasswordUsed=true;passwordPromptActive=false;setTimeout(()=>sendData(saved+'\r',true),120);}}
function trackPasswordInput(text){if(!passwordPromptActive)return;for(const ch of String(text||'')){if(ch==='\r'||ch==='\n'){if(passwordBuffer)saveSshPassword(passwordBuffer);passwordPromptActive=false;passwordBuffer='';return;}if(ch==='\b'||ch==='\x7f'){passwordBuffer=passwordBuffer.slice(0,-1);continue;}if(ch>=' ')passwordBuffer+=ch;}}
function isEditableTarget(target){const tag=String((target&&target.tagName)||'').toLowerCase();return tag==='input'||tag==='textarea'||tag==='select'||(target&&target.isContentEditable);}
function terminalFocused(){const active=document.activeElement;return terminalEl.contains(active)||active===document.body;}
function fitTerminal(force=false){if(!term)return;if(isMobileTerminal&&document.activeElement===cmdInput&&!force)return;try{if(fitAddon)fitAddon.fit();}catch(e){}setTimeout(sendResize,40);}
function pendingHubRestartActive(){return !!pendingHubRestartAt&&Date.now()-pendingHubRestartAt<20000;}
function isHubRestartCommand(text){const value=String(text||'');if(!IS_VPS_TERMINAL)return false;return /\bsystemctl\s+restart\s+owrt-remote\b/i.test(value)||(/\bsystemd-run\b/i.test(value)&&/\bowrt-remote-selfupdate-\$\(date \+%s\)\b/i.test(value))||(/\bsystemd-run\b/i.test(value)&&/\bsystemctl\s+restart\s+owrt-remote\b/i.test(value));}
function trackRiskyTerminalCommand(text){if(!isHubRestartCommand(text))return;pendingHubRestartAt=Date.now();notice('Команда отправлена. Hub сейчас перезапустится, поэтому этот VPS terminal может оборваться штатно. Хвост после restart вроде `&& systemctl status ...` в этой же сессии уже может не успеть показаться.','33');notice('Если окно замолчит, это нормально: открой VPS terminal заново через 3-5 секунд и отдельно выполни `systemctl status owrt-remote --no-pager -l`.','36');}
function clearHttpTimers(){if(httpPollTimer){clearTimeout(httpPollTimer);httpPollTimer=0;}if(httpReconnectTimer){clearTimeout(httpReconnectTimer);httpReconnectTimer=0;}}
function scheduleHttpPoll(delay=650){if(!httpSid||httpRecovering)return;clearTimeout(httpPollTimer);httpPollTimer=setTimeout(()=>{httpPollTimer=0;pollHttpTerminal();},delay);}
function sendResize(){if(!term)return;const cols=term.cols||80,rows=term.rows||24;if(ws&&ws.readyState===WebSocket.OPEN)ws.send(JSON.stringify({type:'resize',cols,rows}));if(httpSid){fetch('/api/ssh-session/'+encodeURIComponent(httpSid)+'/resize',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({cols,rows})}).catch(()=>{});}}
function sendDataNow(text){if(!text)return Promise.resolve(true);trackPasswordInput(text);trackRiskyTerminalCommand(text);if(ws&&ws.readyState===WebSocket.OPEN){ws.send(encoder.encode(text));return Promise.resolve(true);}if(terminalMode==='http'&&httpSid){return fetch('/api/ssh-session-write',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({sid:httpSid,data:text})}).then(async(res)=>{let data={};try{data=await res.json();}catch(e){}if(!res.ok||!data.ok){notice('HTTP-terminal: ввод не принят: '+(data.error||res.status),'31');return false;}scheduleHttpPoll(80);return true;}).catch(()=>{notice('Не смог отправить ввод в HTTP-terminal','31');return false;});}notice(httpRecovering?'HTTP-terminal переподключается после перезапуска Hub, подожди пару секунд':'Терминал еще подключается, повтори ввод через секунду','33');return Promise.resolve(false);}
function flushInputQueue(){const text=inputQueue;inputQueue='';if(inputFlushTimer){clearTimeout(inputFlushTimer);inputFlushTimer=0;}if(text)sendDataNow(text);}
function sendData(text,immediate=false){if(!text)return Promise.resolve(true);if(immediate){flushInputQueue();return sendDataNow(text);}inputQueue+=text;if(text.indexOf('\r')!==-1||text.indexOf('\n')!==-1||text.indexOf('\x03')!==-1||text.indexOf('\x04')!==-1){flushInputQueue();return Promise.resolve(true);}if(!inputFlushTimer)inputFlushTimer=setTimeout(flushInputQueue,isMobileTerminal?12:6);return Promise.resolve(true);}
async function copyText(text){const value=String(text||'');if(!value)return false;try{await navigator.clipboard.writeText(value);return true;}catch(e){const area=document.createElement('textarea');area.value=value;area.setAttribute('readonly','readonly');area.style.position='fixed';area.style.left='-1000px';document.body.appendChild(area);area.select();let ok=false;try{ok=document.execCommand('copy');}catch(err){}area.remove();return ok;}}
async function copySelection(){if(!term)return false;const text=term.getSelection?term.getSelection():'';return copyText(text);}
function terminalBufferText(){if(!term||!term.buffer||!term.buffer.active)return '';const buffer=term.buffer.active;const lines=[];for(let i=0;i<buffer.length;i++){const line=buffer.getLine(i);if(line)lines.push(line.translateToString(true));}return lines.join('\n').replace(/\s+$/,'');}
async function copyTerminalAll(){return copyText(terminalBufferText());}
function handlePaste(ev){const text=(ev.clipboardData||window.clipboardData)?.getData('text')||'';if(!text)return;sendData(normalizePaste(text),true);ev.preventDefault();ev.stopPropagation();}
async function sendCommandInput(){const value=cmdInput.value;if(!value)return;await sendData(normalizePaste(value)+'\r',true);cmdInput.value='';cmdInput.focus();scheduleHttpPoll(120);}
async function pasteIntoInput(){cmdInput.focus();try{const text=await navigator.clipboard.readText();if(!text)return;const start=cmdInput.selectionStart??cmdInput.value.length,end=cmdInput.selectionEnd??cmdInput.value.length;cmdInput.value=cmdInput.value.slice(0,start)+text+cmdInput.value.slice(end);const pos=start+text.length;cmdInput.setSelectionRange(pos,pos);}catch(e){cmdInput.placeholder='Зажми поле и выбери Вставить';}}
function httpRecoveryMessage(){if(pendingHubRestartActive())return 'HTTP-terminal: похоже, команда рестарта Hub ушла успешно. Текущая VPS terminal сессия оборвалась штатно; переподключаю автоматически...';return IS_VPS_TERMINAL?'HTTP-terminal: Hub перезапускается или временно недоступен. Переподключаю VPS terminal автоматически...':'HTTP-terminal: связь с Hub временно пропала. Пробую переподключить SSH автоматически...';}
function httpRecoveredMessage(){if(pendingHubRestartActive())return 'HTTP-terminal переподключен. Hub уже поднялся заново; команда, скорее всего, отработала. Проверь `systemctl status owrt-remote --no-pager -l` в новой сессии.';return IS_VPS_TERMINAL?'HTTP-terminal переподключен. Открыта новая VPS shell-сессия после перезапуска Hub.':'HTTP-terminal переподключен. Открыта новая SSH-сессия.';}
function scheduleHttpRecovery(reason){if(httpRecovering)return;httpRecovering=true;httpPollInFlight=false;clearHttpTimers();httpSid='';httpPollFailures+=1;if(!httpRecoveryNoticeShown){notice(httpRecoveryMessage(),'33');httpRecoveryNoticeShown=true;}const delay=Math.min(8000,Math.max(1200,900*httpPollFailures));httpReconnectTimer=setTimeout(()=>{httpReconnectTimer=0;startHttpTerminal('reconnect');},delay);}
async function pollHttpTerminal(){if(!httpSid||httpPollInFlight||httpRecovering)return;httpPollInFlight=true;try{const res=await fetch('/api/ssh-session/'+encodeURIComponent(httpSid)+'/read',{cache:'no-store'});let data={};try{data=await res.json();}catch(e){}if(!res.ok||!data.ok){scheduleHttpRecovery('read-failed');return;}httpPollFailures=0;httpRecoveryNoticeShown=false;if(data.data){term.write(data.data);maybeAutoSendPassword(data.data);}if(data.alive)scheduleHttpPoll(650);else{{httpSid='';notice(CLOSED_LABEL,'33');}}}catch(e){scheduleHttpRecovery('read-error');}finally{httpPollInFlight=false;}}
async function startHttpTerminal(reason){if(httpSid||httpPollInFlight)return;terminalMode='http';httpRecovering=reason==='reconnect'||reason==='read-failed'||reason==='read-error';clearHttpTimers();try{if(ws&&(ws.readyState===WebSocket.OPEN||ws.readyState===WebSocket.CONNECTING))ws.close();}catch(e){}if(reason==='direct')notice('Подключаю HTTP-terminal...','36');else if(reason==='mobile')notice('HTTP-terminal подключается...','36');else if(httpRecovering)notice(IS_VPS_TERMINAL?'Пробую поднять новую VPS terminal сессию...':'Пробую поднять новую SSH-сессию...','36');else notice(reason+'. Включаю запасной HTTP-terminal...','36');try{const res=await fetch(appendQuery(SESSION_PATH,{cols:term.cols||80,rows:term.rows||24}),{cache:'no-store'});let data={};try{data=await res.json();}catch(e){}if(!res.ok||!data.ok){httpPollFailures=Math.max(1,httpPollFailures+1);const delay=Math.min(8000,Math.max(1200,900*httpPollFailures));notice('HTTP-terminal пока не стартовал: '+(data.error||res.status||'hub недоступен')+'. Повторю автоматически...','33');httpReconnectTimer=setTimeout(()=>{httpReconnectTimer=0;startHttpTerminal('reconnect');},delay);return;}httpSid=data.sid;const wasRecovering=httpRecovering;httpRecovering=false;httpPollFailures=0;if(wasRecovering&&httpRecoveryNoticeShown)notice(httpRecoveredMessage(),'32');else notice(READY_LABEL || (isMobileTerminal?'HTTP-terminal подключен. Вводи через поле снизу или клавиатуру.':'HTTP-terminal подключен. Кликни в терминал, Ctrl+V вставляет.'),'32');httpRecoveryNoticeShown=false;if(pendingHubRestartActive())pendingHubRestartAt=0;sendResize();scheduleHttpPoll(80);}catch(e){httpPollFailures=Math.max(1,httpPollFailures+1);const delay=Math.min(8000,Math.max(1200,900*httpPollFailures));notice('HTTP-terminal не стартовал: '+e+'. Повторю автоматически...','31');httpReconnectTimer=setTimeout(()=>{httpReconnectTimer=0;startHttpTerminal('reconnect');},delay);}}
async function explainTerminalError(source){if(diagnosticStarted)return;diagnosticStarted=true;try{const res=await fetch(CHECK_PATH,{cache:'no-store'});const data=await res.json();if(data.tcp_ok)await startHttpTerminal(source);else{notice('SSH-туннель на VPS не отвечает: '+(data.error||'порт закрыт'),'31');notice('В Hub нажми: Обновить Xray CFG, потом Рестарт Xray VPS, и проверь heartbeat роутера.','33');}}catch(e){notice('Не смог проверить SSH-туннель. Проверь firewall VPS и доступ к Hub.','31');}}
function connect(){wsOpened=false;receivedTerminalData=false;diagnosticStarted=false;httpPollFailures=0;httpPollInFlight=false;httpRecovering=false;httpRecoveryNoticeShown=false;terminalMode='ws';httpSid='';clearHttpTimers();inputQueue='';passwordPromptActive=false;passwordBuffer='';autoPasswordUsed=false;if(inputFlushTimer){clearTimeout(inputFlushTimer);inputFlushTimer=0;}if(term){term.reset();notice('Подключение к '+CONNECT_LABEL+'...','36');}if(FORCE_HTTP_ONLY||isMobileTerminal){startHttpTerminal(FORCE_HTTP_ONLY?'direct':'mobile');return;}const proto=location.protocol==='https:'?'wss://':'ws://';ws=new WebSocket(proto+location.host+WS_PATH);ws.binaryType='arraybuffer';ws.onopen=()=>{wsOpened=true;sendResize();};ws.onmessage=async(ev)=>{if(terminalMode==='http')return;let text='';if(typeof ev.data==='string')text=ev.data;else if(ev.data instanceof Blob)text=await ev.data.text();else text=decoder.decode(ev.data);if(!receivedTerminalData)term.clear();receivedTerminalData=true;term.write(text);maybeAutoSendPassword(text);};ws.onerror=()=>explainTerminalError('ошибка web-terminal');ws.onclose=()=>{if(terminalMode==='http')return;if(wsOpened)notice(CLOSED_LABEL,'33');else explainTerminalError(CLOSED_LABEL);};setTimeout(()=>{if(!receivedTerminalData&&!httpSid)startHttpTerminal(SILENT_LABEL);},3000);}
function initTerminal(){if(!window.Terminal){terminalEl.classList.remove('loading');terminalEl.textContent='xterm.js не загрузился. Проверь доступ браузера к cdn.jsdelivr.net.';return;}terminalEl.classList.remove('loading');terminalEl.textContent='';term=new Terminal({cursorBlink:!isMobileTerminal,convertEol:false,scrollback:isMobileTerminal?200:5000,scrollSensitivity:isMobileTerminal?8:1,fastScrollSensitivity:isMobileTerminal?14:5,smoothScrollDuration:0,fontFamily:'"Cascadia Mono","Consolas","Liberation Mono",monospace',fontSize:isMobileTerminal?12:14,lineHeight:1.14,theme:{background:'#0b0714',foreground:'#f7f2ff',cursor:'#fbbf24',selectionBackground:'#334155',black:'#0b0714',red:'#fb7185',green:'#86efac',yellow:'#fde68a',blue:'#93c5fd',magenta:'#c084fc',cyan:'#67e8f9',white:'#f7f2ff'}});if(window.FitAddon&&FitAddon.FitAddon){fitAddon=new FitAddon.FitAddon();term.loadAddon(fitAddon);}term.open(terminalEl);term.onData(sendData);term.onResize(sendResize);term.attachCustomKeyEventHandler((ev)=>{const key=String(ev.key||'').toLowerCase();if((ev.ctrlKey||ev.metaKey)&&key==='c'&&term.hasSelection&&term.hasSelection()){copySelection();return false;}return true;});terminalEl.addEventListener('click',()=>term.focus());fitTerminal(true);connect();setTimeout(()=>{fitTerminal(true);term.focus();},120);}
document.addEventListener('paste',(ev)=>{if(isEditableTarget(ev.target))return;if(!terminalFocused())return;handlePaste(ev);});
window.addEventListener('beforeunload',()=>{clearHttpTimers();if(httpSid)navigator.sendBeacon('/api/ssh-session/'+encodeURIComponent(httpSid)+'/close');});
let resizeTimer=0;window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>fitTerminal(false),160);});
copyBtn.addEventListener('click',copyTerminalAll);clearBtn.addEventListener('click',()=>term&&term.clear());reconnectBtn.addEventListener('click',()=>{clearHttpTimers();try{if(ws)ws.close();}catch(e){}if(httpSid)navigator.sendBeacon('/api/ssh-session/'+encodeURIComponent(httpSid)+'/close');httpSid='';connect();});
cmdSend.addEventListener('click',sendCommandInput);cmdInput.addEventListener('keydown',(ev)=>{if(ev.key==='Enter'&&(ev.ctrlKey||ev.metaKey)){sendCommandInput();ev.preventDefault();}});
quickCopyButtons.forEach((btn)=>btn.addEventListener('click',()=>copyQuickCommand(btn)));
quickRunButtons.forEach((btn)=>btn.addEventListener('click',()=>runQuickCommand(btn)));
function quickCommandText(btn){return String(btn?.dataset?.cmd||'');}
async function copyQuickCommand(btn){const text=quickCommandText(btn);if(!text)return;const ok=await copyText(text);if(ok)notice('Команда скопирована','32');}
async function runQuickCommand(btn){const text=quickCommandText(btn);if(!text)return;term&&term.focus&&term.focus();await sendData(normalizePaste(text)+'\r',true);}
window.addEventListener('load',initTerminal);
</script>
</body>
</html>"""
    return (
        page.replace("__SAFE_NAME__", safe_name)
        .replace("__WS_PATH_JSON__", json.dumps(ws_path))
        .replace("__CHECK_PATH_JSON__", json.dumps(check_path))
        .replace("__SESSION_PATH_JSON__", json.dumps(session_path))
        .replace("__ROUTER_ID_JSON__", json.dumps(router_id))
        .replace("__IS_VPS_TERMINAL_JSON__", json.dumps(is_vps_terminal))
        .replace("__FORCE_HTTP_ONLY_JSON__", json.dumps(force_http_only))
        .replace("__CONNECT_LABEL_JSON__", json.dumps(connect_label))
        .replace("__CLOSED_LABEL_JSON__", json.dumps(closed_label))
        .replace("__SILENT_LABEL_JSON__", json.dumps(silent_label))
        .replace("__READY_LABEL_JSON__", json.dumps(ready_label))
        .replace("__QUICK_COMMANDS_HTML__", quick_commands_html)
        .replace(f"<title>SSH {safe_name}</title>", f"<title>{html.escape(page_title)}</title>")
        .replace(f"<h1>SSH · {safe_name}</h1>", f"<h1>{html.escape(header_title)}</h1>")
    )


def login_html(error=""):
    error_html = f"<div class=\"err\">{html.escape(error)}</div>" if error else ""
    captcha_code, captcha_token = captcha_challenge()
    safe_captcha_token = html.escape(captcha_token, quote=True)
    safe_captcha_code = html.escape(captcha_code, quote=True)
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#7c3aed">
<title>OpenWrt Remote Hub</title>
<style>
:root{{color-scheme:dark;--bg:#07040f;--panel:rgba(19,14,32,.9);--text:#f7f2ff;--muted:#b9adc9;--line:rgba(169,126,255,.28);--blue:#7c3aed;--cyan:#22d3ee;--red:#fb7185;--green:#22c55e;--grid:rgba(168,85,247,.13)}}
*{{box-sizing:border-box}}
body{{position:relative;min-height:100vh;margin:0;overflow:hidden;background-color:var(--bg);background-image:radial-gradient(circle at 16% 12%,rgba(168,85,247,.48),transparent 30%),radial-gradient(circle at 84% 8%,rgba(59,130,246,.34),transparent 32%),radial-gradient(circle at 55% 105%,rgba(236,72,153,.24),transparent 36%),linear-gradient(145deg,#07040f,#120a24 48%,#05030a),repeating-linear-gradient(0deg,transparent 0 30px,var(--grid) 31px),repeating-linear-gradient(90deg,transparent 0 30px,var(--grid) 31px);background-size:130% 130%,140% 140%,135% 135%,100% 100%,31px 31px,31px 31px;background-attachment:fixed;color:var(--text);font:14px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;display:grid;place-items:center;padding:18px;animation:bgFlow 28s ease-in-out infinite alternate}}
body::before{{content:"";position:fixed;inset:-28%;pointer-events:none;background:conic-gradient(from 0deg at 50% 50%,rgba(168,85,247,.06),rgba(236,72,153,.30),rgba(59,130,246,.22),rgba(34,211,238,.16),rgba(168,85,247,.06));filter:blur(58px);opacity:.74;animation:auraSpin 40s linear infinite}}
@keyframes bgFlow{{0%{{background-position:0% 0%,100% 0%,50% 100%,0 0,0 0,0 0}}50%{{background-position:24% 18%,62% 28%,38% 82%,0 0,15px 24px,24px 15px}}100%{{background-position:46% 30%,42% 42%,74% 62%,0 0,30px 0,0 30px}}}}
@keyframes auraSpin{{from{{transform:rotate(0deg) scale(1)}}to{{transform:rotate(360deg) scale(1.08)}}}}
.login{{position:relative;z-index:1;width:min(352px,100%);min-height:352px;padding:16px;border:1px solid var(--line);border-radius:8px;background:linear-gradient(180deg,rgba(255,255,255,.10),rgba(255,255,255,.045)),var(--panel);box-shadow:0 22px 64px rgba(0,0,0,.40);backdrop-filter:blur(14px)}}
.brand{{display:block;text-align:center;margin-bottom:14px}}
h1{{margin:0;font-size:18px;line-height:1.1;letter-spacing:0}}.appBanner{{position:relative;display:flex;align-items:center;justify-content:center;width:100%;min-height:54px;padding:10px 12px;border:1px solid rgba(34,211,238,.34);border-radius:8px;background:linear-gradient(110deg,rgba(34,211,238,.14),rgba(124,58,237,.24),rgba(236,72,153,.14));box-shadow:0 10px 24px rgba(124,58,237,.18),inset 0 1px 0 rgba(255,255,255,.10);font-size:17px;overflow:hidden}}.appBanner::before{{content:"";position:absolute;inset:0;background:linear-gradient(90deg,transparent,rgba(255,255,255,.18),transparent);transform:translateX(-120%);animation:bannerShine 6.2s ease-in-out infinite}}.appBanner span{{position:relative}}@keyframes bannerShine{{0%,45%{{transform:translateX(-120%)}}72%,100%{{transform:translateX(120%)}}}}
p{{margin:3px 0 0;color:var(--muted)}}
label{{display:block;margin:10px 0 5px;font-weight:850;color:#ede9fe;text-align:center}}
input{{width:100%;border:1px solid var(--line);border-radius:8px;padding:11px 12px;background:rgba(8,5,18,.74);color:var(--text);outline:none;text-align:center;box-shadow:inset 0 1px 0 rgba(255,255,255,.04)}}
input:focus{{border-color:rgba(34,211,238,.62);box-shadow:0 0 0 3px rgba(34,211,238,.12),inset 0 1px 0 rgba(255,255,255,.04)}}
.captcha{{width:100%;display:flex;align-items:center;justify-content:center;margin-top:0;border:1px solid var(--line);border-radius:8px;padding:11px 12px;background:rgba(8,5,18,.74);text-align:center;box-shadow:inset 0 1px 0 rgba(255,255,255,.04)}}.captcha b{{display:block;color:#fde68a;font:950 23px/1 ui-monospace,SFMono-Regular,Consolas,monospace;letter-spacing:6px;text-indent:6px;text-shadow:0 0 18px rgba(251,191,36,.28)}}
button{{width:100%;margin-top:13px;border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:11px 12px;background:linear-gradient(135deg,#7c3aed,#a855f7);color:#fff;font-weight:950;cursor:pointer;box-shadow:0 14px 30px rgba(124,58,237,.28)}}
button:hover{{filter:brightness(1.06)}}
.err{{margin:0 0 12px;padding:11px 12px;border:1px solid rgba(251,113,133,.45);border-radius:8px;background:rgba(251,113,133,.14);color:#fecdd3;font-weight:800}}
@media(max-width:520px){{body{{padding:14px}}.login{{padding:15px;min-height:0}}h1{{font-size:22px}}.login .brand .appBanner{{min-height:50px}}}}
</style>
</head>
<body>
<form class="login" method="post" action="/login">
  {error_html}
  <label for="hubUsername">Логин</label>
  <input id="hubUsername" name="username" autocomplete="username" autofocus required>
  <label for="hubPassword">Пароль</label>
  <input id="hubPassword" name="password" type="password" autocomplete="current-password" required>
  <label for="hubCaptchaCode">Капча: введи эти цифры</label>
  <div class="captcha" id="hubCaptchaCode" aria-live="polite"><b>{safe_captcha_code}</b></div>
  <input name="captcha_token" type="hidden" value="{safe_captcha_token}">
  <label for="hubCaptcha">Повтори капчу</label>
  <input id="hubCaptcha" name="captcha_answer" inputmode="numeric" pattern="[0-9]*" autocomplete="off" required>
  <button>Войти</button>
</form>
</body>
</html>"""


class App:
    def __init__(self, db_path, session, agent, public_url):
        self.db_path = Path(db_path)
        self.session_token = session
        self.agent_token = agent
        self.public_url = public_url.rstrip("/")

    def conn(self):
        conn = connect(self.db_path)
        init_db(conn)
        return conn


class Handler(BaseHTTPRequestHandler):
    server_version = "owrt-remote-hub/1.0"
    protocol_version = "HTTP/1.1"

    @property
    def app(self):
        return self.server.app

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args))

    def parsed(self):
        return urllib.parse.urlsplit(self.path)

    def query(self):
        return urllib.parse.parse_qs(self.parsed().query)

    def maybe_proxy_luci_absolute(self, path):
        if not any(path == root or path.startswith(root + "/") for root in LUCI_ABSOLUTE_ROOTS):
            return False
        router_id = ""
        ref_path = urllib.parse.urlsplit(self.headers.get("Referer", "")).path
        parts = ref_path.split("/", 3)
        if len(parts) >= 3 and parts[1] == "access" and parts[2]:
            router_id = parts[2]
        if not router_id:
            router_id = urllib.parse.unquote(parse_cookies(self.headers.get("Cookie", "")).get(ROUTER_COOKIE, ""))
        if router_id:
            self.proxy_access(f"/access/{router_id}{path}")
            return True
        return False

    def client_ip(self):
        forwarded = self.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
        try:
            return self.client_address[0]
        except Exception:
            return ""

    def current_session_token(self):
        return parse_cookies(self.headers.get("Cookie", "")).get(SESSION_COOKIE, "")

    def current_hub_session(self, touch=True):
        return verify_hub_session(
            self.current_session_token(),
            touch=touch,
            ip=self.client_ip() if touch else "",
            user_agent=self.headers.get("User-Agent", "") if touch else "",
        )

    def legacy_admin_ok(self):
        return secrets.compare_digest(self.current_session_token(), self.app.session_token)

    def admin_ok(self):
        return bool(self.current_hub_session()) or self.legacy_admin_ok()

    def ssh_token_ok(self, router_id):
        token = self.query().get("t", [""])[0]
        if not token:
            return False
        return secrets.compare_digest(token, ssh_ws_token(self.app.session_token, router_id))

    def agent_ok(self):
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return secrets.compare_digest(auth[7:].strip(), self.app.agent_token)
        return False

    def send_bytes(self, status, body, content_type="text/plain; charset=utf-8", extra_headers=None):
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        if extra_headers:
            for key, value in extra_headers:
                if key.lower() in {"connection", "content-length", "content-type", "cache-control"}:
                    continue
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                pass

    def send_raw_bytes(self, status, body, content_type="application/octet-stream", extra_headers=None):
        self.close_connection = True
        reason = self.responses.get(status, ("OK",))[0]
        headers = [
            f"{self.protocol_version} {status} {reason}",
            f"Server: {self.version_string()}",
            f"Date: {self.date_time_string()}",
            f"Content-Type: {content_type}",
            "Cache-Control: no-store",
            "Connection: close",
            f"Content-Length: {len(body)}",
        ]
        if extra_headers:
            for key, value in extra_headers:
                low = key.lower()
                if low in {"connection", "content-length", "content-type", "cache-control"}:
                    continue
                safe_key = str(key).replace("\r", "").replace("\n", "")
                safe_value = str(value).replace("\r", "").replace("\n", "")
                headers.append(f"{safe_key}: {safe_value}")
        raw = ("\r\n".join(headers) + "\r\n\r\n").encode("iso-8859-1", "replace")
        if self.command != "HEAD":
            raw += body
        try:
            self.request.sendall(raw)
            self.log_request(status, len(body))
        except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
            pass

    def send_text(self, status, text, content_type="text/plain; charset=utf-8"):
        self.send_bytes(status, text.encode("utf-8"), content_type)

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_bytes(status, body, "application/json; charset=utf-8")

    def require_admin(self):
        if self.admin_ok():
            return True
        self.send_bytes(401, login_html().encode("utf-8"), "text/html; charset=utf-8")
        return False

    def read_body(self):
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            chunks = []
            while True:
                line = self.rfile.readline().strip()
                if not line:
                    continue
                size = int(line.split(b";", 1)[0], 16)
                if size == 0:
                    while self.rfile.readline().strip():
                        pass
                    break
                chunks.append(self.rfile.read(size))
                self.rfile.read(2)
            return b"".join(chunks)
        length = int(self.headers.get("Content-Length", "0") or "0")
        return self.rfile.read(length) if length else b""

    def read_payload(self):
        body = self.read_body()
        ctype = self.headers.get("Content-Type", "")
        if "application/json" in ctype:
            return json.loads(body.decode("utf-8") or "{}")
        parsed = urllib.parse.parse_qs(body.decode("utf-8"))
        return {k: v[-1] for k, v in parsed.items()}

    def session_cookie(self, token):
        return f"{SESSION_COOKIE}={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={SESSION_TTL_SECONDS}"

    def clear_session_cookie(self):
        return f"{SESSION_COOKIE}=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"

    def serve_acme_challenge(self, path):
        prefix = "/.well-known/acme-challenge/"
        token = urllib.parse.unquote(path[len(prefix):])
        if not token or "/" in token or "\\" in token:
            self.send_text(404, "not found")
            return
        challenge_path = ACME_WEBROOT / ".well-known" / "acme-challenge" / token
        try:
            body = challenge_path.read_bytes()
        except OSError:
            self.send_text(404, "not found")
            return
        self.send_bytes(200, body, "text/plain; charset=utf-8")

    def redirect(self, location, extra_headers=None):
        self.close_connection = True
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        if extra_headers:
            for key, value in extra_headers:
                self.send_header(key, value)
        self.end_headers()

    def login(self):
        payload = self.read_payload()
        username = payload.get("username", "")
        password = payload.get("password", "")
        captcha_token = payload.get("captcha_token", "")
        captcha_answer = payload.get("captcha_answer", "")
        if not verify_captcha(captcha_token, captcha_answer):
            self.send_bytes(401, login_html("Неверная капча").encode("utf-8"), "text/html; charset=utf-8")
            return
        if verify_login(username, password):
            token, session = make_hub_session(username, self.client_ip(), self.headers.get("User-Agent", ""))
            add_notification(
                "login",
                "Вход в Hub",
                f"{session.get('client', 'устройство')} · IP {session.get('ip', 'unknown')}",
                "warn",
                [session.get("user_agent", "")],
                {"session_id": session.get("id", ""), "ip": session.get("ip", "")},
            )
            self.redirect("/", [("Set-Cookie", self.session_cookie(token))])
            return
        self.send_bytes(401, login_html("Неверный логин или пароль").encode("utf-8"), "text/html; charset=utf-8")

    def update_auth(self):
        payload = self.read_payload()
        auth = load_auth()
        current_password = payload.get("current_password", "")
        if not verify_login(auth.get("username", ""), current_password):
            self.send_text(403, "Текущий пароль неверный")
            return
        username = payload.get("username", auth.get("username", "admin"))
        new_password = payload.get("password", "")
        confirm = payload.get("password_confirm", "")
        if new_password:
            if new_password != confirm:
                self.send_text(400, "Новый пароль и повтор не совпадают")
                return
            if len(new_password) < MIN_PASSWORD_LENGTH:
                self.send_text(400, f"Новый пароль должен быть минимум {MIN_PASSWORD_LENGTH} символа")
                return
            save_auth(username, new_password)
        else:
            clean = clean_username(username)
            auth["username"] = clean
            auth["updated_at"] = now_ts()
            write_json_private(AUTH_FILE, auth)
        self.send_text(200, "Доступ к Hub обновлен")

    def router_id_from_path(self, prefix):
        suffix = self.parsed().path[len(prefix):].strip("/")
        if not suffix:
            return ""
        return urllib.parse.unquote(suffix.split("/", 1)[0])

    def ssh_page(self):
        if not self.require_admin():
            return
        router_id = self.router_id_from_path("/ssh/")
        with self.app.conn() as conn:
            row = get_active_router(conn, router_id)
        if not row:
            self.send_text(404, "router not found")
            return
        if int(row["ssh_entry_port"] or 0) <= 0:
            self.send_text(400, "router has no ssh_entry_port")
            return
        ws_token = ssh_ws_token(self.app.session_token, router_id)
        self.send_bytes(
            200,
            ssh_terminal_html_v2(row, ws_token).encode("utf-8"),
            "text/html; charset=utf-8",
            [("Set-Cookie", current_router_cookie(router_id))],
        )

    def vps_terminal_page(self):
        if not self.require_admin():
            return
        row = vps_terminal_row()
        ws_token = ssh_ws_token(self.app.session_token, row["id"])
        public_host = urllib.parse.urlsplit(self.app.public_url).hostname if self.app.public_url else ""
        if not public_host:
            public_host = self.headers.get("Host", "").split(":", 1)[0]
        quick_commands_html = vps_quick_commands_html(public_host or "YOUR_VPS_IP")
        self.send_bytes(200, ssh_terminal_html_v2(row, ws_token, quick_commands_html).encode("utf-8"), "text/html; charset=utf-8")

    def ssh_ws(self):
        try:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        router_id = self.router_id_from_path("/ssh-ws/")
        if not (self.admin_ok() or self.ssh_token_ok(router_id)):
            self.send_response(403)
            self.end_headers()
            return
        if not is_vps_terminal_id(router_id):
            with self.app.conn() as conn:
                row = get_active_router(conn, router_id)
            if not row:
                self.send_response(404)
                self.end_headers()
                return
            port = int(row["ssh_entry_port"] or 0)
            if port <= 0:
                self.send_response(400)
                self.end_headers()
                return
        key = self.headers.get("Sec-WebSocket-Key", "")
        if not key:
            self.send_response(400)
            self.end_headers()
            return
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", ws_accept_value(key))
        self.end_headers()
        self.close_connection = True
        if is_vps_terminal_id(router_id):
            self.run_vps_terminal_session()
            return
        self.run_ssh_session(router_id, port)

    def ssh_check(self):
        router_id = self.router_id_from_path("/api/ssh/")
        if router_id.endswith("/check"):
            router_id = router_id[:-6].rstrip("/")
        if not (self.admin_ok() or self.ssh_token_ok(router_id)):
            self.send_json(403, {"ok": False, "error": "not authorized", "tcp_ok": False})
            return
        if is_vps_terminal_id(router_id):
            self.send_json(200, {"ok": True, "router_id": router_id, "tcp_ok": True, "mode": "local-shell"})
            return
        with self.app.conn() as conn:
            row = get_active_router(conn, router_id)
        if not row:
            self.send_json(404, {"ok": False, "error": "router not found", "tcp_ok": False})
            return
        port = int(row["ssh_entry_port"] or 0)
        if port <= 0:
            self.send_json(200, {"ok": False, "error": "router has no ssh_entry_port", "tcp_ok": False})
            return
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=3):
                pass
            self.send_json(200, {"ok": True, "router_id": router_id, "port": port, "tcp_ok": True})
        except Exception as exc:
            self.send_json(200, {"ok": False, "router_id": router_id, "port": port, "tcp_ok": False, "error": str(exc)})

    def ssh_args(self, port):
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        args = [
            "ssh",
            "-tt",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "GlobalKnownHostsFile=/dev/null",
            "-o",
            "LogLevel=ERROR",
            "-o",
            "ServerAliveInterval=15",
            "-p",
            str(port),
            "root@127.0.0.1",
        ]
        return env, args

    def ssh_exec_args(self, port, allow_password=False):
        env = os.environ.copy()
        env.setdefault("LC_ALL", "C.UTF-8")
        args = [
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "GlobalKnownHostsFile=/dev/null",
            "-o",
            "LogLevel=ERROR",
            "-o",
            "ConnectTimeout=8",
            "-o",
            "NumberOfPasswordPrompts=1",
            "-p",
            str(port),
            "root@127.0.0.1",
        ]
        if allow_password:
            args[1:1] = [
                "-o",
                "PreferredAuthentications=publickey,password,keyboard-interactive",
            ]
        else:
            args[1:1] = ["-o", "BatchMode=yes"]
        return env, args

    def prepare_ssh_askpass(self, env, ssh_password):
        password = str(ssh_password or "")
        if not password:
            return None
        askpass_dir = Path(tempfile.mkdtemp(prefix="owrt-askpass-"))
        askpass_script = askpass_dir / "askpass.sh"
        askpass_script.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$OWRT_REMOTE_SSH_PASSWORD\"\n",
            encoding="utf-8",
        )
        os.chmod(askpass_script, 0o700)
        env["SSH_ASKPASS"] = str(askpass_script)
        env["SSH_ASKPASS_REQUIRE"] = "force"
        env["DISPLAY"] = env.get("DISPLAY") or "owrt-remote:0"
        env["OWRT_REMOTE_SSH_PASSWORD"] = password
        return askpass_dir

    def normalize_wol_mac(self, value):
        mac = str(value or "").strip().upper().replace("-", ":")
        if not mac:
            return ""
        compact = re.sub(r"[^0-9A-F]", "", mac)
        if len(compact) == 12:
            mac = ":".join(compact[idx : idx + 2] for idx in range(0, 12, 2))
        if not re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", mac):
            return ""
        if mac == "00:00:00:00:00:00":
            return ""
        return mac

    def run_router_ssh_script(self, row, script, script_args=None, timeout=15, ssh_password=""):
        port = int(row["ssh_entry_port"] or 0) if row else 0
        if port <= 0:
            raise RuntimeError("router has no ssh_entry_port")
        password = str(ssh_password or "")
        env, args = self.ssh_exec_args(port, allow_password=bool(password))
        askpass_dir = self.prepare_ssh_askpass(env, password)
        argv = args + ["sh", "-s", "--"] + [str(item) for item in (script_args or [])]
        try:
            result = subprocess.run(
                argv,
                input=str(script),
                text=True,
                capture_output=True,
                timeout=float(timeout),
                env=env,
                start_new_session=bool(password),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"ssh command timeout after {int(timeout)}s") from exc
        finally:
            if askpass_dir:
                try:
                    for child in askpass_dir.iterdir():
                        child.unlink(missing_ok=True)
                    askpass_dir.rmdir()
                except OSError:
                    pass
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(message or f"ssh command exited with code {result.returncode}")
        return result.stdout

    def discover_router_wol_devices(self, row, ssh_password=""):
        script = r"""
set -eu
tmp="${TMPDIR:-/tmp}/owrt-wol.$$.txt"
cleanup() {
  rm -f "$tmp"
}
trap cleanup EXIT INT TERM
: >"$tmp"

if [ -f /tmp/dhcp.leases ]; then
  while read -r lease_ts mac ip host client_id; do
    [ -n "${mac:-}" ] || continue
    [ "${mac:-}" = "*" ] && continue
    [ "${host:-}" = "*" ] && host=""
    printf '%s\t%s\t%s\t%s\t%s\n' "$mac" "${ip:-}" "${host:-}" "" "dhcp" >>"$tmp"
  done </tmp/dhcp.leases
fi

if command -v uci >/dev/null 2>&1; then
  uci -q show dhcp | sed -n "s/^\(dhcp\.[^=]*\)=host$/\1/p" | while IFS= read -r section; do
    [ -n "$section" ] || continue
    mac_list="$(uci -q get "$section.mac" 2>/dev/null || true)"
    ip_static="$(uci -q get "$section.ip" 2>/dev/null || true)"
    host_static="$(uci -q get "$section.name" 2>/dev/null || true)"
    [ -n "$mac_list" ] || continue
    old_ifs="$IFS"
    IFS=', '
    for mac_item in $mac_list; do
      [ -n "$mac_item" ] || continue
      printf '%s\t%s\t%s\t%s\t%s\n' "$mac_item" "${ip_static:-}" "${host_static:-}" "" "static-dhcp" >>"$tmp"
    done
    IFS="$old_ifs"
  done
fi

if command -v ip >/dev/null 2>&1; then
  ip neigh show 2>/dev/null | awk '
    BEGIN { OFS="\t" }
    {
      ip="";
      dev="";
      mac="";
      if (NF >= 1) ip=$1;
      for (i = 1; i <= NF; i++) {
        if ($i == "dev" && i < NF) dev=$(i + 1);
        if ($i == "lladdr" && i < NF) mac=$(i + 1);
      }
      if (mac != "") print mac, ip, "", dev, "ip-neigh";
    }
  ' >>"$tmp"
fi

if [ -r /proc/net/arp ]; then
  awk 'NR > 1 && $4 != "" && $4 != "00:00:00:00:00:00" {
    printf "%s\t%s\t\t%s\tarp\n", $4, $1, $6
  }' /proc/net/arp >>"$tmp"
fi

cat "$tmp"
"""
        raw = self.run_router_ssh_script(row, script, timeout=18, ssh_password=ssh_password)
        devices = {}
        for line in raw.splitlines():
            if not line.strip():
                continue
            parts = [part.strip() for part in line.split("\t")]
            while len(parts) < 5:
                parts.append("")
            mac = self.normalize_wol_mac(parts[0])
            if not mac:
                continue
            ip_addr = parts[1]
            name = parts[2]
            iface = parts[3]
            source = parts[4] or "unknown"
            entry = devices.get(mac)
            if entry is None:
                entry = {"mac": mac, "ip": "", "name": "", "iface": "", "sources": []}
                devices[mac] = entry
            if ip_addr and not entry["ip"]:
                entry["ip"] = ip_addr
            if name and not entry["name"]:
                entry["name"] = name
            if iface and not entry["iface"]:
                entry["iface"] = iface
            if source and source not in entry["sources"]:
                entry["sources"].append(source)
        ordered = sorted(
            devices.values(),
            key=lambda item: (
                str(item.get("name") or "").lower(),
                str(item.get("ip") or ""),
                str(item.get("mac") or ""),
            ),
        )
        for item in ordered:
            label_parts = []
            if item.get("name"):
                label_parts.append(item["name"])
            if item.get("ip"):
                label_parts.append(item["ip"])
            label_parts.append(item["mac"])
            item["label"] = " | ".join(part for part in label_parts if part)
            item["source"] = ", ".join(item.pop("sources", []))
        return ordered

    def send_router_wol_packet(self, row, mac, iface="", ssh_password=""):
        clean_mac = self.normalize_wol_mac(mac)
        if not clean_mac:
            raise RuntimeError("invalid MAC address")
        script = r"""
set -eu
mac="$1"
iface="${2:-}"
if command -v etherwake >/dev/null 2>&1; then
  if [ -n "$iface" ]; then
    etherwake -i "$iface" "$mac"
  else
    etherwake "$mac"
  fi
  echo "Wake packet sent via etherwake"
  exit 0
fi
if command -v wakeonlan >/dev/null 2>&1; then
  wakeonlan "$mac"
  echo "Wake packet sent via wakeonlan"
  exit 0
fi
if command -v wol >/dev/null 2>&1; then
  wol "$mac"
  echo "Wake packet sent via wol"
  exit 0
fi
echo "No WOL tool found on router. Install etherwake, wakeonlan or wol." >&2
exit 127
"""
        output = self.run_router_ssh_script(
            row,
            script,
            [clean_mac, str(iface or "")],
            timeout=12,
            ssh_password=ssh_password,
        )
        return {"mac": clean_mac, "iface": str(iface or ""), "output": output.strip() or "Wake packet sent"}

    def vps_shell_args(self):
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        shell = env.get("SHELL", "")
        if not shell:
            shell = "/bin/bash" if Path("/bin/bash").exists() else "/bin/sh"
        args = [shell]
        if os.path.basename(shell) in {"bash", "sh", "ash", "dash", "zsh", "ksh"}:
            args.append("-l")
        return env, args

    def spawn_terminal_pty(self, env, args, unavailable_message, open_message, exec_name):
        try:
            import pty
        except Exception as exc:
            raise RuntimeError(f"{unavailable_message}: {exc}")
        try:
            pid, fd = pty.fork()
        except Exception as exc:
            raise RuntimeError(f"{open_message}: {exc}")
        if pid == 0:
            try:
                os.execvpe(args[0], args, env)
            except Exception as exc:
                print(f"{exec_name} start failed: {exc}", flush=True)
                os._exit(127)
        return pid, fd

    def ssh_http_reader(self, sid):
        session = SSH_HTTP_SESSIONS.get(sid)
        if not session:
            return
        fd = session["fd"]
        try:
            while True:
                ready, _, _ = select.select([fd], [], [], 0.5)
                if fd not in ready:
                    with session["lock"]:
                        if not session["alive"]:
                            break
                    continue
                try:
                    data = os.read(fd, 4096)
                except OSError as exc:
                    with session["lock"]:
                        session["buffer"].append(f"\r\n[{session.get('label', 'Terminal')} read error: {exc}]\r\n")
                    break
                if not data:
                    break
                with session["lock"]:
                    session["buffer"].append(data.decode("utf-8", errors="replace"))
                    session["last_seen"] = now_ts()
        finally:
            with session["lock"]:
                session["alive"] = False
                session["buffer"].append("\r\n[" + session.get("close_notice", "SSH соединение закрыто") + "]\r\n")
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.waitpid(session["pid"], os.WNOHANG)
            except OSError:
                pass

    def start_ssh_http_session(self, router_id, port):
        env, args = self.ssh_args(port)
        pid, fd = self.spawn_terminal_pty(env, args, "pty недоступен на VPS", "не удалось открыть SSH pty", "ssh")
        sid = secrets.token_urlsafe(24)
        session = {
            "id": sid,
            "router_id": router_id,
            "port": port,
            "pid": pid,
            "fd": fd,
            "buffer": [],
            "alive": True,
            "created": now_ts(),
            "last_seen": now_ts(),
            "lock": threading.Lock(),
            "label": "SSH",
            "close_notice": "SSH соединение закрыто",
        }
        with SSH_HTTP_LOCK:
            SSH_HTTP_SESSIONS[sid] = session
        threading.Thread(target=self.ssh_http_reader, args=(sid,), daemon=True).start()
        return session

    def start_vps_http_session(self):
        env, args = self.vps_shell_args()
        pid, fd = self.spawn_terminal_pty(env, args, "pty недоступен на VPS", "не удалось открыть VPS pty", "vps-shell")
        sid = secrets.token_urlsafe(24)
        session = {
            "id": sid,
            "router_id": VPS_TERMINAL_ID,
            "port": 0,
            "pid": pid,
            "fd": fd,
            "buffer": [],
            "alive": True,
            "created": now_ts(),
            "last_seen": now_ts(),
            "lock": threading.Lock(),
            "label": "VPS terminal",
            "close_notice": "VPS terminal закрыт",
        }
        with SSH_HTTP_LOCK:
            SSH_HTTP_SESSIONS[sid] = session
        threading.Thread(target=self.ssh_http_reader, args=(sid,), daemon=True).start()
        return session

    def ssh_http_session(self):
        router_id = self.router_id_from_path("/api/ssh/")
        if not (self.admin_ok() or self.ssh_token_ok(router_id)):
            self.send_json(403, {"ok": False, "error": "not authorized"})
            return
        if is_vps_terminal_id(router_id):
            try:
                session = self.start_vps_http_session()
                query = self.query()
                set_pty_size(session["fd"], query.get("rows", ["24"])[0], query.get("cols", ["80"])[0])
                self.send_json(200, {"ok": True, "sid": session["id"], "router_id": router_id, "mode": "local-shell"})
            except Exception as exc:
                self.send_json(500, {"ok": False, "error": str(exc)})
            return
        with self.app.conn() as conn:
            row = get_active_router(conn, router_id)
        if not row:
            self.send_json(404, {"ok": False, "error": "router not found"})
            return
        port = int(row["ssh_entry_port"] or 0)
        if port <= 0:
            self.send_json(400, {"ok": False, "error": "router has no ssh_entry_port"})
            return
        try:
            session = self.start_ssh_http_session(router_id, port)
            query = self.query()
            set_pty_size(session["fd"], query.get("rows", ["24"])[0], query.get("cols", ["80"])[0])
            self.send_json(200, {"ok": True, "sid": session["id"], "router_id": router_id, "port": port})
        except Exception as exc:
            self.send_json(500, {"ok": False, "error": str(exc)})

    def ssh_http_read(self, sid):
        with SSH_HTTP_LOCK:
            session = SSH_HTTP_SESSIONS.get(sid)
        if not session:
            self.send_json(404, {"ok": False, "error": "terminal session not found", "alive": False, "data": ""})
            return
        with session["lock"]:
            data = "".join(session["buffer"])
            session["buffer"].clear()
            alive = bool(session["alive"])
            session["last_seen"] = now_ts()
        self.send_json(200, {"ok": True, "alive": alive, "data": data})

    def ssh_http_write(self, sid, payload=None):
        with SSH_HTTP_LOCK:
            session = SSH_HTTP_SESSIONS.get(sid)
        if not session:
            self.send_json(404, {"ok": False, "error": "terminal session not found"})
            return
        if payload is None:
            payload = self.read_payload()
        data = payload.get("data", "")
        if not isinstance(data, str):
            data = str(data)
        with session["lock"]:
            alive = bool(session["alive"])
            fd = session["fd"]
        if not alive:
            self.send_json(409, {"ok": False, "error": "terminal session closed"})
            return
        try:
            write_pty_all(fd, data)
            self.send_json(200, {"ok": True})
        except Exception as exc:
            self.send_json(500, {"ok": False, "error": str(exc)})

    def ssh_http_resize(self, sid, payload=None):
        with SSH_HTTP_LOCK:
            session = SSH_HTTP_SESSIONS.get(sid)
        if not session:
            self.send_json(404, {"ok": False, "error": "terminal session not found"})
            return
        if payload is None:
            payload = self.read_payload()
        ok = set_pty_size(session["fd"], payload.get("rows", 24), payload.get("cols", 80))
        self.send_json(200, {"ok": bool(ok)})

    def ssh_http_write_short(self):
        payload = self.read_payload()
        sid = payload.get("sid", "")
        if not sid:
            sid = self.query().get("sid", [""])[0]
        if not sid:
            self.send_json(400, {"ok": False, "error": "terminal sid is empty"})
            return
        self.ssh_http_write(sid, payload)

    def ssh_http_close(self, sid):
        with SSH_HTTP_LOCK:
            session = SSH_HTTP_SESSIONS.pop(sid, None)
        if not session:
            self.send_json(200, {"ok": True})
            return
        with session["lock"]:
            session["alive"] = False
        try:
            os.kill(session["pid"], signal.SIGHUP)
        except OSError:
            pass
        self.send_json(200, {"ok": True})

    def ssh_session_action(self, path):
        parts = path.strip("/").split("/")
        if len(parts) != 4 or parts[0] != "api" or parts[1] != "ssh-session":
            self.send_json(404, {"ok": False, "error": "terminal route not found"})
            return
        sid = urllib.parse.unquote(parts[2])
        action = parts[3]
        if action == "read":
            self.ssh_http_read(sid)
            return
        if action == "write":
            self.ssh_http_write(sid)
            return
        if action == "resize":
            self.ssh_http_resize(sid)
            return
        if action == "close":
            self.ssh_http_close(sid)
            return
        self.send_json(404, {"ok": False, "error": "terminal action not found"})

    def run_terminal_session(self, env, args, unavailable_message, open_message, exec_name, error_label):
        ensure_state()
        try:
            pid, fd = self.spawn_terminal_pty(env, args, unavailable_message, open_message, exec_name)
        except Exception as exc:
            ws_send_frame(self.connection, f"{exc}\r\n")
            return
        ws_send_frame(self.connection, "")
        try:
            while True:
                ready, _, _ = select.select([self.connection, fd], [], [], 0.25)
                if fd in ready:
                    try:
                        data = os.read(fd, 4096)
                    except OSError:
                        break
                    if not data:
                        break
                    ws_send_frame(self.connection, data.decode("utf-8", errors="replace"))
                if self.connection in ready:
                    try:
                        opcode, payload = ws_read_frame(self.connection)
                    except Exception:
                        break
                    if opcode == 8:
                        break
                    if opcode == 9:
                        ws_send_frame(self.connection, payload, opcode=10)
                        continue
                    if opcode == 1 and payload:
                        resize = parse_resize_payload(payload)
                        if resize:
                            set_pty_size(fd, resize[0], resize[1])
                            continue
                    if opcode in (1, 2) and payload:
                        write_pty_all(fd, payload)
        except Exception as exc:
            try:
                ws_send_frame(self.connection, f"\r\n[{error_label}: {exc}]\r\n")
            except Exception:
                pass
        finally:
            try:
                os.kill(pid, signal.SIGHUP)
            except OSError:
                pass
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.waitpid(pid, os.WNOHANG)
            except OSError:
                pass

    def run_ssh_session(self, router_id, port):
        env, args = self.ssh_args(port)
        self.run_terminal_session(env, args, "pty недоступен на VPS", "не удалось открыть SSH pty", "ssh", "SSH error")

    def run_vps_terminal_session(self):
        env, args = self.vps_shell_args()
        self.run_terminal_session(env, args, "pty недоступен на VPS", "не удалось открыть VPS pty", "vps-shell", "VPS terminal error")

    def backup_download(self):
        with tempfile.TemporaryDirectory(prefix="owrt-hub-download-") as tmp_name:
            filename = backup_filename()
            archive = Path(tmp_name) / filename
            create_hub_backup(archive, self.app.db_path)
            body = archive.read_bytes()
        self.send_bytes(
            200,
            body,
            "application/gzip",
            [("Content-Disposition", f'attachment; filename="{filename}"')],
        )

    def backup_restore(self):
        try:
            payload = self.read_payload()
            archive_b64 = str(payload.get("archive_b64") or "")
            if not archive_b64:
                self.send_json(400, {"ok": False, "error": "archive_b64 is empty"})
                return
            if len(archive_b64) > 80 * 1024 * 1024:
                self.send_json(413, {"ok": False, "error": "backup archive is too large"})
                return
            raw = base64.b64decode(archive_b64.encode("ascii"), validate=True)
            with tempfile.TemporaryDirectory(prefix="owrt-hub-upload-") as tmp_name:
                archive = Path(tmp_name) / "restore.tar.gz"
                archive.write_bytes(raw)
                result = restore_hub_backup(
                    archive,
                    self.app.db_path,
                    payload.get("vps_host", ""),
                    payload.get("public_url", ""),
                )
            self.app.session_token = session_token()
            self.app.agent_token = agent_token()
            add_notification(
                "backup-restore",
                "Hub backup восстановлен",
                "Состояние Hub восстановлено из резервной копии. Перезапусти owrt-remote на VPS для чистого применения.",
                "warn",
                result.get("warnings", []),
                {"rewritten": result.get("rewritten", {})},
            )
            self.send_json(200, {"ok": True, **result})
        except Exception as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})

    def do_GET(self):
        path = self.parsed().path
        if path == "/health":
            self.send_json(200, {"ok": True})
            return
        if path == "/favicon.svg":
            self.send_bytes(200, favicon_svg().encode("utf-8"), "image/svg+xml; charset=utf-8")
            return
        if path == "/favicon.ico":
            self.send_response(302)
            self.send_header("Location", "/favicon.svg")
            self.end_headers()
            return
        if path == "/sw.js":
            self.send_bytes(
                200,
                service_worker_js().encode("utf-8"),
                "application/javascript; charset=utf-8",
                [("Service-Worker-Allowed", "/")],
            )
            return
        if path == "/manifest.webmanifest":
            self.send_bytes(200, web_manifest_json().encode("utf-8"), "application/manifest+json; charset=utf-8")
            return
        if path.startswith("/.well-known/acme-challenge/"):
            self.serve_acme_challenge(path)
            return
        if path == "/login":
            self.send_bytes(200, login_html().encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/logout":
            revoke_hub_session(token=self.current_session_token())
            self.redirect("/login", [("Set-Cookie", self.clear_session_cookie())])
            return
        if path == "/vps-terminal":
            self.redirect("/vps-terminal/")
            return
        if path == "/vps-terminal/":
            self.vps_terminal_page()
            return
        if path.startswith("/api/ssh/") and path.endswith("/check"):
            self.ssh_check()
            return
        if path.startswith("/api/ssh/") and path.endswith("/session"):
            self.ssh_http_session()
            return
        if path == "/api/ssh-session-write":
            self.ssh_http_write_short()
            return
        if path.startswith("/api/ssh-session/"):
            self.ssh_session_action(path)
            return
        if path.startswith("/ssh-ws/"):
            self.ssh_ws()
            return
        if path.startswith("/ssh/"):
            self.ssh_page()
            return
        if path.startswith("/access/"):
            self.proxy_access(path)
            return
        if self.maybe_proxy_luci_absolute(path):
            return
        if not self.require_admin():
            return
        if path == "/" or path == "":
            extra_headers = []
            session_token_value = self.current_session_token()
            if self.legacy_admin_ok() and not self.current_hub_session(touch=False):
                session_token_value, session = make_hub_session(current_username(), self.client_ip(), self.headers.get("User-Agent", ""))
                add_notification(
                    "login",
                    "Вход в Hub",
                    f"{session.get('client', 'устройство')} · IP {session.get('ip', 'unknown')}",
                    "warn",
                    [session.get("user_agent", "")],
                    {"session_id": session.get("id", ""), "ip": session.get("ip", ""), "legacy": True},
                )
                extra_headers.append(("Set-Cookie", self.session_cookie(session_token_value)))
            with self.app.conn() as conn:
                routers = [row_to_router(r) for r in list_router_rows(conn)]
            self.send_bytes(
                200,
                dashboard_html(
                    routers,
                    current_username(),
                    list_hub_sessions(session_token_value),
                    list_notifications(0, 40),
                ).encode("utf-8"),
                "text/html; charset=utf-8",
                extra_headers,
            )
            return
        if path == "/api/routers":
            with self.app.conn() as conn:
                routers = [row_to_router(r) for r in list_router_rows(conn)]
            self.send_json(200, {"routers": routers})
            return
        if path == "/api/backup/download":
            self.backup_download()
            return
        if path == "/api/sessions":
            self.send_json(200, {"sessions": list_hub_sessions(self.current_session_token())})
            return
        if path == "/api/notifications":
            query = self.query()
            self.send_json(
                200,
                {
                    "notifications": list_notifications(
                        query.get("after", ["0"])[0],
                        query.get("limit", ["60"])[0],
                    )
                },
            )
            return
        if path == "/api/push/vapid-public-key":
            public_key = vapid_public_key()
            if not webpush:
                self.send_json(503, {"ok": False, "error": "На VPS не установлен Web Push модуль. Запусти свежий install-vps.sh.", "publicKey": public_key})
                return
            if not public_key:
                self.send_json(503, {"ok": False, "error": "Не смог создать VAPID ключи на VPS", "publicKey": ""})
                return
            self.send_json(200, {"ok": True, "publicKey": public_key})
            return
        if path.startswith("/api/router/") and path.endswith("/wol/devices"):
            parts = path.strip("/").split("/")
            if len(parts) != 5 or parts[0] != "api" or parts[1] != "router" or parts[3] != "wol" or parts[4] != "devices":
                self.send_text(404, "not found")
                return
            router_id = urllib.parse.unquote(parts[2])
            with self.app.conn() as conn:
                row = get_active_router(conn, router_id)
            if not row:
                self.send_json(404, {"ok": False, "error": "router not found"})
                return
            try:
                devices = self.discover_router_wol_devices(row)
                self.send_json(200, {"ok": True, "router_id": router_id, "devices": devices})
            except Exception as exc:
                self.send_json(400, {"ok": False, "error": str(exc), "router_id": router_id, "devices": []})
            return
        if path.startswith("/router/"):
            self.router_asset(path)
            return
        self.send_text(404, "not found")

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        path = self.parsed().path
        if path == "/login":
            self.login()
            return
        if path == "/api/ssh-session-write":
            self.ssh_http_write_short()
            return
        if path.startswith("/api/ssh-session/"):
            self.ssh_session_action(path)
            return
        if path == "/api/heartbeat":
            if not self.agent_ok():
                self.send_json(401, {"ok": False, "error": "bad agent token"})
                return
            try:
                payload = self.read_payload()
                with self.app.conn() as conn:
                    router = heartbeat(conn, payload)
                xray_reload = None
                if router and router.pop("_xray_reload_required", False):
                    try:
                        xray_reload = reload_vps_xray(self.app.db_path)
                    except Exception as exc:
                        self.log_message("heartbeat xray reload error: %s", exc)
                        xray_reload = {"error": str(exc)}
                xray_restart = None
                if payload.get("event") == "wan-reconnect":
                    try:
                        xray_restart = maybe_restart_vps_xray_after_wan_reconnect(router["id"])
                    except Exception as exc:
                        self.log_message("wan reconnect xray restart error: %s", exc)
                        xray_restart = {"error": str(exc)}
                self.send_json(
                    200,
                    {
                        "ok": True,
                        "router": router,
                        "xray_reload": xray_reload,
                        "xray_restart": xray_restart,
                    },
                )
            except Exception as exc:
                self.log_message("heartbeat error: %s", exc)
                self.send_json(400, {"ok": False, "error": str(exc)})
            return
        if path.startswith("/access/"):
            self.proxy_access(path)
            return
        if self.maybe_proxy_luci_absolute(path):
            return
        if not self.require_admin():
            return
        if path == "/api/auth":
            self.update_auth()
            return
        if path == "/api/session/revoke":
            payload = self.read_payload()
            session_id = payload.get("id", "")
            if not session_id:
                self.send_text(400, "session id is empty")
                return
            current = self.current_hub_session(touch=False)
            if current and session_id == current.get("id"):
                self.send_text(400, "Текущую сессию заверши кнопкой Выйти")
                return
            removed = revoke_hub_session(session_id=session_id)
            self.send_json(200, {"ok": True, "removed": removed})
            return
        if path == "/api/session/revoke-others":
            current = self.current_hub_session(touch=False)
            current_id = current.get("id", "") if current else ""
            removed = 0
            for session in list_hub_sessions(self.current_session_token()):
                if session.get("id") != current_id:
                    removed += revoke_hub_session(session_id=session.get("id", ""))
            self.send_json(200, {"ok": True, "removed": removed})
            return
        if path == "/api/notifications/clear":
            clear_notifications()
            self.send_json(200, {"ok": True})
            return
        if path == "/api/backup/restore":
            self.backup_restore()
            return
        if path == "/api/push/subscribe":
            try:
                payload = self.read_payload()
                session = self.current_hub_session(touch=False) or {}
                subscription = save_push_subscription(
                    payload,
                    session.get("username", current_username()),
                    self.client_ip(),
                    self.headers.get("User-Agent", ""),
                )
                queue_web_push_payload(
                    {
                        "title": APP_NAME,
                        "body": "Push включён на этом устройстве.",
                        "tag": "owrt-push-test",
                        "url": "/",
                        "kind": "push-test",
                        "ts": now_ts(),
                    },
                    [subscription],
                )
                self.send_json(200, {"ok": True, "subscription": {"id": subscription.get("id"), "client": subscription.get("client")}})
            except Exception as exc:
                self.send_json(400, {"ok": False, "error": str(exc)})
            return
        if path == "/api/push/unsubscribe":
            try:
                payload = self.read_payload()
                removed = remove_push_subscription(payload.get("endpoint", ""))
                self.send_json(200, {"ok": True, "removed": removed})
            except Exception as exc:
                self.send_json(400, {"ok": False, "error": str(exc)})
            return
        if path == "/api/xray/reload":
            try:
                result = reload_vps_xray(self.app.db_path)
                self.send_json(200, {"ok": True, **result})
            except Exception as exc:
                self.send_text(500, str(exc))
            return
        if path == "/api/xray/restart":
            try:
                result = restart_vps_xray()
                self.send_json(200, {"ok": True, **result})
            except Exception as exc:
                self.send_text(500, str(exc))
            return
        if path == "/api/router":
            try:
                payload = self.read_payload()
                router_id = clean_router_id(payload.get("id"))
                entry_port = int(payload.get("entry_port") or 0)
                ssh_entry_port = int(payload.get("ssh_entry_port") or (entry_port + 1000))
                if entry_port <= 0:
                    self.send_text(400, "entry_port должен быть больше 0")
                    return
                with self.app.conn() as conn:
                    existing = get_router(conn, router_id)
                    if existing and not router_deleted(existing):
                        self.send_text(
                            409,
                            f"Router ID '{router_id}' уже есть. Для второго роутера укажи новый ID, например node-2 или main123.",
                        )
                        return
                    port_owner = get_router_by_any_port(conn, entry_port, router_id)
                    if port_owner:
                        self.send_text(
                            409,
                            f"entry_port {entry_port} уже занят роутером '{port_owner['id']}'. Для следующего роутера поставь другой порт, например {entry_port + 10}.",
                        )
                        return
                    ssh_port_owner = get_router_by_any_port(conn, ssh_entry_port, router_id)
                    if ssh_port_owner:
                        self.send_text(
                            409,
                            f"ssh_entry_port {ssh_entry_port} уже занят роутером '{ssh_port_owner['id']}'. Поставь entry_port так, чтобы entry_port + 1000 был свободен.",
                        )
                        return
                    payload["ssh_entry_port"] = ssh_entry_port
                    row = upsert_router(conn, payload)
                    router = row_to_router(row)
                self.send_json(200, {"ok": True, "router": router})
            except Exception as exc:
                self.send_text(400, str(exc))
            return
        if path.startswith("/api/router/") and path.endswith("/wol/devices"):
            parts = path.strip("/").split("/")
            if len(parts) != 5 or parts[0] != "api" or parts[1] != "router" or parts[3] != "wol" or parts[4] != "devices":
                self.send_text(404, "not found")
                return
            router_id = urllib.parse.unquote(parts[2])
            try:
                payload = self.read_payload()
                with self.app.conn() as conn:
                    row = get_active_router(conn, router_id)
                if not row:
                    self.send_json(404, {"ok": False, "error": "router not found"})
                    return
                devices = self.discover_router_wol_devices(row, payload.get("ssh_password", ""))
                self.send_json(200, {"ok": True, "router_id": router_id, "devices": devices})
            except Exception as exc:
                self.send_json(400, {"ok": False, "error": str(exc), "router_id": router_id, "devices": []})
            return
        if path.startswith("/api/router/") and path.endswith("/wol"):
            parts = path.strip("/").split("/")
            if len(parts) != 4 or parts[0] != "api" or parts[1] != "router" or parts[3] != "wol":
                self.send_text(404, "not found")
                return
            router_id = urllib.parse.unquote(parts[2])
            try:
                payload = self.read_payload()
                with self.app.conn() as conn:
                    row = get_active_router(conn, router_id)
                if not row:
                    self.send_json(404, {"ok": False, "error": "router not found"})
                    return
                result = self.send_router_wol_packet(
                    row,
                    payload.get("mac"),
                    payload.get("iface", ""),
                    payload.get("ssh_password", ""),
                )
                self.send_json(200, {"ok": True, "router_id": router_id, **result})
            except Exception as exc:
                self.send_json(400, {"ok": False, "error": str(exc), "router_id": router_id})
            return
        if path.startswith("/api/router/") and path.endswith("/rename"):
            try:
                router_id = urllib.parse.unquote(path.split("/")[3])
                payload = self.read_payload()
                with self.app.conn() as conn:
                    row = rename_router(conn, router_id, payload.get("name"))
                self.send_json(200, {"ok": True, "router": row_to_router(row)})
            except Exception as exc:
                self.send_text(400, str(exc))
            return
        if path.startswith("/api/router/") and path.endswith("/delete"):
            router_id = urllib.parse.unquote(path.split("/")[3])
            deleted = False
            xray_result = None
            warnings = []
            with self.app.conn() as conn:
                row = get_router(conn, router_id)
                if row and not router_deleted(row):
                    ts = now_ts()
                    conn.execute(
                        "update routers set deleted_at = ?, updated_at = ? where id = ?",
                        (ts, ts, router_id),
                    )
                    conn.commit()
                    deleted = True
            if deleted:
                try:
                    xray_result = reload_vps_xray(self.app.db_path)
                except Exception as exc:
                    warnings.append(f"xray reload skipped: {exc}")
            self.send_json(200, {"ok": True, "deleted": deleted, "xray": xray_result, "warnings": warnings})
            return
        self.send_text(404, "not found")

    def router_asset(self, path):
        parts = path.strip("/").split("/")
        if len(parts) != 3:
            self.send_text(404, "not found")
            return
        _, router_id, asset = parts
        router_id = urllib.parse.unquote(router_id)
        with self.app.conn() as conn:
            row = get_active_router(conn, router_id)
        if not row:
            self.send_text(404, "router not found")
            return
        hub_url = self.app.public_url or f"http://{self.headers.get('Host')}"
        if asset == "config":
            self.send_text(200, make_openwrt_config(row, hub_url))
            return
        if asset == "xray-client.json":
            self.send_json(200, make_router_xray_config(row))
            return
        self.send_text(404, "not found")

    def proxy_access(self, path):
        if not self.require_admin():
            return
        parts = path.split("/", 3)
        if len(parts) < 3 or not parts[2]:
            self.redirect("/")
            return
        router_id = urllib.parse.unquote(parts[2])
        rest = "/" + parts[3] if len(parts) == 4 else "/"
        with self.app.conn() as conn:
            row = get_active_router(conn, router_id)
        if not row:
            self.send_text(404, "router not found")
            return
        port = int(row["entry_port"] or 0)
        if port <= 0:
            self.send_text(400, "router has no entry_port")
            return

        query = urllib.parse.parse_qsl(self.parsed().query, keep_blank_values=True)
        query = list(query)
        target = rest
        if query:
            target += "?" + urllib.parse.urlencode(query)

        is_static = self.command in ("GET", "HEAD") and is_luci_static_target(target)
        cache_key = static_cache_key(router_id, target) if is_static else None
        if cache_key:
            cached = static_cache_get(cache_key)
            if cached:
                status, resp_body, content_type, resp_headers = cached
                resp_headers.append(("X-OWRT-Static-Cache", "hit"))
                self.send_raw_bytes(status, resp_body, content_type, resp_headers)
                return

        body = self.read_body() if self.command in ("POST", "PUT", "PATCH") else None
        headers = {}
        skip = {"host", "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade", "content-length", "accept-encoding"}
        for key, value in self.headers.items():
            if key.lower() in skip:
                continue
            if key.lower() == "cookie":
                value = clean_forward_cookie(value)
                if not value:
                    continue
            elif key.lower() == "referer":
                value = rewrite_forward_url(value, router_id, port)
            elif key.lower() == "origin":
                value = f"http://127.0.0.1:{port}"
            headers[key] = value
        headers["Host"] = f"127.0.0.1:{port}"
        headers["Connection"] = "close"
        headers["X-Forwarded-Host"] = self.headers.get("Host", "")
        headers["X-Forwarded-Prefix"] = f"/access/{urllib.parse.quote(router_id)}"
        headers["X-Forwarded-Proto"] = "http"
        if body is not None:
            headers["Content-Length"] = str(len(body))

        limiter = None if is_static else router_proxy_limiter(router_id)
        acquired = False
        try:
            if limiter is not None:
                acquired = limiter.acquire(timeout=PROXY_TIMEOUT)
                if not acquired:
                    self.send_text(503, "proxy busy: router is handling too many requests")
                    return
            attempts = 4 if is_static and self.command == "GET" else 1
            last_exc = None
            for attempt in range(attempts):
                backend = None
                try:
                    backend = http.client.HTTPConnection("127.0.0.1", port, timeout=PROXY_TIMEOUT)
                    backend.request(self.command, target, body=body, headers=headers)
                    resp = backend.getresponse()
                    resp_status = resp.status
                    resp_raw_headers = resp.getheaders()
                    resp_body = resp.read()
                    content_type = resp.getheader("Content-Type", "")
                    last_exc = None
                    break
                except Exception as exc:
                    last_exc = exc
                    if attempt + 1 < attempts:
                        time.sleep(0.12 * (attempt + 1))
                        continue
                finally:
                    if backend is not None:
                        backend.close()
            if last_exc is not None:
                raise last_exc
            resp_headers = []
            prefix = f"/access/{urllib.parse.quote(router_id)}"
            public_hosts = normalize_public_hosts(
                self.headers.get("Host", ""),
                self.app.public_url,
                "127.0.0.1",
                f"127.0.0.1:{port}",
                "localhost",
                f"localhost:{port}",
                row["admin_host"],
                f"{row['admin_host']}:{row['admin_port']}",
            )
            for key, value in resp_raw_headers:
                low = key.lower()
                if low in {"cache-control", "connection", "content-length", "content-type", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade"}:
                    continue
                if low == "location":
                    value = rewrite_location(value, prefix, port)
                if low == "set-cookie":
                    value = rewrite_cookie_path(value, "/")
                resp_headers.append((key, value))
            if should_rewrite_body(content_type):
                resp_body = rewrite_html(resp_body, prefix, content_type, public_hosts)
            if cache_key and self.command == "GET":
                static_cache_put(
                    cache_key,
                    resp_status,
                    resp_body,
                    content_type or "application/octet-stream",
                    static_cache_headers(resp_headers),
                )
            if not is_static:
                resp_headers.append(("Set-Cookie", current_router_cookie(router_id)))
            if is_static:
                self.send_raw_bytes(
                    resp_status,
                    resp_body,
                    content_type or "application/octet-stream",
                    resp_headers + [("X-OWRT-Static-Cache", "miss")],
                )
            else:
                self.send_bytes(
                    resp_status,
                    resp_body,
                    content_type or "application/octet-stream",
                    resp_headers,
                )
        except Exception as exc:
            self.send_text(502, f"proxy error: {exc}")
        finally:
            if acquired:
                limiter.release()


def rewrite_location(value, prefix, port):
    if value.startswith("/"):
        return prefix + value
    for base in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
        if value.startswith(base + "/"):
            return prefix + value[len(base):]
    return value


def rewrite_cookie_path(value, path):
    chunks = value.split(";")
    changed = []
    saw_path = False
    for chunk in chunks:
        if chunk.strip().lower().startswith("path="):
            changed.append(" Path=" + path)
            saw_path = True
        else:
            changed.append(chunk)
    if not saw_path:
        changed.append(" Path=" + path)
    return ";".join(changed)


def proxy_runtime_script(prefix):
    prefix_json = json.dumps(prefix)
    return """<script>
(function() {
  const prefix = %s;
  const roots = ["/ubus", "/cgi-bin/luci", "/luci-static"];
  window.__owrtRemotePrefix = prefix;
  function fixUrl(url) {
    const original = url;
    let raw = "";
    if (typeof url === "string") raw = url;
    else if (url && typeof url.href === "string") raw = url.href;
    else if (url && typeof url.url === "string") raw = url.url;
    if (!raw) return original;
    if (raw.startsWith(prefix + "/")) return raw;
    try {
      const absolute = /^[a-z][a-z0-9+.-]*:/i.test(raw) || raw.startsWith("//");
      const parsed = absolute ? new URL(raw, location.href) : null;
      const value = parsed ? (parsed.pathname + parsed.search + parsed.hash) : raw;
      for (const root of roots) {
        if (value === root || value.startsWith(root + "/") || value.startsWith(root + "?")) {
          return prefix + value;
        }
      }
      if (parsed && parsed.hostname !== location.hostname) return raw;
    } catch (e) {}
    return original;
  }
  function fixElementUrl(el, attr) {
    if (!el) return;
    const value = el.getAttribute(attr);
    const fixed = fixUrl(value);
    if (fixed !== value) el.setAttribute(attr, fixed);
  }
  function fixTree(node) {
    if (!node || node.nodeType !== 1) return node;
    if (node.hasAttribute) {
      if (node.hasAttribute("href")) fixElementUrl(node, "href");
      if (node.hasAttribute("action")) fixElementUrl(node, "action");
      if (node.hasAttribute("src")) fixElementUrl(node, "src");
    }
    if (node.querySelectorAll) {
      node.querySelectorAll("a[href], form[action], link[href], script[src], img[src]").forEach(function(el) {
        if (el.hasAttribute("href")) fixElementUrl(el, "href");
        if (el.hasAttribute("action")) fixElementUrl(el, "action");
        if (el.hasAttribute("src")) fixElementUrl(el, "src");
      });
    }
    return node;
  }
  function patchUrlProperty(proto, prop) {
    if (!proto) return;
    const desc = Object.getOwnPropertyDescriptor(proto, prop);
    if (!desc || !desc.set || !desc.get) return;
    Object.defineProperty(proto, prop, {
      configurable: true,
      enumerable: desc.enumerable,
      get: function() { return desc.get.call(this); },
      set: function(value) { return desc.set.call(this, fixUrl(value)); }
    });
  }
  if (window.Element && Element.prototype.setAttribute) {
    const nativeSetAttribute = Element.prototype.setAttribute;
    Element.prototype.setAttribute = function(name, value) {
      const attr = String(name || "").toLowerCase();
      if (attr === "href" || attr === "src" || attr === "action") value = fixUrl(value);
      return nativeSetAttribute.call(this, name, value);
    };
  }
  if (window.Node) {
    const nativeAppendChild = Node.prototype.appendChild;
    const nativeInsertBefore = Node.prototype.insertBefore;
    if (nativeAppendChild) {
      Node.prototype.appendChild = function(node) {
        return nativeAppendChild.call(this, fixTree(node));
      };
    }
    if (nativeInsertBefore) {
      Node.prototype.insertBefore = function(node, before) {
        return nativeInsertBefore.call(this, fixTree(node), before);
      };
    }
  }
  patchUrlProperty(window.HTMLAnchorElement && HTMLAnchorElement.prototype, "href");
  patchUrlProperty(window.HTMLLinkElement && HTMLLinkElement.prototype, "href");
  patchUrlProperty(window.HTMLScriptElement && HTMLScriptElement.prototype, "src");
  patchUrlProperty(window.HTMLImageElement && HTMLImageElement.prototype, "src");
  patchUrlProperty(window.HTMLFormElement && HTMLFormElement.prototype, "action");
  if (window.fetch) {
    const nativeFetch = window.fetch;
    window.fetch = function(input, init) {
      if (typeof input === "string") {
        input = fixUrl(input);
      } else if (input && typeof input.href === "string") {
        input = fixUrl(input.href);
      } else if (input && input.url) {
        const fixed = fixUrl(input.url);
        if (fixed !== input.url) input = new Request(fixed, input);
      }
      return nativeFetch.call(this, input, init);
    };
  }
  if (window.XMLHttpRequest) {
    const nativeOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function(method, url) {
      arguments[1] = fixUrl(url);
      return nativeOpen.apply(this, arguments);
    };
  }
  if (navigator.sendBeacon) {
    const nativeBeacon = navigator.sendBeacon.bind(navigator);
    navigator.sendBeacon = function(url, data) {
      return nativeBeacon(fixUrl(url), data);
    };
  }
  document.addEventListener("click", function(ev) {
    const a = ev.target && ev.target.closest ? ev.target.closest("a[href]") : null;
    fixElementUrl(a, "href");
  }, true);
  document.addEventListener("submit", function(ev) {
    fixElementUrl(ev.target, "action");
  }, true);
  if (document.addEventListener) {
    document.addEventListener("DOMContentLoaded", function() {
      document.querySelectorAll("a[href], form[action], link[href], script[src], img[src]").forEach(function(el) {
        if (el.hasAttribute("href")) fixElementUrl(el, "href");
        if (el.hasAttribute("action")) fixElementUrl(el, "action");
        if (el.hasAttribute("src")) fixElementUrl(el, "src");
      });
    });
  }
})();
</script>""" % prefix_json


def protect_luci_resource_root(text):
    protected = []

    def protect_value(match):
        token = f"__OWRT_REMOTE_LUCI_RESOURCE_{len(protected)}__"
        protected.append(match.group(0))
        return token

    patterns = (
        r"""((?:"resource"|'resource'|resource)\s*[:=]\s*)(["'])(/luci-static/resources)(\2)""",
        r"""((?:"resource"|'resource'|resource)\s*[:=]\s*)(["'])(\\/luci-static\\/resources)(\2)""",
    )
    for pattern in patterns:
        text = re.sub(pattern, protect_value, text)
    return text, protected


def restore_luci_resource_root(text, protected):
    for index, value in enumerate(protected):
        text = text.replace(f"__OWRT_REMOTE_LUCI_RESOURCE_{index}__", value)
    return text


def normalize_public_hosts(*values):
    hosts = set()
    for value in values:
        if not value:
            continue
        raw = str(value).strip()
        if not raw:
            continue
        parsed = urllib.parse.urlsplit(raw if "://" in raw else f"//{raw}")
        netloc = parsed.netloc or parsed.path.split("/", 1)[0]
        netloc = netloc.split("@", 1)[-1].strip().lower()
        if not netloc:
            continue
        hosts.add(netloc)
        if ":" not in netloc:
            hosts.add(f"{netloc}:80")
            hosts.add(f"{netloc}:443")
            hosts.add(f"{netloc}:8088")
    return sorted(hosts, key=len, reverse=True)


def rewrite_public_absolute_urls(text, prefix, public_hosts):
    escaped_prefix = prefix.replace("/", "\\/")
    for host in public_hosts or []:
        for scheme in ("http", "https"):
            for root in ("/ubus", "/cgi-bin/luci", "/luci-static"):
                text = text.replace(f"{scheme}://{host}{root}", f"{prefix}{root}")
                escaped_root = root.replace("/", "\\/")
                text = text.replace(f"{scheme}:\\/\\/{host}{escaped_root}", f"{escaped_prefix}{escaped_root}")
    return text


def rewrite_remaining_luci_roots(text, prefix):
    roots = ("/ubus", "/cgi-bin/luci", "/luci-static")
    escaped_prefix = prefix.replace("/", "\\/")
    for root in roots:
        key = root.strip("/").replace("/", "_")
        marker = f"__OWRT_REMOTE_RAW_ROOT_{key}__"
        escaped_marker = f"__OWRT_REMOTE_ESC_ROOT_{key}__"

        escaped_root = root.replace("/", "\\/")
        escaped_prefixed = f"{escaped_prefix}{escaped_root}"
        text = text.replace(escaped_prefixed, escaped_marker)
        text = text.replace(escaped_root, escaped_prefixed)
        text = text.replace(escaped_prefixed, escaped_marker)

        prefixed = f"{prefix}{root}"
        text = text.replace(prefixed, marker)
        text = text.replace(root, prefixed)
        text = text.replace(marker, prefixed)

        text = text.replace(escaped_marker, escaped_prefixed)
    return text


def rewrite_html(body, prefix, content_type="", public_hosts=None):
    text = body.decode("utf-8", errors="ignore")
    escaped_prefix = prefix.replace("/", "\\/")
    text, protected_resource_roots = protect_luci_resource_root(text)
    replacements = {
        'href="/': f'href="{prefix}/',
        'src="/': f'src="{prefix}/',
        'action="/': f'action="{prefix}/',
        'data-url="/': f'data-url="{prefix}/',
        "href=/": f"href={prefix}/",
        "src=/": f"src={prefix}/",
        "action=/": f"action={prefix}/",
        "data-url=/": f"data-url={prefix}/",
        "href='/": f"href='{prefix}/",
        "src='/": f"src='{prefix}/",
        "action='/": f"action='{prefix}/",
        "data-url='/": f"data-url='{prefix}/",
        'url("/': f'url("{prefix}/',
        "url('/": f"url('{prefix}/",
        "url(/": f"url({prefix}/",
        '"/ubus"': f'"{prefix}/ubus"',
        "'/ubus'": f"'{prefix}/ubus'",
        "`/ubus`": f"`{prefix}/ubus`",
        '"/cgi-bin/luci"': f'"{prefix}/cgi-bin/luci"',
        "'/cgi-bin/luci'": f"'{prefix}/cgi-bin/luci'",
        "`/cgi-bin/luci`": f"`{prefix}/cgi-bin/luci`",
        '"/cgi-bin/luci': f'"{prefix}/cgi-bin/luci',
        '"/ubus/': f'"{prefix}/ubus/',
        '"/luci-static/': f'"{prefix}/luci-static/',
        "'/cgi-bin/luci": f"'{prefix}/cgi-bin/luci",
        "'/ubus/": f"'{prefix}/ubus/",
        "'/luci-static/": f"'{prefix}/luci-static/",
        "`/cgi-bin/luci": f"`{prefix}/cgi-bin/luci",
        "`/ubus/": f"`{prefix}/ubus/",
        "`/luci-static/": f"`{prefix}/luci-static/",
        '"\\/ubus"': f'"{escaped_prefix}\\/ubus"',
        "'\\/ubus'": f"'{escaped_prefix}\\/ubus'",
        "`\\/ubus`": f"`{escaped_prefix}\\/ubus`",
        '"\\/cgi-bin\\/luci"': f'"{escaped_prefix}\\/cgi-bin\\/luci"',
        "'\\/cgi-bin\\/luci'": f"'{escaped_prefix}\\/cgi-bin\\/luci'",
        "`\\/cgi-bin\\/luci`": f"`{escaped_prefix}\\/cgi-bin\\/luci`",
        '"\\/cgi-bin\\/luci': f'"{escaped_prefix}\\/cgi-bin\\/luci',
        '"\\/ubus\\/': f'"{escaped_prefix}\\/ubus\\/',
        '"\\/luci-static\\/': f'"{escaped_prefix}\\/luci-static\\/',
        "'\\/cgi-bin\\/luci": f"'{escaped_prefix}\\/cgi-bin\\/luci",
        "'\\/ubus\\/": f"'{escaped_prefix}\\/ubus\\/",
        "'\\/luci-static\\/": f"'{escaped_prefix}\\/luci-static\\/",
        "`\\/cgi-bin\\/luci": f"`{escaped_prefix}\\/cgi-bin\\/luci",
        "`\\/ubus\\/": f"`{escaped_prefix}\\/ubus\\/",
        "`\\/luci-static\\/": f"`{escaped_prefix}\\/luci-static\\/",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = rewrite_public_absolute_urls(text, prefix, public_hosts or [])
    text = rewrite_remaining_luci_roots(text, prefix)
    text = restore_luci_resource_root(text, protected_resource_roots)
    if "text/html" in (content_type or "").lower():
        script = proxy_runtime_script(prefix)
        head_match = re.search(r"<head[^>]*>", text, flags=re.IGNORECASE)
        if head_match:
            text = text[: head_match.end()] + "\n" + script + text[head_match.end() :]
        elif "</head>" in text:
            text = text.replace("</head>", script + "\n</head>", 1)
        elif "</body>" in text:
            text = text.replace("</body>", script + "\n</body>", 1)
        else:
            text += script
    return text.encode("utf-8")


def should_rewrite_body(content_type):
    content_type = (content_type or "").lower()
    return (
        "text/html" in content_type
        or "text/css" in content_type
    )


def cmd_init(args):
    with connect(args.db) as conn:
        init_db(conn)
    auth = load_auth()
    print(f"DB: {args.db}")
    print(f"HUB_LOGIN: {auth.get('username', 'admin')}")
    print(f"AGENT_TOKEN: {agent_token()}")
    hint = STATE_DIR / "hub-login.txt"
    if hint.exists():
        print(f"HUB_PASSWORD_FILE: {hint}")


def cmd_set_login(args):
    save_auth(args.username, args.password)
    print(f"HUB_LOGIN: {args.username}")
    print("HUB_PASSWORD: updated")


def cmd_add_router(args):
    with connect(args.db) as conn:
        init_db(conn)
        row = upsert_router(
            conn,
            {
                "id": args.id,
                "name": args.name,
                "role": args.role,
                "entry_port": args.entry_port,
                "vps_host": args.vps_host,
                "vless_port": args.vless_port,
                "vless_uuid": args.vless_uuid,
                "vless_encryption": args.vless_encryption,
                "vless_decryption": args.vless_decryption,
                "vless_flow": args.vless_flow,
                "reverse_tag": args.reverse_tag,
                "public_url": args.public_url,
                "admin_host": args.admin_host,
                "admin_port": args.admin_port,
                "ssh_entry_port": args.ssh_entry_port or args.entry_port + 1000,
                "ssh_vless_uuid": args.ssh_vless_uuid,
                "ssh_reverse_tag": args.ssh_reverse_tag,
                "ssh_host": args.ssh_host,
                "ssh_port": args.ssh_port,
            },
        )
    router = row_to_router(row)
    print(json.dumps(router, ensure_ascii=False, indent=2))


def cmd_set_entry_port(args):
    router_id = clean_router_id(args.id)
    with connect(args.db) as conn:
        init_db(conn)
        row = get_router(conn, router_id)
        if not row:
            raise SystemExit(f"router not found: {router_id}")
        old_entry = int(row["entry_port"] or 0)
        old_ssh_entry = int(row["ssh_entry_port"] or 0)
        new_entry = int(args.entry_port)
        new_ssh_entry = new_entry + 1000 if old_ssh_entry in (0, old_entry + 1000) else old_ssh_entry
        owner = get_router_by_any_port(conn, new_entry, router_id)
        if owner:
            raise SystemExit(f"entry_port {new_entry} already used by {owner['id']}")
        owner = get_router_by_any_port(conn, new_ssh_entry, router_id)
        if owner:
            raise SystemExit(f"ssh_entry_port {new_ssh_entry} already used by {owner['id']}")
        conn.execute(
            "update routers set entry_port = ?, ssh_entry_port = ?, updated_at = ? where id = ?",
            (new_entry, new_ssh_entry, now_ts(), router_id),
        )
        conn.commit()
        row = get_router(conn, router_id)
    router = row_to_router(row)
    print(json.dumps(router, ensure_ascii=False, indent=2))


def cmd_list(args):
    with connect(args.db) as conn:
        init_db(conn)
        rows = [row_to_router(r) for r in list_router_rows(conn)]
    print(json.dumps(rows, ensure_ascii=False, indent=2))


def cmd_render_xray(args):
    with connect(args.db) as conn:
        init_db(conn)
        rows = list_router_rows(conn)
    config = make_server_xray_config(rows, args.listen_host, args.listen_port, args.decryption)
    text = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    if args.out == "-":
        print(text, end="")
    else:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        try:
            os.chmod(out, 0o600)
        except OSError:
            pass
        print(f"written: {out}")


def cmd_print_openwrt(args):
    with connect(args.db) as conn:
        init_db(conn)
        row = get_router(conn, args.id)
    if not row:
        raise SystemExit(f"router not found: {args.id}")
    hub_url = args.hub_url or os.environ.get("OWRT_REMOTE_PUBLIC_URL") or f"http://{args.vps_host}:{args.port}"
    print(make_openwrt_config(row, hub_url), end="")


def cmd_backup(args):
    out = Path(args.out) if args.out else STATE_DIR / backup_filename()
    result = create_hub_backup(out, args.db)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_restore(args):
    result = restore_hub_backup(args.file, args.db, args.vps_host, args.public_url)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("Restart Hub after restore: systemctl restart owrt-remote")


def parse_extra_ports(value):
    ports = []
    for item in str(value or "").replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            port = int(item)
        except ValueError:
            continue
        if 0 < port < 65536 and port not in ports:
            ports.append(port)
    return ports


class HubHTTPServer(ThreadingHTTPServer):
    request_queue_size = REQUEST_QUEUE_SIZE
    daemon_threads = True
    allow_reuse_address = True

    def get_request(self):
        request, client_address = super().get_request()
        try:
            request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        return request, client_address


def make_http_server(app, host, port, tls_cert="", tls_key=""):
    server = HubHTTPServer((host, port), Handler)
    server.app = app
    server.is_tls = False
    if tls_cert and tls_key:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.set_alpn_protocols(["http/1.1"])
        context.load_cert_chain(tls_cert, tls_key)
        server.socket = context.wrap_socket(
            server.socket,
            server_side=True,
            do_handshake_on_connect=False,
        )
        server.is_tls = True
    return server


def cmd_serve(args):
    app = App(args.db, session_token(), agent_token(), args.public_url)
    with app.conn():
        pass
    record_hub_start_event()
    auth = load_auth()
    server = make_http_server(app, args.host, args.port)
    extra_servers = []
    tls_ports = parse_extra_ports(args.tls_ports) if args.tls_cert and args.tls_key else []
    tls_port_set = set(tls_ports)
    for port in parse_extra_ports(args.extra_ports):
        if port == args.port:
            continue
        if port in tls_port_set:
            print(f"WARNING: port {port} skipped for plain HTTP because TLS is enabled on it", file=sys.stderr)
            continue
        try:
            extra_server = make_http_server(app, args.host, port)
        except OSError as exc:
            print(f"WARNING: extra port {port} not started: {exc}", file=sys.stderr)
            continue
        extra_servers.append(extra_server)
        thread = threading.Thread(target=extra_server.serve_forever, daemon=True)
        thread.start()
        print(f"{APP_NAME} also listening on http://{args.host}:{port}")
    if args.tls_cert and args.tls_key:
        for port in tls_ports:
            try:
                tls_server = make_http_server(app, args.host, port, args.tls_cert, args.tls_key)
            except OSError as exc:
                print(f"WARNING: HTTPS port {port} not started: {exc}", file=sys.stderr)
                continue
            except ssl.SSLError as exc:
                print(f"WARNING: HTTPS cert/key error: {exc}", file=sys.stderr)
                continue
            extra_servers.append(tls_server)
            thread = threading.Thread(target=tls_server.serve_forever, daemon=True)
            thread.start()
            print(f"{APP_NAME} also listening on https://{args.host}:{port}")
    print(f"{APP_NAME} listening on http://{args.host}:{args.port}")
    print(f"HUB_LOGIN: {auth.get('username', 'admin')}")
    print(f"AGENT_TOKEN: {app.agent_token}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("")
    finally:
        for extra_server in extra_servers:
            extra_server.shutdown()


def parser():
    p = argparse.ArgumentParser(description=APP_NAME)
    p.add_argument("--db", default=str(DB_PATH), help="SQLite DB path")
    sub = p.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init", help="initialize state and tokens")
    init.set_defaults(func=cmd_init)

    auth = sub.add_parser("set-login", help="set dashboard username and password")
    auth.add_argument("--username", required=True)
    auth.add_argument("--password", required=True)
    auth.set_defaults(func=cmd_set_login)

    add = sub.add_parser("add-router", help="add or update router")
    add.add_argument("--id", required=True)
    add.add_argument("--name", required=True)
    add.add_argument("--role", default="node")
    add.add_argument("--entry-port", type=int, required=True)
    add.add_argument("--vps-host", required=True)
    add.add_argument("--vless-port", type=int, default=DEFAULT_VLESS_PORT)
    add.add_argument("--vless-uuid", default="")
    add.add_argument("--vless-encryption", default="none")
    add.add_argument("--vless-decryption", default="none")
    add.add_argument("--vless-flow", default="")
    add.add_argument("--reverse-tag", default="reverse-in")
    add.add_argument("--public-url", default="")
    add.add_argument("--admin-host", default="127.0.0.1")
    add.add_argument("--admin-port", type=int, default=80)
    add.add_argument("--ssh-entry-port", type=int, default=0)
    add.add_argument("--ssh-vless-uuid", default="")
    add.add_argument("--ssh-reverse-tag", default="")
    add.add_argument("--ssh-host", default="127.0.0.1")
    add.add_argument("--ssh-port", type=int, default=22)
    add.set_defaults(func=cmd_add_router)

    sep = sub.add_parser("set-entry-port", help="set router VPS entry port without changing UUID")
    sep.add_argument("--id", required=True)
    sep.add_argument("--entry-port", type=int, required=True)
    sep.set_defaults(func=cmd_set_entry_port)

    ls = sub.add_parser("list-routers", help="print routers")
    ls.set_defaults(func=cmd_list)

    rx = sub.add_parser("render-xray", help="render VPS Xray config")
    rx.add_argument("--listen-host", default="0.0.0.0")
    rx.add_argument("--listen-port", type=int, default=DEFAULT_VLESS_PORT)
    rx.add_argument("--decryption", default="none")
    rx.add_argument("--out", default="-")
    rx.set_defaults(func=cmd_render_xray)

    ow = sub.add_parser("print-openwrt-config", help="print UCI commands for router")
    ow.add_argument("--id", required=True)
    ow.add_argument("--hub-url", default="")
    ow.add_argument("--vps-host", default="127.0.0.1")
    ow.add_argument("--port", type=int, default=8088)
    ow.set_defaults(func=cmd_print_openwrt)

    backup = sub.add_parser("backup", help="create Hub backup archive")
    backup.add_argument("--out", default="", help="output .tar.gz path")
    backup.set_defaults(func=cmd_backup)

    restore = sub.add_parser("restore", help="restore Hub backup archive")
    restore.add_argument("--file", required=True, help="backup .tar.gz path")
    restore.add_argument("--vps-host", default="", help="rewrite routers to new VPS host/IP")
    restore.add_argument("--public-url", default="", help="rewrite routers to new public Hub URL")
    restore.set_defaults(func=cmd_restore)

    serve = sub.add_parser("serve", help="run web dashboard")
    serve.add_argument("--host", default=os.environ.get("OWRT_REMOTE_BIND", "0.0.0.0"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("OWRT_REMOTE_PORT", "8088")))
    serve.add_argument("--extra-ports", default=os.environ.get("OWRT_REMOTE_EXTRA_PORTS", ""))
    serve.add_argument("--tls-ports", default=os.environ.get("OWRT_REMOTE_TLS_PORTS", ""))
    serve.add_argument("--tls-cert", default=os.environ.get("OWRT_REMOTE_TLS_CERT", ""))
    serve.add_argument("--tls-key", default=os.environ.get("OWRT_REMOTE_TLS_KEY", ""))
    serve.add_argument("--public-url", default=os.environ.get("OWRT_REMOTE_PUBLIC_URL", ""))
    serve.set_defaults(func=cmd_serve)

    return p


def main():
    args = parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
