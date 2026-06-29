#!/usr/bin/env python3
import argparse
import datetime as dt
import html
import http.client
import json
import os
import secrets
import sqlite3
import sys
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


APP_NAME = "OpenWrt Remote Hub"
STATE_DIR = Path(os.environ.get("OWRT_REMOTE_STATE_DIR", "/var/lib/owrt-remote"))
DB_PATH = Path(os.environ.get("OWRT_REMOTE_DB", str(STATE_DIR / "hub.db")))
ADMIN_TOKEN_FILE = STATE_DIR / "admin.token"
AGENT_TOKEN_FILE = STATE_DIR / "agent.token"
ONLINE_AFTER_SECONDS = int(os.environ.get("OWRT_REMOTE_ONLINE_AFTER", "75"))
DEFAULT_VLESS_PORT = int(os.environ.get("OWRT_REMOTE_VLESS_PORT", "8443"))


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


def admin_token():
    return os.environ.get("OWRT_REMOTE_ADMIN_TOKEN") or read_or_make_token(ADMIN_TOKEN_FILE)


def agent_token():
    return os.environ.get("OWRT_REMOTE_AGENT_TOKEN") or read_or_make_token(AGENT_TOKEN_FILE)


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
            created_at integer not null,
            updated_at integer not null,
            last_seen integer,
            status_json text not null default '{}'
        )
        """
    )
    conn.commit()


def get_router(conn, router_id):
    return conn.execute("select * from routers where id = ?", (router_id,)).fetchone()


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
    data["config_url"] = f"/router/{urllib.parse.quote(data['id'])}/config"
    data["xray_client_url"] = f"/router/{urllib.parse.quote(data['id'])}/xray-client.json"
    data.pop("status_json", None)
    return data


def upsert_router(conn, values):
    router_id = clean_router_id(values.get("id"))
    current = get_router(conn, router_id)
    ts = now_ts()
    payload = {
        "id": router_id,
        "name": values.get("name") or router_id,
        "role": values.get("role") or "node",
        "entry_port": int(values.get("entry_port") or 0),
        "vps_host": values.get("vps_host") or "",
        "vless_port": int(values.get("vless_port") or DEFAULT_VLESS_PORT),
        "vless_uuid": values.get("vless_uuid") or str(uuid.uuid4()),
        "vless_encryption": values.get("vless_encryption") or "none",
        "vless_decryption": values.get("vless_decryption") or "none",
        "vless_flow": values.get("vless_flow") or "",
        "reverse_tag": values.get("reverse_tag") or "reverse-in",
        "public_url": values.get("public_url") or "",
        "admin_host": values.get("admin_host") or "127.0.0.1",
        "admin_port": int(values.get("admin_port") or 80),
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
                public_url, admin_host, admin_port, created_at, updated_at
            ) values (
                :id, :name, :role, :entry_port, :vps_host, :vless_port, :vless_uuid,
                :vless_encryption, :vless_decryption, :vless_flow, :reverse_tag,
                :public_url, :admin_host, :admin_port, :created_at, :updated_at
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
        reverse_out = f"reverse-{router_id}"
        portal_in = f"portal-{router_id}"
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
        inbounds.append(
            {
                "tag": portal_in,
                "listen": "127.0.0.1",
                "port": entry_port,
                "protocol": "tunnel",
                "settings": {
                    "allowedNetwork": "tcp",
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

    return {
        "log": {"loglevel": "warning"},
        "inbounds": inbounds,
        "outbounds": [{"tag": "direct", "protocol": "freedom"}],
        "routing": {"rules": rules},
        "remarks": "OpenWrt Remote Hub server config",
    }


def make_router_xray_config(row):
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [],
        "outbounds": [
            {
                "tag": "router-admin",
                "protocol": "freedom",
                "settings": {
                    "redirect": f"{row['admin_host']}:{int(row['admin_port'])}",
                    "finalRules": [
                        {
                            "action": "allow",
                            "network": "tcp",
                            "ip": row["admin_host"],
                            "port": str(int(row["admin_port"])),
                        }
                    ],
                },
            },
            {
                "tag": row["reverse_tag"],
                "protocol": "vless",
                "settings": {
                    "address": row["vps_host"],
                    "port": int(row["vless_port"]),
                    "id": row["vless_uuid"],
                    "encryption": row["vless_encryption"],
                    "flow": row["vless_flow"],
                    "reverse": {"tag": row["reverse_tag"]},
                },
                "streamSettings": {"network": "tcp", "security": "none"},
            },
        ],
        "routing": {
            "rules": [
                {
                    "inboundTag": [row["reverse_tag"]],
                    "outboundTag": "router-admin",
                }
            ]
        },
        "remarks": f"OpenWrt Remote client for {row['id']}",
    }


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
        f"uci set owrtremote.main.admin_host='{sh_quote(row['admin_host'])}'",
        f"uci set owrtremote.main.admin_port='{int(row['admin_port'])}'",
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
        if chunk.strip().startswith("owrt_remote_admin="):
            continue
        parts.append(chunk.strip())
    return "; ".join(parts)


def dashboard_html(routers, token):
    routers_json = json.dumps(routers, ensure_ascii=False)
    safe_token = html.escape(token, quote=True)
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
h1{{margin:0;font-size:29px;line-height:1.2;letter-spacing:0}}.muted{{color:var(--muted)}}.top p{{margin:4px 0 0}}.links,.headerActions{{display:flex;align-items:center;gap:8px;flex-wrap:wrap}}.links{{margin-top:8px}}.links a,.badge{{position:relative;display:inline-flex;align-items:center;justify-content:center;gap:8px;min-height:36px;min-width:132px;padding:8px 14px;border:1px solid var(--line);border-radius:999px;background:rgba(255,255,255,.08);color:#f3e8ff;text-decoration:none;font-weight:800;font-size:13px;line-height:1;white-space:nowrap;overflow:hidden}}.headerActions{{justify-content:flex-end;padding-top:42px}}.badge{{background:var(--panel);color:var(--muted)}}.dot{{width:9px;height:9px;border-radius:999px;background:var(--red);box-shadow:0 0 13px rgba(251,113,133,.72)}}.dot.on{{background:var(--green);box-shadow:0 0 13px rgba(34,197,94,.75)}}.dot.warn{{background:var(--amber);box-shadow:0 0 13px rgba(245,158,11,.75)}}
.toolbar{{display:grid;grid-template-columns:1fr 1fr 110px 110px 150px auto;gap:10px;margin:18px 0;padding:14px;background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.045)),var(--panel);border:1px solid var(--line);border-radius:8px;box-shadow:0 18px 46px rgba(0,0,0,.20);backdrop-filter:blur(10px)}}
input,select{{min-width:0;border:1px solid var(--line);border-radius:8px;padding:10px 11px;background:rgba(8,5,18,.72);color:var(--text)}}button,.btn{{border:1px solid rgba(255,255,255,.10);border-radius:8px;padding:10px 13px;background:rgba(255,255,255,.10);color:#f7f2ff;font-weight:850;text-decoration:none;cursor:pointer;display:inline-flex;justify-content:center;align-items:center}}button.primary,.btn.primary{{background:var(--blue);color:#fff;box-shadow:0 10px 22px rgba(124,58,237,.22)}}button.bad,.btn.bad{{background:rgba(251,113,133,.16);color:#fecdd3}}.btn.good{{background:rgba(34,197,94,.16);color:#bbf7d0}}.btn.disabled{{opacity:.45;cursor:not-allowed}}
.sectionHead{{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin:24px 0 14px}}.sectionHead h2{{margin:0;font-size:22px}}.sectionHead p{{margin:3px 0 0;color:var(--muted)}}.summary{{display:flex;gap:10px;flex-wrap:wrap;justify-content:flex-end}}.miniStat{{border:1px solid var(--line);border-radius:999px;background:rgba(255,255,255,.07);padding:8px 12px;color:#ddd6fe;font-weight:750;white-space:nowrap}}
.cards{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}}.card{{position:relative;min-height:246px;overflow:hidden;background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.045)),var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px;box-shadow:0 18px 46px rgba(0,0,0,.28);backdrop-filter:blur(10px)}}.card::before{{content:"";position:absolute;inset:0 0 auto 0;height:3px;background:var(--green)}}.card.off::before{{background:var(--red)}}.card.warn::before{{background:var(--amber)}}.card.main{{grid-column:span 2}}
.cardTop{{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}}.routerMark{{display:grid;place-items:center;width:46px;height:46px;border-radius:8px;border:1px solid rgba(255,255,255,.14);background:rgba(255,255,255,.08)}}.routerIcon{{position:relative;width:28px;height:18px;border:2px solid #ddd6fe;border-radius:5px}}.routerIcon::before,.routerIcon::after{{content:"";position:absolute;top:-9px;width:9px;height:9px;border-top:2px solid #ddd6fe}}.routerIcon::before{{left:2px;transform:rotate(-34deg)}}.routerIcon::after{{right:2px;transform:rotate(34deg)}}.routerIcon span{{position:absolute;left:5px;right:5px;bottom:4px;display:flex;justify-content:space-between}}.routerIcon span::before,.routerIcon span::after{{content:"";width:4px;height:4px;border-radius:50%;background:#ddd6fe}}
.status{{display:inline-flex;align-items:center;gap:7px;border-radius:999px;border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.07);padding:7px 10px;font-weight:850;font-size:12px}}.status i{{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 13px var(--green)}}.status.off i{{background:var(--red);box-shadow:0 0 13px var(--red)}}.status.warn i{{background:var(--amber);box-shadow:0 0 13px var(--amber)}}.name{{margin:12px 0 0;font-size:19px;font-weight:900;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.metaLine{{margin-top:3px;color:var(--muted)}}.tagRow{{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}}.tag{{border:1px solid var(--line);border-radius:999px;padding:5px 9px;background:rgba(255,255,255,.06);color:#ddd6fe;font-size:12px;font-weight:750}}
.metrics{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:14px}}.metric{{border:1px solid var(--line);border-radius:8px;background:rgba(255,255,255,.055);padding:9px}}.metric span{{display:block;color:var(--muted);font-size:11px}}.metric strong{{display:block;margin-top:2px;font-size:14px;word-break:break-word}}.actions{{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}}.empty{{grid-column:1/-1;border:1px dashed var(--line);border-radius:8px;padding:30px;background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.045)),var(--panel);text-align:center;color:var(--muted)}}.hint{{margin-top:16px;padding:13px;border:1px solid var(--line);border-radius:8px;background:linear-gradient(180deg,rgba(255,255,255,.08),rgba(255,255,255,.045)),var(--panel);color:var(--muted)}}code{{background:rgba(255,255,255,.10);border-radius:6px;padding:2px 5px;color:#f3e8ff}}
@media(max-width:980px){{.cards{{grid-template-columns:repeat(2,minmax(0,1fr))}}.toolbar{{grid-template-columns:1fr 1fr}}.card.main{{grid-column:span 2}}.top{{flex-direction:column}}.headerActions{{padding-top:0;justify-content:flex-start}}}}
@media(max-width:680px){{.wrap{{padding:14px}}.cards,.toolbar{{grid-template-columns:1fr}}.card.main{{grid-column:span 1}}.top,.sectionHead{{align-items:flex-start;flex-direction:column}}.headerActions,.summary{{justify-content:flex-start}}.links a,.badge{{width:100%;min-width:0}}h1{{font-size:24px}}}}
</style>
</head>
<body>
<main class="wrap">
  <section class="top">
    <div class="brand">
      <div>
        <h1>OpenWrt Remote Hub</h1>
        <p class="muted">Карточки роутеров, heartbeat и вход в LuCI через VPS.</p>
        <div class="links">
          <a href="https://t.me/kzolotarev95" target="_blank" rel="noopener noreferrer">Telegram</a>
          <a href="https://github.com/kzolotarev95" target="_blank" rel="noopener noreferrer">GitHub</a>
        </div>
      </div>
    </div>
    <div class="headerActions">
      <div class="badge"><span class="dot on"></span>Hub online</div>
    </div>
  </section>

  <form class="toolbar" id="routerForm">
    <input name="id" placeholder="router id: main" required>
    <input name="name" placeholder="Название роутера" required>
    <select name="role"><option value="node">node</option><option value="main">main</option></select>
    <input name="entry_port" placeholder="18080" inputmode="numeric" required>
    <input name="vps_host" placeholder="VPS IP/domain" required>
    <button class="primary">Добавить</button>
  </form>

  <section class="sectionHead">
    <div>
      <h2>Карточки роутеров</h2>
      <p>Главный роутер и подключенные точки с живым online/offline.</p>
    </div>
    <div class="summary">
      <div class="miniStat" id="statTotal">0 роутеров</div>
      <div class="miniStat" id="statOnline">0 онлайн</div>
      <div class="miniStat" id="statOffline">0 оффлайн</div>
    </div>
  </section>

  <section id="cards" class="cards"></section>
  <div class="hint muted">После добавления роутера открой <code>OpenWrt config</code>, вставь команды на роутере и перегенерируй server Xray config командой <code>owrt-remote-hub.py render-xray --out /etc/xray/owrt-remote.json</code>.</div>
</main>
<script>
window.ADMIN_TOKEN = "{safe_token}";
window.ROUTERS = {routers_json};
const cards = document.getElementById('cards');
const tokenParam = () => 'token=' + encodeURIComponent(window.ADMIN_TOKEN);

function ago(iso) {{
  if (!iso) return 'never';
  const diff = Math.max(0, Math.floor((Date.now() - Date.parse(iso)) / 1000));
  if (diff < 60) return diff + ' sec ago';
  if (diff < 3600) return Math.floor(diff / 60) + ' min ago';
  return Math.floor(diff / 3600) + ' h ago';
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
    const access = r.public_url || (r.access_url + '?' + tokenParam());
    const tags = [
      isMain ? 'главный' : 'node',
      r.entry_port ? 'entry ' + r.entry_port : '',
      r.reverse_tag || '',
      (r.admin_host || '127.0.0.1') + ':' + (r.admin_port || 80)
    ].filter(Boolean).slice(0, 4);
    const tagHtml = tags.map(t => `<span class="tag">${{escapeHtml(t)}}</span>`).join('');
    const adminButton = online
      ? `<a class="btn primary" href="${{escapeAttr(access)}}">Админка</a>`
      : `<span class="btn primary disabled">Админка</span>`;
    return `<article class="card ${{isMain ? 'main' : ''}} ${{online ? '' : 'off'}}">
      <div class="cardTop">
        <div class="routerMark"><div class="routerIcon"><span></span></div></div>
        <div class="status ${{stateClass}}"><i></i>${{stateText}}</div>
      </div>
      <div class="name">${{escapeHtml(r.name)}}</div>
      <div class="metaLine">ID: ${{escapeHtml(r.id)}} · роль: ${{escapeHtml(role)}}</div>
      <div class="tagRow">${{tagHtml}}</div>
      <div class="metrics">
        <div class="metric"><span>Модель</span><strong>${{escapeHtml(model)}}</strong></div>
        <div class="metric"><span>OpenWrt</span><strong>${{escapeHtml(release)}}</strong></div>
        <div class="metric"><span>Xray</span><strong>${{escapeHtml(xray)}}</strong></div>
        <div class="metric"><span>Был на связи</span><strong>${{escapeHtml(ago(r.last_seen_iso))}}</strong></div>
      </div>
      <div class="actions">
        ${{adminButton}}
        <a class="btn good" href="${{escapeAttr(r.config_url)}}?${{tokenParam()}}">OpenWrt config</a>
        <a class="btn" href="${{escapeAttr(r.xray_client_url)}}?${{tokenParam()}}">Client JSON</a>
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

async function loadRouters() {{
  const res = await fetch('/api/routers?' + tokenParam(), {{cache: 'no-store'}});
  if (res.ok) {{
    const data = await res.json();
    window.ROUTERS = data.routers;
    render(window.ROUTERS);
  }}
}}

document.getElementById('routerForm').addEventListener('submit', async (ev) => {{
  ev.preventDefault();
  const body = new URLSearchParams(new FormData(ev.currentTarget));
  const res = await fetch('/api/router?' + tokenParam(), {{method: 'POST', body}});
  if (res.ok) {{
    ev.currentTarget.reset();
    await loadRouters();
  }} else {{
    alert(await res.text());
  }}
}});

cards.addEventListener('click', async (ev) => {{
  const id = ev.target?.dataset?.delete;
  if (!id) return;
  if (!confirm('Удалить роутер ' + id + '?')) return;
  const res = await fetch('/api/router/' + encodeURIComponent(id) + '/delete?' + tokenParam(), {{method: 'POST'}});
  if (res.ok) await loadRouters();
}});

render(window.ROUTERS);
setInterval(loadRouters, 5000);
</script>
</body>
</html>"""


