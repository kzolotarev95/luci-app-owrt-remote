#!/usr/bin/env python3
import argparse
import base64
import datetime as dt
import dataclasses
import enum
from email.parser import BytesParser
from email.policy import default as email_policy_default
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
import shutil
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from pywebpush import WebPushException, webpush
except Exception:
    WebPushException = None
    webpush = None

try:
    from fido2.server import Fido2Server
    from fido2.webauthn import (
        AuthenticationResponse,
        AttestedCredentialData,
        PublicKeyCredentialDescriptor,
        PublicKeyCredentialRpEntity,
        PublicKeyCredentialType,
        PublicKeyCredentialUserEntity,
        ResidentKeyRequirement,
        UserVerificationRequirement,
    )
except Exception:
    Fido2Server = None
    AuthenticationResponse = None
    AttestedCredentialData = None
    PublicKeyCredentialDescriptor = None
    PublicKeyCredentialRpEntity = None
    PublicKeyCredentialType = None
    PublicKeyCredentialUserEntity = None
    ResidentKeyRequirement = None
    UserVerificationRequirement = None


APP_NAME = "OpenWrt Remote Hub"
RAW_REPO_BASE = "https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main"
APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
BRAND_THEME_COLOR = "#24192f"
FAVICON_ICO_FILE = STATIC_DIR / "favicon.ico"
FAVICON_PNG_32_FILE = STATIC_DIR / "favicon-32.png"
FAVICON_PNG_192_FILE = STATIC_DIR / "favicon-192.png"
FAVICON_PNG_512_FILE = STATIC_DIR / "favicon-512.png"
APPLE_TOUCH_ICON_FILE = STATIC_DIR / "apple-touch-icon.png"
STATE_DIR = Path(os.environ.get("OWRT_REMOTE_STATE_DIR", "/var/lib/owrt-remote"))
DB_PATH = Path(os.environ.get("OWRT_REMOTE_DB", str(STATE_DIR / "hub.db")))
AUTH_FILE = STATE_DIR / "hub-auth.json"
SESSION_TOKEN_FILE = STATE_DIR / "hub-session.token"
SESSIONS_FILE = STATE_DIR / "hub-sessions.json"
NOTIFICATIONS_FILE = STATE_DIR / "hub-notifications.json"
PUSH_SUBSCRIPTIONS_FILE = STATE_DIR / "hub-push-subscriptions.json"
PUSH_DELIVERY_FILE = STATE_DIR / "hub-push-delivery.json"
VAPID_PRIVATE_KEY_FILE = STATE_DIR / "hub-vapid-private.pem"
VAPID_PUBLIC_KEY_FILE = STATE_DIR / "hub-vapid-public.txt"
BOOT_ID_FILE = STATE_DIR / "hub-boot.id"
AGENT_TOKEN_FILE = STATE_DIR / "agent.token"
XRAY_WAN_RECONNECT_FILE = STATE_DIR / "xray-wan-reconnect-restart.json"
TRAFFIC_COUNTERS_FILE = STATE_DIR / "hub-traffic-counters.json"
ACME_WEBROOT = STATE_DIR / "acme-webroot"
ONLINE_AFTER_SECONDS = int(os.environ.get("OWRT_REMOTE_ONLINE_AFTER", "75"))
DEFAULT_VLESS_PORT = int(os.environ.get("OWRT_REMOTE_VLESS_PORT", "8443"))
REQUEST_QUEUE_SIZE = int(os.environ.get("OWRT_REMOTE_REQUEST_QUEUE_SIZE", "128"))
ROUTER_PROXY_LIMIT = max(1, int(os.environ.get("OWRT_REMOTE_ROUTER_PROXY_LIMIT", "4")))
PROXY_TIMEOUT = float(os.environ.get("OWRT_REMOTE_PROXY_TIMEOUT", "25"))
STATIC_CACHE_TTL = int(os.environ.get("OWRT_REMOTE_STATIC_CACHE_TTL", "3600"))
STATIC_CACHE_MAX_BYTES = int(os.environ.get("OWRT_REMOTE_STATIC_CACHE_MAX_BYTES", str(8 * 1024 * 1024)))
try:
    SERVICE_WORKER_VERSION = str(int(Path(__file__).stat().st_mtime))
except Exception:
    SERVICE_WORKER_VERSION = "1"
PBKDF2_ITERATIONS = 240000
MIN_PASSWORD_LENGTH = 4
SESSION_COOKIE = "owrt_remote_session"
ROUTER_COOKIE = "owrt_remote_router"
SESSION_TTL_SECONDS = int(os.environ.get("OWRT_REMOTE_SESSION_TTL", str(30 * 24 * 60 * 60)))
CAPTCHA_TTL_SECONDS = 600
CAPTCHA_MODE_DIGITS = "digits"
CAPTCHA_MODE_RECAPTCHA = "recaptcha"
ROUTER_ACCESS_FLAGS = ("A", "B", "G")
ROUTER_ACCESS_LABELS = {
    "A": "Web only",
    "B": "Web + SSH",
    "G": "Full router access",
}
RECAPTCHA_VERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"
AUTH_CHALLENGE_TTL_SECONDS = 300
TOTP_PERIOD_SECONDS = 30
TOTP_DIGITS = 6
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
NOTIFICATIONS_COND = threading.Condition()
PUSH_LOCK = threading.Lock()
TRAFFIC_COUNTERS_LOCK = threading.Lock()
AUTH_STATE_LOCK = threading.Lock()
AUTH_FLOW_LOCK = threading.Lock()
STATIC_CACHE = {}
STATIC_CACHE_BYTES = 0
AUTH_FLOW_STATE = {}
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
    TRAFFIC_COUNTERS_FILE,
)
SOCIAL_PROVIDERS = {
    "github": {
        "label": "GitHub",
        "authorize_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
    },
    "vk": {
        "label": "VK ID",
        "authorize_url": "https://id.vk.ru/authorize",
        "token_url": "https://id.vk.ru/oauth2/auth",
        "api_url": "https://id.vk.ru/oauth2/user_info",
    },
}


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


def format_router_note_time(ts):
    if not ts:
        return "Дата публикации неизвестна"
    return dt.datetime.fromtimestamp(int(ts), tz=dt.timezone.utc).strftime("%d.%m.%Y %H:%M UTC")


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


def b64url_decode(raw):
    text = str(raw or "").strip()
    if not text:
        return b""
    text += "=" * ((4 - len(text) % 4) % 4)
    return base64.urlsafe_b64decode(text.encode("ascii"))


def normalize_totp_secret(value):
    text = re.sub(r"[^A-Z2-7]", "", str(value or "").upper())
    if not text:
        return ""
    padded = text + ("=" * ((8 - len(text) % 8) % 8))
    base64.b32decode(padded.encode("ascii"), casefold=True)
    return text


def generate_totp_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def totp_token(secret, counter, digits=TOTP_DIGITS):
    clean_secret = normalize_totp_secret(secret)
    padded = clean_secret + ("=" * ((8 - len(clean_secret) % 8) % 8))
    key = base64.b32decode(padded.encode("ascii"), casefold=True)
    msg = struct.pack(">Q", int(counter))
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10**digits)).zfill(digits)


def verify_totp(secret, code, ts=None, window=1, digits=TOTP_DIGITS, period=TOTP_PERIOD_SECONDS):
    clean_secret = normalize_totp_secret(secret)
    clean_code = re.sub(r"\s+", "", str(code or ""))
    if not clean_secret or not clean_code.isdigit():
        return False
    current_counter = int((ts or now_ts()) // max(1, int(period or TOTP_PERIOD_SECONDS)))
    for shift in range(-max(0, int(window)), max(0, int(window)) + 1):
        if secrets.compare_digest(totp_token(clean_secret, current_counter + shift, digits=digits), clean_code):
            return True
    return False


def totp_otpauth_uri(username, secret, issuer=APP_NAME):
    safe_user = urllib.parse.quote(str(username or "admin"))
    safe_issuer = urllib.parse.quote(str(issuer or APP_NAME))
    return f"otpauth://totp/{safe_issuer}:{safe_user}?secret={secret}&issuer={safe_issuer}&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_PERIOD_SECONDS}"


def default_totp_state():
    return {
        "enabled": False,
        "secret": "",
        "digits": TOTP_DIGITS,
        "period": TOTP_PERIOD_SECONDS,
        "updated_at": 0,
    }


def normalize_ssh_public_key(value):
    text = " ".join(str(value or "").strip().split())
    if not text:
        raise ValueError("SSH public key is empty")
    if not (text.startswith("ssh-ed25519 ") or text.startswith("sk-ssh-ed25519@openssh.com ")):
        raise ValueError("Only ED25519 SSH public keys are supported")
    parts = text.split(" ", 2)
    if len(parts) < 2:
        raise ValueError("SSH public key format is invalid")
    base64.b64decode(parts[1].encode("ascii"), validate=True)
    return text


def sanitize_passkey_record(item):
    item = dict(item or {})
    transports = item.get("transports", [])
    if not isinstance(transports, list):
        transports = []
    return {
        "id": str(item.get("id") or "").strip(),
        "label": str(item.get("label") or "Passkey").strip()[:80] or "Passkey",
        "credential_data": str(item.get("credential_data") or "").strip(),
        "created_at": int(item.get("created_at") or 0),
        "last_used_at": int(item.get("last_used_at") or 0),
        "sign_count": int(item.get("sign_count") or 0),
        "transports": [str(value).strip() for value in transports if str(value).strip()],
    }


def sanitize_ssh_key_record(item):
    item = dict(item or {})
    public_key = str(item.get("public_key") or "").strip()
    if not public_key:
        return None
    try:
        public_key = normalize_ssh_public_key(public_key)
    except ValueError:
        return None
    return {
        "id": str(item.get("id") or "").strip() or secrets.token_hex(8),
        "label": str(item.get("label") or "SSH ED25519").strip()[:80] or "SSH ED25519",
        "public_key": public_key,
        "created_at": int(item.get("created_at") or now_ts()),
        "last_used_at": int(item.get("last_used_at") or 0),
    }


def social_provider_label(provider):
    return SOCIAL_PROVIDERS.get(str(provider or "").strip().lower(), {}).get("label", "OAuth")


def default_social_provider_state(provider=""):
    return {
        "client_id": "",
        "client_secret": "",
        "accounts": [],
    }


def social_provider_secret_label(provider):
    provider = str(provider or "").strip().lower()
    if provider == "vk":
        return "Service Token"
    return "Client Secret"


def social_provider_secret_required(provider):
    provider = str(provider or "").strip().lower()
    return provider != "vk"


def social_provider_configured(provider, item):
    provider = str(provider or "").strip().lower()
    item = dict(item or {})
    if not item.get("client_id"):
        return False
    if social_provider_secret_required(provider):
        return bool(item.get("client_secret"))
    return True


def social_provider_setup_message(provider):
    label = social_provider_label(provider)
    if social_provider_secret_required(provider):
        return f"Сначала сохрани client_id и {social_provider_secret_label(provider).lower()} для {label}"
    return f"Сначала сохрани client_id для {label}"


def sanitize_social_account_record(provider, item):
    item = dict(item or {})
    account_id = str(item.get("id") or "").strip()
    if not account_id:
        return None
    login = str(item.get("login") or "").strip()[:120]
    name = str(item.get("name") or "").strip()[:160]
    email = str(item.get("email") or "").strip()[:160]
    label = str(item.get("label") or "").strip()[:160]
    if not label:
        label = name or login or email or f"{social_provider_label(provider)} #{account_id}"
    return {
        "id": account_id,
        "label": label,
        "login": login,
        "name": name,
        "email": email,
        "profile_url": str(item.get("profile_url") or "").strip()[:500],
        "avatar_url": str(item.get("avatar_url") or "").strip()[:500],
        "linked_at": int(item.get("linked_at") or now_ts()),
        "last_used_at": int(item.get("last_used_at") or 0),
    }


def sanitize_social_provider_state(provider, item):
    clean = default_social_provider_state(provider)
    item = dict(item or {})
    clean["client_id"] = str(item.get("client_id") or "").strip()[:240]
    clean["client_secret"] = str(item.get("client_secret") or "").strip()[:512]
    seen = set()
    accounts = []
    for row in item.get("accounts", []) if isinstance(item.get("accounts"), list) else []:
        account = sanitize_social_account_record(provider, row)
        if not account or account["id"] in seen:
            continue
        seen.add(account["id"])
        accounts.append(account)
    clean["accounts"] = accounts
    return clean


def social_provider_enabled(provider, item):
    item = dict(item or {})
    return bool(social_provider_configured(provider, item) and item.get("accounts"))


def social_public_meta(auth=None):
    auth = normalize_auth_state(auth or load_auth())
    social = auth.get("social", {})
    result = {}
    for provider in SOCIAL_PROVIDERS:
        item = sanitize_social_provider_state(provider, social.get(provider))
        result[provider] = {
            "label": social_provider_label(provider),
            "configured": social_provider_configured(provider, item),
            "linked_count": len(item.get("accounts", [])),
            "enabled": social_provider_enabled(provider, item),
        }
    return result


def default_captcha_state():
    return {
        "mode": CAPTCHA_MODE_DIGITS,
        "site_key": "",
        "secret_key": "",
    }


def sanitize_captcha_mode(value):
    value = str(value or "").strip().lower()
    return CAPTCHA_MODE_RECAPTCHA if value == CAPTCHA_MODE_RECAPTCHA else CAPTCHA_MODE_DIGITS


def sanitize_captcha_state(item):
    clean = default_captcha_state()
    item = dict(item or {})
    clean["mode"] = sanitize_captcha_mode(item.get("mode"))
    clean["site_key"] = str(item.get("site_key") or "").strip()[:240]
    clean["secret_key"] = str(item.get("secret_key") or "").strip()[:512]
    return clean


def effective_captcha_state(auth=None):
    auth = normalize_auth_state(auth or load_auth())
    state = sanitize_captcha_state(auth.get("captcha"))
    configured = bool(state.get("site_key") and state.get("secret_key"))
    effective_mode = CAPTCHA_MODE_RECAPTCHA if state.get("mode") == CAPTCHA_MODE_RECAPTCHA and configured else CAPTCHA_MODE_DIGITS
    state["configured"] = configured
    state["effective_mode"] = effective_mode
    return state


def normalize_auth_state(data):
    raw = dict(data or {})
    state = {
        "version": max(2, int(raw.get("version") or 2)),
        "username": clean_username(raw.get("username", "admin")),
        "password": raw.get("password") if isinstance(raw.get("password"), dict) else {},
        "updated_at": int(raw.get("updated_at") or now_ts()),
    }
    totp = default_totp_state()
    if isinstance(raw.get("totp"), dict):
        totp.update(raw.get("totp") or {})
    try:
        totp["secret"] = normalize_totp_secret(totp.get("secret"))
    except ValueError:
        totp["secret"] = ""
    totp["enabled"] = bool(totp.get("enabled") and totp.get("secret"))
    totp["digits"] = TOTP_DIGITS
    totp["period"] = TOTP_PERIOD_SECONDS
    totp["updated_at"] = int(totp.get("updated_at") or 0)
    state["totp"] = totp
    seen_passkeys = set()
    passkeys = []
    for item in raw.get("passkeys", []) if isinstance(raw.get("passkeys"), list) else []:
        clean = sanitize_passkey_record(item)
        if not clean["id"] or not clean["credential_data"] or clean["id"] in seen_passkeys:
            continue
        seen_passkeys.add(clean["id"])
        passkeys.append(clean)
    seen_ssh_keys = set()
    ssh_keys = []
    for item in raw.get("ssh_keys", []) if isinstance(raw.get("ssh_keys"), list) else []:
        clean = sanitize_ssh_key_record(item)
        if not clean or clean["id"] in seen_ssh_keys:
            continue
        seen_ssh_keys.add(clean["id"])
        ssh_keys.append(clean)
    state["passkeys"] = passkeys
    state["ssh_keys"] = ssh_keys
    state["captcha"] = sanitize_captcha_state(raw.get("captcha"))
    delegated_users = []
    seen_delegated_usernames = {state["username"]}
    for item in raw.get("delegated_users", []) if isinstance(raw.get("delegated_users"), list) else []:
        clean = sanitize_delegated_user_record(item)
        if not clean:
            continue
        username = clean.get("username", "")
        if not username or username in seen_delegated_usernames:
            continue
        seen_delegated_usernames.add(username)
        delegated_users.append(clean)
    delegated_users.sort(key=lambda item: str(item.get("username") or "").lower())
    state["delegated_users"] = delegated_users
    social_state = {}
    raw_social = raw.get("social") if isinstance(raw.get("social"), dict) else {}
    for provider in SOCIAL_PROVIDERS:
        social_state[provider] = sanitize_social_provider_state(provider, raw_social.get(provider))
    state["social"] = social_state
    return state


def normalize_router_access_flags(value):
    flags = set()
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = re.findall(r"[ABG]", str(value or "").upper())
    for item in items:
        token = str(item or "").strip().upper()
        if token in ROUTER_ACCESS_FLAGS:
            flags.add(token)
    if "G" in flags:
        return ["G"]
    if "B" in flags:
        return ["A", "B"]
    if "A" in flags:
        return ["A"]
    return []


def expand_router_access_flags(value):
    flags = normalize_router_access_flags(value)
    if "G" in flags:
        return {"A", "B", "G"}
    if "B" in flags:
        return {"A", "B"}
    if "A" in flags:
        return {"A"}
    return set()


def sanitize_router_access_map(value):
    if not isinstance(value, dict):
        return {}
    clean = {}
    for router_id, flags in value.items():
        try:
            router_key = clean_router_id(router_id)
        except Exception:
            continue
        normalized = normalize_router_access_flags(flags)
        if normalized:
            clean[router_key] = normalized
    return dict(sorted(clean.items(), key=lambda item: item[0].lower()))


def sanitize_delegated_user_record(item):
    if not isinstance(item, dict):
        return None
    try:
        username = clean_username(item.get("username", ""))
    except Exception:
        return None
    password = item.get("password") if isinstance(item.get("password"), dict) else {}
    if not str(password.get("hash") or "").strip():
        return None
    ts = now_ts()
    return {
        "id": str(item.get("id") or secrets.token_hex(8)).strip()[:64] or secrets.token_hex(8),
        "username": username,
        "password": password,
        "enabled": bool(item.get("enabled", True)),
        "router_flags": sanitize_router_access_map(item.get("router_flags")),
        "created_at": int(item.get("created_at") or ts),
        "updated_at": int(item.get("updated_at") or ts),
    }


def auth_password_matches(stored, password):
    stored = stored if isinstance(stored, dict) else {}
    digest = password_digest(password or "", stored.get("salt"), stored.get("iterations", PBKDF2_ITERATIONS))
    return secrets.compare_digest(digest["hash"], str(stored.get("hash") or ""))


def find_delegated_user(auth, username):
    wanted = str(username or "").strip()
    if not wanted:
        return None
    for item in normalize_auth_state(auth).get("delegated_users", []):
        if secrets.compare_digest(item.get("username", ""), wanted):
            return item
    return None


def effective_router_access_map(user):
    if not isinstance(user, dict):
        return {}
    result = {}
    for router_id, flags in (user.get("router_flags") or {}).items():
        expanded = sorted(expand_router_access_flags(flags))
        if expanded:
            result[str(router_id)] = expanded
    return result


def account_profile(auth, username):
    auth = normalize_auth_state(auth or load_auth())
    wanted = str(username or "").strip()
    owner_username = auth.get("username", "admin")
    if wanted and secrets.compare_digest(wanted, owner_username):
        return {
            "username": owner_username,
            "is_owner": True,
            "enabled": True,
            "router_flags": {},
            "router_ids": [],
        }
    delegated = find_delegated_user(auth, wanted)
    if not delegated or not delegated.get("enabled"):
        return None
    router_map = effective_router_access_map(delegated)
    return {
        "id": delegated.get("id", ""),
        "username": delegated.get("username", ""),
        "is_owner": False,
        "enabled": True,
        "router_flags": router_map,
        "router_ids": sorted(router_map.keys()),
    }


def router_flags_for_account(account, router_id):
    if not isinstance(account, dict):
        return set()
    if account.get("is_owner"):
        return {"A", "B", "G"}
    return set((account.get("router_flags") or {}).get(str(router_id or ""), []))


def account_has_router_flag(account, router_id, flag):
    token = str(flag or "").strip().upper()
    if token == "A":
        return bool(router_flags_for_account(account, router_id) & {"A", "B", "G"})
    if token == "B":
        return bool(router_flags_for_account(account, router_id) & {"B", "G"})
    if token == "G":
        return "G" in router_flags_for_account(account, router_id)
    return False


def account_can_add_router(account):
    if not isinstance(account, dict):
        return False
    if account.get("is_owner"):
        return True
    for flags in (account.get("router_flags") or {}).values():
        if "G" in set(flags or []):
            return True
    return False


def grant_delegated_router_flag(auth, username, router_id, flag="G"):
    auth = normalize_auth_state(auth or load_auth())
    wanted_user = str(username or "").strip()
    wanted_router = str(router_id or "").strip()
    token = str(flag or "").strip().upper()
    if not wanted_user or not wanted_router or token not in ROUTER_ACCESS_FLAGS:
        return auth
    updated_users = []
    changed = False
    for item in auth.get("delegated_users", []):
        current_username = str(item.get("username") or "").strip()
        if not secrets.compare_digest(current_username, wanted_user):
            updated_users.append(item)
            continue
        router_flags = sanitize_router_access_map(item.get("router_flags"))
        next_flags = set(router_flags.get(wanted_router, []))
        if token not in next_flags:
            next_flags.add(token)
            router_flags[wanted_router] = [candidate for candidate in ROUTER_ACCESS_FLAGS if candidate in next_flags]
            item = {**item, "router_flags": router_flags, "updated_at": now_ts()}
            changed = True
        updated_users.append(item)
    if not changed:
        return auth
    return save_auth_state({**auth, "delegated_users": updated_users})


def delegated_user_session_snapshot(session_rows=None):
    rows = [item for item in (session_rows or []) if isinstance(item, dict)]
    if not rows:
        return {
            "count": 0,
            "created_at": 0,
            "last_seen": 0,
            "ip": "",
            "client": "",
            "auth_method": "",
        }
    rows.sort(key=lambda item: (int(item.get("last_seen") or 0), int(item.get("created_at") or 0)), reverse=True)
    latest = rows[0]
    return {
        "count": len(rows),
        "created_at": int(latest.get("created_at") or 0),
        "last_seen": int(latest.get("last_seen") or 0),
        "ip": str(latest.get("ip") or ""),
        "client": str(latest.get("client") or ""),
        "auth_method": str(latest.get("auth_method") or ""),
    }


def delegated_user_login_snapshot(login_rows=None):
    rows = [item for item in (login_rows or []) if isinstance(item, dict)]
    if not rows:
        return {
            "count": 0,
            "ts": 0,
            "ip": "",
            "client": "",
            "auth_method": "",
        }
    rows.sort(key=lambda item: int(item.get("ts") or 0), reverse=True)
    latest = rows[0]
    return {
        "count": len(rows),
        "ts": int(latest.get("ts") or 0),
        "ip": str(latest.get("ip") or ""),
        "client": str(latest.get("client") or ""),
        "auth_method": str(latest.get("auth_method") or ""),
    }


def serialize_delegated_user(auth, user, router_rows=None, session_rows=None, login_rows=None):
    auth = normalize_auth_state(auth or load_auth())
    user = sanitize_delegated_user_record(user)
    if not user:
        return None
    session = delegated_user_session_snapshot(session_rows)
    login = delegated_user_login_snapshot(login_rows)
    active_session_count = int(session.get("count") or 0)
    login_count = int(login.get("count") or 0)
    last_login_at = int(login.get("ts") or session.get("created_at") or 0)
    last_seen_at = int(session.get("last_seen") or login.get("ts") or 0)
    last_ip = session.get("ip") or login.get("ip") or ""
    last_client = session.get("client") or login.get("client") or ""
    last_auth_method = session.get("auth_method") or login.get("auth_method") or ""
    router_name_map = {}
    for row in router_rows or []:
        router = row_to_router(row)
        router_name_map[str(router.get("id") or "")] = str(router.get("name") or router.get("id") or "")
    router_access = []
    for router_id, flags in effective_router_access_map(user).items():
        label = "".join(flag for flag in ROUTER_ACCESS_FLAGS if flag in flags)
        router_access.append(
            {
                "id": router_id,
                "name": router_name_map.get(router_id, router_id),
                "flags": flags,
                "flag_label": label or "-",
            }
        )
    router_access.sort(key=lambda item: str(item.get("name") or item.get("id") or "").lower())
    return {
        "id": user.get("id", ""),
        "username": user.get("username", ""),
        "enabled": bool(user.get("enabled")),
        "created_at": int(user.get("created_at") or 0),
        "updated_at": int(user.get("updated_at") or 0),
        "router_flags": effective_router_access_map(user),
        "router_access": router_access,
        "router_count": len(router_access),
        "session_count": active_session_count,
        "login_count": login_count,
        "online": bool(active_session_count),
        "last_login_at": last_login_at,
        "last_seen_at": last_seen_at,
        "last_ip": last_ip,
        "last_client": last_client,
        "last_auth_method": last_auth_method,
    }


def public_auth_meta(auth=None):
    auth = normalize_auth_state(auth or load_auth())
    captcha = effective_captcha_state(auth)
    passkey_info = passkey_inventory(auth)
    return {
        "username": auth.get("username", "admin"),
        "totp_enabled": bool(auth.get("totp", {}).get("enabled")),
        "passkeys_supported": passkey_supported(),
        "passkey_count": len(passkey_info.get("credentials", [])),
        "passkey_invalid_count": int(passkey_info.get("invalid_count", 0)),
        "ssh_key_count": len(auth.get("ssh_keys", [])),
        "ed25519_enabled": bool(auth.get("ssh_keys", [])),
        "ssh_keys": [{"id": item.get("id", ""), "label": item.get("label", "SSH ED25519")} for item in auth.get("ssh_keys", [])],
        "captcha": {
            "mode": captcha.get("effective_mode", CAPTCHA_MODE_DIGITS),
            "configured": bool(captcha.get("configured")),
            "site_key": captcha.get("site_key", "") if captcha.get("configured") else "",
        },
        "social": social_public_meta(auth),
    }


def admin_auth_meta(auth=None):
    auth = normalize_auth_state(auth or load_auth())
    captcha = effective_captcha_state(auth)
    return {
        **public_auth_meta(auth),
        "captcha": {
            "mode": sanitize_captcha_mode(auth.get("captcha", {}).get("mode")),
            "effective_mode": captcha.get("effective_mode", CAPTCHA_MODE_DIGITS),
            "configured": bool(captcha.get("configured")),
            "site_key": auth.get("captcha", {}).get("site_key", ""),
            "secret_key": auth.get("captcha", {}).get("secret_key", ""),
        },
        "passkeys": [
            {
                "id": item.get("id", ""),
                "label": item.get("label", "Passkey"),
                "created_at": int(item.get("created_at") or 0),
                "last_used_at": int(item.get("last_used_at") or 0),
            }
            for item in auth.get("passkeys", [])
        ],
        "ssh_keys": [
            {
                "id": item.get("id", ""),
                "label": item.get("label", "SSH ED25519"),
                "created_at": int(item.get("created_at") or 0),
                "last_used_at": int(item.get("last_used_at") or 0),
                "public_key": item.get("public_key", ""),
            }
            for item in auth.get("ssh_keys", [])
        ],
        "social": {
            provider: {
                "label": social_provider_label(provider),
                "client_id": auth.get("social", {}).get(provider, {}).get("client_id", ""),
                "client_secret": auth.get("social", {}).get(provider, {}).get("client_secret", ""),
                "configured": social_provider_configured(provider, auth.get("social", {}).get(provider, {})),
                "linked_count": len(auth.get("social", {}).get(provider, {}).get("accounts", [])),
                "enabled": social_provider_enabled(provider, auth.get("social", {}).get(provider, {})),
                "accounts": list(auth.get("social", {}).get(provider, {}).get("accounts", [])),
            }
            for provider in SOCIAL_PROVIDERS
        },
    }


def auth_method_label(value):
    value = str(value or "").strip().lower()
    if value == "passkey":
        return "Passkey"
    if value == "ed25519":
        return "ED25519"
    if value == "password+totp":
        return "Password + 2FA"
    if value == "password":
        return "Password"
    if value == "github":
        return "GitHub"
    if value == "vk":
        return "VK ID"
    return "Auth"


def passkey_supported():
    return Fido2Server is not None and PublicKeyCredentialRpEntity is not None


def webauthn_json(value):
    if dataclasses.is_dataclass(value):
        return {field.name: webauthn_json(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, bytes):
        return b64url(value)
    if isinstance(value, dict):
        return {str(key): webauthn_json(item) for key, item in value.items() if item is not None}
    if isinstance(value, (list, tuple, set)):
        return [webauthn_json(item) for item in value if item is not None]
    return value


def save_auth_state(data):
    clean = normalize_auth_state(data)
    clean["updated_at"] = now_ts()
    write_json_private(AUTH_FILE, clean)
    return clean


def update_auth_state(mutator):
    with AUTH_STATE_LOCK:
        current = normalize_auth_state(load_auth())
        result = mutator(current)
        return save_auth_state(result if isinstance(result, dict) else current)


def passkey_inventory(auth=None):
    auth = normalize_auth_state(auth or load_auth())
    credentials = []
    invalid_count = 0
    for item in auth.get("passkeys", []):
        try:
            credentials.append(AttestedCredentialData(b64url_decode(item.get("credential_data", ""))))
        except Exception:
            invalid_count += 1
    return {
        "credentials": credentials,
        "stored_count": len(auth.get("passkeys", [])),
        "invalid_count": invalid_count,
    }


def passkey_credentials(auth=None):
    return passkey_inventory(auth).get("credentials", [])


def find_passkey_record(auth, credential_id):
    wanted = str(credential_id or "").strip()
    for item in normalize_auth_state(auth).get("passkeys", []):
        if item.get("id") == wanted:
            return item
    return None


def find_ssh_key_record(auth, key_id):
    wanted = str(key_id or "").strip()
    for item in normalize_auth_state(auth).get("ssh_keys", []):
        if item.get("id") == wanted:
            return item
    return None


def find_social_account_record(auth, provider, account_id):
    wanted = str(account_id or "").strip()
    if not wanted:
        return None
    social = normalize_auth_state(auth).get("social", {})
    for item in social.get(str(provider or "").strip().lower(), {}).get("accounts", []):
        if item.get("id") == wanted:
            return item
    return None


def upsert_social_account(auth, provider, account):
    clean = normalize_auth_state(auth)
    provider = str(provider or "").strip().lower()
    if provider not in SOCIAL_PROVIDERS:
        raise ValueError("unknown provider")
    account_row = sanitize_social_account_record(provider, account)
    if not account_row:
        raise ValueError("account is empty")
    items = []
    replaced = False
    for current in clean.get("social", {}).get(provider, {}).get("accounts", []):
        if current.get("id") == account_row["id"]:
            merged = dict(current)
            merged.update(account_row)
            merged["linked_at"] = int(current.get("linked_at") or account_row.get("linked_at") or now_ts())
            merged["last_used_at"] = int(current.get("last_used_at") or account_row.get("last_used_at") or 0)
            items.append(sanitize_social_account_record(provider, merged))
            replaced = True
        else:
            items.append(current)
    if not replaced:
        items.append(account_row)
    clean["social"][provider]["accounts"] = [item for item in items if item]
    return clean


def remove_social_account(auth, provider, account_id):
    clean = normalize_auth_state(auth)
    provider = str(provider or "").strip().lower()
    clean["social"][provider]["accounts"] = [
        item for item in clean.get("social", {}).get(provider, {}).get("accounts", []) if item.get("id") != str(account_id or "").strip()
    ]
    return clean


def touch_social_account_last_used(auth, provider, account_id):
    clean = normalize_auth_state(auth)
    provider = str(provider or "").strip().lower()
    now = now_ts()
    for item in clean.get("social", {}).get(provider, {}).get("accounts", []):
        if item.get("id") == str(account_id or "").strip():
            item["last_used_at"] = now
            break
    return clean


def oauth_fetch_json(url, method="GET", headers=None, data=None):
    payload = None
    header_map = {str(key): str(value) for key, value in dict(headers or {}).items()}
    if data is not None:
        if isinstance(data, (bytes, bytearray)):
            payload = bytes(data)
        elif isinstance(data, dict):
            payload = urllib.parse.urlencode(data).encode("utf-8")
            if not any(str(key).lower() == "content-type" for key in header_map):
                header_map["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            payload = str(data).encode("utf-8")
    request = urllib.request.Request(url, data=payload, method=str(method or "GET").upper())
    base_headers = {"User-Agent": f"{APP_NAME}/1.0", "Accept": "application/json"}
    for key, value in header_map.items():
        request.add_header(str(key), str(value))
    for key, value in base_headers.items():
        if not request.has_header(key):
            request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read().decode("utf-8", "replace")
            return json.loads(body or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            detail = json.loads(body or "{}")
        except Exception:
            detail = {"error": body[:400] or exc.reason}
        message = detail.get("error_description") or detail.get("error") or detail.get("message") or exc.reason
        raise ValueError(str(message or "OAuth HTTP error"))
    except Exception as exc:
        raise ValueError(str(exc))


def origin_from_parts(scheme, host):
    scheme = str(scheme or "").strip().lower()
    host = str(host or "").strip()
    if not scheme or not host:
        return ""
    parts = urllib.parse.urlsplit(f"{scheme}://{host}")
    if not parts.scheme or not parts.hostname:
        return ""
    default_port = 443 if parts.scheme == "https" else 80 if parts.scheme == "http" else None
    netloc = parts.hostname
    if parts.port and parts.port != default_port:
        netloc = f"{netloc}:{parts.port}"
    return f"{parts.scheme}://{netloc}"


def public_url_origin(public_url):
    value = str(public_url or "").strip()
    if not value:
        return ""
    parts = urllib.parse.urlsplit(value)
    if not parts.scheme or not parts.netloc:
        return ""
    return origin_from_parts(parts.scheme, parts.netloc)


def append_forwarded_port(scheme, host, forwarded_port=""):
    scheme = str(scheme or "").strip().lower()
    host = str(host or "").strip()
    forwarded_port = str(forwarded_port or "").split(",", 1)[0].strip()
    if not host:
        return ""
    try:
        parts = urllib.parse.urlsplit(f"{scheme or 'http'}://{host}")
    except Exception:
        return host
    if not parts.hostname:
        return host
    if parts.port:
        return parts.netloc or host
    try:
        port = int(forwarded_port)
    except (TypeError, ValueError):
        return parts.netloc or host
    if not (0 < port < 65536):
        return parts.netloc or host
    default_port = 443 if scheme == "https" else 80 if scheme == "http" else None
    if default_port and port == default_port:
        return parts.netloc or host
    host_name = parts.hostname
    if ":" in host_name and not host_name.startswith("["):
        host_name = f"[{host_name}]"
    return f"{host_name}:{port}"


def prefer_origin_with_explicit_port(primary="", secondary=""):
    primary = str(primary or "").strip()
    secondary = str(secondary or "").strip()
    if not primary:
        return secondary
    if not secondary:
        return primary
    try:
        primary_parts = urllib.parse.urlsplit(primary)
        secondary_parts = urllib.parse.urlsplit(secondary)
        if (
            primary_parts.scheme == secondary_parts.scheme
            and (primary_parts.hostname or "") == (secondary_parts.hostname or "")
            and not primary_parts.port
            and secondary_parts.port
        ):
            return secondary
    except Exception:
        pass
    return primary


def public_url_rp_id(public_url):
    value = str(public_url or "").strip()
    if not value:
        return ""
    try:
        return urllib.parse.urlsplit(value).hostname or ""
    except Exception:
        return ""


def vps_host_name(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urllib.parse.urlsplit(raw if "://" in raw else f"//{raw}")
        return parsed.hostname or ""
    except Exception:
        return ""


def should_preserve_router_vps_fallback(vps_host="", public_url=""):
    next_vps_host = vps_host_name(vps_host)
    next_public_host = public_url_rp_id(public_url)
    return bool(next_vps_host and next_public_host and next_vps_host == next_public_host)


def router_fallback_hub_url(router, fallback_host="", port=8088):
    host = str((router or {}).get("vps_host") or "").strip()
    host = vps_host_name(host) or vps_host_name(fallback_host) or "127.0.0.1"
    return f"http://{host}:{int(port or 8088)}"


def row_str_value(row, key, default=""):
    try:
        value = row[key]
    except (TypeError, KeyError, IndexError):
        value = None
    if value in (None, ""):
        return default
    return str(value)


def row_int_value(row, key, default=0):
    try:
        value = row[key]
    except (TypeError, KeyError, IndexError):
        value = None
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def canonical_router_hub_update(router, app_public_url="", request_origin=""):
    router = router or {}
    router_public_url = public_url_origin(router.get("public_url", ""))
    app_origin = public_url_origin(app_public_url)
    request_origin = public_url_origin(request_origin)
    public_url = prefer_origin_with_explicit_port(router_public_url, app_origin)
    public_url = prefer_origin_with_explicit_port(public_url, request_origin)
    hub_url = public_url or request_origin
    vps_host = str(router.get("vps_host") or "").strip()
    if not vps_host and hub_url:
        try:
            vps_host = urllib.parse.urlsplit(hub_url).hostname or ""
        except Exception:
            vps_host = ""
    return {
        "hub_url": hub_url,
        "public_url": public_url,
        "vps_host": vps_host,
    }


def verify_password_login(username, password, otp=""):
    auth = load_auth()
    profile = account_profile(auth, username)
    if not profile:
        return False, "Неверный логин или пароль", {}, "password"
    if not verify_login(username, password):
        return False, "Неверный логин или пароль", auth, "password"
    if profile.get("is_owner"):
        totp = auth.get("totp", {})
    else:
        totp = {}
    if totp.get("enabled"):
        if not verify_totp(totp.get("secret", ""), otp):
            return False, "Неверный код 2FA", auth, "password+totp"
        return True, "", profile, "password+totp"
    return True, "", profile, "password"


def ssh_auth_namespace():
    return "owrt-remote-hub"


def ssh_auth_principal(username=""):
    return (clean_username(username or current_username()) or "admin").replace(" ", "_")


def build_ssh_auth_message(ticket, username="", host="", issued_at=0):
    lines = [
        "OpenWrt Remote Hub ED25519 login",
        f"ticket={ticket}",
        f"user={clean_username(username or current_username())}",
        f"host={host or '-'}",
        f"issued_at={int(issued_at or now_ts())}",
    ]
    return "\n".join(lines) + "\n"


def ssh_auth_message_variants(message):
    text = str(message or "")
    variants = []
    seen = set()

    def add(value):
        if value not in seen:
            seen.add(value)
            variants.append(value)

    add(text)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    add(normalized)
    add(normalized.replace("\n", "\r\n"))
    trimmed = normalized.rstrip("\r\n")
    if trimmed:
        add(trimmed)
        add(trimmed + "\n")
        add(trimmed.replace("\n", "\r\n"))
        add(trimmed.replace("\n", "\r\n") + "\r\n")
    return variants


def verify_ssh_auth_signature(ssh_keys, username, message, signature_text):
    signature_text = str(signature_text or "").strip()
    if "BEGIN SSH SIGNATURE" not in signature_text:
        raise ValueError("Вставь ASCII SSH signature целиком")
    namespace = ssh_auth_namespace()
    principal = ssh_auth_principal(username)
    message_variants = ssh_auth_message_variants(message)
    for item in [sanitize_ssh_key_record(row) for row in (ssh_keys or [])]:
        if not item:
            continue
        with tempfile.TemporaryDirectory(prefix="owrt-hub-ssh-auth-") as tmp_name:
            tmp_dir = Path(tmp_name)
            allowed = tmp_dir / "allowed_signers"
            sig_file = tmp_dir / "challenge.sig"
            allowed.write_text(f"{principal} {item['public_key']}\n", encoding="utf-8")
            sig_file.write_text(signature_text + "\n", encoding="utf-8")
            for candidate in message_variants:
                try:
                    result = subprocess.run(
                        [
                            "ssh-keygen",
                            "-Y",
                            "verify",
                            "-f",
                            str(allowed),
                            "-I",
                            principal,
                            "-n",
                            namespace,
                            "-s",
                            str(sig_file),
                        ],
                        input=candidate.encode("utf-8"),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=10,
                    )
                except FileNotFoundError as exc:
                    raise ValueError("ssh-keygen не найден на сервере") from exc
                except subprocess.TimeoutExpired as exc:
                    raise ValueError("Проверка подписи ED25519 превысила таймаут") from exc
                if result.returncode == 0:
                    return item
    raise ValueError("Подпись ED25519 не подошла ни к одному зарегистрированному ключу")


def prune_auth_flows():
    now = now_ts()
    expired = [ticket for ticket, item in AUTH_FLOW_STATE.items() if now - int(item.get("created_at") or 0) > AUTH_CHALLENGE_TTL_SECONDS]
    for ticket in expired:
        AUTH_FLOW_STATE.pop(ticket, None)


def put_auth_flow(kind, payload):
    with AUTH_FLOW_LOCK:
        prune_auth_flows()
        ticket = secrets.token_urlsafe(24)
        AUTH_FLOW_STATE[ticket] = {"kind": str(kind or ""), "created_at": now_ts(), **dict(payload or {})}
        return ticket


def get_auth_flow(ticket, kind=""):
    with AUTH_FLOW_LOCK:
        prune_auth_flows()
        item = AUTH_FLOW_STATE.get(str(ticket or ""))
        if not item:
            return None
        if kind and item.get("kind") != kind:
            return None
        return dict(item)


def pop_auth_flow(ticket, kind=""):
    with AUTH_FLOW_LOCK:
        prune_auth_flows()
        item = AUTH_FLOW_STATE.get(str(ticket or ""))
        if not item:
            return None
        if kind and item.get("kind") != kind:
            return None
        return AUTH_FLOW_STATE.pop(str(ticket or ""), None)


def clamp_nonnegative_int(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def load_traffic_counters():
    ensure_state()
    if not TRAFFIC_COUNTERS_FILE.exists():
        return {"version": 1, "routers": {}}
    try:
        data = json.loads(TRAFFIC_COUNTERS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "routers": {}}
    routers = data.get("routers")
    if not isinstance(routers, dict):
        routers = {}
    return {"version": 1, "routers": routers}


def save_traffic_counters(data):
    payload = {"version": 1, "routers": data.get("routers") if isinstance(data, dict) else {}}
    atomic_write_text(TRAFFIC_COUNTERS_FILE, json.dumps(payload, ensure_ascii=False, indent=2) + "\n", mode=0o600)


def accumulate_traffic_clients(router_id, clients):
    router_key = str(router_id or "").strip()
    if not router_key or not isinstance(clients, list):
        return clients
    now = now_ts()
    with TRAFFIC_COUNTERS_LOCK:
        state = load_traffic_counters()
        routers = state.setdefault("routers", {})
        current_router_state = routers.get(router_key)
        if not isinstance(current_router_state, dict):
            current_router_state = {}
        router_state = {}
        for mac, item in current_router_state.items():
            if not isinstance(item, dict):
                continue
            mac_key = str(mac or "").strip().upper()
            if mac_key:
                router_state[mac_key] = item
        changed = router_state != current_router_state
        for client in clients:
            mac_key = str(client.get("mac") or "").strip().upper()
            if not mac_key:
                continue
            raw_rx = clamp_nonnegative_int(client.get("rx_bytes"))
            raw_tx = clamp_nonnegative_int(client.get("tx_bytes"))
            saved = router_state.get(mac_key)
            if isinstance(saved, dict):
                prev_raw_rx = clamp_nonnegative_int(saved.get("raw_rx_bytes"))
                prev_raw_tx = clamp_nonnegative_int(saved.get("raw_tx_bytes"))
                total_rx = clamp_nonnegative_int(saved.get("rx_bytes")) + max(0, raw_rx - prev_raw_rx)
                total_tx = clamp_nonnegative_int(saved.get("tx_bytes")) + max(0, raw_tx - prev_raw_tx)
            else:
                total_rx = raw_rx
                total_tx = raw_tx
            client["rx_bytes"] = total_rx
            client["tx_bytes"] = total_tx
            client["total_bytes"] = total_rx + total_tx
            next_saved = {
                "raw_rx_bytes": raw_rx,
                "raw_tx_bytes": raw_tx,
                "rx_bytes": total_rx,
                "tx_bytes": total_tx,
                "updated_at": now,
                "ip": str(client.get("ip") or ""),
                "name": str(client.get("name") or ""),
            }
            if router_state.get(mac_key) != next_saved:
                router_state[mac_key] = next_saved
                changed = True
        if changed or routers.get(router_key) != router_state:
            routers[router_key] = router_state
            save_traffic_counters(state)
    return clients


def clear_traffic_counters(router_id):
    router_key = str(router_id or "").strip()
    if not router_key:
        return
    with TRAFFIC_COUNTERS_LOCK:
        state = load_traffic_counters()
        routers = state.get("routers")
        if not isinstance(routers, dict) or router_key not in routers:
            return
        routers.pop(router_key, None)
        save_traffic_counters(state)


def save_auth(username, password):
    username = clean_username(username)
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    try:
        data = normalize_auth_state(load_auth())
    except Exception:
        data = normalize_auth_state({"username": username})
    data["username"] = username
    data["password"] = password_digest(password)
    data["updated_at"] = now_ts()
    write_json_private(AUTH_FILE, data)
    return data


def load_auth():
    ensure_state()
    if AUTH_FILE.exists():
        raw = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
        data = normalize_auth_state(raw)
        if data != raw:
            write_json_private(AUTH_FILE, data)
        return data
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
    except Exception:
        return False
    wanted = str(username or "").strip()
    if not wanted:
        return False
    if secrets.compare_digest(wanted, auth.get("username", "")):
        return auth_password_matches(auth.get("password", {}), password)
    delegated = find_delegated_user(auth, wanted)
    if not delegated or not delegated.get("enabled"):
        return False
    return auth_password_matches(delegated.get("password", {}), password)


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


def normalize_client_hint(value):
    hint = str(value or "").strip().lower()
    if hint in {"hub", "pwa", "standalone", "ios-home-screen", "home-screen"}:
        return "hub"
    return ""


def client_label(user_agent, client_hint=""):
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
    if normalize_client_hint(client_hint) == "hub":
        return f"{device} · Hub"
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


def make_hub_session(username, ip, user_agent, auth_method="password"):
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
        "auth_method": str(auth_method or "password"),
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
    for session in sessions:
        if secrets.compare_digest(session.get("token_hash", ""), wanted):
            result = session
            if touch:
                current_client = client_label(user_agent or "", session.get("client_hint", ""))
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
                    old_client = session.get("client") or client_label(old_user_agent, session.get("client_hint", ""))
                    session["user_agent"] = current_user_agent
                    session["client"] = current_client or client_label(current_user_agent, session.get("client_hint", ""))
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


def update_hub_session_client_hint(token, client_hint):
    token = str(token or "").strip()
    client_hint = normalize_client_hint(client_hint)
    if not token or not client_hint:
        return None
    wanted = session_hash(token)
    sessions = load_sessions()
    changed = False
    result = None
    ts = now_ts()
    for session in sessions:
        if not secrets.compare_digest(session.get("token_hash", ""), wanted):
            continue
        result = session
        old_hint = normalize_client_hint(session.get("client_hint", ""))
        if old_hint == client_hint:
            break
        session["client_hint"] = client_hint
        session["client"] = client_label(session.get("user_agent", ""), client_hint)
        session["last_seen"] = ts
        session["expires_at"] = ts + SESSION_TTL_SECONDS
        changed = True
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


def revoke_hub_sessions_for_username(username=""):
    wanted_user = str(username or "").strip()
    if not wanted_user:
        return 0
    revoke_ids = {
        str(session.get("id") or "")
        for session in load_sessions()
        if str(session.get("username") or "").strip() == wanted_user
    }
    removed = 0
    for session_id in revoke_ids:
        if not session_id:
            continue
        removed += revoke_hub_session(session_id=session_id)
    return removed


def list_hub_sessions(current_token="", username=""):
    current_hash = session_hash(current_token) if current_token else ""
    wanted_user = str(username or "").strip()
    rows = []
    for session in sorted(load_sessions(), key=lambda item: int(item.get("last_seen") or 0), reverse=True):
        session_username = str(session.get("username") or "")
        if wanted_user and not secrets.compare_digest(session_username, wanted_user):
            continue
        rows.append(
            {
                "id": session.get("id", ""),
                "username": session_username,
                "ip": session.get("ip", ""),
                "client": session.get("client") or client_label(session.get("user_agent", "")),
                "user_agent": session.get("user_agent", ""),
                "created_at": int(session.get("created_at") or 0),
                "last_seen": int(session.get("last_seen") or 0),
                "expires_at": int(session.get("expires_at") or 0),
                "auth_method": str(session.get("auth_method") or "password"),
                "current": bool(current_hash and secrets.compare_digest(session.get("token_hash", ""), current_hash)),
            }
        )
    return rows


def list_login_events(username=""):
    wanted_user = str(username or "").strip()
    rows = []
    for item in load_notifications():
        if str(item.get("kind") or "") != "login":
            continue
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        event_username = str(data.get("username") or "").strip()
        if wanted_user and not event_username:
            continue
        if wanted_user and not secrets.compare_digest(event_username, wanted_user):
            continue
        rows.append(
            {
                "id": item.get("id", ""),
                "username": event_username,
                "ip": str(data.get("ip") or ""),
                "client": str(data.get("client") or ""),
                "user_agent": str(data.get("user_agent") or ""),
                "ts": int(item.get("ts") or 0),
                "auth_method": str(data.get("auth_method") or "password"),
            }
        )
    rows.sort(key=lambda item: int(item.get("ts") or 0), reverse=True)
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
    return normalize_notification_items([item for item in data if isinstance(item, dict)])


def save_notifications(items):
    items = normalize_notification_items(items)
    items = sorted(
        items,
        key=lambda item: (int(item.get("ts") or 0), int(item.get("serial") or 0)),
        reverse=True,
    )[:NOTIFICATIONS_MAX]
    write_json_private(NOTIFICATIONS_FILE, {"notifications": items})


def normalize_notification_items(items):
    rows = [dict(item) for item in (items or []) if isinstance(item, dict)]
    rows.sort(key=lambda item: (int(item.get("ts") or 0), int(item.get("serial") or 0), str(item.get("id") or "")))
    serial = 0
    for item in rows:
        current = int(item.get("serial") or 0)
        if current <= serial:
            serial += 1
            item["serial"] = serial
        else:
            serial = current
            item["serial"] = current
    return rows


def latest_notification_serial(items=None):
    rows = normalize_notification_items(items if items is not None else load_notifications())
    return max((int(item.get("serial") or 0) for item in rows), default=0)


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
        next_serial = latest_notification_serial(items) + 1
        item["serial"] = next_serial
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
    with NOTIFICATIONS_COND:
        NOTIFICATIONS_COND.notify_all()
    queue_web_push_notification(item)
    return item


def list_notifications(after=0, limit=60, after_serial=0):
    try:
        after = int(after or 0)
    except (TypeError, ValueError):
        after = 0
    try:
        after_serial = int(after_serial or 0)
    except (TypeError, ValueError):
        after_serial = 0
    try:
        limit = max(1, min(120, int(limit or 60)))
    except (TypeError, ValueError):
        limit = 60
    items = load_notifications()
    latest_serial = max((int(item.get("serial") or 0) for item in items), default=0)
    if after_serial > latest_serial:
        after_serial = 0
    if after_serial > 0:
        items = [item for item in items if int(item.get("serial") or 0) > after_serial]
    elif after > 0:
        items = [item for item in items if int(item.get("ts") or 0) >= after]
    return sorted(
        items,
        key=lambda item: (int(item.get("ts") or 0), int(item.get("serial") or 0)),
        reverse=True,
    )[:limit]


def wait_for_notifications(after_serial=0, timeout=25, limit=60):
    try:
        after_serial = int(after_serial or 0)
    except (TypeError, ValueError):
        after_serial = 0
    try:
        timeout = max(1, min(60, int(timeout or 25)))
    except (TypeError, ValueError):
        timeout = 25
    deadline = time.time() + timeout
    while True:
        items = list_notifications(limit=limit, after_serial=after_serial)
        if items:
            return items
        remaining = deadline - time.time()
        if remaining <= 0:
            return []
        with NOTIFICATIONS_COND:
            NOTIFICATIONS_COND.wait(timeout=remaining)


def clear_notifications():
    with NOTIFICATIONS_LOCK:
        save_notifications([])
    with NOTIFICATIONS_COND:
        NOTIFICATIONS_COND.notify_all()


def b64url(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def make_pkce_verifier():
    verifier = secrets.token_urlsafe(64)
    return verifier[:96]


def make_pkce_challenge(verifier):
    return b64url(hashlib.sha256(str(verifier or "").encode("utf-8")).digest())


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
        client_hint = normalize_client_hint(item.get("client_hint", ""))
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
                "client_hint": client_hint,
                "ip": str(item.get("ip") or "")[:80],
                "user_agent": str(item.get("user_agent") or "")[:260],
                "created_at": int(item.get("created_at") or now_ts()),
                "last_seen": int(item.get("last_seen") or now_ts()),
            }
        )
    write_json_private(PUSH_SUBSCRIPTIONS_FILE, {"subscriptions": cleaned[-80:]})


def load_push_delivery_attempts():
    ensure_state()
    if not PUSH_DELIVERY_FILE.exists():
        return []
    try:
        data = json.loads(PUSH_DELIVERY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("attempts", [])
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def save_push_delivery_attempts(items):
    cleaned = []
    for item in items:
        if not isinstance(item, dict):
            continue
        results = item.get("results") if isinstance(item.get("results"), list) else []
        cleaned.append(
            {
                "ts": int(item.get("ts") or now_ts()),
                "title": str(item.get("title") or APP_NAME)[:160],
                "body": str(item.get("body") or "")[:260],
                "kind": str(item.get("kind") or "info")[:40],
                "summary": str(item.get("summary") or "")[:120],
                "results": [
                    {
                        "client": str(result.get("client") or "браузер")[:120],
                        "endpoint_host": str(result.get("endpoint_host") or "")[:120],
                        "result": str(result.get("result") or "unknown")[:200],
                    }
                    for result in results
                    if isinstance(result, dict)
                ][:20],
            }
        )
    write_json_private(PUSH_DELIVERY_FILE, {"attempts": cleaned[-20:]})


def push_endpoint_host(endpoint):
    try:
        return (urllib.parse.urlparse(str(endpoint or "")).hostname or "")[:120]
    except Exception:
        return ""


def remember_push_delivery(payload, results, summary=""):
    attempt = {
        "ts": int((payload or {}).get("ts") or now_ts()),
        "title": (payload or {}).get("title") or APP_NAME,
        "body": (payload or {}).get("body") or "",
        "kind": (payload or {}).get("kind") or "info",
        "summary": summary,
        "results": results,
    }
    with PUSH_LOCK:
        attempts = load_push_delivery_attempts()
        attempts.append(attempt)
        save_push_delivery_attempts(attempts)


def web_push_backend_status():
    status = {
        "ready": False,
        "reason": "",
        "error": "",
        "public_key": "",
        "subject": vapid_subject(),
    }
    if webpush is None or WebPushException is None:
        status["reason"] = "pywebpush_missing"
        status["error"] = "На VPS не установлен Web Push модуль. Запусти свежий install-vps.sh."
        return status
    try:
        public_key = vapid_public_key()
    except Exception as exc:
        detail = " ".join(str(exc).split())[:160]
        status["reason"] = "vapid_key_error"
        status["error"] = f"Не смог подготовить VAPID ключи на VPS. {detail}".strip()
        return status
    status["public_key"] = public_key
    if not public_key:
        status["reason"] = "vapid_key_missing"
        status["error"] = "Не смог создать VAPID ключи на VPS"
        return status
    status["ready"] = True
    return status


def push_status_snapshot():
    backend = web_push_backend_status()
    subscriptions = sorted(
        load_push_subscriptions(),
        key=lambda item: int(item.get("last_seen") or 0),
        reverse=True,
    )
    attempts = load_push_delivery_attempts()
    last_attempt = attempts[-1] if attempts else None
    return {
        "ok": True,
        "ready": backend.get("ready", False),
        "ready_reason": backend.get("reason", ""),
        "ready_error": backend.get("error", ""),
        "subscription_count": len(subscriptions),
        "subject": backend.get("subject", vapid_subject(subscriptions[0] if subscriptions else None)),
        "subscriptions": [
            {
                "id": item.get("id", ""),
                "client": item.get("client", "браузер"),
                "client_hint": item.get("client_hint", ""),
                "endpoint_host": push_endpoint_host(item.get("endpoint", "")),
                "last_seen": int(item.get("last_seen") or 0),
                "last_seen_iso": iso_time(int(item.get("last_seen") or 0)),
            }
            for item in subscriptions[:8]
        ],
        "last_attempt": last_attempt,
    }


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
    return web_push_backend_status().get("ready", False)


def web_push_apple_endpoint(subscription):
    endpoint = str((subscription or {}).get("endpoint") or "").lower()
    return "push.apple.com" in endpoint


def vapid_subject(subscription=None):
    configured = str(os.environ.get("OWRT_REMOTE_VAPID_SUB", "")).strip()
    if configured:
        return configured
    public_url = str(os.environ.get("OWRT_REMOTE_PUBLIC_URL", "")).strip().rstrip("/")
    if public_url.lower().startswith("https://"):
        return public_url
    return "mailto:admin@localhost"


def save_push_subscription(subscription, username="", ip="", user_agent=""):
    if not isinstance(subscription, dict):
        raise ValueError("subscription must be object")
    endpoint = str(subscription.get("endpoint") or "").strip()
    keys = subscription.get("keys") if isinstance(subscription.get("keys"), dict) else {}
    p256dh = str(keys.get("p256dh") or "").strip()
    auth = str(keys.get("auth") or "").strip()
    client_hint = normalize_client_hint(subscription.get("client_hint", ""))
    if not endpoint or not p256dh or not auth:
        raise ValueError("bad push subscription")
    ts = now_ts()
    item = {
        "id": hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:16],
        "endpoint": endpoint,
        "keys": {"p256dh": p256dh, "auth": auth},
        "client": client_label(user_agent, client_hint),
        "client_hint": client_hint,
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
    payload = {
        "title": item.get("title") or APP_NAME,
        "body": item.get("body") or "",
        "tag": "owrt-" + str(item.get("id") or item.get("kind") or now_ts()),
        "url": "/",
        "kind": item.get("kind") or "info",
        "ts": int(item.get("ts") or now_ts()),
    }
    payload["web_push"] = 8030
    payload["notification"] = {
        "title": payload["title"],
        "body": payload["body"],
        "tag": payload["tag"],
        "navigate": payload["url"],
        "silent": False,
    }
    return payload


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
            vapid_claims={"sub": vapid_subject(subscription)},
            timeout=10,
            ttl=86400,
        )
        return "ok"
    except Exception as exc:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        detail = ""
        try:
            detail = str(getattr(response, "text", "") or "").strip()
        except Exception:
            detail = ""
        if not detail:
            detail = " ".join(str(exc).split())
        detail = detail[:160]
        if status_code in (404, 410):
            return "gone"
        if detail:
            return f"error:{status_code or exc.__class__.__name__}:{detail}"
        return f"error:{status_code or exc.__class__.__name__}"


def push_result_message(result, subscription=None):
    if result == "ok":
        return ""
    if result == "unavailable":
        return "На VPS не готов Web Push. Запусти свежий install-vps.sh и проверь HTTPS."
    if result == "gone":
        return "Подписка браузера устарела. Включи push заново на этом устройстве."
    if str(result).startswith("error:"):
        parts = str(result).split(":", 2)
        code = parts[1] if len(parts) > 1 and parts[1] else "unknown"
        detail = parts[2] if len(parts) > 2 else ""
        detail_lower = detail.lower()
        if web_push_apple_endpoint(subscription) and (
            "badjwttoken" in detail_lower or vapid_subject(subscription) == "mailto:admin@localhost"
        ):
            return "iPhone/iPad push упёрся в VAPID subject на VPS. Включи HTTPS через enable-https.sh или задай OWRT_REMOTE_PUBLIC_URL=https://твой-домен (или OWRT_REMOTE_VAPID_SUB)."
        return f"VPS не смог отправить тестовый Web Push ({code}). Проверь HTTPS, DNS и install-vps.sh."
    return "VPS не смог подтвердить доставку Web Push."


def queue_web_push_payload(payload, subscriptions=None):
    if subscriptions is None:
        subscriptions = load_push_subscriptions()
    if not subscriptions:
        remember_push_delivery(payload, [], "no_subscriptions")
        return

    def worker():
        gone = []
        results = []
        for subscription in subscriptions:
            result = send_web_push(subscription, payload)
            results.append(
                {
                    "client": subscription.get("client") or "браузер",
                    "endpoint_host": push_endpoint_host(subscription.get("endpoint", "")),
                    "result": result,
                }
            )
            if result == "gone":
                gone.append(subscription.get("endpoint", ""))
        for endpoint in gone:
            remove_push_subscription(endpoint)
        summary = "ok" if results and all(item.get("result") == "ok" for item in results) else "partial"
        remember_push_delivery(payload, results, summary)

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

self.addEventListener('message', event => {
  if (event && event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
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
  const notificationData = data && typeof data.notification === 'object' ? data.notification : null;
  const payload = notificationData ? {
    title: notificationData.title || data.title || 'OpenWrt Remote Hub',
    body: notificationData.body || data.body || '',
    tag: notificationData.tag || data.tag || 'owrt-remote-hub',
    url: notificationData.navigate || data.url || '/',
    badge: notificationData.badge || data.badge || undefined,
    icon: notificationData.icon || data.icon || undefined,
    kind: data.kind || 'info'
  } : data;
  const title = payload.title || 'OpenWrt Remote Hub';
  const options = {
    body: payload.body || '',
    tag: payload.tag || 'owrt-remote-hub',
    renotify: true,
    data: {url: payload.url || '/'},
    badge: payload.badge || undefined,
    icon: payload.icon || undefined
  };
  event.waitUntil((async () => {
    const clients = await self.clients.matchAll({type: 'window', includeUncontrolled: true});
    const hasVisibleClient = clients.some((client) => client && (client.visibilityState === 'visible' || client.focused));
    if (hasVisibleClient && payload.kind !== 'push-test') return;
    await self.registration.showNotification(title, options);
  })());
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
            "id": "/",
            "name": "OpenWrt Remote Hub",
            "short_name": "Wrt Hub",
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "background_color": BRAND_THEME_COLOR,
            "theme_color": BRAND_THEME_COLOR,
            "description": "Удаленный доступ к OpenWrt через свой VPS",
            "icons": [
                {
                    "src": f"/favicon-192.png?v={SERVICE_WORKER_VERSION}",
                    "sizes": "192x192",
                    "type": "image/png",
                    "purpose": "any maskable",
                },
                {
                    "src": f"/favicon-512.png?v={SERVICE_WORKER_VERSION}",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "any maskable",
                },
                {
                    "src": f"/apple-touch-icon.png?v={SERVICE_WORKER_VERSION}",
                    "sizes": "180x180",
                    "type": "image/png",
                    "purpose": "any maskable",
                }
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def branding_head_tags(include_manifest=True):
    parts = [
        f'<link rel="icon" href="/favicon.ico?v={SERVICE_WORKER_VERSION}" sizes="any">',
        f'<link rel="icon" href="/favicon-32.png?v={SERVICE_WORKER_VERSION}" type="image/png" sizes="32x32">',
        f'<link rel="apple-touch-icon" href="/apple-touch-icon.png?v={SERVICE_WORKER_VERSION}" sizes="180x180">',
    ]
    if include_manifest:
        parts.append(f'<link rel="manifest" href="/manifest.webmanifest?v={SERVICE_WORKER_VERSION}">')
    parts.extend(
        [
            f'<meta name="theme-color" content="{BRAND_THEME_COLOR}">',
            '<meta name="mobile-web-app-capable" content="yes">',
            '<meta name="apple-mobile-web-app-capable" content="yes">',
            '<meta name="apple-mobile-web-app-title" content="Wrt Hub">',
        ]
    )
    return "\n".join(parts)


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


def verify_recaptcha_token(secret_key, response_token, remote_ip="", expected_hostname=""):
    secret_key = str(secret_key or "").strip()
    response_token = str(response_token or "").strip()
    if not secret_key or not response_token:
        return False, "Подтверди Google reCAPTCHA"
    payload = {
        "secret": secret_key,
        "response": response_token,
    }
    if remote_ip:
        payload["remoteip"] = str(remote_ip).strip()
    try:
        result = oauth_fetch_json(RECAPTCHA_VERIFY_URL, method="POST", data=payload)
    except Exception as exc:
        return False, f"Не удалось проверить Google reCAPTCHA: {exc}"
    if not result.get("success"):
        errors = result.get("error-codes", [])
        detail = ", ".join(str(item) for item in errors if str(item).strip())
        if "timeout-or-duplicate" in errors:
            return False, "Google reCAPTCHA устарела, попробуй еще раз"
        return False, f"Google reCAPTCHA отклонена{': ' + detail if detail else ''}"
    actual_hostname = str(result.get("hostname") or "").strip().lower()
    expected_hostname = str(expected_hostname or "").strip().lower()
    if expected_hostname and actual_hostname and actual_hostname != expected_hostname:
        return False, f"Google reCAPTCHA выдана для другого хоста: {actual_hostname}"
    return True, ""


def login_recaptcha_script(auth=None):
    state = effective_captcha_state(auth)
    if state.get("effective_mode") != CAPTCHA_MODE_RECAPTCHA:
        return ""
    return '<script src="https://www.google.com/recaptcha/api.js" async defer></script>'


def legacy_login_captcha_html(auth=None):
    state = effective_captcha_state(auth)
    if state.get("effective_mode") == CAPTCHA_MODE_RECAPTCHA:
        safe_site_key = html.escape(state.get("site_key", ""), quote=True)
        return f"""
    <label for="hubRecaptcha">Капча: подтверди вход через Google reCAPTCHA</label>
    <div class="captcha recaptchaWrap" id="hubRecaptcha"><div class="g-recaptcha" data-sitekey="{safe_site_key}" data-theme="dark"></div></div>
    <div class="hint">Google reCAPTCHA проверяется на сервере после отправки формы.</div>"""
    captcha_code, captcha_token = captcha_challenge()
    safe_captcha_token = html.escape(captcha_token, quote=True)
    safe_captcha_code = html.escape(captcha_code, quote=True)
    return f"""
    <label for="hubCaptchaCode">Капча: введи эти цифры</label>
    <div class="captcha" id="hubCaptchaCode" aria-live="polite"><b>{safe_captcha_code}</b></div>
    <input name="captcha_token" type="hidden" value="{safe_captcha_token}">
    <label for="hubCaptcha">Повтори капчу</label>
    <input id="hubCaptcha" name="captcha_answer" inputmode="numeric" pattern="[0-9]*" autocomplete="off" required>"""


def modern_login_captcha_html(auth=None):
    state = effective_captcha_state(auth)
    if state.get("effective_mode") == CAPTCHA_MODE_RECAPTCHA:
        safe_site_key = html.escape(state.get("site_key", ""), quote=True)
        return f"""
                  <div class="captchaSection captchaSectionRecaptcha">
                    <div class="captchaHeading">Капча: подтверди, что ты не робот</div>
                    <div class="recaptchaBox"><div class="g-recaptcha" data-sitekey="{safe_site_key}" data-theme="dark"></div></div>
                  </div>"""
    captcha_code, captcha_token = captcha_challenge()
    safe_captcha_token = html.escape(captcha_token, quote=True)
    safe_captcha_code = html.escape(captcha_code, quote=True)
    return f"""
                  <div class="captchaSection">
                    <div class="captchaHeading">Капча: введи эти цифры</div>
                    <div class="captchaTopRow">
                      <button class="captchaRefresh" id="captchaRefreshBtn" type="button" aria-label="Обновить капчу">
                        <svg viewBox="0 0 24 24" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                          <path d="M20 11a8 8 0 1 0 2.2 5.5"></path>
                          <path d="M20 4v7h-7"></path>
                        </svg>
                      </button>
                      <div class="captcha" id="hubCaptchaCode" aria-live="polite"><b>{safe_captcha_code}</b></div>
                    </div>
                    <div class="inputShell captchaAnswerShell">
                      <input id="hubCaptcha" name="captcha_answer" inputmode="numeric" pattern="[0-9]*" autocomplete="off" placeholder="Повтори капчу" required>
                      <span class="captchaInputMark" aria-hidden="true">
                        <svg viewBox="0 0 24 24" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                          <path d="M7 12.5 10.5 16 17 9"></path>
                        </svg>
                      </span>
                    </div>
                  </div>
                  <input name="captcha_token" type="hidden" value="{safe_captcha_token}">"""


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


ROUTER_NOTE_TEXT_MAX_CHARS = 2000
ROUTER_NOTES_MAX_ENTRIES = 200
ROUTER_NOTES_STORAGE_VERSION = 1
ROUTER_NOTES_MEDIA_DIR = STATE_DIR / "router-note-images"
ROUTER_NOTE_IMAGE_MAX_FILES = 8
ROUTER_NOTE_IMAGE_MAX_BYTES = 8 * 1024 * 1024
ROUTER_NOTE_IMAGE_TOTAL_MAX_BYTES = 24 * 1024 * 1024
ROUTER_NOTE_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
ROUTER_NOTE_IMAGE_EXTENSIONS = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def clean_router_notes(value):
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines).strip()
    return text[:ROUTER_NOTE_TEXT_MAX_CHARS]


def clean_router_note_image_name(value):
    text = Path(str(value or "").strip()).name
    text = re.sub(r"\s+", " ", text).strip(" .")
    return (text or "image")[:120]


def clean_router_note_image_item(item):
    if not isinstance(item, dict):
        return {}
    file_name = Path(str(item.get("file_name") or "")).name
    if not file_name:
        return {}
    ext = Path(file_name).suffix.lower()
    content_type = str(item.get("content_type") or "").split(";", 1)[0].strip().lower()
    if not content_type and ext in ROUTER_NOTE_IMAGE_EXTENSIONS:
        content_type = ROUTER_NOTE_IMAGE_EXTENSIONS[ext]
    if content_type not in ROUTER_NOTE_IMAGE_TYPES:
        return {}
    expected_ext = ROUTER_NOTE_IMAGE_TYPES[content_type]
    stem = Path(file_name).stem or secrets.token_hex(6)
    file_name = f"{stem}{expected_ext}"
    size = 0
    try:
        size = max(0, int(item.get("size") or 0))
    except (TypeError, ValueError):
        size = 0
    return {
        "id": str(item.get("id") or stem)[:32],
        "file_name": file_name,
        "content_type": content_type,
        "name": clean_router_note_image_name(item.get("name") or file_name),
        "size": size,
    }


def clean_router_note_images(items):
    clean_items = []
    for item in list(items or [])[:ROUTER_NOTE_IMAGE_MAX_FILES]:
        clean_item = clean_router_note_image_item(item)
        if clean_item:
            clean_items.append(clean_item)
    return clean_items


def router_note_media_dir(router_id, note_id=""):
    base = ROUTER_NOTES_MEDIA_DIR / clean_router_id(router_id)
    note_name = str(note_id or "").strip()
    if note_name:
        return base / note_name[:64]
    return base


def router_note_image_path(router_id, note_id, file_name):
    return router_note_media_dir(router_id, note_id) / Path(str(file_name or "")).name


def router_note_image_url(router_id, note_id, file_name):
    return (
        f"/router/{urllib.parse.quote(clean_router_id(router_id))}"
        f"/notes-media/{urllib.parse.quote(str(note_id or '')[:64])}"
        f"/{urllib.parse.quote(Path(str(file_name or '')).name)}"
    )


def save_router_note_images(router_id, note_id, uploads):
    uploads = list(uploads or [])
    if len(uploads) > ROUTER_NOTE_IMAGE_MAX_FILES:
        raise ValueError(f"можно прикрепить не больше {ROUTER_NOTE_IMAGE_MAX_FILES} изображений")
    total_size = 0
    saved = []
    target_dir = router_note_media_dir(router_id, note_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    for upload in uploads:
        raw = bytes((upload or {}).get("body") or b"")
        if not raw:
            continue
        total_size += len(raw)
        if len(raw) > ROUTER_NOTE_IMAGE_MAX_BYTES:
            raise ValueError("одно изображение слишком большое")
        if total_size > ROUTER_NOTE_IMAGE_TOTAL_MAX_BYTES:
            raise ValueError("общий размер изображений слишком большой")
        content_type = str((upload or {}).get("content_type") or "").split(";", 1)[0].strip().lower()
        filename = str((upload or {}).get("filename") or "")
        if content_type not in ROUTER_NOTE_IMAGE_TYPES:
            content_type = ROUTER_NOTE_IMAGE_EXTENSIONS.get(Path(filename).suffix.lower(), "")
        if content_type not in ROUTER_NOTE_IMAGE_TYPES:
            raise ValueError("поддерживаются только JPG, PNG, WEBP и GIF")
        image_id = secrets.token_hex(6)
        file_name = f"{image_id}{ROUTER_NOTE_IMAGE_TYPES[content_type]}"
        write_private_bytes(target_dir / file_name, raw)
        saved.append(
            {
                "id": image_id,
                "file_name": file_name,
                "content_type": content_type,
                "name": clean_router_note_image_name(filename or file_name),
                "size": len(raw),
            }
        )
    return saved


def delete_router_note_images(router_id, note_id):
    target = router_note_media_dir(router_id, note_id)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)


def payload_files(payload, field_name):
    files = (payload or {}).get("__files__", {})
    if not isinstance(files, dict):
        return []
    items = files.get(field_name, [])
    return list(items) if isinstance(items, list) else []


def build_router_note_entry(text, created_at=0, note_id="", images=None):
    clean_text = clean_router_notes(text)
    clean_images = clean_router_note_images(images)
    if not clean_text and not clean_images:
        return {}
    created = int(created_at or now_ts())
    entry = {
        "id": str(note_id or secrets.token_hex(6))[:32],
        "text": clean_text,
        "created_at": created,
    }
    if clean_images:
        entry["images"] = clean_images
    return entry


def parse_router_notes_entries(raw_value, fallback_ts=0):
    raw_text = str(raw_value or "").strip()
    if not raw_text:
        return []
    payload = None
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        payload = None
    entries = []
    if isinstance(payload, dict):
        payload_entries = payload.get("entries")
        if isinstance(payload_entries, list):
            for item in payload_entries:
                if not isinstance(item, dict):
                    continue
                entry = build_router_note_entry(
                    item.get("text", ""),
                    created_at=item.get("created_at") or fallback_ts or now_ts(),
                    note_id=item.get("id", ""),
                    images=item.get("images") or [],
                )
                if entry:
                    entries.append(entry)
            return entries[:ROUTER_NOTES_MAX_ENTRIES]
    elif isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            entry = build_router_note_entry(
                item.get("text", ""),
                created_at=item.get("created_at") or fallback_ts or now_ts(),
                note_id=item.get("id", ""),
                images=item.get("images") or [],
            )
            if entry:
                entries.append(entry)
        return entries[:ROUTER_NOTES_MAX_ENTRIES]
    legacy_entry = build_router_note_entry(raw_text, created_at=fallback_ts or now_ts())
    return [legacy_entry] if legacy_entry else []


def encode_router_notes_entries(entries):
    clean_entries = []
    for item in list(entries or [])[:ROUTER_NOTES_MAX_ENTRIES]:
        if not isinstance(item, dict):
            continue
        entry = build_router_note_entry(
            item.get("text", ""),
            created_at=item.get("created_at") or now_ts(),
            note_id=item.get("id", ""),
            images=item.get("images") or [],
        )
        if entry:
            clean_entries.append(entry)
    return json.dumps(
        {
            "version": ROUTER_NOTES_STORAGE_VERSION,
            "entries": clean_entries,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


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
            notes text not null default '',
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
    ensure_column(conn, "routers", "notes", "text not null default ''")
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
    note_entries = parse_router_notes_entries(data.get("notes") or "", fallback_ts=data.get("updated_at") or data.get("created_at") or 0)
    latest_entry = note_entries[-1] if note_entries else {}
    latest_images = list(latest_entry.get("images") or [])
    latest_note = latest_entry.get("text") or (f"Фото: {len(latest_images)}" if latest_images else "")
    data["notes_entries"] = [
        {
            **entry,
            "images": [
                {
                    **image,
                    "url": router_note_image_url(data["id"], entry.get("id") or "", image.get("file_name") or ""),
                }
                for image in list(entry.get("images") or [])
            ],
            "created_at_iso": iso_time(entry.get("created_at") or 0),
            "created_at_label": format_router_note_time(entry.get("created_at") or 0),
        }
        for entry in note_entries
    ]
    data["notes"] = latest_note
    data["has_notes"] = bool(note_entries)
    data["notes_count"] = len(note_entries)
    data["notes_preview"] = " ".join(latest_note.split())[:220]
    custom_name = str(data.get("custom_name") or "").strip()
    if custom_name:
        data["name"] = custom_name
    last_seen = data.get("last_seen")
    service_enabled = str(status.get("service") or "").lower() != "disabled"
    online = bool(last_seen and now_ts() - int(last_seen) <= ONLINE_AFTER_SECONDS)
    data["status"] = status
    data["service_enabled"] = service_enabled
    data["online"] = online
    data["last_seen_iso"] = iso_time(last_seen)
    data["access_url"] = f"/access/{urllib.parse.quote(data['id'])}/"
    data["ssh_url"] = f"/ssh/{urllib.parse.quote(data['id'])}/"
    data["config_url"] = f"/router/{urllib.parse.quote(data['id'])}/config"
    data["notes_url"] = f"/router/{urllib.parse.quote(data['id'])}/notes"
    data["xray_client_url"] = f"/router/{urllib.parse.quote(data['id'])}/xray-client.json"
    data.pop("status_json", None)
    return data


def router_with_access(router, account):
    router = dict(router or {})
    router_id = str(router.get("id") or "")
    can_admin = account_has_router_flag(account, router_id, "A")
    can_ssh = account_has_router_flag(account, router_id, "B")
    can_manage = account_has_router_flag(account, router_id, "G")
    router["access_flags"] = sorted(router_flags_for_account(account, router_id))
    router["permissions"] = {
        "owner": bool(account and account.get("is_owner")),
        "admin": bool(can_admin),
        "ssh": bool(can_ssh),
        "manage": bool(can_manage),
        "notes": bool(can_admin),
        "notes_edit": bool(can_manage),
        "notes_delete": bool(account and account.get("is_owner")),
        "config": bool(can_manage),
        "wol": bool(can_manage),
        "traffic": bool(can_manage),
        "delete": bool(account and account.get("is_owner")),
        "diagnose": bool(can_manage),
    }
    return router


def delegated_auth_meta(auth=None, account=None, router_rows=None):
    auth = normalize_auth_state(auth or load_auth())
    account = account or account_profile(auth, "")
    router_rows = list(router_rows or [])
    router_name_map = {}
    for row in router_rows:
        router = row_to_router(row)
        router_name_map[str(router.get("id") or "")] = str(router.get("name") or router.get("id") or "")
    assigned = []
    for router_id in sorted((account or {}).get("router_ids", []), key=str.lower):
        flags = sorted(router_flags_for_account(account, router_id))
        if not flags:
            continue
        assigned.append(
            {
                "id": router_id,
                "name": router_name_map.get(router_id, router_id),
                "flags": flags,
                "flag_label": "".join(flag for flag in ROUTER_ACCESS_FLAGS if flag in flags) or "-",
            }
        )
    return {
        "username": (account or {}).get("username") or auth.get("username", "admin"),
        "is_owner": False,
        "can_add_router": bool(account_can_add_router(account)),
        "current_user": {
            "username": (account or {}).get("username") or auth.get("username", "admin"),
            "is_owner": False,
        },
        "assigned_routers": assigned,
        "router_access": {item.get("id", ""): item.get("flags", []) for item in assigned},
        "passkeys_supported": passkey_supported(),
    }


def router_notification_label(router):
    router_id = str((router or {}).get("id") or "").strip()
    name = str((router or {}).get("name") or router_id or "router").strip()
    if router_id and name and name != router_id:
        return f"{name} ({router_id})"
    return name or router_id or "router"


def router_notification_details(router):
    details = []
    if not isinstance(router, dict):
        return details
    last_seen_iso = str(router.get("last_seen_iso") or "").strip()
    if last_seen_iso:
        details.append(f"Последний heartbeat: {last_seen_iso}")
    status = router.get("status") if isinstance(router.get("status"), dict) else {}
    wan_ip = str(status.get("wan_ip") or status.get("ip") or "").strip()
    if wan_ip:
        details.append(f"WAN IP: {wan_ip}")
    release = str(status.get("release") or "").strip()
    if release:
        details.append(f"Система: {release}")
    return details[:6]


def notify_router_online(router, dedupe_seconds=45):
    label = router_notification_label(router)
    add_notification(
        "router_online",
        "Роутер снова в сети",
        f"{label} снова выходит на связь с Hub.",
        "info",
        router_notification_details(router),
        {"router_id": str((router or {}).get("id") or ""), "online": True},
        dedupe_seconds=dedupe_seconds,
    )


def notify_router_offline(router, dedupe_seconds=0):
    label = router_notification_label(router)
    grace = max(ONLINE_AFTER_SECONDS, 1)
    add_notification(
        "router_offline",
        "Роутер пропал из сети",
        f"{label} не присылает heartbeat дольше {grace} сек.",
        "bad",
        router_notification_details(router),
        {"router_id": str((router or {}).get("id") or ""), "online": False},
        dedupe_seconds=dedupe_seconds or max(45, grace // 2),
    )


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


def rewrite_router_endpoints(conn, vps_host="", public_url="", preserve_vps_fallback=False):
    rewritten = {}
    next_vps_host = str(vps_host or "").strip()
    next_public_url = str(public_url or "").strip()
    ts = now_ts()
    keep_vps_host = preserve_vps_fallback and should_preserve_router_vps_fallback(next_vps_host, next_public_url)
    if next_vps_host and not keep_vps_host:
        conn.execute(
            "update routers set vps_host = ?, updated_at = ? where deleted_at = 0",
            (next_vps_host, ts),
        )
        rewritten["vps_host"] = next_vps_host
    if next_public_url:
        conn.execute(
            "update routers set public_url = ?, updated_at = ? where deleted_at = 0",
            (next_public_url, ts),
        )
        rewritten["public_url"] = next_public_url
    if rewritten:
        conn.commit()
    return rewritten


def router_current_hub_bundle(router, app_public_url="", request_origin="", fallback_host=""):
    row = dict(router or {})
    router_public_url = public_url_origin(row.get("public_url", ""))
    app_origin = public_url_origin(app_public_url)
    request_origin = public_url_origin(request_origin)
    public_url = prefer_origin_with_explicit_port(router_public_url, app_origin)
    public_url = prefer_origin_with_explicit_port(public_url, request_origin)
    hub_url = public_url or request_origin or router_fallback_hub_url(row, fallback_host, 8088)
    config_vps_host = (
        vps_host_name(public_url or hub_url)
        or str(row.get("vps_host") or "").strip()
        or vps_host_name(app_origin or request_origin)
        or vps_host_name(router_fallback_hub_url(row, fallback_host, 8088))
        or ""
    )
    hub_update = {
        "hub_url": hub_url,
        "public_url": public_url,
        "vps_host": config_vps_host,
    }
    return hub_update, hub_url, config_vps_host


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
        rewritten = rewrite_router_endpoints(conn, vps_host=vps_host, public_url=public_url)
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
    if "notes" in values:
        incoming_notes = values.get("notes", "")
        if isinstance(incoming_notes, list):
            notes = encode_router_notes_entries(incoming_notes)
        else:
            notes = encode_router_notes_entries(parse_router_notes_entries(incoming_notes, fallback_ts=ts))
    elif current:
        notes = str(current["notes"] or "")
    else:
        notes = encode_router_notes_entries([])

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
        "notes": notes,
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
                notes = :notes,
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
                ssh_reverse_tag, ssh_host, ssh_port, notes,
                created_at, updated_at, deleted_at
            ) values (
                :id, :name, :role, :entry_port, :vps_host, :vless_port, :vless_uuid,
                :vless_encryption, :vless_decryption, :vless_flow, :reverse_tag,
                :public_url, :admin_host, :admin_port, :ssh_entry_port, :ssh_vless_uuid,
                :ssh_reverse_tag, :ssh_host, :ssh_port, :notes,
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
    known_before = bool(row and not was_deleted)
    was_online = bool(row_to_router(row).get("online")) if known_before else False
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
    router["_became_online"] = bool(known_before and not was_online and router.get("online"))
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


def update_router_notes(conn, router_id, notes):
    router_id = clean_router_id(router_id)
    row = get_router(conn, router_id)
    if not row or router_deleted(row):
        raise ValueError("router not found")
    clean_notes = clean_router_notes(notes)
    stored = encode_router_notes_entries([build_router_note_entry(clean_notes, created_at=now_ts())] if clean_notes else [])
    conn.execute(
        "update routers set notes = ?, updated_at = ? where id = ?",
        (stored, now_ts(), router_id),
    )
    conn.commit()
    return get_router(conn, router_id)


def append_router_note(conn, router_id, note_text, image_uploads=None):
    router_id = clean_router_id(router_id)
    row = get_router(conn, router_id)
    if not row or router_deleted(row):
        raise ValueError("router not found")
    note_id = secrets.token_hex(6)
    saved_images = save_router_note_images(router_id, note_id, image_uploads)
    entry = build_router_note_entry(note_text, created_at=now_ts(), note_id=note_id, images=saved_images)
    if not entry:
        if saved_images:
            delete_router_note_images(router_id, note_id)
        raise ValueError("note is empty")
    try:
        entries = parse_router_notes_entries(row["notes"] or "", fallback_ts=row["updated_at"] or row["created_at"] or 0)
        entries.append(entry)
        entries = entries[-ROUTER_NOTES_MAX_ENTRIES:]
        conn.execute(
            "update routers set notes = ?, updated_at = ? where id = ?",
            (encode_router_notes_entries(entries), now_ts(), router_id),
        )
        conn.commit()
    except Exception:
        if saved_images:
            delete_router_note_images(router_id, note_id)
        raise
    return get_router(conn, router_id)


def update_router_note_entry(conn, router_id, note_id, note_text):
    router_id = clean_router_id(router_id)
    note_id = str(note_id or "").strip()
    if not note_id:
        raise ValueError("note id is empty")
    row = get_router(conn, router_id)
    if not row or router_deleted(row):
        raise ValueError("router not found")
    clean_text = clean_router_notes(note_text)
    entries = parse_router_notes_entries(row["notes"] or "", fallback_ts=row["updated_at"] or row["created_at"] or 0)
    updated = False
    for item in entries:
        if str(item.get("id") or "") != note_id:
            continue
        if not clean_text and not list(item.get("images") or []):
            raise ValueError("note is empty")
        item["text"] = clean_text
        updated = True
        break
    if not updated:
        raise ValueError("note not found")
    conn.execute(
        "update routers set notes = ?, updated_at = ? where id = ?",
        (encode_router_notes_entries(entries), now_ts(), router_id),
    )
    conn.commit()
    return get_router(conn, router_id)


def delete_router_note_entry(conn, router_id, note_id):
    router_id = clean_router_id(router_id)
    note_id = str(note_id or "").strip()
    if not note_id:
        raise ValueError("note id is empty")
    row = get_router(conn, router_id)
    if not row or router_deleted(row):
        raise ValueError("router not found")
    entries = parse_router_notes_entries(row["notes"] or "", fallback_ts=row["updated_at"] or row["created_at"] or 0)
    filtered = [item for item in entries if str(item.get("id") or "") != note_id]
    if len(filtered) == len(entries):
        raise ValueError("note not found")
    conn.execute(
        "update routers set notes = ?, updated_at = ? where id = ?",
        (encode_router_notes_entries(filtered), now_ts(), router_id),
    )
    conn.commit()
    delete_router_note_images(router_id, note_id)
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


def build_openwrt_config_payload(row, hub_url, vps_host_override="", public_url_override=""):
    reverse_tag = row_str_value(row, "reverse_tag", "reverse-in")
    ssh_reverse_tag = row_str_value(row, "ssh_reverse_tag", "") or f"{reverse_tag}-ssh"
    ssh_host = row_str_value(row, "ssh_host", "127.0.0.1")
    admin_host = row_str_value(row, "admin_host", "127.0.0.1")
    effective_public_url = str(public_url_override or row_str_value(row, "public_url", "")).strip()
    effective_vps_host = str(vps_host_override or row_str_value(row, "vps_host", "")).strip()
    if not effective_vps_host:
        effective_vps_host = vps_host_name(effective_public_url) or vps_host_name(hub_url) or "127.0.0.1"
    payload = {
        "id": row_str_value(row, "id"),
        "name": row_str_value(row, "name"),
        "role": row_str_value(row, "role", "node"),
        "hub_url": str(hub_url or ""),
        "vps_host": effective_vps_host,
        "vps_port": row_int_value(row, "vless_port", DEFAULT_VLESS_PORT),
        "vless_uuid": row_str_value(row, "vless_uuid"),
        "vless_encryption": row_str_value(row, "vless_encryption", "none"),
        "vless_flow": row_str_value(row, "vless_flow", ""),
        "reverse_tag": reverse_tag,
        "ssh_vless_uuid": row_str_value(row, "ssh_vless_uuid"),
        "ssh_reverse_tag": ssh_reverse_tag,
        "admin_host": admin_host,
        "admin_port": row_int_value(row, "admin_port", 80),
        "ssh_host": ssh_host,
        "ssh_port": normalize_internal_ssh_port(
            row_int_value(row, "ssh_port", 22),
            row_int_value(row, "entry_port", 0),
            row_int_value(row, "ssh_entry_port", 0),
        ),
        "public_url": effective_public_url,
    }
    return payload


def build_openwrt_config_lines(payload, mode="manual"):
    lines = [
        "uci -q delete owrtremote.main",
        "uci set owrtremote.main=remote",
        "uci set owrtremote.main.enabled='1'",
        f"uci set owrtremote.main.router_id='{sh_quote(payload['id'])}'",
        f"uci set owrtremote.main.router_name='{sh_quote(payload['name'])}'",
        f"uci set owrtremote.main.role='{sh_quote(payload['role'])}'",
        f"uci set owrtremote.main.hub_url='{sh_quote(payload['hub_url'])}'",
        f"uci set owrtremote.main.hub_token='{sh_quote(agent_token())}'",
        "uci set owrtremote.main.heartbeat_interval='30'",
        "uci set owrtremote.main.xray_bin='/usr/bin/xray'",
        "uci set owrtremote.main.xray_config='/etc/xray/owrt-remote-client.json'",
        f"uci set owrtremote.main.vps_host='{sh_quote(payload['vps_host'])}'",
        f"uci set owrtremote.main.vps_port='{payload['vps_port']}'",
        f"uci set owrtremote.main.vless_uuid='{sh_quote(payload['vless_uuid'])}'",
        f"uci set owrtremote.main.vless_encryption='{sh_quote(payload['vless_encryption'])}'",
        f"uci set owrtremote.main.vless_flow='{sh_quote(payload['vless_flow'])}'",
        f"uci set owrtremote.main.reverse_tag='{sh_quote(payload['reverse_tag'])}'",
        f"uci set owrtremote.main.ssh_vless_uuid='{sh_quote(payload['ssh_vless_uuid'])}'",
        f"uci set owrtremote.main.ssh_reverse_tag='{sh_quote(payload['ssh_reverse_tag'])}'",
        f"uci set owrtremote.main.admin_host='{sh_quote(payload['admin_host'])}'",
        f"uci set owrtremote.main.admin_port='{payload['admin_port']}'",
        f"uci set owrtremote.main.ssh_host='{sh_quote(payload['ssh_host'])}'",
        f"uci set owrtremote.main.ssh_port='{payload['ssh_port']}'",
        f"uci set owrtremote.main.public_url='{sh_quote(payload['public_url'])}'",
        "uci commit owrtremote",
        "owrt-remote render-client",
        "/etc/init.d/owrt-remote enable",
    ]
    if mode == "ssh-apply":
        lines.extend(
            [
                "owrt-remote heartbeat rebind >/dev/null 2>&1 || true",
                "( sleep 1; /etc/init.d/owrt-remote restart >/dev/null 2>&1 ) >/dev/null 2>&1 &",
                "echo 'owrt-remote rebind applied'",
            ]
        )
    else:
        lines.extend(
            [
                "/etc/init.d/owrt-remote restart",
                "owrt-remote heartbeat",
            ]
        )
    return lines


def make_openwrt_config(row, hub_url, vps_host_override="", public_url_override=""):
    payload = build_openwrt_config_payload(
        row,
        hub_url,
        vps_host_override=vps_host_override,
        public_url_override=public_url_override,
    )
    lines = build_openwrt_config_lines(payload, mode="manual")
    return "\n".join(lines) + "\n"


def make_openwrt_ssh_apply_script(row, hub_url, vps_host_override="", public_url_override=""):
    payload = build_openwrt_config_payload(
        row,
        hub_url,
        vps_host_override=vps_host_override,
        public_url_override=public_url_override,
    )
    lines = ["set -eu"] + build_openwrt_config_lines(payload, mode="ssh-apply")
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


def dashboard_html(routers, username, sessions=None, notifications=None, auth_bootstrap=None):
    routers_json = json.dumps(routers, ensure_ascii=False)
    sessions_json = json.dumps(sessions or [], ensure_ascii=False)
    notifications_json = json.dumps(notifications or [], ensure_ascii=False)
    auth_bootstrap_json = json.dumps(auth_bootstrap or {}, ensure_ascii=False)
    push_bootstrap = web_push_backend_status()
    push_bootstrap_json = json.dumps(
        {
            "ready": bool(push_bootstrap.get("ready", False)),
            "publicKey": str(push_bootstrap.get("public_key", "") or ""),
            "subject": str(push_bootstrap.get("subject", "") or ""),
        },
        ensure_ascii=False,
    )
    is_owner = bool((auth_bootstrap or {}).get("is_owner"))
    safe_username = html.escape(username, quote=True)
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{branding_head_tags()}
<title>{APP_NAME}</title>
<style>
:root{{color-scheme:dark;--bg:#07040f;--panel:rgba(19,14,32,.88);--panel2:rgba(255,255,255,.07);--text:#f7f2ff;--muted:#b9adc9;--line:rgba(169,126,255,.25);--blue:#7c3aed;--green:#22c55e;--red:#fb7185;--amber:#f59e0b;--cyan:#22d3ee;--teal:#a855f7;--grid:rgba(168,85,247,.14)}}
*{{box-sizing:border-box}}body{{position:relative;min-height:100vh;margin:0;overflow-x:hidden;background-color:var(--bg);background-image:radial-gradient(circle at 12% 8%,rgba(168,85,247,.46),transparent 31%),radial-gradient(circle at 82% 12%,rgba(79,70,229,.38),transparent 30%),radial-gradient(circle at 50% 105%,rgba(236,72,153,.26),transparent 35%),linear-gradient(145deg,#07040f,#120a24 48%,#05030a),repeating-linear-gradient(0deg,transparent 0 30px,var(--grid) 31px),repeating-linear-gradient(90deg,transparent 0 30px,var(--grid) 31px);background-size:130% 130%,140% 140%,135% 135%,100% 100%,31px 31px,31px 31px;background-attachment:fixed;color:var(--text);font:14px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;animation:bgFlow 28s ease-in-out infinite alternate}}
body::before{{content:"";position:fixed;inset:-25%;z-index:0;pointer-events:none;background:conic-gradient(from 0deg at 50% 50%,rgba(168,85,247,.05),rgba(236,72,153,.34),rgba(59,130,246,.22),rgba(245,158,11,.13),rgba(168,85,247,.05));filter:blur(54px);opacity:.7;animation:auraSpin 38s linear infinite}}
@keyframes bgFlow{{0%{{background-position:0% 0%,100% 0%,50% 100%,0 0,0 0,0 0}}50%{{background-position:28% 18%,62% 26%,38% 82%,0 0,15px 24px,24px 15px}}100%{{background-position:48% 28%,42% 42%,74% 62%,0 0,30px 0,0 30px}}}}
@keyframes auraSpin{{from{{transform:rotate(0deg) scale(1)}}to{{transform:rotate(360deg) scale(1.08)}}}}
.wrap{{position:relative;z-index:1;max-width:1220px;margin:0 auto;padding:22px}}.top{{display:grid;align-items:flex-start;justify-items:start;gap:8px;border-bottom:1px solid var(--line);padding:20px 0 18px}}
.brand{{display:flex;align-items:center;gap:14px;width:100%;min-width:0}}
h1{{margin:0;font-size:29px;line-height:1.2;letter-spacing:0}}.appBanner{{position:relative;display:inline-flex;align-items:center;justify-content:center;gap:8px;min-height:36px;min-width:132px;padding:8px 14px;border:1px solid rgba(34,211,238,.38);border-radius:999px;background:linear-gradient(110deg,rgba(34,211,238,.14),rgba(124,58,237,.24),rgba(236,72,153,.14));color:#f3e8ff;text-decoration:none;font-weight:800;font-size:13px;line-height:1;white-space:nowrap;box-shadow:0 10px 24px rgba(124,58,237,.16),inset 0 1px 0 rgba(255,255,255,.10);overflow:hidden}}.appBanner::before{{content:"";position:absolute;inset:0;background:linear-gradient(90deg,transparent,rgba(255,255,255,.20),transparent);transform:translateX(-120%);animation:bannerShine 6.2s ease-in-out infinite}}.appBanner span{{position:relative}}.appBannerVersion{{color:#fb7185;text-shadow:0 0 12px rgba(251,113,133,.35)}}.muted{{color:var(--muted)}}.top p{{margin:4px 0 0}}.links,.headerActions{{display:flex;align-items:center;gap:8px}}.links{{margin-top:0;flex-wrap:nowrap}}.links a,.badge{{position:relative;display:inline-flex;align-items:center;justify-content:center;gap:8px;min-height:36px;min-width:132px;padding:8px 14px;border:1px solid var(--line);border-radius:999px;background:rgba(255,255,255,.08);color:#f3e8ff;text-decoration:none;font-weight:800;font-size:13px;line-height:1;white-space:nowrap;overflow:hidden}}.headerActions{{position:relative;display:grid;grid-template-columns:repeat(5,minmax(0,1fr));align-self:flex-start;justify-content:flex-start;align-content:flex-start;flex:1 1 auto;min-width:0;gap:8px;padding-top:0;max-width:none}}.headerActions .badge,.headerActions .btn{{width:100%;min-height:36px;min-width:0;padding:8px 10px;border-radius:999px;font-weight:800;font-size:12px;line-height:1;white-space:nowrap}}.headerActions .btn[href="/logout"]{{margin-left:0}}.badge{{background:rgba(255,255,255,.08);color:#f3e8ff;box-shadow:inset 0 1px 0 rgba(255,255,255,.06)}}.nethavenTop{{border-color:rgba(34,211,238,.46);background:linear-gradient(110deg,rgba(14,165,233,.20),rgba(168,85,247,.22),rgba(34,197,94,.14));color:#ecfeff;box-shadow:0 10px 24px rgba(14,165,233,.14),inset 0 1px 0 rgba(255,255,255,.10)}}.nethavenTop::before{{content:"";position:absolute;inset:0;background:linear-gradient(90deg,transparent,rgba(255,255,255,.24),transparent);transform:translateX(-120%);animation:bannerShine 6.2s ease-in-out infinite;pointer-events:none}}.authToggle{{cursor:pointer}}.dot{{width:9px;height:9px;border-radius:999px;background:var(--red);box-shadow:0 0 13px rgba(251,113,133,.72)}}.dot.on{{background:var(--green);box-shadow:0 0 13px rgba(34,197,94,.75)}}.dot.warn{{background:var(--amber);box-shadow:0 0 13px rgba(245,158,11,.75)}}
 .toolbar{{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1.25fr) minmax(88px,.46fr) minmax(92px,.48fr) minmax(0,1.1fr) minmax(116px,.58fr);gap:8px;margin:18px 0;padding:14px 16px;background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.045)),var(--panel);border:1px solid var(--line);border-radius:8px;box-shadow:0 18px 46px rgba(0,0,0,.20);backdrop-filter:blur(10px);width:100%;max-width:100%;box-sizing:border-box}}.toolbar>*{{width:100%;min-width:0}}.toolbar input,.toolbar select{{background:rgba(255,255,255,.08);border-color:var(--line);color:#f3e8ff;box-shadow:inset 0 1px 0 rgba(255,255,255,.05)}}.toolbar input::placeholder{{color:#b9adc9}}
.authMenu{{position:absolute;right:0;top:calc(100% + 10px);z-index:60;width:min(640px,calc(100vw - 44px));max-height:min(820px,calc(100svh - 120px));overflow:auto;padding:16px;background:linear-gradient(180deg,rgba(255,255,255,.09),rgba(255,255,255,.05)),rgba(19,14,32,.96);border:1px solid var(--line);border-radius:8px;box-shadow:0 24px 70px rgba(0,0,0,.36);backdrop-filter:blur(12px);scrollbar-width:thin}}.authMenu[hidden]{{display:none}}.authMenu>h2,.authMenu>p{{display:none}}.authMenuHead{{position:relative;display:block}}.authMenuHead>div{{min-width:0}}.authMenuHead p{{display:none}}.authMenu h2{{margin:0 0 4px;font-size:18px}}.authMenuClose{{display:none;position:absolute;top:14px;right:14px;min-height:34px;padding:7px 12px;border-radius:999px;white-space:nowrap}}.authMenu p{{margin:0 0 12px;color:var(--muted)}}.authGrid{{display:grid;grid-template-columns:1fr;gap:10px}}.authGrid .wide{{grid-column:1/-1}}.msg{{margin-top:10px;color:#bbf7d0;font-weight:750}}.msg.bad{{color:#fecdd3}}.formMsg{{margin:-8px 0 18px;padding:10px 12px;border:1px solid rgba(34,197,94,.34);border-radius:8px;background:rgba(34,197,94,.12);color:#bbf7d0;font-weight:800}}.formMsg.bad{{border-color:rgba(251,113,133,.4);background:rgba(251,113,133,.13);color:#fecdd3}}
.authGroup{{margin-top:14px;border:1px solid rgba(34,211,238,.22);border-radius:14px;background:linear-gradient(180deg,rgba(255,255,255,.06),rgba(255,255,255,.03)),rgba(15,10,26,.78);box-shadow:inset 0 1px 0 rgba(255,255,255,.04)}}.authGroupSummary{{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 14px;cursor:pointer;list-style:none}}.authGroupSummary::-webkit-details-marker{{display:none}}.authGroupTitle strong{{display:block;color:#f7f2ff;font-size:15px;font-weight:900;line-height:1.15}}.authGroupTitle span{{display:block;margin-top:4px;color:var(--muted);font-size:12px;line-height:1.35}}.authGroupChevron{{display:inline-flex;align-items:center;justify-content:center;flex:0 0 28px;width:28px;height:28px;border:1px solid rgba(34,211,238,.24);border-radius:999px;background:rgba(34,211,238,.08);color:#dbeafe;font-size:16px;line-height:1;transition:transform .18s ease,background .18s ease,border-color .18s ease}}.authGroup[open] .authGroupChevron{{transform:rotate(180deg);background:rgba(34,197,94,.12);border-color:rgba(34,197,94,.24)}}.authGroupBody{{padding:0 14px 14px;border-top:1px solid rgba(255,255,255,.08)}}.authGroupBody>.authPasswordSection{{margin-top:14px;padding-top:0;border-top:0}}.authGroupBody>.authOverview{{margin-top:0;padding-top:14px;border-top:0}}.authGroupBody>.sessionBox{{margin-top:14px;padding-top:0;border-top:0}}.authGroupBody>.notifyBox{{margin-top:14px;padding-top:0;border-top:0}}.authGroupBody>.backupBox{{margin-top:14px;padding-top:0;border-top:0}}.authOverview{{display:grid;gap:10px;margin-top:14px;padding-top:12px;border-top:1px solid var(--line)}}.authPills{{display:flex;flex-wrap:wrap;gap:8px}}.authPill{{display:inline-flex;align-items:center;gap:7px;padding:7px 10px;border:1px solid rgba(34,211,238,.24);border-radius:999px;background:rgba(34,211,238,.08);color:#dbeafe;font-size:12px;font-weight:850}}.authPill.off{{border-color:rgba(255,255,255,.10);background:rgba(255,255,255,.06);color:#c4b5fd}}.authSection{{margin-top:14px;padding-top:12px;border-top:1px solid var(--line)}}.authSectionHead{{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;margin-bottom:8px}}.authSectionHead h3{{margin:0;font-size:15px}}.authSectionHead p{{margin:4px 0 0;color:var(--muted);font-size:12px;line-height:1.35}}.authSectionState{{display:inline-flex;align-items:center;justify-content:center;min-height:30px;padding:6px 10px;border:1px solid rgba(34,197,94,.30);border-radius:999px;background:rgba(34,197,94,.12);color:#bbf7d0;font-size:12px;font-weight:850;white-space:nowrap}}.authSectionState.off{{border-color:rgba(255,255,255,.10);background:rgba(255,255,255,.06);color:#c4b5fd}}.authSectionState.warn{{border-color:rgba(251,191,36,.28);background:rgba(251,191,36,.11);color:#fde68a}}.authFields{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}.authFields .wide{{grid-column:1/-1}}.authFields input,.authFields textarea{{width:100%;min-width:0}}.authFields textarea{{min-height:92px;resize:vertical}}.authActions{{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}}.authActions .sessionBtn,.authActions .btn,.authActions button{{flex:1 1 180px}}.authHint{{margin:8px 0 0;color:var(--muted);font-size:12px;line-height:1.35}}.authRememberToggle{{display:inline-flex;align-items:center;gap:8px;min-height:34px;padding:7px 11px;border:1px solid rgba(255,255,255,.10);border-radius:999px;background:rgba(255,255,255,.05);color:var(--muted);font-size:12px;font-weight:750;cursor:pointer;user-select:none}}.authRememberToggle input{{width:14px;height:14px;margin:0;flex:0 0 14px;accent-color:#22c55e}}.authSecretBox{{margin-top:10px;padding:10px;border:1px solid rgba(34,211,238,.24);border-radius:8px;background:rgba(34,211,238,.08)}}.authSecretBox strong{{display:block;margin-bottom:6px;font-size:12px;color:#f7f2ff}}.authSecretValue{{margin:0;padding:9px 10px;border:1px solid rgba(255,255,255,.08);border-radius:8px;background:rgba(0,0,0,.18);color:#c4b5fd;font:12px/1.4 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;word-break:break-word}}.authList{{display:grid;gap:8px;margin-top:10px}}.authRow{{display:grid;grid-template-columns:1fr auto;gap:8px;align-items:start;border:1px solid var(--line);border-radius:8px;background:rgba(255,255,255,.055);padding:9px}}.authRowAll{{border-style:dashed;background:linear-gradient(135deg,rgba(34,211,238,.10),rgba(34,197,94,.08),rgba(255,255,255,.04))}}.authRowTitle{{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-weight:900}}.authRowMeta{{margin-top:4px;color:var(--muted);font-size:12px;line-height:1.35;word-break:break-word}}.authRowKey{{margin-top:7px;padding:8px;border:1px solid rgba(255,255,255,.08);border-radius:7px;background:rgba(0,0,0,.18);color:#c4b5fd;font:11px/1.35 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;word-break:break-all}}.authRouterFlags{{justify-content:flex-end}}.authFlagPill{{gap:6px;min-height:30px;padding:5px 8px;cursor:pointer;user-select:none;touch-action:manipulation}}.authFlagPill input{{width:14px;height:14px;margin:0;flex:0 0 14px;accent-color:#22c55e}}.authFlagPill span{{font-size:11px;font-weight:900;line-height:1}}.authEmpty{{padding:10px;border:1px dashed var(--line);border-radius:8px;color:var(--muted);text-align:center}}.authDanger{{color:#fecdd3}}.authSocialIdentity{{display:grid;grid-template-columns:auto 1fr;gap:10px;align-items:center}}.authSocialAvatar{{width:38px;height:38px;border-radius:12px;overflow:hidden;border:1px solid rgba(255,255,255,.08);background:rgba(255,255,255,.05);display:grid;place-items:center;color:#dbeafe;font-size:15px;font-weight:900}}.authSocialAvatar img{{display:block;width:100%;height:100%;object-fit:cover}}.authSocialName{{font-weight:900;color:#f7f2ff}}.authSocialHandle{{margin-top:3px;color:var(--muted);font-size:12px;line-height:1.35;word-break:break-word}}
.sessionBox{{margin-top:14px;padding-top:12px;border-top:1px solid var(--line)}}.sessionHead{{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:8px}}.sessionHead h3{{margin:0;font-size:15px}}.sessionList{{display:grid;gap:8px;max-height:260px;overflow:auto;padding-right:2px}}.sessionRow{{display:grid;grid-template-columns:1fr auto;gap:8px;align-items:center;border:1px solid var(--line);border-radius:8px;background:rgba(255,255,255,.055);padding:9px;text-align:left}}.sessionTitle{{display:flex;gap:7px;align-items:center;flex-wrap:wrap;font-weight:900}}.sessionMeta{{margin-top:3px;color:var(--muted);font-size:12px;line-height:1.35;word-break:break-word}}.sessionCurrent{{border:1px solid rgba(34,197,94,.38);border-radius:999px;padding:2px 7px;color:#bbf7d0;background:rgba(34,197,94,.13);font-size:11px}}.sessionBtn{{padding:7px 9px;font-size:12px;border-radius:999px}}.sessionEmpty{{padding:10px;border:1px dashed var(--line);border-radius:8px;color:var(--muted);text-align:center}}
.notifyBox{{margin-top:14px;padding-top:12px;border-top:1px solid var(--line)}}.notifyActions{{display:flex;gap:7px;align-items:center;justify-content:flex-end;flex-wrap:wrap}}.notifyHint{{margin:-4px 0 10px;color:var(--muted);font-size:12px;line-height:1.35}}.notifyList{{display:grid;gap:8px;max-height:260px;overflow:auto;padding-right:2px}}.notifyRow{{border:1px solid var(--line);border-radius:8px;background:rgba(255,255,255,.055);padding:9px;text-align:left}}.notifyTitle{{display:flex;align-items:center;justify-content:space-between;gap:8px;font-weight:950}}.notifyTitle span:first-child{{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.notifyTime{{color:var(--muted);font-size:11px;font-weight:750;white-space:nowrap}}.notifyBody{{margin-top:4px;color:#ddd6fe;font-size:12px;line-height:1.35;word-break:break-word}}.notifyDetails{{margin:7px 0 0;padding:8px;border:1px solid rgba(255,255,255,.08);border-radius:7px;background:rgba(0,0,0,.18);color:#c4b5fd;font:11px/1.35 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;max-height:92px;overflow:auto}}.notifyRow.warn{{border-color:rgba(245,158,11,.34);background:rgba(245,158,11,.08)}}.notifyRow.bad{{border-color:rgba(251,113,133,.38);background:rgba(251,113,133,.09)}}.notifyBtn.on{{border-color:rgba(34,197,94,.36);background:rgba(34,197,94,.15);color:#bbf7d0}}
.backupBox{{margin-top:14px;padding-top:12px;border-top:1px solid var(--line)}}.backupGrid{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}.backupGrid .wide{{grid-column:1/-1}}.backupGrid input[type=file]{{padding:8px;background:rgba(8,5,18,.72);border:1px solid var(--line);border-radius:8px;color:#ddd6fe;min-width:0}}.backupHint{{margin:0 0 10px;color:var(--muted);font-size:12px;line-height:1.35}}.backupCmds{{display:grid;gap:8px;margin-top:10px}}.backupCmd{{border:1px solid rgba(255,255,255,.10);border-radius:8px;background:rgba(0,0,0,.18);padding:9px}}.backupCmd strong{{display:block;margin-bottom:5px;color:#f7f2ff;font-size:12px}}.backupCmd pre{{margin:0;white-space:pre-wrap;word-break:break-word;color:#c4b5fd;font:11px/1.35 ui-monospace,SFMono-Regular,Consolas,monospace}}#backupRewrite{{border-color:rgba(251,191,36,.42);background:linear-gradient(135deg,rgba(251,191,36,.28),rgba(245,158,11,.22));color:#fff7cc;box-shadow:0 10px 24px rgba(245,158,11,.14),inset 0 1px 0 rgba(255,255,255,.12)}}#backupRewrite:hover{{border-color:rgba(251,191,36,.56);background:linear-gradient(135deg,rgba(251,191,36,.34),rgba(245,158,11,.28))}}.backupMsg{{margin-top:9px;color:#bbf7d0;font-size:12px;font-weight:800;line-height:1.35}}.backupMsg.bad{{color:#fecdd3}}
input,select{{min-width:0;border:1px solid var(--line);border-radius:8px;padding:10px 11px;background:rgba(8,5,18,.72);color:var(--text)}}button,.btn{{border:1px solid rgba(255,255,255,.10);border-radius:8px;padding:10px 13px;background:rgba(255,255,255,.10);color:#f7f2ff;font-weight:850;text-decoration:none;cursor:pointer;display:inline-flex;justify-content:center;align-items:center}}.authToggle{{border-radius:999px;padding:8px 14px;background:rgba(255,255,255,.08);color:#f3e8ff}}button.primary,.btn.primary{{background:var(--blue);color:#fff;box-shadow:0 10px 22px rgba(124,58,237,.22)}}button.bad,.btn.bad{{background:rgba(251,113,133,.16);color:#fecdd3}}.btn.good{{background:rgba(34,197,94,.16);color:#bbf7d0}}.btn.disabled{{opacity:.45;cursor:not-allowed}}
.summary{{display:flex;gap:10px;flex-wrap:wrap;justify-content:flex-end}}.miniStat{{display:inline-flex;align-items:center;justify-content:center;gap:8px;min-height:36px;min-width:132px;border:1px solid var(--line);border-radius:999px;background:rgba(255,255,255,.07);padding:8px 12px;color:#ddd6fe;font-weight:800;font-size:13px;line-height:1;white-space:nowrap}}
.routerStats{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));align-items:start;gap:8px;margin:0 0 10px}}.statCard{{position:relative;min-height:54px;overflow:hidden;border:1px solid var(--line);border-radius:8px;padding:8px 10px 7px;background:linear-gradient(180deg,rgba(255,255,255,.075),rgba(255,255,255,.04)),var(--panel);box-shadow:0 8px 20px rgba(0,0,0,.14);text-align:center;box-sizing:border-box}}.statCard::before{{content:"";position:absolute;inset:0 0 auto 0;height:2px;background:var(--cyan)}}.statCard span{{display:block;color:var(--muted);font-size:10px;font-weight:850;text-transform:uppercase;letter-spacing:.035em}}.statCard strong{{display:block;margin-top:0;font-size:22px;line-height:1;color:#f7f2ff}}.statCard em{{display:block;min-width:0;margin-top:2px;padding:0 2px;color:#c4b5fd;font-style:normal;font-size:10px;line-height:1.15}}.statCard.online::before{{background:var(--green)}}.statCard.online strong{{color:#bbf7d0}}.statCard.offline::before{{background:var(--red)}}.statCard.offline strong{{color:#fecdd3}}.statCard.total::before{{background:var(--cyan)}}.statCard.total strong{{color:#a5f3fc}}.statCardHead{{display:flex;align-items:center;justify-content:center;min-height:14px}}.statCard.has-popover{{overflow:visible;z-index:12}}.statValueRow,.offlineStatRow{{position:relative;display:flex;align-items:center;justify-content:center;min-height:20px;margin-top:3px}}.offlineMoreBtn{{position:absolute;right:6px;top:50%;transform:translateY(-50%);display:inline-flex;align-items:center;justify-content:center;min-height:22px;padding:0 8px;border:1px solid rgba(251,113,133,.34);border-radius:999px;background:rgba(251,113,133,.12);color:#fecdd3;font-size:10px;font-weight:900;line-height:1;cursor:pointer;white-space:nowrap}}.offlineMoreBtn[aria-expanded="true"]{{background:rgba(251,113,133,.18);border-color:rgba(251,113,133,.48)}}.offlinePopover{{position:absolute;top:calc(100% + 8px);right:6px;width:min(340px,calc(100vw - 30px));padding:10px;border:1px solid rgba(251,113,133,.28);border-radius:12px;background:linear-gradient(180deg,rgba(49,28,44,.98),rgba(30,18,40,.98));box-shadow:0 22px 50px rgba(0,0,0,.34);text-align:left;z-index:25}}.offlinePopover[hidden]{{display:none!important}}.offlineList{{display:grid;gap:6px;max-height:170px;overflow:auto;padding-right:2px}}.offlineItem{{border:1px solid rgba(251,113,133,.18);border-radius:8px;padding:7px 8px;background:rgba(251,113,133,.08)}}.offlineName{{display:block;color:#fdf2f8;font-size:11px;font-weight:900;line-height:1.25;text-transform:none;letter-spacing:0}}
.cards{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}}.card{{position:relative;min-height:246px;overflow:hidden;background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.045)),var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px;box-shadow:0 18px 46px rgba(0,0,0,.28);backdrop-filter:blur(10px)}}.card::before{{content:"";position:absolute;inset:0 0 auto 0;height:3px;background:var(--green)}}.card.online{{border-color:rgba(34,197,94,.45);box-shadow:0 18px 46px rgba(0,0,0,.28),0 0 0 1px rgba(34,197,94,.10),0 0 34px rgba(34,197,94,.10)}}.card.off{{border-color:rgba(251,113,133,.42);box-shadow:0 18px 46px rgba(0,0,0,.28),0 0 0 1px rgba(251,113,133,.08),0 0 30px rgba(251,113,133,.08)}}.card.off::before{{background:var(--red)}}.card.warn::before{{background:var(--amber)}}.card.main{{grid-column:span 1}}
@keyframes onlineGlow{{0%,100%{{transform:scale(.9);opacity:.55}}50%{{transform:scale(1.08);opacity:1}}}}@keyframes offlineGlow{{0%,100%{{transform:scale(.88);opacity:.34}}50%{{transform:scale(1.08);opacity:.9}}}}
@keyframes bannerShine{{0%,45%{{transform:translateX(-120%)}}72%,100%{{transform:translateX(120%)}}}}
.cardTop{{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}}
.status{{display:inline-flex;align-items:center;gap:7px;border-radius:999px;border:1px solid rgba(34,197,94,.36);background:rgba(34,197,94,.14);padding:7px 10px;font-weight:900;font-size:12px;color:#bbf7d0}}.status i{{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 13px var(--green);animation:statusPulse 1.6s ease-in-out infinite}}.status.off{{border-color:rgba(251,113,133,.36);background:rgba(251,113,133,.12);color:#fecdd3}}.status.off i{{background:var(--red);box-shadow:0 0 13px var(--red);animation:offlinePulse 1.9s ease-in-out infinite}}.status.warn i{{background:var(--amber);box-shadow:0 0 13px var(--amber)}}@keyframes statusPulse{{0%,100%{{transform:scale(1);opacity:.75}}50%{{transform:scale(1.45);opacity:1}}}}@keyframes offlinePulse{{0%,100%{{transform:scale(1);opacity:.5}}50%{{transform:scale(1.42);opacity:1}}}}.nameRow{{display:inline-flex;align-items:center;justify-content:center;gap:8px;max-width:100%;margin-top:12px;vertical-align:top}}.nameRow::before{{content:"";display:block;flex:0 0 28px;width:28px;height:28px}}.name{{margin:0;font-size:19px;font-weight:900;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:220px}}.nameEditBtn{{position:static;display:inline-flex;align-items:center;justify-content:center;flex:0 0 28px;width:28px;height:28px;padding:0;border:1px solid rgba(251,191,36,.38);border-radius:999px;background:rgba(251,191,36,.12);color:#fde68a;box-shadow:inset 0 1px 0 rgba(255,255,255,.08);cursor:pointer;opacity:.5;transition:background .15s ease,border-color .15s ease,color .15s ease,transform .15s ease,opacity .15s ease}}.nameEditBtn:hover,.nameEditBtn:focus-visible{{opacity:1;border-color:rgba(34,211,238,.55);background:rgba(34,211,238,.14);color:#cffafe}}.nameEditBtn:active{{transform:scale(.96)}}.nameEditBtn svg{{width:13px;height:13px;display:block}}.nameEditBtn.disabled{{cursor:default;pointer-events:none;opacity:.22;border-color:rgba(255,255,255,.10);background:rgba(255,255,255,.05);color:rgba(255,255,255,.32)}}.mobilePanelToggle,.routerFormToggle{{display:none;width:100%;margin:14px 0 10px;border-radius:999px}}.routerFormWrap{{display:block}}[hidden],.headerActions[hidden],.routerStats[hidden],.routerFormWrap[hidden]{{display:none!important}}.metaLine{{margin-top:3px;color:var(--muted)}}.tagRow{{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}}.tag{{border:1px solid var(--line);border-radius:999px;padding:5px 9px;background:rgba(255,255,255,.06);color:#ddd6fe;font-size:12px;font-weight:750}}
.metrics{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:14px}}.metric{{display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;border:1px solid var(--line);border-radius:8px;background:rgba(255,255,255,.055);padding:9px}}.metric.span2{{grid-column:span 2}}.metric.temp-ok strong,.metric.flash-ok strong,.metric.memory-ok strong{{color:#bbf7d0}}.metric.temp-warn strong,.metric.flash-warn strong,.metric.memory-warn strong{{color:#fde68a}}.metric.temp-bad strong,.metric.flash-bad strong,.metric.memory-bad strong{{color:#fecdd3}}.metric>span{{display:block;width:100%;color:var(--muted);font-size:11px;text-align:center}}.metric strong{{display:block;width:100%;margin-top:2px;font-size:14px;word-break:break-word;text-align:center}}.metric.metric-compact strong{{font-size:14px;line-height:1.3;white-space:pre-line;word-break:normal}}.metric.temp-unavailable strong{{font-size:14px;line-height:1.3;white-space:pre-line;word-break:normal;color:#f3e8ff}}.metric.model-metric strong{{margin-top:5px}}.modelMetricValue{{position:relative;display:flex;align-items:center;justify-content:center;width:100%;max-width:100%}}.modelLegendSpacer{{display:none}}.modelMetricName{{display:block;max-width:calc(100% - 72px);min-width:0;color:#ffffff;font-size:14px;line-height:1.2;text-align:center;white-space:nowrap}}.modelLegendBadge{{position:absolute;right:0;top:50%;transform:translateY(-50%);display:inline-flex;align-items:center;justify-content:center;min-height:20px;min-width:62px;padding:0 8px;border:1px solid rgba(34,211,238,.38);border-radius:999px;background:linear-gradient(120deg,rgba(34,211,238,.24),rgba(59,130,246,.16),rgba(168,85,247,.14));color:#e0f7ff;font-size:8px;font-weight:900;line-height:1;letter-spacing:.10em;text-transform:uppercase;box-shadow:0 8px 18px rgba(34,211,238,.14),inset 0 1px 0 rgba(255,255,255,.12);overflow:hidden;white-space:nowrap}}.modelLegendBadge::before{{content:"";position:absolute;inset:0;background:linear-gradient(90deg,transparent,rgba(255,255,255,.22),transparent);transform:translateX(-120%);animation:bannerShine 6.2s ease-in-out infinite}}.modelLegendBadge span{{position:relative;display:block}}.actionToggle{{display:none}}.actions{{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}}.notesPreview{{margin-top:10px;padding:10px 12px;border:1px solid rgba(34,211,238,.16);border-radius:10px;background:linear-gradient(180deg,rgba(255,255,255,.06),rgba(255,255,255,.03)),rgba(18,14,30,.62);color:#ddd6fe;font-size:12px;line-height:1.45}}.notesPreview strong{{color:#f5f3ff}}.notesPreview span{{color:#c4b5fd}}.wolPanel,.trafficPanel{{display:grid;gap:10px;margin-top:12px;padding:12px;border:1px solid rgba(34,211,238,.20);border-radius:12px;background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.045)),rgba(18,14,30,.82);text-align:left}}.notesPanel{{gap:12px}}.notesEditor{{display:grid;gap:8px}}.notesArea{{width:100%;min-height:128px;padding:12px 13px;border:1px solid rgba(34,211,238,.24);border-radius:10px;background:linear-gradient(180deg,rgba(52,38,74,.96),rgba(34,26,52,.96));color:#f5f3ff;outline:none;box-shadow:inset 0 1px 0 rgba(255,255,255,.04);resize:vertical;font:13px/1.45 ui-monospace,SFMono-Regular,Consolas,monospace}}.notesArea:focus{{border-color:rgba(34,211,238,.60);box-shadow:0 0 0 3px rgba(34,211,238,.12),inset 0 1px 0 rgba(255,255,255,.05)}}.notesArea[readonly]{{opacity:.88;cursor:default}}.notesMeta{{display:flex;align-items:center;justify-content:space-between;gap:10px;color:#c4b5fd;font-size:12px;line-height:1.35}}.notesMeta.bad{{color:#fecdd3}}.notesMeta.ok{{color:#bbf7d0}}.notesMeta span:last-child{{white-space:nowrap}}.notesActions{{display:flex;gap:8px;flex-wrap:wrap}}.trafficPanel{{gap:12px}}.wolPanel[hidden],.trafficPanel[hidden]{{display:none!important}}.wolHeader{{display:flex;align-items:center;justify-content:space-between;gap:8px}}.wolTitle{{margin:0;color:#f3e8ff;font-size:13px;font-weight:900}}.wolMeta{{min-height:16px;color:#c4b5fd;font-size:12px;line-height:1.35}}.wolMeta.bad{{color:#fecdd3}}.wolControls,.trafficControls{{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,.95fr) auto;gap:8px;align-items:end}}.wolField{{display:grid;gap:6px;min-width:0;color:#ddd6fe;font-size:11px;font-weight:850}}.wolField select,.wolField input{{width:100%;min-width:0;min-height:38px;padding:0 12px;border:1px solid rgba(34,211,238,.24);border-radius:10px;background:linear-gradient(180deg,rgba(52,38,74,.96),rgba(34,26,52,.96));color:#f5f3ff;outline:none;box-shadow:inset 0 1px 0 rgba(255,255,255,.04)}}.wolField select option{{background:#221a34;color:#f5f3ff}}.wolField select option:checked{{background:#3b82f6;color:#ffffff}}.wolField select:focus,.wolField input:focus{{border-color:rgba(34,211,238,.60);box-shadow:0 0 0 3px rgba(34,211,238,.12),inset 0 1px 0 rgba(255,255,255,.05)}}.wolField input::placeholder{{color:rgba(221,214,254,.52)}}.wolControls .btn,.wolControls button,.trafficControls .btn,.trafficControls button{{min-height:38px}}.trafficSummary{{display:flex;flex-wrap:wrap;gap:6px}}.trafficSummaryChip{{display:inline-flex;align-items:center;min-height:24px;padding:0 9px;border:1px solid rgba(167,139,250,.20);border-radius:999px;background:rgba(255,255,255,.05);color:#ddd6fe;font-size:10px;font-weight:850;letter-spacing:.03em;text-transform:uppercase}}.trafficSummaryChip.accent{{border-color:rgba(34,211,238,.34);background:rgba(34,211,238,.12);color:#cffafe}}.trafficSummaryChip.muted{{border-color:rgba(245,158,11,.24);background:rgba(245,158,11,.10);color:#fde68a}}.trafficViewport{{max-height:min(46vh,420px);overflow:auto;padding:8px 4px 0 0;border-top:1px solid rgba(167,139,250,.14);scrollbar-width:thin;overscroll-behavior:contain}}.trafficViewport::-webkit-scrollbar{{width:8px}}.trafficViewport::-webkit-scrollbar-thumb{{background:rgba(167,139,250,.24);border-radius:999px}}.trafficViewport::-webkit-scrollbar-track{{background:transparent}}.trafficList{{display:grid;gap:7px}}.trafficRow{{display:grid;gap:7px;padding:10px;border:1px solid rgba(167,139,250,.16);border-radius:10px;background:linear-gradient(180deg,rgba(57,43,82,.45),rgba(33,26,48,.70));box-shadow:inset 0 1px 0 rgba(255,255,255,.03)}}.trafficRowTop{{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:start;gap:10px}}.trafficIdentity{{min-width:0}}.trafficName{{margin:0;color:#f5f3ff;font-size:13px;font-weight:900;line-height:1.35}}.trafficMetaLine{{margin:3px 0 0;color:#c4b5fd;font-size:10.5px;line-height:1.4;word-break:break-word}}.trafficTotalBadge{{display:grid;gap:2px;min-width:84px;padding:7px 9px;border:1px solid rgba(34,211,238,.22);border-radius:10px;background:linear-gradient(180deg,rgba(34,211,238,.12),rgba(34,211,238,.04));text-align:right}}.trafficTotalBadge span{{color:#a5f3fc;font-size:9px;font-weight:800;letter-spacing:.06em;text-transform:uppercase}}.trafficTotalBadge strong{{color:#ecfeff;font-size:12px;line-height:1.25;word-break:break-word}}.trafficTagRow{{display:flex;flex-wrap:wrap;gap:5px}}.trafficTag{{display:inline-flex;align-items:center;min-height:20px;padding:0 7px;border:1px solid rgba(34,211,238,.24);border-radius:999px;background:rgba(34,211,238,.10);color:#cffafe;font-size:9px;font-weight:850;letter-spacing:.03em;text-transform:uppercase}}.trafficStats{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}}.trafficStat{{display:grid;gap:2px;padding:7px 8px;border:1px solid rgba(255,255,255,.08);border-radius:8px;background:rgba(255,255,255,.04);text-align:center}}.trafficStat span{{color:#c4b5fd;font-size:9px;font-weight:800;letter-spacing:.02em;text-transform:uppercase}}.trafficStat strong{{color:#f5f3ff;font-size:11px;line-height:1.35;word-break:break-word}}.trafficStatTotal{{display:none}}.empty{{grid-column:1/-1;border:1px dashed var(--line);border-radius:8px;padding:30px;background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.045)),var(--panel);text-align:center;color:var(--muted)}}.hint{{margin-top:16px;padding:13px;border:1px solid var(--line);border-radius:8px;background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.045)),var(--panel);color:var(--muted)}}.diagnosticPanel{{margin:16px 0 4px;padding:14px;border:1px solid rgba(34,211,238,.22);border-radius:8px;background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.045)),var(--panel);box-shadow:0 18px 46px rgba(0,0,0,.20);text-align:center}}.diagnosticPanel[hidden]{{display:none!important}}.diagnosticTop{{display:grid;grid-template-columns:1fr auto 1fr;gap:12px;align-items:flex-start}}.diagnosticTop>div{{grid-column:2;text-align:center}}.diagnosticTop .btn{{grid-column:3;justify-self:end;align-self:start;width:auto;min-width:118px;max-width:none;padding-left:18px;padding-right:18px}}.diagnosticTop h2{{margin:0;font-size:18px}}.diagnosticLead{{margin:4px 0 0;color:var(--muted);font-size:12px;line-height:1.4}}.diagnosticGrid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-top:12px}}.diagnosticGrid label{{display:grid;gap:6px;color:#ddd6fe;font-size:12px;font-weight:850;text-align:center}}.diagnosticGrid textarea{{min-height:92px;resize:vertical;border:1px solid rgba(167,139,250,.24);border-radius:10px;padding:12px 13px;background:linear-gradient(180deg,rgba(52,38,74,.96),rgba(34,26,52,.96));color:#f5f3ff;box-shadow:inset 0 1px 0 rgba(255,255,255,.04);outline:none;text-align:left}}.diagnosticGrid textarea::placeholder{{color:rgba(221,214,254,.42)}}.diagnosticGrid textarea:focus{{border-color:rgba(34,211,238,.62);box-shadow:0 0 0 3px rgba(34,211,238,.12),inset 0 1px 0 rgba(255,255,255,.05)}}.diagnosticActions{{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;justify-content:center}}.diagSummary{{margin-top:12px;padding:10px 12px;border-radius:8px;font-weight:850}}.diagSummary.good{{border:1px solid rgba(34,197,94,.35);background:rgba(34,197,94,.12);color:#bbf7d0}}.diagSummary.warn{{border:1px solid rgba(245,158,11,.34);background:rgba(245,158,11,.10);color:#fde68a}}.diagSummary.bad{{border:1px solid rgba(251,113,133,.38);background:rgba(251,113,133,.10);color:#fecdd3}}.diagList{{margin:0;padding-left:18px;color:#ddd6fe}}.diagList li{{margin:2px 0}}.diagBlocks{{display:grid;gap:8px;margin-top:10px;text-align:left}}.diagBlock{{border:1px solid rgba(167,139,250,.16);border-radius:8px;padding:10px;background:linear-gradient(180deg,rgba(57,43,82,.55),rgba(33,26,48,.74));box-shadow:inset 0 1px 0 rgba(255,255,255,.03)}}.diagBlock strong{{display:block;margin-bottom:4px}}.diagBody{{display:grid;gap:8px}}.diagTextLine{{white-space:pre-line;line-height:1.45}}.diagCmdLine{{display:grid;gap:5px}}.diagCmdLabel{{line-height:1.4}}.diagCmdRow{{display:flex;align-items:center;gap:8px;min-width:0}}.diagCode{{flex:1;min-width:0;margin:0;padding:7px 11px;border:1px solid rgba(255,255,255,.08);border-radius:8px;background:rgba(0,0,0,.20);color:#c4b5fd;font:12px/1.35 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:nowrap;overflow:auto;scrollbar-width:thin}}.diagCopyBtn{{flex:0 0 36px;width:36px;height:36px;padding:0;border:1px solid rgba(255,255,255,.12);border-radius:8px;background:rgba(255,255,255,.08);color:#f3e8ff;display:inline-flex;align-items:center;justify-content:center;cursor:pointer;transition:transform .15s ease,background .15s ease,border-color .15s ease}}.diagCopyBtn:hover{{border-color:rgba(34,211,238,.52);background:rgba(34,211,238,.12)}}.diagCopyBtn:active{{transform:scale(.96)}}.diagCopyBtn.copied{{border-color:rgba(34,197,94,.42);background:rgba(34,197,94,.16);color:#bbf7d0}}.diagCopyBtn svg{{width:16px;height:16px;display:block}}.diagCopyBtn span{{position:absolute;left:-9999px}}.diagBlock.good strong{{color:#bbf7d0}}.diagBlock.warn strong{{color:#fde68a}}.diagBlock.bad strong{{color:#fecdd3}}code{{background:rgba(255,255,255,.10);border-radius:6px;padding:2px 5px;color:#f3e8ff}}
.wolControls .btn[disabled],.wolControls button[disabled]{{opacity:.48;cursor:not-allowed;filter:saturate(.62)}}
.trafficControls{{grid-template-columns:repeat(2,minmax(0,1fr))}}.trafficPasswordField{{grid-column:1}}.trafficStatusField{{grid-column:2}}.trafficActionBtn{{width:100%;min-width:0;padding:8px 8px;font-size:10px;letter-spacing:.01em;white-space:nowrap}}.trafficStatusField .wolMeta{{font-size:10px;line-height:1.22}}.trafficDangerBtn{{border-color:rgba(251,113,133,.34)!important;background:linear-gradient(180deg,rgba(251,113,133,.18),rgba(251,113,133,.08))!important;color:#ffe4e6!important}}.trafficDangerBtn:hover{{border-color:rgba(251,113,133,.54)!important;background:linear-gradient(180deg,rgba(251,113,133,.24),rgba(251,113,133,.12))!important}}
.wolField input,.wolPickerToggle{{width:100%;min-width:0;min-height:38px;padding:0 12px;border:1px solid rgba(34,211,238,.24);border-radius:10px;background:linear-gradient(180deg,rgba(52,38,74,.96),rgba(34,26,52,.96));color:#f5f3ff;outline:none;box-shadow:inset 0 1px 0 rgba(255,255,255,.04)}}.wolField input:focus,.wolPickerToggle:focus,.wolPickerToggle:focus-visible{{border-color:rgba(34,211,238,.60);box-shadow:0 0 0 3px rgba(34,211,238,.12),inset 0 1px 0 rgba(255,255,255,.05)}}.wolDeviceField{{grid-column:1/-1;width:100%}}.wolPickerToggle{{display:flex;align-items:center;justify-content:space-between;gap:10px;text-align:left;cursor:pointer;touch-action:manipulation}}.wolPickerToggle[disabled]{{opacity:.55;cursor:not-allowed}}.wolPickerValue{{display:grid;gap:2px;min-width:0;flex:1}}.wolPickerValue strong{{display:block;min-width:0;color:#f5f3ff;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.wolPickerValue small{{display:block;min-width:0;color:#c4b5fd;font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.wolPickerChevron{{flex:0 0 auto;color:#c4b5fd;font-size:15px;line-height:1}}.wolPickerList{{display:grid;gap:8px;max-height:240px;overflow:auto;padding:8px;border:1px solid rgba(34,211,238,.24);border-radius:12px;background:rgba(17,12,29,.88);box-shadow:inset 0 1px 0 rgba(255,255,255,.04)}}.wolDeviceBtn{{width:100%;display:grid;gap:3px;padding:10px 12px;border:1px solid rgba(167,139,250,.18);border-radius:10px;background:rgba(255,255,255,.04);color:#f5f3ff;text-align:left;cursor:pointer;touch-action:manipulation;transition:border-color .15s ease,background .15s ease,transform .15s ease}}.wolDeviceBtn:hover,.wolDeviceBtn:focus-visible{{border-color:rgba(34,211,238,.48);background:rgba(34,211,238,.12)}}.wolDeviceBtn:active{{transform:scale(.99)}}.wolDeviceBtn.active{{border-color:rgba(59,130,246,.62);background:rgba(59,130,246,.18);box-shadow:0 0 0 1px rgba(59,130,246,.16)}}.wolDeviceName{{display:block;color:#f5f3ff;font-size:13px;font-weight:800;line-height:1.3}}.wolDeviceMeta{{display:block;color:#c4b5fd;font-size:11px;line-height:1.35;word-break:break-word}}.wolPickerEmpty{{padding:14px 12px;border:1px dashed rgba(167,139,250,.20);border-radius:10px;background:rgba(255,255,255,.03);color:#c4b5fd;text-align:center}}.metric.memory-ok strong,.metric.flash-ok strong,.metric.memory-warn strong,.metric.flash-warn strong,.metric.memory-bad strong,.metric.flash-bad strong{{color:#f3e8ff}}.metric.memory-ok .metric-accent,.metric.flash-ok .metric-accent{{color:#bbf7d0}}.metric.memory-warn .metric-accent,.metric.flash-warn .metric-accent{{color:#fde68a}}.metric.memory-bad .metric-accent,.metric.flash-bad .metric-accent{{color:#fecdd3}}.metric-line{{display:block}}.metric-accent{{font-weight:inherit}}
.seasonalFx{{position:fixed;inset:0;display:block;width:100vw;height:100vh;z-index:0;pointer-events:none;opacity:.9;mix-blend-mode:screen}}.desktopHeader{{--hdr-col-1:124px;--hdr-col-2:124px;--hdr-col-3:156px;--hdr-col-4:124px;--hdr-col-5:144px;--hdr-col-6:144px;--hdr-col-7:144px;--hdr-col-8:144px;--top-col-1:256px;--top-col-2:288px;--top-col-3:144px;display:grid;gap:8px;width:max-content;max-width:100%;margin-left:15px}}.desktopHeaderTop{{display:grid;grid-template-columns:var(--top-col-1) var(--top-col-2) var(--top-col-3);align-items:flex-start;gap:8px;width:max-content;max-width:100%;justify-self:start}}.desktopHeaderTop>.appBanner{{width:var(--top-col-1);min-width:var(--top-col-1);max-width:var(--top-col-1)}}.desktopHeaderTop>.routerSearchDock{{width:var(--top-col-2);min-width:var(--top-col-2);max-width:var(--top-col-2)}}.desktopHeaderTop>#seasonDock{{width:var(--top-col-3);min-width:var(--top-col-3);max-width:var(--top-col-3)}}.desktopHeaderBottom{{display:grid;grid-template-columns:var(--hdr-col-1) var(--hdr-col-2) var(--hdr-col-3) var(--hdr-col-4) var(--hdr-col-5) var(--hdr-col-6) var(--hdr-col-7) var(--hdr-col-8);gap:8px;width:max-content;max-width:100%;align-items:start;justify-self:start}}.desktopHeader .appBanner{{grid-column:auto;width:100%;min-width:0}}.desktopHeader .links{{display:grid;grid-column:1/span 3;grid-template-columns:var(--hdr-col-1) var(--hdr-col-2) var(--hdr-col-3);gap:8px;margin-top:0}}.desktopHeader .links a{{width:100%;min-width:0}}.routerSearchDock{{position:relative;display:block;grid-column:auto;width:100%;min-width:0;max-width:100%}}.mobileSearchDock{{display:none;width:100%;position:relative}}.routerSearchToggle{{position:relative;display:inline-flex;align-items:center;justify-content:center;gap:8px;min-height:36px;width:100%;padding:8px 14px;border:1px solid rgba(34,211,238,.30);border-radius:999px;background:linear-gradient(110deg,rgba(34,211,238,.12),rgba(124,58,237,.22),rgba(236,72,153,.12));color:#f3e8ff;font-size:13px;font-weight:800;line-height:1;box-shadow:0 10px 24px rgba(124,58,237,.16),inset 0 1px 0 rgba(255,255,255,.10)}}.routerSearchToggle[data-active="true"],.routerSearchToggle[aria-expanded="true"]{{border-color:rgba(34,211,238,.52);box-shadow:0 14px 30px rgba(34,211,238,.14),inset 0 1px 0 rgba(255,255,255,.12)}}.routerSearchToggleIcon{{flex:0 0 auto;color:#c4b5fd;font-size:14px;line-height:1}}.mobileSearchVersion{{display:inline-flex;align-items:center;justify-content:center;min-height:20px;padding:0 7px;border:1px solid rgba(251,113,133,.34);border-radius:999px;background:rgba(251,113,133,.14);color:#fecdd3;font-size:10px;font-weight:900;line-height:1;letter-spacing:.04em;box-shadow:0 0 14px rgba(251,113,133,.14)}}.mobileSearchDock>.mobileSearchVersion{{display:flex;width:fit-content;margin:0 auto 6px}}#routerSearchToggle,#seasonToggle{{border:1px solid var(--line);background:rgba(255,255,255,.08);box-shadow:inset 0 1px 0 rgba(255,255,255,.06);color:#f3e8ff}}#routerSearchToggle[data-active="true"],#routerSearchToggle[aria-expanded="true"],#seasonToggle[data-open="true"],#seasonToggle[aria-expanded="true"]{{border-color:var(--line);background:rgba(255,255,255,.11);box-shadow:inset 0 1px 0 rgba(255,255,255,.08)}}#routerSearchToggle .routerSearchToggleIcon,#seasonToggle .routerSearchToggleIcon{{color:#ddd6fe}}.routerSearchPanel{{position:absolute;top:calc(100% + 10px);left:0;z-index:55;width:min(296px,calc(100vw - 24px))}}.routerSearchPanel[hidden]{{display:none!important}}.routerSearchCard{{display:grid;grid-template-rows:auto auto auto;align-content:start;gap:8px;width:100%;min-height:82px;padding:12px;border:1px solid rgba(34,211,238,.26);border-radius:18px;background:linear-gradient(180deg,rgba(255,255,255,.09),rgba(255,255,255,.045)),rgba(19,14,32,.96);box-shadow:0 18px 42px rgba(0,0,0,.22),inset 0 1px 0 rgba(255,255,255,.06);backdrop-filter:blur(10px)}}.routerSearchHead{{display:flex;align-items:center;justify-content:space-between;gap:8px}}.routerSearchTitle{{display:block;color:#f3e8ff;font-size:12px;font-weight:900;line-height:1.1}}.routerSearchClear{{min-height:24px;padding:0 9px;border:1px solid rgba(167,139,250,.24);border-radius:999px;background:rgba(255,255,255,.06);color:#ddd6fe;font-size:11px;font-weight:850;cursor:pointer}}.routerSearchClear[disabled]{{opacity:.42;cursor:not-allowed}}.routerSearchField{{display:flex;align-items:center;gap:8px;min-height:38px;padding:0 12px;border:1px solid rgba(34,211,238,.26);border-radius:999px;background:linear-gradient(110deg,rgba(34,211,238,.08),rgba(124,58,237,.14),rgba(236,72,153,.08));box-shadow:inset 0 1px 0 rgba(255,255,255,.06)}}.routerSearchField:focus-within{{border-color:rgba(34,211,238,.54);box-shadow:0 0 0 3px rgba(34,211,238,.10),inset 0 1px 0 rgba(255,255,255,.08)}}.routerSearchField input{{width:100%;padding:0;border:0;background:transparent;color:#f7f2ff;box-shadow:none;outline:none;font-size:13px;font-weight:700}}.routerSearchField input::placeholder{{color:#b9adc9}}.routerSearchIcon{{flex:0 0 auto;color:#c4b5fd;font-size:14px;line-height:1}}.routerSearchMeta{{min-height:14px;color:#c4b5fd;font-size:11px;font-weight:800;line-height:1.2}}.seasonDock{{display:grid;gap:6px;width:100%;padding:9px 10px;border:1px solid rgba(251,191,36,.18);border-radius:16px;background:linear-gradient(180deg,rgba(255,255,255,.07),rgba(255,255,255,.035)),rgba(19,14,32,.90);box-shadow:0 12px 28px rgba(0,0,0,.18);backdrop-filter:blur(10px)}}.seasonLabel{{display:flex;align-items:center;justify-content:center;text-align:center;gap:8px;color:#fde68a;font-size:11px;font-weight:900;line-height:1.1;text-transform:uppercase;letter-spacing:.04em}}.seasonSwitch{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:5px}}.seasonBtn{{min-height:30px;padding:6px 8px;border:1px solid rgba(251,191,36,.22);border-radius:999px;background:rgba(255,255,255,.06);color:#f7f2ff;font-size:11px;font-weight:850;line-height:1;cursor:pointer;transition:transform .15s ease,border-color .15s ease,background .15s ease,box-shadow .15s ease,color .15s ease}}.seasonBtn:hover,.seasonBtn:focus-visible{{border-color:rgba(34,211,238,.50);background:rgba(34,211,238,.12);color:#ecfeff}}.seasonBtn[data-active="true"]{{border-color:rgba(251,191,36,.52);background:linear-gradient(110deg,rgba(251,191,36,.22),rgba(34,211,238,.10),rgba(168,85,247,.14));box-shadow:0 10px 20px rgba(251,191,36,.14),inset 0 1px 0 rgba(255,255,255,.08);color:#fff7cc}}.seasonBtn:active{{transform:scale(.98)}}#seasonDock{{position:relative;display:block;grid-column:auto;width:100%;min-width:0;max-width:none;padding:0;border:0;background:none;box-shadow:none;backdrop-filter:none;align-self:start}}.seasonToggle{{min-height:36px;padding:8px 12px;font-size:13px;line-height:1;white-space:normal;text-align:center}}.seasonToggleText{{display:block}}.seasonPanel{{position:absolute;top:calc(100% + 10px);left:0;z-index:56;width:min(320px,calc(100vw - 24px))}}.seasonPanel[hidden]{{display:none!important}}.seasonCard{{display:grid;gap:8px;width:100%;padding:12px;border:1px solid rgba(251,191,36,.18);border-radius:18px;background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.035)),rgba(19,14,32,.95);box-shadow:0 18px 42px rgba(0,0,0,.22),inset 0 1px 0 rgba(255,255,255,.06);backdrop-filter:blur(12px)}}.headerActions{{display:grid;grid-column:4/span 5;grid-template-columns:var(--hdr-col-4) var(--hdr-col-5) var(--hdr-col-6) var(--hdr-col-7) var(--hdr-col-8);align-self:flex-start;justify-content:flex-start;align-content:flex-start;min-width:0;gap:8px;padding-top:0;max-width:none}}.card{{text-align:center}}.cardTop{{align-items:center;justify-content:center;flex-direction:column}}.tagRow,.actions{{justify-content:center}}.name{{display:inline-flex;align-items:center;justify-content:center;max-width:220px;min-height:34px;margin:0;padding:7px 10px;border:1px solid rgba(251,191,36,.48);border-radius:999px;background:linear-gradient(135deg,rgba(251,191,36,.32),rgba(245,158,11,.22),rgba(255,255,255,.07));white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#fff;font-size:13px;line-height:1;font-weight:900;text-shadow:0 0 16px rgba(251,191,36,.42);box-shadow:0 10px 24px rgba(245,158,11,.10),inset 0 1px 0 rgba(255,255,255,.12)}}.metric{{text-align:center}}.metric.span2{{grid-column:1/-1}}
@media(max-width:980px){{.cards{{grid-template-columns:repeat(2,minmax(0,1fr))}}.toolbar{{grid-template-columns:1fr 1fr}}.card.main{{grid-column:span 1}}.top{{justify-items:stretch}}.desktopHeader,.desktopHeaderTop,.desktopHeaderBottom{{width:100%;max-width:none}}.desktopHeader{{--hdr-col-1:minmax(0,1fr);--hdr-col-2:minmax(0,1fr);--hdr-col-3:minmax(0,1.35fr);--hdr-col-4:minmax(0,1fr);--hdr-col-5:minmax(0,1fr);--hdr-col-6:minmax(0,1fr);--hdr-col-7:minmax(0,1fr);--hdr-col-8:minmax(0,1fr)}}.headerActions{{width:100%}}}}
@media(max-width:680px){{body{{font-size:13px;background-attachment:scroll}}.wrap{{padding:10px}}.top{{gap:12px;padding:14px 0;align-items:flex-start}}.brand,.brand>div{{width:100%}}h1{{font-size:22px;line-height:1.18}}.appBanner{{width:auto;max-width:100%;justify-content:center;min-height:36px;padding:8px 12px}}.links,.headerActions,.summary,.mobileOwnerTools{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));width:100%;gap:8px;max-width:none}}.links{{margin-top:10px}}.links a,.badge,.headerActions .btn,.mobileOwnerTools .btn,.mobileOwnerTools .badge,.miniStat{{width:100%;min-width:0;padding:9px 10px;font-size:12px}}.headerStack{{margin-top:0;width:100%;min-width:0}}.headerStack .badge{{width:100%;min-width:0}}.authMenu{{position:fixed;left:10px;right:10px;top:74px;width:auto;max-height:calc(100svh - 90px);overflow:auto}}.authMenuHead{{min-height:40px;padding:0 104px 8px 0}}.authMenu h2{{margin:2px 104px 0 0;line-height:1.2}}.authMenuClose{{display:inline-flex;top:0;right:0;min-height:32px;padding:6px 12px}}.cards,.toolbar,.authGrid{{grid-template-columns:1fr}}.authRow{{grid-template-columns:1fr;gap:7px;padding:8px}}.authRowTitle{{font-size:13px}}.authRowMeta{{font-size:11px;line-height:1.3}}.authRouterFlags{{justify-content:flex-start;gap:6px}}.authFlagPill{{min-height:25px;padding:3px 7px;gap:5px}}.authFlagPill input{{width:12px;height:12px;flex-basis:12px}}.authFlagPill span{{font-size:10px}}.routerStats{{grid-template-columns:repeat(3,minmax(0,1fr));gap:5px;margin-bottom:7px}}.statCard{{min-height:52px;padding:7px 6px}}.statCard span{{font-size:8px;letter-spacing:.015em}}.statCard strong{{font-size:20px}}.statCard em{{display:none}}.statCardHead{{min-height:16px}}.statValueRow,.offlineStatRow{{min-height:20px;margin-top:4px}}.offlineMoreBtn{{right:2px;min-height:16px;padding:0 5px;font-size:7px}}.offlinePopover{{right:3px;width:min(230px,calc(100vw - 20px));padding:8px}}.offlineItem{{padding:6px 7px}}.offlineName{{font-size:10px}}.toolbar{{padding:10px;margin:12px 0}}.card.main{{grid-column:span 1}}.card{{padding:12px;min-height:0}}.card>.metaLine,.card>.tagRow{{display:none}}.nameRow{{gap:6px;margin-top:10px}}.nameRow::before{{flex-basis:26px;width:26px;height:26px}}.name{{font-size:12px;max-width:190px}}.nameEditBtn{{flex-basis:26px;width:26px;height:26px}}.mobilePanelToggle,.routerFormToggle{{display:inline-flex}}.routerSearchDock{{width:100%}}.seasonDock{{width:100%}}#mobileSeasonDock{{width:100%;min-width:0;max-width:none;padding:0;border:0;background:none;box-shadow:none;backdrop-filter:none}}.routerSearchToggle,.seasonToggle{{width:100%}}.routerSearchPanel,.seasonPanel{{position:static;width:100%;margin-top:8px}}.routerSearchCard,.seasonCard{{width:100%;min-height:0;padding:10px 11px;border-radius:16px}}.routerSearchTitle{{font-size:11px}}.routerSearchField{{min-height:36px}}.routerSearchMeta{{text-align:center}}.metrics{{grid-template-columns:repeat(2,minmax(0,1fr));gap:7px}}.metric{{padding:8px}}.diagnosticPanel{{padding:12px}}.diagnosticTop{{gap:8px}}.diagnosticTop h2{{font-size:17px}}.diagnosticLead{{font-size:11px}}.diagnosticGrid{{grid-template-columns:1fr;gap:10px}}.diagnosticGrid label{{font-size:13px}}.diagnosticGrid textarea{{min-height:118px;padding:12px 13px;font-size:15px;line-height:1.35}}.diagBlock{{padding:11px}}.actions{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}}.actions .btn,.actions button{{width:100%;min-width:0;padding:9px 8px;font-size:12px}}.wolControls,.trafficControls{{grid-template-columns:1fr}}.wolField span,.wolMeta{{text-align:center}}.wolControls .btn,.wolControls button,.trafficControls .btn,.trafficControls button{{width:100%}}.trafficSummary{{justify-content:center}}.trafficViewport{{max-height:360px;padding-right:0}}.trafficRowTop{{grid-template-columns:1fr}}.trafficTotalBadge{{min-width:0;text-align:left}}}}
@media(max-width:680px){{.desktopHeader{{display:none}}#hubMenuPanelHost{{display:block;width:100%}}#hubMenuPanelHost:empty{{display:none}}.mobileOwnerTools{{display:grid;margin:0 0 8px}}.mobileOwnerTools[hidden]{{display:none!important}}.top{{padding:6px 0 0;gap:0}}.mobileSearchDock{{display:block;margin:0}}.mobilePanelToggle,.routerFormToggle,.actionToggle{{width:100%;min-height:38px;margin:7px 0;padding:9px 12px;border-radius:999px;font-size:12px;line-height:1}}.actionToggle{{display:inline-flex}}body.preload-mobile-panels .headerActions,body.preload-mobile-panels .routerFormWrap,body.preload-mobile-panels .routerStats,body.preload-mobile-panels .mobileOwnerTools{{display:none!important}}.card .actions.mobileCollapsed:not(.open){{display:none}}.cardTop{{gap:0}}}}
@media(max-width:680px){{.headerActions .badge,.headerActions .btn,.mobileOwnerTools .badge,.mobileOwnerTools .btn{{width:100%;min-width:0;max-width:none}}}}
@media(max-width:420px){{.links,.headerActions,.summary,.actions,.mobileOwnerTools{{grid-template-columns:1fr}}.metrics{{grid-template-columns:1fr}}.metric.span2{{grid-column:span 1}}}}
@media(max-width:680px){{.desktopHeader{{width:100%}}.summary{{justify-content:center}}}}
@media(min-width:681px){{.trafficPanel{{width:min(100%,332px);justify-self:center;padding:9px 9px 8px;border-radius:10px;gap:9px}}.trafficControls{{grid-template-columns:repeat(2,minmax(0,1fr))!important;gap:6px}}.trafficPanel .wolField{{gap:4px;font-size:11px}}.trafficPanel .wolField span{{text-align:center}}.trafficPanel .wolField input{{min-height:32px;padding:0 9px;font-size:13px}}.trafficStatusField .wolMeta{{min-height:32px;font-size:11px;line-height:1.18;text-align:center}}.trafficPanel .btn{{min-height:32px;padding:6px 7px;font-size:11px}}.trafficSummary{{justify-content:center}}.trafficSummaryChip{{min-height:20px;padding:0 7px;font-size:9.5px}}.trafficViewport{{max-height:min(42svh,350px)}}.trafficList{{gap:6px}}.trafficRow{{gap:6px;padding:8px}}.trafficRowTop{{grid-template-columns:1fr;gap:7px}}.trafficIdentity{{text-align:center!important}}.trafficName{{display:block;width:100%;font-size:13px;text-align:center!important}}.trafficMetaLine{{font-size:10px;line-height:1.32;text-align:center!important}}.trafficTotalBadge{{display:grid;width:fit-content;min-width:136px;max-width:186px;justify-self:center!important;margin:0 auto;place-self:center;padding:6px 10px;text-align:center!important}}.trafficTotalBadge span{{font-size:9px}}.trafficTotalBadge strong{{font-size:12px}}.trafficStat{{padding:6px 5px}}.trafficStat span{{font-size:9px}}.trafficStat strong{{font-size:11px}}}}
@media(max-width:900px),(pointer:coarse){{.trafficPanel{{width:min(100%,340px);justify-self:center;padding:10px 10px 9px;gap:9px}}.trafficControls{{grid-template-columns:repeat(2,minmax(0,1fr))!important;gap:6px;align-items:start}}.trafficPanel .wolField{{gap:4px;font-size:11px}}.trafficPanel .wolField span{{text-align:center}}.trafficPanel .wolField input{{min-height:34px;padding:0 10px;font-size:13px}}.trafficStatusField .wolMeta{{display:flex;align-items:center;justify-content:center;min-height:34px;padding:0 4px;font-size:11px;line-height:1.18;text-align:center}}.trafficPanel .btn{{min-height:34px;padding:7px 8px;font-size:11px}}.trafficSummary{{justify-content:center}}.trafficSummaryChip{{min-height:22px;padding:0 8px;font-size:9.5px}}.trafficViewport{{max-height:min(44svh,390px);padding-top:4px}}.trafficList{{gap:6px}}.trafficRow{{gap:6px;padding:8px}}.trafficRowTop{{grid-template-columns:1fr;gap:7px}}.trafficIdentity{{text-align:center!important}}.trafficName{{display:block;width:100%;font-size:13px;text-align:center!important}}.trafficMetaLine{{font-size:10px;line-height:1.33;text-align:center!important}}.trafficTotalBadge{{display:grid;width:fit-content;min-width:140px;max-width:198px;justify-self:center!important;margin:0 auto;place-self:center;padding:6px 12px;text-align:center!important}}.trafficTotalBadge span{{font-size:9px}}.trafficTotalBadge strong{{font-size:12px}}.trafficStats{{gap:5px}}.trafficStat{{padding:6px 6px}}.trafficStat span{{font-size:9px}}.trafficStat strong{{font-size:11px}}}}
@media(max-width:680px){{.trafficPanel{{width:min(100%,332px);padding:9px 9px 8px;border-radius:10px}}.trafficControls{{grid-template-columns:repeat(2,minmax(0,1fr))!important;gap:6px}}.trafficPanel .wolField{{gap:4px;font-size:11px}}.trafficPanel .wolField input{{min-height:32px;padding:0 9px;font-size:12.5px}}.trafficStatusField .wolMeta{{min-height:32px;font-size:11px;line-height:1.18}}.trafficPanel .btn{{min-height:32px;padding:6px 7px;font-size:11px}}.trafficSummaryChip{{min-height:20px;padding:0 7px;font-size:9px}}.trafficViewport{{max-height:min(42svh,350px)}}.trafficIdentity{{text-align:center!important}}.trafficName{{font-size:12px;text-align:center!important}}.trafficMetaLine{{font-size:10px;line-height:1.28;text-align:center!important}}.trafficTotalBadge{{display:grid;width:fit-content;min-width:130px;max-width:180px;justify-self:center!important;margin:0 auto;place-self:center;padding:6px 10px;text-align:center!important}}.trafficTotalBadge strong{{font-size:11px}}.trafficStat{{padding:6px 5px}}.trafficStat strong{{font-size:11px}}}}
@media(max-width:420px){{.trafficPanel{{width:100%}}.trafficControls{{grid-template-columns:repeat(2,minmax(0,1fr))!important;gap:5px}}.trafficStatusField,.trafficPasswordField{{grid-column:auto}}.trafficStatusField .wolMeta{{padding:6px 8px}}.trafficTotalBadge{{min-width:118px;max-width:168px}}}}
#diagnosticBuild,.btn[data-diagnose]{{display:none!important}}
</style>
</head>
<body class="preload-mobile-panels">
<canvas class="seasonalFx" id="seasonalFx" aria-hidden="true" hidden></canvas>
<main class="wrap">
  <section class="top">
    <div class="brand">
      <div class="desktopHeader">
        <div class="desktopHeaderTop">
          <h1 class="appBanner"><span>OpenWrt Remote Hub <span class="appBannerVersion">v106</span></span></h1>
          <div class="routerSearchDock" id="routerSearchDock">
            <button class="routerSearchToggle" id="routerSearchToggle" type="button" aria-expanded="false" aria-controls="routerSearchPanel" data-active="false">
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
          <div class="seasonDock" id="seasonDock" aria-label="Зимние эффекты">
            <button class="routerSearchToggle seasonToggle" id="seasonToggle" type="button" aria-expanded="false" aria-controls="seasonPanel" data-open="false">
              <span class="seasonToggleText">Эффекты</span>
            </button>
            <section class="seasonPanel" id="seasonPanel" aria-label="Зимние эффекты" hidden>
              <div class="seasonCard">
                <div class="seasonLabel">Эффекты Hub</div>
                <div class="seasonSwitch" role="group" aria-label="Выбор эффектов">
                  <button class="seasonBtn" type="button" data-season-mode="off" data-active="true" aria-pressed="true">Выкл</button>
                  <button class="seasonBtn" type="button" data-season-mode="snow" data-active="false" aria-pressed="false">Снег</button>
                  <button class="seasonBtn" type="button" data-season-mode="rain" data-active="false" aria-pressed="false">Дождь</button>
                  <button class="seasonBtn" type="button" data-season-mode="cosmos" data-active="false" aria-pressed="false">Космос</button>
                  <button class="seasonBtn" type="button" data-season-mode="embers" data-active="false" aria-pressed="false">&#1048;&#1089;&#1082;&#1088;&#1099;</button>
                  <button class="seasonBtn" type="button" data-season-mode="orbit" data-active="false" aria-pressed="false">&#1054;&#1088;&#1073;&#1080;&#1090;&#1099;</button>
                  <button class="seasonBtn" type="button" data-season-mode="prism" data-active="false" aria-pressed="false">&#1055;&#1088;&#1080;&#1079;&#1084;&#1072;</button>
                  <button class="seasonBtn" type="button" data-season-mode="pulse" data-active="false" aria-pressed="false">&#1055;&#1091;&#1083;&#1100;&#1089;</button>
                  <button class="seasonBtn" type="button" data-season-mode="laser" data-active="false" aria-pressed="false">&#1051;&#1091;&#1095;&#1080;</button>
                  <button class="seasonBtn" type="button" data-season-mode="aurora" data-active="false" aria-pressed="false">Аврора</button>
                  <button class="seasonBtn" type="button" data-season-mode="matrix" data-active="false" aria-pressed="false">Матрица</button>
                  <button class="seasonBtn" type="button" data-season-mode="nebula" data-active="false" aria-pressed="false">Туманность</button>
                  <button class="seasonBtn" type="button" data-season-mode="fireworks" data-active="false" aria-pressed="false">Фейерверк</button>
                  <button class="seasonBtn" type="button" data-season-mode="vortex" data-active="false" aria-pressed="false">Вихрь</button>
                  <button class="seasonBtn" type="button" data-season-mode="comet" data-active="false" aria-pressed="false">Кометы</button>
                </div>
              </div>
            </section>
          </div>
        </div>
        <div class="desktopHeaderBottom">
          <div class="links">
            <a href="https://t.me/kzolotarev95" target="_blank" rel="noopener noreferrer">Telegram</a>
            <a href="https://github.com/kzolotarev95" target="_blank" rel="noopener noreferrer">GitHub</a>
            <a class="nethavenTop" href="https://t.me/+LZDsQJhUfcNhYWEy" target="_blank" rel="noopener noreferrer">NetHaven VPN</a>
          </div>
          <div class="headerActions">
            <a class="badge" id="vpsTerminalLink" href="/vps-terminal/" target="_blank" rel="noopener noreferrer" {'hidden' if not is_owner else ''}>Терминал VPS</a>
            <button class="badge" id="xrayReload" type="button" {'hidden' if not is_owner else ''}>Обновить Xray CFG</button>
            <button class="badge" id="xrayRestart" type="button" {'hidden' if not is_owner else ''}>Рестарт Xray VPS</button>
            <button class="badge authToggle" id="authToggle" type="button">{safe_username}</button>
            <a class="btn" href="/logout">Выйти</a>
            <div class="authMenu" id="authMenu" hidden>
              <h2>Доступ к Hub</h2>
              <p>Смена логина и пароля входа.</p>
              <div class="authMenuHead">
                <div>
                  <h2>Р”РѕСЃС‚СѓРї Рє Hub</h2>
                  <p>РЎРјРµРЅР° Р»РѕРіРёРЅР° Рё РїР°СЂРѕР»СЏ РІС…РѕРґР°.</p>
                </div>
                <button class="sessionBtn authMenuClose" id="authMenuClose" type="button">Р—Р°РєСЂС‹С‚СЊ</button>
              </div>
              <details class="authGroup" id="securityGroup">
                <summary class="authGroupSummary">
                  <div class="authGroupTitle">
                    <strong id="securityGroupTitle">Безопасность</strong>
                    <span id="securityGroupLead">Пароль, 2FA, Passkey и SSH ED25519 для входа в Hub.</span>
                  </div>
                  <span class="authGroupChevron" aria-hidden="true">⌄</span>
                </summary>
                <div class="authGroupBody">
                  <div class="authSection authPasswordSection">
                    <div class="authSectionHead">
                      <div>
                        <h3 id="passwordSectionTitle">Пароль</h3>
                        <p id="passwordSectionLead">Смена логина и пароля входа в панель Hub.</p>
                      </div>
                    </div>
              <form id="authForm" class="authGrid">
                <input class="wide" name="username" value="{safe_username}" placeholder="Логин" autocomplete="username" required>
                <input name="current_password" type="password" placeholder="Текущий пароль" autocomplete="current-password" required>
                <input name="password" type="password" placeholder="Новый пароль" autocomplete="new-password">
                <input name="password_confirm" type="password" placeholder="Повтор пароля" autocomplete="new-password">
                <button class="primary wide">Сохранить</button>
              </form>
              <div id="authMsg" class="msg" hidden></div>
                  </div>
              <div class="authOverview">
                <div id="authSummary" class="authPills"></div>
              </div>
              <div class="authSection">
                <div class="authSectionHead">
                  <div>
                    <h3>2FA</h3>
                    <p>Р РµР·РµСЂРІРЅР°СЏ РІРµС‚РєР° РґРѕСЃС‚СѓРїР°: РїР°СЂРѕР»СЊ РѕСЃС‚Р°РµС‚СЃСЏ Р·Р°РїР°СЃРЅС‹Рј, Р° TOTP РјРѕР¶РЅРѕ РІРєР»СЋС‡РёС‚СЊ РєР°Рє РІС‚РѕСЂРѕР№ С„Р°РєС‚РѕСЂ.</p>
                  </div>
                  <div class="authSectionState off" id="totpState">2FA off</div>
                </div>
                <div class="authFields">
                  <input id="totpCurrentPassword" class="wide" type="password" placeholder="РўРµРєСѓС‰РёР№ РїР°СЂРѕР»СЊ РґР»СЏ РЅР°СЃС‚СЂРѕР№РєРё 2FA" autocomplete="current-password">
                </div>
                <div class="authActions">
                  <button class="sessionBtn" id="totpSetupBtn" type="button">РЎРѕР·РґР°С‚СЊ СЃРµРєСЂРµС‚ 2FA</button>
                </div>
                <div class="authSecretBox" id="totpSecretBox" hidden>
                  <strong>TOTP secret</strong>
                  <pre class="authSecretValue" id="totpSecretValue"></pre>
                  <strong style="margin-top:10px">otpauth URI</strong>
                  <pre class="authSecretValue" id="totpUriValue"></pre>
                  <div class="authFields" style="margin-top:10px">
                    <input id="totpCode" class="wide" inputmode="numeric" pattern="[0-9]*" autocomplete="one-time-code" placeholder="РљРѕРґ РёР· РїСЂРёР»РѕР¶РµРЅРёСЏ 2FA">
                  </div>
                  <div class="authActions">
                    <button class="sessionBtn" id="totpEnableBtn" type="button">Р’РєР»СЋС‡РёС‚СЊ 2FA</button>
                  </div>
                </div>
                <div class="authFields" style="margin-top:10px">
                  <input id="totpDisableCurrentPassword" type="password" placeholder="РўРµРєСѓС‰РёР№ РїР°СЂРѕР»СЊ РґР»СЏ РѕС‚РєР»СЋС‡РµРЅРёСЏ 2FA" autocomplete="current-password">
                  <input id="totpDisableCode" inputmode="numeric" pattern="[0-9]*" autocomplete="one-time-code" placeholder="РўРµРєСѓС‰РёР№ РєРѕРґ 2FA">
                </div>
                <div class="authActions">
                  <button class="sessionBtn bad" id="totpDisableBtn" type="button">Р’С‹РєР»СЋС‡РёС‚СЊ 2FA</button>
                </div>
              </div>
              <div class="authSection">
                <div class="authSectionHead">
                  <div>
                    <h3>Passkey</h3>
                    <p>РћСЃРЅРѕРІРЅР°СЏ РІРµС‚РєР° Р±РµР· РїР°СЂРѕР»СЏ: Android, Windows Hello, Face ID, Touch ID Рё РґСЂСѓРіРёРµ СЃРёСЃС‚РµРјРЅС‹Рµ РєР»СЋС‡Рё.</p>
                  </div>
                  <div class="authSectionState off" id="passkeySummary">0 passkey</div>
                </div>
                <div class="authFields">
                  <input id="passkeyCurrentPassword" type="password" placeholder="РўРµРєСѓС‰РёР№ РїР°СЂРѕР»СЊ РґР»СЏ passkey" autocomplete="current-password">
                  <input id="passkeyLabel" placeholder="РњРµС‚РєР° РєР»СЋС‡Р°: Pixel 9, Windows Hello">
                </div>
                <div class="authActions">
                  <button class="sessionBtn" id="passkeyRegisterBtn" type="button">Р”РѕР±Р°РІРёС‚СЊ passkey</button>
                </div>
                <div class="authHint" id="passkeyClientHint">Passkey С‚СЂРµР±СѓРµС‚ HTTPS Рё РїРѕРґРґРµСЂР¶РєСѓ WebAuthn РІ Р±СЂР°СѓР·РµСЂРµ.</div>
                <div id="passkeyList" class="authList"></div>
              </div>
              <div class="authSection">
                <div class="authSectionHead">
                  <div>
                    <h3>SSH ED25519</h3>
                    <p>Р’С…РѕРґ РїРѕ SSH ED25519 РєР»СЋС‡Сѓ. Р”РѕР±Р°РІСЊ РїСѓР±Р»РёС‡РЅС‹Р№ РєР»СЋС‡, Р° РЅР° СЌРєСЂР°РЅРµ РІС…РѕРґР° РїРѕС‚РѕРј РїРѕРґРїРёСЃС‹РІР°Р№ challenge.</p>
                  </div>
                  <div class="authSectionState off" id="sshKeySummary">0 keys</div>
                </div>
                <div class="authFields">
                  <input id="sshKeyCurrentPassword" type="password" placeholder="РўРµРєСѓС‰РёР№ РїР°СЂРѕР»СЊ РґР»СЏ SSH РєР»СЋС‡РµР№" autocomplete="current-password">
                  <input id="sshKeyLabel" placeholder="РњРµС‚РєР° РєР»СЋС‡Р°: MacBook, Workstation">
                  <textarea id="sshKeyPublic" class="wide" placeholder="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... user@host"></textarea>
                </div>
                <div class="authActions">
                  <button class="sessionBtn" id="sshKeyAddBtn" type="button">Р”РѕР±Р°РІРёС‚СЊ SSH ED25519</button>
                </div>
                <div class="authHint">РџРѕРґРґРµСЂР¶РёРІР°РµС‚СЃСЏ С‚РѕР»СЊРєРѕ `ssh-ed25519`.</div>
                <div id="sshKeyList" class="authList"></div>
              </div>
                </div>
              </details>
              <details class="authGroup" id="socialGroup">
                <summary class="authGroupSummary">
                  <div class="authGroupTitle">
                    <strong id="socialGroupTitle">Привязанные сервисы</strong>
                    <span id="socialGroupLead">GitHub и VK ID для входа без отдельного пароля приложения. На экране входа показываются только реально привязанные аккаунты.</span>
                  </div>
                  <span class="authGroupChevron" aria-hidden="true">⌄</span>
                </summary>
                <div class="authGroupBody">
                  <div class="authSection">
                    <div class="authSectionHead">
                      <div>
                        <h3>GitHub</h3>
                        <p>Сохрани OAuth Client ID/Secret, затем привяжи тот GitHub-аккаунт, которому разрешён вход в Hub.</p>
                      </div>
                      <div class="authSectionState off" id="githubSummary">Не настроен</div>
                    </div>
                    <div class="authFields">
                      <input id="githubClientId" placeholder="GitHub Client ID" autocomplete="off">
                      <input id="githubClientSecret" type="password" placeholder="GitHub Client Secret" autocomplete="off">
                      <input id="githubCurrentPassword" class="wide" type="password" placeholder="Текущий пароль Hub для сохранения и привязки" autocomplete="current-password">
                    </div>
                    <div class="authActions">
                      <button class="sessionBtn" id="githubSaveBtn" type="button">Сохранить OAuth</button>
                      <button class="sessionBtn" id="githubLinkBtn" type="button">Привязать аккаунт</button>
                    </div>
                    <div class="authHint" id="githubHint">Появится на странице входа только после успешной привязки хотя бы одного GitHub-аккаунта.</div>
                    <div id="githubAccountList" class="authList"></div>
                  </div>
                  <div class="authSection">
                    <div class="authSectionHead">
                      <div>
                        <h3>VK ID</h3>
                        <p>Современный VK ID OAuth 2.1 flow с PKCE: сохрани Client ID, при confidential-приложении добавь Service Token, затем привяжи конкретный VK ID.</p>
                      </div>
                      <div class="authSectionState off" id="vkSummary">Не настроен</div>
                    </div>
                    <div class="authFields">
                      <input id="vkClientId" placeholder="VK ID Client ID" autocomplete="off">
                      <input id="vkClientSecret" type="password" placeholder="VK ID Service Token (optional for public app)" autocomplete="off">
                      <input id="vkCurrentPassword" class="wide" type="password" placeholder="Текущий пароль Hub для сохранения и привязки" autocomplete="current-password">
                    </div>
                    <div class="authActions">
                      <button class="sessionBtn" id="vkSaveBtn" type="button">Сохранить OAuth</button>
                      <button class="sessionBtn" id="vkLinkBtn" type="button">Привязать аккаунт</button>
                    </div>
                    <div class="authHint" id="vkHint">На странице входа будут видны только привязанные VK ID. Для confidential-приложения VK ID во втором поле нужен Service Token.</div>
                    <div id="vkAccountList" class="authList"></div>
                  </div>
                </div>
              </details>
              <details class="authGroup" id="sessionGroup">
                <summary class="authGroupSummary">
                  <div class="authGroupTitle">
                    <strong id="sessionGroupTitle">Управление сессиями</strong>
                    <span id="sessionGroupLead">Активные входы в Hub и быстрое завершение лишних сессий.</span>
                  </div>
                  <span class="authGroupChevron" aria-hidden="true">⌄</span>
                </summary>
                <div class="authGroupBody">
                  <div class="sessionBox">
                <div class="sessionHead">
                  <h3>Управление сессиями</h3>
                  <button class="sessionBtn bad" id="revokeOtherSessions" type="button">Завершить остальные</button>
                </div>
                <div id="sessionList" class="sessionList"></div>
                  </div>
                </div>
              </details>
              <details class="authGroup" id="delegatedUsersGroup">
                <summary class="authGroupSummary">
                  <div class="authGroupTitle">
                    <strong id="delegatedUsersGroupTitle">Ограниченные пользователи</strong>
                    <span id="delegatedUsersGroupLead">Учётки с флагами A, B и G только на выбранные роутеры.</span>
                  </div>
                  <span class="authGroupChevron" aria-hidden="true">⌄</span>
                </summary>
                <div class="authGroupBody">
                  <div class="authSection">
                    <div class="authSectionHead">
                      <div>
                        <h3 id="delegatedUsersSectionTitle">Доступ к роутерам</h3>
                        <p id="delegatedUsersSectionLead">A = только LuCI, B = SSH к роутеру, G = полный доступ к панели этого роутера без VPS/Xray/backup, а также просмотр, добавление и редактирование заметок.</p>
                      </div>
                    </div>
                    <form id="delegatedUserForm" class="authFields">
                      <input type="hidden" name="id" value="">
                      <input class="wide" name="username" placeholder="Логин пользователя" autocomplete="new-password" autocapitalize="off" autocorrect="off" spellcheck="false" required>
                      <label class="wide"><input name="enabled" type="checkbox" checked> Учётка активна</label>
                      <input name="password" type="password" placeholder="Новый пароль" autocomplete="new-password">
                      <input name="password_confirm" type="password" placeholder="Повтори пароль" autocomplete="new-password">
                      <input class="wide" name="current_password" type="password" placeholder="Текущий пароль владельца" autocomplete="new-password" autocapitalize="off" autocorrect="off" spellcheck="false" required>
                      <div class="wide" id="delegatedRouterAccess"></div>
                      <div class="authActions">
                        <button class="sessionBtn" type="submit">Сохранить пользователя</button>
                      </div>
                    </form>
                    <div id="delegatedUserMsg" class="msg" hidden></div>
                    <div id="delegatedUserList" class="authList"></div>
                  </div>
                </div>
              </details>
              <details class="authGroup" id="notifyGroup">
                <summary class="authGroupSummary">
                  <div class="authGroupTitle">
                    <strong id="notifyGroupTitle">Уведомления</strong>
                    <span id="notifyGroupLead">Web Push для входов, смены IP и событий VPS/Hub.</span>
                  </div>
                  <span class="authGroupChevron" aria-hidden="true">⌄</span>
                </summary>
                <div class="authGroupBody">
                  <div class="notifyBox">
                <div class="sessionHead">
                  <h3>Уведомления</h3>
                  <div class="notifyActions">
                    <button class="sessionBtn notifyBtn" id="notifyEnable" type="button">Включить</button>
                    <button class="sessionBtn" id="notifyTest" type="button">Проверить Push</button>
                    <button class="sessionBtn" id="notifyClear" type="button">Очистить</button>
                  </div>
                </div>
                <div class="notifyHint">Web Push для входов в панель, смены IP и запуска VPS/Hub. На iOS включай из приложения Hub с экрана Домой.</div>
                <div id="notifyStatus" class="authHint" hidden></div>
                <div id="notifyList" class="notifyList"></div>
                  </div>
                </div>
              </details>
              <details class="authGroup" id="backupGroup">
                <summary class="authGroupSummary">
                  <div class="authGroupTitle">
                    <strong id="backupGroupTitle">Резервная копия VPS</strong>
                    <span id="backupGroupLead">Backup, restore и перенос Hub на новый VPS.</span>
                  </div>
                  <span class="authGroupChevron" aria-hidden="true">⌄</span>
                </summary>
                <div class="authGroupBody">
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
                  <input id="backupSshPassword" type="password" placeholder="Общий SSH пароль роутеров, если нужен" autocomplete="current-password" autocapitalize="off" autocorrect="off" spellcheck="false">
                  <button class="primary wide" id="backupRestore" type="button">Восстановить из backup</button>
                  <button class="wide" id="backupRewrite" type="button">Перепривязать существующие роутеры</button>
                </div>
                <div id="backupMsg" class="backupMsg" hidden></div>
                  </div>
                </div>
              </details>
            </div>
          </div>
        </div>
      </div>
    </div>
    <div class="mobileSearchDock" id="mobileRouterSearchDock">
      <span class="mobileSearchVersion">v106</span>
      <button class="routerSearchToggle mobilePanelToggle primary" id="mobileRouterSearchToggle" type="button" aria-expanded="false" aria-controls="mobileRouterSearchPanel" data-active="false">
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
    <div class="mobileSearchDock" id="mobileSeasonDock" aria-label="Зимние эффекты">
      <button class="routerSearchToggle seasonToggle mobilePanelToggle primary" id="mobileSeasonToggle" type="button" aria-expanded="false" aria-controls="mobileSeasonPanel" data-open="false">
        <span class="seasonToggleText">Эффекты</span>
      </button>
      <section class="seasonPanel" id="mobileSeasonPanel" aria-label="Зимние эффекты" hidden>
        <div class="seasonCard">
          <div class="seasonLabel">Эффекты Hub</div>
          <div class="seasonSwitch" role="group" aria-label="Выбор эффектов">
            <button class="seasonBtn" type="button" data-season-mode="off" data-active="true" aria-pressed="true">Выкл</button>
            <button class="seasonBtn" type="button" data-season-mode="snow" data-active="false" aria-pressed="false">Снег</button>
            <button class="seasonBtn" type="button" data-season-mode="rain" data-active="false" aria-pressed="false">Дождь</button>
            <button class="seasonBtn" type="button" data-season-mode="cosmos" data-active="false" aria-pressed="false">Космос</button>
            <button class="seasonBtn" type="button" data-season-mode="embers" data-active="false" aria-pressed="false">&#1048;&#1089;&#1082;&#1088;&#1099;</button>
            <button class="seasonBtn" type="button" data-season-mode="orbit" data-active="false" aria-pressed="false">&#1054;&#1088;&#1073;&#1080;&#1090;&#1099;</button>
            <button class="seasonBtn" type="button" data-season-mode="prism" data-active="false" aria-pressed="false">&#1055;&#1088;&#1080;&#1079;&#1084;&#1072;</button>
            <button class="seasonBtn" type="button" data-season-mode="pulse" data-active="false" aria-pressed="false">&#1055;&#1091;&#1083;&#1100;&#1089;</button>
            <button class="seasonBtn" type="button" data-season-mode="laser" data-active="false" aria-pressed="false">&#1051;&#1091;&#1095;&#1080;</button>
            <button class="seasonBtn" type="button" data-season-mode="aurora" data-active="false" aria-pressed="false">Аврора</button>
            <button class="seasonBtn" type="button" data-season-mode="matrix" data-active="false" aria-pressed="false">Матрица</button>
            <button class="seasonBtn" type="button" data-season-mode="nebula" data-active="false" aria-pressed="false">Туманность</button>
            <button class="seasonBtn" type="button" data-season-mode="fireworks" data-active="false" aria-pressed="false">Фейерверк</button>
            <button class="seasonBtn" type="button" data-season-mode="vortex" data-active="false" aria-pressed="false">Вихрь</button>
            <button class="seasonBtn" type="button" data-season-mode="comet" data-active="false" aria-pressed="false">Кометы</button>
          </div>
        </div>
      </section>
    </div>
    <button class="mobilePanelToggle primary" id="hubMenuToggle" type="button" hidden>Открыть меню хаба</button>
    <div id="hubMenuPanelHost"></div>
    <div class="mobileOwnerTools" id="mobileOwnerTools" hidden>
      <a class="badge" href="/vps-terminal/" id="mobileVpsTerminal" target="_blank" rel="noopener noreferrer">Терминал VPS</a>
      <button class="badge" id="mobileXrayReload" type="button">Обновить Xray CFG</button>
      <button class="badge" id="mobileXrayRestart" type="button">Рестарт Xray VPS</button>
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
window.HUB_PUSH_BOOTSTRAP = {push_bootstrap_json};
const APP_CLIENT_VERSION = {json.dumps(SERVICE_WORKER_VERSION)};
const mobileLayoutMq = window.matchMedia('(max-width: 680px)');
const mobileCardsMq = mobileLayoutMq;
const cards = document.getElementById('cards');
const routerStats = document.getElementById('routerStats');
const routerStatsToggle = document.getElementById('routerStatsToggle');
const headerActions = document.querySelector('.headerActions');
const hubMenuToggle = document.getElementById('hubMenuToggle');
const hubMenuPanelHost = document.getElementById('hubMenuPanelHost');
const mobileOwnerTools = document.getElementById('mobileOwnerTools');
const mobileVpsTerminal = document.getElementById('mobileVpsTerminal');
const mobileXrayReload = document.getElementById('mobileXrayReload');
const mobileXrayRestart = document.getElementById('mobileXrayRestart');
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
const seasonDock = document.getElementById('seasonDock');
const seasonToggle = document.getElementById('seasonToggle');
const seasonPanel = document.getElementById('seasonPanel');
const mobileSeasonDock = document.getElementById('mobileSeasonDock');
const mobileSeasonToggle = document.getElementById('mobileSeasonToggle');
const mobileSeasonPanel = document.getElementById('mobileSeasonPanel');
const seasonalFxCanvas = document.getElementById('seasonalFx');
const seasonButtons = Array.from(document.querySelectorAll('[data-season-mode]')).filter(Boolean);
const seasonSwitches = Array.from(document.querySelectorAll('.seasonSwitch')).filter(Boolean);
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
const noteStateByRouter = new Map();
const wolStateByRouter = new Map();
const trafficStateByRouter = new Map();
const trafficAutoRefreshTimers = new Map();
const TRAFFIC_AUTO_REFRESH_MS = 4000;
let pendingRouterViewRestore = 0;
let pendingRouterRender = false;
let pendingWolBlurFlush = 0;
let recentWolPointerActionKey = '';
let recentWolPointerActionTs = 0;
let activeDiagnosticRouterId = '';
let offlineStatsExpanded = false;
let routerSearchQuery = '';
let lastRouterSearchTrigger = null;
const SEASON_EFFECT_KEY = 'owrtRemote:seasonEffectMode';
const SEASON_EFFECT_MODES = new Set(['off', 'snow', 'rain', 'cosmos', 'embers', 'orbit', 'prism', 'pulse', 'laser', 'aurora', 'matrix', 'nebula', 'fireworks', 'vortex', 'comet']);
let seasonEffectMode = 'off';
let seasonalFxCtx = null;
let seasonalFxFrame = 0;
let seasonalFxResizeTimer = 0;
let seasonalFxLastTs = 0;
let seasonalFxParticles = [];
let seasonalFxSize = {{width: 0, height: 0, dpr: 1}};
let seasonalFxPointer = {{x: 0, y: 0, lastX: 0, lastY: 0, targetWind: 0, wind: 0, lastMoveTs: 0, active: false}};
let seasonPanelOpen = false;

const routerSearchToggles = [routerSearchToggle, mobileRouterSearchToggle].filter(Boolean);
const routerSearchPanels = [routerSearchPanel, mobileRouterSearchPanel].filter(Boolean);
const routerSearchInputs = [routerSearchInput, mobileRouterSearchInput].filter(Boolean);
const routerSearchClears = [routerSearchClear, mobileRouterSearchClear].filter(Boolean);
const routerSearchMetas = [routerSearchMeta, mobileRouterSearchMeta].filter(Boolean);
const routerSearchDocks = [routerSearchDock, mobileRouterSearchDock].filter(Boolean);
const seasonToggles = [seasonToggle, mobileSeasonToggle].filter(Boolean);
const seasonPanels = [seasonPanel, mobileSeasonPanel].filter(Boolean);
const seasonDocks = [seasonDock, mobileSeasonDock].filter(Boolean);

function actionPanelId(routerId) {{
  return 'router-actions-' + String(routerId || '');
}}

function wolPanelId(routerId) {{
  return 'router-wol-' + String(routerId || '');
}}

function trafficPanelId(routerId) {{
  return 'router-traffic-' + String(routerId || '');
}}

function notesPanelId(routerId) {{
  return 'router-notes-' + String(routerId || '');
}}

function normalizeRouterNotes(value) {{
  return String(value ?? '').replace(/\\r\\n?/g, '\\n').slice(0, 5000);
}}

function routerNotesPreview(value) {{
  return normalizeRouterNotes(value).trim().replace(/\s+/g, ' ').slice(0, 220);
}}

function syncRouterNotesState(list) {{
  const routers = Array.isArray(list) ? list : [];
  const validIds = new Set(routers.map((router) => String(router && router.id || '')));
  for (const id of Array.from(noteStateByRouter.keys())) {{
    if (!validIds.has(id)) noteStateByRouter.delete(id);
  }}
  routers.forEach((router) => {{
    const key = String(router && router.id || '');
    if (!key) return;
    const saved = normalizeRouterNotes(router && router.notes || '');
    const current = noteStateByRouter.get(key);
    if (!current) {{
      noteStateByRouter.set(key, {{
        open: false,
        saving: false,
        dirty: false,
        draft: saved,
        saved,
        error: '',
        message: ''
      }});
      return;
    }}
    const next = Object.assign({{}}, current, {{saved}});
    if (!current.dirty && !current.saving) next.draft = saved;
    noteStateByRouter.set(key, next);
  }});
}}

function getNoteState(routerId) {{
  const key = String(routerId || '');
  if (!noteStateByRouter.has(key)) {{
    noteStateByRouter.set(key, {{
      open: false,
      saving: false,
      dirty: false,
      draft: '',
      saved: '',
      error: '',
      message: ''
    }});
  }}
  return noteStateByRouter.get(key);
}}

function setNoteState(routerId, patch) {{
  const key = String(routerId || '');
  const current = getNoteState(key);
  const next = Object.assign({{}}, current, patch || {{}});
  next.draft = normalizeRouterNotes(next.draft || '');
  next.saved = normalizeRouterNotes(next.saved || '');
  next.dirty = Boolean(next.dirty);
  next.open = Boolean(next.open);
  next.saving = Boolean(next.saving);
  noteStateByRouter.set(key, next);
  return next;
}}

function replaceRouterInList(router) {{
  const nextRouter = router && typeof router === 'object' ? router : null;
  if (!nextRouter || !nextRouter.id) return;
  const list = Array.isArray(window.ROUTERS) ? window.ROUTERS.slice() : [];
  const index = list.findIndex((item) => String(item && item.id || '') === String(nextRouter.id || ''));
  if (index >= 0) list[index] = Object.assign({{}}, list[index], nextRouter);
  else list.push(nextRouter);
  window.ROUTERS = list;
  syncRouterNotesState(window.ROUTERS);
}}

function clearTrafficAutoRefresh(routerId) {{
  const key = String(routerId || '');
  const timer = trafficAutoRefreshTimers.get(key);
  if (timer) {{
    clearTimeout(timer);
    trafficAutoRefreshTimers.delete(key);
  }}
}}

function clearAllTrafficAutoRefresh() {{
  Array.from(trafficAutoRefreshTimers.keys()).forEach((routerId) => clearTrafficAutoRefresh(routerId));
}}

function shouldAutoRefreshTraffic(routerId) {{
  const key = String(routerId || '');
  const router = selectedRouter(key);
  const state = getTrafficState(key);
  if (!router || !state || !state.open) return false;
  if (document.hidden) return false;
  return true;
}}

function scheduleTrafficAutoRefresh(routerId, delay = TRAFFIC_AUTO_REFRESH_MS) {{
  const key = String(routerId || '');
  clearTrafficAutoRefresh(key);
  if (!shouldAutoRefreshTraffic(key)) return;
  trafficAutoRefreshTimers.set(key, window.setTimeout(async () => {{
    trafficAutoRefreshTimers.delete(key);
    if (!shouldAutoRefreshTraffic(key)) return;
    await loadTrafficClients(key, true, {{silent: true}});
    scheduleTrafficAutoRefresh(key, TRAFFIC_AUTO_REFRESH_MS);
  }}, Math.max(1200, Number(delay) || TRAFFIC_AUTO_REFRESH_MS)));
}}

function syncTrafficAutoRefresh() {{
  const activeIds = new Set();
  for (const [routerId, state] of trafficStateByRouter.entries()) {{
    const key = String(routerId || '');
    if (state && state.open && shouldAutoRefreshTraffic(key)) {{
      activeIds.add(key);
      if (!trafficAutoRefreshTimers.has(key)) scheduleTrafficAutoRefresh(key, TRAFFIC_AUTO_REFRESH_MS);
    }}
  }}
  Array.from(trafficAutoRefreshTimers.keys()).forEach((routerId) => {{
    if (!activeIds.has(String(routerId || ''))) clearTrafficAutoRefresh(routerId);
  }});
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

function getTrafficState(routerId) {{
  const key = String(routerId || '');
  if (!trafficStateByRouter.has(key)) {{
    trafficStateByRouter.set(key, {{
      open: false,
      loaded: false,
      loading: false,
      error: '',
      message: '',
      sshPassword: loadWolPassword(key),
      clients: [],
      trafficSource: '',
      trafficSupported: false,
      flowHits: 0
    }});
  }}
  return trafficStateByRouter.get(key);
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

function setTrafficState(routerId, patch) {{
  const key = String(routerId || '');
  const current = getTrafficState(key);
  const next = Object.assign({{}}, current, patch || {{}});
  next.clients = Array.isArray(next.clients) ? next.clients : [];
  trafficStateByRouter.set(key, next);
  return next;
}}

function hasOpenWolPicker() {{
  for (const state of wolStateByRouter.values()) {{
    if (state && state.open && state.pickerOpen) return true;
  }}
  return false;
}}

function activeWolInteractiveField() {{
  const active = document.activeElement;
  return active && typeof active.matches === 'function' && active.matches('[data-wol-password],[data-wol-select],[data-traffic-password],[data-router-notes-input]') ? active : null;
}}

function shouldDeferRouterRender() {{
  return Boolean(activeWolInteractiveField());
}}

function clearPendingWolBlurFlush() {{
  if (!pendingWolBlurFlush) return;
  clearTimeout(pendingWolBlurFlush);
  pendingWolBlurFlush = 0;
}}

function scheduleWolBlurFlush() {{
  clearPendingWolBlurFlush();
  pendingWolBlurFlush = window.setTimeout(() => {{
    pendingWolBlurFlush = 0;
    if (shouldDeferRouterRender()) return;
    flushDeferredRouterRender();
  }}, 0);
}}

function wolPointerActionKey(kind, routerId, extra = '') {{
  return [String(kind || ''), String(routerId || ''), String(extra || '')].join('|');
}}

function rememberWolPointerAction(kind, routerId, extra = '') {{
  recentWolPointerActionKey = wolPointerActionKey(kind, routerId, extra);
  recentWolPointerActionTs = Date.now();
}}

function consumeWolPointerAction(kind, routerId, extra = '') {{
  const key = wolPointerActionKey(kind, routerId, extra);
  if (recentWolPointerActionKey !== key) return false;
  if ((Date.now() - recentWolPointerActionTs) > 900) return false;
  recentWolPointerActionKey = '';
  recentWolPointerActionTs = 0;
  return true;
}}

function flushDeferredRouterRender() {{
  if (!pendingRouterRender) return;
  if (shouldDeferRouterRender()) return;
  pendingRouterRender = false;
  renderRouterView();
}}

function requestRouterRender() {{
  if (shouldDeferRouterRender()) {{
    pendingRouterRender = true;
    return;
  }}
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

function formatTrafficBytes(value) {{
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let size = bytes;
  let idx = 0;
  while (size >= 1024 && idx < units.length - 1) {{
    size /= 1024;
    idx += 1;
  }}
  const digits = size >= 100 || idx === 0 ? 0 : (size >= 10 ? 1 : 2);
  return size.toFixed(digits) + ' ' + units[idx];
}}

function trafficClientTitle(client) {{
  if (!client) return 'Клиент';
  return client.name || client.ip || client.static_ip || client.mac || 'Клиент';
}}

function trafficClientMeta(client) {{
  if (!client) return '';
  const parts = [];
  if (client.ip) parts.push('IP ' + client.ip);
  if (client.static_ip) parts.push('Static ' + client.static_ip);
  if (client.mac) parts.push(client.mac);
  if (client.iface) parts.push(client.iface);
  return parts.join(' | ');
}}

function trafficClientTags(client) {{
  return [];
}}

function trafficMetaText(state) {{
  if (!state) return '';
  if (state.loading) return 'Собираю клиентов, статику адресов и трафик с роутера...';
  if (state.error) return state.error;
  if (state.message) return state.message;
  const count = Array.isArray(state.clients) ? state.clients.length : 0;
  if (state.loaded && !count) return 'Клиенты не найдены. Проверь DHCP leases, ARP или SSH-доступ к роутеру.';
  if (count && state.trafficSupported) return `Список обновлен: ${{count}} клиентов.`;
  if (count) return `Клиентов: ${{count}} · адреса и статика видны, но счётчики трафика недоступны.`;
  return 'Открой панель, чтобы получить список клиентов и их трафик.';
}}

function renderTrafficSummary(state, clients) {{
  const count = Array.isArray(clients) ? clients.length : 0;
  const chips = [`<span class="trafficSummaryChip accent">${{count}} клиентов</span>`];
  if (state && state.loaded && !state.trafficSupported) {{
    chips.push('<span class="trafficSummaryChip muted">Без счетчиков трафика</span>');
  }}
  return `<div class="trafficSummary">${{chips.join('')}}</div>`;
}}

function renderTrafficPanel(router) {{
  const state = getTrafficState(router.id);
  const panelId = trafficPanelId(router.id);
  const clients = Array.isArray(state.clients) ? state.clients : [];
  const metaClass = state.error ? 'wolMeta bad' : 'wolMeta';
  const rows = clients.length
    ? clients.map((client) => {{
        const tags = trafficClientTags(client).map((tag) => `<span class="trafficTag">${{escapeHtml(tag)}}</span>`).join('');
        return `<div class="trafficRow">
          <div class="trafficRowTop">
            <div class="trafficIdentity">
              <div class="trafficName">${{escapeHtml(trafficClientTitle(client))}}</div>
              <div class="trafficMetaLine">${{escapeHtml(trafficClientMeta(client) || 'Без IP/MAC данных')}}</div>
            </div>
            <div class="trafficTotalBadge">
              <span>Всего</span>
              <strong>${{escapeHtml(formatTrafficBytes(client.total_bytes))}}</strong>
            </div>
          </div>
          ${{tags ? `<div class="trafficTagRow">${{tags}}</div>` : ''}}
          <div class="trafficStats">
            <div class="trafficStat"><span>Вход</span><strong>${{escapeHtml(formatTrafficBytes(client.rx_bytes))}}</strong></div>
            <div class="trafficStat"><span>Выход</span><strong>${{escapeHtml(formatTrafficBytes(client.tx_bytes))}}</strong></div>
            <div class="trafficStat trafficStatTotal"><span>Всего</span><strong>${{escapeHtml(formatTrafficBytes(client.total_bytes))}}</strong></div>
          </div>
        </div>`;
      }}).join('')
    : '<div class="wolPickerEmpty">Клиенты пока не найдены.</div>';
  return `<div class="trafficPanel" id="${{escapeAttr(panelId)}}"${{state.open ? '' : ' hidden'}}>
    <div class="wolHeader">
      <h3 class="wolTitle">Клиенты Traffic</h3>
    </div>
    <div class="trafficControls">
      <label class="wolField trafficPasswordField">
        <span>SSH пароль</span>
        <input type="password" data-traffic-password="${{escapeAttr(router.id)}}" value="${{escapeAttr(state.sshPassword || '')}}" placeholder="Если SSH по паролю" autocomplete="current-password" autocapitalize="off" autocorrect="off" spellcheck="false" inputmode="text" enterkeyhint="done"${{state.loading ? ' disabled' : ''}}>
      </label>
      <div class="wolField trafficStatusField">
        <span>Статус</span>
        <div class="${{metaClass}}">${{escapeHtml(trafficMetaText(state))}}</div>
      </div>
      <button class="btn trafficActionBtn" type="button" data-traffic-refresh="${{escapeAttr(router.id)}}"${{state.loading ? ' disabled' : ''}}>Обновить</button>
      <button class="btn trafficDangerBtn trafficActionBtn" type="button" data-traffic-reset="${{escapeAttr(router.id)}}" title="Сбросить трафик"${{state.loading ? ' disabled' : ''}}>Сброс</button>
    </div>
    ${{renderTrafficSummary(state, clients)}}
    <div class="trafficViewport" data-traffic-viewport="${{escapeAttr(router.id)}}">
      <div class="trafficList">${{rows}}</div>
    </div>
  </div>`;
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
        <input type="password" data-wol-password="${{escapeAttr(router.id)}}" value="${{escapeAttr(state.sshPassword || '')}}" placeholder="Если SSH по паролю" autocomplete="current-password" autocapitalize="off" autocorrect="off" spellcheck="false" inputmode="text" enterkeyhint="done"${{state.loading || state.waking ? ' disabled' : ''}}>
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
      <div class="wolField wolDeviceField">
        <span>Устройство</span>
        <button class="wolPickerToggle" type="button" data-wol-picker-toggle="${{escapeAttr(router.id)}}" aria-expanded="${{state.pickerOpen ? 'true' : 'false'}}"${{state.loading || state.waking ? ' disabled' : ''}}>
          <span class="wolPickerValue">
            <strong>${{escapeHtml(pickerTitle)}}</strong>
            <small>${{escapeHtml(pickerMeta)}}</small>
          </span>
          <span class="wolPickerChevron">${{state.pickerOpen ? '&#9650;' : '&#9660;'}}</span>
        </button>
        ${{state.pickerOpen ? `<div class="wolPickerList">${{deviceList}}</div>` : ''}}
      </div>
      <label class="wolField">
        <span>SSH пароль</span>
        <input type="password" data-wol-password="${{escapeAttr(router.id)}}" value="${{escapeAttr(state.sshPassword || '')}}" placeholder="Если SSH по паролю" autocomplete="current-password" autocapitalize="off" autocorrect="off" spellcheck="false" inputmode="text" enterkeyhint="done"${{state.loading || state.waking ? ' disabled' : ''}}>
      </label>
      <button class="btn" type="button" data-wol-refresh="${{escapeAttr(router.id)}}"${{state.loading || state.waking ? ' disabled' : ''}}>Обновить</button>
      <button class="btn primary" type="button" data-wol-send="${{escapeAttr(router.id)}}"${{wakeDisabled ? ' disabled' : ''}}>Разбудить</button>
    </div>
    <div class="${{metaClass}}">${{escapeHtml(wolMetaText(state))}}</div>
  </div>`;
}}

function renderWolPanelResponsive(router) {{
  return mobileLayoutMq.matches ? renderWolPanel(router) : renderWolPanelRich(router);
}}

function normalizeRouterSearch(value) {{
  return String(value || '').toLowerCase().trim().replace(/\\s+/g, ' ');
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

function routerSearchPanelForInput(input) {{
  if (!input) return null;
  if (input === mobileRouterSearchInput) return mobileRouterSearchPanel;
  if (input === routerSearchInput) return routerSearchPanel;
  return input.closest ? input.closest('.routerSearchPanel') : null;
}}

function isRouterSearchInputInteractive(input) {{
  if (!input) return false;
  if (document.activeElement === input) return true;
  const panel = routerSearchPanelForInput(input);
  return !!(panel && !panel.hidden);
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
  if (!open) {{
    routerSearchInputs.forEach((input) => {{
      if (document.activeElement === input) input.blur();
    }});
  }}
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
    router && router.status && router.status.board,
    router && router.notes_preview,
    router && router.notes
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

function loadSeasonEffectMode() {{
  try {{
    const value = localStorage.getItem(SEASON_EFFECT_KEY);
    return SEASON_EFFECT_MODES.has(value) ? value : 'off';
  }} catch (e) {{
    return 'off';
  }}
}}

function saveSeasonEffectMode(mode) {{
  try {{
    localStorage.setItem(SEASON_EFFECT_KEY, mode);
  }} catch (e) {{}}
}}

function syncSeasonButtons() {{
  seasonButtons.forEach((button) => {{
    const active = button.dataset.seasonMode === seasonEffectMode;
    button.dataset.active = active ? 'true' : 'false';
    button.setAttribute('aria-pressed', active ? 'true' : 'false');
  }});
}}

function syncSeasonPanelToggleState() {{
  seasonToggles.forEach((toggle) => {{
    const open = seasonPanelOpen;
    toggle.dataset.open = open ? 'true' : 'false';
    toggle.dataset.active = open ? 'true' : 'false';
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    const label = toggle.querySelector('.seasonToggleText');
    if (label) label.textContent = 'Эффекты';
  }});
}}

function setSeasonPanelOpen(nextOpen) {{
  seasonPanelOpen = !!nextOpen;
  seasonPanels.forEach((panel) => {{
    panel.hidden = !seasonPanelOpen;
  }});
  syncSeasonPanelToggleState();
}}

function seasonFxResetCanvas() {{
  if (!seasonalFxCanvas) return;
  seasonalFxCtx = seasonalFxCanvas.getContext('2d');
  if (!seasonalFxCtx) return;
  const rect = seasonalFxCanvas.getBoundingClientRect();
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.max(1, Math.round(rect.width || window.innerWidth || 1));
  const height = Math.max(1, Math.round(rect.height || window.innerHeight || 1));
  seasonalFxCanvas.width = Math.max(1, Math.round(width * dpr));
  seasonalFxCanvas.height = Math.max(1, Math.round(height * dpr));
  seasonalFxCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
  seasonalFxSize = {{width, height, dpr}};
  if (!seasonalFxPointer.active) {{
    seasonalFxPointer.x = width * .5;
    seasonalFxPointer.y = height * .44;
    seasonalFxPointer.lastX = seasonalFxPointer.x;
    seasonalFxPointer.lastY = seasonalFxPointer.y;
  }}
}}

function seasonFxSpawn(mode, randomY = false) {{
  const width = seasonalFxSize.width || window.innerWidth || 1;
  const height = seasonalFxSize.height || window.innerHeight || 1;
  if (mode === 'snow') {{
    return {{
      x: Math.random() * width,
      y: randomY ? Math.random() * height : -Math.random() * height,
      r: .8 + Math.random() * 1.1,
      vy: .014 + Math.random() * .022,
      vx: (Math.random() - .5) * .02,
      drift: Math.random() * Math.PI * 2,
      driftSpeed: .00045 + Math.random() * .0009,
      alpha: .52 + Math.random() * .26
    }};
  }}
  if (mode === 'cosmos') {{
    const angle = Math.random() * Math.PI * 2;
    const distance = 20 + Math.random() * Math.max(width, height) * .28;
    const speed = .10 + Math.random() * .18;
    return {{
      x: width * .5 + Math.cos(angle) * distance,
      y: height * .5 + Math.sin(angle) * distance,
      vx: Math.cos(angle) * speed,
      vy: Math.sin(angle) * speed,
      size: .7 + Math.random() * 1.8,
      tail: 10 + Math.random() * 18,
      twinkle: Math.random() * Math.PI * 2,
      twinkleSpeed: .006 + Math.random() * .01,
      alpha: .45 + Math.random() * .42
    }};
  }}
  if (mode === 'embers') {{
    return {{
      x: Math.random() * width,
      y: randomY ? Math.random() * height : height + Math.random() * height * .18,
      vx: (Math.random() - .5) * .045,
      vy: -(.022 + Math.random() * .05),
      size: 1.1 + Math.random() * 2.4,
      hue: 18 + Math.random() * 26,
      alpha: .22 + Math.random() * .26,
      sway: Math.random() * Math.PI * 2,
      swaySpeed: .0015 + Math.random() * .0024
    }};
  }}
  if (mode === 'orbit') {{
    const centerX = width * .5;
    const centerY = height * .42;
    return {{
      angle: Math.random() * Math.PI * 2,
      radius: 20 + Math.random() * Math.min(width, height) * .22,
      radiusSwing: 6 + Math.random() * 16,
      radiusDrift: Math.random() * Math.PI * 2,
      radiusSpeed: .0014 + Math.random() * .0022,
      speed: (Math.random() > .5 ? 1 : -1) * (.0012 + Math.random() * .0024),
      size: .9 + Math.random() * 2.2,
      alpha: .22 + Math.random() * .34,
      hue: 182 + Math.random() * 110,
      lane: .72 + Math.random() * .7,
      x: centerX,
      y: centerY
    }};
  }}
  if (mode === 'prism') {{
    return {{
      x: Math.random() * width,
      y: randomY ? Math.random() * height : (Math.random() * height * .2),
      vx: (Math.random() - .5) * .075,
      vy: (Math.random() - .5) * .075,
      size: 3.8 + Math.random() * 8.8,
      rot: Math.random() * Math.PI * 2,
      rotSpeed: (Math.random() - .5) * .006,
      hue: Math.random() * 360,
      alpha: .26 + Math.random() * .28
    }};
  }}
  if (mode === 'pulse') {{
    return {{
      angle: Math.random() * Math.PI * 2,
      radius: Math.random() * 26,
      radiusSpeed: .035 + Math.random() * .065,
      orbit: Math.random() * Math.PI * 2,
      orbitSpeed: (Math.random() - .5) * .0034,
      size: 4 + Math.random() * 16,
      hue: 182 + Math.random() * 150,
      alpha: .18 + Math.random() * .26,
      phase: Math.random() * Math.PI * 2,
      phaseSpeed: .003 + Math.random() * .0045
    }};
  }}
  if (mode === 'laser') {{
    const side = Math.random() > .5 ? 1 : -1;
    return {{
      x: side > 0 ? -40 - Math.random() * width * .18 : width + 40 + Math.random() * width * .18,
      y: Math.random() * height,
      vx: side > 0 ? (.24 + Math.random() * .26) : -(.24 + Math.random() * .26),
      vy: (Math.random() - .5) * .065,
      len: 20 + Math.random() * 42,
      width: .8 + Math.random() * 1.5,
      hue: 190 + Math.random() * 140,
      alpha: .16 + Math.random() * .26,
      pulse: Math.random() * Math.PI * 2,
      pulseSpeed: .003 + Math.random() * .004
    }};
  }}
  if (mode === 'aurora') {{
    return {{
      x: Math.random() * width,
      y: height * (.08 + Math.random() * .34),
      phase: Math.random() * Math.PI * 2,
      phaseSpeed: .0008 + Math.random() * .0016,
      wave: 24 + Math.random() * 88,
      waveSpeed: .0002 + Math.random() * .0005,
      sway: Math.random() * Math.PI * 2,
      swaySpeed: .0004 + Math.random() * .001,
      thickness: 10 + Math.random() * 18,
      hue: 155 + Math.random() * 70,
      alpha: .12 + Math.random() * .14,
      vx: (Math.random() - .5) * .02,
      vy: (Math.random() - .5) * .01
    }};
  }}
  if (mode === 'matrix') {{
    const columns = Math.max(2, Math.floor(width / 16));
    const column = Math.floor(Math.random() * columns);
    const columnWidth = width / columns;
    return {{
      x: column * columnWidth + columnWidth * .5,
      y: randomY ? Math.random() * height : -Math.random() * height,
      vy: 4.6 + Math.random() * 6.8,
      size: 11 + Math.random() * 9,
      alpha: .38 + Math.random() * .42,
      glyph: seasonFxRandomGlyph(),
      glyphSpeed: .003 + Math.random() * .006,
      trail: 18 + Math.random() * 30,
      trailAlpha: .06 + Math.random() * .08,
      hue: 120 + Math.random() * 40
    }};
  }}
  if (mode === 'nebula') {{
    const angle = Math.random() * Math.PI * 2;
    const distance = Math.random() * Math.min(width, height) * .34;
    return {{
      x: width * .5 + Math.cos(angle) * distance,
      y: height * .5 + Math.sin(angle) * distance * .72,
      vx: (Math.random() - .5) * .075,
      vy: (Math.random() - .5) * .065,
      size: 70 + Math.random() * 130,
      hue: 225 + Math.random() * 90,
      alpha: .06 + Math.random() * .08,
      drift: Math.random() * Math.PI * 2,
      driftSpeed: .0003 + Math.random() * .0008,
      pulse: Math.random() * Math.PI * 2
    }};
  }}
  if (mode === 'fireworks') {{
    const fromLeft = Math.random() > .5;
    const startX = fromLeft ? -60 - Math.random() * width * .2 : width + 60 + Math.random() * width * .2;
    const startY = Math.random() * height * .82;
    const speed = 1.8 + Math.random() * 3.4;
    const angle = fromLeft ? (-.26 + Math.random() * .38) : (Math.PI - .26 + Math.random() * .38);
    return {{
      x: startX,
      y: startY,
      vx: Math.cos(angle) * speed,
      vy: Math.sin(angle) * speed,
      gravity: .022 + Math.random() * .028,
      drag: .985 + Math.random() * .009,
      life: .4 + Math.random() * 1.2,
      size: 1.1 + Math.random() * 2.4,
      hue: Math.random() * 360,
      alpha: .34 + Math.random() * .42,
      sparkle: Math.random() * Math.PI * 2,
      sparkleSpeed: .03 + Math.random() * .06,
      trail: 8 + Math.random() * 18
    }};
  }}
  if (mode === 'vortex') {{
    const angle = Math.random() * Math.PI * 2;
    return {{
      x: width * .5,
      y: height * .5,
      angle,
      radius: 18 + Math.random() * Math.min(width, height) * .42,
      radiusSpeed: .012 + Math.random() * .018,
      spin: (Math.random() > .5 ? 1 : -1) * (.002 + Math.random() * .0035),
      wobble: Math.random() * Math.PI * 2,
      wobbleSpeed: .002 + Math.random() * .004,
      lane: .38 + Math.random() * .76,
      size: 1.2 + Math.random() * 3.4,
      hue: 190 + Math.random() * 130,
      alpha: .22 + Math.random() * .28
    }};
  }}
  if (mode === 'comet') {{
    const fromLeft = Math.random() > .5;
    const startX = fromLeft ? -60 - Math.random() * width * .2 : width + 60 + Math.random() * width * .2;
    const startY = Math.random() * height * .82;
    const speed = 1.5 + Math.random() * 2.6;
    const angle = fromLeft ? (-.25 + Math.random() * .35) : (Math.PI - .25 + Math.random() * .35);
    return {{
      x: startX,
      y: startY,
      vx: Math.cos(angle) * speed,
      vy: Math.sin(angle) * speed,
      len: 44 + Math.random() * 88,
      width: 1 + Math.random() * 2.8,
      hue: 190 + Math.random() * 120,
      alpha: .2 + Math.random() * .24,
      wobble: Math.random() * Math.PI * 2,
      wobbleSpeed: .006 + Math.random() * .01
    }};
  }}
  return {{
    x: Math.random() * width,
    y: randomY ? Math.random() * height : -Math.random() * height,
    len: 8 + Math.random() * 10,
    vy: .12 + Math.random() * .12,
    vx: -(0.028 + Math.random() * .055),
    width: .55 + Math.random() * .55,
    alpha: .14 + Math.random() * .16
  }};
}}

function seasonFxRandomGlyph() {{
  const glyphs = '01<>[]{{}}#@*+=%$';
  return glyphs[Math.floor(Math.random() * glyphs.length)];
}}

function seasonFxParticleCountForMode(mode, area, width, height) {{
  if (mode === 'snow') return Math.min(560, Math.max(180, Math.round(area / 2100)));
  if (mode === 'cosmos') return Math.min(420, Math.max(160, Math.round(area / 2600)));
  if (mode === 'embers') return Math.min(280, Math.max(90, Math.round(area / 6200)));
  if (mode === 'orbit') return Math.min(160, Math.max(54, Math.round(area / 10800)));
  if (mode === 'prism') return Math.min(240, Math.max(84, Math.round(area / 7200)));
  if (mode === 'pulse') return Math.min(110, Math.max(36, Math.round(area / 16000)));
  if (mode === 'laser') return Math.min(180, Math.max(68, Math.round(area / 9200)));
  if (mode === 'aurora') return Math.min(12, Math.max(6, Math.round(width / 180)));
  if (mode === 'matrix') return Math.min(96, Math.max(54, Math.round(width / 18)));
  if (mode === 'nebula') return Math.min(24, Math.max(14, Math.round(area / 120000)));
  if (mode === 'fireworks') return Math.min(220, Math.max(120, Math.round(area / 5200)));
  if (mode === 'vortex') return Math.min(180, Math.max(80, Math.round(area / 9000)));
  if (mode === 'comet') return Math.min(150, Math.max(54, Math.round(area / 11000)));
  return Math.min(360, Math.max(140, Math.round(area / 3200)));
}}

function seasonFxBuildParticles() {{
  if (seasonEffectMode === 'off') {{
    seasonalFxParticles = [];
    return;
  }}
  const width = seasonalFxSize.width || window.innerWidth || 1;
  const height = seasonalFxSize.height || window.innerHeight || 1;
  const area = Math.max(1, width * height);
  const count = seasonFxParticleCountForMode(seasonEffectMode, area, width, height);
  seasonalFxParticles = Array.from({{length: count}}, () => seasonFxSpawn(seasonEffectMode, true));
}}

function seasonFxStop() {{
  if (seasonalFxFrame) {{
    cancelAnimationFrame(seasonalFxFrame);
    seasonalFxFrame = 0;
  }}
  seasonalFxLastTs = 0;
  seasonalFxPointer.targetWind = 0;
  seasonalFxPointer.wind = 0;
  if (seasonalFxCtx && seasonalFxCanvas) {{
    seasonalFxCtx.clearRect(0, 0, seasonalFxSize.width, seasonalFxSize.height);
  }}
}}

function seasonFxHandlePointerMove(ev) {{
  if (!seasonalFxCanvas) return;
  const width = seasonalFxSize.width || window.innerWidth || 1;
  const height = seasonalFxSize.height || window.innerHeight || 1;
  const x = Math.max(0, Math.min(width, Number(ev.clientX) || 0));
  const y = Math.max(0, Math.min(height, Number(ev.clientY) || 0));
  const deltaX = seasonalFxPointer.lastMoveTs ? (x - seasonalFxPointer.lastX) : 0;
  const centerBias = ((x / width) - .5) * .03;
  const motionBias = Math.max(-.05, Math.min(.05, deltaX * .0016));
  seasonalFxPointer.x = x;
  seasonalFxPointer.y = y;
  seasonalFxPointer.lastX = x;
  seasonalFxPointer.lastY = y;
  seasonalFxPointer.lastMoveTs = Date.now();
  seasonalFxPointer.active = true;
  seasonalFxPointer.targetWind = centerBias + motionBias;
}}

function seasonFxHandlePointerLeave() {{
  seasonalFxPointer.active = false;
  seasonalFxPointer.targetWind = 0;
}}

function seasonFxStep(ts) {{
  if (!seasonalFxCanvas || !seasonalFxCtx || seasonEffectMode === 'off') return;
  if (!seasonalFxLastTs) seasonalFxLastTs = ts;
  const dt = Math.min(40, Math.max(1, ts - seasonalFxLastTs));
  seasonalFxLastTs = ts;
  const {{width, height}} = seasonalFxSize;
  if (!seasonalFxPointer.active || (Date.now() - seasonalFxPointer.lastMoveTs) > 160) {{
    seasonalFxPointer.targetWind *= .92;
    if (Math.abs(seasonalFxPointer.targetWind) < .0008) seasonalFxPointer.targetWind = 0;
  }}
  seasonalFxPointer.wind += (seasonalFxPointer.targetWind - seasonalFxPointer.wind) * Math.min(.18, dt / 140);
  seasonalFxCtx.clearRect(0, 0, width, height);
  seasonalFxCtx.globalCompositeOperation = 'source-over';
  seasonalFxCtx.shadowBlur = 0;
  seasonalFxCtx.shadowColor = 'rgba(0,0,0,0)';
  if (seasonEffectMode === 'snow') {{
    seasonalFxCtx.fillStyle = 'rgba(255,255,255,.8)';
    for (const p of seasonalFxParticles) {{
      p.drift += p.driftSpeed * dt;
      p.y += p.vy * dt;
      p.x += (p.vx + seasonalFxPointer.wind * (.75 + p.r * .22)) * dt + Math.sin(p.drift) * .013 * dt;
      if (p.y > height + 14) {{
        Object.assign(p, seasonFxSpawn('snow'));
        p.y = -12 - Math.random() * 40;
      }}
      if (p.x < -20) p.x = width + 20;
      if (p.x > width + 20) p.x = -20;
      seasonalFxCtx.beginPath();
      seasonalFxCtx.fillStyle = `rgba(255,255,255,${{p.alpha}})`;
      seasonalFxCtx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      seasonalFxCtx.fill();
    }}
  }} else if (seasonEffectMode === 'rain') {{
    seasonalFxCtx.lineCap = 'round';
    for (const p of seasonalFxParticles) {{
      p.y += p.vy * dt;
      const rainWind = seasonalFxPointer.wind * .42;
      p.x += (p.vx + rainWind) * dt;
      if (p.y > height + p.len + 20) {{
        Object.assign(p, seasonFxSpawn('rain'));
        p.y = -20 - Math.random() * height * .2;
      }}
      if (p.x < -40) p.x = width + 40;
      if (p.x > width + 40) p.x = -40;
      seasonalFxCtx.strokeStyle = `rgba(191,229,255,${{p.alpha}})`;
      seasonalFxCtx.lineWidth = p.width;
      seasonalFxCtx.beginPath();
      seasonalFxCtx.moveTo(p.x, p.y);
      seasonalFxCtx.lineTo(p.x + (p.vx + rainWind * .7) * 18, p.y + p.len);
      seasonalFxCtx.stroke();
    }}
  }} else if (seasonEffectMode === 'cosmos') {{
    seasonalFxCtx.lineCap = 'round';
    seasonalFxCtx.lineJoin = 'round';
    seasonalFxCtx.fillStyle = 'rgba(255,255,255,.9)';
    const centerX = width * .5;
    const centerY = height * .5;
    for (const p of seasonalFxParticles) {{
      p.twinkle += p.twinkleSpeed * dt;
      const driftX = seasonalFxPointer.wind * (18 + p.size * 6);
      const driftY = Math.sin(p.twinkle) * .15;
      p.x += (p.vx + driftX) * dt;
      p.y += (p.vy + driftY) * dt;
      const dx = p.x - centerX;
      const dy = p.y - centerY;
      const dist = Math.hypot(dx, dy);
      if (dist > Math.max(width, height) * .8) {{
        Object.assign(p, seasonFxSpawn('cosmos'));
      }}
      const alpha = Math.max(.12, Math.min(.95, p.alpha + Math.sin(p.twinkle) * .12));
      seasonalFxCtx.strokeStyle = `rgba(171,205,255,${{alpha * .34}})`;
      seasonalFxCtx.lineWidth = Math.max(.4, p.size * .55);
      seasonalFxCtx.beginPath();
      seasonalFxCtx.moveTo(p.x, p.y);
      seasonalFxCtx.lineTo(p.x - (p.vx * p.tail + seasonalFxPointer.wind * 4), p.y - (p.vy * p.tail));
      seasonalFxCtx.stroke();
      seasonalFxCtx.beginPath();
      seasonalFxCtx.fillStyle = `rgba(255,255,255,${{alpha}})`;
      seasonalFxCtx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
      seasonalFxCtx.fill();
    }}
  }} else if (seasonEffectMode === 'embers') {{
    seasonalFxCtx.lineCap = 'round';
    seasonalFxCtx.lineJoin = 'round';
    const focusX = seasonalFxPointer.active ? seasonalFxPointer.x : width * .5;
    const focusY = seasonalFxPointer.active ? seasonalFxPointer.y : height * .74;
    const influenceRadius = Math.min(width, height) * .34 + 70;
    for (const p of seasonalFxParticles) {{
      const prevX = p.x;
      const prevY = p.y;
      p.sway += p.swaySpeed * dt;
      if (seasonalFxPointer.active) {{
        const dx = focusX - p.x;
        const dy = focusY - p.y;
        const dist = Math.max(1, Math.hypot(dx, dy));
        const pull = Math.max(0, 1 - dist / influenceRadius);
        p.vx += ((dx / dist) * pull * .0058 + seasonalFxPointer.wind * .04) * dt;
        p.vy += ((dy / dist) * pull * .0041 - .0002) * dt;
      }} else {{
        p.vx += seasonalFxPointer.wind * .015 * dt;
        p.vy -= .00038 * dt;
      }}
      p.vx *= .988;
      p.vy *= .988;
      p.x += (p.vx + Math.sin(p.sway) * .018) * dt;
      p.y += p.vy * dt;
      if (p.y < -34 || p.x < -80 || p.x > width + 80 || p.y > height + 80) {{
        Object.assign(p, seasonFxSpawn('embers'));
        p.y = height + Math.random() * 30;
        p.x = seasonalFxPointer.active ? focusX + (Math.random() - .5) * 120 : Math.random() * width;
      }}
      const glow = seasonalFxPointer.active
        ? Math.max(0, 1 - Math.hypot(focusX - p.x, focusY - p.y) / 150)
        : 0;
      const alpha = Math.min(.96, p.alpha + glow * .4 + Math.abs(Math.sin(p.sway)) * .08);
      seasonalFxCtx.strokeStyle = `hsla(${{p.hue}},100%,72%,${{alpha * .56}})`;
      seasonalFxCtx.lineWidth = Math.max(.7, p.size * .58);
      seasonalFxCtx.beginPath();
      seasonalFxCtx.moveTo(prevX, prevY);
      seasonalFxCtx.lineTo(p.x, p.y);
      seasonalFxCtx.stroke();
      seasonalFxCtx.fillStyle = `hsla(${{p.hue + 8}},100%,66%,${{alpha}})`;
      seasonalFxCtx.beginPath();
      seasonalFxCtx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
      seasonalFxCtx.fill();
    }}
  }} else if (seasonEffectMode === 'orbit') {{
    seasonalFxCtx.lineCap = 'round';
    seasonalFxCtx.lineJoin = 'round';
    const centerX = (seasonalFxPointer.active ? seasonalFxPointer.x : width * .5) + seasonalFxPointer.wind * width * .06;
    const centerY = seasonalFxPointer.active ? seasonalFxPointer.y : height * .42;
    const core = seasonalFxCtx.createRadialGradient(centerX, centerY, 0, centerX, centerY, Math.min(width, height) * .16);
    core.addColorStop(0, 'rgba(34,211,238,.14)');
    core.addColorStop(.45, 'rgba(168,85,247,.08)');
    core.addColorStop(1, 'rgba(15,23,42,0)');
    seasonalFxCtx.fillStyle = core;
    seasonalFxCtx.beginPath();
    seasonalFxCtx.arc(centerX, centerY, Math.min(width, height) * .16, 0, Math.PI * 2);
    seasonalFxCtx.fill();
    for (const p of seasonalFxParticles) {{
      const prevX = p.x || centerX;
      const prevY = p.y || centerY;
      p.radiusDrift += p.radiusSpeed * dt;
      p.angle += p.speed * dt * (1 + Math.min(.8, Math.abs(seasonalFxPointer.wind) * 10));
      const radius = p.radius + Math.sin(p.radiusDrift) * p.radiusSwing;
      p.x = centerX + Math.cos(p.angle) * radius;
      p.y = centerY + Math.sin(p.angle * p.lane) * radius * (.42 + p.lane * .22);
      const alpha = Math.max(.14, Math.min(.92, p.alpha + Math.sin(p.radiusDrift) * .1));
      seasonalFxCtx.strokeStyle = `hsla(${{p.hue}},100%,72%,${{alpha * .42}})`;
      seasonalFxCtx.lineWidth = Math.max(.6, p.size * .52);
      seasonalFxCtx.beginPath();
      seasonalFxCtx.moveTo(prevX, prevY);
      seasonalFxCtx.lineTo(p.x, p.y);
      seasonalFxCtx.stroke();
      if (p.size > 1.7) {{
        seasonalFxCtx.strokeStyle = `hsla(${{p.hue + 18}},100%,74%,${{alpha * .18}})`;
        seasonalFxCtx.lineWidth = .55;
        seasonalFxCtx.beginPath();
        seasonalFxCtx.moveTo(centerX, centerY);
        seasonalFxCtx.lineTo(p.x, p.y);
        seasonalFxCtx.stroke();
      }}
      seasonalFxCtx.fillStyle = `hsla(${{p.hue + 12}},100%,76%,${{alpha}})`;
      seasonalFxCtx.beginPath();
      seasonalFxCtx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
      seasonalFxCtx.fill();
    }}
  }} else if (seasonEffectMode === 'prism') {{
    seasonalFxCtx.lineCap = 'round';
    seasonalFxCtx.lineJoin = 'round';
    const focusX = seasonalFxPointer.active ? seasonalFxPointer.x : width * .5;
    const focusY = seasonalFxPointer.active ? seasonalFxPointer.y : height * .46;
    const pullRadius = Math.min(width, height) * .38 + 90;
    for (const p of seasonalFxParticles) {{
      const prevX = p.x;
      const prevY = p.y;
      p.rot += p.rotSpeed * dt;
      if (seasonalFxPointer.active) {{
        const dx = focusX - p.x;
        const dy = focusY - p.y;
        const dist = Math.max(1, Math.hypot(dx, dy));
        const pull = Math.max(0, 1 - dist / pullRadius);
        p.vx += ((dx / dist) * pull * .0052 + seasonalFxPointer.wind * .022) * dt;
        p.vy += ((dy / dist) * pull * .0044) * dt;
      }} else {{
        p.vx += seasonalFxPointer.wind * .01 * dt;
        p.vy += Math.sin(p.rot) * .0009 * dt;
      }}
      p.vx *= .992;
      p.vy *= .992;
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      if (p.x < -90 || p.x > width + 90 || p.y < -90 || p.y > height + 90) {{
        Object.assign(p, seasonFxSpawn('prism', true));
        if (seasonalFxPointer.active) {{
          p.x = focusX + (Math.random() - .5) * 140;
          p.y = focusY + (Math.random() - .5) * 140;
        }}
      }}
      const alpha = Math.min(.96, p.alpha + Math.abs(Math.sin(p.rot * 1.6)) * .16);
      seasonalFxCtx.strokeStyle = `hsla(${{p.hue}},100%,76%,${{alpha * .34}})`;
      seasonalFxCtx.lineWidth = Math.max(.7, p.size * .16);
      seasonalFxCtx.beginPath();
      seasonalFxCtx.moveTo(prevX, prevY);
      seasonalFxCtx.lineTo(p.x, p.y);
      seasonalFxCtx.stroke();
      seasonalFxCtx.save();
      seasonalFxCtx.translate(p.x, p.y);
      seasonalFxCtx.rotate(p.rot);
      seasonalFxCtx.fillStyle = `hsla(${{p.hue}},100%,64%,${{alpha}})`;
      seasonalFxCtx.beginPath();
      seasonalFxCtx.moveTo(0, -p.size);
      seasonalFxCtx.lineTo(p.size * .8, 0);
      seasonalFxCtx.lineTo(0, p.size);
      seasonalFxCtx.lineTo(-p.size * .8, 0);
      seasonalFxCtx.closePath();
      seasonalFxCtx.fill();
      seasonalFxCtx.restore();
    }}
  }} else if (seasonEffectMode === 'pulse') {{
    seasonalFxCtx.lineCap = 'round';
    seasonalFxCtx.lineJoin = 'round';
    const centerX = seasonalFxPointer.active ? seasonalFxPointer.x : width * .5;
    const centerY = seasonalFxPointer.active ? seasonalFxPointer.y : height * .48;
    const maxRadius = Math.min(width, height) * .34 + 50;
    for (const p of seasonalFxParticles) {{
      p.phase += p.phaseSpeed * dt;
      p.orbit += p.orbitSpeed * dt * (1 + Math.min(1.2, Math.abs(seasonalFxPointer.wind) * 12));
      p.radius += p.radiusSpeed * dt;
      if (p.radius > maxRadius) {{
        Object.assign(p, seasonFxSpawn('pulse', true));
        p.radius = Math.random() * 18;
      }}
      const offsetX = Math.cos(p.orbit) * p.radius;
      const offsetY = Math.sin(p.orbit * 1.18) * p.radius * .7;
      const x = centerX + offsetX;
      const y = centerY + offsetY;
      const life = 1 - Math.min(1, p.radius / maxRadius);
      const pulse = .45 + .55 * Math.abs(Math.sin(p.phase));
      const alpha = Math.max(.05, Math.min(.92, p.alpha * life + pulse * .08));
      const ring = Math.max(2, p.size * (.45 + (1 - life) * 1.6));
      seasonalFxCtx.strokeStyle = `hsla(${{p.hue}},100%,74%,${{alpha * .82}})`;
      seasonalFxCtx.lineWidth = Math.max(.7, 1.1 + (1 - life) * 2.2);
      seasonalFxCtx.beginPath();
      seasonalFxCtx.arc(x, y, ring, 0, Math.PI * 2);
      seasonalFxCtx.stroke();
      seasonalFxCtx.fillStyle = `hsla(${{p.hue + 24}},100%,76%,${{alpha * .72}})`;
      seasonalFxCtx.beginPath();
      seasonalFxCtx.arc(x, y, Math.max(.8, ring * .18), 0, Math.PI * 2);
      seasonalFxCtx.fill();
    }}
  }} else if (seasonEffectMode === 'aurora') {{
    seasonalFxCtx.globalCompositeOperation = 'screen';
    seasonalFxCtx.lineCap = 'round';
    seasonalFxCtx.lineJoin = 'round';
    const baseY = height * .16;
    for (let i = 0; i < seasonalFxParticles.length; i++) {{
      const p = seasonalFxParticles[i];
      p.phase += p.phaseSpeed * dt;
      p.sway += p.swaySpeed * dt;
      p.x += seasonalFxPointer.wind * dt * 36 + p.vx * dt * 10;
      p.y += p.vy * dt * 10;
      p.wave += p.waveSpeed * dt;
      if (p.x < -160) p.x = width + 160;
      if (p.x > width + 160) p.x = -160;
      const amp = p.wave * (1 + Math.sin(p.sway) * .3);
      const yBase = baseY + (i % 3) * height * .09 + Math.sin(p.phase * .7 + i * .42) * amp * .12;
      seasonalFxCtx.strokeStyle = `hsla(${{p.hue}},100%,70%,${{p.alpha * .28}})`;
      seasonalFxCtx.lineWidth = p.thickness * 3.2;
      seasonalFxCtx.beginPath();
      for (let s = 0; s <= 16; s++) {{
        const x = width * s / 16;
        const y = yBase + Math.sin(p.phase + s * .58 + p.sway) * amp + Math.cos(p.phase * .6 + s * .2) * amp * .18;
        if (s === 0) seasonalFxCtx.moveTo(x, y);
        else seasonalFxCtx.lineTo(x, y);
      }}
      seasonalFxCtx.stroke();
      seasonalFxCtx.strokeStyle = `hsla(${{p.hue + 18}},100%,82%,${{p.alpha * .66}})`;
      seasonalFxCtx.lineWidth = Math.max(1, p.thickness * .52);
      seasonalFxCtx.beginPath();
      for (let s = 0; s <= 16; s++) {{
        const x = width * s / 16;
        const y = yBase + Math.sin(p.phase + s * .58 + p.sway) * amp + Math.cos(p.phase * .6 + s * .2) * amp * .18;
        if (s === 0) seasonalFxCtx.moveTo(x, y);
        else seasonalFxCtx.lineTo(x, y);
      }}
      seasonalFxCtx.stroke();
    }}
  }} else if (seasonEffectMode === 'matrix') {{
    seasonalFxCtx.globalCompositeOperation = 'lighter';
    seasonalFxCtx.lineCap = 'round';
    seasonalFxCtx.shadowColor = 'rgba(80,255,120,.55)';
    seasonalFxCtx.shadowBlur = 10;
    seasonalFxCtx.textAlign = 'center';
    seasonalFxCtx.textBaseline = 'middle';
    for (const p of seasonalFxParticles) {{
      if (Math.random() < .08) p.glyph = seasonFxRandomGlyph();
      p.y += p.vy * dt;
      p.x += seasonalFxPointer.wind * dt * 8;
      if (p.y > height + p.trail) {{
        Object.assign(p, seasonFxSpawn('matrix'));
        p.y = -Math.random() * height * .34;
      }}
      if (p.x < -40) p.x = width + 40;
      if (p.x > width + 40) p.x = -40;
      seasonalFxCtx.fillStyle = `rgba(70,255,130,${{p.trailAlpha}})`;
      seasonalFxCtx.fillRect(p.x - .8, p.y - p.trail, 1.6, p.trail + 6);
      seasonalFxCtx.font = `900 ${{Math.max(12, Math.round(p.size))}}px ui-monospace, SFMono-Regular, Consolas, monospace`;
      seasonalFxCtx.fillStyle = `rgba(170,255,190,${{p.alpha}})`;
      seasonalFxCtx.fillText(p.glyph, p.x, p.y);
    }}
  }} else if (seasonEffectMode === 'nebula') {{
    seasonalFxCtx.globalCompositeOperation = 'screen';
    for (const p of seasonalFxParticles) {{
      p.drift += p.driftSpeed * dt;
      p.pulse += .0006 * dt;
      p.x += (p.vx + seasonalFxPointer.wind * .024) * dt;
      p.y += p.vy * dt;
      if (p.x < -p.size) p.x = width + p.size;
      if (p.x > width + p.size) p.x = -p.size;
      if (p.y < -p.size) p.y = height + p.size;
      if (p.y > height + p.size) p.y = -p.size;
      const alpha = Math.min(.22, p.alpha + Math.sin(p.drift) * .03 + Math.abs(Math.sin(p.pulse)) * .02);
      const glow = seasonalFxCtx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.size);
      glow.addColorStop(0, `hsla(${{p.hue}},100%,72%,${{alpha}})`);
      glow.addColorStop(.35, `hsla(${{p.hue + 18}},100%,60%,${{alpha * .44}})`);
      glow.addColorStop(1, 'rgba(0,0,0,0)');
      seasonalFxCtx.fillStyle = glow;
      seasonalFxCtx.beginPath();
      seasonalFxCtx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
      seasonalFxCtx.fill();
    }}
  }} else if (seasonEffectMode === 'fireworks') {{
    seasonalFxCtx.globalCompositeOperation = 'lighter';
    seasonalFxCtx.lineCap = 'round';
    seasonalFxCtx.lineJoin = 'round';
    for (const p of seasonalFxParticles) {{
      const prevX = p.x;
      const prevY = p.y;
      p.sparkle += p.sparkleSpeed * dt;
      p.vx *= Math.pow(p.drag, dt / 16);
      p.vy = p.vy * Math.pow(p.drag, dt / 16) + p.gravity * dt;
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      p.life -= dt / 1200;
      if (p.life <= 0 || p.x < -80 || p.x > width + 80 || p.y > height + 120) {{
        Object.assign(p, seasonFxSpawn('fireworks'));
        continue;
      }}
      const spark = .5 + .5 * Math.sin(p.sparkle);
      const alpha = Math.min(.96, p.alpha * Math.max(.2, p.life) + Math.abs(spark) * .18);
      seasonalFxCtx.strokeStyle = `hsla(${{p.hue}},100%,72%,${{alpha * .84}})`;
      seasonalFxCtx.lineWidth = Math.max(.6, p.size * .6);
      seasonalFxCtx.beginPath();
      seasonalFxCtx.moveTo(prevX, prevY);
      seasonalFxCtx.lineTo(p.x, p.y);
      seasonalFxCtx.stroke();
      seasonalFxCtx.strokeStyle = `hsla(${{p.hue + 22}},100%,86%,${{alpha * .34}})`;
      seasonalFxCtx.lineWidth = Math.max(1.2, p.size * 2.8);
      seasonalFxCtx.beginPath();
      seasonalFxCtx.moveTo(prevX, prevY);
      seasonalFxCtx.lineTo(p.x, p.y);
      seasonalFxCtx.stroke();
      seasonalFxCtx.fillStyle = `hsla(${{p.hue + 14}},100%,88%,${{alpha}})`;
      seasonalFxCtx.beginPath();
      seasonalFxCtx.arc(p.x, p.y, Math.max(.8, p.size * .95), 0, Math.PI * 2);
      seasonalFxCtx.fill();
    }}
  }} else if (seasonEffectMode === 'vortex') {{
    seasonalFxCtx.globalCompositeOperation = 'lighter';
    seasonalFxCtx.lineCap = 'round';
    seasonalFxCtx.lineJoin = 'round';
    const centerX = seasonalFxPointer.active ? seasonalFxPointer.x : width * .5;
    const centerY = seasonalFxPointer.active ? seasonalFxPointer.y : height * .5;
    for (const p of seasonalFxParticles) {{
      const prevX = p.x || centerX;
      const prevY = p.y || centerY;
      p.radius += p.radiusSpeed * dt;
      p.angle += p.spin * dt * (1 + Math.min(1.4, Math.abs(seasonalFxPointer.wind) * 10));
      p.wobble += p.wobbleSpeed * dt;
      const radius = p.radius + Math.sin(p.wobble) * 20;
      p.x = centerX + Math.cos(p.angle) * radius;
      p.y = centerY + Math.sin(p.angle * p.lane) * radius * .84;
      if (radius > Math.max(width, height) * .6) {{
        Object.assign(p, seasonFxSpawn('vortex'));
      }}
      const alpha = Math.min(.92, p.alpha + Math.abs(Math.sin(p.wobble)) * .14);
      seasonalFxCtx.strokeStyle = `hsla(${{p.hue}},100%,72%,${{alpha * .22}})`;
      seasonalFxCtx.lineWidth = Math.max(.7, p.size * .74);
      seasonalFxCtx.beginPath();
      seasonalFxCtx.moveTo(prevX, prevY);
      seasonalFxCtx.lineTo(p.x, p.y);
      seasonalFxCtx.stroke();
      seasonalFxCtx.strokeStyle = `hsla(${{p.hue + 22}},100%,84%,${{alpha * .14}})`;
      seasonalFxCtx.lineWidth = Math.max(1.4, p.size * 2.8);
      seasonalFxCtx.beginPath();
      seasonalFxCtx.moveTo(centerX, centerY);
      seasonalFxCtx.lineTo(p.x, p.y);
      seasonalFxCtx.stroke();
      seasonalFxCtx.fillStyle = `hsla(${{p.hue + 12}},100%,86%,${{alpha}})`;
      seasonalFxCtx.beginPath();
      seasonalFxCtx.arc(p.x, p.y, Math.max(.8, p.size), 0, Math.PI * 2);
      seasonalFxCtx.fill();
    }}
  }} else if (seasonEffectMode === 'comet') {{
    seasonalFxCtx.globalCompositeOperation = 'lighter';
    seasonalFxCtx.lineCap = 'round';
    seasonalFxCtx.lineJoin = 'round';
    for (const p of seasonalFxParticles) {{
      const prevX = p.x;
      const prevY = p.y;
      p.wobble += p.wobbleSpeed * dt;
      p.x += (p.vx + seasonalFxPointer.wind * .03) * dt;
      p.y += (p.vy + Math.sin(p.wobble) * .012) * dt;
      if (p.x < -width * .4 || p.x > width * 1.4 || p.y < -120 || p.y > height + 120) {{
        Object.assign(p, seasonFxSpawn('comet'));
        continue;
      }}
      const tailX = p.x - p.vx * p.len * .62;
      const tailY = p.y - p.vy * p.len * .62;
      const alpha = Math.min(.95, p.alpha + Math.abs(Math.sin(p.wobble)) * .14);
      seasonalFxCtx.strokeStyle = `hsla(${{p.hue}},100%,72%,${{alpha * .84}})`;
      seasonalFxCtx.lineWidth = p.width;
      seasonalFxCtx.beginPath();
      seasonalFxCtx.moveTo(p.x, p.y);
      seasonalFxCtx.lineTo(tailX, tailY);
      seasonalFxCtx.stroke();
      seasonalFxCtx.strokeStyle = `hsla(${{p.hue + 18}},100%,86%,${{alpha * .22}})`;
      seasonalFxCtx.lineWidth = p.width * 3.6;
      seasonalFxCtx.beginPath();
      seasonalFxCtx.moveTo(prevX, prevY);
      seasonalFxCtx.lineTo(tailX, tailY);
      seasonalFxCtx.stroke();
      seasonalFxCtx.fillStyle = `hsla(${{p.hue + 12}},100%,88%,${{alpha}})`;
      seasonalFxCtx.beginPath();
      seasonalFxCtx.arc(p.x, p.y, Math.max(1, p.width * 1.2), 0, Math.PI * 2);
      seasonalFxCtx.fill();
    }}
  }} else if (seasonEffectMode === 'laser') {{
    seasonalFxCtx.lineCap = 'round';
    seasonalFxCtx.lineJoin = 'round';
    const focusX = seasonalFxPointer.active ? seasonalFxPointer.x : width * .5;
    const focusY = seasonalFxPointer.active ? seasonalFxPointer.y : height * .52;
    const influence = Math.min(width, height) * .42 + 120;
    for (const p of seasonalFxParticles) {{
      p.pulse += p.pulseSpeed * dt;
      if (seasonalFxPointer.active) {{
        const dx = focusX - p.x;
        const dy = focusY - p.y;
        const dist = Math.max(1, Math.hypot(dx, dy));
        const pull = Math.max(0, 1 - dist / influence);
        p.vx += (dx / dist) * pull * .008 * dt;
        p.vy += (dy / dist) * pull * .006 * dt;
      }} else {{
        p.vx += seasonalFxPointer.wind * .02 * dt;
      }}
      p.vx *= .996;
      p.vy *= .995;
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      if (p.x < -width * .35 || p.x > width * 1.35 || p.y < -120 || p.y > height + 120) {{
        Object.assign(p, seasonFxSpawn('laser', true));
      }}
      const alpha = Math.min(.94, p.alpha + Math.abs(Math.sin(p.pulse)) * .18);
      const tailX = p.x - p.vx * p.len * .55;
      const tailY = p.y - p.vy * p.len * .55;
      seasonalFxCtx.strokeStyle = `hsla(${{p.hue}},100%,72%,${{alpha * .9}})`;
      seasonalFxCtx.lineWidth = p.width;
      seasonalFxCtx.beginPath();
      seasonalFxCtx.moveTo(p.x, p.y);
      seasonalFxCtx.lineTo(tailX, tailY);
      seasonalFxCtx.stroke();
      seasonalFxCtx.strokeStyle = `hsla(${{p.hue + 28}},100%,86%,${{alpha * .34}})`;
      seasonalFxCtx.lineWidth = p.width * 3.2;
      seasonalFxCtx.beginPath();
      seasonalFxCtx.moveTo(p.x, p.y);
      seasonalFxCtx.lineTo(tailX, tailY);
      seasonalFxCtx.stroke();
    }}
  }}
  seasonalFxFrame = requestAnimationFrame(seasonFxStep);
}}

function seasonFxStart() {{
  if (!seasonalFxCanvas || seasonEffectMode === 'off') return;
  seasonalFxCanvas.hidden = false;
  seasonFxResetCanvas();
  seasonFxBuildParticles();
  seasonFxStop();
  seasonalFxFrame = requestAnimationFrame(seasonFxStep);
}}

function setSeasonEffectMode(nextMode, options = {{}}) {{
  const mode = SEASON_EFFECT_MODES.has(nextMode) ? nextMode : 'off';
  seasonEffectMode = mode;
  if (options.persist !== false) saveSeasonEffectMode(mode);
  syncSeasonButtons();
  if (!seasonalFxCanvas) return;
  if (mode === 'off') {{
    seasonalFxCanvas.hidden = true;
    seasonFxStop();
    return;
  }}
  seasonalFxCanvas.hidden = false;
  seasonFxStart();
}}

function initSeasonEffects() {{
  if (!seasonalFxCanvas) return;
  seasonSwitches.forEach((el) => {{
    const columns = Math.min(3, Math.max(2, el.querySelectorAll('[data-season-mode]').length));
    el.style.gridTemplateColumns = `repeat(${{columns}},minmax(0,1fr))`;
  }});
  window.addEventListener('resize', () => {{
    clearTimeout(seasonalFxResizeTimer);
    seasonalFxResizeTimer = setTimeout(() => {{
      seasonalFxResizeTimer = 0;
      if (seasonEffectMode === 'off') return;
      seasonFxResetCanvas();
      seasonFxBuildParticles();
    }}, 120);
  }});
  window.addEventListener('pointermove', seasonFxHandlePointerMove, {{passive: true}});
  window.addEventListener('pointerleave', seasonFxHandlePointerLeave);
  window.addEventListener('blur', seasonFxHandlePointerLeave);
  seasonEffectMode = loadSeasonEffectMode();
  syncSeasonButtons();
  setSeasonPanelOpen(false);
  setSeasonEffectMode(seasonEffectMode, {{persist: false}});
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
  if (/mounted\\s+used/i.test(text)) return NaN;
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
  if (/mounted\\s+used/i.test(text)) return true;
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
  if (/^(LuCI|DevTools)\\s*:/i.test(sample)) return false;
  return /^(?:\\/etc\\/init\\.d\\/|systemctl|journalctl|ss|scp|cp|chmod|logread|pgrep|ping|grep|curl|wget|cat|tail|head|ps|ip|uci|ubus|opkg|service|netstat|sh|ash|bash|python|node|nginx|xray)\\b/i.test(sample)
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

function renderModelMetric(modelValue, showLegend = false) {{
  const model = metricPlaceholder(modelValue);
  const safeModel = escapeHtml(model);
  if (!showLegend) return metricHtml('Модель', safeModel, 'span2 model-metric');
  return metricHtml(
    'Модель',
    `<span class="modelMetricValue"><span class="modelLegendSpacer" aria-hidden="true"></span><span class="modelMetricName">${{safeModel}}</span><span class="modelLegendBadge"><span>Legend</span></span></span>`,
    'span2 model-metric'
  );
}}

function modelGetsLegendBadge(modelValue) {{
  const model = String(modelValue || '').trim();
  return /xiaomi\\s+mi\\s+router\\s+ax3000t/i.test(model);
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
    const permissions = r.permissions && typeof r.permissions === 'object' ? r.permissions : {{}};
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
    const adminButton = online && !!permissions.admin
      ? `<a class="btn" href="${{escapeAttr(access)}}">Админка</a>`
      : `<span class="btn disabled">Админка</span>`;
    const sshReady = online && ssh === 'running' && Number(r.ssh_entry_port || 0) > 0;
    const sshButton = sshReady
      && !!permissions.ssh
      ? `<a class="btn" href="${{escapeAttr(r.ssh_url || ('/ssh/' + encodeURIComponent(r.id) + '/'))}}" target="_blank" rel="noopener noreferrer">SSH</a>`
      : `<span class="btn disabled">SSH</span>`;
    const wolReady = routerSupportsWol(r) && !!permissions.wol;
    const wolState = getWolState(r.id);
    const trafficState = getTrafficState(r.id);
    const trafficReady = sshReady && !!permissions.traffic;
    const wolButton = wolReady
      ? `<button class="btn" data-wol-toggle="${{escapeAttr(r.id)}}" type="button">${{wolState.open ? 'Скрыть Wake-on-LAN' : 'Wake-on-LAN'}}</button>`
      : `<span class="btn disabled">Wake-on-LAN</span>`;
    const trafficButton = trafficReady
      ? `<button class="btn" data-traffic-toggle="${{escapeAttr(r.id)}}" type="button">${{trafficState.open ? 'Скрыть Клиенты Traffic' : 'Клиенты Traffic'}}</button>`
      : `<span class="btn disabled">Клиенты Traffic</span>`;
    const notesPreview = String(r.notes_preview || routerNotesPreview(r.notes || ''));
    const notesButton = !!permissions.notes
      ? `<a class="btn" href="${{escapeAttr(r.notes_url || ('/router/' + encodeURIComponent(r.id) + '/notes'))}}" target="_blank" rel="noopener noreferrer">Заметки</a>`
      : `<span class="btn disabled">Заметки</span>`;
    const renameLabel = 'Переименовать ' + (r.name || r.id || 'роутер');
    const renameButton = !!permissions.manage
      ? `<button class="nameEditBtn" type="button" data-rename="${{escapeAttr(r.id)}}" aria-label="${{escapeAttr(renameLabel)}}" title="${{escapeAttr(renameLabel)}}"><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path fill="currentColor" d="M3 17.25V21h3.75l11-11.03-3.75-3.75zm17.71-10.04a1.003 1.003 0 0 0 0-1.42l-2.5-2.5a1.003 1.003 0 0 0-1.42 0l-1.96 1.96 3.75 3.75z"/></svg></button>`
      : `<span class="nameEditBtn disabled" aria-hidden="true"><svg viewBox="0 0 24 24" focusable="false"><path fill="currentColor" d="M3 17.25V21h3.75l11-11.03-3.75-3.75zm17.71-10.04a1.003 1.003 0 0 0 0-1.42l-2.5-2.5a1.003 1.003 0 0 0-1.42 0l-1.96 1.96 3.75 3.75z"/></svg></span>`;
    const actionsId = actionPanelId(r.id);
    const actionsOpen = expandedActionPanels.has(actionsId);
    const metricsHtml = [
      renderModelMetric(model, modelGetsLegendBadge(model)),
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
        ${{trafficButton}}
        ${{permissions.config ? `<a class="btn" href="${{escapeAttr(r.config_url)}}">OpenWrt config</a>` : '<span class="btn disabled">OpenWrt config</span>'}}
        ${{permissions.delete ? `<button class="btn" data-delete="${{escapeAttr(r.id)}}">Удалить</button>` : '<span class="btn disabled">Удалить</span>'}}
      </div>
      ${{wolReady ? renderWolPanelResponsive(r) : ''}}
      ${{trafficReady ? renderTrafficPanel(r) : ''}}
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
  return String(value || '').trim().replace(/\\s+/g, ' ').slice(0, 80);
}}

function renderRouterNotesPanel(router) {{
  const permissions = router && router.permissions && typeof router.permissions === 'object' ? router.permissions : {{}};
  const canEdit = Boolean(permissions.notes_edit || permissions.manage);
  const state = getNoteState(router && router.id || '');
  const currentDraft = normalizeRouterNotes(state.draft || (router && router.notes) || '');
  const savedText = normalizeRouterNotes((router && router.notes) || state.saved || '');
  const noteCount = currentDraft.length;
  let statusText = '';
  let statusClass = 'notesMeta';
  if (state.error) {{
    statusText = state.error;
    statusClass += ' bad';
  }} else if (state.message) {{
    statusText = state.message;
    statusClass += ' ok';
  }} else if (savedText) {{
    statusText = 'Сохранённая заметка уже привязана к этому роутеру.';
  }} else {{
    statusText = 'Запиши сюда клиента, изменения, предупреждения или что проверить потом.';
  }}
  return `<div class="wolPanel notesPanel" id="${{escapeAttr(notesPanelId(router.id))}}" data-router-notes-panel="${{escapeAttr(router.id)}}">
    <div class="wolHeader">
      <h3 class="wolTitle">Заметки</h3>
      <div class="${{escapeAttr(statusClass)}}"><span>${{escapeHtml(statusText)}}</span><span>${{noteCount}} / 5000</span></div>
    </div>
    <div class="notesEditor">
      <textarea class="notesArea" data-router-notes-input="${{escapeAttr(router.id)}}" placeholder="Например: клиент Маша, менял DNS, отключал 5 ГГц, просил не трогать LAN4..."${{canEdit ? '' : ' readonly'}}>${{escapeHtml(currentDraft)}}</textarea>
      <div class="notesActions">
        ${{canEdit ? `<button class="btn" type="button" data-notes-save="${{escapeAttr(router.id)}}"${{state.saving ? ' disabled' : ''}}>${{state.saving ? 'Сохраняю...' : 'Сохранить заметки'}}</button>` : '<span class="btn disabled">Только просмотр</span>'}}
        ${{canEdit ? `<button class="btn" type="button" data-notes-reset="${{escapeAttr(router.id)}}"${{state.saving ? ' disabled' : ''}}>Сбросить черновик</button>` : ''}}
      </div>
    </div>
  </div>`;
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
  syncRouterNotesState(list);
  syncExpandedActionPanels(list);
  if (!list.length) {{
    cards.innerHTML = '<div class="empty">Пока нет роутеров. Добавь первый, например <b>main</b>.</div>';
    return;
  }}
  cards.innerHTML = list.map(r => {{
    const permissions = r.permissions && typeof r.permissions === 'object' ? r.permissions : {{}};
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
    const adminButton = online && !!permissions.admin
      ? `<a class="btn" href="${{escapeAttr(access)}}">Админка</a>`
      : `<span class="btn disabled">Админка</span>`;
    const sshReady = online && ssh === 'running' && Number(r.ssh_entry_port || 0) > 0;
    const sshButton = sshReady
      && !!permissions.ssh
      ? `<a class="btn" href="${{escapeAttr(r.ssh_url || ('/ssh/' + encodeURIComponent(r.id) + '/'))}}" target="_blank" rel="noopener noreferrer">SSH</a>`
      : `<span class="btn disabled">SSH</span>`;
    const wolReady = routerSupportsWol(r) && !!permissions.wol;
    const wolState = getWolState(r.id);
    const trafficState = getTrafficState(r.id);
    const trafficReady = sshReady && !!permissions.traffic;
    const wolButton = wolReady
      ? `<button class="btn" data-wol-toggle="${{escapeAttr(r.id)}}" type="button">${{wolState.open ? 'Скрыть Wake-on-LAN' : 'Wake-on-LAN'}}</button>`
      : `<span class="btn disabled">Wake-on-LAN</span>`;
    const trafficButton = trafficReady
      ? `<button class="btn" data-traffic-toggle="${{escapeAttr(r.id)}}" type="button">${{trafficState.open ? 'Скрыть Клиенты Traffic' : 'Клиенты Traffic'}}</button>`
      : `<span class="btn disabled">Клиенты Traffic</span>`;
    const notesPreview = String(r.notes_preview || routerNotesPreview(r.notes || ''));
    const notesButton = !!permissions.notes
      ? `<a class="btn" href="${{escapeAttr(r.notes_url || ('/router/' + encodeURIComponent(r.id) + '/notes'))}}" target="_blank" rel="noopener noreferrer">Заметки</a>`
      : `<span class="btn disabled">Заметки</span>`;
    const renameLabel = 'Переименовать ' + (r.name || r.id || 'роутер');
    const renameButton = !!permissions.manage
      ? `<button class="nameEditBtn" type="button" data-rename="${{escapeAttr(r.id)}}" aria-label="${{escapeAttr(renameLabel)}}" title="${{escapeAttr(renameLabel)}}"><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path fill="currentColor" d="M3 17.25V21h3.75l11-11.03-3.75-3.75zm17.71-10.04a1.003 1.003 0 0 0 0-1.42l-2.5-2.5a1.003 1.003 0 0 0-1.42 0l-1.96 1.96 3.75 3.75z"/></svg></button>`
      : `<span class="nameEditBtn disabled" aria-hidden="true"><svg viewBox="0 0 24 24" focusable="false"><path fill="currentColor" d="M3 17.25V21h3.75l11-11.03-3.75-3.75zm17.71-10.04a1.003 1.003 0 0 0 0-1.42l-2.5-2.5a1.003 1.003 0 0 0-1.42 0l-1.96 1.96 3.75 3.75z"/></svg></span>`;
    const actionsId = actionPanelId(r.id);
    const actionsOpen = expandedActionPanels.has(actionsId);
    const metricsHtml = [
      renderModelMetric(model, modelGetsLegendBadge(model)),
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
      ${{notesPreview ? `<div class="notesPreview"><strong>Заметки:</strong> <span>${{escapeHtml(notesPreview)}}</span></div>` : ''}}
      <div class="metrics">
        ${{metricsHtml}}
      </div>
      <button class="actionToggle primary" type="button" data-actions-toggle="${{escapeAttr(actionsId)}}" aria-expanded="${{actionsOpen ? 'true' : 'false'}}">${{actionsOpen ? 'Скрыть действия' : 'Открыть действия'}}</button>
      <div class="actions mobileCollapsed${{actionsOpen ? ' open' : ''}}" id="${{escapeAttr(actionsId)}}">
        ${{adminButton}}
        ${{sshButton}}
        ${{wolButton}}
        ${{trafficButton}}
        ${{notesButton}}
        ${{permissions.config ? `<a class="btn" href="${{escapeAttr(r.config_url)}}">OpenWrt config</a>` : '<span class="btn disabled">OpenWrt config</span>'}}
        ${{permissions.delete ? `<button class="btn" data-delete="${{escapeAttr(r.id)}}">Удалить</button>` : '<span class="btn disabled">Удалить</span>'}}
      </div>
      ${{wolReady ? renderWolPanelResponsive(r) : ''}}
      ${{trafficReady ? renderTrafficPanel(r) : ''}}
    </article>`;
  }}).join('');
  syncActionToggleStates();
}};

function renderRouterView() {{
  const pageScrollX = window.scrollX || window.pageXOffset || 0;
  const pageScrollY = window.scrollY || window.pageYOffset || 0;
  const trafficViewportState = Array.from(document.querySelectorAll('[data-traffic-viewport]')).map((node) => ({{
    routerId: String(node.dataset.trafficViewport || ''),
    scrollTop: node.scrollTop || 0
  }})).filter((item) => item.routerId);
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
  if (pendingRouterViewRestore) {{
    window.cancelAnimationFrame(pendingRouterViewRestore);
    pendingRouterViewRestore = 0;
  }}
  pendingRouterViewRestore = window.requestAnimationFrame(() => {{
    window.scrollTo(pageScrollX, pageScrollY);
    trafficViewportState.forEach((item) => {{
      const viewport = Array.from(document.querySelectorAll('[data-traffic-viewport]')).find((node) => String(node.dataset.trafficViewport || '') === item.routerId);
      if (viewport) viewport.scrollTop = item.scrollTop;
    }});
    pendingRouterViewRestore = 0;
  }});
  syncTrafficAutoRefresh();
}}

async function loadWolDevices(routerId, force = false) {{
  const key = String(routerId || '');
  const router = selectedRouter(key);
  if (!router) return;
  const state = getWolState(key);
  if (state.loading || (state.loaded && !force)) return;
  setWolState(key, {{loading: true, error: '', message: ''}});
  requestRouterRender();
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
  requestRouterRender();
}}

async function loadTrafficClients(routerId, force = false, options = {{}}) {{
  const key = String(routerId || '');
  const router = selectedRouter(key);
  if (!router) return;
  const state = getTrafficState(key);
  const silent = Boolean(options && options.silent);
  if (state.loading || (state.loaded && !force)) return;
  setTrafficState(key, silent ? {{loading: true, error: ''}} : {{loading: true, error: '', message: ''}});
  if (!silent) requestRouterRender();
  try {{
    const res = await fetch('/api/router/' + encodeURIComponent(key) + '/traffic/clients', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{
        ssh_password: state.sshPassword || ''
      }})
    }});
    const data = await res.json().catch(() => ({{ok: false, error: 'bad json', clients: []}}));
    if (!res.ok || !data.ok) {{
      throw new Error(data.error || 'Не удалось получить клиентов и трафик');
    }}
    const clients = Array.isArray(data.clients) ? data.clients : [];
    setTrafficState(key, {{
      loaded: true,
      loading: false,
      clients,
      trafficSource: String(data.traffic_source || ''),
      trafficSupported: Boolean(data.traffic_supported),
      flowHits: Number(data.flow_hits || 0),
      error: '',
      message: clients.length
        ? `Список обновлен: ${{clients.length}} клиентов.`
        : 'Список обновлен, но клиенты не найдены.'
    }});
  }} catch (err) {{
    setTrafficState(key, {{
      loaded: false,
      loading: false,
      clients: [],
      trafficSource: '',
      trafficSupported: false,
      flowHits: 0,
      error: err && err.message ? err.message : 'Не удалось получить клиентов и трафик'
    }});
  }}
  requestRouterRender();
}}

async function loadTrafficClients(routerId, force = false, options = {{}}) {{
  const key = String(routerId || '');
  const router = selectedRouter(key);
  if (!router) return;
  const state = getTrafficState(key);
  const silent = Boolean(options && options.silent);
  if (state.loading || (state.loaded && !force)) return;
  setTrafficState(key, silent ? {{loading: true, error: ''}} : {{loading: true, error: '', message: ''}});
  if (!silent) requestRouterRender();
  try {{
    const res = await fetch('/api/router/' + encodeURIComponent(key) + '/traffic/clients', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{
        ssh_password: state.sshPassword || ''
      }})
    }});
    const data = await res.json().catch(() => ({{ok: false, error: 'bad json', clients: []}}));
    if (!res.ok || !data.ok) {{
      throw new Error(data.error || 'Не удалось получить клиентов и трафик');
    }}
    const clients = Array.isArray(data.clients) ? data.clients : [];
    const successMessage = clients.length
      ? `Список обновлен: ${{clients.length}} клиентов.`
      : 'Список обновлен, но клиенты не найдены.';
    setTrafficState(key, {{
      loaded: true,
      loading: false,
      clients,
      trafficSource: String(data.traffic_source || ''),
      trafficSupported: Boolean(data.traffic_supported),
      flowHits: Number(data.flow_hits || 0),
      error: '',
      message: silent ? (state.message || successMessage) : successMessage
    }});
  }} catch (err) {{
    setTrafficState(key, {{
      loaded: silent ? state.loaded : false,
      loading: false,
      clients: silent ? state.clients : [],
      trafficSource: silent ? state.trafficSource : '',
      trafficSupported: silent ? state.trafficSupported : false,
      flowHits: silent ? state.flowHits : 0,
      error: err && err.message ? err.message : 'Не удалось получить клиентов и трафик'
    }});
  }}
  requestRouterRender();
}}

async function resetTrafficClients(routerId) {{
  const key = String(routerId || '');
  const router = selectedRouter(key);
  if (!router) return;
  const state = getTrafficState(key);
  if (state.loading) return;
  const confirmed = window.confirm('Сбросить счётчики трафика на роутере? Это очистит conntrack и может оборвать текущие подключения.');
  if (!confirmed) return;
  setTrafficState(key, {{loading: true, error: '', message: 'Сбрасываю conntrack и трафик...'}});
  requestRouterRender();
  try {{
    const res = await fetch('/api/router/' + encodeURIComponent(key) + '/traffic/reset', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{
        ssh_password: state.sshPassword || ''
      }})
    }});
    const data = await res.json().catch(() => ({{ok: false, error: 'bad json'}}));
    if (!res.ok || !data.ok) {{
      throw new Error(data.error || 'Не удалось сбросить трафик');
    }}
    const zeroedClients = (Array.isArray(state.clients) ? state.clients : []).map((client) => Object.assign({{}}, client, {{
      rx_bytes: 0,
      tx_bytes: 0,
      total_bytes: 0
    }}));
    setTrafficState(key, {{
      loading: false,
      loaded: true,
      error: '',
      message: data.message || 'Трафик сброшен. Новые счётчики начнут копиться с нуля.',
      clients: zeroedClients,
      flowHits: 0
    }});
  }} catch (err) {{
    setTrafficState(key, {{
      loading: false,
      error: err && err.message ? err.message : 'Не удалось сбросить трафик'
    }});
  }}
  requestRouterRender();
}}

async function wakeSelectedDevice(routerId) {{
  const key = String(routerId || '');
  const state = getWolState(key);
  const selected = (Array.isArray(state.devices) ? state.devices : []).find((device) => String(device.mac || '') === String(state.selectedMac || ''));
  if (!selected) {{
    setWolState(key, {{error: 'Сначала выбери устройство из списка.', message: ''}});
    requestRouterRender();
    return;
  }}
  setWolState(key, {{waking: true, error: '', message: ''}});
  requestRouterRender();
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
    const delivery = data && data.output ? String(data.output).trim() : '';
    setWolState(key, {{
      waking: false,
      error: '',
      message: delivery ? `Magic packet отправлен: ${{label}}. ${{delivery}}.` : `Magic packet отправлен: ${{label}}.`
    }});
  }} catch (err) {{
    setWolState(key, {{
      waking: false,
      error: err && err.message ? err.message : 'Не удалось отправить Wake-on-LAN пакет'
    }});
  }}
  requestRouterRender();
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
  const controlsHubMenu = panel === headerActions;
  if (!mobile) {{
    toggle.hidden = true;
    panel.hidden = false;
    if (controlsHubMenu && mobileOwnerTools) mobileOwnerTools.hidden = true;
    toggle.setAttribute('aria-expanded', 'true');
    return;
  }}
  toggle.hidden = false;
  const open = !panel.hidden;
  if (controlsHubMenu && mobileOwnerTools) mobileOwnerTools.hidden = !open || !authIsOwner();
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

const headerActionsAnchor = headerActions ? document.createComment('headerActions-anchor') : null;
if (headerActionsAnchor && headerActions.parentNode) {{
  headerActions.parentNode.insertBefore(headerActionsAnchor, headerActions.nextSibling);
}}

function syncHubMenuPlacement() {{
  if (!headerActions || !hubMenuPanelHost || !headerActionsAnchor) return;
  if (mobileLayoutMq.matches) {{
    if (mobileOwnerTools && mobileOwnerTools.parentNode !== hubMenuPanelHost) {{
      hubMenuPanelHost.insertBefore(mobileOwnerTools, hubMenuPanelHost.firstChild);
    }}
    if (headerActions.parentNode !== hubMenuPanelHost) {{
      hubMenuPanelHost.appendChild(headerActions);
    }}
    return;
  }}
  if (mobileOwnerTools) mobileOwnerTools.hidden = true;
  if (headerActionsAnchor.parentNode && headerActions.parentNode !== headerActionsAnchor.parentNode) {{
    headerActionsAnchor.parentNode.insertBefore(headerActions, headerActionsAnchor);
  }}
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
  syncHubMenuPlacement();
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
    if (!isRouterSearchInputInteractive(input)) {{
      if (input.value !== routerSearchQuery) input.value = routerSearchQuery;
      return;
    }}
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
seasonButtons.forEach((button) => {{
  button.addEventListener('click', () => {{
    setSeasonEffectMode(button.dataset.seasonMode || 'off');
  }});
}});
seasonToggles.forEach((toggle) => {{
  toggle.addEventListener('click', (ev) => {{
    ev.stopPropagation();
    setSeasonPanelOpen(!seasonPanelOpen);
  }});
}});
routerSearchPanels.forEach((panel) => {{
  panel.addEventListener('click', (ev) => ev.stopPropagation());
}});
seasonPanels.forEach((panel) => {{
  panel.addEventListener('click', (ev) => ev.stopPropagation());
}});
document.addEventListener('click', (ev) => {{
  if (seasonPanelOpen && !seasonDocks.some((dock) => dock && dock.contains(ev.target))) {{
    setSeasonPanelOpen(false);
  }}
  if (!routerSearchPanels.length || routerSearchPanels.every((panel) => panel.hidden)) return;
  if (routerSearchDocks.some((dock) => dock && dock.contains(ev.target))) return;
  setRouterSearchOpen(false, {{focusInput: false}});
}});
document.addEventListener('keydown', (ev) => {{
  if (ev.key !== 'Escape') return;
  let handled = false;
  if (seasonPanelOpen) {{
    setSeasonPanelOpen(false);
    handled = true;
  }}
  if (routerSearchPanels.length && routerSearchPanels.some((panel) => !panel.hidden)) {{
    setRouterSearchOpen(false, {{focusInput: false}});
    handled = true;
    if (lastRouterSearchTrigger) {{
      lastRouterSearchTrigger.focus();
    }} else if (routerSearchToggle) {{
      routerSearchToggle.focus();
    }}
  }}
  if (handled) ev.preventDefault();
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
syncHubMenuPlacement();
initMobilePanelToggle(hubMenuToggle, headerActions, 'Открыть меню хаба', 'Скрыть меню хаба');
initMobilePanelToggle(routerStatsToggle, routerStats, 'Открыть статистику', 'Скрыть статистику');
document.body.classList.remove('preload-mobile-panels');

async function handleXrayReload() {{
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
}}

async function handleXrayRestart() {{
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
}}

document.getElementById('xrayReload').addEventListener('click', handleXrayReload);
document.getElementById('xrayRestart').addEventListener('click', handleXrayRestart);
if (mobileXrayReload) mobileXrayReload.addEventListener('click', handleXrayReload);
if (mobileXrayRestart) mobileXrayRestart.addEventListener('click', handleXrayRestart);

async function loadRouters() {{
  const res = await fetch('/api/routers', {{cache: 'no-store'}});
  if (res.ok) {{
    const data = await res.json();
    window.ROUTERS = data.routers;
    syncRouterNotesState(window.ROUTERS);
    if (shouldDeferRouterRender()) pendingRouterRender = true;
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
    const data = await res.json().catch(() => ({{}}));
    ev.currentTarget.reset();
    if (routerNameInput) routerNameInput.dataset.touched = '';
    authMeta = data.auth || authMeta;
    renderAuthMeta();
    syncOwnerChrome();
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
  const noteRouterId = ev.target?.dataset?.routerNotesInput;
  if (noteRouterId) {{
    ev.stopPropagation();
    const value = normalizeRouterNotes(ev.target.value || '');
    const state = getNoteState(noteRouterId);
    setNoteState(noteRouterId, {{
      draft: value,
      dirty: value !== normalizeRouterNotes(state.saved || ''),
      error: '',
      message: ''
    }});
    return;
  }}
  const routerId = ev.target?.dataset?.wolPassword || ev.target?.dataset?.trafficPassword;
  if (!routerId) return;
  const value = ev.target.value || '';
  ev.stopPropagation();
  saveWolPassword(routerId, value);
  const wolState = getWolState(routerId);
  wolStateByRouter.set(String(routerId), Object.assign({{}}, wolState, {{sshPassword: value, error: '', message: ''}}));
  const trafficState = getTrafficState(routerId);
  trafficStateByRouter.set(String(routerId), Object.assign({{}}, trafficState, {{sshPassword: value, error: '', message: ''}}));
}});

cards.addEventListener('change', (ev) => {{
  const routerId = ev.target?.dataset?.wolSelect;
  if (!routerId) return;
  ev.stopPropagation();
  clearPendingWolBlurFlush();
  const mac = String(ev.target.value || '');
  setWolState(routerId, {{selectedMac: mac, pickerOpen: false, error: '', message: ''}});
  requestRouterRender();
  flushDeferredRouterRender();
}});

cards.addEventListener('focusin', (ev) => {{
  if (!ev.target?.matches?.('[data-wol-password],[data-wol-select],[data-traffic-password],[data-router-notes-input]')) return;
  clearPendingWolBlurFlush();
}});

cards.addEventListener('focusout', (ev) => {{
  if (!ev.target?.matches?.('[data-wol-password],[data-wol-select],[data-traffic-password],[data-router-notes-input]')) return;
  scheduleWolBlurFlush();
}});

cards.addEventListener('pointerup', (ev) => {{
  if (ev.pointerType === 'mouse') return;
  const wolPickButton = ev.target.closest('[data-wol-pick]');
  if (wolPickButton) {{
    ev.preventDefault();
    ev.stopPropagation();
    clearPendingWolBlurFlush();
    const routerId = wolPickButton.dataset.wolPick;
    const mac = wolPickButton.dataset.wolMac || '';
    rememberWolPointerAction('pick', routerId, mac);
    setWolState(routerId, {{selectedMac: mac, pickerOpen: false, error: '', message: ''}});
    requestRouterRender();
    flushDeferredRouterRender();
    return;
  }}
  const wolPickerToggle = ev.target.closest('[data-wol-picker-toggle]')?.dataset?.wolPickerToggle;
  if (wolPickerToggle) {{
    ev.preventDefault();
    ev.stopPropagation();
    clearPendingWolBlurFlush();
    rememberWolPointerAction('toggle', wolPickerToggle);
    const state = getWolState(wolPickerToggle);
    const pickerOpen = !state.pickerOpen;
    setWolState(wolPickerToggle, {{pickerOpen}});
  requestRouterRender();
    if (!pickerOpen) flushDeferredRouterRender();
  }}
}});

cards.addEventListener('click', async (ev) => {{
  const secureField = ev.target.closest('[data-wol-password],[data-traffic-password],[data-router-notes-input]');
  if (secureField) {{
    ev.stopPropagation();
    return;
  }}
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
  const wolToggleId = ev.target?.closest?.('[data-wol-toggle]')?.dataset?.wolToggle;
  if (wolToggleId) {{
    const state = getWolState(wolToggleId);
    const open = !state.open;
    setWolState(wolToggleId, {{open, pickerOpen: open ? state.pickerOpen : false, error: '', message: open ? state.message : ''}});
  requestRouterRender();
    if (!open) flushDeferredRouterRender();
    if (open) {{
      await loadWolDevices(wolToggleId, false);
    }}
    return;
  }}
  const trafficToggleId = ev.target?.closest?.('[data-traffic-toggle]')?.dataset?.trafficToggle;
  if (trafficToggleId) {{
    const state = getTrafficState(trafficToggleId);
    const open = !state.open;
    setTrafficState(trafficToggleId, {{open, error: '', message: open ? state.message : ''}});
    requestRouterRender();
    if (open) {{
      await loadTrafficClients(trafficToggleId, false);
    }}
    return;
  }}
  const notesToggleId = ev.target?.closest?.('[data-notes-toggle]')?.dataset?.notesToggle;
  if (notesToggleId) {{
    const router = selectedRouter(notesToggleId);
    const state = getNoteState(notesToggleId);
    const open = !state.open;
    const saved = normalizeRouterNotes((router && router.notes) || state.saved || '');
    setNoteState(notesToggleId, {{
      open,
      saved,
      draft: state.dirty && open ? state.draft : saved,
      error: '',
      message: open ? state.message : ''
    }});
    requestRouterRender();
    if (!open) flushDeferredRouterRender();
    return;
  }}
  const notesResetId = ev.target?.closest?.('[data-notes-reset]')?.dataset?.notesReset;
  if (notesResetId) {{
    const router = selectedRouter(notesResetId);
    const saved = normalizeRouterNotes((router && router.notes) || getNoteState(notesResetId).saved || '');
    setNoteState(notesResetId, {{
      open: true,
      saving: false,
      saved,
      draft: saved,
      dirty: false,
      error: '',
      message: 'Черновик заметок сброшен.'
    }});
    requestRouterRender();
    flushDeferredRouterRender();
    return;
  }}
  const notesSaveId = ev.target?.closest?.('[data-notes-save]')?.dataset?.notesSave;
  if (notesSaveId) {{
    const state = getNoteState(notesSaveId);
    const notes = normalizeRouterNotes(state.draft || '');
    setNoteState(notesSaveId, {{open: true, saving: true, error: '', message: ''}});
    requestRouterRender();
    const res = await fetch('/api/router/' + encodeURIComponent(notesSaveId) + '/notes', {{
      method: 'POST',
      body: new URLSearchParams({{notes}})
    }});
    if (!res.ok) {{
      setNoteState(notesSaveId, {{
        open: true,
        saving: false,
        error: await res.text() || 'Не удалось сохранить заметки.',
        message: ''
      }});
      requestRouterRender();
      flushDeferredRouterRender();
      return;
    }}
    const data = await res.json();
    if (data && data.router) replaceRouterInList(data.router);
    const saved = normalizeRouterNotes(data && data.router && data.router.notes || notes);
    setNoteState(notesSaveId, {{
      open: true,
      saving: false,
      saved,
      draft: saved,
      dirty: false,
      error: '',
      message: 'Заметки сохранены.'
    }});
    requestRouterRender();
    flushDeferredRouterRender();
    showRouterMsg('Заметки для роутера обновлены.');
    return;
  }}
  const wolPickButton = ev.target.closest('[data-wol-pick]');
  if (wolPickButton) {{
    ev.preventDefault();
    ev.stopPropagation();
    const routerId = wolPickButton.dataset.wolPick;
    const mac = wolPickButton.dataset.wolMac || '';
    if (consumeWolPointerAction('pick', routerId, mac)) return;
    clearPendingWolBlurFlush();
    setWolState(routerId, {{selectedMac: mac, pickerOpen: false, error: '', message: ''}});
  requestRouterRender();
    flushDeferredRouterRender();
    return;
  }}
  const wolPickerToggle = ev.target.closest('[data-wol-picker-toggle]')?.dataset?.wolPickerToggle;
  if (wolPickerToggle) {{
    ev.preventDefault();
    if (consumeWolPointerAction('toggle', wolPickerToggle)) return;
    clearPendingWolBlurFlush();
    const state = getWolState(wolPickerToggle);
    const pickerOpen = !state.pickerOpen;
    setWolState(wolPickerToggle, {{pickerOpen}});
  requestRouterRender();
    if (!pickerOpen) flushDeferredRouterRender();
    return;
  }}
  const wolRefreshId = ev.target?.closest?.('[data-wol-refresh]')?.dataset?.wolRefresh;
  if (wolRefreshId) {{
    await loadWolDevices(wolRefreshId, true);
    return;
  }}
  const trafficRefreshId = ev.target?.closest?.('[data-traffic-refresh]')?.dataset?.trafficRefresh;
  if (trafficRefreshId) {{
    await loadTrafficClients(trafficRefreshId, true);
    return;
  }}
  const trafficResetId = ev.target?.closest?.('[data-traffic-reset]')?.dataset?.trafficReset;
  if (trafficResetId) {{
    await resetTrafficClients(trafficResetId);
    return;
  }}
  const wolSendId = ev.target?.closest?.('[data-wol-send]')?.dataset?.wolSend;
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
  const id = ev.target?.dataset?.delete;
  if (!id) return;
  if (!confirm('Удалить роутер ' + id + '?')) return;
  const res = await fetch('/api/router/' + encodeURIComponent(id) + '/delete', {{method: 'POST'}});
  if (res.ok) await loadRouters();
}});

if (typeof mobileCardsMq.addEventListener === 'function') {{
  mobileCardsMq.addEventListener('change', () => {{
    if (shouldDeferRouterRender()) pendingRouterRender = true;
    else renderRouterView();
  }});
}} else if (typeof mobileCardsMq.addListener === 'function') {{
  mobileCardsMq.addListener(() => {{
    if (shouldDeferRouterRender()) pendingRouterRender = true;
    else renderRouterView();
  }});
}}
function updateMobileChromeToggles() {{
  syncHubMenuPlacement();
  updateRouterFormToggle();
  updateMobilePanelToggle(hubMenuToggle, headerActions, 'Открыть меню хаба', 'Скрыть меню хаба');
  updateMobilePanelToggle(routerStatsToggle, routerStats, 'Открыть статистику', 'Скрыть статистику');
  syncOwnerChrome();
}}
if (typeof mobileLayoutMq.addEventListener === 'function') {{
  mobileLayoutMq.addEventListener('change', updateMobileChromeToggles);
}} else if (typeof mobileLayoutMq.addListener === 'function') {{
  mobileLayoutMq.addListener(updateMobileChromeToggles);
}}

const authToggle = document.getElementById('authToggle');
const authMenu = document.getElementById('authMenu');
const authMenuClose = document.getElementById('authMenuClose');
const authMenuHeadTitle = authMenu ? authMenu.querySelector('.authMenuHead h2') : null;
const authMenuHeadLead = authMenu ? authMenu.querySelector('.authMenuHead p') : null;
const sessionList = document.getElementById('sessionList');
const revokeOtherSessions = document.getElementById('revokeOtherSessions');
const delegatedUsersGroup = document.getElementById('delegatedUsersGroup');
const delegatedUserForm = document.getElementById('delegatedUserForm');
const delegatedRouterAccess = document.getElementById('delegatedRouterAccess');
const delegatedUserList = document.getElementById('delegatedUserList');
const delegatedUserMsg = document.getElementById('delegatedUserMsg');
const delegatedUserReset = document.getElementById('delegatedUserReset');
const notifyList = document.getElementById('notifyList');
const notifyEnable = document.getElementById('notifyEnable');
const notifyTest = document.getElementById('notifyTest');
const notifyClear = document.getElementById('notifyClear');
const notifyStatus = document.getElementById('notifyStatus');
const backupFile = document.getElementById('backupFile');
const backupVpsHost = document.getElementById('backupVpsHost');
const backupPublicUrl = document.getElementById('backupPublicUrl');
const backupSshPassword = document.getElementById('backupSshPassword');
const backupRestore = document.getElementById('backupRestore');
const backupRewrite = document.getElementById('backupRewrite');
const backupMsg = document.getElementById('backupMsg');
const authForm = document.getElementById('authForm');
const authUsernameField = authForm && authForm.elements ? authForm.elements.namedItem('username') : null;
let authCaptchaModeField = null;
let authCaptchaSiteKeyField = null;
let authCaptchaSecretKeyField = null;
const authMsg = document.getElementById('authMsg');
const authSummary = document.getElementById('authSummary');
const totpState = document.getElementById('totpState');
const totpCurrentPassword = document.getElementById('totpCurrentPassword');
const totpSetupBtn = document.getElementById('totpSetupBtn');
const totpSecretBox = document.getElementById('totpSecretBox');
const totpSecretValue = document.getElementById('totpSecretValue');
const totpUriValue = document.getElementById('totpUriValue');
const totpCode = document.getElementById('totpCode');
const totpEnableBtn = document.getElementById('totpEnableBtn');
const totpDisableCurrentPassword = document.getElementById('totpDisableCurrentPassword');
const totpDisableCode = document.getElementById('totpDisableCode');
const totpDisableBtn = document.getElementById('totpDisableBtn');
const passkeySummary = document.getElementById('passkeySummary');
const passkeyCurrentPassword = document.getElementById('passkeyCurrentPassword');
const passkeyLabel = document.getElementById('passkeyLabel');
const passkeyRegisterBtn = document.getElementById('passkeyRegisterBtn');
const passkeyClientHint = document.getElementById('passkeyClientHint');
const passkeyList = document.getElementById('passkeyList');
const sshKeySummary = document.getElementById('sshKeySummary');
const sshKeyCurrentPassword = document.getElementById('sshKeyCurrentPassword');
const sshKeyLabel = document.getElementById('sshKeyLabel');
const sshKeyPublic = document.getElementById('sshKeyPublic');
const sshKeyAddBtn = document.getElementById('sshKeyAddBtn');
const sshKeyList = document.getElementById('sshKeyList');
const socialProviderUi = {{
  github: {{
    label: 'GitHub',
    secretLabel: 'Client Secret',
    requiresSecret: true,
    clientId: document.getElementById('githubClientId'),
    clientSecret: document.getElementById('githubClientSecret'),
    currentPassword: document.getElementById('githubCurrentPassword'),
    saveBtn: document.getElementById('githubSaveBtn'),
    linkBtn: document.getElementById('githubLinkBtn'),
    summary: document.getElementById('githubSummary'),
    hint: document.getElementById('githubHint'),
    accountList: document.getElementById('githubAccountList')
  }},
  vk: {{
    label: 'VK ID',
    secretLabel: 'Service Token',
    requiresSecret: false,
    clientId: document.getElementById('vkClientId'),
    clientSecret: document.getElementById('vkClientSecret'),
    currentPassword: document.getElementById('vkCurrentPassword'),
    saveBtn: document.getElementById('vkSaveBtn'),
    linkBtn: document.getElementById('vkLinkBtn'),
    summary: document.getElementById('vkSummary'),
    hint: document.getElementById('vkHint'),
    accountList: document.getElementById('vkAccountList')
  }}
}};
let authHideTimer;
let authMeta = {auth_bootstrap_json};
let authTotpTicket = '';
let authUsernameDraftDirty = false;
let authUsernameServerValue = authUsernameField && 'value' in authUsernameField ? String(authUsernameField.value || '') : '';
let socialLinkPopup = null;
let delegatedUserDraftId = '';
const OWNER_PASSWORD_REMEMBER_KEY = 'hub.owner-password.remember';
const OWNER_PASSWORD_STORAGE_KEY = 'hub.owner-password.value';
function hideOfflineStats() {{
  if (!offlineStatsExpanded) return;
  offlineStatsExpanded = false;
  renderRouterStats(window.ROUTERS || []);
}}
function closeAuthMenu(returnFocus = false) {{
  clearTimeout(authHideTimer);
  authMenu.hidden = true;
  if (authToggle) authToggle.setAttribute('aria-expanded', 'false');
  if (returnFocus && authToggle) authToggle.focus();
}}
if (authMenuHeadTitle) authMenuHeadTitle.textContent = '\u0414\u043e\u0441\u0442\u0443\u043f \u043a Hub';
if (authMenuHeadLead) authMenuHeadLead.hidden = true;
if (authMenuClose) authMenuClose.textContent = '\u0417\u0430\u043a\u0440\u044b\u0442\u044c';
function syncAuthUsernameField(force = false) {{
  if (!authUsernameField || !authMeta || !authMeta.username) return;
  const serverValue = String(authMeta.username || '');
  const isEditing = document.activeElement === authUsernameField;
  if (!force && (isEditing || authUsernameDraftDirty)) {{
    authUsernameServerValue = serverValue;
    return;
  }}
  authUsernameField.value = serverValue;
  authUsernameServerValue = serverValue;
  authUsernameDraftDirty = false;
}}
if (authUsernameField) {{
  const updateAuthUsernameDraftState = () => {{
    authUsernameDraftDirty = String(authUsernameField.value || '') !== authUsernameServerValue;
  }};
  authUsernameField.addEventListener('input', updateAuthUsernameDraftState);
  authUsernameField.addEventListener('change', updateAuthUsernameDraftState);
  authUsernameField.addEventListener('blur', updateAuthUsernameDraftState);
}}
function localizeAuthUi() {{
  const securityGroupTitle = document.getElementById('securityGroupTitle');
  const securityGroupLead = document.getElementById('securityGroupLead');
  const passwordSectionTitle = document.getElementById('passwordSectionTitle');
  const passwordSectionLead = document.getElementById('passwordSectionLead');
  const authForm = document.getElementById('authForm');
  const authInputs = authForm ? authForm.querySelectorAll('input') : [];
  if (securityGroupTitle) securityGroupTitle.textContent = '\u0411\u0435\u0437\u043e\u043f\u0430\u0441\u043d\u043e\u0441\u0442\u044c';
  if (securityGroupLead) securityGroupLead.textContent = '\u041f\u0430\u0440\u043e\u043b\u044c, 2FA, Passkey \u0438 SSH ED25519 \u0434\u043b\u044f \u0432\u0445\u043e\u0434\u0430 \u0432 Hub.';
  if (passwordSectionTitle) passwordSectionTitle.textContent = '\u041f\u0430\u0440\u043e\u043b\u044c';
  if (passwordSectionLead) passwordSectionLead.textContent = '\u0421\u043c\u0435\u043d\u0430 \u043b\u043e\u0433\u0438\u043d\u0430 \u0438 \u043f\u0430\u0440\u043e\u043b\u044f \u0432\u0445\u043e\u0434\u0430 \u0432 \u043f\u0430\u043d\u0435\u043b\u044c Hub.';
  if (authInputs[0]) authInputs[0].placeholder = 'Логин';
  if (authInputs[1]) authInputs[1].placeholder = 'Текущий пароль';
  if (authInputs[2]) authInputs[2].placeholder = 'Новый пароль';
  if (authInputs[3]) authInputs[3].placeholder = 'Повтори пароль';
  if (authForm) {{
    const saveBtn = authForm.querySelector('button');
    if (saveBtn) saveBtn.textContent = 'Сохранить';
  }}

  const setSectionCopy = (anchor, title, description) => {{
    const section = anchor ? anchor.closest('.authSection') : null;
    if (!section) return;
    const head = section.querySelector('.authSectionHead > div');
    if (!head) return;
    const heading = head.querySelector('h3');
    const lead = head.querySelector('p');
    if (heading) heading.textContent = title;
    if (lead) lead.textContent = description;
  }};

  setSectionCopy(
    totpState,
    'Пароль + 2FA',
    'Резервный вход через пароль. Если включена 2FA, дополнительно нужен TOTP-код из приложения.'
  );
  if (totpCurrentPassword) totpCurrentPassword.placeholder = 'Текущий пароль для настройки 2FA';
  if (totpSetupBtn) totpSetupBtn.textContent = 'Создать секрет 2FA';
  if (totpCode) totpCode.placeholder = 'Код из приложения 2FA';
  if (totpEnableBtn) totpEnableBtn.textContent = 'Включить 2FA';
  if (totpDisableCurrentPassword) totpDisableCurrentPassword.placeholder = 'Текущий пароль для отключения 2FA';
  if (totpDisableCode) totpDisableCode.placeholder = 'Текущий код 2FA';
  if (totpDisableBtn) totpDisableBtn.textContent = 'Выключить 2FA';

  const totpHeading = totpState ? totpState.parentElement?.querySelector('h3') : null;
  if (totpHeading) totpHeading.textContent = '2FA';

  setSectionCopy(
    passkeySummary,
    'Passkey',
    'Основной вход без пароля: Android, Windows Hello, Face ID, Touch ID и другие системные ключи.'
  );
  if (passkeyCurrentPassword) passkeyCurrentPassword.placeholder = 'Текущий пароль для passkey';
  if (passkeyLabel) passkeyLabel.placeholder = 'Метка ключа: Pixel 9, Windows Hello';
  if (passkeyRegisterBtn) passkeyRegisterBtn.textContent = 'Добавить passkey';

  setSectionCopy(
    sshKeySummary,
    'SSH ED25519',
    'Вход по SSH ED25519 ключу. Добавь публичный ключ, а на экране входа потом подписывай challenge.'
  );
  if (sshKeyCurrentPassword) sshKeyCurrentPassword.placeholder = 'Текущий пароль для SSH ключей';
  if (sshKeyLabel) sshKeyLabel.placeholder = 'Метка ключа: MacBook, Workstation';
  if (sshKeyAddBtn) sshKeyAddBtn.textContent = 'Добавить SSH ED25519';
  const sshSectionHint = sshKeyList ? sshKeyList.parentElement.querySelector('.authHint') : null;
  if (sshSectionHint) sshSectionHint.textContent = 'Поддерживается только формат `ssh-ed25519`.';

  const sessionGroupTitle = document.getElementById('sessionGroupTitle');
  const sessionGroupLead = document.getElementById('sessionGroupLead');
  if (sessionGroupTitle) sessionGroupTitle.textContent = '\u0423\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435 \u0441\u0435\u0441\u0441\u0438\u044f\u043c\u0438';
  if (sessionGroupLead) sessionGroupLead.textContent = '\u0410\u043a\u0442\u0438\u0432\u043d\u044b\u0435 \u0432\u0445\u043e\u0434\u044b \u0432 Hub \u0438 \u0431\u044b\u0441\u0442\u0440\u043e\u0435 \u0437\u0430\u0432\u0435\u0440\u0448\u0435\u043d\u0438\u0435 \u043b\u0438\u0448\u043d\u0438\u0445 \u0441\u0435\u0441\u0441\u0438\u0439.';
  const sessionHead = document.getElementById('revokeOtherSessions')?.closest('.sessionHead');
  if (sessionHead) {{
    const heading = sessionHead.querySelector('h3');
    if (heading) heading.textContent = 'Управление сессиями';
  }}
  const revokeBtn = document.getElementById('revokeOtherSessions');
  if (revokeBtn) revokeBtn.textContent = 'Завершить остальные';

  const notifyGroupTitle = document.getElementById('notifyGroupTitle');
  const notifyGroupLead = document.getElementById('notifyGroupLead');
  if (notifyGroupTitle) notifyGroupTitle.textContent = '\u0423\u0432\u0435\u0434\u043e\u043c\u043b\u0435\u043d\u0438\u044f';
  if (notifyGroupLead) notifyGroupLead.textContent = '\u0057\u0065\u0062\u0020\u0050\u0075\u0073\u0068 \u0434\u043b\u044f \u0432\u0445\u043e\u0434\u043e\u0432, \u0441\u043c\u0435\u043d\u044b IP \u0438 \u0441\u043e\u0431\u044b\u0442\u0438\u0439 VPS/Hub.';
  const notifyBox = document.getElementById('notifyEnable')?.closest('.notifyBox');
  if (notifyBox) {{
    const heading = notifyBox.querySelector('.sessionHead h3');
    const hint = notifyBox.querySelector('.notifyHint');
    if (heading) heading.textContent = 'Уведомления';
    if (hint) hint.textContent = 'Web Push для входов в панель, смены IP и запуска VPS/Hub. На iPhone включай из приложения Hub с экрана Домой.';
  }}
  const notifyEnableBtn = document.getElementById('notifyEnable');
  const notifyTestBtn = document.getElementById('notifyTest');
  const notifyClearBtn = document.getElementById('notifyClear');
  if (notifyEnableBtn) notifyEnableBtn.textContent = 'Включить';
  if (notifyTestBtn) notifyTestBtn.textContent = 'Проверить Push';
  if (notifyClearBtn) notifyClearBtn.textContent = 'Очистить';

  const backupGroupTitle = document.getElementById('backupGroupTitle');
  const backupGroupLead = document.getElementById('backupGroupLead');
  if (backupGroupTitle) backupGroupTitle.textContent = '\u0420\u0435\u0437\u0435\u0440\u0432\u043d\u0430\u044f \u043a\u043e\u043f\u0438\u044f VPS';
  if (backupGroupLead) backupGroupLead.textContent = '\u0420\u0435\u0437\u0435\u0440\u0432\u043d\u0430\u044f \u043a\u043e\u043f\u0438\u044f, \u0432\u043e\u0441\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d\u0438\u0435 \u0438 \u043f\u0435\u0440\u0435\u043d\u043e\u0441 Hub \u043d\u0430 \u043d\u043e\u0432\u044b\u0439 VPS.';
  const backupBox = document.getElementById('backupDownload')?.closest('.backupBox');
  if (backupBox) {{
    const heading = backupBox.querySelector('.sessionHead h3');
    const hint = backupBox.querySelector('.backupHint');
    if (heading) heading.textContent = 'Резервная копия VPS';
    if (hint) hint.textContent = 'Архив сохраняет базу роутеров, логин, токены Hub/агента, push-ключи и уведомления. Перед восстановлением на другом IP укажи новый VPS host или Public URL.';
  }}
  const backupDownload = document.getElementById('backupDownload');
  if (backupDownload) backupDownload.textContent = 'Скачать';
}}
localizeAuthUi();
function authIsOwner() {{
  return !!(authMeta && authMeta.is_owner);
}}
function ownerPasswordRememberEnabled() {{
  try {{
    return window.localStorage.getItem(OWNER_PASSWORD_REMEMBER_KEY) === '1';
  }} catch (err) {{
    return false;
  }}
}}
function readRememberedOwnerPassword() {{
  if (!ownerPasswordRememberEnabled()) return '';
  try {{
    return String(window.localStorage.getItem(OWNER_PASSWORD_STORAGE_KEY) || '');
  }} catch (err) {{
    return '';
  }}
}}
function writeRememberedOwnerPassword(value) {{
  if (!ownerPasswordRememberEnabled()) return;
  try {{
    window.localStorage.setItem(OWNER_PASSWORD_STORAGE_KEY, String(value || ''));
  }} catch (err) {{}}
}}
function ownerPasswordInputs() {{
  const seen = new Set();
  const fields = [
    authForm && authForm.elements ? authForm.elements.namedItem('current_password') : null,
    delegatedUserForm && delegatedUserForm.elements ? delegatedUserForm.elements.namedItem('current_password') : null,
    totpCurrentPassword,
    totpDisableCurrentPassword,
    passkeyCurrentPassword,
    sshKeyCurrentPassword,
    ...Object.values(socialProviderUi || {{}}).map((ui) => ui && ui.currentPassword)
  ];
  return fields.filter((field) => {{
    if (!(field instanceof HTMLInputElement)) return false;
    if (seen.has(field)) return false;
    seen.add(field);
    return true;
  }});
}}
function ownerPasswordRememberCheckboxes() {{
  return Array.from(document.querySelectorAll('input[data-owner-password-remember]')).filter((field) => field instanceof HTMLInputElement);
}}
function syncOwnerPasswordRememberCheckboxes(enabled = ownerPasswordRememberEnabled()) {{
  ownerPasswordRememberCheckboxes().forEach((field) => {{
    field.checked = enabled;
  }});
}}
function syncOwnerPasswordInputs(value = null, source = null) {{
  const nextValue = value === null ? readRememberedOwnerPassword() : String(value || '');
  ownerPasswordInputs().forEach((field) => {{
    if (!(field instanceof HTMLInputElement) || field === source) return;
    if (field.value !== nextValue) field.value = nextValue;
  }});
}}
function currentOwnerPasswordSnapshot() {{
  for (const field of ownerPasswordInputs()) {{
    if (field.value) return String(field.value);
  }}
  return readRememberedOwnerPassword();
}}
function restoreOwnerPasswordInput(field, nextValue = null) {{
  if (!(field instanceof HTMLInputElement)) return;
  if (!ownerPasswordRememberEnabled()) {{
    field.value = '';
    return;
  }}
  const value = nextValue === null ? readRememberedOwnerPassword() : String(nextValue || '');
  writeRememberedOwnerPassword(value);
  field.value = value;
  syncOwnerPasswordInputs(value, field);
}}
function setOwnerPasswordRememberEnabled(enabled, preferredValue = '') {{
  if (!enabled) {{
    try {{
      window.localStorage.removeItem(OWNER_PASSWORD_REMEMBER_KEY);
      window.localStorage.removeItem(OWNER_PASSWORD_STORAGE_KEY);
    }} catch (err) {{}}
    syncOwnerPasswordRememberCheckboxes(false);
    syncOwnerPasswordInputs('');
    return;
  }}
  const nextValue = String(preferredValue || currentOwnerPasswordSnapshot() || '');
  try {{
    window.localStorage.setItem(OWNER_PASSWORD_REMEMBER_KEY, '1');
    window.localStorage.setItem(OWNER_PASSWORD_STORAGE_KEY, nextValue);
  }} catch (err) {{}}
  syncOwnerPasswordRememberCheckboxes(true);
  syncOwnerPasswordInputs(nextValue);
}}
function installOwnerPasswordRememberUi() {{
  ownerPasswordInputs().forEach((field) => {{
    if (!(field instanceof HTMLInputElement) || field.dataset.ownerPasswordRememberReady === '1') return;
    field.dataset.ownerPasswordRememberReady = '1';
    const toggle = document.createElement('label');
    toggle.className = 'wide authRememberToggle';
    toggle.innerHTML = '<input type="checkbox" data-owner-password-remember> Запомнить на этом устройстве';
    field.insertAdjacentElement('afterend', toggle);
    const checkbox = toggle.querySelector('input[data-owner-password-remember]');
    if (checkbox instanceof HTMLInputElement) {{
      checkbox.checked = ownerPasswordRememberEnabled();
      checkbox.addEventListener('change', () => {{
        if (checkbox.checked) {{
          setOwnerPasswordRememberEnabled(true, field.value || currentOwnerPasswordSnapshot());
          return;
        }}
        setOwnerPasswordRememberEnabled(false);
      }});
    }}
    field.addEventListener('input', () => {{
      if (!ownerPasswordRememberEnabled()) return;
      writeRememberedOwnerPassword(field.value || '');
      syncOwnerPasswordInputs(field.value || '', field);
    }});
    field.addEventListener('focus', () => {{
      if (!field.value && ownerPasswordRememberEnabled()) restoreOwnerPasswordInput(field);
    }});
  }});
  syncOwnerPasswordRememberCheckboxes();
  if (ownerPasswordRememberEnabled()) syncOwnerPasswordInputs();
}}
function authCanAddRouter() {{
  if (authIsOwner()) return true;
  if (authMeta && Object.prototype.hasOwnProperty.call(authMeta, 'can_add_router')) {{
    return !!authMeta.can_add_router;
  }}
  const assigned = Array.isArray(authMeta && authMeta.assigned_routers) ? authMeta.assigned_routers : [];
  return assigned.some((item) => Array.isArray(item && item.flags) && item.flags.includes('G'));
}}
function setDelegatedUserMessage(text, bad = false) {{
  if (!delegatedUserMsg) return;
  delegatedUserMsg.hidden = !text;
  delegatedUserMsg.className = 'msg' + (bad ? ' bad' : '');
  delegatedUserMsg.textContent = text || '';
}}
function delegatedUserFormData() {{
  if (!delegatedUserForm || !delegatedUserForm.elements) return null;
  return {{
    id: delegatedUserForm.elements.namedItem('id'),
    username: delegatedUserForm.elements.namedItem('username'),
    enabled: delegatedUserForm.elements.namedItem('enabled'),
    password: delegatedUserForm.elements.namedItem('password'),
    passwordConfirm: delegatedUserForm.elements.namedItem('password_confirm'),
    currentPassword: delegatedUserForm.elements.namedItem('current_password')
  }};
}}
function clearDelegatedUserAutofill({{preserveUsername = false}} = {{}}) {{
  const fields = delegatedUserFormData();
  if (!fields) return;
  if (!preserveUsername && fields.username) fields.username.value = '';
  if (fields.password) fields.password.value = '';
  if (fields.passwordConfirm) fields.passwordConfirm.value = '';
  if (fields.currentPassword) restoreOwnerPasswordInput(fields.currentPassword);
}}
function scheduleDelegatedUserAutofillCleanup(options = {{}}) {{
  clearDelegatedUserAutofill(options);
  window.requestAnimationFrame(() => clearDelegatedUserAutofill(options));
  window.setTimeout(() => clearDelegatedUserAutofill(options), 80);
  window.setTimeout(() => clearDelegatedUserAutofill(options), 260);
}}
function collectDelegatedRouterFlags() {{
  const routerFlags = {{}};
  if (!delegatedRouterAccess) return routerFlags;
  delegatedRouterAccess.querySelectorAll('input[data-router-flag]').forEach((input) => {{
    if (!input.checked) return;
    const routerId = String(input.dataset.routerFlag || '');
    const flag = String(input.value || '').toUpperCase();
    if (!routerId || !flag) return;
    if (!routerFlags[routerId]) routerFlags[routerId] = [];
    if (!routerFlags[routerId].includes(flag)) routerFlags[routerId].push(flag);
  }});
  Object.keys(routerFlags).forEach((routerId) => routerFlags[routerId].sort());
  return routerFlags;
}}
function syncDelegatedRouterAccessMasterFlags() {{
  if (!delegatedRouterAccess) return;
  ['A', 'B', 'G'].forEach((flag) => {{
    const master = delegatedRouterAccess.querySelector(`input[data-router-all-flag="${{flag}}"]`);
    if (!master) return;
    const inputs = Array.from(delegatedRouterAccess.querySelectorAll(`input[data-router-flag][value="${{flag}}"]`));
    if (!inputs.length) {{
      master.checked = false;
      master.indeterminate = false;
      return;
    }}
    const checkedCount = inputs.filter((input) => input.checked).length;
    master.checked = checkedCount === inputs.length;
    master.indeterminate = checkedCount > 0 && checkedCount < inputs.length;
  }});
}}
function renderDelegatedRouterAccess(selectedMap = null) {{
  if (!delegatedRouterAccess) return;
  const routerOptions = Array.isArray(authMeta && authMeta.router_options) ? authMeta.router_options : [];
  const selected = selectedMap && typeof selectedMap === 'object' ? selectedMap : {{}};
  if (!routerOptions.length) {{
    delegatedRouterAccess.innerHTML = '<div class="authEmpty">Сначала добавь роутеры в Hub, потом можно выдавать права пользователям.</div>';
    return;
  }}
  const allRoutersRow = `
    <div class="authRow authRowAll">
      <div>
        <div class="authRowTitle"><span>Все роутеры</span></div>
        <div class="authRowMeta">Массово включает или снимает права A/B/G сразу для всех роутеров ниже.</div>
      </div>
      <div class="authPills authRouterFlags">
        ${{['A', 'B', 'G'].map((flag) => `
          <label class="authPill authFlagPill">
            <input type="checkbox" data-router-all-flag="${{flag}}" value="${{flag}}">
            <span>${{flag}}</span>
          </label>
        `).join('')}}
      </div>
    </div>
  `;
  delegatedRouterAccess.innerHTML = allRoutersRow + routerOptions.map((router) => {{
    const routerId = String(router.id || '');
    const routerName = String(router.name || routerId || 'router');
    const flags = Array.isArray(selected[routerId]) ? selected[routerId] : [];
    return `
      <div class="authRow">
        <div>
          <div class="authRowTitle"><span>${{escapeHtml(routerName)}}</span></div>
          <div class="authRowMeta">${{escapeHtml(routerId)}}${{router.role ? ' · ' + escapeHtml(router.role) : ''}}</div>
        </div>
        <div class="authPills authRouterFlags">
          ${{['A', 'B', 'G'].map((flag) => `
            <label class="authPill authFlagPill">
              <input type="checkbox" data-router-flag="${{escapeAttr(routerId)}}" value="${{flag}}" ${{flags.includes(flag) ? 'checked' : ''}}>
              <span>${{flag}}</span>
            </label>
          `).join('')}}
        </div>
      </div>
    `;
  }}).join('');
  syncDelegatedRouterAccessMasterFlags();
}}
if (delegatedRouterAccess) {{
  delegatedRouterAccess.addEventListener('change', (event) => {{
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    if (target.matches('input[data-router-all-flag]')) {{
      const flag = String(target.value || '').toUpperCase();
      delegatedRouterAccess.querySelectorAll(`input[data-router-flag][value="${{flag}}"]`).forEach((input) => {{
        input.checked = target.checked;
      }});
      target.indeterminate = false;
    }}
    syncDelegatedRouterAccessMasterFlags();
  }});
}}
function resetDelegatedUserForm(clearMessage = true) {{
  if (!delegatedUserForm) return;
  delegatedUserDraftId = '';
  delegatedUserForm.reset();
  const fields = delegatedUserFormData();
  if (fields && fields.id) fields.id.value = '';
  if (fields && fields.username) fields.username.value = '';
  if (fields && fields.enabled) fields.enabled.checked = true;
  renderDelegatedRouterAccess();
  scheduleDelegatedUserAutofillCleanup();
  if (clearMessage) setDelegatedUserMessage('');
}}
function fillDelegatedUserForm(user) {{
  const fields = delegatedUserFormData();
  if (!fields || !user) return;
  delegatedUserDraftId = String(user.id || '');
  fields.id.value = delegatedUserDraftId;
  fields.username.value = String(user.username || '');
  fields.enabled.checked = !!user.enabled;
  fields.password.value = '';
  fields.passwordConfirm.value = '';
  restoreOwnerPasswordInput(fields.currentPassword);
  renderDelegatedRouterAccess(user.router_flags || {{}});
  scheduleDelegatedUserAutofillCleanup({{preserveUsername: true}});
  setDelegatedUserMessage('Редактируется пользователь ' + String(user.username || ''));
}}
function renderDelegatedUsers(items) {{
  if (!delegatedUserList) return;
  const list = Array.isArray(items) ? items : [];
  if (!list.length) {{
    delegatedUserList.innerHTML = '<div class="authEmpty">Ограниченные пользователи пока не созданы</div>';
    return;
  }}
  delegatedUserList.innerHTML = list.map((item) => {{
    const access = Array.isArray(item.router_access) ? item.router_access : [];
    const accessText = access.length
      ? access.map((row) => `${{row.name || row.id}} [${{row.flag_label || '-'}}]`).join(' · ')
      : 'Без выданных роутеров';
    const statusText = item.online ? 'Сейчас в Hub' : (item.enabled ? 'Активен' : 'Выключен');
    const lastSeenText = Number(item.last_seen_at || 0) > 0 ? formatSessionTime(item.last_seen_at) : 'Ещё не заходил';
    const lastLoginText = Number(item.last_login_at || 0) > 0 ? formatSessionTime(item.last_login_at) : 'Ещё не заходил';
    const lastIpText = String(item.last_ip || '').trim() || 'Неизвестно';
    const lastClientText = String(item.last_client || '').trim() || 'Неизвестно';
    return `
      <div class="authRow">
        <div>
          <div class="authRowTitle"><span>${{escapeHtml(item.username || 'user')}}</span></div>
          <div class="authRowMeta">${{escapeHtml(statusText)}}<br>Роутеров: ${{Number(item.router_count || 0)}}<br>Входов: ${{Number(item.login_count || 0)}}<br>Активных сессий: ${{Number(item.session_count || 0)}}<br>Последний вход: ${{escapeHtml(lastLoginText)}}<br>Последняя активность: ${{escapeHtml(lastSeenText)}}<br>IP: ${{escapeHtml(lastIpText)}}<br>Клиент: ${{escapeHtml(lastClientText)}}<br>${{escapeHtml(accessText)}}</div>
        </div>
        <div class="notifyActions">
          <button class="sessionBtn" type="button" data-delegated-edit="${{escapeAttr(item.id || '')}}">Изменить</button>
          <button class="sessionBtn bad" type="button" data-delegated-revoke="${{escapeAttr(item.id || '')}}">Завершить</button>
          <button class="sessionBtn bad" type="button" data-delegated-delete="${{escapeAttr(item.id || '')}}">Удалить</button>
        </div>
      </div>
    `;
  }}).join('');
}}
function renderDelegatedOverview(meta) {{
  const assigned = Array.isArray(meta.assigned_routers) ? meta.assigned_routers : [];
  authSummary.innerHTML = assigned.length
    ? assigned.map((item) => `<span class="authPill">${{escapeHtml((item.name || item.id) + ' [' + (item.flag_label || '-') + ']')}}</span>`).join('')
    : '<span class="authPill off">Роутеры не назначены</span>';
  ['securityGroup', 'socialGroup', 'notifyGroup', 'backupGroup', 'delegatedUsersGroup'].forEach((id) => {{
    const node = document.getElementById(id);
    if (node) node.hidden = true;
  }});
  const sessionGroup = document.getElementById('sessionGroup');
  if (sessionGroup) sessionGroup.hidden = false;
  if (authMenuHeadLead) {{
    authMenuHeadLead.hidden = false;
    authMenuHeadLead.textContent = assigned.length
      ? 'Доступ только к выбранным роутерам. Глобальные функции Hub и VPS скрыты.'
      : 'Для этой учётки пока не выданы роутеры.';
  }}
}}
function syncOwnerChrome() {{
  const owner = authIsOwner();
  const canAddRouter = authCanAddRouter();
  ['vpsTerminalLink', 'xrayReload', 'xrayRestart'].forEach((id) => {{
    const node = document.getElementById(id);
    if (node) node.hidden = !owner || mobileLayoutMq.matches;
  }});
  if (mobileOwnerTools) mobileOwnerTools.hidden = !owner || !mobileLayoutMq.matches || !!(headerActions && headerActions.hidden);
  if (mobileVpsTerminal) mobileVpsTerminal.hidden = !owner;
  if (mobileXrayReload) mobileXrayReload.hidden = !owner;
  if (mobileXrayRestart) mobileXrayRestart.hidden = !owner;
  if (routerFormToggle) routerFormToggle.hidden = !canAddRouter;
  if (!canAddRouter && routerFormWrap) routerFormWrap.hidden = true;
}}
function showAuthMenu() {{
  clearTimeout(authHideTimer);
  hideOfflineStats();
  const wasHidden = authMenu.hidden;
  authMenu.hidden = false;
  if (authToggle) authToggle.setAttribute('aria-expanded', 'true');
  if (wasHidden) loadAuthMeta({{silent: true}}).catch(() => {{}});
}}
authToggle.addEventListener('click', (ev) => {{
  ev.stopPropagation();
  if (authMenu.hidden) {{
    showAuthMenu();
  }} else {{
    closeAuthMenu();
  }}
}});
if (authMenuClose) {{
  authMenuClose.addEventListener('click', (ev) => {{
    ev.stopPropagation();
    closeAuthMenu(true);
  }});
}}
authMenu.addEventListener('click', (ev) => ev.stopPropagation());
document.addEventListener('click', () => {{
  closeAuthMenu();
}});
document.addEventListener('keydown', (ev) => {{
  if (ev.key === 'Escape' && authMenu && !authMenu.hidden) {{
    ev.stopPropagation();
    closeAuthMenu(true);
  }}
}});
document.addEventListener('visibilitychange', () => {{
  syncTrafficAutoRefresh();
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

function authMethodText(value) {{
  const method = String(value || '').toLowerCase();
  if (method === 'passkey') return 'Passkey';
  if (method === 'ed25519') return 'SSH ED25519';
  if (method === 'password+totp') return 'Пароль + 2FA';
  if (method === 'password') return 'Пароль';
  return 'Авторизация';
}}

function setAuthMessage(text, bad = false) {{
  if (!authMsg) return;
  authMsg.hidden = !text;
  authMsg.className = 'msg' + (bad ? ' bad' : '');
  authMsg.textContent = text || '';
}}

function resetTotpSetup() {{
  authTotpTicket = '';
  if (totpSecretBox) totpSecretBox.hidden = true;
  if (totpSecretValue) totpSecretValue.textContent = '';
  if (totpUriValue) totpUriValue.textContent = '';
  if (totpCode) totpCode.value = '';
}}

function ensureCaptchaSettingsFields() {{
  if (!authForm) return;
  if (!authForm.querySelector('[data-captcha-config]')) {{
    const wrap = document.createElement('div');
    wrap.className = 'wide';
    wrap.dataset.captchaConfig = 'true';
    wrap.innerHTML = `
      <div style="display:grid;gap:8px;padding:10px 12px;border:1px solid rgba(148,163,184,.18);border-radius:8px;background:rgba(255,255,255,.03)">
        <strong style="font-size:13px">Капча на экране входа</strong>
        <span style="color:var(--muted);font-size:12px;line-height:1.45">Можно оставить цифровую капчу или переключить вход на Google reCAPTCHA.</span>
        <select class="wide" name="captcha_mode">
          <option value="{CAPTCHA_MODE_DIGITS}">Капча: цифры</option>
          <option value="{CAPTCHA_MODE_RECAPTCHA}">Капча: Google reCAPTCHA</option>
        </select>
        <input class="wide" name="captcha_site_key" placeholder="reCAPTCHA Site Key">
        <input class="wide" name="captcha_secret_key" placeholder="reCAPTCHA Secret Key">
      </div>
    `;
    const saveBtn = authForm.querySelector('button');
    authForm.insertBefore(wrap, saveBtn || null);
  }}
  authCaptchaModeField = authForm.elements ? authForm.elements.namedItem('captcha_mode') : null;
  authCaptchaSiteKeyField = authForm.elements ? authForm.elements.namedItem('captcha_site_key') : null;
  authCaptchaSecretKeyField = authForm.elements ? authForm.elements.namedItem('captcha_secret_key') : null;
}}

function authB64urlToBytes(value) {{
  const normalized = String(value || '').replace(/-/g, '+').replace(/_/g, '/');
  const padded = normalized + '='.repeat((4 - normalized.length % 4) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}}

function authBytesToB64url(buffer) {{
  const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer || []);
  let binary = '';
  for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
  return btoa(binary).replace(/\\+/g, '-').replace(/\\//g, '_').replace(/=+$/g, '');
}}

function decodeCredentialCreationOptions(options) {{
  const publicKey = Object.assign({{}}, options.publicKey || {{}});
  publicKey.challenge = authB64urlToBytes(publicKey.challenge || '');
  if (publicKey.user && publicKey.user.id) {{
    publicKey.user = Object.assign({{}}, publicKey.user, {{id: authB64urlToBytes(publicKey.user.id)}});
  }}
  publicKey.excludeCredentials = Array.isArray(publicKey.excludeCredentials)
    ? publicKey.excludeCredentials.map((item) => Object.assign({{}}, item, {{id: authB64urlToBytes(item.id || '')}}))
    : [];
  return {{publicKey}};
}}

function encodeAttestation(credential) {{
  return {{
    id: credential.id,
    rawId: authBytesToB64url(credential.rawId),
    type: credential.type,
    authenticatorAttachment: credential.authenticatorAttachment || null,
    clientExtensionResults: credential.getClientExtensionResults ? credential.getClientExtensionResults() : {{}},
    response: {{
      clientDataJSON: authBytesToB64url(credential.response.clientDataJSON),
      attestationObject: authBytesToB64url(credential.response.attestationObject)
    }}
  }};
}}

function renderPasskeyRows(items) {{
  const list = Array.isArray(items) ? items : [];
  if (!list.length) {{
    passkeyList.innerHTML = '<div class="authEmpty">Passkey пока не добавлены</div>';
    return;
  }}
  passkeyList.innerHTML = list.map((item) => `
    <div class="authRow">
      <div>
        <div class="authRowTitle"><span>${{escapeHtml(item.label || 'Passkey')}}</span></div>
        <div class="authRowMeta">
          Добавлен: ${{formatSessionTime(item.created_at)}}<br>
          Последнее использование: ${{formatSessionTime(item.last_used_at)}}
        </div>
      </div>
      <button class="sessionBtn bad" type="button" data-passkey-remove="${{escapeAttr(item.id || '')}}">Удалить</button>
    </div>
  `).join('');
}}

function renderSshKeyRows(items) {{
  const list = Array.isArray(items) ? items : [];
  if (!list.length) {{
    sshKeyList.innerHTML = '<div class="authEmpty">SSH ED25519 ключи пока не добавлены</div>';
    return;
  }}
  sshKeyList.innerHTML = list.map((item) => `
    <div class="authRow">
      <div>
        <div class="authRowTitle"><span>${{escapeHtml(item.label || 'SSH ED25519')}}</span></div>
        <div class="authRowMeta">
          Добавлен: ${{formatSessionTime(item.created_at)}}<br>
          Последнее использование: ${{formatSessionTime(item.last_used_at)}}
        </div>
        <div class="authRowKey">${{escapeHtml(item.public_key || '')}}</div>
      </div>
      <button class="sessionBtn bad" type="button" data-ssh-key-remove="${{escapeAttr(item.id || '')}}">Удалить</button>
    </div>
  `).join('');
}}

function renderSocialAccountRows(provider, items) {{
  const ui = socialProviderUi[provider];
  if (!ui || !ui.accountList) return;
  const list = Array.isArray(items) ? items : [];
  if (!list.length) {{
    ui.accountList.innerHTML = `<div class="authEmpty">Пока нет привязанных аккаунтов ${{escapeHtml(ui.label)}}</div>`;
    return;
  }}
  ui.accountList.innerHTML = list.map((item) => {{
    const name = item.label || item.name || item.login || item.email || `${{ui.label}}`;
    const handleParts = [item.login ? `@${{item.login}}` : '', item.email || '', item.profile_url || ''].filter(Boolean);
    const avatar = item.avatar_url
      ? `<img src="${{escapeAttr(item.avatar_url)}}" alt="">`
      : escapeHtml((name || ui.label).trim().slice(0, 1).toUpperCase() || ui.label.slice(0, 1));
    return `
      <div class="authRow">
        <div class="authSocialIdentity">
          <div class="authSocialAvatar">${{avatar}}</div>
          <div>
            <div class="authSocialName">${{escapeHtml(name)}}</div>
            <div class="authSocialHandle">${{handleParts.length ? escapeHtml(handleParts.join(' • ')) : 'Профиль привязан к Hub'}}</div>
            <div class="authRowMeta">
              Привязан: ${{formatSessionTime(item.linked_at)}}<br>
              Последний вход: ${{formatSessionTime(item.last_used_at)}}
            </div>
          </div>
        </div>
        <button class="sessionBtn bad" type="button" data-social-unlink="${{escapeAttr(item.id || '')}}" data-social-provider="${{escapeAttr(provider)}}">Отвязать</button>
      </div>
    `;
  }}).join('');
}}

function renderSocialProvider(provider, info) {{
  const ui = socialProviderUi[provider];
  if (!ui) return;
  const meta = info && typeof info === 'object' ? info : {{}};
  const configured = !!meta.configured;
  const linkedCount = Number(meta.linked_count || (Array.isArray(meta.accounts) ? meta.accounts.length : 0) || 0);
  const enabled = !!meta.enabled;
  const setupHint = ui.requiresSecret
    ? `Сначала сохрани Client ID и ${{ui.secretLabel}} для ${{ui.label}}.`
    : `Сначала сохрани Client ID для ${{ui.label}}. Если приложение confidential, во втором поле нужен ${{ui.secretLabel}}.`;
  if (ui.clientId && document.activeElement !== ui.clientId) ui.clientId.value = meta.client_id || '';
  if (ui.clientSecret && document.activeElement !== ui.clientSecret) ui.clientSecret.value = meta.client_secret || '';
  if (ui.summary) {{
    if (enabled) {{
      ui.summary.textContent = `${{ui.label}}: ${{linkedCount}}`;
      ui.summary.className = 'authSectionState';
    }} else if (configured) {{
      ui.summary.textContent = 'Ждёт привязку';
      ui.summary.className = 'authSectionState warn';
    }} else {{
      ui.summary.textContent = 'Не настроен';
      ui.summary.className = 'authSectionState off';
    }}
  }}
  if (ui.linkBtn) ui.linkBtn.disabled = !configured;
  if (ui.hint) {{
    ui.hint.textContent = enabled
      ? `На странице входа показывается ${{linkedCount}} привяз. аккаунт(ов) ${{ui.label}}.`
      : configured
        ? `OAuth настроен, но для входа нужен хотя бы один привязанный аккаунт ${{ui.label}}.`
        : setupHint;
  }}
  renderSocialAccountRows(provider, meta.accounts || []);
}}

function renderAuthMeta() {{
  const meta = authMeta || {{}};
  const totpEnabled = !!meta.totp_enabled;
  const passkeyCount = Number(meta.passkey_count || (Array.isArray(meta.passkeys) ? meta.passkeys.length : 0) || 0);
  const sshCount = Array.isArray(meta.ssh_keys) ? meta.ssh_keys.length : 0;
  const socialEntries = Object.entries(meta.social || {{}})
    .filter(([, item]) => Number(item && item.linked_count || 0) > 0)
    .map(([provider, item]) => `<span class="authPill">${{escapeHtml((item && item.label) || socialProviderUi[provider]?.label || 'OAuth')}}: ${{Number(item && item.linked_count || 0)}}</span>`);
  authSummary.innerHTML = [
    '<span class="authPill">Пароль</span>',
    `<span class="authPill ${{totpEnabled ? '' : 'off'}}">${{totpEnabled ? '2FA включена' : '2FA выключена'}}</span>`,
    `<span class="authPill ${{passkeyCount ? '' : 'off'}}">Passkey: ${{passkeyCount}}</span>`,
    `<span class="authPill ${{sshCount ? '' : 'off'}}">ED25519: ${{sshCount}}</span>`,
    ...socialEntries
  ].join('');
  totpState.textContent = totpEnabled ? '2FA включена' : '2FA выключена';
  totpState.className = 'authSectionState' + (totpEnabled ? '' : ' off');
  passkeySummary.textContent = passkeyCount ? `Passkey: ${{passkeyCount}}` : '0 ключей';
  passkeySummary.className = 'authSectionState' + (passkeyCount ? '' : ' off');
  sshKeySummary.textContent = sshCount ? `ED25519: ${{sshCount}}` : '0 ключей';
  sshKeySummary.className = 'authSectionState' + (sshCount ? '' : ' off');
  if (!window.isSecureContext || !window.PublicKeyCredential) {{
    passkeyClientHint.textContent = 'Passkey требует HTTPS и поддержку WebAuthn в браузере.';
    passkeySummary.className = 'authSectionState warn';
  }} else if (!meta.passkeys_supported) {{
    passkeyClientHint.textContent = 'На сервере не установлен модуль WebAuthn. Запусти свежий install-vps.sh.';
    passkeySummary.className = 'authSectionState warn';
  }} else if (!passkeyCount) {{
    passkeyClientHint.textContent = 'Можно зарегистрировать первый passkey прямо здесь.';
  }} else {{
    passkeyClientHint.textContent = 'Passkey готов к использованию на экране входа.';
  }}
  renderPasskeyRows(meta.passkeys || []);
  renderSshKeyRows(meta.ssh_keys || []);
  Object.keys(socialProviderUi).forEach((provider) => renderSocialProvider(provider, meta.social && meta.social[provider]));
}}

function renderAuthMeta() {{
  const meta = authMeta || {{}};
  if (!meta.is_owner) {{
    renderDelegatedOverview(meta);
    syncOwnerChrome();
    updateNotifyButton();
    loadPushStatus({{silent: true}});
    return;
  }}
  ['securityGroup', 'socialGroup', 'notifyGroup', 'backupGroup', 'delegatedUsersGroup'].forEach((id) => {{
    const node = document.getElementById(id);
    if (node) node.hidden = false;
  }});
  if (authMenuHeadLead) authMenuHeadLead.hidden = true;
  ensureCaptchaSettingsFields();
  const totpEnabled = !!meta.totp_enabled;
  const passkeyCount = Number(meta.passkey_count || (Array.isArray(meta.passkeys) ? meta.passkeys.length : 0) || 0);
  const invalidPasskeyCount = Number(meta.passkey_invalid_count || 0);
  const sshCount = Array.isArray(meta.ssh_keys) ? meta.ssh_keys.length : 0;
  const captchaMeta = meta.captcha && typeof meta.captcha === 'object' ? meta.captcha : {{}};
  const captchaMode = String(captchaMeta.mode || '{CAPTCHA_MODE_DIGITS}');
  const captchaConfigured = !!captchaMeta.configured;
  const captchaPillText = captchaMode === '{CAPTCHA_MODE_RECAPTCHA}'
    ? (captchaConfigured ? 'Google reCAPTCHA' : 'reCAPTCHA без ключей')
    : 'Капча: цифры';
  const socialEntries = Object.entries(meta.social || {{}})
    .filter(([, item]) => Number(item && item.linked_count || 0) > 0)
    .map(([provider, item]) => `<span class="authPill">${{escapeHtml((item && item.label) || socialProviderUi[provider]?.label || 'OAuth')}}: ${{Number(item && item.linked_count || 0)}}</span>`);
  authSummary.innerHTML = [
    '<span class="authPill">Пароль</span>',
    `<span class="authPill ${{captchaMode === '{CAPTCHA_MODE_RECAPTCHA}' && !captchaConfigured ? 'off' : ''}}">${{captchaPillText}}</span>`,
    `<span class="authPill ${{totpEnabled ? '' : 'off'}}">${{totpEnabled ? '2FA включена' : '2FA выключена'}}</span>`,
    `<span class="authPill ${{passkeyCount ? '' : 'off'}}">Passkey: ${{passkeyCount}}</span>`,
    `<span class="authPill ${{sshCount ? '' : 'off'}}">ED25519: ${{sshCount}}</span>`,
    ...socialEntries
  ].join('');
  if (authCaptchaModeField) authCaptchaModeField.value = captchaMode === '{CAPTCHA_MODE_RECAPTCHA}' ? '{CAPTCHA_MODE_RECAPTCHA}' : '{CAPTCHA_MODE_DIGITS}';
  if (authCaptchaSiteKeyField && document.activeElement !== authCaptchaSiteKeyField) authCaptchaSiteKeyField.value = captchaMeta.site_key || '';
  if (authCaptchaSecretKeyField && document.activeElement !== authCaptchaSecretKeyField) authCaptchaSecretKeyField.value = captchaMeta.secret_key || '';
  totpState.textContent = totpEnabled ? '2FA включена' : '2FA выключена';
  totpState.className = 'authSectionState' + (totpEnabled ? '' : ' off');
  passkeySummary.textContent = passkeyCount ? `Passkey: ${{passkeyCount}}` : '0 ключей';
  passkeySummary.className = 'authSectionState' + (passkeyCount ? '' : ' off');
  sshKeySummary.textContent = sshCount ? `ED25519: ${{sshCount}}` : '0 ключей';
  sshKeySummary.className = 'authSectionState' + (sshCount ? '' : ' off');
  if (!window.isSecureContext || !window.PublicKeyCredential) {{
    passkeyClientHint.textContent = 'Passkey требует HTTPS и поддержку WebAuthn в браузере.';
    passkeySummary.className = 'authSectionState warn';
  }} else if (!meta.passkeys_supported) {{
    passkeyClientHint.textContent = 'На сервере не установлен модуль WebAuthn. Запусти свежий install-vps.sh.';
    passkeySummary.className = 'authSectionState warn';
  }} else if (!passkeyCount && invalidPasskeyCount) {{
    passkeyClientHint.textContent = 'Есть сохранённый passkey в старом или битом формате. Удали его и зарегистрируй заново.';
    passkeySummary.className = 'authSectionState warn';
  }} else if (!passkeyCount) {{
    passkeyClientHint.textContent = 'Можно зарегистрировать первый passkey прямо здесь.';
  }} else {{
    passkeyClientHint.textContent = 'Passkey готов к использованию на экране входа.';
  }}
  renderPasskeyRows(meta.passkeys || []);
  renderSshKeyRows(meta.ssh_keys || []);
  Object.keys(socialProviderUi).forEach((provider) => renderSocialProvider(provider, meta.social && meta.social[provider]));
  renderDelegatedRouterAccess();
  renderDelegatedUsers(meta.delegated_users || []);
  resetDelegatedUserForm(false);
  updateNotifyButton();
  loadPushStatus({{silent: true}});
}}

async function loadAuthMeta({{silent = false, forceUsername = false}} = {{}}) {{
  try {{
    const res = await fetch('/api/auth/meta', {{cache: 'no-store'}});
    const data = await res.json().catch(() => ({{}}));
    if (!res.ok || !data.ok) throw new Error(data.error || 'Не удалось получить статус авторизации');
    authMeta = data.auth || {{}};
    syncAuthUsernameField(forceUsername);
    if (authToggle && authMeta.username) authToggle.textContent = authMeta.username;
    renderAuthMeta();
    syncOwnerChrome();
    return authMeta;
  }} catch (err) {{
    if (!silent) setAuthMessage(err && err.message ? err.message : 'Не удалось получить статус авторизации', true);
    throw err;
  }}
}}

function openCenteredPopup(url, title) {{
  const width = 720;
  const height = 760;
  const left = Math.max(0, Math.round(window.screenX + (window.outerWidth - width) / 2));
  const top = Math.max(0, Math.round(window.screenY + (window.outerHeight - height) / 2));
  return window.open(url, title, `popup=yes,width=${{width}},height=${{height}},left=${{left}},top=${{top}},resizable=yes,scrollbars=yes`);
}}

async function saveSocialProvider(provider) {{
  const ui = socialProviderUi[provider];
  if (!ui) return;
  setAuthMessage('');
  const currentPassword = String(ui.currentPassword && ui.currentPassword.value || '').trim();
  if (!currentPassword) {{
    setAuthMessage(`Введи текущий пароль Hub в секции ${{ui.label}}, чтобы сохранить OAuth-настройки.`, true);
    return;
  }}
  const res = await fetch(`/api/auth/social/${{encodeURIComponent(provider)}}/save`, {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{
      current_password: currentPassword,
      client_id: ui.clientId ? ui.clientId.value : '',
      client_secret: ui.clientSecret ? ui.clientSecret.value : ''
    }})
  }});
  const data = await res.json().catch(() => ({{}}));
  if (!res.ok || !data.ok) {{
    setAuthMessage(data.error || `Не удалось сохранить настройки ${{ui.label}}`, true);
    return;
  }}
  if (ui.currentPassword) restoreOwnerPasswordInput(ui.currentPassword);
  authMeta = data.auth || authMeta;
  renderAuthMeta();
  setAuthMessage(data.message || `${{ui.label}} OAuth сохранён`);
}}

async function beginSocialLink(provider) {{
  const ui = socialProviderUi[provider];
  if (!ui) return;
  setAuthMessage('');
  const currentPassword = String(ui.currentPassword && ui.currentPassword.value || '').trim();
  if (!currentPassword) {{
    setAuthMessage(`Введи текущий пароль Hub в секции ${{ui.label}}, чтобы запустить привязку.`, true);
    return;
  }}
  const res = await fetch(`/api/auth/social/${{encodeURIComponent(provider)}}/link`, {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{current_password: currentPassword}})
  }});
  const data = await res.json().catch(() => ({{}}));
  if (!res.ok || !data.ok || !data.url) {{
    setAuthMessage(data.error || `Не удалось начать привязку ${{ui.label}}`, true);
    return;
  }}
  socialLinkPopup = openCenteredPopup(data.url, `${{ui.label}} Link`);
  if (!socialLinkPopup) {{
    setAuthMessage(`Браузер заблокировал popup для ${{ui.label}}. Разреши всплывающее окно и повтори.`, true);
    return;
  }}
  socialLinkPopup.focus();
  setAuthMessage(`Продолжаем в окне ${{ui.label}}. После успешной привязки список обновится автоматически.`);
}}

async function unlinkSocialAccount(provider, accountId) {{
  const ui = socialProviderUi[provider];
  if (!ui) return;
  const currentPassword = String(ui.currentPassword && ui.currentPassword.value || '').trim();
  if (!currentPassword) {{
    setAuthMessage(`Введи текущий пароль Hub в секции ${{ui.label}}, чтобы отвязать аккаунт.`, true);
    return;
  }}
  if (!confirm(`Отвязать этот аккаунт ${{ui.label}} от Hub?`)) return;
  const res = await fetch(`/api/auth/social/${{encodeURIComponent(provider)}}/unlink`, {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{id: accountId, current_password: currentPassword}})
  }});
  const data = await res.json().catch(() => ({{}}));
  if (!res.ok || !data.ok) {{
    setAuthMessage(data.error || `Не удалось отвязать аккаунт ${{ui.label}}`, true);
    return;
  }}
  if (ui.currentPassword) restoreOwnerPasswordInput(ui.currentPassword);
  authMeta = data.auth || authMeta;
  renderAuthMeta();
  setAuthMessage(data.message || `Аккаунт ${{ui.label}} отвязан`);
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
          Авторизация: ${{escapeHtml(authMethodText(s.auth_method || 'password'))}}<br>
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

function hasWindowPushManager() {{
  return !!(window.pushManager && typeof window.pushManager.subscribe === 'function');
}}

function embeddedPushVapidConfig() {{
  const boot = window.HUB_PUSH_BOOTSTRAP || {{}};
  const publicKey = String(boot.publicKey || '').trim();
  if (!publicKey) return null;
  return {{
    publicKey,
    subject: String(boot.subject || '').trim(),
  }};
}}

function webPushSupportInfo() {{
  const secure = !!window.isSecureContext;
  const hasServiceWorker = 'serviceWorker' in navigator;
  const hasWindowPush = hasWindowPushManager();
  const hasPushManager = hasWindowPush || 'PushManager' in window;
  const hasNotification = 'Notification' in window;
  let reason = '';
  if (!secure) reason = 'https';
  else if (!hasPushManager) reason = 'pushManager';
  else if (!hasNotification) reason = 'notification';
  else if (!hasWindowPush && !hasServiceWorker) reason = 'serviceWorker';
  if (isIOSDevice() && !isStandalonePwa()) reason = 'ios-home-screen';
  return {{secure, hasServiceWorker, hasWindowPush, hasPushManager, hasNotification, reason}};
}}

function webPushSupported() {{
  const support = webPushSupportInfo();
  return !support.reason;
}}

function notificationGranted() {{
  return 'Notification' in window && Notification.permission === 'granted';
}}

function localNotificationsEnabled() {{
  return localStorage.getItem('owrtNotifyEnabled') === '1';
}}

function webPushEnabled() {{
  return localStorage.getItem('owrtPushEnabled') === '1';
}}

function syncNotificationFlags() {{
  if (!('Notification' in window)) {{
    localStorage.setItem('owrtPushEnabled', '0');
    return;
  }}
  if (Notification.permission !== 'granted') {{
    localStorage.setItem('owrtPushEnabled', '0');
    if (Notification.permission === 'denied') localStorage.setItem('owrtNotifyEnabled', '0');
    return;
  }}
  if (!localNotificationsEnabled()) {{
    localStorage.setItem('owrtNotifyEnabled', '1');
  }}
}}

let hubPushServerReady = null;
let hubPushServerError = '';

function notificationPermissionText() {{
  syncNotificationFlags();
  const support = webPushSupportInfo();
   if (hubPushServerReady === false) return 'Push на VPS не готов';
  if (!webPushSupported()) {{
    if (support.reason === 'https') return 'Push: нужен HTTPS';
    if (isIOSDevice()) return isStandalonePwa() ? 'Push недоступен' : 'iOS: добавь на экран';
    return 'Push недоступен';
  }}
  if (localStorage.getItem('owrtPushEnabled') === '1' && Notification.permission === 'granted') return 'Push включён';
  if (Notification.permission === 'granted') return 'Включить Push';
  if (Notification.permission === 'denied') return 'Запрещено';
  return 'Включить Push';
}}

function updateNotifyButton() {{
  if (!authIsOwner()) {{
    notifyEnable.hidden = true;
    if (notifyTest) notifyTest.hidden = true;
    return;
  }}
  notifyEnable.hidden = false;
  if (notifyTest) notifyTest.hidden = false;
  syncNotificationFlags();
  notifyEnable.textContent = notificationPermissionText();
  if (notifyTest) notifyTest.disabled = hubPushServerReady === false;
  notifyEnable.classList.toggle('on', Notification.permission === 'granted' && (localStorage.getItem('owrtNotifyEnabled') === '1' || localStorage.getItem('owrtPushEnabled') === '1'));
}}

function setNotifyStatus(text, bad = false) {{
  if (!notifyStatus) return;
  notifyStatus.hidden = !text;
  notifyStatus.textContent = text || '';
  notifyStatus.className = 'authHint' + (bad ? ' authDanger' : '');
}}

function pushSupportSummary() {{
  const support = webPushSupportInfo();
  const parts = [
    `ver=${{APP_CLIENT_VERSION}}`,
    `perm=${{'Notification' in window ? Notification.permission : 'none'}}`,
    `secure=${{support.secure ? 'yes' : 'no'}}`,
    `sw=${{support.hasServiceWorker ? 'yes' : 'no'}}`,
    `winpm=${{support.hasWindowPush ? 'yes' : 'no'}}`,
    `ctrl=${{navigator.serviceWorker && navigator.serviceWorker.controller ? 'yes' : 'no'}}`,
    `push=${{support.hasPushManager ? 'yes' : 'no'}}`,
    `notify=${{support.hasNotification ? 'yes' : 'no'}}`
  ];
  if (isIOSDevice()) parts.push(`standalone=${{isStandalonePwa() ? 'yes' : 'no'}}`);
  if (support.reason) parts.push(`reason=${{support.reason}}`);
  return parts.join(', ');
}}

function pushErrorText(error, fallback = 'Не удалось включить Web Push') {{
  if (!error) return fallback;
  const name = String(error.name || '').trim();
  const message = String(error.message || error || '').trim();
  if (name && message && !message.startsWith(name + ':')) return `${{name}}: ${{message}}`;
  return message || name || fallback;
}}

function formatPushResult(result) {{
  const text = String(result || 'unknown');
  if (text === 'ok') return 'ok';
  if (text === 'gone') return 'подписка устарела';
  if (text.startsWith('subscribe_error:')) return 'ошибка сохранения подписки: ' + text.slice('subscribe_error:'.length);
  if (text.startsWith('error:')) {{
    const parts = text.split(':', 3);
    const code = parts[1] || 'unknown';
    const detail = parts[2] || '';
    return detail ? `ошибка ${{code}}: ${{detail}}` : `ошибка ${{code}}`;
  }}
  return text;
}}

function formatPushAttempt(attempt) {{
  if (!attempt || typeof attempt !== 'object') return '';
  const results = Array.isArray(attempt.results) ? attempt.results : [];
  const rendered = results.map((item) => {{
    const client = String(item && item.client || 'браузер');
    const host = String(item && item.endpoint_host || '');
    const result = formatPushResult(item && item.result || 'unknown');
    return host ? `${{client}} @ ${{host}}: ${{result}}` : `${{client}}: ${{result}}`;
  }}).join(' | ');
  if (rendered) return rendered;
  if (attempt.summary === 'no_subscriptions') return 'Подписок на VPS сейчас нет.';
  if (attempt.summary === 'subscribe_error') return 'Hub не смог сохранить push-подписку.';
  return '';
}}

async function pushSubscriptionEndpointId(endpoint) {{
  const value = String(endpoint || '').trim();
  if (!value || !window.crypto || !crypto.subtle || !window.TextEncoder) return '';
  try {{
    const bytes = new TextEncoder().encode(value);
    const digest = await crypto.subtle.digest('SHA-256', bytes);
    const hash = Array.from(new Uint8Array(digest)).map((n) => n.toString(16).padStart(2, '0')).join('');
    return hash.slice(0, 16);
  }} catch (e) {{
    return '';
  }}
}}

async function loadPushStatus({{silent = false}} = {{}}) {{
  if (!authIsOwner()) return;
  try {{
    const res = await fetch('/api/push/status', {{cache: 'no-store'}});
    if (!res.ok) throw new Error('push status ' + res.status);
    const data = await res.json().catch(() => ({{}}));
    hubPushServerReady = data.ready !== false;
    hubPushServerError = String(data.ready_error || '').trim();
    const subject = String(data.subject || '').trim();
    if (!hubPushServerReady) {{
      const subjectText = subject ? ` Текущий VAPID subject: ${{subject}}.` : '';
      setNotifyStatus((hubPushServerError || 'На VPS не готов Web Push backend.') + subjectText, true);
      updateNotifyButton();
      return;
    }}
    const count = Number(data.subscription_count || 0);
    const subscriptions = Array.isArray(data.subscriptions) ? data.subscriptions : [];
    const localSubscriptionId = String(localStorage.getItem('owrtPushSubscriptionId') || '').trim();
    const localClientHint = currentClientHint();
    let current = localSubscriptionId ? subscriptions.find((item) => String(item && item.id || '') === localSubscriptionId) : null;
    if (!current && localClientHint) {{
      const hinted = subscriptions.filter((item) => String(item && item.client_hint || '') === localClientHint);
      if (hinted.length === 1) current = hinted[0];
    }}
    if (current && current.id && current.id !== localSubscriptionId) {{
      localStorage.setItem('owrtPushSubscriptionId', String(current.id));
    }}
    const currentActive = !!current;
    if (Notification.permission === 'granted') {{
      localStorage.setItem('owrtPushEnabled', currentActive ? '1' : '0');
    }}
    const clientText = current && current.client
      ? ` Это устройство подписано как ${{current.client}}.`
      : (count ? ' На VPS есть подписки, но не от этого устройства.' : '');
    const attemptText = formatPushAttempt(data.last_attempt);
    const prefix = count ? `Подписок на VPS: ${{count}}.` : 'Подписок на VPS сейчас нет.';
    setNotifyStatus(attemptText ? `${{prefix}} Последняя отправка: ${{attemptText}}.${{clientText}}` : prefix + clientText, !currentActive || (attemptText && !attemptText.includes(': ok')));
    updateNotifyButton();
  }} catch (e) {{
    hubPushServerReady = null;
    hubPushServerError = '';
    if (!silent) setNotifyStatus('Не смог прочитать статус Web Push на VPS.', true);
  }}
}}

function currentClientHint() {{
  return isStandalonePwa() ? 'hub' : '';
}}

async function reportClientHint() {{
  const hint = currentClientHint();
  if (!hint) return;
  try {{
    if (sessionStorage.getItem('owrtClientHintSent') === hint) return;
    const res = await fetch('/api/session/client-hint', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{client_hint: hint}})
    }});
    const data = await res.json().catch(() => ({{}}));
    if (res.ok && data.ok) sessionStorage.setItem('owrtClientHintSent', hint);
  }} catch (e) {{}}
}}

function urlBase64ToUint8Array(value) {{
  const padding = '='.repeat((4 - value.length % 4) % 4);
  const base64 = (value + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  const output = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) output[i] = raw.charCodeAt(i);
  return output;
}}

const HUB_SW_URL = '/sw.js';
let pushServiceWorkerReady = null;
let pushServiceWorkerPromise = null;
let pushVapidConfig = embeddedPushVapidConfig();
let pushVapidPromise = null;

function pushEnvironmentReady() {{
  return !!(((pushServiceWorkerReady && pushServiceWorkerReady.pushManager) || hasWindowPushManager()) && pushVapidConfig && pushVapidConfig.publicKey);
}}

function ensureClientVersion() {{
  const key = 'owrtClientVersion';
  const current = String(localStorage.getItem(key) || '');
  const url = new URL(window.location.href);
  if (current !== APP_CLIENT_VERSION) {{
    localStorage.setItem(key, APP_CLIENT_VERSION);
  }}
  if (url.searchParams.has('appv')) {{
    url.searchParams.delete('appv');
    const cleanUrl = url.pathname + url.search + url.hash;
    window.history.replaceState(null, '', cleanUrl);
  }}
  return false;
}}

function pageControlledByServiceWorker() {{
  return !!(navigator.serviceWorker && navigator.serviceWorker.controller);
}}

function isPushInitStateError(error) {{
  const text = String((error && (error.message || error.name)) || error || '').toLowerCase();
  return text.includes('invalidstateerror') || text.includes('push service initialization failed');
}}

function pushWorkerState(worker) {{
  return worker && worker.state ? String(worker.state) : '';
}}

function pushRegistrationUsable(registration) {{
  return !!(registration && registration.pushManager && registration.active && pushWorkerState(registration.active) === 'activated');
}}

async function waitForPushRegistrationActivation(registration, {{timeoutMs = 5000}} = {{}}) {{
  let reg = registration || null;
  const deadline = Date.now() + Math.max(800, Number(timeoutMs) || 5000);
  while (reg && Date.now() < deadline) {{
    if (pushRegistrationUsable(reg)) return reg;
    const candidate = reg.installing || reg.waiting || reg.active;
    if (reg.waiting && typeof reg.waiting.postMessage === 'function') {{
      try {{ reg.waiting.postMessage({{type: 'SKIP_WAITING'}}); }} catch (e) {{}}
    }}
    await new Promise((resolve) => {{
      let done = false;
      const finish = () => {{
        if (done) return;
        done = true;
        resolve();
      }};
      if (candidate && typeof candidate.addEventListener === 'function') {{
        try {{ candidate.addEventListener('statechange', finish, {{once: true}}); }} catch (e) {{}}
      }}
      setTimeout(finish, 320);
    }});
    try {{
      const fresh = await navigator.serviceWorker.getRegistration();
      if (fresh) reg = fresh;
    }} catch (e) {{}}
  }}
  return reg;
}}

async function resetHubPushState({{unregister = false}} = {{}}) {{
  pushServiceWorkerReady = null;
  pushServiceWorkerPromise = null;
  pushVapidConfig = null;
  pushVapidPromise = null;
  try {{
    localStorage.removeItem('owrtPushSubscriptionId');
    localStorage.removeItem('owrtPushEnabled');
  }} catch (e) {{}}
  if (!unregister || !('serviceWorker' in navigator)) return;
  try {{
    const registrations = await navigator.serviceWorker.getRegistrations();
    await Promise.all(registrations.map(async (registration) => {{
      const scope = String((registration && registration.scope) || '');
      if (!scope.startsWith(window.location.origin + '/')) return;
      try {{ await registration.unregister(); }} catch (e) {{}}
    }}));
  }} catch (e) {{}}
}}

async function ensureServiceWorkerControllingPage({{timeoutMs = 2500}} = {{}}) {{
  if (!('serviceWorker' in navigator)) return false;
  if (pageControlledByServiceWorker()) return true;
  await new Promise((resolve) => {{
    let done = false;
    const finish = () => {{
      if (done) return;
      done = true;
      resolve();
    }};
    navigator.serviceWorker.addEventListener('controllerchange', finish, {{once: true}});
    setTimeout(finish, timeoutMs);
  }});
  return pageControlledByServiceWorker();
}}

async function ensureHubServiceWorker({{forceFresh = false, forceUpdate = false}} = {{}}) {{
  let reg = null;
  if (!forceFresh) {{
    try {{
      reg = await navigator.serviceWorker.getRegistration();
    }} catch (e) {{}}
  }}
  const activeScriptUrl = reg && reg.active && reg.active.scriptURL ? String(reg.active.scriptURL) : '';
  const activeScriptPath = activeScriptUrl ? new URL(activeScriptUrl, window.location.href).pathname : '';
  if (!reg || !reg.pushManager || (activeScriptPath && activeScriptPath !== '/sw.js')) {{
    try {{
      reg = await navigator.serviceWorker.register(HUB_SW_URL, {{scope: '/'}});
    }} catch (e) {{
      throw new Error(`serviceWorker.register failed: ${{pushErrorText(e, 'unknown error')}}`);
    }}
  }}
  if (forceUpdate && reg && typeof reg.update === 'function') {{
    try {{ await reg.update(); }} catch (e) {{}}
  }}
  reg = await waitForPushRegistrationActivation(reg, {{timeoutMs: isIOSDevice() ? 6500 : 3500}});
  if (reg && reg.waiting && typeof reg.waiting.postMessage === 'function') {{
    try {{ reg.waiting.postMessage({{type: 'SKIP_WAITING'}}); }} catch (e) {{}}
  }}
  return reg || navigator.serviceWorker.ready;
}}

async function ensurePushServiceWorkerReady({{forceFresh = false, forceUpdate = false}} = {{}}) {{
  if (!forceFresh && pushRegistrationUsable(pushServiceWorkerReady)) return pushServiceWorkerReady;
  if (!pushServiceWorkerPromise || forceFresh || forceUpdate) {{
    pushServiceWorkerPromise = (async () => {{
      const reg = await ensureHubServiceWorker({{forceFresh, forceUpdate}});
      let ready = reg && reg.pushManager ? reg : await navigator.serviceWorker.ready;
      ready = await waitForPushRegistrationActivation(ready, {{timeoutMs: isIOSDevice() ? 6500 : 3500}});
      const controlled = await ensureServiceWorkerControllingPage({{timeoutMs: isIOSDevice() ? 4000 : 1500}});
      if (!controlled && isIOSDevice() && isStandalonePwa()) {{
        throw new Error('Hub на iPhone ещё не перешёл под контроль service worker. Полностью закрой приложение Hub свайпом и открой снова, потом повтори Push.');
      }}
      try {{
        const fresh = await navigator.serviceWorker.getRegistration();
        if (fresh) ready = await waitForPushRegistrationActivation(fresh, {{timeoutMs: isIOSDevice() ? 6500 : 3500}});
      }} catch (e) {{}}
      if (!ready || !ready.pushManager) {{
        throw new Error('Service worker для Push не подготовился. Полностью перезапусти Hub и повтори Push.');
      }}
      pushServiceWorkerReady = ready;
      return ready;
    }})().catch((e) => {{
      pushServiceWorkerPromise = null;
      throw e;
    }});
  }}
  return pushServiceWorkerPromise;
}}

async function fetchPushVapidConfig() {{
  const embedded = embeddedPushVapidConfig();
  if (embedded && embedded.publicKey) {{
    pushVapidConfig = embedded;
    return embedded;
  }}
  if (pushVapidConfig && pushVapidConfig.publicKey) return pushVapidConfig;
  if (!pushVapidPromise) {{
    pushVapidPromise = (async () => {{
      let keyRes;
      try {{
        keyRes = await fetch('/api/push/vapid-public-key', {{cache: 'no-store'}});
      }} catch (e) {{
        throw new Error(`Не смог получить VAPID ключ с VPS: ${{pushErrorText(e, 'network error')}}`);
      }}
      const keyData = await keyRes.json().catch(() => ({{}}));
      if (!keyRes.ok || !keyData.ok || !keyData.publicKey) {{
        throw new Error(keyData.error || `VAPID endpoint вернул HTTP ${{keyRes.status}}`);
      }}
      pushVapidConfig = {{
        publicKey: String(keyData.publicKey || ''),
        subject: String(keyData.subject || ''),
      }};
      return pushVapidConfig;
    }})().catch((e) => {{
      pushVapidPromise = null;
      throw e;
    }});
  }}
  return pushVapidPromise;
}}

async function warmPushEnvironment({{silent = true}} = {{}}) {{
  if (!webPushSupported()) return null;
  try {{
    const tasks = [fetchPushVapidConfig()];
    if ('serviceWorker' in navigator) {{
      tasks.unshift(ensurePushServiceWorkerReady({{forceFresh: isIOSDevice() && !hasWindowPushManager(), forceUpdate: isIOSDevice() && !hasWindowPushManager()}}).catch((e) => {{
        if (hasWindowPushManager()) return null;
        throw e;
      }}));
    }}
    return await Promise.all(tasks);
  }} catch (e) {{
    if (!silent) throw e;
    return null;
  }}
}}

async function ensurePushWarmupForAction(actionLabel) {{
  if (hasWindowPushManager()) return;
  if (!isIOSDevice() || pushEnvironmentReady()) return;
  showRouterMsg(`Подготавливаю Web Push на iPhone для "${{actionLabel}}"...`);
  setNotifyStatus(`Подготавливаю Web Push на iPhone. Текущее состояние: ${{pushSupportSummary()}}`);
  await warmPushEnvironment({{silent: false}});
  updateNotifyButton();
  setNotifyStatus(`Среда Web Push готова. Продолжаю "${{actionLabel}}".`);
}}

async function resolvePushSubscriptionManager({{forceFresh = false, forceUpdate = false}} = {{}}) {{
  const keyData = await fetchPushVapidConfig();
  if (hasWindowPushManager()) {{
    return {{
      manager: window.pushManager,
      registration: null,
      keyData,
      source: 'window',
    }};
  }}
  const ready = await ensurePushServiceWorkerReady({{forceFresh, forceUpdate}});
  return {{
    manager: ready && ready.pushManager ? ready.pushManager : null,
    registration: ready,
    keyData,
    source: 'serviceWorker',
  }};
}}

async function registerPushSubscription({{preferCached = false, directSubscribeFirst = false}} = {{}}) {{
  let ready = pushServiceWorkerReady;
  let keyData = pushVapidConfig;
  let pushManager = ready && ready.pushManager ? ready.pushManager : null;
  let pushSource = 'serviceWorker';
  const preferWindowManager = hasWindowPushManager();
  const requireFreshRegistration = preferWindowManager || isIOSDevice() || !preferCached || !pushEnvironmentReady();
  if (requireFreshRegistration) {{
    const resolved = await resolvePushSubscriptionManager({{
      forceFresh: !preferWindowManager && isIOSDevice(),
      forceUpdate: !preferWindowManager && isIOSDevice()
    }});
    ready = resolved.registration;
    keyData = resolved.keyData;
    pushManager = resolved.manager;
    pushSource = resolved.source;
  }}
  if (!pushManager) {{
    throw new Error('PushManager не подготовился для этого устройства.');
  }}
  let subscription = null;
  const subscribeOptions = {{
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(keyData.publicKey)
  }};
  const trySubscribe = async () => {{
    return await pushManager.subscribe(subscribeOptions);
  }};
  if (directSubscribeFirst && isIOSDevice()) {{
    try {{
      subscription = await trySubscribe();
    }} catch (e) {{
      if (!(pushSource === 'serviceWorker' && isPushInitStateError(e))) {{
        if (String((e && e.name) || '').toLowerCase() === 'aborterror') {{
          throw new Error('iPhone прервал создание push-подписки. Полностью закрой Hub свайпом, открой снова с экрана Домой и повтори "Включить Push".');
        }}
      }}
      if (pushSource !== 'serviceWorker' || !isPushInitStateError(e)) {{
        throw new Error(`pushManager.subscribe failed: ${{pushErrorText(e, 'unknown error')}}`);
      }}
    }}
  }}
  try {{
    if (!subscription) subscription = await pushManager.getSubscription();
  }} catch (e) {{
    if (!isPushInitStateError(e)) {{
      throw new Error(`pushManager.getSubscription failed: ${{pushErrorText(e, 'unknown error')}}`);
    }}
    if (pushSource !== 'serviceWorker') {{
      throw new Error(`pushManager.getSubscription failed: ${{pushErrorText(e, 'unknown error')}}`);
    }}
    await resetHubPushState({{unregister: false}});
    const resolved = await resolvePushSubscriptionManager({{forceFresh: true, forceUpdate: true}});
    ready = resolved.registration;
    keyData = resolved.keyData;
    pushManager = resolved.manager;
    pushSource = resolved.source;
    try {{
      subscription = await trySubscribe();
    }} catch (retrySubscribeError) {{
      const retryName = String((retrySubscribeError && retrySubscribeError.name) || '').toLowerCase();
      if (retryName === 'aborterror' && isIOSDevice() && isStandalonePwa()) {{
        throw new Error('iPhone прервал создание push-подписки после обновления service worker. Полностью закрой Hub свайпом, открой снова с экрана Домой и повтори "Включить Push".');
      }}
      if (!isPushInitStateError(retrySubscribeError)) {{
        throw new Error(`pushManager.subscribe failed after refresh: ${{pushErrorText(retrySubscribeError, 'unknown error')}}`);
      }}
    }}
    try {{
      if (!subscription) subscription = await pushManager.getSubscription();
    }} catch (retryError) {{
      if (isPushInitStateError(retryError) && isIOSDevice() && isStandalonePwa()) {{
        await resetHubPushState({{unregister: true}});
        throw new Error('iPhone держит битую push-регистрацию Safari. Hub уже сбросил service worker для этого сайта. Полностью закрой приложение Hub свайпом, открой его снова с экрана Домой и ещё раз нажми "Включить Push".');
      }}
      throw new Error(`pushManager.getSubscription failed after refresh: ${{pushErrorText(retryError, 'unknown error')}}`);
    }}
  }}
  if (!subscription) {{
    try {{
      const subscribePromise = trySubscribe();
      subscription = await subscribePromise;
    }} catch (e) {{
      if (isPushInitStateError(e) && isIOSDevice() && isStandalonePwa()) {{
        if (pushSource === 'serviceWorker') {{
          await resetHubPushState({{unregister: true}});
          throw new Error('iPhone завис в push-сервисе Safari. Hub уже очистил service worker этого сайта. Полностью закрой приложение Hub свайпом, открой снова с экрана Домой и повтори "Включить Push". Если снова не пойдёт, тогда уже удаляй Hub с экрана Домой и добавляй заново.');
        }}
      }}
      throw new Error(`pushManager.subscribe failed: ${{pushErrorText(e, 'unknown error')}}`);
    }}
  }}
  const subscriptionId = await pushSubscriptionEndpointId(subscription && subscription.endpoint ? subscription.endpoint : '');
  if (subscriptionId) localStorage.setItem('owrtPushSubscriptionId', subscriptionId);
  let res;
  try {{
    res = await fetch('/api/push/subscribe', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{...(subscription.toJSON ? subscription.toJSON() : subscription), client_hint: currentClientHint()}})
    }});
  }} catch (e) {{
    throw new Error(`Не смог отправить подписку на VPS: ${{pushErrorText(e, 'network error')}}`);
  }}
  const data = await res.json().catch(() => ({{}}));
  if (!res.ok || !data.ok) throw new Error(data.error || `Не смог сохранить push-подписку (HTTP ${{res.status}})`);
  return data;
}}

async function enableNotifications() {{
  if (!authIsOwner()) return;
  if (!webPushSupported()) {{
    localStorage.setItem('owrtNotifyEnabled', notificationGranted() ? '1' : '0');
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
    setNotifyStatus(`Push недоступен: ${{message}} Текущее состояние: ${{pushSupportSummary()}}`, true);
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
    setNotifyStatus(`Разрешение на уведомления не выдано. Текущее состояние: ${{pushSupportSummary()}}`, true);
    return;
  }}
  localStorage.setItem('owrtNotifyEnabled', '1');
  try {{
    if (!(isIOSDevice() && hasWindowPushManager())) {{
      await ensurePushWarmupForAction('Включить Push');
    }}
    showRouterMsg('Включаю настоящий Web Push для этого устройства...');
    setNotifyStatus(`Пробую включить Web Push. Текущее состояние: ${{pushSupportSummary()}}`);
    await registerPushSubscription({{preferCached: isIOSDevice(), directSubscribeFirst: isIOSDevice()}});
    localStorage.setItem('owrtPushEnabled', '1');
    showRouterMsg('Push включён. Теперь уведомления должны приходить даже когда вкладка закрыта.');
    setNotifyStatus(`Web Push включён. Текущее состояние: ${{pushSupportSummary()}}`);
  }} catch (e) {{
    localStorage.setItem('owrtPushEnabled', '0');
    const text = pushErrorText(e, 'Не удалось включить Web Push');
    showRouterMsg(text, true);
    setNotifyStatus(`Не удалось включить Web Push: ${{text}}. Текущее состояние: ${{pushSupportSummary()}}`, true);
  }}
  updateNotifyButton();
  await loadPushStatus();
}}

function showBrowserNotification(item) {{
  syncNotificationFlags();
  if (!item || !('Notification' in window) || Notification.permission !== 'granted') return;
  if (localStorage.getItem('owrtNotifyEnabled') !== '1') return;
  if (localStorage.getItem('owrtPushEnabled') === '1' && document.visibilityState !== 'visible') return;
  try {{
    new Notification(item.title || 'OpenWrt Remote Hub', {{
      body: item.body || '',
      tag: 'owrt-' + (item.id || item.kind || item.ts || Date.now()),
      renotify: false
    }});
  }} catch (e) {{}}
}}

async function testPushDelivery() {{
  if (!authIsOwner()) return;
  if (!webPushSupported()) {{
    showRouterMsg('Сначала открой Hub по HTTPS и включи Web Push на этом устройстве.', true);
    setNotifyStatus(`Тест Push невозможен. Текущее состояние: ${{pushSupportSummary()}}`, true);
    await loadPushStatus();
    return;
  }}
  try {{
    if (!(isIOSDevice() && hasWindowPushManager())) {{
      await ensurePushWarmupForAction('Проверить Push');
    }}
    showRouterMsg('Проверяю доставку Web Push на это устройство...');
    setNotifyStatus(`Проверяю доставку Web Push. Текущее состояние: ${{pushSupportSummary()}}`);
    const data = await registerPushSubscription({{preferCached: isIOSDevice(), directSubscribeFirst: isIOSDevice()}});
    const client = data && data.subscription && data.subscription.client ? data.subscription.client : 'устройство';
    showRouterMsg(`VPS принял подписку и отправил тестовый Web Push на ${{client}}.`);
    setNotifyStatus(`VPS принял подписку и отправил тестовый Web Push на ${{client}}.`);
  }} catch (e) {{
    const text = pushErrorText(e, 'Тестовый Web Push не удался');
    showRouterMsg(text, true);
    setNotifyStatus(`Тестовый Web Push не удался: ${{text}}. Текущее состояние: ${{pushSupportSummary()}}`, true);
  }}
  await loadPushStatus();
}}

function initialNotificationTs() {{
  const stored = Number(localStorage.getItem('owrtLastNotificationTs') || 0);
  const initial = (window.HUB_NOTIFICATIONS || []).reduce((max, n) => Math.max(max, Number(n.ts || 0)), 0);
  const result = Math.max(stored, initial);
  localStorage.setItem('owrtLastNotificationTs', String(result));
  return result;
}}

function initialNotificationSerial() {{
  const stored = Number(localStorage.getItem('owrtLastNotificationSerial') || 0);
  const initial = (window.HUB_NOTIFICATIONS || []).reduce((max, n) => Math.max(max, Number(n.serial || 0)), 0);
  const result = Math.max(stored, initial);
  localStorage.setItem('owrtLastNotificationSerial', String(result));
  return result;
}}

let lastNotificationTs = initialNotificationTs();
let lastNotificationSerial = initialNotificationSerial();
let notificationWaitAbort = false;
let notificationWaitRunning = false;

function storeNotificationCursor() {{
  localStorage.setItem('owrtLastNotificationTs', String(lastNotificationTs));
  localStorage.setItem('owrtLastNotificationSerial', String(lastNotificationSerial));
}}

function applyNotificationItems(items, {{initial = false}} = {{}}) {{
  const list = Array.isArray(items) ? items : [];
  const known = new Set((window.HUB_NOTIFICATIONS || []).map(n => n.id));
  const fresh = initial ? [] : list.filter(n => !known.has(n.id)).sort((a, b) => {{
    const serialDiff = Number(a.serial || 0) - Number(b.serial || 0);
    if (serialDiff) return serialDiff;
    return Number(a.ts || 0) - Number(b.ts || 0);
  }});
  window.HUB_NOTIFICATIONS = [...list, ...(window.HUB_NOTIFICATIONS || [])]
    .filter((item, idx, arr) => arr.findIndex(other => other.id === item.id) === idx)
    .sort((a, b) => {{
      const tsDiff = Number(b.ts || 0) - Number(a.ts || 0);
      if (tsDiff) return tsDiff;
      return Number(b.serial || 0) - Number(a.serial || 0);
    }})
    .slice(0, 60);
  for (const item of window.HUB_NOTIFICATIONS) {{
    lastNotificationTs = Math.max(lastNotificationTs, Number(item.ts || 0));
    lastNotificationSerial = Math.max(lastNotificationSerial, Number(item.serial || 0));
  }}
  storeNotificationCursor();
  renderNotifications(window.HUB_NOTIFICATIONS);
  for (const item of fresh) {{
    showBrowserNotification(item);
  }}
}}

async function loadNotifications({{initial = false}} = {{}}) {{
  if (!authIsOwner()) return;
  const params = new URLSearchParams();
  if (!initial && lastNotificationSerial > 0) params.set('after_serial', String(lastNotificationSerial));
  else if (!initial && lastNotificationTs > 0) params.set('after', String(lastNotificationTs));
  params.set('limit', initial ? '40' : '60');
  const res = await fetch('/api/notifications?' + params.toString(), {{cache: 'no-store'}});
  if (!res.ok) return;
  const data = await res.json().catch(() => ({{}}));
  const items = Array.isArray(data.notifications) ? data.notifications : [];
  if (!items.length && !initial) {{
    lastNotificationSerial = Math.max(lastNotificationSerial, Number(data.serial || 0));
    storeNotificationCursor();
    return;
  }}
  applyNotificationItems(items, {{initial}});
}}

async function waitNotificationsLoop() {{
  if (!authIsOwner()) return;
  if (notificationWaitRunning || notificationWaitAbort) return;
  notificationWaitRunning = true;
  try {{
    while (!notificationWaitAbort) {{
      const params = new URLSearchParams({{
        after_serial: String(lastNotificationSerial || 0),
        timeout: '25',
        limit: '60'
      }});
      let res;
      try {{
        res = await fetch('/api/notifications/wait?' + params.toString(), {{cache: 'no-store'}});
      }} catch (e) {{
        await new Promise(resolve => setTimeout(resolve, 2000));
        continue;
      }}
      if (!res.ok) {{
        await new Promise(resolve => setTimeout(resolve, 2000));
        continue;
      }}
      const data = await res.json().catch(() => ({{}}));
      const items = Array.isArray(data.notifications) ? data.notifications : [];
      if (items.length) {{
        applyNotificationItems(items);
        continue;
      }}
      lastNotificationSerial = Math.max(lastNotificationSerial, Number(data.serial || 0));
      storeNotificationCursor();
    }}
  }} finally {{
    notificationWaitRunning = false;
  }}
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

backupRewrite.addEventListener('click', async () => {{
  const vpsHost = backupVpsHost.value.trim();
  const publicUrl = backupPublicUrl.value.trim();
  const sshPassword = backupSshPassword ? backupSshPassword.value : '';
  if (!vpsHost && !publicUrl) {{
    setBackupMsg('Укажи новый VPS host/IP или Public URL для перепривязки роутеров.', true);
    return;
  }}
  if (!confirm('Переписать endpoint-адреса у всех активных роутеров в Hub и сразу попытаться применить новый конфиг по SSH? Для роутеров с живым SSH-туннелем Hub попробует вернуть heartbeat сам.')) return;
  backupRewrite.disabled = true;
  setBackupMsg('Переписываю endpoint-адреса роутеров в Hub и пытаюсь сразу применить новый конфиг по SSH...');
  try {{
    const res = await fetch('/api/routers/rewrite-endpoints', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{
        vps_host: vpsHost,
        public_url: publicUrl,
        preserve_vps_fallback: true,
        apply_live: true,
        ssh_password: sshPassword
      }})
    }});
    const data = await res.json().catch(() => ({{}}));
    if (!res.ok || !data.ok) throw new Error(data.error || res.status);
    const preservedFallback = vpsHost && publicUrl && !Object.prototype.hasOwnProperty.call(data.rewritten || {{}}, 'vps_host')
      ? ' Текущий VPS host/IP у роутеров сохранен как fallback, чтобы переезд с IP на домен на этом же VPS не обрывал heartbeat.'
      : '';
    const changed = Object.keys(data.rewritten || {{}}).length
      ? ' Переписано: ' + Object.entries(data.rewritten).map(([k, v]) => `${{k}}=${{v}}`).join(', ') + '.'
      : '';
    const autoApply = data.auto_apply || {{}};
    const total = Number(data.routers || 0);
    const confirmed = Number(autoApply.confirmed || 0);
    const applied = Number(autoApply.applied || 0);
    const unreachable = Number(autoApply.unreachable || 0);
    const failed = Number(autoApply.failed || 0);
    let extra = ` Автоприменение по SSH: подтвержден heartbeat у ${{confirmed}}/${{total}}, конфиг отправлен на ${{applied}}/${{total}}.`;
    if (unreachable) extra += ` Недоступны по SSH: ${{unreachable}}.`;
    if (failed) extra += ` С ошибкой применения: ${{failed}}.`;
    if (confirmed < total) extra += ' Для тех, кто не подтвердился, открой OpenWrt config в карточке и перепримени его вручную.';
    setBackupMsg(`Роутеры перепривязаны в Hub.${{changed}} Активных роутеров: ${{total}}.${{extra}}`);
    if (preservedFallback) setBackupMsg(backupMsg.textContent + preservedFallback);
    await loadRouters();
  }} catch (e) {{
    setBackupMsg('Перепривязка роутеров не удалась: ' + (e.message || e), true);
  }} finally {{
    backupRewrite.disabled = false;
  }}
}});

notifyEnable.addEventListener('click', enableNotifications);
if (notifyTest) notifyTest.addEventListener('click', testPushDelivery);
notifyClear.addEventListener('click', async () => {{
  if (!confirm('Очистить все уведомления?')) return;
  const res = await fetch('/api/notifications/clear', {{method: 'POST'}});
  if (!res.ok) {{
    showRouterMsg(await res.text() || 'Не удалось очистить уведомления', true);
    return;
  }}
  window.HUB_NOTIFICATIONS = [];
  lastNotificationTs = Math.floor(Date.now() / 1000);
  lastNotificationSerial = 0;
  storeNotificationCursor();
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

Object.entries(socialProviderUi).forEach(([provider, ui]) => {{
  if (ui.saveBtn) ui.saveBtn.addEventListener('click', () => saveSocialProvider(provider));
  if (ui.linkBtn) ui.linkBtn.addEventListener('click', () => beginSocialLink(provider));
}});

window.addEventListener('message', async (ev) => {{
  if (ev.origin !== window.location.origin) return;
  const data = ev.data || {{}};
  if (data.type !== 'owrt-social-linked') return;
  if (socialLinkPopup && !socialLinkPopup.closed) {{
    try {{
      socialLinkPopup.close();
    }} catch (err) {{}}
  }}
  socialLinkPopup = null;
  if (!data.ok) {{
    setAuthMessage('Привязка сервиса завершилась с ошибкой.', true);
    return;
  }}
  await loadAuthMeta({{silent: true}}).catch(() => {{}});
  const label = socialProviderUi[data.provider]?.label || 'OAuth';
  setAuthMessage(`${{label}} успешно привязан.`);
}});

authMenu.addEventListener('click', async (ev) => {{
  const delegatedEditId = ev.target?.dataset?.delegatedEdit;
  if (delegatedEditId) {{
    const user = (authMeta && Array.isArray(authMeta.delegated_users) ? authMeta.delegated_users : []).find((item) => String(item.id || '') === String(delegatedEditId));
    if (user) fillDelegatedUserForm(user);
    return;
  }}
  const delegatedRevokeId = ev.target?.dataset?.delegatedRevoke;
  if (delegatedRevokeId) {{
    const fields = delegatedUserFormData();
    const currentPassword = String(fields && fields.currentPassword ? fields.currentPassword.value || '' : '').trim();
    if (!currentPassword) {{
      setDelegatedUserMessage('Введи текущий пароль владельца, чтобы завершить сессии пользователя.', true);
      return;
    }}
    if (!confirm('Завершить все активные сессии этого пользователя?')) return;
    const res = await fetch('/api/auth/users/revoke-sessions', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{id: delegatedRevokeId, current_password: currentPassword}})
    }});
    const data = await res.json().catch(() => ({{}}));
    if (!res.ok || !data.ok) {{
      setDelegatedUserMessage(data.error || 'Не удалось завершить сессии пользователя.', true);
      return;
    }}
    authMeta = data.auth || authMeta;
    renderAuthMeta();
    setDelegatedUserMessage(data.message || 'Сессии пользователя завершены');
    return;
  }}
  const delegatedDeleteId = ev.target?.dataset?.delegatedDelete;
  if (delegatedDeleteId) {{
    const fields = delegatedUserFormData();
    const currentPassword = String(fields && fields.currentPassword ? fields.currentPassword.value || '' : '').trim();
    if (!currentPassword) {{
      setDelegatedUserMessage('Введи текущий пароль владельца, чтобы удалить пользователя.', true);
      return;
    }}
    if (!confirm('Удалить этого ограниченного пользователя?')) return;
    const res = await fetch('/api/auth/users/delete', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{id: delegatedDeleteId, current_password: currentPassword}})
    }});
    const data = await res.json().catch(() => ({{}}));
    if (!res.ok || !data.ok) {{
      setDelegatedUserMessage(data.error || 'Не удалось удалить пользователя.', true);
      return;
    }}
    authMeta = data.auth || authMeta;
    resetDelegatedUserForm(false);
    renderAuthMeta();
    setDelegatedUserMessage(data.message || 'Пользователь удалён');
    return;
  }}
  const passkeyId = ev.target?.dataset?.passkeyRemove;
  if (passkeyId) {{
    const currentPassword = passkeyCurrentPassword.value;
    if (!currentPassword) {{
      setAuthMessage('Введи текущий пароль в секции Passkey, чтобы удалить ключ.', true);
      return;
    }}
    if (!confirm('Удалить этот passkey?')) return;
    const res = await fetch('/api/auth/passkeys/remove', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{id: passkeyId, current_password: currentPassword}})
    }});
    const data = await res.json().catch(() => ({{}}));
    if (!res.ok || !data.ok) {{
      setAuthMessage(data.error || 'Не удалось удалить passkey', true);
      return;
    }}
    restoreOwnerPasswordInput(passkeyCurrentPassword);
    authMeta = data.auth || authMeta;
    renderAuthMeta();
    setAuthMessage(data.message || 'Passkey удален');
    return;
  }}
  const sshKeyId = ev.target?.dataset?.sshKeyRemove;
  if (sshKeyId) {{
    const currentPassword = sshKeyCurrentPassword.value;
    if (!currentPassword) {{
      setAuthMessage('Введи текущий пароль в секции ED25519, чтобы удалить ключ.', true);
      return;
    }}
    if (!confirm('Удалить этот SSH ED25519 ключ?')) return;
    const res = await fetch('/api/auth/ssh-keys/remove', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{id: sshKeyId, current_password: currentPassword}})
    }});
    const data = await res.json().catch(() => ({{}}));
    if (!res.ok || !data.ok) {{
      setAuthMessage(data.error || 'Не удалось удалить SSH ключ', true);
      return;
    }}
    restoreOwnerPasswordInput(sshKeyCurrentPassword);
    authMeta = data.auth || authMeta;
    renderAuthMeta();
    setAuthMessage(data.message || 'SSH ключ удален');
    return;
  }}
  const socialAccountId = ev.target?.dataset?.socialUnlink;
  const socialProvider = ev.target?.dataset?.socialProvider;
  if (socialAccountId && socialProvider) {{
    await unlinkSocialAccount(socialProvider, socialAccountId);
  }}
}});

authForm.addEventListener('submit', async (ev) => {{
  ev.preventDefault();
  ev.stopImmediatePropagation();
  setAuthMessage('');
  const body = new URLSearchParams(new FormData(ev.currentTarget));
  const res = await fetch('/api/auth', {{method: 'POST', body}});
  const text = await res.text();
  if (res.ok) {{
    const nextCurrentPassword = ev.currentTarget.password.value ? String(ev.currentTarget.password.value || '') : String(ev.currentTarget.current_password.value || '');
    if (ownerPasswordRememberEnabled()) setOwnerPasswordRememberEnabled(true, nextCurrentPassword);
    setAuthMessage(text || 'Доступ обновлен');
    restoreOwnerPasswordInput(ev.currentTarget.current_password, nextCurrentPassword);
    ev.currentTarget.password.value = '';
    ev.currentTarget.password_confirm.value = '';
    authUsernameDraftDirty = false;
    await loadAuthMeta({{silent: true, forceUsername: true}}).catch(() => {{}});
  }} else {{
    setAuthMessage(text || 'Не удалось сохранить', true);
  }}
}}, true);

if (delegatedUserReset) {{
  delegatedUserReset.addEventListener('click', () => resetDelegatedUserForm());
}}

if (delegatedUsersGroup) {{
  delegatedUsersGroup.addEventListener('toggle', () => {{
    if (delegatedUsersGroup.open) resetDelegatedUserForm(false);
  }});
}}

if (delegatedUserForm) {{
  delegatedUserForm.addEventListener('submit', async (ev) => {{
    ev.preventDefault();
    if (!authIsOwner()) return;
    const fields = delegatedUserFormData();
    if (!fields) return;
    setDelegatedUserMessage('');
    const payload = {{
      id: String(fields.id.value || '').trim(),
      username: String(fields.username.value || '').trim(),
      enabled: !!fields.enabled.checked,
      password: String(fields.password.value || ''),
      password_confirm: String(fields.passwordConfirm.value || ''),
      current_password: String(fields.currentPassword.value || ''),
      router_flags: collectDelegatedRouterFlags()
    }};
    const res = await fetch('/api/auth/users/save', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify(payload)
    }});
    const data = await res.json().catch(() => ({{}}));
    if (!res.ok || !data.ok) {{
      setDelegatedUserMessage(data.error || 'Не удалось сохранить пользователя.', true);
      return;
    }}
    authMeta = data.auth || authMeta;
    resetDelegatedUserForm(false);
    renderAuthMeta();
    setDelegatedUserMessage(data.message || 'Пользователь сохранён');
  }});
}}

totpSetupBtn.addEventListener('click', async () => {{
  setAuthMessage('');
  const currentPassword = totpCurrentPassword.value;
  if (!currentPassword) {{
    setAuthMessage('Введи текущий пароль для настройки 2FA.', true);
    return;
  }}
  const res = await fetch('/api/auth/totp/setup', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{current_password: currentPassword}})
  }});
  const data = await res.json().catch(() => ({{}}));
  if (!res.ok || !data.ok) {{
    setAuthMessage(data.error || 'Не удалось создать секрет 2FA', true);
    return;
  }}
  authTotpTicket = data.ticket || '';
  totpSecretValue.textContent = data.secret || '';
  totpUriValue.textContent = data.otpauth_uri || '';
  totpSecretBox.hidden = false;
  setAuthMessage('Секрет 2FA создан. Добавь его в приложение и подтверди кодом.');
}});

totpEnableBtn.addEventListener('click', async () => {{
  setAuthMessage('');
  if (!authTotpTicket) {{
    setAuthMessage('Сначала создай секрет 2FA.', true);
    return;
  }}
  if (!totpCurrentPassword.value) {{
    setAuthMessage('Введи текущий пароль для включения 2FA.', true);
    return;
  }}
  if (!totpCode.value.trim()) {{
    setAuthMessage('Введи код из приложения 2FA.', true);
    return;
  }}
  const res = await fetch('/api/auth/totp/enable', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{
      current_password: totpCurrentPassword.value,
      ticket: authTotpTicket,
      code: totpCode.value.trim()
    }})
  }});
  const data = await res.json().catch(() => ({{}}));
  if (!res.ok || !data.ok) {{
    setAuthMessage(data.error || 'Не удалось включить 2FA', true);
    return;
  }}
  resetTotpSetup();
  restoreOwnerPasswordInput(totpCurrentPassword);
  authMeta = data.auth || authMeta;
  renderAuthMeta();
  setAuthMessage(data.message || '2FA включена');
}});

totpDisableBtn.addEventListener('click', async () => {{
  setAuthMessage('');
  if (!totpDisableCurrentPassword.value) {{
    setAuthMessage('Введи текущий пароль для отключения 2FA.', true);
    return;
  }}
  if (!totpDisableCode.value.trim()) {{
    setAuthMessage('Введи текущий код 2FA для отключения.', true);
    return;
  }}
  const res = await fetch('/api/auth/totp/disable', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{
      current_password: totpDisableCurrentPassword.value,
      code: totpDisableCode.value.trim()
    }})
  }});
  const data = await res.json().catch(() => ({{}}));
  if (!res.ok || !data.ok) {{
    setAuthMessage(data.error || 'Не удалось отключить 2FA', true);
    return;
  }}
  resetTotpSetup();
  restoreOwnerPasswordInput(totpCurrentPassword);
  restoreOwnerPasswordInput(totpDisableCurrentPassword);
  totpDisableCode.value = '';
  authMeta = data.auth || authMeta;
  renderAuthMeta();
  setAuthMessage(data.message || '2FA выключена');
}});

passkeyRegisterBtn.addEventListener('click', async () => {{
  setAuthMessage('');
  if (!window.isSecureContext || !window.PublicKeyCredential) {{
    setAuthMessage('Passkey требует HTTPS и поддержку WebAuthn в браузере.', true);
    return;
  }}
  if (!passkeyCurrentPassword.value) {{
    setAuthMessage('Введи текущий пароль для регистрации passkey.', true);
    return;
  }}
  const beginRes = await fetch('/api/auth/passkeys/begin', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{
      current_password: passkeyCurrentPassword.value,
      label: passkeyLabel.value.trim()
    }})
  }});
  const beginData = await beginRes.json().catch(() => ({{}}));
  if (!beginRes.ok || !beginData.ok) {{
    setAuthMessage(beginData.error || 'Не удалось начать регистрацию passkey', true);
    return;
  }}
  try {{
    const credential = await navigator.credentials.create(decodeCredentialCreationOptions(beginData.options || {{}}));
    const transports = credential && credential.response && typeof credential.response.getTransports === 'function'
      ? credential.response.getTransports()
      : [];
    const finishRes = await fetch('/api/auth/passkeys/finish', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{
        ticket: beginData.ticket,
        credential: encodeAttestation(credential),
        transports
      }})
    }});
    const finishData = await finishRes.json().catch(() => ({{}}));
    if (!finishRes.ok || !finishData.ok) {{
      setAuthMessage(finishData.error || 'Не удалось завершить регистрацию passkey', true);
      return;
    }}
    restoreOwnerPasswordInput(passkeyCurrentPassword);
    passkeyLabel.value = '';
    authMeta = finishData.auth || authMeta;
    renderAuthMeta();
    setAuthMessage(finishData.message || 'Passkey добавлен');
  }} catch (err) {{
    setAuthMessage(err && err.message ? err.message : 'Регистрация passkey была отменена или завершилась с ошибкой', true);
  }}
}});

sshKeyAddBtn.addEventListener('click', async () => {{
  setAuthMessage('');
  if (!sshKeyCurrentPassword.value) {{
    setAuthMessage('Введи текущий пароль для добавления SSH ключа.', true);
    return;
  }}
  if (!sshKeyPublic.value.trim()) {{
    setAuthMessage('Вставь публичный SSH ED25519 ключ.', true);
    return;
  }}
  const res = await fetch('/api/auth/ssh-keys/add', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{
      current_password: sshKeyCurrentPassword.value,
      label: sshKeyLabel.value.trim(),
      public_key: sshKeyPublic.value.trim()
    }})
  }});
  const data = await res.json().catch(() => ({{}}));
  if (!res.ok || !data.ok) {{
    setAuthMessage(data.error || 'Не удалось добавить SSH ED25519 ключ', true);
    return;
  }}
  restoreOwnerPasswordInput(sshKeyCurrentPassword);
  sshKeyLabel.value = '';
  sshKeyPublic.value = '';
  authMeta = data.auth || authMeta;
  renderAuthMeta();
  setAuthMessage(data.message || 'SSH ED25519 ключ добавлен');
}});

renderSessions(window.HUB_SESSIONS);
renderNotifications(window.HUB_NOTIFICATIONS);
installOwnerPasswordRememberUi();
if (authMeta && typeof authMeta === 'object' && Object.keys(authMeta).length) {{
  renderAuthMeta();
  syncOwnerChrome();
}}
const clientVersionRedirecting = ensureClientVersion();
if (!clientVersionRedirecting) {{
  loadAuthMeta({{silent: true}}).catch(() => {{}});
  reportClientHint();
  updateNotifyButton();
  loadPushStatus({{silent: true}});
  warmPushEnvironment({{silent: true}});
}}
initSeasonEffects();
syncRouterSearchToggleState();
  requestRouterRender();
fillRouterForm(true);
waitNotificationsLoop();
setInterval(loadRouters, 5000);
setInterval(() => loadNotifications(), 30000);
window.addEventListener('beforeunload', () => {{
  clearAllTrafficAutoRefresh();
}});
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
{branding_head_tags(include_manifest=False)}
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


def router_notes_html(router, username="", saved="", error="", draft=None, edit_note_id=""):
    router = dict(router or {})
    router_id = str(router.get("id") or "").strip()
    router_name = str(router.get("name") or router_id or "router").strip()
    permissions = router.get("permissions") if isinstance(router.get("permissions"), dict) else {}
    can_edit = bool(permissions.get("notes_edit") or permissions.get("manage"))
    can_delete = bool(permissions.get("notes_delete") or permissions.get("owner"))
    note_entries = list(router.get("notes_entries") or [])
    visible_entries = list(reversed(note_entries))
    has_note_images = any(bool(item.get("images")) for item in visible_entries)
    edit_note_id = str(edit_note_id or "").strip()
    if not can_edit:
        edit_note_id = ""
    notes_value = clean_router_notes(draft if draft is not None else "")
    safe_router_name = html.escape(router_name)
    safe_notes = html.escape(notes_value)
    safe_notes_url = html.escape(str(router.get("notes_url") or f"/router/{urllib.parse.quote(router_id)}/notes"), quote=True)
    note_len = len(notes_value)
    notes_total = len(note_entries)
    message_html = ""
    saved = str(saved or "").strip().lower()
    def render_note_images_html(images):
        tiles = []
        for image in list(images or []):
            image_url = html.escape(str(image.get("url") or ""), quote=True)
            if not image_url:
                continue
            image_name = html.escape(str(image.get("name") or "Изображение"))
            tiles.append(
                f"""
          <a class="noteShot" href="{image_url}" target="_blank" rel="noopener noreferrer" title="{image_name}">
            <img src="{image_url}" alt="{image_name}" loading="lazy">
            <span>{image_name}</span>
          </a>"""
            )
        return f'<div class="noteGallery">{"".join(tiles)}</div>' if tiles else ""
    if error:
        message_html = f'<div class="flash bad">{html.escape(str(error))}</div>'
    elif saved == "added":
        message_html = '<div class="flash ok">Заметка опубликована.</div>'
    elif saved == "updated":
        message_html = '<div class="flash ok">Заметка обновлена.</div>'
    elif saved == "deleted":
        message_html = '<div class="flash ok">Заметка удалена.</div>'
    read_only_css = ""
    view_only_notice_html = ""
    if not can_edit:
        read_only_css = ".notesLayout{grid-template-columns:1fr}.editor{display:none}.cardActions{display:none!important}"
        view_only_notice_html = '<div class="viewOnlyBox">Доступ к заметкам открыт только для просмотра.</div>'
    notes_layout_class = "notesLayout notesLayoutMedia" if has_note_images else "notesLayout"
    notes_items = []
    for item in visible_entries:
        note_id = str(item.get("id") or "")
        safe_note_id = html.escape(note_id, quote=True)
        safe_edit_link = html.escape(f"{router.get('notes_url') or f'/router/{urllib.parse.quote(router_id)}/notes'}?edit={urllib.parse.quote(note_id)}", quote=True)
        note_time_iso = html.escape(str(item.get("created_at_iso") or ""), quote=True)
        note_time_label = html.escape(str(item.get("created_at_label") or "Дата публикации неизвестна"))
        note_text_html = ""
        if str(item.get("text") or "").strip():
            note_text_html = f'<div class="noteBody">{html.escape(str(item.get("text") or "")).replace(chr(10), "<br>")}</div>'
        note_images_html = render_note_images_html(item.get("images") or [])
        if can_edit and note_id == edit_note_id:
            edit_text = clean_router_notes(draft if draft is not None else item.get("text") or "")
            safe_edit_text = html.escape(edit_text)
            edit_count = len(edit_text)
            note_body_html = f"""
          <form class="noteEditForm" method="post" action="{safe_notes_url}">
            <input type="hidden" name="action" value="update">
            <input type="hidden" name="note_id" value="{safe_note_id}">
            <div class="editorHead noteEditHead">
              <h3>Редактирование</h3>
              <div class="counter"><span id="noteCount-{safe_note_id}">{edit_count}</span> / {ROUTER_NOTE_TEXT_MAX_CHARS}</div>
            </div>
            <textarea class="noteInlineArea" name="note" maxlength="{ROUTER_NOTE_TEXT_MAX_CHARS}" data-note-count-target="noteCount-{safe_note_id}">{safe_edit_text}</textarea>
            {note_images_html}
            <div class="cardActions">
              <button class="submitBtn submitBtnSmall" type="submit">Сохранить</button>
              <a class="linkBtn linkBtnSmall" href="{safe_notes_url}">Отмена</a>
            </div>
          </form>"""
        else:
            note_action_parts = []
            if can_edit:
                note_action_parts.append(f'<a class="linkBtn linkBtnSmall" href="{safe_edit_link}">Редактировать</a>')
            if can_delete:
                note_action_parts.append(
                    f"""
            <form class="inlineForm" method="post" action="{safe_notes_url}">
              <input type="hidden" name="action" value="delete">
              <input type="hidden" name="note_id" value="{safe_note_id}">
              <button class="dangerBtn" type="submit">Удалить</button>
            </form>"""
                )
            note_actions_html = ""
            if note_action_parts:
                note_actions_html = f"""
          <div class="cardActions">
            {''.join(note_action_parts)}
          </div>"""
            note_body_html = f"""
          {note_text_html}
          {note_images_html}
          {note_actions_html}"""
        notes_items.append(
            f"""
        <article class="noteCard">
          <div class="noteMeta">
            <strong>Публикация</strong>
            <time datetime="{note_time_iso}">{note_time_label}</time>
          </div>
{note_body_html}
        </article>
        """
        )
    notes_items_html = "".join(notes_items)
    if not notes_items_html:
        notes_items_html = '<div class="noteEmpty">Пока нет опубликованных заметок. Первая запись появится здесь сразу после сохранения.</div>'
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Заметки роутер {safe_router_name}</title>
<style>
:root{{--bg:#07040f;--panel:#171126;--panel-2:#221736;--line:rgba(167,139,250,.22);--line-strong:rgba(34,211,238,.34);--text:#f5f3ff;--muted:#c4b5fd;--ok:#22c55e;--bad:#fb7185;--shadow:0 22px 56px rgba(0,0,0,.34);--grid:rgba(168,85,247,.14)}}
*{{box-sizing:border-box}}body{{position:relative;min-height:100vh;margin:0;overflow-x:hidden;font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif;color:var(--text);background-color:var(--bg);background-image:radial-gradient(circle at 12% 8%,rgba(168,85,247,.40),transparent 31%),radial-gradient(circle at 82% 12%,rgba(79,70,229,.30),transparent 30%),radial-gradient(circle at 50% 105%,rgba(236,72,153,.20),transparent 35%),linear-gradient(145deg,#07040f,#120a24 48%,#05030a),repeating-linear-gradient(0deg,transparent 0 30px,var(--grid) 31px),repeating-linear-gradient(90deg,transparent 0 30px,var(--grid) 31px);background-size:130% 130%,140% 140%,135% 135%,100% 100%,31px 31px,31px 31px;background-attachment:fixed}}
body::before{{content:"";position:fixed;inset:-25%;pointer-events:none;background:conic-gradient(from 0deg at 50% 50%,rgba(168,85,247,.05),rgba(236,72,153,.26),rgba(59,130,246,.18),rgba(245,158,11,.10),rgba(168,85,247,.05));filter:blur(54px);opacity:.62}}
body::after{{content:"";position:fixed;inset:0;pointer-events:none;background:linear-gradient(180deg,rgba(255,255,255,.03),rgba(255,255,255,0));opacity:.32}}
.wrap{{position:relative;z-index:1;max-width:980px;margin:0 auto;padding:24px 16px 36px}}
.top{{display:flex;align-items:center;justify-content:flex-start;gap:12px;flex-wrap:wrap;margin-bottom:18px}}
.backRow{{display:flex;gap:8px;flex-wrap:wrap}}
.linkBtn,.submitBtn{{display:inline-flex;align-items:center;justify-content:center;min-height:40px;padding:0 14px;border:1px solid var(--line);border-radius:999px;background:rgba(255,255,255,.06);color:var(--text);text-decoration:none;font-weight:700;cursor:pointer}}
.submitBtn{{background:linear-gradient(120deg,rgba(34,211,238,.20),rgba(59,130,246,.26));border-color:var(--line-strong)}}
.linkBtnSmall,.submitBtnSmall,.dangerBtn{{min-height:34px;padding:0 12px;font-size:12px}}
.dangerBtn{{display:inline-flex;align-items:center;justify-content:center;border:1px solid rgba(251,113,133,.34);border-radius:999px;background:rgba(251,113,133,.12);color:#fecdd3;font-weight:700;cursor:pointer}}
.shell{{display:grid;gap:16px;padding:18px;border:1px solid var(--line);border-radius:22px;background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.04)),var(--panel);box-shadow:var(--shadow)}}
.hero{{display:grid;gap:10px}}
.heroTop{{display:flex;align-items:flex-start;justify-content:flex-start;gap:12px;flex-wrap:wrap}}
.hero h1{{margin:0;font-size:28px;line-height:1.1}}
.heroMeta{{display:flex;gap:8px;flex-wrap:wrap}}
.pill{{display:inline-flex;align-items:center;justify-content:center;min-height:32px;padding:0 12px;border:1px solid var(--line);border-radius:999px;background:rgba(255,255,255,.05);color:var(--muted);font-size:12px;font-weight:800}}
.pill.on{{border-color:rgba(34,197,94,.34);background:rgba(34,197,94,.14);color:#bbf7d0}}
.pill.off{{border-color:rgba(251,113,133,.34);background:rgba(251,113,133,.12);color:#fecdd3}}
.flash{{padding:12px 14px;border:1px solid var(--line);border-radius:14px;font-weight:700}}
.flash.ok{{border-color:rgba(34,197,94,.34);background:rgba(34,197,94,.12);color:#bbf7d0}}
.flash.bad{{border-color:rgba(251,113,133,.34);background:rgba(251,113,133,.12);color:#fecdd3}}
.notesLayout{{display:grid;grid-template-columns:minmax(0,1.02fr) minmax(280px,.98fr);gap:16px;align-items:stretch;padding:14px;border:1px solid rgba(167,139,250,.18);border-radius:18px;background:linear-gradient(180deg,rgba(255,255,255,.05),rgba(255,255,255,.03)),rgba(23,17,38,.72);box-shadow:inset 0 1px 0 rgba(255,255,255,.03)}}
.notesLayoutMedia{{grid-template-columns:1fr}}
.editor,.history{{display:grid;gap:10px;align-content:start;height:100%;padding:0;border:0;border-radius:0;background:transparent}}
.editorHead{{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}}
.editorHead h2{{margin:0;font-size:16px}}
.counter{{color:var(--muted);font-size:12px;font-weight:800}}
textarea{{width:100%;min-height:140px;padding:14px 16px;border:1px solid var(--line-strong);border-radius:16px;background:linear-gradient(180deg,rgba(47,34,72,.96),rgba(28,21,44,.98));color:var(--text);resize:vertical;outline:none;font:14px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace;box-shadow:inset 0 1px 0 rgba(255,255,255,.04)}}
textarea::placeholder{{color:rgba(196,181,253,.68)}}
textarea:focus{{box-shadow:0 0 0 3px rgba(34,211,238,.10),inset 0 1px 0 rgba(255,255,255,.04)}}
.hint{{margin:0;color:var(--muted);font-size:12px}}
.actions{{display:flex;align-items:center;justify-content:flex-start;gap:12px;flex-wrap:wrap}}
.fileBtn{{position:relative;overflow:hidden}}
.fileInput{{position:absolute;inset:0;opacity:0;cursor:pointer}}
.uploadMeta{{min-height:18px;color:var(--muted);font-size:12px;line-height:1.35}}
.historyHead{{display:flex;align-items:center;justify-content:flex-start;gap:10px;flex-wrap:wrap}}
.historyHead h2{{margin:0;font-size:16px}}
.historyCount{{margin-left:auto;padding-right:6px;color:var(--muted);font-size:12px;font-weight:800;line-height:1.3;text-align:right;max-width:100%}}
.historyList{{display:grid;gap:10px;max-height:640px;overflow:auto;padding-right:4px;align-content:start}}
.notesLayoutMedia .historyList{{max-height:none;overflow:visible;padding-right:0}}
.noteCard{{display:grid;gap:10px;align-content:start;min-width:0;min-height:140px;padding:14px 16px;border:1px solid rgba(34,211,238,.16);border-radius:16px;background:linear-gradient(180deg,rgba(43,33,67,.92),rgba(28,21,44,.96));box-shadow:inset 0 1px 0 rgba(255,255,255,.03);overflow:hidden}}
.noteMeta{{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;flex-wrap:wrap}}
.noteMeta strong{{font-size:12px;letter-spacing:.04em;text-transform:uppercase;color:#a5f3fc}}
.noteMeta time{{color:var(--muted);font-size:12px;font-weight:700}}
.noteBody{{white-space:pre-wrap;word-break:break-word;line-height:1.55}}
.inlineForm{{margin:0}}
.cardActions{{display:flex;align-items:center;gap:8px;flex-wrap:wrap}}
.noteEditForm{{display:grid;gap:10px}}
.noteEditHead h3{{margin:0;font-size:14px}}
.noteInlineArea{{min-height:120px}}
.noteGallery{{display:flex;flex-wrap:wrap;gap:8px;align-items:flex-start;width:100%;min-width:0}}
.noteShot{{display:grid;grid-template-rows:auto 16px;align-content:start;gap:6px;flex:0 0 136px;width:136px;min-width:0;padding:8px;border:1px solid rgba(167,139,250,.18);border-radius:12px;background:rgba(255,255,255,.04);color:var(--muted);text-decoration:none;overflow:hidden}}
.noteShot img{{display:block;width:100%;aspect-ratio:1/1;object-fit:cover;border-radius:8px;background:rgba(12,9,23,.72)}}
.noteShot span{{display:block;min-width:0;overflow:hidden;font-size:10px;line-height:16px;white-space:nowrap;text-overflow:ellipsis}}
.noteEmpty{{display:grid;align-content:center;min-height:140px;padding:14px 16px;border:1px dashed rgba(167,139,250,.20);border-radius:16px;color:var(--muted);text-align:center}}
.viewOnlyBox{{padding:11px 13px;border:1px solid rgba(34,211,238,.20);border-radius:14px;background:rgba(34,211,238,.08);color:#cffafe;font-size:12px;font-weight:700}}
{read_only_css}
@media(max-width:860px){{.notesLayout{{grid-template-columns:1fr}}.historyList{{max-height:none}}}}
@media(max-width:680px){{
  body{{background-attachment:scroll}}
  .wrap{{padding:10px 8px 18px}}
  .top{{margin-bottom:10px}}
  .backRow,.backRow .linkBtn{{width:100%}}
  .shell{{gap:12px;padding:12px 10px;border-radius:16px}}
  .hero{{gap:6px}}
  .heroTop{{gap:8px}}
  .hero h1{{font-size:18px;line-height:1.14}}
  .notesLayout{{gap:10px;padding:10px;border-radius:14px}}
  .editor,.history{{gap:8px}}
  .editorHead,.historyHead{{align-items:flex-start;gap:6px}}
  .historyHead{{flex-direction:column}}
  .editorHead h2,.historyHead h2{{font-size:14px}}
  .counter,.historyCount{{font-size:11px}}
  .historyCount{{margin-left:0;padding-right:0;text-align:left}}
  .linkBtn,.submitBtn,.dangerBtn{{min-height:36px;padding:0 10px;font-size:12px}}
  textarea{{min-height:94px;padding:11px 12px;font-size:15px;line-height:1.34;border-radius:14px}}
  #routerNotes::placeholder{{font-size:13px;line-height:1.28}}
  .actions{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}
  .actions .submitBtn,.actions .linkBtn{{width:100%;min-width:0;padding:0 10px}}
  .uploadMeta{{min-height:0;font-size:10px}}
  .historyList{{gap:8px;padding-right:0}}
  .noteCard,.noteEmpty{{min-height:0;gap:8px;padding:10px 12px;border-radius:12px}}
  .noteMeta{{flex-direction:column;gap:4px}}
  .noteMeta strong{{font-size:11px}}
  .noteMeta time{{font-size:10px}}
  .noteBody{{font-size:13px;line-height:1.45}}
  .cardActions{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}
  .cardActions .linkBtn,.cardActions .dangerBtn,.cardActions .submitBtn{{width:100%;min-width:0}}
  .inlineForm{{display:block}}
  .inlineForm .dangerBtn{{width:100%}}
  .noteInlineArea{{min-height:88px}}
  .noteGallery{{gap:6px}}
  .noteShot{{gap:5px;flex-basis:112px;width:112px;padding:6px;border-radius:10px}}
  .noteShot span{{font-size:9px;line-height:1.15}}
}}
@media(max-width:420px){{
  .wrap{{padding:8px 7px 16px}}
  .shell{{padding:10px 9px;border-radius:15px}}
  .hero h1{{font-size:17px}}
  .notesLayout{{padding:9px;border-radius:13px}}
  .editorHead h2,.historyHead h2{{font-size:13px}}
  .counter,.historyCount{{font-size:10px}}
  textarea{{min-height:86px;padding:10px 11px;font-size:14px}}
  #routerNotes::placeholder{{font-size:12px;line-height:1.22}}
  .actions,.cardActions{{grid-template-columns:1fr}}
  .linkBtn,.submitBtn,.dangerBtn{{min-height:34px;font-size:11px}}
  .noteCard,.noteEmpty{{padding:9px 10px}}
  .noteBody{{font-size:12px}}
  .noteGallery{{gap:6px}}
  .noteShot{{flex-basis:96px;width:96px}}
}}
</style>
</head>
<body>
<main class="wrap">
  <div class="top">
    <div class="backRow">
      <a class="linkBtn" href="/">Вернуться в панель Hub</a>
    </div>
  </div>
  <section class="shell">
    <div class="hero">
      <div class="heroTop">
        <div>
          <h1>Заметки роутер {safe_router_name}</h1>
        </div>
      </div>
      {message_html}
    </div>
    <div class="{notes_layout_class}">
      <form class="editor" method="post" action="{safe_notes_url}" enctype="multipart/form-data">
        <input type="hidden" name="action" value="add">
        <div class="editorHead">
          <h2>Новая заметка</h2>
          <div class="counter"><span id="noteCount">{note_len}</span> / {ROUTER_NOTE_TEXT_MAX_CHARS}</div>
        </div>
        <textarea id="routerNotes" name="note" maxlength="{ROUTER_NOTE_TEXT_MAX_CHARS}" placeholder="Например: клиент zks95, менял DNS, отключал 5 ГГц, просил не трогать lan4.">{safe_notes}</textarea>
        <div class="actions">
          <button class="submitBtn" type="submit">Сохранить заметку</button>
          <label class="linkBtn fileBtn">
            Прикрепить изображения
            <input class="fileInput" id="noteImages" type="file" name="images" accept="image/*" multiple>
          </label>
        </div>
        <div class="uploadMeta" id="noteFilesMeta">Можно выбрать до {ROUTER_NOTE_IMAGE_MAX_FILES} изображений.</div>
      </form>
      <section class="history">
        <div class="historyHead">
          <h2>История заметок</h2>
          <div class="historyCount">Всего публикаций: {notes_total}</div>
        </div>
        {view_only_notice_html}
        <div class="historyList">
          {notes_items_html}
        </div>
      </section>
    </div>
  </section>
</main>
<script>
document.querySelectorAll('textarea[data-note-count-target], #routerNotes').forEach((field) => {{
  const targetId = field.getAttribute('data-note-count-target') || 'noteCount';
  const target = document.getElementById(targetId);
  if (!target) return;
  const syncCount = () => {{
    target.textContent = String((field.value || '').length);
  }};
  field.addEventListener('input', syncCount);
  syncCount();
}});
const noteImages = document.getElementById('noteImages');
const noteFilesMeta = document.getElementById('noteFilesMeta');
if (noteImages && noteFilesMeta) {{
  const syncFiles = () => {{
    const files = Array.from(noteImages.files || []);
    if (!files.length) {{
      noteFilesMeta.textContent = 'Можно выбрать до {ROUTER_NOTE_IMAGE_MAX_FILES} изображений.';
      return;
    }}
    const preview = files.slice(0, 3).map((file) => file.name).join(', ');
    noteFilesMeta.textContent = files.length > 3
      ? `Выбрано ${{files.length}} изображений: ${{preview}}...`
      : `Выбрано ${{files.length}} изображений: ${{preview}}`;
  }};
  noteImages.addEventListener('change', syncFiles);
  syncFiles();
}}
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
{branding_head_tags(include_manifest=False)}
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
window.addEventListener('load',()=>{initTerminal();});
</script>
</body>
</html>"""
    return (
        page.replace("__SAFE_NAME__", safe_name)
        .replace("{branding_head_tags(include_manifest=False)}", branding_head_tags(include_manifest=False))
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


def _legacy_login_html(error=""):
    error_html = f"<div class=\"err\">{html.escape(error)}</div>" if error else ""
    auth = normalize_auth_state(load_auth())
    captcha_html = legacy_login_captcha_html(auth)
    recaptcha_script = login_recaptcha_script(auth)
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
{branding_head_tags()}
<title>OpenWrt Remote Hub</title>
<style>
:root{{color-scheme:dark;--bg:#07040f;--panel:rgba(19,14,32,.9);--text:#f7f2ff;--muted:#b9adc9;--line:rgba(169,126,255,.28);--blue:#7c3aed;--cyan:#22d3ee;--red:#fb7185;--green:#22c55e;--grid:rgba(168,85,247,.13)}}
*{{box-sizing:border-box}}
body{{position:relative;min-height:100vh;margin:0;overflow-x:hidden;background-color:var(--bg);background-image:radial-gradient(circle at 16% 12%,rgba(168,85,247,.48),transparent 30%),radial-gradient(circle at 84% 8%,rgba(59,130,246,.34),transparent 32%),radial-gradient(circle at 55% 105%,rgba(236,72,153,.24),transparent 36%),linear-gradient(145deg,#07040f,#120a24 48%,#05030a),repeating-linear-gradient(0deg,transparent 0 30px,var(--grid) 31px),repeating-linear-gradient(90deg,transparent 0 30px,var(--grid) 31px);background-size:130% 130%,140% 140%,135% 135%,100% 100%,31px 31px,31px 31px;background-attachment:fixed;color:var(--text);font:14px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;display:grid;place-items:center;padding:18px;animation:bgFlow 28s ease-in-out infinite alternate}}
body::before{{content:"";position:fixed;inset:-28%;pointer-events:none;background:conic-gradient(from 0deg at 50% 50%,rgba(168,85,247,.06),rgba(236,72,153,.30),rgba(59,130,246,.22),rgba(34,211,238,.16),rgba(168,85,247,.06));filter:blur(58px);opacity:.74;animation:auraSpin 40s linear infinite}}
@keyframes bgFlow{{0%{{background-position:0% 0%,100% 0%,50% 100%,0 0,0 0,0 0}}50%{{background-position:24% 18%,62% 28%,38% 82%,0 0,15px 24px,24px 15px}}100%{{background-position:46% 30%,42% 42%,74% 62%,0 0,30px 0,0 30px}}}}
@keyframes auraSpin{{from{{transform:rotate(0deg) scale(1)}}to{{transform:rotate(360deg) scale(1.08)}}}}
.loginShell{{position:relative;z-index:1;width:min(760px,calc(100vw - 24px))}}
.loginFrame{{position:relative;width:100%;padding:9px;border:1px solid rgba(169,126,255,.32);border-radius:18px;background:linear-gradient(180deg,rgba(255,255,255,.11),rgba(255,255,255,.045)),var(--panel);box-shadow:0 28px 72px rgba(0,0,0,.42);backdrop-filter:blur(16px)}}
.loginMain{{display:grid;grid-template-columns:minmax(246px,270px) minmax(0,372px);gap:10px;align-items:start;justify-content:center}}
.login{{position:relative;width:100%;min-height:100%;padding:10px;border:1px solid rgba(169,126,255,.22);border-radius:14px;background:linear-gradient(180deg,rgba(255,255,255,.06),rgba(255,255,255,.025));box-shadow:inset 0 1px 0 rgba(255,255,255,.05)}}
.brand{{display:block;text-align:center;margin-bottom:10px}}
h1{{margin:0;font-size:13px;line-height:1.1;letter-spacing:0}}.appBanner{{position:relative;display:flex;align-items:center;justify-content:center;width:100%;min-height:42px;padding:9px 12px;border:1px solid rgba(34,211,238,.34);border-radius:8px;background:linear-gradient(110deg,rgba(34,211,238,.14),rgba(124,58,237,.24),rgba(236,72,153,.14));box-shadow:0 10px 24px rgba(124,58,237,.18),inset 0 1px 0 rgba(255,255,255,.10);font-size:13px;font-weight:950;overflow:hidden}}.appBanner::before{{content:"";position:absolute;inset:0;background:linear-gradient(90deg,transparent,rgba(255,255,255,.18),transparent);transform:translateX(-120%);animation:bannerShine 6.2s ease-in-out infinite}}.appBanner span{{position:relative}}.appBannerVersion{{color:var(--red);text-shadow:0 0 12px rgba(251,113,133,.35)}}@keyframes bannerShine{{0%,45%{{transform:translateX(-120%)}}72%,100%{{transform:translateX(120%)}}}}
p{{margin:3px 0 0;color:var(--muted)}}
label{{display:block;margin:8px 0 4px;font-weight:850;color:#ede9fe;text-align:center}}
input{{width:100%;min-height:42px;border:1px solid var(--line);border-radius:8px;padding:9px 12px;background:rgba(8,5,18,.74);color:var(--text);outline:none;text-align:center;box-shadow:inset 0 1px 0 rgba(255,255,255,.04)}}
textarea{{width:100%;border:1px solid var(--line);border-radius:8px;padding:9px 12px;background:rgba(8,5,18,.74);color:var(--text);outline:none;box-shadow:inset 0 1px 0 rgba(255,255,255,.04);resize:vertical;min-height:62px;font:12px/1.35 ui-monospace,SFMono-Regular,Consolas,monospace}}
input:focus{{border-color:rgba(34,211,238,.62);box-shadow:0 0 0 3px rgba(34,211,238,.12),inset 0 1px 0 rgba(255,255,255,.04)}}
textarea:focus{{border-color:rgba(34,211,238,.62);box-shadow:0 0 0 3px rgba(34,211,238,.12),inset 0 1px 0 rgba(255,255,255,.04)}}
.captcha{{width:100%;display:flex;align-items:center;justify-content:center;min-height:42px;margin-top:0;border:1px solid var(--line);border-radius:8px;padding:9px 12px;background:rgba(8,5,18,.74);text-align:center;box-shadow:inset 0 1px 0 rgba(255,255,255,.04)}}.captcha b{{display:block;color:#fde68a;font:950 21px/1 ui-monospace,SFMono-Regular,Consolas,monospace;letter-spacing:6px;text-indent:6px;text-shadow:0 0 18px rgba(251,191,36,.28)}}
.recaptchaWrap{{padding:12px;overflow-x:auto}}
button{{width:100%;min-height:42px;margin-top:10px;border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:9px 12px;background:linear-gradient(135deg,#7c3aed,#a855f7);color:#fff;font-weight:950;font-size:13px;line-height:1.1;cursor:pointer;box-shadow:0 14px 30px rgba(124,58,237,.28);white-space:nowrap}}
button:hover{{filter:brightness(1.06)}}
.err{{margin:0 0 12px;padding:11px 12px;border:1px solid rgba(251,113,133,.45);border-radius:8px;background:rgba(251,113,133,.14);color:#fecdd3;font-weight:800}}
.hint{{margin:8px 0 0;color:var(--muted);text-align:center;font-size:11px;line-height:1.3}}.login>.hint{{display:none}}
.otpCard{{margin-top:6px;padding:7px;border:1px solid rgba(34,211,238,.18);border-radius:8px;background:rgba(34,211,238,.06)}}
.loginMethods{{display:grid;gap:10px;align-content:start;width:min(100%,372px);justify-self:center}}
.methodGrid{{display:grid;gap:10px;align-content:start}}
.methodCard{{padding:10px;border:1px solid rgba(169,126,255,.20);border-radius:14px;background:linear-gradient(180deg,rgba(255,255,255,.06),rgba(255,255,255,.025));box-shadow:inset 0 1px 0 rgba(255,255,255,.05)}}
.methodCard h2{{margin:0 0 5px;font-size:16px}}
.methodCard p{{margin:0 0 8px;font-size:12px;line-height:1.45}}
.methodMeta{{display:inline-flex;align-items:center;gap:8px;min-height:26px;padding:5px 9px;border:1px solid var(--line);border-radius:999px;background:rgba(255,255,255,.06);color:#ddd6fe;font-size:11px;font-weight:800}}
.methodStatus{{margin-top:9px;padding:10px 12px;border:1px solid rgba(34,197,94,.32);border-radius:8px;background:rgba(34,197,94,.10);color:#bbf7d0;font-weight:800;line-height:1.35}}
.methodStatus.bad{{border-color:rgba(251,113,133,.4);background:rgba(251,113,133,.12);color:#fecdd3}}
.methodRow{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px;margin-top:6px}}
.methodRow .wide{{grid-column:1/-1;min-height:62px}}
.methodBtnRow{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px;margin-bottom:6px}}
.methodBtnRow button{{margin-top:0;min-height:42px}}
.methodBtnRow button:only-child{{grid-column:1/-1}}
.codeBlock{{margin-top:6px;padding:7px 9px;border:1px solid rgba(255,255,255,.09);border-radius:8px;background:rgba(0,0,0,.18);color:#c4b5fd;font:11px/1.35 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;word-break:break-word}}
.manualGuideLink{{display:inline-flex;align-items:center;justify-content:center;min-height:38px;margin-top:8px;padding:8px 14px;border:1px solid rgba(34,211,238,.24);border-radius:999px;background:rgba(34,211,238,.08);color:#dbeafe;text-decoration:none;font-size:12px;font-weight:850}}
.manualGuideLink:hover{{background:rgba(34,211,238,.14)}}
.authFlowCard{{display:none}}
.authFlowBoard{{display:grid;gap:10px}}
.flowStage{{display:grid;place-items:center;padding:11px 12px;border:1px solid rgba(34,211,238,.24);border-radius:12px;background:rgba(34,211,238,.08);text-align:center;font-weight:900}}
.flowArrow{{text-align:center;color:#c4b5fd;font-size:18px;line-height:1}}
.flowBranches{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}}
.flowBranch{{display:grid;gap:8px;padding:12px;border:1px solid rgba(255,255,255,.09);border-radius:12px;background:rgba(255,255,255,.05)}}
.flowBranch strong{{font-size:15px}}
.flowBranch small{{color:var(--muted);line-height:1.35}}
.flowBadge{{display:inline-flex;align-items:center;justify-content:center;min-height:28px;padding:5px 9px;border:1px solid rgba(255,255,255,.10);border-radius:999px;background:rgba(255,255,255,.06);color:#ddd6fe;font-size:12px;font-weight:850}}
.flowBadge.ready{{border-color:rgba(34,197,94,.30);background:rgba(34,197,94,.12);color:#bbf7d0}}
.flowBadge.pending{{border-color:rgba(245,158,11,.24);background:rgba(245,158,11,.12);color:#fde68a}}
.flowBadge.reserve{{border-color:rgba(34,211,238,.24);background:rgba(34,211,238,.08);color:#a5f3fc}}
.flowBadge.off{{border-color:rgba(255,255,255,.10);background:rgba(255,255,255,.05);color:#c4b5fd}}
.flowFinal{{display:grid;place-items:center;padding:12px;border:1px solid rgba(34,197,94,.30);border-radius:12px;background:rgba(34,197,94,.11);color:#bbf7d0;font-weight:900;text-align:center}}
@media(max-width:980px){{.loginMain{{grid-template-columns:1fr}}}}
@media(max-width:720px){{.flowBranches{{grid-template-columns:1fr}}}}
@media(max-width:520px){{body{{padding:12px;overflow:auto}}.loginShell{{width:min(100%,520px)}}.loginFrame{{padding:10px}}.login{{padding:12px;min-height:0}}h1{{font-size:22px}}.login .brand .appBanner{{min-height:50px}}.methodRow{{grid-template-columns:1fr}}.manualGuideLink{{width:100%}}}}
</style>
{recaptcha_script}
</head>
<body>
<main class="loginShell">
  <section class="loginFrame">
    <div class="loginMain">
      <form class="login" method="post" action="/login">
    {error_html}
    <span class="brand">
      <h1 class="appBanner"><span>OpenWrt Remote Hub <span class="appBannerVersion">v106</span></span></h1>
    </span>
    <label for="hubUsername">Логин</label>
    <input id="hubUsername" name="username" autocomplete="off" autofocus required>
    <label for="hubPassword">Пароль</label>
    <input id="hubPassword" name="password" type="password" autocomplete="current-password" required>
    <div class="otpCard">
      <label for="hubOtp">Код 2FA</label>
      <input id="hubOtp" name="otp" inputmode="numeric" pattern="[0-9]*" autocomplete="one-time-code" placeholder="Код 2FA">
      <div class="hint" id="passwordModeHint" hidden>Резервный вход через пароль. Когда 2FA включена, сюда нужен TOTP-код.</div>
    </div>
    {captcha_html}
    <button>Войти</button>
    <div class="hint">Резервная ветка: `Password + 2FA`. Основные альтернативы справа: `Passkey` и `ED25519`.</div>
      </form>
      <section class="loginMethods">
        <section class="methodGrid">
    <section class="methodCard authFlowCard">
      <div class="methodMeta">AUTH FLOW</div>
      <h2>Схема доступа</h2>
      <p>Основной вход можно увести в Passkey или ED25519, а пароль оставить как резервную ветку с 2FA.</p>
      <div class="authFlowBoard">
        <div class="flowStage">WEB-панель</div>
        <div class="flowArrow">↓</div>
        <div class="flowStage">Авторизация</div>
        <div class="flowBranches">
          <div class="flowBranch">
            <span class="flowBadge off" id="flowPasskeyState">Passkey: off</span>
            <strong>Passkey</strong>
            <small>Android, iPhone, Windows Hello, Face ID, Touch ID и системные ключи.</small>
          </div>
          <div class="flowBranch">
            <span class="flowBadge off" id="flowEd25519State">ED25519: off</span>
            <strong>ED25519</strong>
            <small>Вход по SSH challenge и ASCII signature без отправки приватного ключа.</small>
          </div>
          <div class="flowBranch">
            <span class="flowBadge reserve" id="flowPasswordState">Password: reserve</span>
            <strong>Резерв</strong>
            <small>Пароль остается запасным методом и усиливается TOTP-кодом, когда 2FA включена.</small>
          </div>
        </div>
        <div class="flowArrow">↓</div>
        <div class="flowFinal" id="flowSessionState">Secure session</div>
      </div>
    </section>
    <section class="methodCard">
      <div class="methodMeta">PASSKEY</div>
      <h2>Вход по Passkey</h2>
      <p>Android, iPhone, Windows Hello, Touch ID и другие системные ключи могут входить без пароля.</p>
      <div class="methodBtnRow">
        <button id="passkeyLoginBtn" type="button">Войти через Passkey</button>
      </div>
      <div class="hint" id="passkeyHint">Сначала зарегистрируй passkey в меню доступа внутри панели.</div>
      <div class="methodStatus" id="passkeyStatus" hidden></div>
    </section>
    <section class="methodCard">
      <div class="methodMeta">ED25519</div>
      <h2>Вход по SSH ED25519</h2>
      <div class="methodBtnRow">
        <button id="sshChallengeBtn" type="button">Получить challenge</button>
        <button id="sshVerifyBtn" type="button">Проверить подпись</button>
      </div>
      <div class="methodRow">
        <textarea class="wide" id="sshChallenge" readonly placeholder="Здесь появится challenge для подписи"></textarea>
        <textarea class="wide" id="sshSignature" placeholder="Вставь блок -----BEGIN SSH SIGNATURE----- ... -----END SSH SIGNATURE-----"></textarea>
      </div>
      <a class="manualGuideLink" href="/ssh-key-manual/" target="_blank" rel="noopener noreferrer">Мануал как создать Ssh key</a>
      <div class="methodStatus" id="sshStatus" hidden></div>
    </section>
        </section>
      </section>
    </div>
  </section>
</main>
<script>
const hubUsername = document.getElementById('hubUsername');
const hubOtp = document.getElementById('hubOtp');
const passwordModeHint = document.getElementById('passwordModeHint');
const passkeyLoginBtn = document.getElementById('passkeyLoginBtn');
const passkeyHint = document.getElementById('passkeyHint');
const passkeyStatus = document.getElementById('passkeyStatus');
const sshChallengeBtn = document.getElementById('sshChallengeBtn');
const sshVerifyBtn = document.getElementById('sshVerifyBtn');
const sshChallenge = document.getElementById('sshChallenge');
const sshSignature = document.getElementById('sshSignature');
const sshHint = document.getElementById('sshHint');
const sshStatus = document.getElementById('sshStatus');
const flowPasskeyState = document.getElementById('flowPasskeyState');
const flowEd25519State = document.getElementById('flowEd25519State');
const flowPasswordState = document.getElementById('flowPasswordState');
const flowSessionState = document.getElementById('flowSessionState');
let loginAuthMeta = {initial_login_meta_json};
let sshTicket = '';

function normalizeLoginUi() {{
  const authFlowCard = document.querySelector('.authFlowCard');
  if (authFlowCard) authFlowCard.remove();
  const reserveHint = document.querySelector('.login > .hint');
  if (reserveHint) reserveHint.hidden = true;
  if (passwordModeHint) passwordModeHint.hidden = true;
}}

function setMethodStatus(node, text, bad = false) {{
  node.hidden = false;
  node.className = 'methodStatus' + (bad ? ' bad' : '');
  node.textContent = text;
}}

function setFlowBadge(node, text, mode = 'off') {{
  if (!node) return;
  node.textContent = text;
  node.className = 'flowBadge ' + (mode || 'off');
}}

function b64urlToBytes(value) {{
  const normalized = String(value || '').replace(/-/g, '+').replace(/_/g, '/');
  const padded = normalized + '='.repeat((4 - normalized.length % 4) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}}

function bytesToB64url(buffer) {{
  const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer || []);
  let binary = '';
  for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
  return btoa(binary).replace(/\\+/g, '-').replace(/\\//g, '_').replace(/=+$/g, '');
}}

function decodeRequestOptions(options) {{
  const publicKey = Object.assign({{}}, options.publicKey || {{}});
  publicKey.challenge = b64urlToBytes(publicKey.challenge || '');
  publicKey.allowCredentials = Array.isArray(publicKey.allowCredentials)
    ? publicKey.allowCredentials.map((item) => Object.assign({{}}, item, {{id: b64urlToBytes(item.id || '')}}))
    : [];
  return {{publicKey}};
}}

function encodeAssertion(assertion) {{
  return {{
    id: assertion.id,
    rawId: bytesToB64url(assertion.rawId),
    type: assertion.type,
    authenticatorAttachment: assertion.authenticatorAttachment || null,
    clientExtensionResults: assertion.getClientExtensionResults ? assertion.getClientExtensionResults() : {{}},
    response: {{
      clientDataJSON: bytesToB64url(assertion.response.clientDataJSON),
      authenticatorData: bytesToB64url(assertion.response.authenticatorData),
      signature: bytesToB64url(assertion.response.signature),
      userHandle: assertion.response.userHandle ? bytesToB64url(assertion.response.userHandle) : null
    }}
  }};
}}

function isCompactSshLogin() {{
  return window.matchMedia ? window.matchMedia('(max-width: 760px)').matches : window.innerWidth <= 760;
}}

function updateSshHintText(sshCount) {{
  if (!sshHint) return;
  if (!sshCount) {{
    sshHint.textContent = 'Сначала добавь SSH ED25519 ключ в меню доступа внутри панели.';
    return;
  }}
  sshHint.textContent = isCompactSshLogin()
    ? 'SSH ED25519 вход доступен на полном экране. На телефоне здесь оставлена только подсказка.'
    : 'Нажми Получить challenge, затем вставь подпись и нажми Проверить подпись.';
}}

function updateLoginMeta() {{
  if (loginAuthMeta.totp_enabled) {{
    hubOtp.required = true;
    passwordModeHint.textContent = '2FA включена: для входа по паролю обязателен TOTP-код.';
  }} else {{
    hubOtp.required = false;
    passwordModeHint.textContent = 'Резервный вход через пароль. Когда 2FA включена, сюда нужен TOTP-код.';
  }}
  if (!window.PublicKeyCredential || !window.isSecureContext) {{
    passkeyLoginBtn.disabled = true;
    passkeyHint.textContent = 'Passkey требует secure context: HTTPS или localhost.';
  }} else if (!loginAuthMeta.passkeys_supported) {{
    passkeyLoginBtn.disabled = true;
    passkeyHint.textContent = 'На сервере еще не установлен WebAuthn модуль. Запусти свежий install-vps.sh.';
  }} else if (!Number(loginAuthMeta.passkey_count || 0)) {{
    passkeyLoginBtn.disabled = true;
    passkeyHint.textContent = 'Сначала зарегистрируй passkey в меню доступа внутри панели.';
  }} else {{
    passkeyLoginBtn.disabled = false;
    passkeyHint.textContent = 'Готово: можно входить через системный passkey.';
  }}
}}

function refreshLoginFlowMeta() {{
  const passkeyCount = Number(loginAuthMeta.passkey_count || 0);
  const sshCount = Number(loginAuthMeta.ssh_key_count || 0);
  setFlowBadge(flowPasswordState, loginAuthMeta.totp_enabled ? 'Password + 2FA' : 'Password reserve', loginAuthMeta.totp_enabled ? 'ready' : 'reserve');
  if (!window.PublicKeyCredential || !window.isSecureContext) {{
    setFlowBadge(flowPasskeyState, 'Passkey: secure context', 'pending');
  }} else if (!loginAuthMeta.passkeys_supported) {{
    setFlowBadge(flowPasskeyState, 'Passkey: server module', 'pending');
  }} else if (!passkeyCount) {{
    setFlowBadge(flowPasskeyState, 'Passkey: add first key', 'pending');
  }} else {{
    setFlowBadge(flowPasskeyState, `Passkey: ${{passkeyCount}}`, 'ready');
  }}
  if (!sshCount) {{
    sshTicket = '';
    sshChallenge.value = '';
    sshSignature.value = '';
    sshChallengeBtn.disabled = true;
    sshVerifyBtn.disabled = true;
    sshHint.textContent = 'Сначала добавь SSH ED25519 ключ в меню доступа внутри панели.';
    setFlowBadge(flowEd25519State, 'ED25519: add key', 'pending');
  }} else {{
    sshChallengeBtn.disabled = false;
    sshVerifyBtn.disabled = !sshTicket;
    sshHint.textContent = 'Нажми Получить challenge, затем вставь подпись и нажми Проверить подпись.';
    setFlowBadge(flowEd25519State, `ED25519: ${{sshCount}}`, 'ready');
  }}
  const readyMethods = 1 + (passkeyCount ? 1 : 0) + (sshCount ? 1 : 0);
  if (flowSessionState) flowSessionState.textContent = `Secure session after auth • paths ready: ${{readyMethods}}`;
}}

async function loadLoginMeta() {{
  try {{
    const res = await fetch('/api/auth/meta', {{cache: 'no-store'}});
    const data = await res.json();
    loginAuthMeta = data.auth || {{}};
    updateLoginMeta();
    refreshLoginFlowMeta();
  }} catch (err) {{
    passwordModeHint.textContent = 'Не удалось получить статус 2FA/Passkey. Парольный вход все равно доступен.';
  }}
}}

passkeyLoginBtn.addEventListener('click', async () => {{
  passkeyStatus.hidden = true;
  try {{
    const beginRes = await fetch('/api/login/passkey/begin', {{method: 'POST'}});
    const beginData = await beginRes.json().catch(() => ({{}}));
    if (!beginRes.ok || !beginData.ok) throw new Error(beginData.error || 'Не удалось начать Passkey-вход');
    const assertion = await navigator.credentials.get(decodeRequestOptions(beginData.options || {{}}));
    const finishRes = await fetch('/api/login/passkey/finish', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{ticket: beginData.ticket, credential: encodeAssertion(assertion)}})
    }});
    const finishData = await finishRes.json().catch(() => ({{}}));
    if (!finishRes.ok || !finishData.ok) throw new Error(finishData.error || 'Passkey-вход не подтвердился');
    window.location.href = finishData.redirect || '/';
  }} catch (err) {{
    setMethodStatus(passkeyStatus, err && err.message ? err.message : 'Passkey-вход не удался', true);
  }}
}});

sshChallengeBtn.addEventListener('click', async () => {{
  sshStatus.hidden = true;
  try {{
    const res = await fetch('/api/login/ssh/begin', {{method: 'POST'}});
    const data = await res.json().catch(() => ({{}}));
    if (!res.ok || !data.ok) throw new Error(data.error || 'Не удалось получить challenge');
    sshTicket = data.ticket || '';
    sshChallenge.value = data.challenge || '';
    sshVerifyBtn.disabled = !sshTicket;
    setMethodStatus(sshStatus, 'Challenge готов. Подпиши его SSH ED25519 ключом и вставь signature.');
  }} catch (err) {{
    sshVerifyBtn.disabled = true;
    setMethodStatus(sshStatus, err && err.message ? err.message : 'Не удалось получить challenge', true);
  }}
}});

sshVerifyBtn.addEventListener('click', async () => {{
  sshStatus.hidden = true;
  try {{
    if (!sshTicket || !sshChallenge.value.trim()) throw new Error('Сначала запроси challenge');
    if (!sshSignature.value.trim()) throw new Error('Вставь ASCII SSH signature');
    const res = await fetch('/api/login/ssh/finish', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{ticket: sshTicket, signature: sshSignature.value}})
    }});
    const data = await res.json().catch(() => ({{}}));
    if (!res.ok || !data.ok) throw new Error(data.error || 'Подпись не прошла проверку');
    window.location.href = data.redirect || '/';
  }} catch (err) {{
    setMethodStatus(sshStatus, err && err.message ? err.message : 'Подпись ED25519 не подошла', true);
  }}
}});

normalizeLoginUi();
loadLoginMeta();
</script>
</body>
</html>"""


def login_html(error=""):
    error_html = f"<div class=\"err\">{html.escape(error)}</div>" if error else ""
    auth = normalize_auth_state(load_auth())
    initial_login_meta_json = json.dumps(public_auth_meta(auth), ensure_ascii=False).replace("</", "<\\/")
    captcha_code, captcha_token = captcha_challenge()
    safe_captcha_token = html.escape(captcha_token, quote=True)
    safe_captcha_code = html.escape(captcha_code, quote=True)
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
{branding_head_tags()}
<title>OpenWrt Remote Hub</title>
<style>
:root{{color-scheme:dark;--bg:#07040f;--panel:rgba(19,14,32,.92);--panel-soft:rgba(255,255,255,.06);--text:#f7f2ff;--muted:#b9adc9;--line:rgba(169,126,255,.25);--line-strong:rgba(34,211,238,.56);--blue:#7c3aed;--cyan:#22d3ee;--teal:#a855f7;--red:#fb7185;--amber:#f59e0b;--shadow:0 34px 84px rgba(0,0,0,.5);--grid:rgba(168,85,247,.14);--login-copy-size:14px}}
*{{box-sizing:border-box}}
body{{position:relative;min-height:100vh;margin:0;overflow-x:hidden;background-color:var(--bg);background-image:radial-gradient(circle at 12% 8%,rgba(168,85,247,.46),transparent 31%),radial-gradient(circle at 82% 12%,rgba(79,70,229,.38),transparent 30%),radial-gradient(circle at 50% 105%,rgba(236,72,153,.26),transparent 35%),linear-gradient(145deg,#07040f,#120a24 48%,#05030a),repeating-linear-gradient(0deg,transparent 0 30px,var(--grid) 31px),repeating-linear-gradient(90deg,transparent 0 30px,var(--grid) 31px);background-size:130% 130%,140% 140%,135% 135%,100% 100%,31px 31px,31px 31px;background-attachment:fixed;color:var(--text);font:12px/1.3 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;display:grid;place-items:center;padding:24px;animation:bgFlow 28s ease-in-out infinite alternate}}
body::before{{content:"";position:fixed;inset:-25%;pointer-events:none;background:conic-gradient(from 0deg at 50% 50%,rgba(168,85,247,.05),rgba(236,72,153,.34),rgba(59,130,246,.22),rgba(245,158,11,.13),rgba(168,85,247,.05));filter:blur(54px);opacity:.7;animation:auraSpin 38s linear infinite}}
body::after{{content:"";position:fixed;inset:0;pointer-events:none;background:linear-gradient(180deg,rgba(255,255,255,.03),rgba(255,255,255,0));opacity:.4}}
@keyframes bgFlow{{0%{{background-position:0% 0%,100% 0%,50% 100%,0 0,0 0,0 0}}50%{{background-position:28% 18%,62% 26%,38% 82%,0 0,15px 24px,24px 15px}}100%{{background-position:48% 28%,42% 42%,74% 62%,0 0,30px 0,0 30px}}}}
@keyframes auraSpin{{from{{transform:rotate(0deg) scale(1)}}to{{transform:rotate(360deg) scale(1.08)}}}}
.loginShell{{position:relative;z-index:1;width:min(520px,calc(100vw - 24px))}}
.loginFrame{{position:relative;border:1px solid var(--line);border-radius:24px;overflow:hidden;background:linear-gradient(180deg,rgba(255,255,255,.09),rgba(255,255,255,.04)),var(--panel);box-shadow:var(--shadow);backdrop-filter:blur(18px)}}
.loginMain{{display:grid;grid-template-columns:1fr}}
.brandPanel{{position:relative;display:grid;gap:4px;padding:24px 22px 14px;background:linear-gradient(180deg,rgba(17,12,29,.82),rgba(17,12,29,0))}}
.brandPanel::before{{content:"";position:absolute;inset:0;background:radial-gradient(circle at 50% 16%,rgba(34,211,238,.12),transparent 20%),radial-gradient(circle at 24% 18%,rgba(255,255,255,.04),transparent 0 2px,transparent 3px),radial-gradient(circle at 72% 22%,rgba(168,85,247,.09),transparent 0 2px,transparent 3px);opacity:.8}}
.brandPanel::after{{content:"";position:absolute;left:22px;right:22px;bottom:0;height:1px;background:linear-gradient(90deg,transparent,rgba(169,126,255,.18),transparent)}}
.brandTop,.brandBottom{{position:relative;z-index:1}}
.brandTop{{display:grid;gap:0}}
.brandBottom{{display:none}}
.brandHero{{display:grid;grid-template-columns:1fr;grid-template-areas:"seal" "title";justify-items:center;align-items:center;row-gap:12px;text-align:center}}
.brandSeal{{grid-area:seal;position:relative;display:grid;place-items:center;width:78px;height:78px;margin-bottom:0;border-radius:50%;border:2px solid rgba(34,211,238,.54);background:radial-gradient(circle at 50% 35%,rgba(43,31,68,.98),rgba(12,8,22,.96));box-shadow:0 0 0 4px rgba(168,85,247,.08),0 0 20px rgba(34,211,238,.10)}}
.brandSeal::before{{content:"";position:absolute;inset:12px;border-radius:50%;border:1px solid rgba(34,211,238,.20)}}
.brandSeal svg{{width:36px;height:36px;filter:drop-shadow(0 0 8px rgba(34,211,238,.18))}}
.brandTitle{{grid-area:title;margin:0;display:flex;flex-direction:column;align-items:center;gap:4px;font-size:clamp(22px,4vw,28px);line-height:1.04;font-weight:800;letter-spacing:-.04em}}
.brandVersion{{color:var(--red);font-size:14px;font-weight:800;letter-spacing:0;text-shadow:0 0 12px rgba(251,113,133,.35)}}
.globeWrap{{display:none}}
.globe{{display:none}}
.globe::before{{display:none}}
.globe::after{{display:none}}
.orbitLine{{display:none}}
.authPanel{{padding:0 22px 18px;background:none;overflow:visible}}
.authStack{{display:grid;gap:12px;max-width:none;margin:0}}
.authHead{{display:none}}
.authMark{{display:grid;place-items:center;width:44px;height:44px;border-radius:13px;border:1px solid rgba(169,126,255,.24);background:linear-gradient(180deg,rgba(124,58,237,.14),rgba(15,10,26,.82));box-shadow:inset 0 1px 0 rgba(255,255,255,.05)}}
.authMark svg{{width:22px;height:22px;stroke:#93c5fd}}
.authHead h1{{margin:0;font-size:clamp(18px,4vw,22px);line-height:1.05;font-weight:800;letter-spacing:-.03em}}
.authHead p{{display:none}}
.authTabs{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0;width:min(100%,430px);justify-self:center;border:1px solid var(--line);border-radius:14px;background:rgba(255,255,255,.04);overflow:hidden;box-shadow:inset 0 1px 0 rgba(255,255,255,.04)}}
.authTabBtn{{display:inline-flex;align-items:center;justify-content:center;gap:10px;min-height:48px;padding:0 12px;border:0;border-right:1px solid rgba(169,126,255,.14);border-radius:0;background:transparent;color:#c4b5fd;font-size:12px;font-weight:800;letter-spacing:.01em;cursor:pointer;transition:background .2s ease,color .2s ease}}
.authTabBtn:last-child{{border-right:0}}
.authTabBtn svg{{width:18px;height:18px;stroke:currentColor;flex:0 0 auto}}
.authTabBtn[aria-selected="true"]{{background:linear-gradient(110deg,rgba(34,211,238,.16),rgba(124,58,237,.30),rgba(236,72,153,.12));color:#fff;box-shadow:inset 0 1px 0 rgba(255,255,255,.10)}}
.authTabBtn:hover{{color:#fff}}
.authTabsBody{{display:grid}}
.authTabPanel{{display:none;gap:10px}}
.authTabPanel.is-active{{display:grid}}
.authTabPanel[hidden]{{display:none !important}}
.err{{width:min(100%,430px);justify-self:center;padding:10px 12px;border:1px solid rgba(251,113,133,.38);border-radius:14px;background:rgba(127,29,29,.2);color:#fecdd3;font-size:12px;font-weight:700}}
.login{{display:grid;gap:14px}}
.fieldLabel{{display:none}}
.fieldGroup{{display:grid;gap:12px;justify-items:center}}
.compactField{{width:min(100%,430px);justify-self:center}}
.inputShell{{display:grid;grid-template-columns:46px minmax(0,1fr) auto;align-items:center;min-height:48px;border:1px solid var(--line);border-radius:14px;background:linear-gradient(180deg,rgba(52,38,74,.96),rgba(34,26,52,.96));box-shadow:inset 0 1px 0 rgba(255,255,255,.04);transition:border-color .2s ease,box-shadow .2s ease,transform .2s ease}}
.inputShell:focus-within{{border-color:var(--line-strong);box-shadow:0 0 0 3px rgba(34,211,238,.12),inset 0 1px 0 rgba(255,255,255,.05);transform:translateY(-1px)}}
.fieldIcon{{display:grid;place-items:center;color:#c4b5fd}}
.fieldIcon svg{{width:18px;height:18px;stroke:currentColor}}
.inputShell input{{width:100%;min-width:0;padding:0 12px 0 0;border:0;background:transparent;color:var(--text);font:500 14px/1.15 inherit;outline:none}}
.inputShell input::placeholder,.methodRow textarea::placeholder{{color:#b9adc9}}
.inputAction{{display:grid;place-items:center;width:46px;height:100%;border:0;background:transparent;color:#c4b5fd;cursor:pointer}}
.inputAction svg{{width:18px;height:18px;stroke:currentColor}}
.otpCard{{display:grid;gap:4px;width:min(100%,430px);justify-self:center;padding:0;border:0;border-radius:0;background:none}}
.hint{{width:min(100%,430px);justify-self:center;margin:0;color:#b9adc9;font-size:var(--login-copy-size);line-height:1.35}}
#passwordModeHint{{text-align:center}}
.captchaSection{{display:grid;grid-template-columns:1fr;gap:10px;width:min(100%,430px);justify-self:center;padding:14px 12px 12px;border:1px solid var(--line);border-radius:16px;background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.035)),rgba(19,14,32,.88)}}
.captchaHeading{{color:#eef3ff;font-size:var(--login-copy-size);font-weight:800;line-height:1.2;text-align:center}}
.captchaTopRow{{display:grid;grid-template-columns:34px 1fr;gap:12px;align-items:center}}
.captcha{{display:flex;align-items:center;justify-content:center;min-height:36px;padding:0;background:none;overflow:visible;position:relative}}
.captcha::before{{display:none}}
.captcha b{{position:relative;z-index:1;color:var(--amber);font:800 24px/1 ui-monospace,SFMono-Regular,Consolas,monospace;letter-spacing:.28em;text-indent:.28em;text-shadow:0 0 12px rgba(245,158,11,.28)}}
.captchaRefresh{{width:34px;height:34px;border:0;border-radius:10px;background:transparent;color:#c4b5fd;cursor:pointer;box-shadow:none}}
.captchaRefresh svg{{width:20px;height:20px;stroke:currentColor}}
.captchaAnswerShell{{grid-template-columns:minmax(0,1fr) 42px;min-height:42px}}
.captchaAnswerShell input{{padding:0 12px}}
.captchaInputMark{{display:grid;place-items:center;color:#8e98ad}}
.captchaInputMark svg{{width:18px;height:18px;stroke:currentColor}}
.primaryBtn{{display:inline-flex;align-items:center;justify-content:center;gap:10px;width:min(100%,430px);justify-self:center;min-height:50px;padding:0 14px;border:1px solid rgba(34,211,238,.30);border-radius:14px;background:linear-gradient(110deg,rgba(34,211,238,.20),rgba(124,58,237,.88),rgba(236,72,153,.20));color:#fff;font-size:14px;font-weight:800;letter-spacing:.01em;cursor:pointer;box-shadow:0 12px 24px rgba(124,58,237,.22);transition:transform .2s ease,box-shadow .2s ease,filter .2s ease}}
.primaryBtn svg{{width:18px;height:18px;stroke:currentColor}}
.primaryBtn:hover,.methodBtn:hover,.manualGuideLink:hover,.captchaRefresh:hover{{filter:brightness(1.06)}}
.primaryBtn:active,.methodBtn:active,.captchaRefresh:active{{transform:translateY(1px)}}
.quickAuthPanel{{display:grid;gap:9px}}
.quickAuthHeader{{display:grid;gap:4px}}
.quickAuthHeader h2{{margin:0;font-size:var(--login-copy-size);line-height:1.2}}
.quickAuthHeader p{{margin:0;color:var(--muted);font-size:var(--login-copy-size);line-height:1.35}}
.reserveNote{{display:grid;gap:4px;padding:8px 9px;border:1px solid rgba(169,126,255,.14);border-radius:10px;background:rgba(17,15,30,.54)}}
.reserveNote b{{font-size:var(--login-copy-size);line-height:1.25}}
.divider{{display:grid;grid-template-columns:1fr auto 1fr;gap:6px;align-items:center;color:#8e88aa;font-size:var(--login-copy-size);font-weight:700;letter-spacing:.08em;text-transform:uppercase}}
.divider::before,.divider::after{{content:"";height:1px;background:linear-gradient(90deg,transparent,rgba(169,126,255,.18),transparent)}}
.methodGrid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}}
.methodCard{{display:grid;gap:8px;padding:9px;border:1px solid rgba(169,126,255,.16);border-radius:11px;background:linear-gradient(180deg,rgba(255,255,255,.07),rgba(255,255,255,.03)),rgba(19,14,32,.86);box-shadow:inset 0 1px 0 rgba(255,255,255,.04)}}
.socialLoginSection{{display:grid;gap:8px}}
.socialLoginSection[hidden]{{display:none !important}}
.socialMethodGrid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}}
.socialMethodGrid[hidden]{{display:none !important}}
.socialMethodCard[hidden]{{display:none !important}}
.socialMethodEmpty{{padding:10px 12px;border:1px dashed rgba(169,126,255,.2);border-radius:11px;background:rgba(14,12,24,.5);color:#b5b0cd;font-size:var(--login-copy-size);line-height:1.35}}
.socialMethodEmpty[hidden]{{display:none !important}}
.methodCardTop{{display:flex;align-items:center;justify-content:space-between;gap:6px}}
.methodMark{{display:grid;place-items:center;width:30px;height:30px;border-radius:9px;border:1px solid rgba(34,211,238,.24);background:rgba(34,211,238,.08);color:#dbeafe}}
.methodMark svg{{width:14px;height:14px;stroke:currentColor}}
.methodMeta{{display:inline-flex;align-items:center;min-height:18px;padding:3px 6px;border:1px solid rgba(34,211,238,.24);border-radius:999px;background:rgba(34,211,238,.08);color:#dbeafe;font-size:var(--login-copy-size);font-weight:700;letter-spacing:.04em;text-transform:uppercase}}
.methodCard h2{{margin:0;font-size:var(--login-copy-size);line-height:1.2}}
.methodCard p{{margin:0;color:var(--muted);font-size:var(--login-copy-size);line-height:1.35}}
.methodBtn{{display:inline-flex;align-items:center;justify-content:center;gap:5px;min-height:32px;padding:0 9px;border:1px solid rgba(34,211,238,.22);border-radius:9px;background:linear-gradient(180deg,rgba(255,255,255,.06),rgba(255,255,255,.02)),rgba(17,15,30,.92);color:#edf3ff;font-size:var(--login-copy-size);font-weight:700;cursor:pointer}}
.methodBtn svg{{width:13px;height:13px;stroke:currentColor}}
.methodBtnRow{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:5px}}
.methodBtnRow .methodBtn:only-child{{grid-column:1/-1}}
.methodDetails{{border:1px solid rgba(169,126,255,.14);border-radius:9px;background:rgba(17,15,30,.44);overflow:hidden}}
.methodDetails summary{{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:8px 9px;cursor:pointer;color:#ede9fe;font-size:var(--login-copy-size);font-weight:700;list-style:none}}
.methodDetails summary::-webkit-details-marker{{display:none}}
.methodDetails summary::after{{content:"+";font-size:14px;line-height:1;color:#c4b5fd;transition:transform .2s ease}}
.methodDetails[open] summary::after{{transform:rotate(45deg)}}
.methodDetailsBody{{padding:0 8px 8px}}
.methodRow{{display:grid;grid-template-columns:1fr;gap:5px}}
.methodRow .wide{{grid-column:1/-1}}
.methodRow textarea{{width:100%;min-height:64px;padding:8px 9px;border:1px solid var(--line);border-radius:9px;background:linear-gradient(180deg,rgba(52,38,74,.96),rgba(34,26,52,.96));color:#e8eef9;outline:none;resize:vertical;font:10px/1.35 ui-monospace,SFMono-Regular,Consolas,monospace}}
.methodRow textarea:focus{{border-color:var(--line-strong);box-shadow:0 0 0 3px rgba(34,211,238,.1)}}
.manualGuideLink{{display:inline-flex;align-items:center;justify-content:center;min-height:32px;padding:0 9px;border:1px solid rgba(34,211,238,.24);border-radius:9px;background:rgba(34,211,238,.08);color:#dbeafe;text-decoration:none;font-size:var(--login-copy-size);font-weight:700}}
.sshHint{{margin-top:0;padding:10px 12px;border:1px dashed rgba(34,211,238,.22);border-radius:10px;background:rgba(34,211,238,.07);color:#dbeafe}}
.methodStatus{{padding:8px 9px;border:1px solid rgba(34,197,94,.28);border-radius:9px;background:rgba(20,83,45,.18);color:#bbf7d0;font-size:var(--login-copy-size);font-weight:700;line-height:1.35}}
.methodStatus.bad{{border-color:rgba(251,113,133,.38);background:rgba(127,29,29,.22);color:#fecdd3}}
.authFooter{{display:grid;gap:6px;padding-top:1px}}
.altAuthSummary{{padding:8px 9px;border:1px solid rgba(169,126,255,.14);border-radius:10px;background:rgba(17,15,30,.58);color:#c4b5fd;font-size:var(--login-copy-size);line-height:1.35}}
.securityNote{{display:flex;align-items:center;justify-content:center;gap:8px;color:#b9adc9;font-size:var(--login-copy-size)}}
.securityNote svg{{width:14px;height:14px;stroke:#b9adc9;flex:0 0 auto}}
.securityNote span{{font-size:0}}
.securityNote span::before{{content:"Безопасное соединение";font-size:var(--login-copy-size)}}
@media(max-width:760px){{body{{padding:10px;background-attachment:scroll;background-size:auto,auto,auto,100% 100%,31px 31px,31px 31px}}.loginShell{{width:min(100%,calc(100vw - 20px))}}.loginFrame{{border-radius:20px}}.brandPanel{{padding:20px 14px 12px}}.brandPanel::after{{left:14px;right:14px}}.brandSeal{{width:64px;height:64px}}.brandSeal::before{{inset:10px}}.brandSeal svg{{width:30px;height:30px}}.brandTitle{{font-size:clamp(19px,7vw,24px);gap:4px}}.brandVersion{{font-size:12px}}.authPanel{{padding:0 14px 14px}}.authTabs{{grid-template-columns:1fr;width:100%}}.authTabBtn{{min-height:44px;font-size:12px}}.authTabBtn svg{{width:16px;height:16px}}.fieldGroup{{justify-items:stretch}}.compactField,.otpCard,.hint,.captchaSection,.primaryBtn,.err{{width:100%;justify-self:stretch}}.inputShell{{grid-template-columns:40px minmax(0,1fr) auto;min-height:44px}}.fieldIcon svg{{width:16px;height:16px}}.inputShell input{{font-size:13px}}.inputAction{{width:40px}}.captchaSection{{padding:12px 10px 10px;gap:9px}}.captchaTopRow{{grid-template-columns:30px 1fr;gap:8px}}.captchaRefresh{{width:30px;height:30px}}.captchaRefresh svg{{width:18px;height:18px}}.captcha b{{font-size:21px}}.captchaAnswerShell{{grid-template-columns:minmax(0,1fr) 38px;min-height:38px}}.primaryBtn{{min-height:46px;font-size:13px}}.methodGrid,.socialMethodGrid{{grid-template-columns:1fr}}.methodBtnRow{{grid-template-columns:1fr}}.sshMethodCard{{display:none!important}}.authFooter{{display:none}}}}
</style>
</head>
<body>
<main class="loginShell">
  <section class="loginFrame">
    <div class="loginMain">
      <aside class="brandPanel">
        <div class="brandTop">
          <div class="brandHero">
            <div class="brandSeal" aria-hidden="true">
              <svg viewBox="0 0 96 96" fill="none">
                <rect x="20" y="48" width="56" height="22" rx="7" stroke="#E5F2FF" stroke-width="3"/>
                <path d="M48 28v20" stroke="#E5F2FF" stroke-width="4" stroke-linecap="round"/>
                <path d="M48 24a4 4 0 1 0 0 8a4 4 0 0 0 0-8Z" stroke="#E5F2FF" stroke-width="3"/>
                <path d="M31 38c4-6 10-10 17-10s13 4 17 10" stroke="#3B82F6" stroke-width="4" stroke-linecap="round"/>
                <path d="M25 30c6-8 14-13 23-13s17 5 23 13" stroke="#3B82F6" stroke-width="4" stroke-linecap="round"/>
                <path d="M16 21c9-11 20-17 32-17s23 6 32 17" stroke="#3B82F6" stroke-width="4" stroke-linecap="round"/>
                <circle cx="31" cy="59" r="3" fill="#E5F2FF"/>
                <circle cx="48" cy="59" r="3" fill="#E5F2FF"/>
                <circle cx="65" cy="59" r="3" fill="#E5F2FF"/>
              </svg>
            </div>
            <h2 class="brandTitle">OpenWrt Remote Hub <span class="brandVersion">v106</span></h2>
          </div>
        </div>
        <div class="brandBottom">
          <div class="globeWrap" aria-hidden="true">
            <div class="orbitLine"></div>
            <div class="globe"></div>
          </div>
        </div>
      </aside>
      <section class="authPanel">
        <div class="authStack">
          <header class="authHead">
            <div class="authMark" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none">
                <path d="M7 10V7a5 5 0 0 1 10 0v3" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
                <rect x="4" y="10" width="16" height="10" rx="3" stroke-width="1.8"/>
                <path d="M12 14v2.5" stroke-width="1.8" stroke-linecap="round"/>
              </svg>
            </div>
            <div>
              <h1>Вход в систему</h1>
              <p>Авторизуйся для доступа к панели и защищённым сценариям управления.</p>
            </div>
          </header>
          {error_html}
          <div class="authTabs" role="tablist" aria-label="Сценарии входа">
            <button class="authTabBtn" type="button" role="tab" aria-selected="true" aria-controls="passwordLoginPanel" id="passwordLoginTab" data-auth-tab="password">
              <svg viewBox="0 0 24 24" fill="none">
                <path d="M7 10V7a5 5 0 0 1 10 0v3" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
                <rect x="4" y="10" width="16" height="10" rx="3" stroke-width="1.8"/>
                <path d="M12 14v2.5" stroke-width="1.8" stroke-linecap="round"/>
              </svg>
              <span>Вход в систему</span>
            </button>
            <button class="authTabBtn" type="button" role="tab" aria-selected="false" aria-controls="quickLoginPanel" id="quickLoginTab" data-auth-tab="quick">
              <svg viewBox="0 0 24 24" fill="none">
                <path d="M16 21v-2a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v2" stroke-width="1.8" stroke-linecap="round"/>
                <circle cx="9.5" cy="7" r="3.5" stroke-width="1.8"/>
                <path d="M20 8v6M17 11h6" stroke-width="1.8" stroke-linecap="round"/>
              </svg>
              <span>Быстрый вход</span>
            </button>
          </div>
          <div class="authTabsBody">
            <section class="authTabPanel is-active" id="passwordLoginPanel" role="tabpanel" aria-labelledby="passwordLoginTab" data-auth-panel="password">
              <form class="login" method="post" action="/login">
                <div class="fieldGroup">
                  <div class="compactField">
                    <label class="fieldLabel" for="hubUsername">Логин</label>
                    <div class="inputShell">
                      <span class="fieldIcon" aria-hidden="true">
                        <svg viewBox="0 0 24 24" fill="none">
                          <path d="M12 12a4 4 0 1 0 0-8a4 4 0 0 0 0 8Z" stroke-width="1.7"/>
                          <path d="M4 20a8 8 0 0 1 16 0" stroke-width="1.7" stroke-linecap="round"/>
                        </svg>
                      </span>
                      <input id="hubUsername" name="username" autocomplete="off" placeholder="Введите логин" autofocus required>
                    </div>
                  </div>
                  <div class="compactField">
                    <label class="fieldLabel" for="hubPassword">Пароль</label>
                    <div class="inputShell">
                      <span class="fieldIcon" aria-hidden="true">
                        <svg viewBox="0 0 24 24" fill="none">
                          <path d="M7 10V7a5 5 0 0 1 10 0v3" stroke-width="1.7" stroke-linecap="round"/>
                          <rect x="4" y="10" width="16" height="10" rx="3" stroke-width="1.7"/>
                        </svg>
                      </span>
                      <input id="hubPassword" name="password" type="password" autocomplete="current-password" placeholder="Введите пароль" required>
                      <button class="inputAction" id="passwordToggleBtn" type="button" aria-label="Показать или скрыть пароль">
                        <svg viewBox="0 0 24 24" fill="none">
                          <path d="M2 12s3.5-6 10-6s10 6 10 6s-3.5 6-10 6S2 12 2 12Z" stroke-width="1.7" stroke-linejoin="round"/>
                          <circle cx="12" cy="12" r="3" stroke-width="1.7"/>
                        </svg>
                      </button>
                    </div>
                  </div>
                  <div class="otpCard compactField">
                    <div>
                      <label class="fieldLabel" for="hubOtp">Код 2FA</label>
                      <div class="inputShell">
                        <span class="fieldIcon" aria-hidden="true">
                          <svg viewBox="0 0 24 24" fill="none">
                            <path d="M12 3l7 3v5c0 4.4-2.8 8.4-7 9.8C7.8 19.4 5 15.4 5 11V6l7-3Z" stroke-width="1.7" stroke-linejoin="round"/>
                            <path d="M9.5 11.5l1.6 1.6l3.4-3.6" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>
                          </svg>
                        </span>
                        <input id="hubOtp" name="otp" inputmode="numeric" pattern="[0-9]*" autocomplete="one-time-code" placeholder="Введите код из приложения 2FA">
                      </div>
                    </div>
                    <div class="hint" id="passwordModeHint" hidden>Резервный вход через пароль. Когда 2FA включена, сюда нужен TOTP-код.</div>
                  </div>
                  <div class="captchaSection">
                    <div class="captchaHeading">Капча: введи эти цифры</div>
                    <div class="captchaTopRow">
                      <button class="captchaRefresh" id="captchaRefreshBtn" type="button" aria-label="Обновить капчу">
                        <svg viewBox="0 0 24 24" fill="none">
                          <path d="M20 6v5h-5" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
                          <path d="M4 18v-5h5" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
                          <path d="M7.5 9A7 7 0 0 1 19 11" stroke-width="1.8" stroke-linecap="round"/>
                          <path d="M16.5 15A7 7 0 0 1 5 13" stroke-width="1.8" stroke-linecap="round"/>
                        </svg>
                      </button>
                      <div class="captcha" id="hubCaptchaCode" aria-live="polite"><b>{safe_captcha_code}</b></div>
                    </div>
                    <div class="inputShell captchaAnswerShell">
                      <input id="hubCaptcha" name="captcha_answer" inputmode="numeric" pattern="[0-9]*" autocomplete="off" placeholder="Повтори капчу" required>
                      <span class="captchaInputMark" aria-hidden="true">
                        <svg viewBox="0 0 24 24" fill="none">
                          <rect x="3" y="5" width="18" height="14" rx="3" stroke-width="1.7"/>
                          <path d="M7 9h2M11 9h2M15 9h2M7 13h10" stroke-width="1.7" stroke-linecap="round"/>
                        </svg>
                      </span>
                    </div>
                  </div>
                  <input name="captcha_token" type="hidden" value="{safe_captcha_token}">
                  <button class="primaryBtn">
                    <svg viewBox="0 0 24 24" fill="none">
                      <path d="M7 10V7a5 5 0 0 1 10 0v3" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
                      <rect x="4" y="10" width="16" height="10" rx="3" stroke-width="1.8"/>
                      <path d="M12 14v2.5" stroke-width="1.8" stroke-linecap="round"/>
                    </svg>
                    Войти
                  </button>
                </div>
              </form>
            </section>
            <section class="authTabPanel" id="quickLoginPanel" role="tabpanel" aria-labelledby="quickLoginTab" data-auth-panel="quick" hidden>
              <div class="quickAuthPanel">
                <div class="divider">или</div>
                <section class="methodGrid">
                  <section class="methodCard" id="passkeyMethodCard">
                    <div class="methodCardTop">
                      <div class="methodMark" aria-hidden="true">
                        <svg viewBox="0 0 24 24" fill="none">
                          <path d="M12 2l7 4v5c0 4.6-3 8.7-7 10c-4-1.3-7-5.4-7-10V6l7-4Z" stroke-width="1.7" stroke-linejoin="round"/>
                          <path d="M8.5 12.5l2.3 2.3l4.7-4.8" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>
                        </svg>
                      </div>
                      <span class="methodMeta">Passkey</span>
                    </div>
                    <div>
                      <h2>Системный ключ</h2>
                      <p>Windows Hello, Touch ID, Android и другие системные ключи могут входить без пароля.</p>
                    </div>
                    <button class="methodBtn" id="passkeyLoginBtn" type="button">
                      <svg viewBox="0 0 24 24" fill="none">
                        <path d="M7 10V7a5 5 0 0 1 10 0v3" stroke-width="1.7" stroke-linecap="round"/>
                        <rect x="4" y="10" width="16" height="10" rx="3" stroke-width="1.7"/>
                        <path d="M12 14v2.5" stroke-width="1.7" stroke-linecap="round"/>
                      </svg>
                      Войти через Passkey
                    </button>
                    <div class="hint" id="passkeyHint">Сначала зарегистрируй passkey в меню доступа внутри панели.</div>
                    <div class="methodStatus" id="passkeyStatus" hidden></div>
                  </section>
                  <section class="methodCard sshMethodCard" id="sshMethodCard">
                    <div class="methodCardTop">
                      <div class="methodMark" aria-hidden="true">
                        <svg viewBox="0 0 24 24" fill="none">
                          <path d="M14.5 4.5a4.95 4.95 0 0 1 0 7l-6 6a4.95 4.95 0 1 1-7-7l2-2" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>
                          <path d="M9.5 19.5a4.95 4.95 0 0 1 0-7l6-6a4.95 4.95 0 1 1 7 7l-2 2" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>
                        </svg>
                      </div>
                      <span class="methodMeta">ED25519</span>
                    </div>
                    <div class="sshMethodIntro">
                      <h2>SSH-подпись</h2>
                      <p>Challenge + подпись приватным ключом ED25519 без отправки самого приватного ключа.</p>
                    </div>
                    <div class="methodBtnRow">
                      <button class="methodBtn" id="sshChallengeBtn" type="button">Получить challenge</button>
                      <button class="methodBtn" id="sshVerifyBtn" type="button">Проверить подпись</button>
                    </div>
                    <details class="methodDetails" id="sshDetails">
                      <summary>Ручная подпись challenge</summary>
                      <div class="methodDetailsBody">
                        <div class="methodRow">
                          <textarea class="wide" id="sshChallenge" readonly placeholder="Здесь появится challenge для подписи"></textarea>
                          <textarea class="wide" id="sshSignature" placeholder="Вставь блок -----BEGIN SSH SIGNATURE----- ... -----END SSH SIGNATURE-----"></textarea>
                        </div>
                      </div>
                    </details>
                    <a class="manualGuideLink sshManualGuideLink" href="/ssh-key-manual/" target="_blank" rel="noopener noreferrer">Как создать SSH key</a>
                    <div class="methodStatus" id="sshStatus" hidden></div>
                  </section>
                </section>
                <section class="socialLoginSection" id="socialLoginSection" hidden>
                  <div class="divider">только привязанные</div>
                  <section class="socialMethodGrid" id="socialMethodGrid">
                    <section class="methodCard socialMethodCard" id="githubLoginCard" hidden>
                      <div class="methodCardTop">
                        <div class="methodMark" aria-hidden="true">
                          <svg viewBox="0 0 24 24" fill="none">
                            <path d="M12 3.2a8.8 8.8 0 0 0-2.8 17.1c.4.1.6-.2.6-.5v-1.8c-2.5.5-3-1-3-1c-.4-1-.9-1.3-.9-1.3c-.8-.5 0-.5 0-.5c.8.1 1.3.8 1.3.8c.8 1.3 2 1 2.5.8c.1-.6.3-1 .5-1.3c-2-.2-4.1-1-4.1-4.4c0-1 .3-1.9.8-2.5c-.1-.2-.4-1.1.1-2.4c0 0 .7-.2 2.6 1a9 9 0 0 1 4.8 0c1.8-1.2 2.6-1 2.6-1c.5 1.3.2 2.2.1 2.4c.5.6.8 1.5.8 2.5c0 3.4-2.1 4.2-4.1 4.4c.3.3.6.8.6 1.7v2.5c0 .3.2.6.6.5A8.8 8.8 0 0 0 12 3.2Z" stroke-width="1.4" stroke-linejoin="round"/>
                          </svg>
                        </div>
                        <span class="methodMeta">GitHub</span>
                      </div>
                      <div>
                        <h2>GitHub аккаунт</h2>
                        <p>Появляется только после привязки аккаунта в меню доступа Hub.</p>
                      </div>
                      <button class="methodBtn" id="githubLoginBtn" type="button">Войти через GitHub</button>
                      <div class="hint" id="githubLoginHint">Пока нет привязанного GitHub-аккаунта.</div>
                    </section>
                    <section class="methodCard socialMethodCard" id="vkLoginCard" hidden>
                      <div class="methodCardTop">
                        <div class="methodMark" aria-hidden="true">
                          <svg viewBox="0 0 24 24" fill="none">
                            <path d="M5 7.5c.2 4 2.2 8.3 6.2 8.3h.4V13c1.6 0 2.3.5 3.2 1.6l1 1.2h2.8l-1.8-2.5c-.5-.7-1.3-1.4-2-1.7c0 0 1.8-.9 2.8-3.1h-2.5c-1 2-2.6 2.1-2.6 2.1V7.5h-2.4v5.5c0 .5-.1.6-.5.6c-1 0-3.3-1.9-3.8-6.1H5Z" stroke-width="1.4" stroke-linejoin="round"/>
                          </svg>
                        </div>
                        <span class="methodMeta">VK ID</span>
                      </div>
                      <div>
                        <h2>VK ID аккаунт</h2>
                        <p>Кнопка видна только для реально привязанных VK ID.</p>
                      </div>
                      <button class="methodBtn" id="vkLoginBtn" type="button">Войти через VK ID</button>
                      <div class="hint" id="vkLoginHint">Пока нет привязанного VK ID.</div>
                    </section>
                  </section>
                  <div class="socialMethodEmpty" id="socialMethodEmpty" hidden></div>
                </section>
                <footer class="authFooter">
                  <div class="altAuthSummary" id="altAuthSummary" hidden>Проверяю доступные методы входа...</div>
                </footer>
              </div>
            </section>
          </div>
          <div class="securityNote">
            <svg viewBox="0 0 24 24" fill="none">
              <path d="M12 3l7 3v5c0 4.4-2.8 8.4-7 9.8C7.8 19.4 5 15.4 5 11V6l7-3Z" stroke-width="1.7" stroke-linejoin="round"/>
              <path d="M9.5 11.5l1.6 1.6l3.4-3.6" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            <span>Безопасное соединение. Данные и альтернативные методы авторизации защищены.</span>
          </div>
        </div>
      </section>
    </div>
  </section>
</main>
<script>
const hubUsername = document.getElementById('hubUsername');
const hubPassword = document.getElementById('hubPassword');
const hubOtp = document.getElementById('hubOtp');
const authTabButtons = Array.from(document.querySelectorAll('[data-auth-tab]'));
const authTabPanels = Array.from(document.querySelectorAll('[data-auth-panel]'));
const passwordModeHint = document.getElementById('passwordModeHint');
const passwordToggleBtn = document.getElementById('passwordToggleBtn');
const captchaRefreshBtn = document.getElementById('captchaRefreshBtn');
const passkeyLoginBtn = document.getElementById('passkeyLoginBtn');
const passkeyHint = document.getElementById('passkeyHint');
const passkeyStatus = document.getElementById('passkeyStatus');
const sshChallengeBtn = document.getElementById('sshChallengeBtn');
const sshVerifyBtn = document.getElementById('sshVerifyBtn');
const sshDetails = document.getElementById('sshDetails');
const sshChallenge = document.getElementById('sshChallenge');
const sshSignature = document.getElementById('sshSignature');
const sshHint = document.getElementById('sshHint');
const sshStatus = document.getElementById('sshStatus');
const altAuthSummary = document.getElementById('altAuthSummary');
const socialLoginSection = document.getElementById('socialLoginSection');
const socialMethodGrid = document.getElementById('socialMethodGrid');
const socialMethodEmpty = document.getElementById('socialMethodEmpty');
const socialLoginUi = {{
  github: {{
    label: 'GitHub',
    card: document.getElementById('githubLoginCard'),
    button: document.getElementById('githubLoginBtn'),
    hint: document.getElementById('githubLoginHint')
  }},
  vk: {{
    label: 'VK ID',
    card: document.getElementById('vkLoginCard'),
    button: document.getElementById('vkLoginBtn'),
    hint: document.getElementById('vkLoginHint')
  }}
}};
let loginAuthMeta = {initial_login_meta_json};
let sshTicket = '';

function setActiveAuthTab(name) {{
  const activeTab = name === 'quick' ? 'quick' : 'password';
  authTabButtons.forEach((button) => {{
    const selected = button.dataset.authTab === activeTab;
    button.setAttribute('aria-selected', selected ? 'true' : 'false');
    button.tabIndex = selected ? 0 : -1;
  }});
  authTabPanels.forEach((panel) => {{
    const visible = panel.dataset.authPanel === activeTab;
    panel.classList.toggle('is-active', visible);
    panel.hidden = !visible;
  }});
}}

function setMethodStatus(node, text, bad = false) {{
  if (!node) return;
  node.hidden = false;
  node.className = 'methodStatus' + (bad ? ' bad' : '');
  node.textContent = text;
}}

function b64urlToBytes(value) {{
  const normalized = String(value || '').replace(/-/g, '+').replace(/_/g, '/');
  const padded = normalized + '='.repeat((4 - normalized.length % 4) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}}

function bytesToB64url(buffer) {{
  const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer || []);
  let binary = '';
  for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
  return btoa(binary).replace(/\\+/g, '-').replace(/\\//g, '_').replace(/=+$/g, '');
}}

function decodeRequestOptions(options) {{
  const publicKey = Object.assign({{}}, options.publicKey || {{}});
  publicKey.challenge = b64urlToBytes(publicKey.challenge || '');
  publicKey.allowCredentials = Array.isArray(publicKey.allowCredentials)
    ? publicKey.allowCredentials.map((item) => Object.assign({{}}, item, {{id: b64urlToBytes(item.id || '')}}))
    : [];
  return {{publicKey}};
}}

function encodeAssertion(assertion) {{
  return {{
    id: assertion.id,
    rawId: bytesToB64url(assertion.rawId),
    type: assertion.type,
    authenticatorAttachment: assertion.authenticatorAttachment || null,
    clientExtensionResults: assertion.getClientExtensionResults ? assertion.getClientExtensionResults() : {{}},
    response: {{
      clientDataJSON: bytesToB64url(assertion.response.clientDataJSON),
      authenticatorData: bytesToB64url(assertion.response.authenticatorData),
      signature: bytesToB64url(assertion.response.signature),
      userHandle: assertion.response.userHandle ? bytesToB64url(assertion.response.userHandle) : null
    }}
  }};
}}

function isCompactSshLogin() {{
  return window.matchMedia ? window.matchMedia('(max-width: 760px)').matches : window.innerWidth <= 760;
}}

function updateSshHintText(sshCount) {{
  if (!sshHint) return;
  if (!sshCount) {{
    sshHint.textContent = 'Сначала добавь SSH ED25519 ключ в меню доступа внутри панели.';
    return;
  }}
  sshHint.textContent = isCompactSshLogin()
    ? 'SSH ED25519 вход доступен на полном экране. На телефоне здесь оставлена только подсказка.'
    : 'Нажми Получить challenge, затем вставь подпись и нажми Проверить подпись.';
}}

function updateLoginMeta() {{
  if (hubOtp) hubOtp.required = !!loginAuthMeta.totp_enabled;
  if (passwordModeHint) {{
    passwordModeHint.hidden = !loginAuthMeta.totp_enabled;
    if (loginAuthMeta.totp_enabled) {{
      passwordModeHint.textContent = '2FA включена: для входа по паролю обязателен TOTP-код.';
    }}
  }}
  const invalidPasskeyCount = Number(loginAuthMeta.passkey_invalid_count || 0);
  if (!passkeyLoginBtn || !passkeyHint) return;
  if (!window.PublicKeyCredential || !window.isSecureContext) {{
    passkeyLoginBtn.disabled = true;
    passkeyHint.textContent = 'Passkey требует secure context: HTTPS или localhost.';
  }} else if (!loginAuthMeta.passkeys_supported) {{
    passkeyLoginBtn.disabled = true;
    passkeyHint.textContent = 'На сервере ещё не установлен WebAuthn модуль. Запусти свежий install-vps.sh.';
  }} else if (!Number(loginAuthMeta.passkey_count || 0) && invalidPasskeyCount) {{
    passkeyLoginBtn.disabled = true;
    passkeyHint.textContent = 'Сохранённый passkey повреждён или устарел. Удали его в меню доступа и зарегистрируй заново.';
  }} else if (!Number(loginAuthMeta.passkey_count || 0)) {{
    passkeyLoginBtn.disabled = true;
    passkeyHint.textContent = 'Сначала зарегистрируй passkey в меню доступа внутри панели.';
  }} else {{
    passkeyLoginBtn.disabled = false;
    passkeyHint.textContent = 'Готово: можно входить через системный passkey.';
  }}
}}

function refreshSocialLoginMethods() {{
  const social = loginAuthMeta.social || {{}};
  let visibleCount = 0;
  Object.entries(socialLoginUi).forEach(([provider, ui]) => {{
    const meta = social[provider] || {{}};
    const linkedCount = Number(meta.linked_count || 0);
    const enabled = !!meta.enabled && linkedCount > 0;
    if (ui.card) ui.card.hidden = !enabled;
    if (ui.button) ui.button.disabled = !enabled;
    if (ui.hint) {{
      ui.hint.textContent = enabled
        ? `Доступно ${{linkedCount}} привяз. аккаунт(ов) ${{ui.label}}.`
        : `Вход через ${{ui.label}} появится только после привязки аккаунта в Hub.`;
    }}
    if (enabled) visibleCount += 1;
  }});
  if (socialLoginSection) socialLoginSection.hidden = visibleCount === 0;
  if (socialMethodGrid) socialMethodGrid.hidden = visibleCount === 0;
  if (socialMethodEmpty) socialMethodEmpty.hidden = true;
  return visibleCount;
}}

function refreshLoginFlowMeta() {{
  const passkeyCount = Number(loginAuthMeta.passkey_count || 0);
  const invalidPasskeyCount = Number(loginAuthMeta.passkey_invalid_count || 0);
  const sshCount = Number(loginAuthMeta.ssh_key_count || 0);
  const socialVisibleCount = refreshSocialLoginMethods();
  const passwordState = loginAuthMeta.totp_enabled ? 'Пароль + 2FA активен.' : 'Пароль доступен как резерв.';
  const passkeyState = !window.PublicKeyCredential || !window.isSecureContext
    ? 'Passkey ждёт HTTPS/localhost.'
    : !loginAuthMeta.passkeys_supported
      ? 'Passkey ждёт модуль WebAuthn.'
      : !passkeyCount && invalidPasskeyCount
        ? 'Passkey повреждён: нужна перерегистрация.'
      : passkeyCount
        ? `Passkey готов: ${{passkeyCount}} шт.`
        : 'Passkey пока не зарегистрирован.';
  if (!sshCount) {{
    sshTicket = '';
    sshChallenge.value = '';
    sshSignature.value = '';
    if (sshDetails) sshDetails.open = false;
    sshChallengeBtn.disabled = true;
    sshVerifyBtn.disabled = true;
  }} else {{
    sshChallengeBtn.disabled = false;
    sshVerifyBtn.disabled = !sshTicket;
  }}
  updateSshHintText(sshCount);
  const socialState = socialVisibleCount
    ? `Соцвход готов: ${{socialVisibleCount}} привяз. сервис(ов).`
    : 'Соцвход появится после привязки аккаунта в Hub.';
  if (altAuthSummary) {{
    altAuthSummary.hidden = false;
    altAuthSummary.textContent = `${{passwordState}} Passkey: ${{passkeyState}} ED25519: ${{sshCount ? `готово (${{sshCount}})` : 'ожидает добавления ключа'}} Соцсервисы: ${{socialState}}`;
  }}
}}

async function loadLoginMeta() {{
  try {{
    const res = await fetch('/api/auth/meta', {{cache: 'no-store'}});
    const data = await res.json();
    loginAuthMeta = data.auth || {{}};
    updateLoginMeta();
    refreshLoginFlowMeta();
  }} catch (err) {{
    if (window.console && typeof window.console.warn === 'function') {{
      window.console.warn('Не удалось обновить /api/auth/meta', err);
    }}
  }}
}}

authTabButtons.forEach((button) => {{
  button.addEventListener('click', () => {{
    setActiveAuthTab(button.dataset.authTab || 'password');
  }});
}});

window.addEventListener('resize', () => {{
  updateSshHintText(Number(loginAuthMeta.ssh_key_count || 0));
  refreshLoginFlowMeta();
}});

setActiveAuthTab('password');
updateLoginMeta();
refreshLoginFlowMeta();

if (passwordToggleBtn && hubPassword) {{
  passwordToggleBtn.addEventListener('click', () => {{
    const hidden = hubPassword.type === 'password';
    hubPassword.type = hidden ? 'text' : 'password';
    passwordToggleBtn.setAttribute('aria-label', hidden ? 'Скрыть пароль' : 'Показать или скрыть пароль');
  }});
}}

if (captchaRefreshBtn) {{
  captchaRefreshBtn.addEventListener('click', () => {{
    window.location.reload();
  }});
}}

Object.entries(socialLoginUi).forEach(([provider, ui]) => {{
  if (!ui.button) return;
  ui.button.addEventListener('click', () => {{
    window.location.href = `/oauth/${{encodeURIComponent(provider)}}/start`;
  }});
}});

if (passkeyLoginBtn) {{
  passkeyLoginBtn.addEventListener('click', async () => {{
    passkeyStatus.hidden = true;
    try {{
      const beginRes = await fetch('/api/login/passkey/begin', {{method: 'POST'}});
      const beginData = await beginRes.json().catch(() => ({{}}));
      if (!beginRes.ok || !beginData.ok) throw new Error(beginData.error || 'Не удалось начать Passkey-вход');
      const assertion = await navigator.credentials.get(decodeRequestOptions(beginData.options || {{}}));
      const finishRes = await fetch('/api/login/passkey/finish', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{ticket: beginData.ticket, credential: encodeAssertion(assertion)}})
      }});
      const finishData = await finishRes.json().catch(() => ({{}}));
      if (!finishRes.ok || !finishData.ok) throw new Error(finishData.error || 'Passkey-вход не подтвердился');
      window.location.href = finishData.redirect || '/';
    }} catch (err) {{
      setMethodStatus(passkeyStatus, err && err.message ? err.message : 'Passkey-вход не удался', true);
    }}
  }});
}}

if (sshChallengeBtn) {{
  sshChallengeBtn.addEventListener('click', async () => {{
    sshStatus.hidden = true;
    try {{
      const res = await fetch('/api/login/ssh/begin', {{method: 'POST'}});
      const data = await res.json().catch(() => ({{}}));
      if (!res.ok || !data.ok) throw new Error(data.error || 'Не удалось получить challenge');
      sshTicket = data.ticket || '';
      sshChallenge.value = data.challenge || '';
      if (sshDetails) sshDetails.open = true;
      sshVerifyBtn.disabled = !sshTicket;
      setMethodStatus(sshStatus, 'Challenge готов. Подпиши его SSH ED25519 ключом и вставь signature.');
      refreshLoginFlowMeta();
    }} catch (err) {{
      sshVerifyBtn.disabled = true;
      setMethodStatus(sshStatus, err && err.message ? err.message : 'Не удалось получить challenge', true);
    }}
  }});
}}

if (sshVerifyBtn) {{
  sshVerifyBtn.addEventListener('click', async () => {{
    sshStatus.hidden = true;
    try {{
      if (!sshTicket || !sshChallenge.value.trim()) throw new Error('Сначала запроси challenge');
      if (!sshSignature.value.trim()) throw new Error('Вставь ASCII SSH signature');
      const res = await fetch('/api/login/ssh/finish', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{ticket: sshTicket, signature: sshSignature.value}})
      }});
      const data = await res.json().catch(() => ({{}}));
      if (!res.ok || !data.ok) throw new Error(data.error || 'Подпись не прошла проверку');
      window.location.href = data.redirect || '/';
    }} catch (err) {{
      setMethodStatus(sshStatus, err && err.message ? err.message : 'Подпись ED25519 не подошла', true);
    }}
  }});
}}

loadLoginMeta();
</script>
</body>
</html>"""


def ssh_key_manual_html():
    return """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Мануал: SSH key для Hub</title>
<style>
:root{color-scheme:dark;--bg:#07040f;--panel:rgba(19,14,32,.92);--text:#f7f2ff;--muted:#b9adc9;--line:rgba(169,126,255,.26);--cyan:#22d3ee;--amber:#f59e0b;--grid:rgba(168,85,247,.14)}
*{box-sizing:border-box}
body{position:relative;min-height:100vh;margin:0;overflow-x:hidden;background-color:var(--bg);background-image:radial-gradient(circle at 12% 8%,rgba(168,85,247,.46),transparent 31%),radial-gradient(circle at 82% 12%,rgba(79,70,229,.38),transparent 30%),radial-gradient(circle at 50% 105%,rgba(236,72,153,.26),transparent 35%),linear-gradient(145deg,#07040f,#120a24 48%,#05030a),repeating-linear-gradient(0deg,transparent 0 30px,var(--grid) 31px),repeating-linear-gradient(90deg,transparent 0 30px,var(--grid) 31px);background-size:130% 130%,140% 140%,135% 135%,100% 100%,31px 31px,31px 31px;background-attachment:fixed;color:var(--text);font:14px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;animation:bgFlow 28s ease-in-out infinite alternate}
body::before{content:"";position:fixed;inset:-25%;z-index:0;pointer-events:none;background:conic-gradient(from 0deg at 50% 50%,rgba(168,85,247,.05),rgba(236,72,153,.34),rgba(59,130,246,.22),rgba(245,158,11,.13),rgba(168,85,247,.05));filter:blur(54px);opacity:.7;animation:auraSpin 38s linear infinite}
@keyframes bgFlow{0%{background-position:0% 0%,100% 0%,50% 100%,0 0,0 0,0 0}50%{background-position:28% 18%,62% 26%,38% 82%,0 0,15px 24px,24px 15px}100%{background-position:48% 28%,42% 42%,74% 62%,0 0,30px 0,0 30px}}
@keyframes auraSpin{from{transform:rotate(0deg) scale(1)}to{transform:rotate(360deg) scale(1.08)}}
.wrap{position:relative;z-index:1;max-width:920px;margin:0 auto;padding:22px 14px 36px}
.hero{display:grid;gap:8px;margin-bottom:16px;padding:16px;border:1px solid var(--line);border-radius:16px;background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.04)),var(--panel)}
.hero h1{margin:0;font-size:24px;line-height:1.2}
.hero p{margin:0;color:var(--muted)}
.note{margin:0;padding:10px 12px;border:1px solid rgba(245,158,11,.24);border-radius:10px;background:rgba(245,158,11,.08);color:#fde68a}
.steps{display:grid;gap:12px}
.step{display:grid;gap:8px;padding:14px;border:1px solid var(--line);border-radius:14px;background:linear-gradient(180deg,rgba(255,255,255,.07),rgba(255,255,255,.03)),var(--panel)}
.step h2{margin:0;font-size:16px}
.step p{margin:0;color:var(--muted)}
.cmd{border:1px solid rgba(255,255,255,.08);border-radius:10px;background:rgba(7,4,15,.72);overflow:hidden}
.cmdHead{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:8px 10px;border-bottom:1px solid rgba(255,255,255,.07)}
.cmdLabel{color:#ddd6fe;font-size:11px;font-weight:850;text-transform:uppercase;letter-spacing:.04em}
.copyBtn,.topBtn{display:inline-flex;align-items:center;justify-content:center;min-height:34px;padding:7px 12px;border:1px solid rgba(34,211,238,.26);border-radius:999px;background:rgba(34,211,238,.10);color:#dbeafe;text-decoration:none;font-size:12px;font-weight:850;cursor:pointer}
.copyBtn:hover,.topBtn:hover{background:rgba(34,211,238,.16)}
.cmd pre{margin:0;padding:10px 12px;color:#c4b5fd;font:12px/1.45 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;word-break:break-word}
.topRow{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}
@media(max-width:560px){.wrap{padding:14px 10px 24px}.hero h1{font-size:21px}.cmdHead{flex-direction:column;align-items:stretch}.copyBtn,.topBtn{width:100%}}
</style>
</head>
<body>
<main class="wrap">
  <section class="hero">
    <div class="topRow">
      <h1>Мануал: как создать SSH key для Hub</h1>
      <a class="topBtn" href="/login" target="_blank" rel="noopener noreferrer">Открыть логин Hub</a>
    </div>
    <p>PowerShell команды выполняются на вашем ПК с Windows, не на VPS. В Hub вы вставляете публичный ключ, потом challenge и затем подпись.</p>
    <p class="note">Важно: файл <code>id_ed25519_hub</code> это приватный ключ, его никому не показывать. В Hub вставляется только файл <code>.pub</code>.</p>
  </section>

  <section class="steps">
    <article class="step">
      <h2>1. Открой PowerShell на Windows</h2>
      <p>Нажмите Пуск, введите <code>PowerShell</code> и откройте Windows PowerShell.</p>
    </article>

    <article class="step">
      <h2>2. Создай SSH-ключ на своем ПК</h2>
      <p>Выполни команду ниже. Потом можно просто два раза нажать Enter, если не хочешь ставить passphrase на ключ.</p>
      <div class="cmd">
        <div class="cmdHead">
          <span class="cmdLabel">PowerShell</span>
          <button class="copyBtn" type="button" data-copy-text="ssh-keygen -t ed25519 -a 100 -f $env:USERPROFILE\\.ssh\\id_ed25519_hub -C &quot;hub-login&quot;">Копировать</button>
        </div>
        <pre>ssh-keygen -t ed25519 -a 100 -f $env:USERPROFILE\\.ssh\\id_ed25519_hub -C "hub-login"</pre>
      </div>
      <p>После этого появятся файлы <code>C:\\Users\\ВАШЕ_ИМЯ\\.ssh\\id_ed25519_hub</code> и <code>C:\\Users\\ВАШЕ_ИМЯ\\.ssh\\id_ed25519_hub.pub</code>.</p>
    </article>

    <article class="step">
      <h2>3. Покажи публичный ключ и скопируй его целиком</h2>
      <p>Нужна вся строка вида <code>ssh-ed25519 AAAA... hub-login</code>.</p>
      <div class="cmd">
        <div class="cmdHead">
          <span class="cmdLabel">PowerShell</span>
          <button class="copyBtn" type="button" data-copy-text="Get-Content $env:USERPROFILE\\.ssh\\id_ed25519_hub.pub">Копировать</button>
        </div>
        <pre>Get-Content $env:USERPROFILE\\.ssh\\id_ed25519_hub.pub</pre>
      </div>
    </article>

    <article class="step">
      <h2>4. Добавь публичный ключ в Hub</h2>
      <p>Открой Hub, зайди в <code>Доступ к Hub</code>, блок <code>SSH ED25519</code>. Введи текущий пароль от Hub, укажи метку, например <code>Windows-PC</code>, вставь строку из файла <code>.pub</code> и нажми <code>Добавить SSH ED25519</code>.</p>
    </article>

    <article class="step">
      <h2>5. Получи challenge на экране логина Hub</h2>
      <p>Нажми <code>Получить challenge</code>, скопируй текст и сохрани его в файл на Windows ПК. Проще всего открыть Блокнот этой командой:</p>
      <div class="cmd">
        <div class="cmdHead">
          <span class="cmdLabel">PowerShell</span>
          <button class="copyBtn" type="button" data-copy-text="notepad $env:TEMP\\hub-challenge.txt">Копировать</button>
        </div>
        <pre>notepad $env:TEMP\\hub-challenge.txt</pre>
      </div>
      <p>Вставь туда challenge из Hub, сохрани файл и закрой Блокнот.</p>
    </article>

    <article class="step">
      <h2>6. Подпиши challenge приватным ключом</h2>
      <p>Подписывать нужно файлом без <code>.pub</code>, то есть именно приватным ключом <code>id_ed25519_hub</code>.</p>
      <div class="cmd">
        <div class="cmdHead">
          <span class="cmdLabel">PowerShell</span>
          <button class="copyBtn" type="button" data-copy-text="ssh-keygen -Y sign -f $env:USERPROFILE\\.ssh\\id_ed25519_hub -n owrt-remote-hub $env:TEMP\\hub-challenge.txt">Копировать</button>
        </div>
        <pre>ssh-keygen -Y sign -f $env:USERPROFILE\\.ssh\\id_ed25519_hub -n owrt-remote-hub $env:TEMP\\hub-challenge.txt</pre>
      </div>
      <p>После этого рядом появится файл <code>C:\\Users\\ВАШЕ_ИМЯ\\AppData\\Local\\Temp\\hub-challenge.txt.sig</code>.</p>
    </article>

    <article class="step">
      <h2>7. Покажи подпись и скопируй весь блок</h2>
      <p>Нужен весь текст от <code>-----BEGIN SSH SIGNATURE-----</code> до <code>-----END SSH SIGNATURE-----</code>.</p>
      <div class="cmd">
        <div class="cmdHead">
          <span class="cmdLabel">PowerShell</span>
          <button class="copyBtn" type="button" data-copy-text="Get-Content $env:TEMP\\hub-challenge.txt.sig">Копировать</button>
        </div>
        <pre>Get-Content $env:TEMP\\hub-challenge.txt.sig</pre>
      </div>
    </article>

    <article class="step">
      <h2>8. Вставь подпись обратно в Hub</h2>
      <p>Вернись на экран логина, вставь весь блок подписи в поле <code>SSH signature</code> и нажми <code>Проверить подпись</code>. Если все ок, Hub впустит без пароля.</p>
    </article>

    <article class="step">
      <h2>Если команда <code>ssh-keygen</code> не работает</h2>
      <p>Проверь, установлен ли OpenSSH Client в Windows.</p>
      <div class="cmd">
        <div class="cmdHead">
          <span class="cmdLabel">PowerShell</span>
          <button class="copyBtn" type="button" data-copy-text="ssh -V">Копировать</button>
        </div>
        <pre>ssh -V</pre>
      </div>
    </article>
  </section>
</main>
<script>
async function copyText(text){
  const value = String(text || '');
  if (!value) return false;
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch (err) {
    const area = document.createElement('textarea');
    area.value = value;
    area.setAttribute('readonly', 'readonly');
    area.style.position = 'fixed';
    area.style.left = '-1000px';
    area.style.top = '-1000px';
    document.body.appendChild(area);
    area.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (copyErr) {}
    area.remove();
    return ok;
  }
}
document.addEventListener('click', async (event) => {
  const btn = event.target.closest('.copyBtn[data-copy-text]');
  if (!btn) return;
  const original = btn.textContent;
  const ok = await copyText(btn.getAttribute('data-copy-text') || '');
  btn.textContent = ok ? 'Скопировано' : 'Не удалось';
  window.setTimeout(() => {
    btn.textContent = original;
  }, 1400);
});
</script>
</body>
</html>"""


_base_login_html = login_html


def login_html(error=""):
    auth = normalize_auth_state(load_auth())
    page = _base_login_html(error)
    state = effective_captcha_state(auth)
    if state.get("effective_mode") != CAPTCHA_MODE_RECAPTCHA:
        return page
    replacement = modern_login_captcha_html(auth)
    page, count = re.subn(
        r'<div class="captchaSection">.*?<input name="captcha_token" type="hidden" value="[^"]*">',
        replacement,
        page,
        count=1,
        flags=re.S,
    )
    if count and ".captchaSectionRecaptcha" not in page:
        page = page.replace(
            ".captchaRefresh:active{transform:translateY(1px)}",
            ".captchaRefresh:active{transform:translateY(1px)}"
            ".captchaSectionRecaptcha{gap:12px}"
            ".captchaHint{color:var(--muted);font-size:12px;line-height:1.45}"
            ".recaptchaBox{display:flex;justify-content:center;overflow-x:auto;padding:4px 0}",
            1,
        )
    script = login_recaptcha_script(auth)
    if script and script not in page:
        page = page.replace("</head>", f"{script}\n</head>", 1)
    return page


class App:
    def __init__(self, db_path, session, agent, public_url):
        self.db_path = Path(db_path)
        self.session_token = session
        self.agent_token = agent
        self.public_url = public_url.rstrip("/")
        self.router_state_lock = threading.Lock()
        self.router_state_snapshot = {}
        self.router_monitor_stop = threading.Event()
        self.router_monitor_thread = None

    def conn(self):
        conn = connect(self.db_path)
        init_db(conn)
        return conn

    def snapshot_router_states(self):
        with self.conn() as conn:
            routers = [row_to_router(row) for row in list_router_rows(conn)]
        return {
            str(router.get("id") or ""): {
                "online": bool(router.get("online")),
                "router": router,
            }
            for router in routers
            if str(router.get("id") or "").strip()
        }

    def prime_router_state_snapshot(self):
        snapshot = self.snapshot_router_states()
        with self.router_state_lock:
            self.router_state_snapshot = snapshot

    def check_router_state_changes(self):
        current = self.snapshot_router_states()
        with self.router_state_lock:
            previous = dict(self.router_state_snapshot)
            self.router_state_snapshot = current
        for router_id, old in previous.items():
            new = current.get(router_id)
            if not new:
                continue
            if bool(old.get("online")) and not bool(new.get("online")):
                notify_router_offline(new.get("router") or {})

    def router_state_monitor_loop(self):
        interval = max(3, int(os.environ.get("OWRT_REMOTE_ROUTER_NOTIFY_POLL", "5")))
        self.prime_router_state_snapshot()
        while not self.router_monitor_stop.wait(interval):
            try:
                self.check_router_state_changes()
            except Exception as exc:
                print(f"WARNING: router state monitor error: {exc}", file=sys.stderr)

    def start_router_state_monitor(self):
        if self.router_monitor_thread and self.router_monitor_thread.is_alive():
            return
        self.router_monitor_stop.clear()
        self.router_monitor_thread = threading.Thread(target=self.router_state_monitor_loop, daemon=True)
        self.router_monitor_thread.start()

    def stop_router_state_monitor(self):
        self.router_monitor_stop.set()


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

    def current_account_profile(self, touch=True):
        auth = normalize_auth_state(load_auth())
        if self.legacy_admin_ok():
            return account_profile(auth, auth.get("username", "admin"))
        session = self.current_hub_session(touch=touch)
        if not session:
            return None
        return account_profile(auth, session.get("username", ""))

    def owner_ok(self):
        profile = self.current_account_profile(touch=False)
        return bool(profile and profile.get("is_owner"))

    def can_add_router(self):
        profile = self.current_account_profile(touch=False)
        return bool(profile and account_can_add_router(profile))

    def require_owner(self, json_mode=False):
        if self.owner_ok():
            return True
        if not self.admin_ok():
            if json_mode:
                self.send_json(401, {"ok": False, "error": "not authorized"})
            else:
                self.send_bytes(401, login_html().encode("utf-8"), "text/html; charset=utf-8")
            return False
        if json_mode:
            self.send_json(403, {"ok": False, "error": "Недостаточно прав"})
        else:
            self.send_text(403, "Недостаточно прав")
        return False

    def require_add_router(self, json_mode=False):
        if self.can_add_router():
            return True
        if not self.admin_ok():
            if json_mode:
                self.send_json(401, {"ok": False, "error": "not authorized"})
            else:
                self.send_bytes(401, login_html().encode("utf-8"), "text/html; charset=utf-8")
            return False
        if json_mode:
            self.send_json(403, {"ok": False, "error": "Недостаточно прав для добавления роутера"})
        else:
            self.send_text(403, "Недостаточно прав для добавления роутера")
        return False

    def router_access_allowed(self, router_id, flag):
        profile = self.current_account_profile()
        return bool(profile and account_has_router_flag(profile, router_id, flag))

    def require_router_access(self, router_id, flag, json_mode=False):
        if self.router_access_allowed(router_id, flag):
            return True
        if not self.admin_ok():
            if json_mode:
                self.send_json(401, {"ok": False, "error": "not authorized"})
            else:
                self.send_bytes(401, login_html().encode("utf-8"), "text/html; charset=utf-8")
            return False
        if json_mode:
            self.send_json(403, {"ok": False, "error": "Недостаточно прав для этого роутера"})
        else:
            self.send_text(403, "Недостаточно прав для этого роутера")
        return False

    def current_or_owner_session_scope(self):
        profile = self.current_account_profile(touch=False)
        if not profile or profile.get("is_owner"):
            return ""
        return str(profile.get("username") or "")

    def visible_routers(self, conn, account=None):
        profile = account or self.current_account_profile()
        routers = [row_to_router(r) for r in list_router_rows(conn)]
        if profile and not profile.get("is_owner"):
            allowed = set(profile.get("router_ids") or [])
            routers = [router for router in routers if str(router.get("id") or "") in allowed]
        return [router_with_access(router, profile) for router in routers]

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

    def send_local_asset(self, file_path, content_type):
        try:
            body = Path(file_path).read_bytes()
        except FileNotFoundError:
            self.send_text(404, "asset not found")
            return
        self.send_bytes(200, body, content_type)

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
        if "multipart/form-data" in ctype.lower():
            message = BytesParser(policy=email_policy_default).parsebytes(
                b"Content-Type: " + ctype.encode("utf-8", errors="replace") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
            )
            payload = {"__files__": {}}
            if not message.is_multipart():
                return payload
            for part in message.iter_parts():
                if part.get_content_disposition() != "form-data":
                    continue
                name = str(part.get_param("name", header="content-disposition") or "").strip()
                if not name:
                    continue
                filename = part.get_filename()
                raw = part.get_payload(decode=True) or b""
                if filename is not None:
                    payload["__files__"].setdefault(name, []).append(
                        {
                            "filename": filename,
                            "content_type": part.get_content_type() or "",
                            "body": raw,
                        }
                    )
                    continue
                payload[name] = raw.decode(part.get_content_charset() or "utf-8", errors="replace")
            return payload
        parsed = urllib.parse.parse_qs(body.decode("utf-8"))
        return {k: v[-1] for k, v in parsed.items()}

    def session_cookie(self, token):
        return f"{SESSION_COOKIE}={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={SESSION_TTL_SECONDS}"

    def clear_session_cookie(self):
        return f"{SESSION_COOKIE}=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"

    def request_scheme(self):
        forwarded = str(self.headers.get("X-Forwarded-Proto", "")).split(",", 1)[0].strip().lower()
        if forwarded in {"http", "https"}:
            return forwarded
        return "https" if isinstance(self.request, ssl.SSLSocket) else "http"

    def request_host(self):
        scheme = self.request_scheme()
        host = str(self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "").split(",", 1)[0].strip()
        forwarded_port = self.headers.get("X-Forwarded-Port", "")
        return append_forwarded_port(scheme, host, forwarded_port)

    def request_origin(self):
        return origin_from_parts(self.request_scheme(), self.request_host())

    def request_host_name(self):
        try:
            return urllib.parse.urlsplit(self.request_origin()).hostname or ""
        except Exception:
            return ""

    def webauthn_rp_id(self):
        return public_url_rp_id(self.app.public_url) or self.request_host_name()

    def webauthn_allowed_origins(self):
        values = []
        for item in (public_url_origin(self.app.public_url), self.request_origin()):
            if item and item not in values:
                values.append(item)
        return values

    def build_webauthn_server(self):
        if not passkey_supported():
            raise ValueError("На сервере не установлен модуль Passkey/WebAuthn")
        rp_id = self.webauthn_rp_id()
        origins = set(self.webauthn_allowed_origins())
        if not rp_id or not origins:
            raise ValueError("Не удалось определить origin/RP ID для Passkey")

        def verify_origin(origin):
            try:
                parts = urllib.parse.urlsplit(str(origin or ""))
                return origin_from_parts(parts.scheme, parts.netloc) in origins
            except Exception:
                return False

        return Fido2Server({"id": rp_id, "name": APP_NAME}, verify_origin=verify_origin)

    def require_current_password(self, payload, auth=None):
        auth = normalize_auth_state(auth or load_auth())
        current_password = str((payload or {}).get("current_password") or "")
        if not verify_login(auth.get("username", ""), current_password):
            raise ValueError("Текущий пароль неверный")
        return auth

    def verify_login_captcha(self, payload, auth=None):
        auth = normalize_auth_state(auth or load_auth())
        state = effective_captcha_state(auth)
        if state.get("effective_mode") == CAPTCHA_MODE_RECAPTCHA:
            token = (
                (payload or {}).get("g-recaptcha-response")
                or (payload or {}).get("recaptcha_token")
                or ""
            )
            return verify_recaptcha_token(
                state.get("secret_key", ""),
                token,
                remote_ip=self.client_ip(),
                expected_hostname=self.request_host_name(),
            )
        captcha_token = (payload or {}).get("captcha_token", "")
        captcha_answer = (payload or {}).get("captcha_answer", "")
        if verify_captcha(captcha_token, captcha_answer):
            return True, ""
        return False, "РќРµРІРµСЂРЅР°СЏ РєР°РїС‡Р°"

    def create_login_session(self, username="", auth_method="password"):
        session_username = str(username or current_username()).strip()
        token, session = make_hub_session(
            session_username,
            self.client_ip(),
            self.headers.get("User-Agent", ""),
            auth_method=auth_method,
        )
        add_notification(
            "login",
            "Вход в Hub",
            f"{session.get('client', 'устройство')} · {auth_method_label(auth_method)} · IP {session.get('ip', 'unknown')}",
            "warn",
            [session.get("user_agent", "")],
            {
                "session_id": session.get("id", ""),
                "username": session_username,
                "ip": session.get("ip", ""),
                "client": session.get("client", ""),
                "user_agent": session.get("user_agent", ""),
                "auth_method": auth_method,
            },
        )
        return token, session

    def send_login_json(self, username="", auth_method="password"):
        token, _ = self.create_login_session(username, auth_method)
        body = {
            "ok": True,
            "redirect": "/",
            "auth_method": auth_method,
            "username": username or current_username(),
        }
        self.send_bytes(
            200,
            json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            "application/json; charset=utf-8",
            [("Set-Cookie", self.session_cookie(token))],
        )

    def social_callback_url(self, provider):
        provider = str(provider or "").strip().lower()
        base = public_url_origin(self.app.public_url) or self.request_origin()
        if not base:
            raise ValueError("Не удалось определить внешний URL Hub для OAuth callback")
        return f"{base}/oauth/{provider}/callback"

    def social_provider_config(self, provider, auth=None):
        provider = str(provider or "").strip().lower()
        if provider not in SOCIAL_PROVIDERS:
            raise ValueError("Неизвестный OAuth provider")
        auth = normalize_auth_state(auth or load_auth())
        return sanitize_social_provider_state(provider, auth.get("social", {}).get(provider))

    def social_flow_authorize_url(self, provider, state, redirect_uri, flow=None):
        provider = str(provider or "").strip().lower()
        cfg = self.social_provider_config(provider)
        if not social_provider_configured(provider, cfg):
            raise ValueError(social_provider_setup_message(provider))
        flow = dict(flow or {})
        if provider == "github":
            query = {
                "client_id": cfg["client_id"],
                "redirect_uri": redirect_uri,
                "scope": "read:user user:email",
                "state": state,
                "allow_signup": "false",
            }
        elif provider == "vk":
            code_verifier = str(flow.get("code_verifier") or "").strip()
            if not code_verifier:
                raise ValueError("VK ID flow не подготовлен")
            query = {
                "client_id": cfg["client_id"],
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": "email vkid.personal_info",
                "state": state,
                "code_challenge": make_pkce_challenge(code_verifier),
                "code_challenge_method": "S256",
            }
        else:
            raise ValueError("Неизвестный OAuth provider")
        return f"{SOCIAL_PROVIDERS[provider]['authorize_url']}?{urllib.parse.urlencode(query)}"

    def oauth_popup_html(self, title, message, provider="", ok=False):
        tone = "ok" if ok else "bad"
        provider = str(provider or "").strip().lower()
        payload = json.dumps({"type": "owrt-social-linked", "provider": provider, "ok": bool(ok)}, ensure_ascii=False)
        safe_title = html.escape(title or APP_NAME)
        safe_message = html.escape(message or "")
        return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{branding_head_tags(include_manifest=False)}
<title>{safe_title}</title>
<style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#0f172a;color:#e2e8f0;font:16px/1.45 system-ui,sans-serif;padding:20px}}
.card{{width:min(100%,420px);padding:24px;border-radius:18px;border:1px solid rgba(148,163,184,.24);background:rgba(15,23,42,.88);box-shadow:0 20px 60px rgba(15,23,42,.45)}}
.title{{margin:0 0 10px;font-size:22px;font-weight:800}}
.msg{{margin:0;color:#cbd5e1}}
.pill{{display:inline-flex;align-items:center;gap:8px;margin-bottom:14px;padding:7px 12px;border-radius:999px;background:{'#052e16' if ok else '#3f0d12'};color:{'#bbf7d0' if ok else '#fecaca'};font-size:13px;font-weight:700}}
.link{{display:inline-flex;margin-top:18px;color:#93c5fd}}
</style>
</head>
<body>
  <article class="card">
    <div class="pill">{'Готово' if ok else 'Ошибка'}</div>
    <h1 class="title">{safe_title}</h1>
    <p class="msg">{safe_message}</p>
    <a class="link" href="/">{'Вернуться в Hub' if ok else 'Открыть вход в Hub'}</a>
  </article>
<script>
const payload = {payload};
try {{
  if (window.opener && !window.opener.closed) {{
    window.opener.postMessage(payload, window.location.origin);
    setTimeout(() => window.close(), 250);
  }}
}} catch (err) {{}}
</script>
</body>
</html>"""

    def social_login_error_page(self, message):
        safe_message = html.escape(message or "Не удалось завершить OAuth вход")
        return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{branding_head_tags(include_manifest=False)}
<title>OAuth ошибка</title>
<style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#0f172a;color:#e2e8f0;font:16px/1.45 system-ui,sans-serif;padding:20px}}
.card{{width:min(100%,460px);padding:24px;border-radius:18px;border:1px solid rgba(248,113,113,.26);background:rgba(15,23,42,.88);box-shadow:0 20px 60px rgba(15,23,42,.45)}}
h1{{margin:0 0 10px;font-size:22px}}
p{{margin:0;color:#cbd5e1}}
a{{display:inline-flex;margin-top:18px;color:#93c5fd}}
</style>
</head>
<body>
  <article class="card">
    <h1>OAuth вход не завершен</h1>
    <p>{safe_message}</p>
    <a href="/login">Вернуться на страницу входа</a>
  </article>
</body>
</html>"""

    def exchange_social_code(self, provider, code, redirect_uri, flow=None, flow_state="", device_id=""):
        provider = str(provider or "").strip().lower()
        cfg = self.social_provider_config(provider)
        flow = dict(flow or {})
        if provider == "github":
            token = oauth_fetch_json(
                SOCIAL_PROVIDERS[provider]["token_url"],
                method="POST",
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "client_id": cfg["client_id"],
                    "client_secret": cfg["client_secret"],
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
            )
            access_token = str(token.get("access_token") or "").strip()
            if not access_token:
                raise ValueError(token.get("error_description") or token.get("error") or "GitHub не вернул access token")
            profile = oauth_fetch_json(
                "https://api.github.com/user",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {access_token}",
                },
            )
            emails = oauth_fetch_json(
                "https://api.github.com/user/emails",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {access_token}",
                },
            )
            primary_email = ""
            if isinstance(emails, list):
                for row in emails:
                    if row.get("primary"):
                        primary_email = str(row.get("email") or "").strip()
                        break
                if not primary_email and emails:
                    primary_email = str(emails[0].get("email") or "").strip()
            account_id = str(profile.get("id") or "").strip()
            if not account_id:
                raise ValueError("GitHub не вернул ID пользователя")
            return {
                "id": account_id,
                "label": str(profile.get("name") or profile.get("login") or primary_email or account_id),
                "login": str(profile.get("login") or "").strip(),
                "name": str(profile.get("name") or "").strip(),
                "email": primary_email,
                "profile_url": str(profile.get("html_url") or "").strip(),
                "avatar_url": str(profile.get("avatar_url") or "").strip(),
            }
        if provider == "vk":
            code_verifier = str(flow.get("code_verifier") or "").strip()
            if not code_verifier:
                raise ValueError("VK ID flow истек. Запусти вход еще раз")
            device_id = str(device_id or "").strip()
            if not device_id:
                raise ValueError("VK ID не вернул device_id")
            token_payload = {
                "grant_type": "authorization_code",
                "client_id": cfg["client_id"],
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
                "device_id": device_id,
                "state": str(flow_state or "").strip(),
            }
            service_token = str(cfg.get("client_secret") or "").strip()
            if service_token:
                token_payload["service_token"] = service_token
            token = oauth_fetch_json(
                SOCIAL_PROVIDERS[provider]["token_url"],
                method="POST",
                headers={"Accept": "application/json"},
                data=token_payload,
            )
            access_token = str(token.get("access_token") or "").strip()
            if not access_token:
                raise ValueError(token.get("error_description") or token.get("error") or "VK ID не вернул access token")
            token_state = str(token.get("state") or "").strip()
            if token_state and token_state != str(flow_state or "").strip():
                raise ValueError("VK ID вернул неожиданный state")
            profile = oauth_fetch_json(
                SOCIAL_PROVIDERS[provider]["api_url"],
                method="POST",
                headers={"Accept": "application/json"},
                data={
                    "client_id": cfg["client_id"],
                    "access_token": access_token,
                },
            )
            user = profile.get("user") if isinstance(profile.get("user"), dict) else {}
            account_id = str(token.get("user_id") or user.get("user_id") or "").strip()
            if not account_id:
                raise ValueError("VK ID не вернул ID пользователя")
            first_name = str(user.get("first_name") or "").strip()
            last_name = str(user.get("last_name") or "").strip()
            full_name = " ".join(part for part in (first_name, last_name) if part).strip()
            email = str(user.get("email") or "").strip()
            return {
                "id": account_id,
                "label": full_name or email or f"VK ID {account_id}",
                "login": "",
                "name": full_name,
                "email": email,
                "profile_url": f"https://vk.com/id{account_id}",
                "avatar_url": str(user.get("avatar") or "").strip(),
            }
        raise ValueError("Неизвестный OAuth provider")

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
        otp = payload.get("otp", "")
        captcha_token = payload.get("captcha_token", "")
        captcha_answer = payload.get("captcha_answer", "")
        if not verify_captcha(captcha_token, captcha_answer):
            self.send_bytes(401, login_html("Неверная капча").encode("utf-8"), "text/html; charset=utf-8")
            return
        ok, error_text, auth, auth_method = verify_password_login(username, password, otp)
        if ok:
            token, _ = self.create_login_session((auth or {}).get("username", username), auth_method)
            self.redirect("/", [("Set-Cookie", self.session_cookie(token))])
            return
        self.send_bytes(401, login_html(error_text or "Неверный логин или пароль").encode("utf-8"), "text/html; charset=utf-8")

    def login(self):
        payload = self.read_payload()
        username = payload.get("username", "")
        password = payload.get("password", "")
        otp = payload.get("otp", "")
        captcha_ok, captcha_error = self.verify_login_captcha(payload)
        if not captcha_ok:
            self.send_bytes(401, login_html(captcha_error or "РќРµРІРµСЂРЅР°СЏ РєР°РїС‡Р°").encode("utf-8"), "text/html; charset=utf-8")
            return
        ok, error_text, auth, auth_method = verify_password_login(username, password, otp)
        if ok:
            token, _ = self.create_login_session((auth or {}).get("username", username), auth_method)
            self.redirect("/", [("Set-Cookie", self.session_cookie(token))])
            return
        self.send_bytes(401, login_html(error_text or "РќРµРІРµСЂРЅС‹Р№ Р»РѕРіРёРЅ РёР»Рё РїР°СЂРѕР»СЊ").encode("utf-8"), "text/html; charset=utf-8")

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
            save_auth_state(auth)
        self.send_text(200, "Доступ к Hub обновлен")

    def update_auth(self):
        payload = self.read_payload()
        auth = normalize_auth_state(load_auth())
        current_password = payload.get("current_password", "")
        if not verify_login(auth.get("username", ""), current_password):
            self.send_text(403, "Текущий пароль неверный")
            return
        username = payload.get("username", auth.get("username", "admin"))
        new_password = payload.get("password", "")
        confirm = payload.get("password_confirm", "")
        captcha_mode = sanitize_captcha_mode(payload.get("captcha_mode"))
        captcha_site_key = str(payload.get("captcha_site_key") or "").strip()
        captcha_secret_key = str(payload.get("captcha_secret_key") or "").strip()
        if new_password:
            if new_password != confirm:
                self.send_text(400, "Новый пароль и повтор не совпадают")
                return
            if len(new_password) < MIN_PASSWORD_LENGTH:
                self.send_text(400, f"Новый пароль должен быть минимум {MIN_PASSWORD_LENGTH} символа")
                return
        if captcha_mode == CAPTCHA_MODE_RECAPTCHA and not (captcha_site_key and captcha_secret_key):
            self.send_text(400, "Для Google reCAPTCHA нужны Site Key и Secret Key")
            return
        auth["username"] = clean_username(username)
        if new_password:
            auth["password"] = password_digest(new_password)
        auth["captcha"] = sanitize_captcha_state(
            {
                "mode": captcha_mode,
                "site_key": captcha_site_key,
                "secret_key": captcha_secret_key,
            }
        )
        auth["updated_at"] = now_ts()
        save_auth_state(auth)
        self.send_text(200, "Доступ к Hub обновлен")

    def build_auth_meta(self):
        auth = normalize_auth_state(load_auth())
        current = self.current_account_profile(touch=False) if self.admin_ok() else None
        if current and current.get("is_owner"):
            meta = admin_auth_meta(auth)
            with self.app.conn() as conn:
                router_rows = list_router_rows(conn)
            session_rows_by_username = {}
            for session in list_hub_sessions("", ""):
                username = str(session.get("username") or "")
                if not username:
                    continue
                session_rows_by_username.setdefault(username, []).append(session)
            login_rows_by_username = {}
            for event in list_login_events(""):
                username = str(event.get("username") or "")
                if not username:
                    continue
                login_rows_by_username.setdefault(username, []).append(event)
            meta["username"] = current.get("username", auth.get("username", "admin"))
            meta["is_owner"] = True
            meta["can_add_router"] = True
            meta["current_user"] = {
                "username": current.get("username", auth.get("username", "admin")),
                "is_owner": True,
            }
            meta["router_options"] = [
                {
                    "id": router.get("id", ""),
                    "name": router.get("name", ""),
                    "role": router.get("role", "node"),
                }
                for router in [row_to_router(row) for row in router_rows]
            ]
            meta["delegated_users"] = [
                item
                for item in (
                    serialize_delegated_user(
                        auth,
                        user,
                        router_rows,
                        session_rows_by_username.get(str((user or {}).get("username") or ""), []),
                        login_rows_by_username.get(str((user or {}).get("username") or ""), []),
                    )
                    for user in auth.get("delegated_users", [])
                )
                if item
            ]
        elif current:
            with self.app.conn() as conn:
                router_rows = list_router_rows(conn)
            meta = delegated_auth_meta(auth, current, router_rows)
        else:
            meta = public_auth_meta(auth)
            meta["can_add_router"] = False
        social = meta.get("social") if isinstance(meta.get("social"), dict) else {}
        for provider, item in social.items():
            if not isinstance(item, dict):
                continue
            item["redirect_uri"] = self.social_callback_url(provider)
        return meta

    def auth_meta(self):
        self.send_json(200, {"ok": True, "auth": self.build_auth_meta()})

    def save_delegated_user(self):
        try:
            payload = self.read_payload()
            auth = self.require_current_password(payload)
            username = clean_username(payload.get("username", ""))
            if secrets.compare_digest(username, auth.get("username", "")):
                raise ValueError("Главный аккаунт нельзя превратить в ограниченный")
            user_id = str(payload.get("id") or "").strip()
            password = str(payload.get("password") or "")
            password_confirm = str(payload.get("password_confirm") or "")
            if password:
                if password != password_confirm:
                    raise ValueError("Пароль и повтор не совпадают")
                if len(password) < MIN_PASSWORD_LENGTH:
                    raise ValueError(f"Пароль должен быть минимум {MIN_PASSWORD_LENGTH} символа")
                next_password = password_digest(password)
            else:
                next_password = None
            router_flags = sanitize_router_access_map(payload.get("router_flags"))
            enabled = bool(payload.get("enabled", True))
            existing = None
            for item in auth.get("delegated_users", []):
                if user_id and item.get("id") == user_id:
                    existing = item
                    break
            for item in auth.get("delegated_users", []):
                if existing and item.get("id") == existing.get("id"):
                    continue
                if secrets.compare_digest(item.get("username", ""), username):
                    raise ValueError("Пользователь с таким логином уже существует")
            if existing is None and next_password is None:
                raise ValueError("Для нового пользователя нужен пароль")
            updated_users = []
            replaced = False
            for item in auth.get("delegated_users", []):
                if existing and item.get("id") == existing.get("id"):
                    updated_users.append(
                        {
                            **item,
                            "username": username,
                            "password": next_password or item.get("password", {}),
                            "enabled": enabled,
                            "router_flags": router_flags,
                            "updated_at": now_ts(),
                        }
                    )
                    replaced = True
                    continue
                updated_users.append(item)
            if not replaced:
                updated_users.append(
                    {
                        "id": user_id or secrets.token_hex(8),
                        "username": username,
                        "password": next_password,
                        "enabled": enabled,
                        "router_flags": router_flags,
                        "created_at": now_ts(),
                        "updated_at": now_ts(),
                    }
                )
            auth = save_auth_state({**auth, "delegated_users": updated_users})
            self.send_json(200, {"ok": True, "message": "Пользователь сохранен", "auth": self.build_auth_meta()})
        except ValueError as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self.send_json(500, {"ok": False, "error": str(exc)})

    def delete_delegated_user(self):
        try:
            payload = self.read_payload()
            auth = self.require_current_password(payload)
            user_id = str(payload.get("id") or "").strip()
            if not user_id:
                raise ValueError("Не указан пользователь")
            removed = 0
            removed_username = ""
            kept = []
            for item in auth.get("delegated_users", []):
                if item.get("id") == user_id:
                    removed += 1
                    removed_username = str(item.get("username") or "")
                    continue
                kept.append(item)
            if not removed:
                raise ValueError("Пользователь уже удален")
            revoke_hub_sessions_for_username(removed_username)
            auth = save_auth_state({**auth, "delegated_users": kept})
            self.send_json(200, {"ok": True, "message": "Пользователь удален", "auth": self.build_auth_meta()})
        except ValueError as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self.send_json(500, {"ok": False, "error": str(exc)})

    def revoke_delegated_user_sessions(self):
        try:
            payload = self.read_payload()
            auth = self.require_current_password(payload)
            user_id = str(payload.get("id") or "").strip()
            if not user_id:
                raise ValueError("Не указан пользователь")
            target = next((item for item in auth.get("delegated_users", []) if item.get("id") == user_id), None)
            if not target:
                raise ValueError("Пользователь не найден")
            target_username = str(target.get("username") or "").strip()
            removed = revoke_hub_sessions_for_username(target_username)
            self.send_json(
                200,
                {
                    "ok": True,
                    "removed": removed,
                    "message": "Сессии пользователя завершены" if removed else "У пользователя нет активных сессий",
                    "auth": self.build_auth_meta(),
                },
            )
        except ValueError as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self.send_json(500, {"ok": False, "error": str(exc)})

    def save_social_provider_config(self, provider):
        try:
            payload = self.read_payload()
            auth = self.require_current_password(payload)
            provider = str(provider or "").strip().lower()
            if provider not in SOCIAL_PROVIDERS:
                raise ValueError("Неизвестный OAuth provider")
            client_id = str(payload.get("client_id") or "").strip()
            client_secret = str(payload.get("client_secret") or "").strip()

            def mutator(current):
                current = normalize_auth_state(current)
                current["social"][provider]["client_id"] = client_id
                current["social"][provider]["client_secret"] = client_secret
                return current

            save_auth_state(mutator(auth))
            self.send_json(
                200,
                {
                    "ok": True,
                    "auth": self.build_auth_meta(admin=True),
                    "message": f"{social_provider_label(provider)} сохранен",
                },
            )
        except ValueError as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self.send_json(500, {"ok": False, "error": str(exc)})

    def begin_social_link(self, provider):
        try:
            payload = self.read_payload()
            auth = self.require_current_password(payload)
            provider = str(provider or "").strip().lower()
            redirect_uri = self.social_callback_url(provider)
            cfg = self.social_provider_config(provider, auth)
            if not social_provider_configured(provider, cfg):
                raise ValueError(social_provider_setup_message(provider))
            flow_payload = {
                "provider": provider,
                "mode": "link",
                "redirect_uri": redirect_uri,
            }
            if provider == "vk":
                flow_payload["code_verifier"] = make_pkce_verifier()
            ticket = put_auth_flow(f"oauth-{provider}", flow_payload)
            self.send_json(
                200,
                {
                    "ok": True,
                    "url": self.social_flow_authorize_url(provider, ticket, redirect_uri, flow=flow_payload),
                },
            )
        except ValueError as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self.send_json(500, {"ok": False, "error": str(exc)})

    def unlink_social_account(self, provider):
        try:
            payload = self.read_payload()
            auth = self.require_current_password(payload)
            provider = str(provider or "").strip().lower()
            account_id = str(payload.get("id") or "").strip()
            if not account_id:
                raise ValueError("Не указан аккаунт для отвязки")
            if not find_social_account_record(auth, provider, account_id):
                raise ValueError("Аккаунт уже отвязан")
            updated = save_auth_state(remove_social_account(auth, provider, account_id))
            self.send_json(
                200,
                {
                    "ok": True,
                    "auth": self.build_auth_meta(admin=True),
                    "message": f"{social_provider_label(provider)} отвязан",
                },
            )
        except ValueError as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self.send_json(500, {"ok": False, "error": str(exc)})

    def start_social_login(self, provider):
        provider = str(provider or "").strip().lower()
        try:
            redirect_uri = self.social_callback_url(provider)
            cfg = self.social_provider_config(provider)
            if not social_provider_configured(provider, cfg):
                raise ValueError(social_provider_setup_message(provider))
            if not cfg.get("accounts"):
                raise ValueError(f"Для {social_provider_label(provider)} пока нет привязанных аккаунтов")
            flow_payload = {
                "provider": provider,
                "mode": "login",
                "redirect_uri": redirect_uri,
            }
            if provider == "vk":
                flow_payload["code_verifier"] = make_pkce_verifier()
            ticket = put_auth_flow(f"oauth-{provider}", flow_payload)
            self.redirect(self.social_flow_authorize_url(provider, ticket, redirect_uri, flow=flow_payload))
        except ValueError as exc:
            self.send_bytes(400, self.social_login_error_page(str(exc)).encode("utf-8"), "text/html; charset=utf-8")
        except Exception as exc:
            self.send_bytes(500, self.social_login_error_page(str(exc)).encode("utf-8"), "text/html; charset=utf-8")

    def finish_social_callback(self, provider):
        provider = str(provider or "").strip().lower()
        params = self.query()
        flow_state = str(params.get("state", [""])[0] or "")
        error_code = str(params.get("error", [""])[0] or "")
        error_text = str(params.get("error_description", [""])[0] or params.get("error_reason", [""])[0] or error_code or "")
        flow = None
        try:
            flow = pop_auth_flow(flow_state, f"oauth-{provider}")
            if not flow:
                raise ValueError("OAuth сессия истекла или уже была использована")
            if error_code:
                raise ValueError(error_text or f"{social_provider_label(provider)} вернул ошибку авторизации")
            code = str(params.get("code", [""])[0] or "").strip()
            if not code:
                raise ValueError("OAuth provider не вернул код авторизации")
            device_id = str(params.get("device_id", [""])[0] or "").strip()
            account = self.exchange_social_code(
                provider,
                code,
                str(flow.get("redirect_uri") or self.social_callback_url(provider)),
                flow=flow,
                flow_state=flow_state,
                device_id=device_id,
            )
            if flow.get("mode") == "link":
                updated = save_auth_state(upsert_social_account(load_auth(), provider, account))
                message = f"{social_provider_label(provider)} аккаунт {account.get('label') or account.get('id')} привязан"
                self.send_bytes(
                    200,
                    self.oauth_popup_html(social_provider_label(provider), message, provider=provider, ok=True).encode("utf-8"),
                    "text/html; charset=utf-8",
                )
                return
            auth = load_auth()
            linked = find_social_account_record(auth, provider, account.get("id"))
            if not linked:
                raise ValueError("Этот аккаунт не привязан к Hub. Сначала привяжи его в разделе Доступ к Hub.")
            save_auth_state(touch_social_account_last_used(auth, provider, account.get("id")))
            token, _ = self.create_login_session(current_username(), auth_method=provider)
            self.redirect("/", [("Set-Cookie", self.session_cookie(token))])
        except ValueError as exc:
            if (flow or {}).get("mode") == "link":
                self.send_bytes(
                    400,
                    self.oauth_popup_html(social_provider_label(provider), str(exc), provider=provider, ok=False).encode("utf-8"),
                    "text/html; charset=utf-8",
                )
                return
            self.send_bytes(400, self.social_login_error_page(str(exc)).encode("utf-8"), "text/html; charset=utf-8")
        except Exception as exc:
            self.send_bytes(500, self.social_login_error_page(str(exc)).encode("utf-8"), "text/html; charset=utf-8")

    def totp_setup(self):
        try:
            payload = self.read_payload()
            auth = self.require_current_password(payload)
            secret = generate_totp_secret()
            ticket = put_auth_flow("totp-setup", {"secret": secret, "username": auth.get("username", "admin")})
            self.send_json(
                200,
                {
                    "ok": True,
                    "ticket": ticket,
                    "secret": secret,
                    "otpauth_uri": totp_otpauth_uri(auth.get("username", "admin"), secret),
                    "digits": TOTP_DIGITS,
                    "period": TOTP_PERIOD_SECONDS,
                },
            )
        except Exception as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})

    def totp_enable(self):
        try:
            payload = self.read_payload()
            self.require_current_password(payload)
            ticket = payload.get("ticket", "")
            flow = get_auth_flow(ticket, "totp-setup")
            if not flow:
                raise ValueError("Сессия настройки 2FA истекла, начни заново")
            secret = flow.get("secret", "")
            if not verify_totp(secret, payload.get("code", "")):
                raise ValueError("Неверный код 2FA")
            pop_auth_flow(ticket, "totp-setup")
            auth = update_auth_state(
                lambda current: {
                    **current,
                    "totp": {
                        **default_totp_state(),
                        "enabled": True,
                        "secret": secret,
                        "digits": TOTP_DIGITS,
                        "period": TOTP_PERIOD_SECONDS,
                        "updated_at": now_ts(),
                    },
                }
            )
            self.send_json(200, {"ok": True, "message": "2FA включена", "auth": admin_auth_meta(auth)})
        except Exception as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})

    def totp_disable(self):
        try:
            payload = self.read_payload()
            auth = self.require_current_password(payload)
            if not auth.get("totp", {}).get("enabled"):
                raise ValueError("2FA уже выключена")
            if not verify_totp(auth.get("totp", {}).get("secret", ""), payload.get("code", "")):
                raise ValueError("Неверный код 2FA")
            auth = update_auth_state(
                lambda current: {
                    **current,
                    "totp": {
                        **default_totp_state(),
                        "updated_at": now_ts(),
                    },
                }
            )
            self.send_json(200, {"ok": True, "message": "2FA выключена", "auth": admin_auth_meta(auth)})
        except Exception as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})

    def begin_passkey_registration(self):
        try:
            payload = self.read_payload()
            auth = self.require_current_password(payload)
            server = self.build_webauthn_server()
            label = str(payload.get("label") or f"Passkey {len(auth.get('passkeys', [])) + 1}").strip()[:80] or "Passkey"
            options, state = server.register_begin(
                {
                    "id": auth.get("username", "admin").encode("utf-8"),
                    "name": auth.get("username", "admin"),
                    "displayName": auth.get("username", "admin"),
                },
                passkey_credentials(auth),
                resident_key_requirement=ResidentKeyRequirement.PREFERRED if ResidentKeyRequirement else None,
                user_verification=UserVerificationRequirement.PREFERRED if UserVerificationRequirement else None,
            )
            ticket = put_auth_flow(
                "passkey-register",
                {"state": state, "label": label, "username": auth.get("username", "admin")},
            )
            self.send_json(200, {"ok": True, "ticket": ticket, "options": webauthn_json(dict(options))})
        except Exception as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})

    def finish_passkey_registration(self):
        try:
            payload = self.read_payload()
            ticket = payload.get("ticket", "")
            response = payload.get("credential") if isinstance(payload.get("credential"), dict) else payload
            flow = pop_auth_flow(ticket, "passkey-register")
            if not flow:
                raise ValueError("Passkey-регистрация истекла, начни заново")
            server = self.build_webauthn_server()
            auth_data = server.register_complete(flow.get("state"), response)
            credential_data = auth_data.credential_data
            if not credential_data:
                raise ValueError("Не удалось прочитать credential data")
            key_id = b64url(credential_data.credential_id)
            label = str(flow.get("label") or "Passkey").strip()[:80] or "Passkey"

            def mutator(current):
                current = normalize_auth_state(current)
                rows = [item for item in current.get("passkeys", []) if item.get("id") != key_id]
                rows.append(
                    {
                        "id": key_id,
                        "label": label,
                        "credential_data": b64url(bytes(credential_data)),
                        "created_at": now_ts(),
                        "last_used_at": 0,
                        "sign_count": int(auth_data.counter or 0),
                        "transports": [str(value).strip() for value in payload.get("transports", []) if str(value).strip()],
                    }
                )
                current["passkeys"] = rows
                return current

            auth = update_auth_state(mutator)
            self.send_json(200, {"ok": True, "message": "Passkey добавлен", "auth": admin_auth_meta(auth)})
        except Exception as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})

    def remove_passkey(self):
        try:
            payload = self.read_payload()
            self.require_current_password(payload)
            credential_id = str(payload.get("id") or "").strip()
            if not credential_id:
                raise ValueError("id passkey пустой")

            def mutator(current):
                current = normalize_auth_state(current)
                current["passkeys"] = [item for item in current.get("passkeys", []) if item.get("id") != credential_id]
                return current

            auth = update_auth_state(mutator)
            self.send_json(200, {"ok": True, "message": "Passkey удален", "auth": admin_auth_meta(auth)})
        except Exception as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})

    def begin_passkey_login(self):
        try:
            auth = load_auth()
            passkey_info = passkey_inventory(auth)
            credentials = passkey_info.get("credentials", [])
            if not credentials:
                if passkey_info.get("invalid_count"):
                    raise ValueError("Сохраненный passkey поврежден или записан в старом формате. Удали его в меню доступа и зарегистрируй заново")
                raise ValueError("Для входа Passkey пока не зарегистрирован")
            server = self.build_webauthn_server()
            options, state = server.authenticate_begin(
                credentials,
                user_verification=UserVerificationRequirement.PREFERRED if UserVerificationRequirement else None,
            )
            ticket = put_auth_flow("passkey-login", {"state": state, "username": auth.get("username", "admin")})
            self.send_json(200, {"ok": True, "ticket": ticket, "options": webauthn_json(dict(options))})
        except Exception as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})

    def finish_passkey_login(self):
        try:
            payload = self.read_payload()
            ticket = payload.get("ticket", "")
            response = payload.get("credential") if isinstance(payload.get("credential"), dict) else payload
            flow = pop_auth_flow(ticket, "passkey-login")
            if not flow:
                raise ValueError("Passkey-челлендж истек, начни вход заново")
            auth = load_auth()
            passkey_info = passkey_inventory(auth)
            credentials = passkey_info.get("credentials", [])
            if not credentials:
                if passkey_info.get("invalid_count"):
                    raise ValueError("Сохраненный passkey поврежден или записан в старом формате. Удали его в меню доступа и зарегистрируй заново")
                raise ValueError("Passkey не зарегистрирован")
            server = self.build_webauthn_server()
            credential = server.authenticate_complete(flow.get("state"), credentials, response)
            key_id = b64url(credential.credential_id)
            counter = 0
            if AuthenticationResponse is not None:
                try:
                    auth_response = AuthenticationResponse.from_dict(response)
                    counter = int(auth_response.response.authenticator_data.counter or 0)
                except Exception:
                    counter = 0

            def mutator(current):
                current = normalize_auth_state(current)
                for item in current.get("passkeys", []):
                    if item.get("id") == key_id:
                        item["last_used_at"] = now_ts()
                        item["sign_count"] = max(int(item.get("sign_count") or 0), counter)
                        break
                return current

            update_auth_state(mutator)
            self.send_login_json(auth.get("username", "admin"), "passkey")
        except Exception as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})

    def add_ssh_key(self):
        try:
            payload = self.read_payload()
            self.require_current_password(payload)
            key = sanitize_ssh_key_record(
                {
                    "id": payload.get("id", ""),
                    "label": payload.get("label", ""),
                    "public_key": payload.get("public_key", ""),
                    "created_at": now_ts(),
                }
            )
            if not key:
                raise ValueError("SSH ED25519 ключ не распознан")

            def mutator(current):
                current = normalize_auth_state(current)
                rows = [item for item in current.get("ssh_keys", []) if item.get("public_key") != key.get("public_key")]
                rows.append(key)
                current["ssh_keys"] = rows
                return current

            auth = update_auth_state(mutator)
            self.send_json(200, {"ok": True, "message": "SSH ED25519 ключ добавлен", "auth": admin_auth_meta(auth)})
        except Exception as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})

    def remove_ssh_key(self):
        try:
            payload = self.read_payload()
            self.require_current_password(payload)
            key_id = str(payload.get("id") or "").strip()
            if not key_id:
                raise ValueError("id SSH ключа пустой")

            def mutator(current):
                current = normalize_auth_state(current)
                current["ssh_keys"] = [item for item in current.get("ssh_keys", []) if item.get("id") != key_id]
                return current

            auth = update_auth_state(mutator)
            self.send_json(200, {"ok": True, "message": "SSH ключ удален", "auth": admin_auth_meta(auth)})
        except Exception as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})

    def begin_ssh_login(self):
        try:
            auth = load_auth()
            ssh_keys = auth.get("ssh_keys", [])
            if not ssh_keys:
                raise ValueError("Для входа ED25519 пока нет зарегистрированных ключей")
            issued_at = now_ts()
            host = self.request_host()
            ticket = put_auth_flow("ssh-login", {"username": auth.get("username", "admin"), "issued_at": issued_at, "host": host})
            challenge = build_ssh_auth_message(ticket, auth.get("username", "admin"), host, issued_at)
            with AUTH_FLOW_LOCK:
                state = AUTH_FLOW_STATE.get(ticket)
                if state:
                    state["challenge"] = challenge
            self.send_json(
                200,
                {
                    "ok": True,
                    "ticket": ticket,
                    "challenge": challenge,
                    "namespace": ssh_auth_namespace(),
                    "principal": ssh_auth_principal(auth.get("username", "admin")),
                    "keys": public_auth_meta(auth).get("ssh_keys", []),
                },
            )
        except Exception as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})

    def finish_ssh_login(self):
        try:
            payload = self.read_payload()
            ticket = str(payload.get("ticket") or "").strip()
            flow = pop_auth_flow(ticket, "ssh-login")
            if not flow:
                raise ValueError("ED25519-челлендж истек, начни вход заново")
            auth = load_auth()
            challenge = str(flow.get("challenge") or "")
            if not challenge:
                challenge = build_ssh_auth_message(
                    ticket,
                    auth.get("username", "admin"),
                    flow.get("host", "") or self.request_host(),
                    flow.get("issued_at", 0),
                )
            matched = verify_ssh_auth_signature(auth.get("ssh_keys", []), auth.get("username", "admin"), challenge, payload.get("signature", ""))

            def mutator(current):
                current = normalize_auth_state(current)
                for item in current.get("ssh_keys", []):
                    if item.get("id") == matched.get("id"):
                        item["last_used_at"] = now_ts()
                        break
                return current

            update_auth_state(mutator)
            self.send_login_json(auth.get("username", "admin"), "ed25519")
        except Exception as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})

    def router_id_from_path(self, prefix):
        suffix = self.parsed().path[len(prefix):].strip("/")
        if not suffix:
            return ""
        return urllib.parse.unquote(suffix.split("/", 1)[0])

    def ssh_page(self):
        router_id = self.router_id_from_path("/ssh/")
        if not self.require_router_access(router_id, "B"):
            return
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

    def router_notes_page(self, router_id, error="", draft=None, status=200, edit_note_id=""):
        if not self.require_router_access(router_id, "A"):
            return
        profile = self.current_account_profile(touch=False) or {}
        with self.app.conn() as conn:
            row = get_active_router(conn, router_id)
        if not row:
            self.send_text(404, "router not found")
            return
        router = router_with_access(row_to_router(row), profile)
        saved = self.query().get("saved", [""])[0]
        if (router.get("permissions") or {}).get("notes_edit") and not edit_note_id:
            edit_note_id = self.query().get("edit", [""])[0]
        else:
            edit_note_id = ""
        body = router_notes_html(
            router,
            username=(profile or {}).get("username") or current_username(),
            saved="" if error else saved,
            error=error,
            draft=draft,
            edit_note_id=edit_note_id,
        ).encode("utf-8")
        self.send_bytes(
            status,
            body,
            "text/html; charset=utf-8",
            [("Set-Cookie", current_router_cookie(router_id))],
        )

    def router_note_media_page(self, path):
        parts = path.strip("/").split("/")
        if len(parts) != 5 or parts[0] != "router" or parts[2] != "notes-media":
            self.send_text(404, "not found")
            return
        router_id = urllib.parse.unquote(parts[1])
        note_id = urllib.parse.unquote(parts[3])
        file_name = Path(urllib.parse.unquote(parts[4])).name
        if not self.require_router_access(router_id, "A"):
            return
        with self.app.conn() as conn:
            row = get_active_router(conn, router_id)
        if not row:
            self.send_text(404, "router not found")
            return
        entries = parse_router_notes_entries(row["notes"] or "", fallback_ts=row["updated_at"] or row["created_at"] or 0)
        image_meta = {}
        for item in entries:
            if str(item.get("id") or "") != note_id:
                continue
            for image in list(item.get("images") or []):
                if Path(str(image.get("file_name") or "")).name == file_name:
                    image_meta = image
                    break
            break
        if not image_meta:
            self.send_text(404, "image not found")
            return
        file_path = router_note_image_path(router_id, note_id, file_name)
        try:
            body = file_path.read_bytes()
        except FileNotFoundError:
            self.send_text(404, "image file not found")
            return
        self.send_bytes(
            200,
            body,
            str(image_meta.get("content_type") or "application/octet-stream"),
            [("Cache-Control", "private, max-age=3600")],
        )

    def router_notes_submit(self, router_id):
        payload = self.read_payload()
        action = str(payload.get("action") or "add").strip().lower()
        note_id = payload.get("note_id", "")
        notes = payload.get("note", payload.get("notes", ""))
        if action == "delete":
            if not self.owner_ok():
                self.router_notes_page(
                    router_id,
                    error="Удаление заметок доступно только владельцу.",
                    draft=notes,
                    status=403,
                )
                return
        elif not self.require_router_access(router_id, "G"):
            return
        uploads = payload_files(payload, "images") if action == "add" else []
        try:
            with self.app.conn() as conn:
                if action == "delete":
                    delete_router_note_entry(conn, router_id, note_id)
                    saved = "deleted"
                elif action == "update":
                    update_router_note_entry(conn, router_id, note_id, notes)
                    saved = "updated"
                else:
                    append_router_note(conn, router_id, notes, image_uploads=uploads)
                    saved = "added"
        except Exception as exc:
            self.router_notes_page(
                router_id,
                error=str(exc),
                draft=notes,
                status=400,
                edit_note_id=note_id if action == "update" else "",
            )
            return
        self.redirect(f"/router/{urllib.parse.quote(router_id)}/notes?saved={urllib.parse.quote(saved)}")

    def vps_terminal_page(self):
        if not self.require_owner():
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
        if self.ssh_token_ok(router_id):
            allowed = True
        elif is_vps_terminal_id(router_id):
            allowed = self.owner_ok()
        else:
            allowed = self.router_access_allowed(router_id, "B")
        if not allowed:
            self.send_response(403 if self.admin_ok() else 401)
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
        if self.ssh_token_ok(router_id):
            allowed = True
        elif is_vps_terminal_id(router_id):
            allowed = self.owner_ok()
        else:
            allowed = self.router_access_allowed(router_id, "B")
        if not allowed:
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

    def router_ssh_tcp_status(self, row, timeout=2):
        port = int(row["ssh_entry_port"] or 0) if row else 0
        if port <= 0:
            return False, 0, "router has no ssh_entry_port"
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=float(timeout)):
                pass
            return True, port, ""
        except Exception as exc:
            return False, port, str(exc)

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

    def wait_router_heartbeat(self, router_id, last_seen_before=0, timeout=6):
        deadline = time.time() + max(float(timeout), 0.0)
        seen_before = int(last_seen_before or 0)
        while time.time() <= deadline:
            with self.app.conn() as conn:
                row = get_active_router(conn, router_id)
            if row:
                try:
                    last_seen = int(row["last_seen"] or 0)
                except (TypeError, ValueError):
                    last_seen = 0
                if last_seen > seen_before:
                    return True, row
            time.sleep(1)
        with self.app.conn() as conn:
            row = get_active_router(conn, router_id)
        return False, row

    def apply_router_rebind_via_ssh(self, row, ssh_password="", request_origin=""):
        router_id = row_str_value(row, "id")
        last_seen_before = row_int_value(row, "last_seen", 0)
        tcp_ok, port, tcp_error = self.router_ssh_tcp_status(row)
        result = {
            "id": router_id,
            "name": row_str_value(row, "name", router_id),
            "port": port,
            "tcp_ok": bool(tcp_ok),
            "status": "pending",
            "heartbeat_confirmed": False,
            "error": "",
        }
        if not tcp_ok:
            result["status"] = "unreachable"
            result["error"] = tcp_error or "ssh tunnel is unreachable"
            return result
        hub_update, hub_url, config_vps_host = router_current_hub_bundle(
            row,
            self.app.public_url,
            request_origin,
            self.headers.get("Host", ""),
        )
        script = make_openwrt_ssh_apply_script(
            row,
            hub_url,
            vps_host_override=config_vps_host,
            public_url_override=hub_update.get("public_url", ""),
        )
        try:
            output = self.run_router_ssh_script(
                row,
                script,
                timeout=12,
                ssh_password=ssh_password,
            ).strip()
            result["status"] = "applied"
            if output:
                result["output"] = output
        except Exception as exc:
            result["status"] = "failed"
            result["error"] = str(exc)
            return result
        confirmed, fresh_row = self.wait_router_heartbeat(router_id, last_seen_before=last_seen_before, timeout=6)
        result["heartbeat_confirmed"] = bool(confirmed)
        result["status"] = "confirmed" if confirmed else "applied"
        if fresh_row:
            result["last_seen"] = row_int_value(fresh_row, "last_seen", 0)
        return result

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

    def discover_router_client_traffic(self, row, ssh_password=""):
        script = r"""
set -eu
tmp_clients="${TMPDIR:-/tmp}/owrt-clients.$$.txt"
tmp_nft="${TMPDIR:-/tmp}/owrt-nft.$$.txt"
tmp_conntrack="${TMPDIR:-/tmp}/owrt-conntrack.$$.txt"
tmp_known_ips="${TMPDIR:-/tmp}/owrt-client-ips.$$.txt"
tmp_rules="${TMPDIR:-/tmp}/owrt-client-rules.$$.txt"
cleanup() {
  rm -f "$tmp_clients" "$tmp_nft" "$tmp_conntrack" "$tmp_known_ips" "$tmp_rules"
}
trap cleanup EXIT INT TERM
: >"$tmp_clients"
: >"$tmp_nft"
: >"$tmp_conntrack"
: >"$tmp_known_ips"
: >"$tmp_rules"

if [ -f /tmp/dhcp.leases ]; then
  while read -r lease_ts mac ip host client_id; do
    [ -n "${mac:-}" ] || continue
    [ "${mac:-}" = "*" ] && continue
    [ "${host:-}" = "*" ] && host=""
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$mac" "${ip:-}" "${host:-}" "" "" "dhcp" >>"$tmp_clients"
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
      printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$mac_item" "" "${host_static:-}" "${ip_static:-}" "" "static-dhcp" >>"$tmp_clients"
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
      if (mac != "") print mac, ip, "", "", dev, "ip-neigh";
    }
  ' >>"$tmp_clients"
fi

if [ -r /proc/net/arp ]; then
  awk 'NR > 1 && $4 != "" && $4 != "00:00:00:00:00:00" {
    printf "%s\t%s\t\t\t%s\tarp\n", $4, $1, $6
  }' /proc/net/arp >>"$tmp_clients"
fi

awk -F '\t' '
{
  if ($2 != "") print $2;
  if ($4 != "") print $4;
}
' "$tmp_clients" | awk '!seen[$0]++ && $0 ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ { print }' >"$tmp_known_ips"

traffic_source="none"
has_nft=0
has_conntrack=0
if command -v nft >/dev/null 2>&1; then
  nft add table inet owrt_traffic >/dev/null 2>&1 || true
  nft 'add chain inet owrt_traffic client_prerouting { type filter hook prerouting priority filter; policy accept; }' >/dev/null 2>&1 || true
  nft 'add chain inet owrt_traffic client_postrouting { type filter hook postrouting priority filter; policy accept; }' >/dev/null 2>&1 || true
  nft list table inet owrt_traffic >"$tmp_rules" 2>/dev/null || true
  while IFS= read -r client_ip; do
    [ -n "${client_ip:-}" ] || continue
    grep -Fq "owrt-tx:$client_ip" "$tmp_rules" || nft add rule inet owrt_traffic client_prerouting ip saddr "$client_ip" counter comment "owrt-tx:$client_ip" >/dev/null 2>&1 || true
    grep -Fq "owrt-rx:$client_ip" "$tmp_rules" || nft add rule inet owrt_traffic client_postrouting ip daddr "$client_ip" counter comment "owrt-rx:$client_ip" >/dev/null 2>&1 || true
  done <"$tmp_known_ips"
  if nft list table inet owrt_traffic >"$tmp_nft" 2>/dev/null; then
    has_nft=1
  fi
fi

if command -v conntrack >/dev/null 2>&1; then
  if conntrack -L -o extended >"$tmp_conntrack" 2>/dev/null; then
    has_conntrack=1
    traffic_source="conntrack"
  fi
fi

if [ "$has_conntrack" -eq 0 ]; then
  for candidate in /proc/net/nf_conntrack /proc/net/ip_conntrack; do
    if [ -r "$candidate" ]; then
      cat "$candidate" >"$tmp_conntrack"
      has_conntrack=1
      traffic_source="$(basename "$candidate")"
      break
    fi
  done
fi

if [ "$has_nft" -eq 1 ] && [ "$has_conntrack" -eq 1 ]; then
  traffic_source="nftables+${traffic_source}"
elif [ "$has_nft" -eq 1 ]; then
  traffic_source="nftables"
fi

printf 'META\ttraffic_source\t%s\n' "${traffic_source:-none}"

awk -F '\t' -v nft_file="$tmp_nft" -v conntrack_file="$tmp_conntrack" -v has_nft="$has_nft" -v has_conntrack="$has_conntrack" -v traffic_source="${traffic_source:-none}" '
function append_unique(existing, value, arr, idx) {
  if (value == "") return existing;
  if (existing == "") return value;
  split(existing, arr, /, /);
  for (idx in arr) if (arr[idx] == value) return existing;
  return existing ", " value;
}
FNR == NR {
  mac=toupper($1);
  ip=$2;
  host=$3;
  static_ip=$4;
  iface=$5;
  source=$6;
  if (mac == "") next;
  if (!(mac in seen)) {
    order[++count]=mac;
    seen[mac]=1;
  }
  if (ip != "" && ips[mac] == "") ips[mac]=ip;
  if (host != "" && hosts[mac] == "") hosts[mac]=host;
  if (static_ip != "" && statics[mac] == "") statics[mac]=static_ip;
  if (iface != "" && ifaces[mac] == "") ifaces[mac]=iface;
  sources[mac]=append_unique(sources[mac], source);
  if (ip != "") mac_by_ip[ip]=mac;
  if (static_ip != "") mac_by_ip[static_ip]=mac;
  next;
}
END {
  flow_hits=0;
  if ((has_nft + 0) > 0) {
    while ((getline line < nft_file) > 0) {
      bytes=0;
      ip="";
      dir="";
      quoted_count=split(line, quoted, "\"");
      for (q=1; q<=quoted_count; q++) {
        if (quoted[q] ~ /^owrt-(tx|rx):/) {
          marker_count=split(quoted[q], marker, ":");
          if (marker_count >= 2) {
            if (marker[1] == "owrt-tx") dir="tx";
            else if (marker[1] == "owrt-rx") dir="rx";
            ip=marker[2];
          }
          break;
        }
      }
      if (dir == "" || ip == "" || !(ip in mac_by_ip)) continue;
      n=split(line, parts, /[[:space:]]+/);
      for (i=1; i<=n; i++) {
        if (parts[i] == "bytes" && i < n) {
          bytes=parts[i + 1] + 0;
          break;
        }
      }
      if (dir == "tx") tx_nft[mac_by_ip[ip]] = bytes;
      else if (dir == "rx") rx_nft[mac_by_ip[ip]] = bytes;
      flow_hits++;
    }
    close(nft_file);
  }
  if ((has_conntrack + 0) > 0) {
    while ((getline line < conntrack_file) > 0) {
      src1=dst1=src2=dst2="";
      bytes1=bytes2=0;
      src_count=0;
      dst_count=0;
      bytes_count=0;
      n=split(line, parts, /[[:space:]]+/);
      for (i=1; i<=n; i++) {
        token=parts[i];
        if (token ~ /^src=/) {
          src_count++;
          if (src_count == 1) src1=substr(token, 5);
          else if (src_count == 2) src2=substr(token, 5);
        } else if (token ~ /^dst=/) {
          dst_count++;
          if (dst_count == 1) dst1=substr(token, 5);
          else if (dst_count == 2) dst2=substr(token, 5);
        } else if (token ~ /^bytes=/) {
          bytes_count++;
          if (bytes_count == 1) bytes1=substr(token, 7) + 0;
          else if (bytes_count == 2) bytes2=substr(token, 7) + 0;
        }
      }
      if (bytes1 > 0) {
        if (src1 in mac_by_ip) tx_conntrack[mac_by_ip[src1]] += bytes1;
        if (dst1 in mac_by_ip) rx_conntrack[mac_by_ip[dst1]] += bytes1;
        flow_hits++;
      }
      if (bytes2 > 0) {
        if (src2 in mac_by_ip) tx_conntrack[mac_by_ip[src2]] += bytes2;
        if (dst2 in mac_by_ip) rx_conntrack[mac_by_ip[dst2]] += bytes2;
        flow_hits++;
      }
    }
    close(conntrack_file);
  }
  printf "META\tflow_hits\t%d\n", flow_hits + 0;
  for (i=1; i<=count; i++) {
    mac=order[i];
    rx_value=rx_nft[mac] + 0;
    tx_value=tx_nft[mac] + 0;
    if ((rx_conntrack[mac] + 0) > rx_value) rx_value=rx_conntrack[mac] + 0;
    if ((tx_conntrack[mac] + 0) > tx_value) tx_value=tx_conntrack[mac] + 0;
    total=rx_value + tx_value;
    printf "CLIENT\t%s\t%s\t%s\t%s\t%s\t%s\t%.0f\t%.0f\t%.0f\n",
      mac, ips[mac], hosts[mac], statics[mac], ifaces[mac], sources[mac], rx_value, tx_value, total;
  }
}
' "$tmp_clients"
"""
        raw = self.run_router_ssh_script(row, script, timeout=28, ssh_password=ssh_password)
        clients = []
        traffic_source = "none"
        flow_hits = 0
        for line in raw.splitlines():
            if not line.strip():
                continue
            parts = [part.strip() for part in line.split("\t")]
            if not parts:
                continue
            kind = parts[0]
            if kind == "META" and len(parts) >= 3:
                if parts[1] == "traffic_source":
                    traffic_source = parts[2] or "none"
                elif parts[1] == "flow_hits":
                    try:
                        flow_hits = int(parts[2] or 0)
                    except ValueError:
                        flow_hits = 0
                continue
            if kind != "CLIENT":
                continue
            while len(parts) < 10:
                parts.append("")
            mac = self.normalize_wol_mac(parts[1])
            if not mac:
                continue
            ip_addr = parts[2]
            name = parts[3]
            static_ip = parts[4]
            iface = parts[5]
            source = parts[6]
            try:
                rx_bytes = int(float(parts[7] or 0))
            except ValueError:
                rx_bytes = 0
            try:
                tx_bytes = int(float(parts[8] or 0))
            except ValueError:
                tx_bytes = 0
            try:
                total_bytes = int(float(parts[9] or (rx_bytes + tx_bytes)))
            except ValueError:
                total_bytes = rx_bytes + tx_bytes
            clients.append(
                {
                    "mac": mac,
                    "ip": ip_addr,
                    "name": name,
                    "static_ip": static_ip,
                    "iface": iface,
                    "source": source,
                    "rx_bytes": rx_bytes,
                    "tx_bytes": tx_bytes,
                    "total_bytes": total_bytes,
                    "has_static": bool(static_ip),
                }
            )
        clients = accumulate_traffic_clients(row["id"], clients)
        clients.sort(
            key=lambda item: (
                -int(item.get("total_bytes") or 0),
                str(item.get("name") or "").lower(),
                str(item.get("ip") or ""),
                str(item.get("mac") or ""),
            )
        )
        return {
            "clients": clients,
            "traffic_source": traffic_source,
            "traffic_supported": traffic_source != "none",
            "flow_hits": flow_hits,
        }

    def reset_router_client_traffic(self, row, ssh_password=""):
        script = r"""
set -eu

install_conntrack() {
  pkg=""
  if command -v opkg >/dev/null 2>&1; then
    opkg update >/dev/null 2>&1 || true
    for pkg in conntrack conntrack-tools; do
      opkg install "$pkg" >/dev/null 2>&1 && return 0
    done
  fi
  if command -v apk >/dev/null 2>&1; then
    apk update >/dev/null 2>&1 || true
    for pkg in conntrack conntrack-tools; do
      apk add "$pkg" >/dev/null 2>&1 && return 0
    done
  fi
  return 1
}

did_reset=0
conntrack_failed=0

if command -v nft >/dev/null 2>&1; then
  nft delete table inet owrt_traffic >/dev/null 2>&1 || true
  did_reset=1
fi

if ! command -v conntrack >/dev/null 2>&1; then
  install_conntrack || true
fi

if ! command -v conntrack >/dev/null 2>&1; then
  echo "Не удалось найти или установить conntrack на роутере. Попробуй вручную: opkg install conntrack" >&2
  exit 127
fi

if ! conntrack -F >/dev/null 2>&1; then
  echo "Команда conntrack найдена, но очистить таблицу не удалось." >&2
  exit 1
fi

echo "Трафик сброшен. Conntrack очищен, новые счётчики начнут копиться заново."
"""
        output = self.run_router_ssh_script(row, script, timeout=75, ssh_password=ssh_password).strip()
        return {
            "message": output or "Трафик сброшен. Conntrack очищен, новые счётчики начнут копиться заново."
        }

    def reset_router_client_traffic(self, row, ssh_password=""):
        script = r"""
set -eu

install_conntrack() {
  pkg=""
  if command -v opkg >/dev/null 2>&1; then
    opkg update >/dev/null 2>&1 || true
    for pkg in conntrack conntrack-tools; do
      opkg install "$pkg" >/dev/null 2>&1 && return 0
    done
  fi
  if command -v apk >/dev/null 2>&1; then
    apk update >/dev/null 2>&1 || true
    for pkg in conntrack conntrack-tools; do
      apk add "$pkg" >/dev/null 2>&1 && return 0
    done
  fi
  return 1
}

did_reset=0
conntrack_failed=0

if command -v nft >/dev/null 2>&1; then
  nft delete table inet owrt_traffic >/dev/null 2>&1 || true
  did_reset=1
fi

if ! command -v conntrack >/dev/null 2>&1; then
  install_conntrack || true
fi

if command -v conntrack >/dev/null 2>&1; then
  if conntrack -F >/dev/null 2>&1; then
    did_reset=1
  else
    conntrack_failed=1
  fi
fi

[ "$did_reset" -eq 1 ] || {
  echo "Failed to reset traffic counters: neither conntrack nor nftables could be cleared." >&2
  exit 1
}

if [ "$conntrack_failed" -eq 1 ]; then
  echo "Traffic counters partially reset: nft client counters were cleared, but conntrack flush failed."
else
  echo "Traffic counters reset: conntrack flushed and nft client counters cleared."
fi
"""
        output = self.run_router_ssh_script(row, script, timeout=75, ssh_password=ssh_password).strip()
        clear_traffic_counters(row["id"])
        return {
            "message": output or "Traffic counters reset: conntrack flushed and nft client counters cleared."
        }

    def ensure_router_wol_support(self, row, ssh_password=""):
        script = r"""
set -eu

has_wol_tool() {
  command -v etherwake >/dev/null 2>&1 || command -v wakeonlan >/dev/null 2>&1 || command -v wol >/dev/null 2>&1
}

install_pkg() {
  pkg="$1"
  case "$PKG_MANAGER" in
    opkg)
      opkg install "$pkg" >/dev/null 2>&1
      ;;
    apk)
      apk add "$pkg" >/dev/null 2>&1
      ;;
    *)
      return 1
      ;;
  esac
}

if has_wol_tool; then
  echo "WOL tool already present on router"
  exit 0
fi

PKG_MANAGER=""
if command -v apk >/dev/null 2>&1; then
  PKG_MANAGER="apk"
elif command -v opkg >/dev/null 2>&1; then
  PKG_MANAGER="opkg"
fi

[ -n "$PKG_MANAGER" ] || {
  echo "No package manager found to install WOL support." >&2
  exit 1
}

case "$PKG_MANAGER" in
  opkg)
    opkg update >/dev/null 2>&1 || true
    ;;
  apk)
    apk update >/dev/null 2>&1 || true
    ;;
esac

install_pkg luci-app-wol || true
install_pkg etherwake || true
install_pkg wakeonlan || true
install_pkg wol || true

if has_wol_tool; then
  echo "WOL support installed via $PKG_MANAGER"
  exit 0
fi

echo "WOL package installation finished, but no etherwake/wakeonlan/wol command is available." >&2
exit 1
"""
        return self.run_router_ssh_script(row, script, timeout=75, ssh_password=ssh_password).strip()

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
        try:
            output = self.run_router_ssh_script(
                row,
                script,
                [clean_mac, str(iface or "")],
                timeout=12,
                ssh_password=ssh_password,
            )
        except RuntimeError as exc:
            if "No WOL tool found on router" not in str(exc):
                raise
            install_output = self.ensure_router_wol_support(row, ssh_password=ssh_password)
            output = self.run_router_ssh_script(
                row,
                script,
                [clean_mac, str(iface or "")],
                timeout=18,
                ssh_password=ssh_password,
            )
            if install_output:
                output = (install_output + "\n" + output).strip()
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
        if self.ssh_token_ok(router_id):
            allowed = True
        elif is_vps_terminal_id(router_id):
            allowed = self.owner_ok()
        else:
            allowed = self.router_access_allowed(router_id, "B")
        if not allowed:
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
        with SSH_HTTP_LOCK:
            session = SSH_HTTP_SESSIONS.get(sid)
        if not session:
            self.send_json(404, {"ok": False, "error": "terminal session not found"})
            return
        router_id = str(session.get("router_id") or "")
        if is_vps_terminal_id(router_id):
            allowed = self.owner_ok()
        else:
            allowed = self.router_access_allowed(router_id, "B")
        if not allowed:
            self.send_json(403, {"ok": False, "error": "not authorized"})
            return
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

    def router_endpoints_rewrite(self):
        try:
            payload = self.read_payload()
            vps_host = str(payload.get("vps_host") or "").strip()
            public_url = str(payload.get("public_url") or "").strip()
            apply_live = bool(payload.get("apply_live", True))
            ssh_password = str(payload.get("ssh_password") or "")
            if not vps_host and not public_url:
                self.send_json(400, {"ok": False, "error": "vps_host or public_url is required"})
                return
            with self.app.conn() as conn:
                rewritten = rewrite_router_endpoints(
                    conn,
                    vps_host=vps_host,
                    public_url=public_url,
                    preserve_vps_fallback=bool(payload.get("preserve_vps_fallback", True)),
                )
                rows = list_router_rows(conn)
                total = len(rows)
            auto_apply = {
                "requested": bool(apply_live),
                "attempted": 0,
                "confirmed": 0,
                "applied": 0,
                "unreachable": 0,
                "failed": 0,
                "results": [],
            }
            if apply_live:
                for row in rows:
                    result = self.apply_router_rebind_via_ssh(row, ssh_password=ssh_password, request_origin=self.request_origin())
                    auto_apply["results"].append(result)
                    auto_apply["attempted"] += 1
                    status = str(result.get("status") or "")
                    if status == "confirmed":
                        auto_apply["confirmed"] += 1
                        auto_apply["applied"] += 1
                    elif status == "applied":
                        auto_apply["applied"] += 1
                    elif status == "unreachable":
                        auto_apply["unreachable"] += 1
                    else:
                        auto_apply["failed"] += 1
            add_notification(
                "router-endpoints-rewrite",
                "Router endpoints updated",
                "Hub updated router endpoint settings and tried to push the new config over SSH to reachable routers.",
                "warn",
                [
                    f"rewritten: {', '.join(f'{k}={v}' for k, v in rewritten.items()) or 'none'}",
                    f"auto-apply confirmed: {auto_apply['confirmed']}/{total}",
                    f"auto-apply applied: {auto_apply['applied']}/{total}",
                    f"auto-apply unreachable: {auto_apply['unreachable']}/{total}",
                    f"auto-apply failed: {auto_apply['failed']}/{total}",
                ],
                {"rewritten": rewritten, "routers": total, "auto_apply": auto_apply},
            )
            self.send_json(200, {"ok": True, "rewritten": rewritten, "routers": total, "auto_apply": auto_apply})
        except Exception as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})

    def do_GET(self):
        path = self.parsed().path
        if path == "/health":
            self.send_json(200, {"ok": True})
            return
        if path == "/favicon.ico":
            self.send_local_asset(FAVICON_ICO_FILE, "image/x-icon")
            return
        if path == "/favicon-32.png":
            self.send_local_asset(FAVICON_PNG_32_FILE, "image/png")
            return
        if path == "/favicon-192.png":
            self.send_local_asset(FAVICON_PNG_192_FILE, "image/png")
            return
        if path == "/favicon-512.png":
            self.send_local_asset(FAVICON_PNG_512_FILE, "image/png")
            return
        if path == "/apple-touch-icon.png":
            self.send_local_asset(APPLE_TOUCH_ICON_FILE, "image/png")
            return
        if path == "/favicon.svg":
            self.send_bytes(200, favicon_svg().encode("utf-8"), "image/svg+xml; charset=utf-8")
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
        if path == "/api/auth/meta":
            self.auth_meta()
            return
        if path == "/oauth/github/start":
            self.start_social_login("github")
            return
        if path == "/oauth/vk/start":
            self.start_social_login("vk")
            return
        if path == "/oauth/github/callback":
            self.finish_social_callback("github")
            return
        if path == "/oauth/vk/callback":
            self.finish_social_callback("vk")
            return
        if path.startswith("/.well-known/acme-challenge/"):
            self.serve_acme_challenge(path)
            return
        if path == "/login":
            self.send_bytes(200, login_html().encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/ssh-key-manual/":
            self.send_bytes(200, ssh_key_manual_html().encode("utf-8"), "text/html; charset=utf-8")
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
            profile = self.current_account_profile(touch=False)
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
                routers = self.visible_routers(conn, profile)
            self.send_bytes(
                200,
                dashboard_html(
                    routers,
                    (profile or {}).get("username") or current_username(),
                    list_hub_sessions(session_token_value, self.current_or_owner_session_scope()),
                    list_notifications(0, 40) if not profile or profile.get("is_owner") else [],
                ).encode("utf-8"),
                "text/html; charset=utf-8",
                extra_headers,
            )
            return
        if path == "/api/routers":
            with self.app.conn() as conn:
                routers = self.visible_routers(conn)
            self.send_json(200, {"routers": routers})
            return
        if path == "/api/backup/download":
            if not self.require_owner():
                return
            self.backup_download()
            return
        if path == "/api/sessions":
            self.send_json(200, {"sessions": list_hub_sessions(self.current_session_token(), self.current_or_owner_session_scope())})
            return
        if path == "/api/notifications":
            if not self.require_owner(json_mode=True):
                return
            query = self.query()
            self.send_json(
                200,
                {
                    "notifications": list_notifications(
                        query.get("after", ["0"])[0],
                        query.get("limit", ["60"])[0],
                        query.get("after_serial", ["0"])[0],
                    ),
                    "serial": latest_notification_serial(),
                },
            )
            return
        if path == "/api/notifications/wait":
            if not self.require_owner(json_mode=True):
                return
            query = self.query()
            self.send_json(
                200,
                {
                    "notifications": wait_for_notifications(
                        query.get("after_serial", ["0"])[0],
                        query.get("timeout", ["25"])[0],
                        query.get("limit", ["60"])[0],
                    ),
                    "serial": latest_notification_serial(),
                },
            )
            return
        if path == "/api/push/vapid-public-key":
            if not self.require_owner(json_mode=True):
                return
            backend = web_push_backend_status()
            if not backend.get("ready"):
                self.send_json(
                    503,
                    {
                        "ok": False,
                        "error": backend.get("error") or "На VPS не готов Web Push backend.",
                        "reason": backend.get("reason", ""),
                        "subject": backend.get("subject", ""),
                        "publicKey": backend.get("public_key", ""),
                    },
                )
                return
            self.send_json(200, {"ok": True, "publicKey": backend.get("public_key", ""), "subject": backend.get("subject", "")})
            return
        if path == "/api/push/status":
            if not self.require_owner(json_mode=True):
                return
            self.send_json(200, push_status_snapshot())
            return
        if path.startswith("/api/router/") and path.endswith("/wol/devices"):
            parts = path.strip("/").split("/")
            if len(parts) != 5 or parts[0] != "api" or parts[1] != "router" or parts[3] != "wol" or parts[4] != "devices":
                self.send_text(404, "not found")
                return
            router_id = urllib.parse.unquote(parts[2])
            if not self.require_router_access(router_id, "G", json_mode=True):
                return
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
            if "/notes-media/" in path:
                self.router_note_media_page(path)
                return
            if path.endswith("/notes"):
                self.router_notes_page(self.router_id_from_path("/router/"))
                return
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
        if path == "/api/login/passkey/begin":
            self.begin_passkey_login()
            return
        if path == "/api/login/passkey/finish":
            self.finish_passkey_login()
            return
        if path == "/api/login/ssh/begin":
            self.begin_ssh_login()
            return
        if path == "/api/login/ssh/finish":
            self.finish_ssh_login()
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
                if router and router.pop("_became_online", False):
                    notify_router_online(router)
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
                        "hub_update": canonical_router_hub_update(router, self.app.public_url, self.request_origin()),
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
            if not self.require_owner():
                return
            self.update_auth()
            return
        if path == "/api/auth/users/save":
            if not self.require_owner(json_mode=True):
                return
            self.save_delegated_user()
            return
        if path == "/api/auth/users/delete":
            if not self.require_owner(json_mode=True):
                return
            self.delete_delegated_user()
            return
        if path == "/api/auth/users/revoke-sessions":
            if not self.require_owner(json_mode=True):
                return
            self.revoke_delegated_user_sessions()
            return
        if path == "/api/auth/totp/setup":
            if not self.require_owner(json_mode=True):
                return
            self.totp_setup()
            return
        if path == "/api/auth/totp/enable":
            if not self.require_owner(json_mode=True):
                return
            self.totp_enable()
            return
        if path == "/api/auth/totp/disable":
            if not self.require_owner(json_mode=True):
                return
            self.totp_disable()
            return
        if path == "/api/auth/passkeys/begin":
            if not self.require_owner(json_mode=True):
                return
            self.begin_passkey_registration()
            return
        if path == "/api/auth/passkeys/finish":
            if not self.require_owner(json_mode=True):
                return
            self.finish_passkey_registration()
            return
        if path == "/api/auth/passkeys/remove":
            if not self.require_owner(json_mode=True):
                return
            self.remove_passkey()
            return
        if path == "/api/auth/ssh-keys/add":
            if not self.require_owner(json_mode=True):
                return
            self.add_ssh_key()
            return
        if path == "/api/auth/ssh-keys/remove":
            if not self.require_owner(json_mode=True):
                return
            self.remove_ssh_key()
            return
        if path == "/api/auth/social/github/save":
            if not self.require_owner(json_mode=True):
                return
            self.save_social_provider_config("github")
            return
        if path == "/api/auth/social/vk/save":
            if not self.require_owner(json_mode=True):
                return
            self.save_social_provider_config("vk")
            return
        if path == "/api/auth/social/github/link":
            if not self.require_owner(json_mode=True):
                return
            self.begin_social_link("github")
            return
        if path == "/api/auth/social/vk/link":
            if not self.require_owner(json_mode=True):
                return
            self.begin_social_link("vk")
            return
        if path == "/api/auth/social/github/unlink":
            if not self.require_owner(json_mode=True):
                return
            self.unlink_social_account("github")
            return
        if path == "/api/auth/social/vk/unlink":
            if not self.require_owner(json_mode=True):
                return
            self.unlink_social_account("vk")
            return
        if path == "/api/session/client-hint":
            payload = self.read_payload()
            session = update_hub_session_client_hint(self.current_session_token(), payload.get("client_hint", ""))
            if not session:
                self.send_json(400, {"ok": False, "error": "session not found"})
                return
            self.send_json(200, {"ok": True, "client": session.get("client", ""), "hint": normalize_client_hint(payload.get("client_hint", ""))})
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
            if not self.owner_ok():
                session_allowed = next(
                    (
                        session
                        for session in list_hub_sessions(self.current_session_token(), self.current_or_owner_session_scope())
                        if session.get("id") == session_id
                    ),
                    None,
                )
                if not session_allowed:
                    self.send_json(403, {"ok": False, "error": "not authorized"})
                    return
            removed = revoke_hub_session(session_id=session_id)
            self.send_json(200, {"ok": True, "removed": removed})
            return
        if path == "/api/session/revoke-others":
            current = self.current_hub_session(touch=False)
            current_id = current.get("id", "") if current else ""
            removed = 0
            for session in list_hub_sessions(self.current_session_token(), self.current_or_owner_session_scope()):
                if session.get("id") != current_id:
                    removed += revoke_hub_session(session_id=session.get("id", ""))
            self.send_json(200, {"ok": True, "removed": removed})
            return
        if path == "/api/notifications/clear":
            if not self.require_owner(json_mode=True):
                return
            clear_notifications()
            self.send_json(200, {"ok": True})
            return
        if path == "/api/backup/restore":
            if not self.require_owner(json_mode=True):
                return
            self.backup_restore()
            return
        if path == "/api/routers/rewrite-endpoints":
            if not self.require_owner(json_mode=True):
                return
            self.router_endpoints_rewrite()
            return
        if path == "/api/push/subscribe":
            if not self.require_owner(json_mode=True):
                return
            payload = {}
            session = self.current_hub_session(touch=False) or {}
            username = session.get("username", current_username())
            user_agent = self.headers.get("User-Agent", "")
            client_ip = self.client_ip()
            try:
                payload = self.read_payload()
                subscription = save_push_subscription(
                    payload,
                    username,
                    client_ip,
                    user_agent,
                )
                test_payload = {
                    "title": APP_NAME,
                    "body": "Push включён на этом устройстве.",
                    "tag": "owrt-push-test",
                    "url": "/",
                    "kind": "push-test",
                    "ts": now_ts(),
                }
                test_payload["web_push"] = 8030
                test_payload["notification"] = {
                    "title": test_payload["title"],
                    "body": test_payload["body"],
                    "tag": test_payload["tag"],
                    "navigate": test_payload["url"],
                    "silent": False,
                }
                result = send_web_push(subscription, test_payload)
                remember_push_delivery(
                    test_payload,
                    [
                        {
                            "client": subscription.get("client") or "браузер",
                            "endpoint_host": push_endpoint_host(subscription.get("endpoint", "")),
                            "result": result,
                        }
                    ],
                    "ok" if result == "ok" else "partial",
                )
                if result == "gone":
                    remove_push_subscription(subscription.get("endpoint", ""))
                if result != "ok":
                    self.send_json(503, {"ok": False, "error": push_result_message(result, subscription), "delivery": result, "subscription": {"id": subscription.get("id"), "client": subscription.get("client")}})
                    return
                self.send_json(200, {"ok": True, "delivery": result, "subscription": {"id": subscription.get("id"), "client": subscription.get("client")}})
            except Exception as exc:
                detail = " ".join(str(exc).split())[:160] or exc.__class__.__name__
                client = client_label(user_agent, normalize_client_hint((payload or {}).get("client_hint", "")))
                remember_push_delivery(
                    {
                        "title": APP_NAME,
                        "body": "Не удалось сохранить push-подписку.",
                        "tag": "owrt-push-test",
                        "url": "/",
                        "kind": "push-test",
                        "ts": now_ts(),
                    },
                    [
                        {
                            "client": client or "браузер",
                            "endpoint_host": push_endpoint_host((payload or {}).get("endpoint", "")),
                            "result": f"subscribe_error:{detail}",
                        }
                    ],
                    "subscribe_error",
                )
                self.log_message("push subscribe error: %s", detail)
                self.send_json(400, {"ok": False, "error": str(exc)})
            return
        if path == "/api/push/unsubscribe":
            if not self.require_owner(json_mode=True):
                return
            try:
                payload = self.read_payload()
                removed = remove_push_subscription(payload.get("endpoint", ""))
                self.send_json(200, {"ok": True, "removed": removed})
            except Exception as exc:
                self.send_json(400, {"ok": False, "error": str(exc)})
            return
        if path == "/api/xray/reload":
            if not self.require_owner(json_mode=True):
                return
            try:
                result = reload_vps_xray(self.app.db_path)
                self.send_json(200, {"ok": True, **result})
            except Exception as exc:
                self.send_text(500, str(exc))
            return
        if path == "/api/xray/restart":
            if not self.require_owner(json_mode=True):
                return
            try:
                result = restart_vps_xray()
                self.send_json(200, {"ok": True, **result})
            except Exception as exc:
                self.send_text(500, str(exc))
            return
        if path == "/api/router":
            if not self.require_add_router(json_mode=True):
                return
            try:
                current = self.current_account_profile(touch=False) or {}
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
                if current and not current.get("is_owner"):
                    grant_delegated_router_flag(load_auth(), current.get("username", ""), router_id, "G")
                self.send_json(200, {"ok": True, "router": router, "auth": self.build_auth_meta()})
            except Exception as exc:
                self.send_text(400, str(exc))
            return
        if path.startswith("/api/router/") and path.endswith("/wol/devices"):
            parts = path.strip("/").split("/")
            if len(parts) != 5 or parts[0] != "api" or parts[1] != "router" or parts[3] != "wol" or parts[4] != "devices":
                self.send_text(404, "not found")
                return
            router_id = urllib.parse.unquote(parts[2])
            if not self.require_router_access(router_id, "G", json_mode=True):
                return
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
        if path.startswith("/api/router/") and path.endswith("/traffic/clients"):
            parts = path.strip("/").split("/")
            if len(parts) != 5 or parts[0] != "api" or parts[1] != "router" or parts[3] != "traffic" or parts[4] != "clients":
                self.send_text(404, "not found")
                return
            router_id = urllib.parse.unquote(parts[2])
            if not self.require_router_access(router_id, "G", json_mode=True):
                return
            try:
                payload = self.read_payload()
                with self.app.conn() as conn:
                    row = get_active_router(conn, router_id)
                if not row:
                    self.send_json(404, {"ok": False, "error": "router not found"})
                    return
                result = self.discover_router_client_traffic(row, payload.get("ssh_password", ""))
                self.send_json(200, {"ok": True, "router_id": router_id, **result})
            except Exception as exc:
                self.send_json(
                    400,
                    {
                        "ok": False,
                        "error": str(exc),
                        "router_id": router_id,
                        "clients": [],
                        "traffic_source": "none",
                        "traffic_supported": False,
                        "flow_hits": 0,
                    },
                )
            return
        if path.startswith("/api/router/") and path.endswith("/traffic/reset"):
            parts = path.strip("/").split("/")
            if len(parts) != 5 or parts[0] != "api" or parts[1] != "router" or parts[3] != "traffic" or parts[4] != "reset":
                self.send_text(404, "not found")
                return
            router_id = urllib.parse.unquote(parts[2])
            if not self.require_router_access(router_id, "G", json_mode=True):
                return
            try:
                payload = self.read_payload()
                with self.app.conn() as conn:
                    row = get_active_router(conn, router_id)
                if not row:
                    self.send_json(404, {"ok": False, "error": "router not found"})
                    return
                result = self.reset_router_client_traffic(row, payload.get("ssh_password", ""))
                self.send_json(200, {"ok": True, "router_id": router_id, **result})
            except Exception as exc:
                self.send_json(400, {"ok": False, "error": str(exc), "router_id": router_id})
            return
        if path.startswith("/api/router/") and path.endswith("/wol"):
            parts = path.strip("/").split("/")
            if len(parts) != 4 or parts[0] != "api" or parts[1] != "router" or parts[3] != "wol":
                self.send_text(404, "not found")
                return
            router_id = urllib.parse.unquote(parts[2])
            if not self.require_router_access(router_id, "G", json_mode=True):
                return
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
                if not self.require_router_access(router_id, "G", json_mode=True):
                    return
                payload = self.read_payload()
                with self.app.conn() as conn:
                    row = rename_router(conn, router_id, payload.get("name"))
                self.send_json(200, {"ok": True, "router": row_to_router(row)})
            except Exception as exc:
                self.send_text(400, str(exc))
            return
        if path.startswith("/api/router/") and path.endswith("/notes"):
            try:
                router_id = urllib.parse.unquote(path.split("/")[3])
                if not self.require_router_access(router_id, "G", json_mode=True):
                    return
                payload = self.read_payload()
                with self.app.conn() as conn:
                    row = update_router_notes(conn, router_id, payload.get("notes", ""))
                router = router_with_access(row_to_router(row), self.current_account_profile())
                self.send_json(200, {"ok": True, "router": router})
            except Exception as exc:
                self.send_text(400, str(exc))
            return
        if path.startswith("/router/") and path.endswith("/notes"):
            self.router_notes_submit(self.router_id_from_path("/router/"))
            return
        if path.startswith("/api/router/") and path.endswith("/delete"):
            router_id = urllib.parse.unquote(path.split("/")[3])
            if not self.require_owner(json_mode=True):
                return
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
        if asset == "config":
            if not self.require_router_access(router_id, "G"):
                return
        elif asset == "xray-client.json":
            if not self.require_router_access(router_id, "G", json_mode=True):
                return
        else:
            self.send_text(404, "not found")
            return
        with self.app.conn() as conn:
            row = get_active_router(conn, router_id)
        if not row:
            self.send_text(404, "router not found")
            return
        hub_update, hub_url, config_vps_host = router_current_hub_bundle(
            row,
            self.app.public_url,
            self.request_origin(),
            self.headers.get("Host", ""),
        )
        if asset == "config":
            self.send_text(
                200,
                make_openwrt_config(
                    row,
                    hub_url,
                    vps_host_override=config_vps_host,
                    public_url_override=hub_update.get("public_url", ""),
                ),
            )
            return
        if asset == "xray-client.json":
            self.send_json(200, make_router_xray_config(row))
            return
        self.send_text(404, "not found")

    def proxy_access(self, path):
        parts = path.split("/", 3)
        if len(parts) < 3 or not parts[2]:
            self.redirect("/")
            return
        router_id = urllib.parse.unquote(parts[2])
        if not self.require_router_access(router_id, "A"):
            return
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
    hub_url = args.hub_url or router_fallback_hub_url(row, args.vps_host, args.port)
    print(make_openwrt_config(row, hub_url), end="")


def cmd_backup(args):
    out = Path(args.out) if args.out else STATE_DIR / backup_filename()
    result = create_hub_backup(out, args.db)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_restore(args):
    result = restore_hub_backup(args.file, args.db, args.vps_host, args.public_url)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("Restart Hub after restore: systemctl restart owrt-remote")


def cmd_rewrite_endpoints(args):
    with connect(args.db) as conn:
        init_db(conn)
        rewritten = rewrite_router_endpoints(
            conn,
            args.vps_host,
            args.public_url,
            preserve_vps_fallback=bool(args.preserve_vps_fallback),
        )
        total = conn.execute("select count(*) from routers where deleted_at = 0").fetchone()[0]
    print(json.dumps({"ok": True, "rewritten": rewritten, "routers": total}, ensure_ascii=False, indent=2))


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
    app.start_router_state_monitor()
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
        app.stop_router_state_monitor()
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

    rewrite = sub.add_parser("rewrite-endpoints", help="rewrite active router endpoint settings in Hub DB")
    rewrite.add_argument("--vps-host", default="", help="new VPS host/IP for active routers")
    rewrite.add_argument("--public-url", default="", help="new public Hub URL for active routers")
    rewrite.add_argument(
        "--preserve-vps-fallback",
        action="store_true",
        help="keep current router vps_host values when public_url and vps_host point to the same host, preserving old IP fallback during same-VPS domain migration",
    )
    rewrite.set_defaults(func=cmd_rewrite_endpoints)

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
