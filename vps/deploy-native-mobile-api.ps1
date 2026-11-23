param(
    [ValidateNotNullOrEmpty()][string]$Vps = 'root@13.143.167.219',
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [ValidatePattern('^owrt-remote-backup-[A-Za-z0-9_-]{1,96}$')]
    [string]$BackupName = 'owrt-remote-backup-before-native-mobile-api-v123',
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$UploadName = 'owrt-native-mobile-api-' + [guid]::NewGuid().ToString('N')
$StageRoot = Join-Path ([System.IO.Path]::GetTempPath()) $UploadName
$Utf8 = [System.Text.UTF8Encoding]::new($false)

$RemoteScript = @'
#!/bin/sh
set -eu
stage=/tmp/__UPLOAD_NAME__
backup=/root/__BACKUP_NAME__
action=__ACTION__
changed=0
step=preflight
names='mobile_api.py hub.py'
python_bin=/opt/owrt-remote/venv/bin/python
[ -x "$python_bin" ] || python_bin=python3

target() {
    case "$1" in
        hub.py) echo /opt/owrt-remote/owrt-remote-hub.py ;;
        mobile_api.py) echo /opt/owrt-remote/mobile_api.py ;;
        *) return 1 ;;
    esac
}

snapshot() {
    folder="$1"
    mkdir -m 700 "$folder"
    for name in $names; do
        path="$(target "$name")"
        if [ -e "$path" ]; then
            cp -p "$path" "$folder/$name"
        else
            : >"$folder/$name.absent"
        fi
    done
    printf '%s\n' native-mobile-api-v123 >"$folder/complete"
}

validate() {
    folder="$1"
    test -s "$folder/hub.py"
    "$python_bin" -m py_compile "$folder/hub.py"
    if [ -s "$folder/mobile_api.py" ]; then
        "$python_bin" -m py_compile "$folder/mobile_api.py"
    else
        test -e "$folder/mobile_api.py.absent"
    fi
}

apply_files() {
    folder="$1"
    for name in $names; do
        path="$(target "$name")"
        if [ -e "$folder/$name.absent" ]; then
            rm -f "$path" || return
        else
            test -s "$folder/$name"
            cp "$folder/$name" "$path.new-__UPLOAD_NAME__" || return
            if [ "$name" = hub.py ]; then chmod 0755 "$path.new-__UPLOAD_NAME__"; else chmod 0644 "$path.new-__UPLOAD_NAME__"; fi || return
            mv -f "$path.new-__UPLOAD_NAME__" "$path" || return
            # Refresh this interpreter's bytecode even when two same-size
            # revisions happen to be written within the same second.
            "$python_bin" -m py_compile "$path" || return
        fi
    done
}

cleanup() {
    code=$?
    trap - EXIT
    set +e
    rollback_failed=0
    if [ "$code" -ne 0 ]; then
        echo "Native mobile API $action failed during $step (rc=$code)." >&2
        journalctl -u owrt-remote -n 45 --no-pager >&2 || true
        if [ "$changed" = 1 ]; then
            echo 'Restoring both files from before this attempt.' >&2
            if apply_files "$stage/previous" && systemctl restart owrt-remote; then
                echo 'Previous Hub files restored; service restart requested.' >&2
            else
                rollback_failed=1
                echo 'Automatic rollback needs attention. Retained files:' "$stage/previous" 'and' "$backup" >&2
            fi
        fi
    fi
    for name in $names; do path="$(target "$name")"; rm -f "$path.new-__UPLOAD_NAME__"; done
    if [ "$rollback_failed" = 0 ]; then rm -rf "$stage"; fi
    exit "$code"
}
trap cleanup EXIT
trap 'exit 1' INT TERM

test -s "$(target hub.py)"
if [ "$action" = Install ]; then
    validate "$stage"
    "$python_bin" - "$stage" <<'PY'
import ast
from pathlib import Path
import sys

folder = Path(sys.argv[1])
hub = (folder / 'hub.py').read_text(encoding='utf-8')
for marker in ('import mobile_api', 'mobile_api.dispatch_get(self, path)', 'mobile_api.dispatch_post(self, path)'):
    if marker not in hub:
        raise SystemExit('Hub candidate is missing integration: ' + marker)
api = ast.parse((folder / 'mobile_api.py').read_text(encoding='utf-8'))
names = {node.name for node in api.body if isinstance(node, ast.FunctionDef)}
if not {'dispatch_get', 'dispatch_post'} <= names:
    raise SystemExit('Native API candidate is incomplete')
