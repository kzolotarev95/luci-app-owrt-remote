param(
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$Vps,
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install'
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$UploadName = 'owrt-request-load-' + [guid]::NewGuid().ToString('N')
$StageRoot = Join-Path ([System.IO.Path]::GetTempPath()) $UploadName
$Utf8 = [System.Text.UTF8Encoding]::new($false)

$RemoteScript = @'
#!/bin/sh
set -eu

stage=/tmp/__UPLOAD_NAME__
target=/opt/owrt-remote/owrt-remote-hub.py
backup=/root/owrt-remote-backup-before-request-load-v119
policy=/etc/nginx/conf.d/owrt-remote-log-policy.conf
journal=/etc/systemd/journald.conf.d/owrt-remote-limits.conf
logrotate=/etc/logrotate.d/owrt-remote
candidate="$target.new-__UPLOAD_NAME__"
changed=0

show_failure() {
    echo '--- owrt-remote status ---' >&2
    systemctl --no-pager -l status owrt-remote >&2 || true
    echo '--- owrt-remote journal ---' >&2
    journalctl -u owrt-remote -n 80 --no-pager -l >&2 || true
}

backup_optional() {
    file="$1"
    name="$2"
    if [ -e "$file" ]; then
        cp -p "$file" "$backup/$name"
    else
        : > "$backup/$name.absent"
    fi
}

restore_optional() {
    file="$1"
    name="$2"
    if [ -e "$backup/$name" ]; then
        cp -p "$backup/$name" "$file"
    elif [ -e "$backup/$name.absent" ]; then
        rm -f "$file"
    fi
}

cleanup() {
    code=$?
    trap - EXIT
    if [ "$code" -ne 0 ]; then
        echo "Request-load fix failed with rc=$code" >&2
        if [ "$changed" = 1 ] && [ -s "$backup/owrt-remote-hub.py" ]; then
            cp -p "$backup/owrt-remote-hub.py" "$candidate"
            mv -f "$candidate" "$target"
            restore_optional "$policy" nginx-policy
            restore_optional "$journal" journald-limits
            restore_optional "$logrotate" logrotate
            nginx -t >/dev/null 2>&1 && systemctl reload nginx >/dev/null 2>&1 || true
            systemctl daemon-reload >/dev/null 2>&1 || true
            systemctl restart owrt-remote >/dev/null 2>&1 || true
        fi
        show_failure
    fi
    rm -f "$candidate"
    rm -rf "$stage"
    exit "$code"
}
trap cleanup EXIT INT TERM

test -s "$target"
python_bin=/opt/owrt-remote/venv/bin/python
[ -x "$python_bin" ] || python_bin=python3

if [ '__ACTION__' = Install ]; then
    test -s "$stage/hub.py"
    "$python_bin" -m py_compile "$stage/hub.py"
    "$python_bin" - "$stage/hub.py" <<'PY'
from pathlib import Path
import sys
source = Path(sys.argv[1]).read_text(encoding='utf-8')
required = (
    'def notification_wait_context(',
    'def claim_notification_wait(',
    'if after_serial > latest_serial:',
    'notification_wait_metrics()',
    'client_id: notificationClient',
    'notificationWaitController',
)
missing = [marker for marker in required if marker not in source]
if missing:
    for marker in missing:
        print('Missing marker:', repr(marker), file=sys.stderr)
    raise SystemExit(1)
print('Candidate validation OK')
PY
    mkdir -p "$backup"
    if [ ! -e "$backup/owrt-remote-hub.py" ]; then
        cp -p "$target" "$backup/owrt-remote-hub.py"
        backup_optional "$policy" nginx-policy
        backup_optional "$journal" journald-limits
        backup_optional "$logrotate" logrotate
    fi
    changed=1
    mkdir -p "$(dirname "$policy")" "$(dirname "$journal")" "$(dirname "$logrotate")"
    cat > "$policy" <<'EOF'
# OpenWrt Remote Hub: keep successful long-poll traffic out of access.log.
# 4xx/5xx responses remain logged for diagnostics.
map "$request_uri:$status" $owrt_remote_access_log {
    default 1;
    ~^/api/notifications/wait[^:]*:2[0-9][0-9]$ 0;
    ~^/api/ssh-session/[^/]+/read[^:]*:2[0-9][0-9]$ 0;
}
access_log /var/log/nginx/access.log combined if=$owrt_remote_access_log;
EOF
    cat > "$journal" <<'EOF'
[Journal]
SystemMaxUse=256M
SystemKeepFree=512M
MaxRetentionSec=7day
RateLimitIntervalSec=30s
RateLimitBurst=2000
Compress=yes
EOF
    cat > "$logrotate" <<'EOF'
/var/log/nginx/*.log {
    daily
    rotate 7
    size 50M
    missingok
    notifempty
    compress
    delaycompress
    copytruncate
}
EOF
    cp -p "$stage/hub.py" "$candidate"
    chmod 0755 "$candidate"
    mv -f "$candidate" "$target"
    if command -v nginx >/dev/null 2>&1; then
        nginx -t
        systemctl reload nginx >/dev/null 2>&1 || systemctl restart nginx
    fi
    systemctl daemon-reload >/dev/null 2>&1 || true
    systemctl reload-or-restart systemd-journald >/dev/null 2>&1 || true
    journalctl --vacuum-size=256M --vacuum-time=7d >/dev/null 2>&1 || true
else
    test -s "$backup/owrt-remote-hub.py"
    "$python_bin" -m py_compile "$backup/owrt-remote-hub.py"
    cp -p "$backup/owrt-remote-hub.py" "$candidate"
    mv -f "$candidate" "$target"
    restore_optional "$policy" nginx-policy
    restore_optional "$journal" journald-limits
    restore_optional "$logrotate" logrotate
    if command -v nginx >/dev/null 2>&1; then
        nginx -t
        systemctl reload nginx >/dev/null 2>&1 || systemctl restart nginx
    fi
    systemctl daemon-reload >/dev/null 2>&1 || true
    systemctl reload-or-restart systemd-journald >/dev/null 2>&1 || true
    journalctl --vacuum-size=256M --vacuum-time=7d >/dev/null 2>&1 || true
fi

systemctl restart owrt-remote
healthy=0
for attempt in 1 2 3 4 5 6 7 8; do
    if systemctl is-active --quiet owrt-remote && "$python_bin" - <<'PY'
import json
import urllib.request
with urllib.request.urlopen('http://127.0.0.1:8088/health?request-load-fix=1', timeout=3) as response:
    data = json.loads(response.read(4096))
assert data.get('ok') and 'notification_wait' in data
print('Running Hub health verified: notification_wait=' + json.dumps(data['notification_wait']))
PY
    then
        healthy=1
        break
    fi
    sleep 2
done
[ "$healthy" = 1 ]

printf '\nBackup: %s\nREQUEST_LOAD_FIX_V119_%s_OK\n' "$backup" '__ACTION__'
'@

$RemoteScript = $RemoteScript.Replace('__UPLOAD_NAME__', $UploadName).Replace('__ACTION__', $Action)

try {
    [System.IO.Directory]::CreateDirectory($StageRoot) | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $StageRoot 'run.sh'), $RemoteScript.Replace("`r`n", "`n") + "`n", $Utf8)
    if ($Action -eq 'Install') {
        $Content = [System.IO.File]::ReadAllText((Join-Path $RepoRoot 'vps\owrt-remote-hub.py')).Replace("`r`n", "`n")
        [System.IO.File]::WriteAllText((Join-Path $StageRoot 'hub.py'), $Content, $Utf8)
    }
    & scp -O -r -- $StageRoot "${Vps}:/tmp/"
    if ($LASTEXITCODE -ne 0) { throw 'Upload failed. VPS was not changed.' }
    & ssh -T -o ConnectTimeout=10 $Vps "sh /tmp/$UploadName/run.sh"
    if ($LASTEXITCODE -ne 0) { throw "$Action failed. See the output above; success was not confirmed." }
    Write-Host "Request-load fix $Action finished successfully."
}
finally {
    $Resolved = [System.IO.Path]::GetFullPath($StageRoot)
    $Expected = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetTempPath()) $UploadName))
    if ($Resolved -ne $Expected -or (Split-Path -Leaf $Resolved) -ne $UploadName) { throw 'Unsafe staging cleanup path.' }
    if (Test-Path -LiteralPath $Resolved) { Remove-Item -LiteralPath $Resolved -Recurse -Force }
}
