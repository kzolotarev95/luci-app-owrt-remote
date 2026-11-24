"""Native client authentication over the Hub's existing cookie sessions.

The Hub calls dispatch_get/dispatch_post before its HTML authentication guard.
This module does not grant access to any existing router or owner-only API.
"""

import base64
import hashlib
import hmac
import json
import re
import secrets
import sys
import urllib.parse


MOBILE_API_REVISION = 4
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
    uploads = []
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
                or len(length) > 7 or not 0 < int(length) <= 3 * 1024 * 1024 or content_type != "application/json"):
            _error(handler, 400, "invalid_request", "Expected a small JSON note request")
            return
        try:
            payload = json.loads(handler.read_body().decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Expected an object")
            text = payload.get("text")
            if not isinstance(text, str) or len(text) > hub.ROUTER_NOTE_TEXT_MAX_CHARS:
                raise ValueError("Invalid note text")
            images = payload.get('images', [])
            if not isinstance(images, list) or len(images) > 4:
                raise ValueError('Up to four images allowed')
            total = 0
            for image in images:
                if not isinstance(image, dict) or image.get('content_type') not in hub.ROUTER_NOTE_IMAGE_TYPES:
                    raise ValueError('Invalid image type')
                encoded = image.get('data', '')
                if not isinstance(encoded, str) or len(encoded) > 2800000:
                    raise ValueError('Invalid image data')
                raw = base64.b64decode(encoded, validate=True)
                total += len(raw)
                if not raw or total > 2 * 1024 * 1024:
                    raise ValueError('Images exceed 2 MiB')
                valid = ((image['content_type'] == 'image/jpeg' and raw.startswith(b'\xff\xd8\xff'))
                    or (image['content_type'] == 'image/png' and raw.startswith(b'\x89PNG\r\n\x1a\n'))
                    or (image['content_type'] == 'image/gif' and raw.startswith((b'GIF87a', b'GIF89a')))
                    or (image['content_type'] == 'image/webp' and raw[:4] == b'RIFF' and raw[8:12] == b'WEBP'))
                if not valid:
                    raise ValueError('Image signature does not match type')
                uploads.append({'body': raw, 'filename': str(image.get('name') or 'photo')[:160], 'content_type': image['content_type']})
            if not text.strip() and not uploads:
                raise ValueError('Text or photo required')
        except (UnicodeDecodeError, ValueError):
            _error(handler, 400, "invalid_note", "Use text up to 2000 characters or up to four images, 2 MiB total")
            return
    try:
        with handler.app.conn() as conn:
            row = hub.get_active_router(conn, router_id)
            if not row:
                _error(handler, 404, "router_not_found", "Router not found")
                return
            if write:
                row = hub.append_router_note(conn, router_id, text, image_uploads=uploads)
            router = hub.router_with_access(hub.row_to_router(row), profile)
        handler.send_json(200, {
            "ok": True, "router_id": router_id,
            "notes_entries": router.get("notes_entries", []),
            "supports_images": True, "image_max_files": 4, "image_total_max_bytes": 2 * 1024 * 1024,
            "can_append": bool((router.get("permissions") or {}).get("notes_edit")),
            "can_delete": bool((router.get("permissions") or {}).get("notes_delete")),
        })
    except ValueError:
        _error(handler, 400, "invalid_note", "Could not append this note")


def _delete_note(handler, hub, encoded_router_id):
    session, profile = _current(handler)
    if not session:
        return
    if not profile.get('is_owner'):
        _error(handler, 403, 'owner_required', 'Удаление заметок доступно только владельцу Hub')
        return
    if not _same_origin(handler):
        return
    supplied = handler.headers.get(CSRF_HEADER, '').encode('utf-8')
    if not secrets.compare_digest(supplied, _csrf(handler, session).encode('ascii')):
        _error(handler, 403, 'csrf_failed', 'Refresh the Hub session and retry')
        return
    length = handler.headers.get('Content-Length', '')
    if (handler.headers.get('Transfer-Encoding') or not length.isascii() or not length.isdigit()
            or len(length)>4 or not 0<int(length)<=4096
            or handler.headers.get('Content-Type','').split(';',1)[0].strip().lower()!='application/json'):
        _error(handler,400,'invalid_request','Expected a small JSON request')
        return
    try:
        router_id=urllib.parse.unquote(encoded_router_id)
        if hub.clean_router_id(router_id)!=router_id:
            raise ValueError('Invalid router')
        payload=json.loads(handler.read_body().decode('utf-8'))
        note_id=payload.get('note_id') if isinstance(payload,dict) else None
        if not isinstance(note_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',note_id):
            raise ValueError('Invalid note id')
        with handler.app.conn() as conn:
            row=hub.get_active_router(conn,router_id)
            if not row:
                _error(handler,404,'router_not_found','Router not found')
                return
            entries=hub.parse_router_notes_entries(row['notes'] or '', fallback_ts=row['updated_at'] or row['created_at'] or 0)
            if not any(entry.get('id')==note_id for entry in entries):
                _error(handler,404,'note_not_found','Заметка не найдена. Обновите список.')
                return
            row=hub.delete_router_note_entry(conn,router_id,note_id)
            router=hub.router_with_access(hub.row_to_router(row),profile)
        handler.send_json(200,{'ok':True,'router_id':router_id,'notes_entries':router.get('notes_entries',[]),
                              'can_append':True,'can_delete':True,'supports_images':True,'deleted_note_id':note_id})
    except (UnicodeDecodeError,ValueError):
        _error(handler,400,'invalid_note','Не удалось удалить заметку. Проверьте выбранную запись.')


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


def _router_defaults(handler, hub):
    session, profile = _current(handler)
    if not session or not handler.require_add_router(json_mode=True):
        return
    with handler.app.conn() as conn:
        rows = [dict(row) for row in conn.execute("select * from routers where coalesce(deleted_at, 0) = 0").fetchall()]
        ids = {str(row['id']) for row in rows}
        used = {int(row.get(key) or 0) for row in rows for key in ('entry_port', 'ssh_entry_port', 'tachyon_entry_port')}
        router_id = 'main'
        if router_id in ids:
            number = 2
            while 'node-' + str(number) in ids:
                number += 1
            router_id = 'node-' + str(number)
        port = 18080
        while port in used or port + 1000 in used:
            port += 10
        if port + 1000 > 65535:
            _error(handler, 409, 'ports_exhausted', 'No free router ports available')
            return
        visible = handler.visible_routers(conn, profile)
        host = next((row.get('vps_host') for row in visible if row.get('vps_host')), '')
        host = host or hub.vps_host_name(handler.app.public_url or handler.request_origin())
        groups = hub.list_router_groups(conn)
        if not profile.get('is_owner'):
            visible_groups = {row.get('group_id') for row in visible}
            groups = [group for group in groups if group['id'] in visible_groups]
    handler.send_json(200, {'ok': True, 'defaults': {
        'id': router_id, 'name': 'Главный роутер' if router_id == 'main' else 'Роутер ' + router_id,
        'role': 'main' if router_id == 'main' else 'node', 'entry_port': port,
        'ssh_entry_port': port + 1000, 'vps_host': host, 'group_id': '',
        'public_url': handler.request_origin(),
    }, 'groups': groups})


def dispatch_get(handler, path):
    """Return True after responding to an exact mobile API path."""
    if path != BASE and not path.startswith(BASE + "/"):
        return False
    media = re.fullmatch(re.escape(BASE) + r"/router/([^/]+)/notes-media/([^/]+)/([^/]+)", path)
    if media:
        session, _ = _current(handler)
        if session and handler.require_router_access(urllib.parse.unquote(media[1]), 'A', json_mode=True):
            handler.router_note_media_page('/router/' + media[1] + '/notes-media/' + media[2] + '/' + media[3])
        return True
    if path == BASE + "/router-defaults":
        _router_defaults(handler, _hub(handler))
        return True
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
            "ok": True, "api_version": 1, "api_revision": MOBILE_API_REVISION, "hub_version": hub.HUB_LOADED_VERSION,
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
    delete_match=re.fullmatch(re.escape(BASE)+r"/router/([^/]+)/notes/delete",path)
    if delete_match:
        _delete_note(handler,_hub(handler),delete_match[1])
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
