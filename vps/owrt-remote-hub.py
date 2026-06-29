#!/usr/bin/env python3
import argparse
import base64
import datetime as dt
import hashlib
import html
import http.client
import json
import os
import secrets
import select
import signal
import sqlite3
import struct
import subprocess
import sys
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


APP_NAME = "OpenWrt Remote Hub"
STATE_DIR = Path(os.environ.get("OWRT_REMOTE_STATE_DIR", "/var/lib/owrt-remote"))
DB_PATH = Path(os.environ.get("OWRT_REMOTE_DB", str(STATE_DIR / "hub.db")))
AUTH_FILE = STATE_DIR / "hub-auth.json"
SESSION_TOKEN_FILE = STATE_DIR / "hub-session.token"
AGENT_TOKEN_FILE = STATE_DIR / "agent.token"
ONLINE_AFTER_SECONDS = int(os.environ.get("OWRT_REMOTE_ONLINE_AFTER", "75"))
DEFAULT_VLESS_PORT = int(os.environ.get("OWRT_REMOTE_VLESS_PORT", "8443"))
PBKDF2_ITERATIONS = 240000
MIN_PASSWORD_LENGTH = 4
SESSION_COOKIE = "owrt_remote_session"
ROUTER_COOKIE = "owrt_remote_router"
LUCI_ABSOLUTE_ROOTS = ("/ubus", "/cgi-bin/luci", "/luci-static")
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def now_ts():
    return int(time.time())


