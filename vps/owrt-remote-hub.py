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
:root{{color-scheme:light;--bg:#dfeef1;--panel:rgba(255,255,255,.84);--text:#152033;--muted:#687385;--line:#cbd8e6;--blue:#2563eb;--green:#16a34a;--red:#dc2626;--amber:#d97706;--teal:#0f766e;--grid:rgba(20,65,95,.12)}}
*{{box-sizing:border-box}}body{{min-height:100vh;margin:0;background-color:var(--bg);background-image:radial-gradient(circle at 12% 8%,rgba(15,118,110,.22),transparent 28%),radial-gradient(circle at 85% 0,rgba(37,99,235,.18),transparent 30%),linear-gradient(135deg,rgba(255,255,255,.30),rgba(255,255,255,0)),repeating-linear-gradient(0deg,transparent 0 30px,var(--grid) 31px),repeating-linear-gradient(90deg,transparent 0 30px,var(--grid) 31px);background-attachment:fixed;color:var(--text);font:14px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
.wrap{{max-width:1220px;margin:0 auto;padding:22px}}.top{{display:flex;align-items:center;justify-content:space-between;gap:16px;border-bottom:1px solid var(--line);padding:4px 0 18px}}
.brand{{display:flex;align-items:center;gap:14px}}
h1{{margin:0;font-size:29px;letter-spacing:0}}.muted{{color:var(--muted)}}.top p{{margin:4px 0 0}}.links{{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}}.links a{{border:1px solid var(--line);border-radius:999px;background:rgba(255,255,255,.70);color:#1f2a44;padding:5px 10px;text-decoration:none;font-weight:750}}.headerActions{{display:flex;align-items:center;justify-content:flex-end;gap:10px;flex-wrap:wrap}}.badge{{display:inline-flex;gap:8px;align-items:center;border:1px solid var(--line);background:var(--panel);border-radius:999px;padding:8px 12px;color:var(--muted);white-space:nowrap}}
.dot{{width:9px;height:9px;border-radius:999px;background:var(--red)}}.dot.on{{background:var(--green)}}.toolbar{{display:grid;grid-template-columns:1fr 1fr 110px 110px 150px auto;gap:10px;margin:18px 0;padding:14px;background:var(--panel);border:1px solid var(--line);border-radius:8px}}
input,select{{min-width:0;border:1px solid var(--line);border-radius:8px;padding:10px 11px;background:#fff;color:var(--text)}}button,.btn{{border:0;border-radius:8px;padding:10px 13px;background:#e8eef8;color:#1f2a44;font-weight:800;text-decoration:none;cursor:pointer;display:inline-flex;justify-content:center;align-items:center}}button.primary,.btn.primary{{background:var(--blue);color:#fff}}button.bad{{background:#fee2e2;color:#991b1b}}
.cards{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}}.card{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:15px;box-shadow:0 12px 30px rgba(20,35,60,.08);backdrop-filter:blur(8px)}}
.cardHead{{display:flex;gap:12px;align-items:flex-start;justify-content:space-between}}.routerIcon{{min-width:46px;height:46px;padding:0 8px;border-radius:8px;background:#eef6ff;color:#1d4ed8;display:grid;place-items:center;font-weight:900;font-size:12px;border:1px solid #dbeafe}}
.title{{display:flex;gap:10px;align-items:center;min-width:0}}.title h2{{font-size:18px;margin:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.role{{border:1px solid var(--line);border-radius:999px;padding:3px 8px;color:var(--muted);font-size:12px;background:#fbfdff}}
.state{{display:flex;align-items:center;gap:7px;font-weight:800}}.state.off{{color:var(--red)}}.state.on{{color:var(--green)}}.meta{{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin:14px 0}}.metric{{border:1px solid var(--line);border-radius:8px;padding:10px;background:rgba(255,255,255,.58)}}.metric span{{display:block;color:var(--muted);font-size:12px}}.metric strong{{display:block;margin-top:3px;word-break:break-word}}
.actions{{display:flex;gap:8px;flex-wrap:wrap}}.empty{{border:1px dashed var(--line);border-radius:8px;padding:30px;background:#fff;text-align:center;color:var(--muted)}}.hint{{margin-top:16px;padding:13px;border:1px solid var(--line);border-radius:8px;background:#fff}}code{{background:#eef2f7;border-radius:6px;padding:2px 5px}}
@media(max-width:980px){{.cards{{grid-template-columns:repeat(2,minmax(0,1fr))}}.toolbar{{grid-template-columns:1fr 1fr}}}}
@media(max-width:680px){{.wrap{{padding:14px}}.cards,.toolbar{{grid-template-columns:1fr}}.top{{align-items:flex-start;flex-direction:column}}.headerActions{{justify-content:flex-start}}h1{{font-size:24px}}}}
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

function render(list) {{
  if (!list.length) {{
    cards.innerHTML = '<div class="empty">Пока нет роутеров. Добавь первый, например <b>main</b>.</div>';
    return;
  }}
  cards.innerHTML = list.map(r => {{
    const state = r.online ? 'on' : 'off';
    const stateText = r.online ? 'online' : 'offline';
    const model = (r.status && r.status.model) || r.status?.board || 'OpenWrt';
    const release = (r.status && r.status.release) || 'waiting heartbeat';
    const xray = (r.status && r.status.xray) || 'unknown';
    const access = r.public_url || (r.access_url + '?' + tokenParam());
    return `<article class="card">
      <div class="cardHead">
        <div class="title">
          <div class="routerIcon">OWRT</div>
          <div>
            <h2>${{escapeHtml(r.name)}}</h2>
            <div class="muted">${{escapeHtml(r.id)}} · <span class="role">${{escapeHtml(r.role)}}</span></div>
          </div>
        </div>
        <div class="state ${{state}}"><span class="dot ${{state === 'on' ? 'on' : ''}}"></span>${{stateText}}</div>
      </div>
      <div class="meta">
        <div class="metric"><span>Model</span><strong>${{escapeHtml(model)}}</strong></div>
        <div class="metric"><span>OpenWrt</span><strong>${{escapeHtml(release)}}</strong></div>
        <div class="metric"><span>Xray</span><strong>${{escapeHtml(xray)}}</strong></div>
        <div class="metric"><span>Last seen</span><strong>${{escapeHtml(ago(r.last_seen_iso))}}</strong></div>
      </div>
      <div class="actions">
        <a class="btn primary" href="${{access}}">Открыть админку</a>
        <a class="btn" href="${{r.config_url}}?${{tokenParam()}}">OpenWrt config</a>
        <a class="btn" href="${{r.xray_client_url}}?${{tokenParam()}}">Client JSON</a>
        <button class="bad" data-delete="${{escapeHtml(r.id)}}">Удалить</button>
      </div>
    </article>`;
  }}).join('');
}}

function escapeHtml(s) {{
  return String(s ?? '').replace(/[&<>"']/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));
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
