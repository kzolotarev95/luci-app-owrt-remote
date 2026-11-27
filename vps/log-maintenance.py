#!/usr/bin/env python3
"""Bounded VPS log maintenance. Never opens the Hub database or router archives.

Uses the distribution's logrotate rules and reopen hooks, with retention
overrides in a temporary configuration. One owner: system logrotate.timer.
"""
import argparse
import ast
import datetime as dt
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time

MARKER = "# Managed by OpenWrt Remote log maintenance"
INSTALL_PATH = Path("/opt/owrt-remote/log-maintenance.py")
BACKUP = Path("/root/owrt-remote-backup-before-log-maintenance-v121")
STATE = Path("/var/lib/owrt-remote-log-maintenance")
RUN = Path("/run/owrt-remote-log-maintenance")
RESULT = Path("/var/log/owrt-remote-maintenance.log")
HUB = Path("/opt/owrt-remote/owrt-remote-hub.py")
LEGACY = Path("/etc/logrotate.d/owrt-remote")
POLICY = "\n    daily\n    maxsize 20M\n    rotate 1\n    maxage 1\n    compress\n    nodelaycompress\n"
CONFIGS = {
    "/etc/systemd/journald.conf.d/owrt-remote-limits.conf": MARKER + """
[Journal]
SystemMaxUse=50M
SystemKeepFree=500M
SystemMaxFileSize=10M
RuntimeMaxUse=16M
RuntimeKeepFree=64M
MaxRetentionSec=7day
Compress=yes
""",
    "/etc/systemd/system/logrotate.timer.d/owrt-remote.conf": MARKER + """
[Timer]
OnCalendar=
OnCalendar=*-*-* 03:20:00
AccuracySec=1min
RandomizedDelaySec=0
Persistent=true
""",
    "/etc/systemd/system/logrotate.service.d/owrt-remote.conf": MARKER + """
[Service]
ExecStart=
ExecStart=/usr/bin/python3 /opt/owrt-remote/log-maintenance.py run
Nice=10
IOSchedulingClass=idle
TimeoutStartSec=30min
""",
    "/etc/nginx/conf.d/owrt-remote-log-policy.conf": MARKER + """
# Only routine successful traffic is quiet; errors and other actions remain.
map "$uri:$status" $owrt_remote_access_log {
    default 1;
    ~^/api/notifications/wait:[23][0-9][0-9]$ 0;
    ~^/api/ssh-session/[^/]+/read:[23][0-9][0-9]$ 0;
    ~^/api/heartbeat:[23][0-9][0-9]$ 0;
}
""",
}


def atomic(path, data, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise RuntimeError("Refusing symlink: " + str(path))
    fd, name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data if isinstance(data, bytes) else data.encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def command(args, timeout=120, check=True):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=timeout, text=True, errors="replace")
    if check and result.returncode:
        raise RuntimeError(f"{shlex.join(args)}: rc={result.returncode}; {result.stdout[-6000:]}")
    return result


def record(level, message):
    """One small result file, at most 64 KiB plus one 64 KiB previous file."""
    line = (dt.datetime.now().astimezone().isoformat(timespec="seconds") +
            f" [{level}] " + str(message).replace("\n", " ")[:4000] + "\n").encode()
    print(line.decode().rstrip(), flush=True)
    if RESULT.is_symlink() or RESULT.with_suffix(".log.1").is_symlink():
        raise RuntimeError("Unsafe maintenance log path")
    if RESULT.exists() and RESULT.stat().st_size + len(line) > 65536:
        with RESULT.open("rb") as stream:
            stream.seek(max(0, os.fstat(stream.fileno()).st_size - 65536))
            previous = stream.read(65536)
        atomic(RESULT.with_suffix(".log.1"), previous)
        # Keep the active inode available to readers.
        with RESULT.open("r+b") as stream:
            stream.truncate(0)
    fd = os.open(RESULT, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "ab") as stream:
        stream.write(line)