def iso_time(ts):
    if not ts:
        return ""
    return dt.datetime.fromtimestamp(int(ts), tz=dt.timezone.utc).isoformat()


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
            last_seen integer,
            status_json text not null default '{}'
        )
        """
    )
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


def get_router_by_entry_port(conn, entry_port, exclude_id=""):
    return conn.execute(
        "select * from routers where (entry_port = ? or ssh_entry_port = ?) and id != ?",
        (int(entry_port), int(entry_port), exclude_id),
    ).fetchone()


def get_router_by_any_port(conn, port, exclude_id=""):
    return conn.execute(
        "select * from routers where (entry_port = ? or ssh_entry_port = ?) and id != ?",
        (int(port), int(port), exclude_id),
    ).fetchone()


def list_router_rows(conn):
    return conn.execute(
        """
        select * from routers
        order by case role when 'main' then 0 else 1 end, lower(id)
        """
    ).fetchall()


def row_to_router(row):
    data = dict(row)
    try:
        status = json.loads(data.get("status_json") or "{}")
    except json.JSONDecodeError:
        status = {}
    last_seen = data.get("last_seen")
    online = bool(last_seen and now_ts() - int(last_seen) <= ONLINE_AFTER_SECONDS)
    data["status"] = status
    data["online"] = online
    data["last_seen_iso"] = iso_time(last_seen)
    data["access_url"] = f"/access/{urllib.parse.quote(data['id'])}/"
    data["ssh_url"] = f"/ssh/{urllib.parse.quote(data['id'])}/"
    data["config_url"] = f"/router/{urllib.parse.quote(data['id'])}/config"
    data["xray_client_url"] = f"/router/{urllib.parse.quote(data['id'])}/xray-client.json"
    data.pop("status_json", None)
    return data


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
    }
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
                created_at, updated_at
            ) values (
                :id, :name, :role, :entry_port, :vps_host, :vless_port, :vless_uuid,
                :vless_encryption, :vless_decryption, :vless_flow, :reverse_tag,
                :public_url, :admin_host, :admin_port, :ssh_entry_port, :ssh_vless_uuid,
                :ssh_reverse_tag, :ssh_host, :ssh_port,
                :created_at, :updated_at
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
                "ssh_port": payload.get("ssh_port") or 22,
            },
        )
    conn.execute(
        """
        update routers set
            name = coalesce(nullif(?, ''), name),
            role = coalesce(nullif(?, ''), role),
            public_url = coalesce(nullif(?, ''), public_url),
            admin_host = coalesce(nullif(?, ''), admin_host),
            admin_port = coalesce(?, admin_port),
            ssh_host = coalesce(nullif(?, ''), ssh_host),
            ssh_port = coalesce(?, ssh_port),
            last_seen = ?,
            status_json = ?,
            updated_at = ?
        where id = ?
        """,
        (
            payload.get("name") or "",
            payload.get("role") or "",
            payload.get("public_url") or "",
            payload.get("admin_host") or "",
            int(payload["admin_port"]) if str(payload.get("admin_port", "")).isdigit() else None,
            payload.get("ssh_host") or "",
            int(payload["ssh_port"]) if str(payload.get("ssh_port", "")).isdigit() else None,
            ts,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            ts,
            router_id,
        ),
    )
    conn.commit()
    return row_to_router(get_router(conn, router_id))


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
    with connect(db_path) as conn:
        init_db(conn)
        rows = list_router_rows(conn)
    config = make_server_xray_config(rows)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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


def dashboard_html(routers, username):
    routers_json = json.dumps(routers, ensure_ascii=False)
    safe_username = html.escape(username, quote=True)
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{APP_NAME}</title>
<style>
:root{{color-scheme:dark;--bg:#07040f;--panel:rgba(19,14,32,.88);--panel2:rgba(255,255,255,.07);--text:#f7f2ff;--muted:#b9adc9;--line:rgba(169,126,255,.25);--blue:#7c3aed;--green:#22c55e;--red:#fb7185;--amber:#f59e0b;--cyan:#22d3ee;--teal:#a855f7;--grid:rgba(168,85,247,.14)}}
*{{box-sizing:border-box}}body{{position:relative;min-height:100vh;margin:0;overflow-x:hidden;background-color:var(--bg);background-image:radial-gradient(circle at 12% 8%,rgba(168,85,247,.46),transparent 31%),radial-gradient(circle at 82% 12%,rgba(79,70,229,.38),transparent 30%),radial-gradient(circle at 50% 105%,rgba(236,72,153,.26),transparent 35%),linear-gradient(145deg,#07040f,#120a24 48%,#05030a),repeating-linear-gradient(0deg,transparent 0 30px,var(--grid) 31px),repeating-linear-gradient(90deg,transparent 0 30px,var(--grid) 31px);background-size:130% 130%,140% 140%,135% 135%,100% 100%,31px 31px,31px 31px;background-attachment:fixed;color:var(--text);font:14px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;animation:bgFlow 28s ease-in-out infinite alternate}}
body::before{{content:"";position:fixed;inset:-25%;z-index:0;pointer-events:none;background:conic-gradient(from 0deg at 50% 50%,rgba(168,85,247,.05),rgba(236,72,153,.34),rgba(59,130,246,.22),rgba(245,158,11,.13),rgba(168,85,247,.05));filter:blur(54px);opacity:.7;animation:auraSpin 38s linear infinite}}
@keyframes bgFlow{{0%{{background-position:0% 0%,100% 0%,50% 100%,0 0,0 0,0 0}}50%{{background-position:28% 18%,62% 26%,38% 82%,0 0,15px 24px,24px 15px}}100%{{background-position:48% 28%,42% 42%,74% 62%,0 0,30px 0,0 30px}}}}
@keyframes auraSpin{{from{{transform:rotate(0deg) scale(1)}}to{{transform:rotate(360deg) scale(1.08)}}}}
.wrap{{position:relative;z-index:1;max-width:1220px;margin:0 auto;padding:22px}}.top{{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;border-bottom:1px solid var(--line);padding:20px 0 18px}}
.brand{{display:flex;align-items:center;gap:14px}}
h1{{margin:0;font-size:29px;line-height:1.2;letter-spacing:0}}.muted{{color:var(--muted)}}.top p{{margin:4px 0 0}}.links,.headerActions{{display:flex;align-items:center;gap:8px;flex-wrap:wrap}}.links{{margin-top:8px}}.links a,.badge{{position:relative;display:inline-flex;align-items:center;justify-content:center;gap:8px;min-height:36px;min-width:132px;padding:8px 14px;border:1px solid var(--line);border-radius:999px;background:rgba(255,255,255,.08);color:#f3e8ff;text-decoration:none;font-weight:800;font-size:13px;line-height:1;white-space:nowrap;overflow:hidden}}.headerActions{{position:relative;justify-content:flex-end;padding-top:42px}}.badge{{background:var(--panel);color:var(--muted)}}.authToggle{{cursor:pointer}}.dot{{width:9px;height:9px;border-radius:999px;background:var(--red);box-shadow:0 0 13px rgba(251,113,133,.72)}}.dot.on{{background:var(--green);box-shadow:0 0 13px rgba(34,197,94,.75)}}.dot.warn{{background:var(--amber);box-shadow:0 0 13px rgba(245,158,11,.75)}}
 .toolbar{{display:grid;grid-template-columns:1fr 1fr 110px 110px 150px auto;gap:10px;margin:18px 0;padding:14px;background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.045)),var(--panel);border:1px solid var(--line);border-radius:8px;box-shadow:0 18px 46px rgba(0,0,0,.20);backdrop-filter:blur(10px)}}
.authMenu{{position:absolute;right:0;top:calc(100% + 10px);z-index:5;width:min(520px,calc(100vw - 44px));padding:14px;background:linear-gradient(180deg,rgba(255,255,255,.09),rgba(255,255,255,.05)),rgba(19,14,32,.96);border:1px solid var(--line);border-radius:8px;box-shadow:0 24px 70px rgba(0,0,0,.36);backdrop-filter:blur(12px)}}.authMenu[hidden]{{display:none}}.authMenu h2{{margin:0 0 4px;font-size:18px}}.authMenu p{{margin:0 0 12px;color:var(--muted)}}.authGrid{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}.authGrid .wide{{grid-column:1/-1}}.msg{{margin-top:10px;color:#bbf7d0;font-weight:750}}.msg.bad{{color:#fecdd3}}.formMsg{{margin:-8px 0 18px;padding:10px 12px;border:1px solid rgba(34,197,94,.34);border-radius:8px;background:rgba(34,197,94,.12);color:#bbf7d0;font-weight:800}}.formMsg.bad{{border-color:rgba(251,113,133,.4);background:rgba(251,113,133,.13);color:#fecdd3}}
input,select{{min-width:0;border:1px solid var(--line);border-radius:8px;padding:10px 11px;background:rgba(8,5,18,.72);color:var(--text)}}button,.btn{{border:1px solid rgba(255,255,255,.10);border-radius:8px;padding:10px 13px;background:rgba(255,255,255,.10);color:#f7f2ff;font-weight:850;text-decoration:none;cursor:pointer;display:inline-flex;justify-content:center;align-items:center}}.authToggle{{border-radius:999px;padding:8px 14px;background:var(--panel);color:var(--muted)}}button.primary,.btn.primary{{background:var(--blue);color:#fff;box-shadow:0 10px 22px rgba(124,58,237,.22)}}button.bad,.btn.bad{{background:rgba(251,113,133,.16);color:#fecdd3}}.btn.good{{background:rgba(34,197,94,.16);color:#bbf7d0}}.btn.disabled{{opacity:.45;cursor:not-allowed}}
.sectionHead{{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin:24px 0 14px}}.sectionHead h2{{margin:0;font-size:22px}}.sectionHead p{{margin:3px 0 0;color:var(--muted)}}.summary{{display:flex;gap:10px;flex-wrap:wrap;justify-content:flex-end}}.miniStat{{border:1px solid var(--line);border-radius:999px;background:rgba(255,255,255,.07);padding:8px 12px;color:#ddd6fe;font-weight:750;white-space:nowrap}}
.cards{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}}.card{{position:relative;min-height:246px;overflow:hidden;background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.045)),var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px;box-shadow:0 18px 46px rgba(0,0,0,.28);backdrop-filter:blur(10px)}}.card::before{{content:"";position:absolute;inset:0 0 auto 0;height:3px;background:var(--green)}}.card.online{{border-color:rgba(34,197,94,.45);box-shadow:0 18px 46px rgba(0,0,0,.28),0 0 0 1px rgba(34,197,94,.10),0 0 34px rgba(34,197,94,.10)}}.card.online::after{{content:"";position:absolute;right:-42px;top:-42px;width:108px;height:108px;border-radius:50%;background:radial-gradient(circle,rgba(34,197,94,.26),transparent 68%);animation:onlineGlow 2.4s ease-in-out infinite}}.card.off{{border-color:rgba(251,113,133,.42);box-shadow:0 18px 46px rgba(0,0,0,.28),0 0 0 1px rgba(251,113,133,.08),0 0 30px rgba(251,113,133,.08)}}.card.off::after{{content:"";position:absolute;right:-42px;top:-42px;width:108px;height:108px;border-radius:50%;background:radial-gradient(circle,rgba(251,113,133,.22),transparent 68%);animation:offlineGlow 2.8s ease-in-out infinite}}.card.off::before{{background:var(--red)}}.card.warn::before{{background:var(--amber)}}.card.main{{grid-column:span 2}}
@keyframes onlineGlow{{0%,100%{{transform:scale(.9);opacity:.55}}50%{{transform:scale(1.08);opacity:1}}}}@keyframes offlineGlow{{0%,100%{{transform:scale(.88);opacity:.34}}50%{{transform:scale(1.08);opacity:.9}}}}
.cardTop{{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}}.routerMark{{display:grid;place-items:center;width:46px;height:46px;border-radius:8px;border:1px solid rgba(255,255,255,.14);background:rgba(255,255,255,.08)}}.routerIcon{{position:relative;width:28px;height:18px;border:2px solid #ddd6fe;border-radius:5px}}.routerIcon::before,.routerIcon::after{{content:"";position:absolute;top:-9px;width:9px;height:9px;border-top:2px solid #ddd6fe}}.routerIcon::before{{left:2px;transform:rotate(-34deg)}}.routerIcon::after{{right:2px;transform:rotate(34deg)}}.routerIcon span{{position:absolute;left:5px;right:5px;bottom:4px;display:flex;justify-content:space-between}}.routerIcon span::before,.routerIcon span::after{{content:"";width:4px;height:4px;border-radius:50%;background:#ddd6fe}}
.status{{display:inline-flex;align-items:center;gap:7px;border-radius:999px;border:1px solid rgba(34,197,94,.36);background:rgba(34,197,94,.14);padding:7px 10px;font-weight:900;font-size:12px;color:#bbf7d0}}.status i{{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 13px var(--green);animation:statusPulse 1.6s ease-in-out infinite}}.status.off{{border-color:rgba(251,113,133,.36);background:rgba(251,113,133,.12);color:#fecdd3}}.status.off i{{background:var(--red);box-shadow:0 0 13px var(--red);animation:offlinePulse 1.9s ease-in-out infinite}}.status.warn i{{background:var(--amber);box-shadow:0 0 13px var(--amber)}}@keyframes statusPulse{{0%,100%{{transform:scale(1);opacity:.75}}50%{{transform:scale(1.45);opacity:1}}}}@keyframes offlinePulse{{0%,100%{{transform:scale(1);opacity:.5}}50%{{transform:scale(1.42);opacity:1}}}}.name{{margin:12px 0 0;font-size:19px;font-weight:900;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.metaLine{{margin-top:3px;color:var(--muted)}}.tagRow{{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}}.tag{{border:1px solid var(--line);border-radius:999px;padding:5px 9px;background:rgba(255,255,255,.06);color:#ddd6fe;font-size:12px;font-weight:750}}
.metrics{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:14px}}.metric{{border:1px solid var(--line);border-radius:8px;background:rgba(255,255,255,.055);padding:9px}}.metric.span2{{grid-column:span 2}}.metric.temp-ok strong{{color:#bbf7d0}}.metric.temp-warn strong{{color:#fde68a}}.metric.temp-bad strong{{color:#fecdd3}}.metric span{{display:block;color:var(--muted);font-size:11px}}.metric strong{{display:block;margin-top:2px;font-size:14px;word-break:break-word}}.actions{{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}}.empty{{grid-column:1/-1;border:1px dashed var(--line);border-radius:8px;padding:30px;background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.045)),var(--panel);text-align:center;color:var(--muted)}}.hint{{margin-top:16px;padding:13px;border:1px solid var(--line);border-radius:8px;background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.045)),var(--panel);color:var(--muted)}}code{{background:rgba(255,255,255,.10);border-radius:6px;padding:2px 5px;color:#f3e8ff}}
@media(max-width:980px){{.cards{{grid-template-columns:repeat(2,minmax(0,1fr))}}.toolbar,.authGrid{{grid-template-columns:1fr 1fr}}.card.main{{grid-column:span 2}}.top{{flex-direction:column}}.headerActions{{padding-top:0;justify-content:flex-start}}}}
@media(max-width:680px){{body{{font-size:13px;background-attachment:scroll}}.wrap{{padding:10px}}.top{{gap:12px;padding:14px 0;align-items:flex-start;flex-direction:column}}.brand,.brand>div{{width:100%}}h1{{font-size:22px;line-height:1.18}}.links,.headerActions,.summary{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));width:100%;gap:8px}}.links{{margin-top:10px}}.links a,.badge,.headerActions .btn,.miniStat{{width:100%;min-width:0;padding:9px 10px;font-size:12px}}.authMenu{{position:fixed;left:10px;right:10px;top:74px;width:auto;max-height:calc(100svh - 90px);overflow:auto}}.cards,.toolbar,.authGrid{{grid-template-columns:1fr}}.toolbar{{padding:10px;margin:12px 0}}.card.main{{grid-column:span 1}}.card{{padding:12px;min-height:0}}.name{{white-space:normal;font-size:18px}}.sectionHead{{align-items:flex-start;flex-direction:column;margin:18px 0 10px}}.metrics{{grid-template-columns:repeat(2,minmax(0,1fr));gap:7px}}.metric{{padding:8px}}.actions{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}}.actions .btn,.actions button{{width:100%;min-width:0;padding:9px 8px;font-size:12px}}}}
@media(max-width:420px){{.links,.headerActions,.summary,.actions{{grid-template-columns:1fr}}.metrics{{grid-template-columns:1fr}}.metric.span2{{grid-column:span 1}}}}
</style>
</head>
<body>
<main class="wrap">
  <section class="top">
    <div class="brand">
      <div>
        <h1>OpenWrt Remote Hub</h1>
        <div class="links">
          <a href="https://t.me/kzolotarev95" target="_blank" rel="noopener noreferrer">Telegram</a>
          <a href="https://github.com/kzolotarev95" target="_blank" rel="noopener noreferrer">GitHub</a>
          <a href="https://t.me/+LZDsQJhUfcNhYWEy" target="_blank" rel="noopener noreferrer">NetHaven VPN</a>
        </div>
      </div>
    </div>
    <div class="headerActions">
      <div class="badge"><span class="dot on"></span>Hub online</div>
      <button class="badge" id="xrayReload" type="button">Обновить Xray VPS</button>
      <button class="badge" id="xrayRestart" type="button">Рестарт Xray VPS</button>
      <button class="badge authToggle" id="authToggle" type="button">login: {safe_username}</button>
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
      </div>
    </div>
  </section>

  <form class="toolbar" id="routerForm">
    <input name="id" placeholder="router id: node-2" autocomplete="off" required>
    <input name="name" placeholder="Название роутера" required>
    <select name="role"><option value="node">node</option><option value="main">main</option></select>
    <input name="entry_port" placeholder="18080" inputmode="numeric" required>
    <input name="vps_host" placeholder="VPS IP/domain" required>
    <button class="primary">Добавить</button>
  </form>
  <div id="routerMsg" class="formMsg" hidden></div>

  <section class="sectionHead">
    <div>
      <h2>Карточки роутеров</h2>
    </div>
    <div class="summary">
      <div class="miniStat" id="statTotal">0 роутеров</div>
      <div class="miniStat" id="statOnline">0 онлайн</div>
      <div class="miniStat" id="statOffline">0 оффлайн</div>
    </div>
  </section>

  <section id="cards" class="cards"></section>
</main>
<script>
window.ROUTERS = {routers_json};
const cards = document.getElementById('cards');
const routerForm = document.getElementById('routerForm');
const routerMsg = document.getElementById('routerMsg');

function ago(iso) {{
  if (!iso) return 'never';
  const diff = Math.max(0, Math.floor((Date.now() - Date.parse(iso)) / 1000));
  if (diff < 60) return diff + ' sec ago';
  if (diff < 3600) return Math.floor(diff / 60) + ' min ago';
  return Math.floor(diff / 3600) + ' h ago';
}}

function duration(seconds) {{
  let value = Number(seconds || 0);
  if (!value) return 'unknown';
  const days = Math.floor(value / 86400);
  value %= 86400;
  const hours = Math.floor(value / 3600);
  value %= 3600;
  const mins = Math.floor(value / 60);
  if (days) return days + 'd ' + hours + 'h';
  if (hours) return hours + 'h ' + mins + 'm';
  return mins + 'm';
}}

function metric(label, value, cls = '') {{
  return `<div class="metric ${{cls}}"><span>${{escapeHtml(label)}}</span><strong>${{escapeHtml(value || 'unknown')}}</strong></div>`;
}}

function tempClass(value) {{
  const n = Number(String(value || '').replace(',', '.').match(/-?\\d+(\\.\\d+)?/)?.[0]);
  if (!Number.isFinite(n)) return '';
  if (n >= 75) return 'temp-bad';
  if (n >= 60) return 'temp-warn';
  return 'temp-ok';
}}

function setSummary(list) {{
  const total = list.length;
  const online = list.filter(r => r.online).length;
  document.getElementById('statTotal').textContent = total + ' ' + plural(total, 'роутер', 'роутера', 'роутеров');
  document.getElementById('statOnline').textContent = online + ' онлайн';
  document.getElementById('statOffline').textContent = (total - online) + ' оффлайн';
}}

function plural(n, one, two, five) {{
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return two;
  return five;
}}

function render(list) {{
  setSummary(list);
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
    const release = (r.status && r.status.release) || 'waiting heartbeat';
    const xray = (r.status && r.status.xray) || 'unknown';
    const ssh = (r.status && r.status.ssh) || 'unknown';
    const uptime = r.status && r.status.uptime ? duration(r.status.uptime) : 'unknown';
    const load = (r.status && r.status.load) || 'unknown';
    const memory = (r.status && r.status.memory) || 'unknown';
    const flash = (r.status && r.status.flash) || 'unknown';
    const temperature = (r.status && r.status.temperature) || 'unknown';
    const access = r.public_url || r.access_url;
    const tags = [
      isMain ? 'главный' : 'node',
      r.entry_port ? 'entry ' + r.entry_port : '',
      r.ssh_entry_port ? 'ssh ' + r.ssh_entry_port : '',
      r.reverse_tag || '',
      (r.admin_host || '127.0.0.1') + ':' + (r.admin_port || 80)
    ].filter(Boolean).slice(0, 5);
    const tagHtml = tags.map(t => `<span class="tag">${{escapeHtml(t)}}</span>`).join('');
    const adminButton = online
      ? `<a class="btn primary" href="${{escapeAttr(access)}}">Админка</a>`
      : `<span class="btn primary disabled">Админка</span>`;
    const sshReady = online && ssh === 'running' && Number(r.ssh_entry_port || 0) > 0;
    const sshButton = sshReady
      ? `<a class="btn" href="${{escapeAttr(r.ssh_url || ('/ssh/' + encodeURIComponent(r.id) + '/'))}}" target="_blank" rel="noopener noreferrer">SSH</a>`
      : `<span class="btn disabled">SSH</span>`;
    const metricHtml = [
      metric('Модель', model, 'span2'),
      metric('OpenWrt', release),
      metric('Xray', xray),
      metric('SSH', ssh),
      metric('В сети уже', uptime),
      metric('Был на связи', ago(r.last_seen_iso)),
      metric('RAM', memory),
      metric('Flash', flash),
      metric('Температура', temperature, tempClass(temperature)),
      metric('Load', load, 'span2')
    ].join('');
    return `<article class="card ${{isMain ? 'main' : ''}} ${{online ? 'online' : 'off'}}">
      <div class="cardTop">
        <div class="routerMark"><div class="routerIcon"><span></span></div></div>
        <div class="status ${{stateClass}}"><i></i>${{stateText}}</div>
      </div>
      <div class="name">${{escapeHtml(r.name)}}</div>
      <div class="metaLine">ID: ${{escapeHtml(r.id)}} · роль: ${{escapeHtml(role)}}</div>
      <div class="tagRow">${{tagHtml}}</div>
      <div class="metrics">
        ${{metricHtml}}
      </div>
      <div class="actions">
        ${{adminButton}}
        ${{sshButton}}
        <a class="btn good" href="${{escapeAttr(r.config_url)}}">OpenWrt config</a>
        <a class="btn" href="${{escapeAttr(r.xray_client_url)}}">Client JSON</a>
        <button class="bad" data-delete="${{escapeAttr(r.id)}}">Удалить</button>
      </div>
    </article>`;
  }}).join('');
}}

function escapeHtml(s) {{
  return String(s ?? '').replace(/[&<>"']/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));
}}

function escapeAttr(s) {{
  return escapeHtml(s);
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

function fillRouterForm(force = false) {{
  const list = window.ROUTERS || [];
  const id = nextRouterId(list);
  if (force || !routerForm.id.value) routerForm.id.value = id;
  if (force || !routerForm.name.value) routerForm.name.value = defaultRouterName(routerForm.id.value || id);
  if (force || !routerForm.entry_port.value) routerForm.entry_port.value = String(nextEntryPort(list));
  if (force || !routerForm.vps_host.value) routerForm.vps_host.value = defaultVpsHost(list);
  if (force || !routerForm.role.value) routerForm.role.value = id === 'main' ? 'main' : 'node';
}}

function showRouterMsg(text, bad = false) {{
  routerMsg.hidden = false;
  routerMsg.className = bad ? 'formMsg bad' : 'formMsg';
  routerMsg.textContent = text;
}}

routerForm.id.addEventListener('input', () => {{
  if (!routerForm.name.dataset.touched) {{
    routerForm.name.value = defaultRouterName(routerForm.id.value.trim());
  }}
}});
routerForm.name.addEventListener('input', () => {{
  routerForm.name.dataset.touched = '1';
}});

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
    render(window.ROUTERS);
    fillRouterForm(false);
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
    routerForm.entry_port.value = String(nextEntryPort(window.ROUTERS || []));
    return;
  }}
  if (duplicateSshPort) {{
    showRouterMsg(`SSH-порт ${{sshEntryPort}} уже занят роутером "${{duplicateSshPort.id}}". Поставь другой entry port.`, true);
    routerForm.entry_port.value = String(nextEntryPort(window.ROUTERS || []));
    return;
  }}
  const res = await fetch('/api/router', {{method: 'POST', body}});
  if (res.ok) {{
    ev.currentTarget.reset();
    routerForm.name.dataset.touched = '';
    await loadRouters();
    fillRouterForm(true);
    showRouterMsg('Роутер добавлен. Теперь открой OpenWrt config в его карточке и вставь команды на роутер.');
  }} else {{
    showRouterMsg(await res.text(), true);
  }}
}});

cards.addEventListener('click', async (ev) => {{
  const id = ev.target?.dataset?.delete;
  if (!id) return;
  if (!confirm('Удалить роутер ' + id + '?')) return;
  const res = await fetch('/api/router/' + encodeURIComponent(id) + '/delete', {{method: 'POST'}});
  if (res.ok) await loadRouters();
}});

const authToggle = document.getElementById('authToggle');
const authMenu = document.getElementById('authMenu');
let authHideTimer;
function showAuthMenu() {{
  clearTimeout(authHideTimer);
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
  authMenu.hidden = !authMenu.hidden;
}});
authToggle.addEventListener('mouseenter', showAuthMenu);
authToggle.addEventListener('mouseleave', scheduleHideAuthMenu);
authMenu.addEventListener('mouseenter', showAuthMenu);
authMenu.addEventListener('mouseleave', scheduleHideAuthMenu);
authMenu.addEventListener('click', (ev) => ev.stopPropagation());
document.addEventListener('click', () => {{
  authMenu.hidden = true;
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

render(window.ROUTERS);
fillRouterForm(true);
setInterval(loadRouters, 5000);
</script>
</body>
</html>"""