print('Hub and native API candidates validated')
PY
    step=backup
    if [ ! -e "$backup" ]; then
        snapshot "$stage/retained"
        mv "$stage/retained" "$backup"
    fi
    source_dir="$stage"
else
    step=backup
    source_dir="$backup"
fi

test "$(cat "$backup/complete" 2>/dev/null)" = native-mobile-api-v123 || {
    echo "No complete backup: $backup. Nothing changed." >&2
    exit 1
}
validate "$source_dir"
expected_revision=$("$python_bin" - "$source_dir/hub.py" <<'PY'
import hashlib
from pathlib import Path
import sys
print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)

snapshot "$stage/previous"
step=replace
changed=1
apply_files "$source_dir"
step=restart
systemctl restart owrt-remote
step=health
healthy=0
for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    if systemctl is-active --quiet owrt-remote && "$python_bin" - "$expected_revision" "$action" >"$stage/health-check" 2>&1 <<'PY'
import json
import sys
import urllib.error
import urllib.request

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
base = 'http://127.0.0.1:8088'

def get(path):
    with opener.open(base + path, timeout=3) as response:
        return json.loads(response.read(16384))

try:
    health = get('/health?panel-update=1')
    if not health.get('ok') or health.get('revision') != sys.argv[1]:
        raise RuntimeError('The running Hub revision does not match the installed source')
    if sys.argv[2] == 'Install':
        capabilities = get('/api/mobile/v1/capabilities')
        if not capabilities.get('ok') or capabilities.get('api_version') != 1:
            raise RuntimeError('Native API capabilities were not confirmed')
        try:
            get('/api/mobile/v1/session')
        except urllib.error.HTTPError as error:
            data = json.loads(error.read(16384))
            if error.code != 401 or data.get('code') != 'not_authenticated':
                raise RuntimeError('Native session authentication guard was not confirmed')
        else:
            raise RuntimeError('Native session endpoint accepted an unauthenticated request')
    print('Running Hub revision verified: ' + sys.argv[1])
    print('Native API and authentication guard verified' if sys.argv[2] == 'Install' else 'Rollback health verified')
except Exception as error:
    print('Health verification failed: ' + str(error), file=sys.stderr)
    raise SystemExit(1)
PY
    then
        healthy=1
        break
    fi
    sleep 2
done
if [ "$healthy" != 1 ]; then
    cat "$stage/health-check" >&2 2>/dev/null || true
    exit 1
fi
for name in $names; do
    path="$(target "$name")"
    if [ -e "$source_dir/$name.absent" ]; then test ! -e "$path"; else cmp "$source_dir/$name" "$path"; fi
done
cat "$stage/health-check"
printf '\nBackup: %s\nNATIVE_MOBILE_API_%s_OK\n' "$backup" "$action"
'@

$RemoteScript = $RemoteScript.Replace('__UPLOAD_NAME__', $UploadName).Replace('__ACTION__', $Action).Replace('__BACKUP_NAME__', $BackupName)

try {
    [System.IO.Directory]::CreateDirectory($StageRoot) | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $StageRoot 'run.sh'), $RemoteScript.Replace("`r`n", "`n") + "`n", $Utf8)
    if ($Action -eq 'Install') {
        foreach ($Pair in @(@('vps\owrt-remote-hub.py', 'hub.py'), @('vps\mobile_api.py', 'mobile_api.py'))) {
            $Content = [System.IO.File]::ReadAllText((Join-Path $RepoRoot $Pair[0])).Replace("`r`n", "`n")
            [System.IO.File]::WriteAllText((Join-Path $StageRoot $Pair[1]), $Content, $Utf8)
        }
    }
    if ($PrepareOnly) {
        Write-Host "Prepared only: $StageRoot"
        return
    }
    & scp -O -r -- $StageRoot "${Vps}:/tmp/"
    if ($LASTEXITCODE -ne 0) { throw 'Upload failed. VPS was not changed.' }
    & ssh -T -o ConnectTimeout=10 $Vps "sh /tmp/$UploadName/run.sh"
    if ($LASTEXITCODE -ne 0) { throw "$Action failed. See the output above; success was not confirmed." }
    Write-Host "Native mobile API $Action finished successfully. Backup: /root/$BackupName"
}
finally {
    if (-not $PrepareOnly) {
        $Resolved = [System.IO.Path]::GetFullPath($StageRoot)
        $Expected = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetTempPath()) $UploadName))
        if ($Resolved -ne $Expected -or (Split-Path -Leaf $Resolved) -ne $UploadName) {
            throw 'Unsafe staging cleanup path.'
        }
        if (Test-Path -LiteralPath $Resolved) { Remove-Item -LiteralPath $Resolved -Recurse -Force }
    }
}