def legacy_policy(path=LEGACY):
    if not path.exists():
        return False
    if path.is_symlink():
        return False
    lines = [line.strip() for line in path.read_text().splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    expected = {"daily", "rotate 7", "size 50M", "missingok", "notifempty",
                "compress", "delaycompress", "copytruncate"}
    return (len(lines) == 10 and lines[0] == "/var/log/nginx/*.log {"
            and lines[-1] == "}" and set(lines[1:-1]) == expected)


def nginx_policies():
    """Patch existing standard destinations; never add a second access logger.

    sites-enabled entries are commonly symlinks: backup/edit their resolved
    targets in /etc/nginx, leaving the symlinks themselves intact.
    """
    rendered = command(["nginx", "-T"]).stdout
    configs = {}
    for filename in re.findall(r"^# configuration file ([^\n]+):$", rendered, re.M):
        path = Path(filename).resolve()
        if str(path) == "/etc/nginx/conf.d/owrt-remote-log-policy.conf":
            continue
        # Certbot includes this standard TLS policy outside /etc/nginx.
        # Read-only exception for this exact resolved file, not its directory.
        if str(path) == "/etc/letsencrypt/options-ssl-nginx.conf" and path.is_file():
            continue
        if not str(path).startswith("/etc/nginx/") or not path.is_file():
            raise RuntimeError("Unexpected nginx configuration: " + str(path))
        text = path.read_text()
        def add_condition(match):
            directive = match.group(0)
            if "$owrt_remote_access_log" in directive:
                return directive
            if re.search(r"\bif=", directive):
                raise RuntimeError("Custom nginx log condition requires manual review: " + str(path))
            # nginx needs an explicit format before optional parameters. Ubuntu's
            # default is simply `access_log /var/log/nginx/access.log;`.
            prefix = directive[:-1]
            if len(shlex.split(prefix)) == 2:
                prefix += " combined"
            return prefix + " if=$owrt_remote_access_log;"
        updated = re.sub(r"(?m)^\s*access_log\s+/var/log/nginx/access\.log(?:\s+[^;\n]+)?;", add_condition, text)
        if updated != text:
            configs[str(path)] = updated
    return configs


def flatten_config(path, seen=None):
    """Read trusted distribution configs; never execute hooks during parsing."""
    path = Path(path)
    seen = set() if seen is None else seen
    if path in seen or len(seen) > 128:
        raise RuntimeError("Recursive or oversized logrotate include: " + str(path))
    info = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError("Untrusted logrotate config: " + str(path))
    if info.st_size > 262144:
        raise RuntimeError("Oversized logrotate config: " + str(path))
    seen.add(path)
    lines, script = [], False
    for line in path.read_text().splitlines(keepends=True):
        word = line.strip().split(" ", 1)[0]
        if word in ("firstaction", "prerotate", "postrotate", "lastaction", "preremove"):
            script = True
        elif word == "endscript":
            script = False
        if not script and re.match(r"^\s*include\s+", line):
            parts = shlex.split(line, comments=True)
            if len(parts) != 2 or not (parts[1] == "/etc/logrotate.conf" or parts[1] == "/etc/logrotate.d" or parts[1].startswith("/etc/logrotate.d/")):
                raise RuntimeError("Unsupported logrotate include: " + line.strip())
            include = Path(parts[1])
            children = sorted(include.iterdir()) if include.is_dir() else [include]
            for child in children:
                if child.is_dir() or re.search(r"(?:~|\.(?:bak|old|disabled|dpkg-.+|rpmnew|rpmsave))$", child.name):
                    continue
                if child == LEGACY and legacy_policy():
                    continue
                lines.append(flatten_config(child, seen))
        else:
            lines.append(line)
    seen.remove(path)
    result = "".join(lines)
    if len(result) > 1048576:
        raise RuntimeError("Logrotate config exceeds 1 MiB")
    return result + "\n"


def retention_config(source):
    """Keep native create/su/reopen hooks, override only retention per stanza.

    Refuse ambiguous syntax and targets outside /var/log before any rotation.
    In particular, no arbitrary service file in /var/lib can enter cleanup.
    """
    output, patterns, header, block = [], [], [], []
    script, inside = False, False
    for line in source.splitlines(keepends=True):
        stripped = line.strip()
        if inside:
            word = stripped.split(" ", 1)[0]
            if not script and word in ("firstaction", "prerotate", "postrotate", "lastaction", "preremove"):
                script = True
            elif script and word == "endscript":
                script = False
            if not script and stripped == "}":
                # olddir may put archives outside /var/log: refuse to clean there.
                check_script = False
                for item in block:
                    word = item.strip().split(" ", 1)[0]
                    if word in ("firstaction", "prerotate", "postrotate", "lastaction", "preremove"):
                        check_script = True
                    elif word == "endscript":
                        check_script = False
                    if check_script:
                        continue
                    if re.match(r"\s*(?:olddir|include)\s+", item):
                        raise RuntimeError("olddir/include inside log stanza requires administrator review")
                output.extend(block)
                output.append(POLICY + "}\n")
                inside, block = False, []
            elif not script and ("{" in stripped or stripped.startswith("}")):
                raise RuntimeError("Unsupported logrotate block syntax: " + stripped)
            else:
                block.append(line)
            continue
        if header or stripped.startswith(("/", '"', "'")):
            header.append(line)
            if "{" in line:
                combined = "".join(header)
                if combined.count("{") != 1 or combined.split("{", 1)[1].strip():
                    raise RuntimeError("Unsupported inline logrotate stanza")
                targets = shlex.split(combined.split("{", 1)[0], comments=True)
                for target in targets:
                    if not target.startswith("/var/log/") or ".." in Path(target).parts:
                        raise RuntimeError("Refusing log target outside /var/log: " + target)
                patterns.extend(targets)
                output.extend(header)
                header, inside = [], True
        else:
            output.append(line)
    if header or inside or script:
        raise RuntimeError("Incomplete logrotate configuration")
    return "".join(output), patterns


def archive_name(active, candidate):
    if not candidate.startswith(active):
        return False
    suffix = candidate[len(active):]
    return bool(re.fullmatch(r"(?:\.\d+|\.old|[-.]\d{8}(?:[-.]\d{4,6})?)(?:\.(?:gz|xz|bz2|zst))?", suffix))


def open_inodes():
    opened = set()
    for directory in Path("/proc").iterdir():
        if not directory.name.isdigit():
            continue
        try:
            for fd in (directory / "fd").iterdir():
                try:
                    info = fd.stat()
                    opened.add((info.st_dev, info.st_ino))
                except FileNotFoundError:
                    pass
        except FileNotFoundError:
            pass
    return opened


def prune_archives(active_paths, emergency=False, now=None, opened=None):
    now = time.time() if now is None else now
    opened = open_inodes() if opened is None else opened
    removed, freed = 0, 0
    for active in sorted(set(Path(p) for p in active_paths)):
        if not active.is_file() or active.is_symlink() or active.resolve() != active:
            continue
        for candidate in active.parent.iterdir():
            if not archive_name(active.name, candidate.name):
                continue
            info = candidate.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                continue
            if not emergency and now - info.st_mtime <= 86400:
                continue
            if (info.st_dev, info.st_ino) in opened:
                continue
            latest = candidate.lstat()
            if (latest.st_dev, latest.st_ino, latest.st_mtime_ns) != (info.st_dev, info.st_ino, info.st_mtime_ns):
                continue
            candidate.unlink()  # Only verified closed archives, never active logs.
            removed += 1
            freed += info.st_size
    return removed, freed


def disk_levels():
    result = []
    for path in ("/", "/var/log"):
        usage = shutil.disk_usage(path)
        # Match df: blocks reserved for root are not available to log writers.
        available_total = usage.used + usage.free
        percent = 100 * usage.used / available_total if available_total else 100
        level = "CRITICAL" if percent > 90 else "WARN" if percent > 75 else "OK"
        result.append((path, percent, level))
    return result


def service_status():
    for name in ("owrt-remote", "nginx", "rsyslog", "systemd-journald"):
        result = command(["systemctl", "is-active", name], check=False)
        print(f"{name}: {result.stdout.strip()}")


def validate_journal_config():
    effective, section = {}, ""
    for line in command(["systemd-analyze", "cat-config", "systemd/journald.conf"]).stdout.splitlines():
        text = line.strip()
        if text.startswith("["):
            section = text
        elif section == "[Journal]" and not text.startswith(("#", ";")) and "=" in text:
            key, value = text.split("=", 1)
            effective[key.strip()] = value.strip()
    for key, value in {"SystemMaxUse": "50M", "SystemKeepFree": "500M",
                       "MaxRetentionSec": "7day", "Compress": "yes"}.items():
        if effective.get(key) != value:
            raise RuntimeError("Another journald configuration overrides " + key + "; administrator review required")


def run(force=False):
    import fcntl
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    RUN.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (RUN / "lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Maintenance already running; skipped")
            return
        today = dt.date.today().isoformat()
        stamp = STATE / "last-attempt"
        if not force and stamp.exists() and stamp.read_text().strip() == today:
            print("Daily attempt already made; use run --force to retry an error")
            return
        # Write before work: even a process crash cannot cause a second daily run.
        atomic(stamp, today + "\n")
        try:
            config, patterns = retention_config(flatten_config("/etc/logrotate.conf"))
            active = [Path(p) for pattern in patterns for p in glob.glob(pattern)]
            emergency = any(percent > 85 for _, percent, _ in disk_levels())
            before = disk_levels()
            for path, percent, level in before:
                record(level, f"before {path}: {percent:.1f}% used")
            atomic(RUN / "logrotate.conf", config)
            command(["logrotate", "--debug", str(RUN / "logrotate.conf")])
            removed, freed = prune_archives(active, emergency=emergency)
            # Same state/lock as distro logrotate, not a private competing state.
            command(["logrotate", "--wait-for-state-lock", "--force", str(RUN / "logrotate.conf")], timeout=1500)
            command(["journalctl", "--rotate"], timeout=60)
            vacuum = command(["journalctl", "--vacuum-time=1d", "--vacuum-size=" + ("10M" if emergency else "50M")])
            more, size = prune_archives(active, emergency=emergency)
            removed += more
            freed += size
            after = disk_levels()
            result = {"day": today, "ok": True, "archives_removed": removed,
                      "bytes_removed": freed, "emergency": emergency,
                      "disk_after": after, "finished_at": time.time()}
            atomic(STATE / "result.json", json.dumps(result) + "\n")
            record("OK", f"completed; archives_removed={removed}; bytes_removed={freed}; emergency={emergency}; {vacuum.stdout[-500:]}")
            for path, percent, level in after:
                record(level, f"after {path}: {percent:.1f}% used; database and user data excluded")
            print(command(["journalctl", "--disk-usage"]).stdout.strip())
        except Exception as exc:
            atomic(STATE / "result.json", json.dumps({"day": today, "ok": False, "error": str(exc)[-6000:]}) + "\n")
            record("ERROR", exc)
            raise
        finally:
            (RUN / "logrotate.conf").unlink(missing_ok=True)


def snapshot(paths, backup):
    backup.mkdir(mode=0o700, parents=True, exist_ok=True)
    manifest_path = backup / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
    else:
        manifest = {"files": [], "timer_enabled": command(["systemctl", "is-enabled", "logrotate.timer"], check=False).stdout.strip(),
                    "timer_active": command(["systemctl", "is-active", "logrotate.timer"], check=False).stdout.strip()}
    known = {item["path"] for item in manifest["files"]}
    for path in paths:
        path = Path(path)
        if str(path) in known:
            continue
        if path.is_symlink():
            raise RuntimeError("Refusing to replace symlink: " + str(path))
        item = {"path": str(path), "exists": path.exists(), "copy": str(len(manifest["files"]))}
        if path.exists():
            info = path.stat()
            item.update(mode=stat.S_IMODE(info.st_mode), uid=info.st_uid, gid=info.st_gid)
            atomic(backup / item["copy"], path.read_bytes())
        manifest["files"].append(item)
    atomic(manifest_path, json.dumps(manifest))


def restore(backup):
    manifest = json.loads((backup / "manifest.json").read_text())
    command(["systemctl", "stop", "logrotate.timer"])
    for item in manifest["files"]:
        path = Path(item["path"])
        if item["exists"]:
            atomic(path, (backup / item["copy"]).read_bytes(), item["mode"])
            os.chown(path, item["uid"], item["gid"])
        else:
            path.unlink(missing_ok=True)
    command(["systemctl", "daemon-reload"])
    command(["systemctl", "restart", "systemd-journald"])
    if shutil.which("nginx"):
        command(["nginx", "-t"])
        if command(["systemctl", "is-active", "nginx"], check=False).returncode == 0:
            command(["systemctl", "reload", "nginx"])
    if manifest["timer_enabled"] == "enabled":
        command(["systemctl", "enable", "logrotate.timer"])
    else:
        command(["systemctl", "disable", "logrotate.timer"])
    if manifest["timer_active"] == "active":
        command(["systemctl", "start", "logrotate.timer"])
    if any(item["path"] == str(HUB) for item in manifest["files"]):
        command(["systemctl", "restart", "owrt-remote"])
    print("Configuration restored. Already rotated/deleted archives cannot be restored.")


def install(hub_candidate=None, backup=BACKUP):
    for executable in ("logrotate", "journalctl", "systemctl", "systemd-analyze"):
        if not shutil.which(executable):
            raise RuntimeError("Missing prerequisite: " + executable)
    if command(["systemctl", "is-active", "logrotate.service"], check=False).stdout.strip() in ("active", "activating"):
        raise RuntimeError("Wait for the running system logrotate.service to finish")
    if LEGACY.exists() and not legacy_policy():
        raise RuntimeError("Custom /etc/logrotate.d/owrt-remote found; manual review required")
    retention_config(flatten_config("/etc/logrotate.conf"))
    if hub_candidate:
        text = Path(hub_candidate).read_text()
        ast.parse(text)
        if "OWRT_REMOTE_HTTP_DEBUG" not in text:
            raise RuntimeError("Hub candidate does not contain the HTTP log fix")
    configs = dict(CONFIGS)
    if not shutil.which("nginx"):
        configs.pop("/etc/nginx/conf.d/owrt-remote-log-policy.conf")
    else:
        configs.update(nginx_policies())
    paths = [*configs, str(INSTALL_PATH), str(LEGACY)] + ([str(HUB)] if hub_candidate else [])
    snapshot(paths, backup)
    try:
        command(["systemctl", "stop", "logrotate.timer"])
        for path, content in configs.items():
            atomic(path, content, 0o644)
        atomic(INSTALL_PATH, Path(__file__).read_bytes(), 0o755)
        if legacy_policy():
            LEGACY.unlink()  # Old duplicate configuration, not a log file.
        if hub_candidate:
            atomic(HUB, Path(hub_candidate).read_bytes(), 0o755)
        config, _ = retention_config(flatten_config("/etc/logrotate.conf"))
        RUN.mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic(RUN / "validate.conf", config)
        try:
            command(["logrotate", "--debug", str(RUN / "validate.conf")])
        finally:
            (RUN / "validate.conf").unlink(missing_ok=True)
        command(["systemctl", "daemon-reload"])
        command(["systemd-analyze", "verify", "logrotate.service", "logrotate.timer"])
        validate_journal_config()
        if shutil.which("nginx"):
            command(["nginx", "-t"])
            if command(["systemctl", "is-active", "nginx"], check=False).returncode == 0:
                command(["systemctl", "reload", "nginx"])
        command(["systemctl", "restart", "systemd-journald"])
        if hub_candidate:
            command(["systemctl", "restart", "owrt-remote"])
            check_health(hashlib.sha256(HUB.read_bytes()).hexdigest())
        # Start the persistent timer only after main releases our lock. A missed
        # calendar event can otherwise immediately run and be skipped as busy.
        command(["systemctl", "enable", "logrotate.timer"])
        print("Backup: " + str(backup))
        print("LOG_MAINTENANCE_INSTALL_OK")
    except Exception:
        restore(backup)
        raise


def check_health(revision):
    import urllib.request
    # No proxy environment / shell wildcard expansion. Retry startup quietly.
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for attempt in range(15):
        try:
            with client.open("http://127.0.0.1:8088/health?panel-update=1", timeout=3) as response:
                data = json.load(response)
                if data.get("ok") and data.get("revision") == revision:
                    return
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise RuntimeError("Hub health check failed after restart")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "rollback", "run", "status"))
    parser.add_argument("--hub-candidate")
    parser.add_argument("--backup", type=Path, default=BACKUP,
                        help="Configuration snapshot for this installation/rollback")
    parser.add_argument("--force", action="store_true", help="Explicit manual retry; daily timer never uses this")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Run as root")
    if args.action in ("install", "rollback"):
        import fcntl
        RUN.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (RUN / "lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Log maintenance/install already running; retry after completion")
            if args.action == "install":
                install(args.hub_candidate, backup=args.backup)
            else:
                restore(args.backup)
        if args.action == "install":
            try:
                command(["systemctl", "start", "logrotate.timer"])
            except Exception:
                restore(args.backup)
                raise
    elif args.action == "run":
        run(args.force)
    else:
        print(command(["systemctl", "list-timers", "logrotate.timer", "--no-pager"]).stdout)
        print(command(["journalctl", "--disk-usage"]).stdout)
        print((STATE / "result.json").read_text() if (STATE / "result.json").exists() else "No maintenance result yet")
        for path, percent, level in disk_levels():
            print(level, path, f"{percent:.1f}%")
        service_status()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("ERROR: " + str(error), file=sys.stderr)
        sys.exit(1)
