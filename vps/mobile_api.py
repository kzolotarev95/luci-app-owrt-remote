"""Native client authentication over the Hub's existing cookie sessions.

The Hub calls dispatch_get/dispatch_post before its HTML authentication guard.
This module does not grant access to any existing router or owner-only API.
"""

import hashlib
import hmac
import json
import re
import secrets
import sys
import urllib.parse


BASE = "/api/mobile/v1"
CSRF_HEADER = "X-Mobile-CSRF"
MAX_LOGIN_BODY = 8192
ENDPOINTS = {
    "challenge": BASE + "/login/challenge",
    "login": BASE + "/login",
    "session": BASE + "/session",
    "logout": BASE + "/logout",
}
GET_PATHS = {BASE + "/capabilities", ENDPOINTS["challenge"], ENDPOINTS["session"]}
POST_PATHS = {ENDPOINTS["login"], ENDPOINTS["logout"]}
NOTE_PATH = re.compile(re.escape(BASE) + r"/router/([^/]+)/notes$")
CONFIG_PATH = re.compile(re.escape(BASE) + r"/router/([^/]+)/config$")


def _hub(handler):
    # The real Handler belongs to the running Hub module; subclasses in HTTP
    # fixtures or local extensions can belong to another module.
    for cls in type(handler).__mro__:
        module = sys.modules.get(cls.__module__)
        if module is not None and hasattr(module, "verify_password_login"):
            return module
    raise RuntimeError("mobile API requires the Hub request handler")


def _error(handler, status, code, message, **extra):
    handler.send_json(status, {"ok": False, "code": code, "error": message, **extra})


def _same_origin(handler):
    # Native requests have no browser Origin. A browser must use this Hub's
    # origin; no CORS response headers are introduced by this module.
    origin = handler.headers.get("Origin")
    if origin and origin != handler.request_origin():
        _error(handler, 403, "origin_forbidden", "Request origin is not allowed")
        return False
    return True


def _captcha(hub):
    auth = hub.normalize_auth_state(hub.load_auth())
    return auth, hub.effective_captcha_state(auth)


def _csrf(handler, session):
    # Domain separation keeps this value distinct from the authentication
    # cookie. The cookie itself, its stored hash and signing keys stay private.
    context = ("owrt-mobile-v1:logout:" + str(session.get("id") or "")).encode("utf-8")
    return hmac.new(handler.current_session_token().encode("utf-8"), context, hashlib.sha256).hexdigest()


def _current(handler):
    # Require a revocable Hub session, rather than accepting the legacy global
    # cookie or the router agent's Bearer token as a native application login.
    session = handler.current_hub_session()
    profile = handler.current_account_profile(touch=False) if session else None
    if not session or not profile or session.get("username") != profile.get("username"):
        _error(handler, 401, "not_authenticated", "Sign in to Hub")
        return None, None
    return session, profile


def _auth_meta(hub, auth, profile):
    public = hub.public_auth_meta(auth)
    captcha = public.get("captcha") or {}
    result = {
        "username": profile.get("username", ""),
        "totp_enabled": bool(public.get("totp_enabled")) if profile.get("is_owner") else False,
        "captcha": {"mode": captcha.get("mode", "digits")},
    }
    if profile.get("is_owner"):
        for field in ("passkeys_supported", "passkey_count", "passkey_invalid_count", "ssh_key_count", "ed25519_enabled"):
            result[field] = public.get(field, False if field.endswith(("supported", "enabled")) else 0)
    return result


def _session_payload(handler, hub, session, profile):
    owner = bool(profile.get("is_owner"))
    can_add = bool(hub.account_can_add_router(profile))
    links = {
        "dashboard": "/",
        "routers": "/api/routers",
        "router_groups": "/api/router-groups",
        "sessions": "/api/sessions",
        "vps_resources": "/api/vps/resources",
    }
    if can_add:
        links["router_add"] = "/api/router"
    if owner:
        links.update({
            "notifications": "/api/notifications",
            "hub_update": "/api/hub-update",
            "router_updates": "/api/router-updates",
            "vps_terminal": "/vps-terminal/",
        })
    csrf = {}
    csrf_headers = {}
    if owner or any("G" in flags for flags in (profile.get("router_flags") or {}).values()):
        csrf["xray_control"] = handler.app.xray_control_csrf
        csrf_headers["xray_control"] = "X-Xray-Control-CSRF"
    if owner:
        csrf.update({"hub_update": handler.app.hub_update.csrf, "router_updates": handler.app.router_updates.csrf})
        csrf_headers.update({"hub_update": "X-Hub-Update-CSRF", "router_updates": "X-Router-Update-CSRF"})
    return {
        "ok": True,
        "api_version": 1,
        "account": {
            "username": profile.get("username", ""),
            "is_owner": owner,
            "can_add_router": can_add,
            "router_ids": list(profile.get("router_ids") or []),
            "router_flags": dict(profile.get("router_flags") or {}),
        },
        "auth": _auth_meta(hub, hub.normalize_auth_state(hub.load_auth()), profile),
        "session": {field: session.get(field) for field in ("id", "expires_at", "auth_method")},
        "csrf_token": _csrf(handler, session),
        "csrf_header": CSRF_HEADER,
        "csrf": csrf,
        "csrf_headers": csrf_headers,
        "links": links,
    }