def login_html():
    return """<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>OpenWrt Remote Hub</title>
<style>body{margin:0;background:#f5f7fb;color:#172033;font:14px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.box{max-width:380px;margin:12vh auto;background:#fff;border:1px solid #d9e1ec;border-radius:8px;padding:20px;box-shadow:0 12px 30px rgba(20,35,60,.06)}h1{margin:0 0 8px;font-size:24px}p{color:#687385}input,button{width:100%;border-radius:8px;padding:11px;margin-top:10px}input{border:1px solid #d9e1ec}button{border:0;background:#2563eb;color:#fff;font-weight:800}</style></head>
<body><form class="box" method="get"><h1>OpenWrt Remote Hub</h1><p>Вставь ADMIN_TOKEN с VPS.</p><input name="token" type="password" autofocus><button>Открыть</button></form></body></html>"""


class App:
    def __init__(self, db_path, admin, agent, public_url):
        self.db_path = Path(db_path)
        self.admin_token = admin
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

    def admin_ok(self):
        query_token = self.query().get("token", [""])[0]
        if secrets.compare_digest(query_token, self.app.admin_token):
            return True
        cookies = parse_cookies(self.headers.get("Cookie", ""))
        return secrets.compare_digest(cookies.get("owrt_remote_admin", ""), self.app.admin_token)

    def admin_cookie_needed(self):
        query_token = self.query().get("token", [""])[0]
        return secrets.compare_digest(query_token, self.app.admin_token)

    def agent_ok(self):
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return secrets.compare_digest(auth[7:].strip(), self.app.agent_token)
        return False

    def send_bytes(self, status, body, content_type="text/plain; charset=utf-8", extra_headers=None, set_cookie=False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        if set_cookie:
            self.send_header("Set-Cookie", f"owrt_remote_admin={self.app.admin_token}; HttpOnly; SameSite=Lax; Path=/")
        if extra_headers:
            for key, value in extra_headers:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, status, text, content_type="text/plain; charset=utf-8"):
        self.send_bytes(status, text.encode("utf-8"), content_type, set_cookie=self.admin_cookie_needed())

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_bytes(status, body, "application/json; charset=utf-8", set_cookie=self.admin_cookie_needed())

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

    def do_GET(self):
        path = self.parsed().path
        if path == "/health":
            self.send_json(200, {"ok": True})
            return
        if path.startswith("/access/"):
            self.proxy_access(path)
            return
        if not self.require_admin():
            return
        if path == "/" or path == "":
            with self.app.conn() as conn:
                routers = [row_to_router(r) for r in list_router_rows(conn)]
            self.send_bytes(
                200,
                dashboard_html(routers, self.app.admin_token).encode("utf-8"),
                "text/html; charset=utf-8",
                set_cookie=self.admin_cookie_needed(),
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
        if not self.require_admin():
            return
        if path == "/api/router":
            try:
                payload = self.read_payload()
                with self.app.conn() as conn:
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
        skip = {"host", "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade", "content-length"}
        for key, value in self.headers.items():
            if key.lower() in skip:
                continue
            if key.lower() == "cookie":
                value = clean_forward_cookie(value)
                if not value:
                    continue
            headers[key] = value
        headers["Host"] = f"127.0.0.1:{port}"
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
                    value = rewrite_cookie_path(value, prefix + "/")
                resp_headers.append((key, value))
            if "text/html" in content_type:
                resp_body = rewrite_html(resp_body, prefix)
            self.send_bytes(resp.status, resp_body, content_type or "application/octet-stream", resp_headers)
        except Exception as exc:
            self.send_text(502, f"proxy error: {exc}")

    def redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()


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


def rewrite_html(body, prefix):
    text = body.decode("utf-8", errors="ignore")
    replacements = {
        'href="/': f'href="{prefix}/',
        'src="/': f'src="{prefix}/',
        'action="/': f'action="{prefix}/',
        "href='/": f"href='{prefix}/",
        "src='/": f"src='{prefix}/",
        "action='/": f"action='{prefix}/",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text.encode("utf-8")


def cmd_init(args):
    with connect(args.db) as conn:
        init_db(conn)
    print(f"DB: {args.db}")
    print(f"ADMIN_TOKEN: {admin_token()}")
    print(f"AGENT_TOKEN: {agent_token()}")


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
            },
        )
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
    app = App(args.db, admin_token(), agent_token(), args.public_url)
    with app.conn():
        pass
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.app = app
    print(f"{APP_NAME} listening on http://{args.host}:{args.port}")
    print(f"ADMIN_TOKEN: {app.admin_token}")
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
    add.set_defaults(func=cmd_add_router)

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
    serve.add_argument("--host", default=os.environ.get("OWRT_REMOTE_BIND", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("OWRT_REMOTE_PORT", "8088")))
    serve.add_argument("--public-url", default=os.environ.get("OWRT_REMOTE_PUBLIC_URL", ""))
    serve.set_defaults(func=cmd_serve)

    return p


def main():
    args = parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