def ssh_terminal_html(row):
    router_id = row["id"]
    safe_id = html.escape(router_id, quote=True)
    safe_name = html.escape(row["name"] or router_id, quote=True)
    ssh_port = int(row["ssh_entry_port"] or 0)
    ws_path = f"/ssh-ws/{urllib.parse.quote(router_id)}"
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SSH {safe_name}</title>
<style>
:root{{color-scheme:dark;--bg:#07040f;--panel:rgba(19,14,32,.92);--text:#f7f2ff;--muted:#b9adc9;--line:rgba(169,126,255,.28);--green:#22c55e;--blue:#7c3aed;--red:#fb7185;--grid:rgba(168,85,247,.13)}}
*{{box-sizing:border-box}}
body{{min-height:100vh;margin:0;background-color:var(--bg);background-image:radial-gradient(circle at 16% 10%,rgba(168,85,247,.45),transparent 30%),radial-gradient(circle at 88% 16%,rgba(59,130,246,.28),transparent 32%),linear-gradient(145deg,#07040f,#120a24 48%,#05030a),repeating-linear-gradient(0deg,transparent 0 30px,var(--grid) 31px),repeating-linear-gradient(90deg,transparent 0 30px,var(--grid) 31px);background-attachment:fixed;color:var(--text);font:14px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;padding:18px}}
.wrap{{max-width:1180px;margin:0 auto;display:flex;flex-direction:column;gap:10px}}
.top{{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:42px}}
.sshTitle{{display:flex;align-items:center;gap:10px;flex-wrap:wrap}}h1{{margin:0;font-size:18px;line-height:1.15}}.muted{{color:var(--muted)}}.chips{{display:flex;align-items:center;gap:8px;flex-wrap:wrap}}.chip{{border:1px solid var(--line);border-radius:999px;padding:6px 10px;background:rgba(255,255,255,.07);color:var(--muted);font-size:12px;font-weight:800}}
.badge,.btn{{display:inline-flex;align-items:center;justify-content:center;gap:8px;border:1px solid var(--line);border-radius:999px;padding:7px 12px;background:rgba(255,255,255,.08);color:#f3e8ff;text-decoration:none;font-weight:850;font-size:13px}}
.dot{{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 13px var(--green)}}
.termBox{{height:min(560px,calc(100vh - 112px));min-height:340px;display:flex;flex-direction:column;border:1px solid var(--line);border-radius:8px;background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.045)),var(--panel);box-shadow:0 22px 64px rgba(0,0,0,.38);overflow:hidden}}
.bar{{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:7px 10px;border-bottom:1px solid var(--line);background:rgba(255,255,255,.05)}}.termActions{{display:flex;align-items:center;gap:7px;flex-wrap:wrap}}.miniBtn{{border:1px solid var(--line);border-radius:999px;background:rgba(255,255,255,.08);color:#f3e8ff;padding:6px 10px;font:800 12px/1 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;cursor:pointer}}.miniBtn:hover{{background:rgba(255,255,255,.14)}}
.mobileInput{{display:none;gap:8px;padding:8px;border-top:1px solid var(--line);background:rgba(255,255,255,.045)}}.mobileInput input{{flex:1;min-width:0;border:1px solid var(--line);border-radius:8px;padding:11px 12px;background:rgba(8,5,18,.76);color:var(--text);font:14px/1.2 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;outline:none}}.mobileInput input:focus{{border-color:rgba(34,211,238,.62);box-shadow:0 0 0 3px rgba(34,211,238,.12)}}.mobileInput button{{border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:11px 12px;background:linear-gradient(135deg,#7c3aed,#a855f7);color:#fff;font-weight:950}}
#term{{flex:1;min-height:0;margin:0;padding:12px;overflow:auto;white-space:pre-wrap;word-break:break-word;outline:none;background:rgba(0,0,0,.42);font:13px/1.34 "Cascadia Mono","Consolas","Liberation Mono",monospace;color:#e9d5ff;scrollbar-width:thin;scrollbar-color:rgba(168,85,247,.72) rgba(255,255,255,.06)}}#term::-webkit-scrollbar{{width:12px;height:12px}}#term::-webkit-scrollbar-track{{background:rgba(255,255,255,.06)}}#term::-webkit-scrollbar-thumb{{background:linear-gradient(180deg,#7c3aed,#22d3ee);border-radius:999px;border:3px solid rgba(10,6,18,.96)}}#term::-webkit-scrollbar-thumb:hover{{background:linear-gradient(180deg,#a855f7,#67e8f9)}}.term-error{{color:#fb7185;font-weight:900}}.term-warn{{color:#fde68a;font-weight:850}}.term-ok{{color:#bbf7d0;font-weight:850}}.term-info{{color:#67e8f9;font-weight:850}}.term-prompt{{color:#86efac;font-weight:900}}.term-metric{{color:#93c5fd;font-weight:850}}.term-muted{{color:#c4b5fd}}.term-inverse{{display:inline-block;background:#ddd6fe;color:#13091f;border-radius:3px;padding:0 3px;font-weight:900}}
.bad{{color:#fecdd3}}
@media(max-width:680px){{body{{padding:10px;font-size:13px;background-attachment:scroll}}.wrap{{gap:8px}}.top{{align-items:stretch;flex-direction:column;gap:8px}}.sshTitle{{align-items:flex-start;flex-direction:column;gap:7px}}h1{{font-size:17px}}.chips{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));width:100%;gap:6px}}.chip{{text-align:center;padding:6px 7px;font-size:11px;overflow:hidden;text-overflow:ellipsis}}.btn{{width:100%;min-width:0}}.termBox{{height:min(500px,calc(100vh - 150px));max-height:500px;min-height:300px}}.bar{{align-items:stretch;flex-direction:column}}.badge{{width:100%;justify-content:center}}.termActions{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));width:100%;gap:6px}}.miniBtn{{padding:8px 7px;font-size:11px}}#term{{font-size:12px;line-height:1.32;padding:10px}}.mobileInput{{display:flex}}}}
@media(max-width:420px){{.chips,.termActions{{grid-template-columns:1fr}}.termBox{{height:min(420px,calc(100vh - 165px));max-height:420px}}}}
</style>
</head>
<body>
<main class="wrap">
  <div class="top">
    <div class="sshTitle">
      <h1>SSH · {safe_name}</h1>
      <div class="chips">
        <span class="chip">{safe_id}</span>
        <span class="chip">VPS:{ssh_port}</span>
        <span class="chip">OpenWrt:22</span>
      </div>
    </div>
    <a class="btn" href="/">Назад в Hub</a>
  </div>
  <section class="termBox">
    <div class="bar">
      <span class="badge"><i class="dot"></i>Terminal</span>
      <div class="termActions">
        <button class="miniBtn" id="copyTerm" type="button">Копировать</button>
        <button class="miniBtn" id="scrollTop" type="button">Вверх</button>
        <button class="miniBtn" id="scrollBottom" type="button">Вниз</button>
      </div>
    </div>
    <pre id="term" tabindex="0"></pre>
    <div class="mobileInput">
      <input id="cmdInput" autocomplete="off" autocapitalize="off" spellcheck="false" enterkeyhint="send" placeholder="Команда или пароль">
      <button id="cmdSend" type="button">Отправить</button>
    </div>
  </section>
</main>
<script>
const term = document.getElementById('term');
const copyTerm = document.getElementById('copyTerm');
const scrollTopBtn = document.getElementById('scrollTop');
const scrollBottomBtn = document.getElementById('scrollBottom');
const cmdInput = document.getElementById('cmdInput');
const cmdSend = document.getElementById('cmdSend');
let ws;
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
  return {{text: value, clearScreen}};
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
  if (cleaned.clearScreen) term.innerHTML = '';
  term.insertAdjacentHTML('beforeend', highlightTerminal(cleaned.text));
  term.scrollTop = term.scrollHeight;
}}
function send(text) {{
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(text);
}}
function sendCommandInput() {{
  const value = cmdInput.value;
  if (!value) return;
  send(value + '\\r');
  cmdInput.value = '';
  term.focus();
}}
function connect() {{
  const proto = location.protocol === 'https:' ? 'wss://' : 'ws://';
  ws = new WebSocket(proto + location.host + '{ws_path}');
  ws.onopen = () => {{}};
  ws.onmessage = (ev) => write(ev.data);
  ws.onerror = () => write('\\r\\n[ошибка web-terminal]\\r\\n');
  ws.onclose = () => write('\\r\\n[SSH соединение закрыто]\\r\\n');
}}
term.addEventListener('keydown', (ev) => {{
  if (ev.ctrlKey && ev.key.toLowerCase() === 'c') {{ send('\\x03'); ev.preventDefault(); return; }}
  if (ev.ctrlKey && ev.key.toLowerCase() === 'd') {{ send('\\x04'); ev.preventDefault(); return; }}
  if (ev.key === 'Enter') {{ send('\\r'); ev.preventDefault(); return; }}
  if (ev.key === 'Backspace') {{ send('\\x7f'); ev.preventDefault(); return; }}
  if (ev.key === 'Tab') {{ send('\\t'); ev.preventDefault(); return; }}
  if (ev.key === 'ArrowUp') {{ send('\\x1b[A'); ev.preventDefault(); return; }}
  if (ev.key === 'ArrowDown') {{ send('\\x1b[B'); ev.preventDefault(); return; }}
  if (ev.key === 'ArrowRight') {{ send('\\x1b[C'); ev.preventDefault(); return; }}
  if (ev.key === 'ArrowLeft') {{ send('\\x1b[D'); ev.preventDefault(); return; }}
  if (ev.key.length === 1 && !ev.metaKey && !ev.altKey) {{ send(ev.key); ev.preventDefault(); }}
}});
term.addEventListener('paste', (ev) => {{
  const text = (ev.clipboardData || window.clipboardData).getData('text');
  if (text) send(text);
  ev.preventDefault();
}});
copyTerm.addEventListener('click', async () => {{
  const text = term.textContent || '';
  try {{
    await navigator.clipboard.writeText(text);
    copyTerm.textContent = 'Скопировано';
  }} catch (e) {{
    const range = document.createRange();
    range.selectNodeContents(term);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    document.execCommand('copy');
    sel.removeAllRanges();
    copyTerm.textContent = 'Скопировано';
  }}
  setTimeout(() => {{ copyTerm.textContent = 'Копировать'; }}, 1200);
}});
scrollTopBtn.addEventListener('click', () => {{
  term.scrollTo({{top: 0, behavior: 'smooth'}});
  term.focus();
}});
scrollBottomBtn.addEventListener('click', () => {{
  term.scrollTo({{top: term.scrollHeight, behavior: 'smooth'}});
  term.focus();
}});
cmdSend.addEventListener('click', sendCommandInput);
cmdInput.addEventListener('keydown', (ev) => {{
  if (ev.key === 'Enter') {{
    sendCommandInput();
    ev.preventDefault();
  }}
}});
connect();
term.focus();
</script>
</body>
</html>"""


def login_html(error=""):
    error_html = f"<div class=\"err\">{html.escape(error)}</div>" if error else ""
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpenWrt Remote Hub</title>
<style>
:root{{color-scheme:dark;--bg:#07040f;--panel:rgba(19,14,32,.9);--text:#f7f2ff;--muted:#b9adc9;--line:rgba(169,126,255,.28);--blue:#7c3aed;--cyan:#22d3ee;--red:#fb7185;--green:#22c55e;--grid:rgba(168,85,247,.13)}}
*{{box-sizing:border-box}}
body{{position:relative;min-height:100vh;margin:0;overflow:hidden;background-color:var(--bg);background-image:radial-gradient(circle at 16% 12%,rgba(168,85,247,.48),transparent 30%),radial-gradient(circle at 84% 8%,rgba(59,130,246,.34),transparent 32%),radial-gradient(circle at 55% 105%,rgba(236,72,153,.24),transparent 36%),linear-gradient(145deg,#07040f,#120a24 48%,#05030a),repeating-linear-gradient(0deg,transparent 0 30px,var(--grid) 31px),repeating-linear-gradient(90deg,transparent 0 30px,var(--grid) 31px);background-size:130% 130%,140% 140%,135% 135%,100% 100%,31px 31px,31px 31px;background-attachment:fixed;color:var(--text);font:14px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;display:grid;place-items:center;padding:18px;animation:bgFlow 28s ease-in-out infinite alternate}}
body::before{{content:"";position:fixed;inset:-28%;pointer-events:none;background:conic-gradient(from 0deg at 50% 50%,rgba(168,85,247,.06),rgba(236,72,153,.30),rgba(59,130,246,.22),rgba(34,211,238,.16),rgba(168,85,247,.06));filter:blur(58px);opacity:.74;animation:auraSpin 40s linear infinite}}
@keyframes bgFlow{{0%{{background-position:0% 0%,100% 0%,50% 100%,0 0,0 0,0 0}}50%{{background-position:24% 18%,62% 28%,38% 82%,0 0,15px 24px,24px 15px}}100%{{background-position:46% 30%,42% 42%,74% 62%,0 0,30px 0,0 30px}}}}
@keyframes auraSpin{{from{{transform:rotate(0deg) scale(1)}}to{{transform:rotate(360deg) scale(1.08)}}}}
.login{{position:relative;z-index:1;width:min(360px,100%);padding:16px;border:1px solid var(--line);border-radius:8px;background:linear-gradient(180deg,rgba(255,255,255,.10),rgba(255,255,255,.045)),var(--panel);box-shadow:0 22px 64px rgba(0,0,0,.40);backdrop-filter:blur(14px)}}
.brand{{display:flex;gap:10px;align-items:center;margin-bottom:12px}}
.logo{{width:72px;height:34px;border-radius:8px;display:grid;place-items:center;background:linear-gradient(135deg,#22d3ee,#7c3aed 58%,#22c55e);box-shadow:0 12px 30px rgba(124,58,237,.28);font-size:12px;font-weight:950;color:white;letter-spacing:.1px}}
h1{{margin:0;font-size:23px;line-height:1.1;letter-spacing:0}}
p{{margin:3px 0 0;color:var(--muted)}}
label{{display:block;margin:10px 0 5px;font-weight:850;color:#ede9fe}}
input{{width:100%;border:1px solid var(--line);border-radius:8px;padding:11px 12px;background:rgba(8,5,18,.74);color:var(--text);outline:none;box-shadow:inset 0 1px 0 rgba(255,255,255,.04)}}
input:focus{{border-color:rgba(34,211,238,.62);box-shadow:0 0 0 3px rgba(34,211,238,.12),inset 0 1px 0 rgba(255,255,255,.04)}}
button{{width:100%;margin-top:13px;border:1px solid rgba(255,255,255,.12);border-radius:8px;padding:11px 12px;background:linear-gradient(135deg,#7c3aed,#a855f7);color:#fff;font-weight:950;cursor:pointer;box-shadow:0 14px 30px rgba(124,58,237,.28)}}
button:hover{{filter:brightness(1.06)}}
.err{{margin:0 0 12px;padding:11px 12px;border:1px solid rgba(251,113,133,.45);border-radius:8px;background:rgba(251,113,133,.14);color:#fecdd3;font-weight:800}}
@media(max-width:520px){{body{{padding:14px}}.login{{padding:15px}}h1{{font-size:22px}}.logo{{width:68px;height:32px}}}}
</style>
</head>
<body>
<form class="login" method="post" action="/login">
  <div class="brand">
    <div class="logo">OpenWrt</div>
    <div>
      <h1>Remote Hub</h1>
      <p>Вход в панель роутеров</p>
    </div>
  </div>
  {error_html}
  <label for="hubUsername">Логин</label>
  <input id="hubUsername" name="username" autocomplete="username" autofocus required>
  <label for="hubPassword">Пароль</label>
  <input id="hubPassword" name="password" type="password" autocomplete="current-password" required>
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

    def admin_ok(self):
        cookies = parse_cookies(self.headers.get("Cookie", ""))
        return secrets.compare_digest(cookies.get(SESSION_COOKIE, ""), self.app.session_token)

    def agent_ok(self):
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return secrets.compare_digest(auth[7:].strip(), self.app.agent_token)
        return False

    def send_bytes(self, status, body, content_type="text/plain; charset=utf-8", extra_headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for key, value in extra_headers:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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

    def session_cookie(self):
        return f"{SESSION_COOKIE}={self.app.session_token}; HttpOnly; SameSite=Lax; Path=/"

    def clear_session_cookie(self):
        return f"{SESSION_COOKIE}=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"

    def redirect(self, location, extra_headers=None):
        self.send_response(302)
        self.send_header("Location", location)
        if extra_headers:
            for key, value in extra_headers:
                self.send_header(key, value)
        self.end_headers()

    def login(self):
        payload = self.read_payload()
        username = payload.get("username", "")
        password = payload.get("password", "")
        if verify_login(username, password):
            self.redirect("/", [("Set-Cookie", self.session_cookie())])
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
            row = get_router(conn, router_id)
        if not row:
            self.send_text(404, "router not found")
            return
        if int(row["ssh_entry_port"] or 0) <= 0:
            self.send_text(400, "router has no ssh_entry_port")
            return
        self.send_bytes(
            200,
            ssh_terminal_html(row).encode("utf-8"),
            "text/html; charset=utf-8",
            [("Set-Cookie", current_router_cookie(router_id))],
        )

    def ssh_ws(self):
        if not self.admin_ok():
            self.send_response(403)
            self.end_headers()
            return
        router_id = self.router_id_from_path("/ssh-ws/")
        with self.app.conn() as conn:
            row = get_router(conn, router_id)
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
        self.run_ssh_session(router_id, port)

    def run_ssh_session(self, router_id, port):
        try:
            import pty
        except Exception as exc:
            ws_send_frame(self.connection, f"pty недоступен на VPS: {exc}\r\n")
            return

        ensure_state()
        try:
            pid, fd = pty.fork()
        except Exception as exc:
            ws_send_frame(self.connection, f"не удалось открыть SSH pty: {exc}\r\n")
            return

        if pid == 0:
            env = os.environ.copy()
            env["TERM"] = "dumb"
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
            try:
                os.execvpe("ssh", args, env)
            except Exception as exc:
                print(f"ssh start failed: {exc}", flush=True)
                os._exit(127)

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
                    if opcode in (1, 2) and payload:
                        os.write(fd, payload)
        except Exception as exc:
            try:
                ws_send_frame(self.connection, f"\r\n[SSH error: {exc}]\r\n")
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

    def do_GET(self):
        path = self.parsed().path
        if path == "/health":
            self.send_json(200, {"ok": True})
            return
        if path == "/login":
            self.send_bytes(200, login_html().encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/logout":
            self.redirect("/login", [("Set-Cookie", self.clear_session_cookie())])
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
            with self.app.conn() as conn:
                routers = [row_to_router(r) for r in list_router_rows(conn)]
            self.send_bytes(
                200,
                dashboard_html(routers, current_username()).encode("utf-8"),
                "text/html; charset=utf-8",
            )
            return
        if path == "/api/routers":
            with self.app.conn() as conn:
                routers = [row_to_router(r) for r in list_router_rows(conn)]
            self.send_json(200, {"routers": routers})
            return
        if path.startswith("/router/"):
            self.router_asset(path)
            return
        self.send_text(404, "not found")

    def do_POST(self):
        path = self.parsed().path
        if path == "/login":
            self.login()
            return
        if path == "/api/heartbeat":
            if not self.agent_ok():
                self.send_json(401, {"ok": False, "error": "bad agent token"})
                return
            try:
                payload = self.read_payload()
                with self.app.conn() as conn:
                    router = heartbeat(conn, payload)
                self.send_json(200, {"ok": True, "router": router})
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
                    if get_router(conn, router_id):
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
        if path.startswith("/api/router/") and path.endswith("/delete"):
            router_id = urllib.parse.unquote(path.split("/")[3])
            with self.app.conn() as conn:
                conn.execute("delete from routers where id = ?", (router_id,))
                conn.commit()
            self.send_json(200, {"ok": True})
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
            row = get_router(conn, router_id)
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
        if len(parts) < 3:
            self.redirect("/access/")
            return
        router_id = urllib.parse.unquote(parts[2])
        rest = "/" + parts[3] if len(parts) == 4 else "/"
        with self.app.conn() as conn:
            row = get_router(conn, router_id)
        if not row:
            self.send_text(404, "router not found")
            return
        port = int(row["entry_port"] or 0)
        if port <= 0:
            self.send_text(400, "router has no entry_port")
            return

        query = urllib.parse.parse_qsl(self.parsed().query, keep_blank_values=True)
        query = [(k, v) for k, v in query if k != "token"]
        target = rest
        if query:
            target += "?" + urllib.parse.urlencode(query)

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
        headers["X-Forwarded-Host"] = self.headers.get("Host", "")
        headers["X-Forwarded-Prefix"] = f"/access/{urllib.parse.quote(router_id)}"
        headers["X-Forwarded-Proto"] = "https" if self.headers.get("X-Forwarded-Proto", "") == "https" else "http"
        if body is not None:
            headers["Content-Length"] = str(len(body))

        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=35)
            conn.request(self.command, target, body=body, headers=headers)
            resp = conn.getresponse()
            resp_body = resp.read()
            resp_headers = []
            content_type = resp.getheader("Content-Type", "")
            prefix = f"/access/{urllib.parse.quote(router_id)}"
            for key, value in resp.getheaders():
                low = key.lower()
                if low in {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade", "content-length"}:
                    continue
                if low == "location":
                    value = rewrite_location(value, prefix, port)
                if low == "set-cookie":
                    value = rewrite_cookie_path(value, "/")
                resp_headers.append((key, value))
            resp_headers.append(("Set-Cookie", current_router_cookie(router_id)))
            if should_rewrite_body(content_type):
                resp_body = rewrite_html(resp_body, prefix, content_type)
            self.send_bytes(
                resp.status,
                resp_body,
                content_type or "application/octet-stream",
                resp_headers,
            )
        except Exception as exc:
            self.send_text(502, f"proxy error: {exc}")


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
  function fixUrl(url) {
    if (typeof url !== "string" || !url) return url;
    if (url.startsWith(prefix + "/")) return url;
    try {
      const absolute = /^[a-z][a-z0-9+.-]*:/i.test(url);
      const parsed = absolute ? new URL(url) : null;
      if (parsed && parsed.origin !== location.origin) return url;
      const value = parsed ? (parsed.pathname + parsed.search + parsed.hash) : url;
      for (const root of roots) {
        if (value === root || value.startsWith(root + "/") || value.startsWith(root + "?")) {
          return prefix + value;
        }
      }
    } catch (e) {}
    return url;
  }
  if (window.fetch) {
    const nativeFetch = window.fetch;
    window.fetch = function(input, init) {
      if (typeof input === "string") {
        input = fixUrl(input);
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
})();
</script>""" % prefix_json


def rewrite_html(body, prefix, content_type=""):
    text = body.decode("utf-8", errors="ignore")
    escaped_prefix = prefix.replace("/", "\\/")
    replacements = {
        'href="/': f'href="{prefix}/',
        'src="/': f'src="{prefix}/',
        'action="/': f'action="{prefix}/',
        'data-url="/': f'data-url="{prefix}/',
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
    if "text/html" in (content_type or "").lower():
        script = proxy_runtime_script(prefix)
        if "</head>" in text:
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
        or "javascript" in content_type
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


def cmd_serve(args):
    app = App(args.db, session_token(), agent_token(), args.public_url)
    with app.conn():
        pass
    auth = load_auth()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.app = app
    print(f"{APP_NAME} listening on http://{args.host}:{args.port}")
    print(f"HUB_LOGIN: {auth.get('username', 'admin')}")
    print(f"AGENT_TOKEN: {app.agent_token}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("")


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

    serve = sub.add_parser("serve", help="run web dashboard")
    serve.add_argument("--host", default=os.environ.get("OWRT_REMOTE_BIND", "0.0.0.0"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("OWRT_REMOTE_PORT", "8088")))
    serve.add_argument("--public-url", default=os.environ.get("OWRT_REMOTE_PUBLIC_URL", ""))
    serve.set_defaults(func=cmd_serve)

    return p


def main():
    args = parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