def _read_login(handler):
    content_type = str(handler.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    length = str(handler.headers.get("Content-Length") or "")
    if (handler.headers.get("Transfer-Encoding") or not length.isascii() or not length.isdigit() or len(length) > 5
            or not 0 < int(length) <= MAX_LOGIN_BODY or content_type != "application/json"):
        _error(handler, 400, "invalid_request", "Expected a small JSON request")
        return None
    try:
        payload = json.loads(handler.read_body().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Expected a JSON object")
        limits = {"username": 64, "password": 4096, "otp": 32, "captcha_token": 2048, "captcha_answer": 32}
        for field, limit in limits.items():
            value = payload.get(field, "")
            if not isinstance(value, str) or len(value) > limit:
                raise ValueError("Invalid login field")
        return payload
    except (UnicodeDecodeError, ValueError):
        _error(handler, 400, "invalid_request", "Invalid JSON login request")
        return None


def _login(handler, hub):
    if not _same_origin(handler):
        return
    payload = _read_login(handler)
    if payload is None:
        return
    auth, captcha = _captcha(hub)
    if captcha.get("effective_mode") != hub.CAPTCHA_MODE_DIGITS:
        _error(handler, 409, "browser_login_required", "Complete this Hub's captcha in the browser",
               browser_login_required=True, browser_login_url="/login")
        return
    captcha_ok, captcha_error = handler.verify_login_captcha(payload, auth)
    if not captcha_ok:
        _error(handler, 401, "captcha_failed", captcha_error or "Invalid captcha")
        return
    ok, error, profile, method = hub.verify_password_login(
        payload.get("username", ""), payload.get("password", ""), payload.get("otp", ""))
    if not ok:
        _error(handler, 401, "login_failed", error or "Invalid login")
        return
    username = (profile or {}).get("username", payload.get("username", ""))
    token, _ = handler.create_login_session(username, method)
    handler.send_json(200, {
        "ok": True, "username": username, "auth_method": method, "session_url": ENDPOINTS["session"],
    }, extra_headers=[("Set-Cookie", handler.session_cookie(token))])


def _notes(handler, hub, encoded_router_id, write=False):
    """Append one note using the same storage and permissions as the Hub notes page."""
    session, profile = _current(handler)
    if not session:
        return
    router_id = urllib.parse.unquote(encoded_router_id)
    try:
        if hub.clean_router_id(router_id) != router_id:
            raise ValueError("Invalid router id")
    except ValueError:
        _error(handler, 400, "invalid_router", "Invalid router id")
        return
    if not handler.require_router_access(router_id, "G" if write else "A", json_mode=True):
        return
    text = None
    if write:
        if not _same_origin(handler):
            return
        supplied = handler.headers.get(CSRF_HEADER, "").encode("utf-8")
        if not secrets.compare_digest(supplied, _csrf(handler, session).encode("ascii")):
            _error(handler, 403, "csrf_failed", "Refresh the Hub session and retry")
            return
        content_type = str(handler.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        length = str(handler.headers.get("Content-Length") or "")
        if (handler.headers.get("Transfer-Encoding") or not length.isascii() or not length.isdigit()
                or len(length) > 5 or not 0 < int(length) <= 16384 or content_type != "application/json"):
            _error(handler, 400, "invalid_request", "Expected a small JSON note request")
            return
        try:
            payload = json.loads(handler.read_body().decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Expected an object")
            text = payload.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > hub.ROUTER_NOTE_TEXT_MAX_CHARS:
                raise ValueError("Invalid note text")
        except (UnicodeDecodeError, ValueError):
            _error(handler, 400, "invalid_note", "Note text is required and must contain at most 2000 characters")
            return
    try:
        with handler.app.conn() as conn:
            row = hub.get_active_router(conn, router_id)
            if not row:
                _error(handler, 404, "router_not_found", "Router not found")
                return
            if write:
                row = hub.append_router_note(conn, router_id, text)
            router = hub.router_with_access(hub.row_to_router(row), profile)
        handler.send_json(200, {
            "ok": True, "router_id": router_id,
            "notes_entries": router.get("notes_entries", []),
            "can_append": bool((router.get("permissions") or {}).get("notes_edit")),
        })
    except ValueError:
        _error(handler, 400, "invalid_note", "Could not append this note")


def _config(handler, hub, encoded_router_id):
    session, _ = _current(handler)
    if not session:
        return
    router_id = urllib.parse.unquote(encoded_router_id)
    try:
        if hub.clean_router_id(router_id) != router_id:
            raise ValueError("Invalid router id")
    except ValueError:
        _error(handler, 400, "invalid_router", "Invalid router id")
        return
    if not handler.require_router_access(router_id, "G", json_mode=True):
        return
    with handler.app.conn() as conn:
        row = hub.get_active_router(conn, router_id)
    if not row:
        _error(handler, 404, "router_not_found", "Router not found")
        return
    update, hub_url, vps_host = hub.router_current_hub_bundle(
        row, handler.app.public_url, handler.request_origin(), handler.headers.get("Host", ""))
    config = hub.make_openwrt_config(row, hub_url, vps_host_override=vps_host,
                                     public_url_override=update.get("public_url", ""))
    handler.send_json(200, {"ok": True, "router_id": router_id, "config": config})


def dispatch_get(handler, path):
    """Return True after responding to an exact mobile API path."""
    if path != BASE and not path.startswith(BASE + "/"):
        return False
    config_match = CONFIG_PATH.fullmatch(path)
    if config_match:
        _config(handler, _hub(handler), config_match[1])
        return True
    note_match = NOTE_PATH.fullmatch(path)
    if note_match:
        _notes(handler, _hub(handler), note_match[1])
        return True
    if path not in GET_PATHS:
        status = 405 if path in POST_PATHS else 404
        _error(handler, status, "method_not_allowed" if status == 405 else "not_found", "Unknown mobile API request")
        return True
    hub = _hub(handler)
    if path == BASE + "/capabilities":
        _, captcha = _captcha(hub)
        native = captcha.get("effective_mode") == hub.CAPTCHA_MODE_DIGITS
        handler.send_json(200, {
            "ok": True, "api_version": 1, "hub_version": hub.HUB_LOADED_VERSION,
            "native_password_login": native, "browser_login_url": "/login", "endpoints": dict(ENDPOINTS),
        })
    elif path == ENDPOINTS["challenge"]:
        _, captcha = _captcha(hub)
        mode = captcha.get("effective_mode", hub.CAPTCHA_MODE_DIGITS)
        challenge = {"mode": mode}
        browser = mode != hub.CAPTCHA_MODE_DIGITS
        if not browser:
            code, token = hub.captcha_challenge()
            challenge.update({"code": code, "token": token, "ttl_seconds": hub.CAPTCHA_TTL_SECONDS})
        handler.send_json(200, {
            "ok": True, "captcha": challenge, "browser_login_required": browser, "browser_login_url": "/login",
        })
    else:
        session, profile = _current(handler)
        if session:
            handler.send_json(200, _session_payload(handler, hub, session, profile))
    return True


def dispatch_post(handler, path):
    """Return True after handling a mobile login or CSRF-protected logout."""
    if path != BASE and not path.startswith(BASE + "/"):
        return False
    if CONFIG_PATH.fullmatch(path):
        _error(handler, 405, "method_not_allowed", "Router configuration is read-only")
        return True
    note_match = NOTE_PATH.fullmatch(path)
    if note_match:
        _notes(handler, _hub(handler), note_match[1], write=True)
        return True
    if path not in POST_PATHS:
        status = 405 if path in GET_PATHS else 404
        _error(handler, status, "method_not_allowed" if status == 405 else "not_found", "Unknown mobile API request")
        return True
    hub = _hub(handler)
    if path == ENDPOINTS["login"]:
        _login(handler, hub)
    elif _same_origin(handler):
        session, _ = _current(handler)
        if session:
            supplied = handler.headers.get(CSRF_HEADER, "").encode("utf-8")
            if not secrets.compare_digest(supplied, _csrf(handler, session).encode("ascii")):
                _error(handler, 403, "csrf_failed", "Refresh the Hub session and retry")
            else:
                hub.revoke_hub_session(token=handler.current_session_token())
                handler.send_json(200, {"ok": True}, extra_headers=[("Set-Cookie", handler.clear_session_cookie())])
    return True
