param(
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$Vps,
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$RemoteTarget = $Vps
$UploadName = 'owrt-recaptcha-v3-' + [guid]::NewGuid().ToString('N')
$Utf8 = [System.Text.UTF8Encoding]::new($false)
$UploadFiles = @()

if ($Action -eq 'Install') {
    $RemoteScript = @'
#!/bin/bash
set -Eeuo pipefail
stage=/tmp/__UPLOAD_NAME__
target=/opt/owrt-remote/owrt-remote-hub.py
backup=/opt/owrt-remote/owrt-remote-hub.py.bak-before-recaptcha-v3-v112
captcha_backup=/opt/owrt-remote/captcha-before-recaptcha-v3-v112.json
candidate=$target.new-__UPLOAD_NAME__
changed=0
cleanup() {
    code=$?
    trap - EXIT
    if [ "$code" -ne 0 ] && [ "$changed" = 1 ]; then
        echo 'Installation failed; restoring the file from this attempt.' >&2
        systemctl --no-pager -l status owrt-remote >&2 || true
        journalctl -u owrt-remote -n 60 --no-pager >&2 || true
        cp -a "$stage/previous.py" "$candidate"
        mv -f "$candidate" "$target"
        systemctl restart owrt-remote || true
    fi
    rm -f "$candidate"
    rm -rf -- "$stage"
    exit "$code"
}
trap cleanup EXIT
test -s "$target"
test -s "$stage/hub.py"
python_bin=/opt/owrt-remote/venv/bin/python
if [ ! -x "$python_bin" ]; then python_bin=python3; fi
"$python_bin" - "$stage/hub.py" <<'PY'
import ast, pathlib, sys
source = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')
tree = ast.parse(source)
assert 'v112' in source
assert 'v108' not in source
assert 'CAPTCHA_MODE_RECAPTCHA_V3' in source
assert 'prepareLoginRecaptcha' in source
PY
# The Linux regression suite uses its own temporary DB and loopback ports.
# It also exercises a real PTY and verifies child reaping.
OWRT_RESOURCE_HUB_SOURCE="$stage/hub.py" "$python_bin" "$stage/resources.py" -v
OWRT_RECAPTCHA_HUB_SOURCE="$stage/hub.py" "$python_bin" "$stage/recaptcha.py" -v

if [ ! -e "$backup" ]; then cp -a "$target" "$backup"; fi
test -s "$backup"
if [ ! -e "$captcha_backup" ]; then
    "$python_bin" - "$captcha_backup" <<'PY'
import json, os, pathlib, sys
auth = pathlib.Path('/var/lib/owrt-remote/hub-auth.json')
state = json.loads(auth.read_text(encoding='utf-8')) if auth.exists() else {}
with open(os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8') as output:
    json.dump(state.get('captcha', {'mode': 'digits', 'site_key': '', 'secret_key': ''}), output)
PY
fi
test -s "$captcha_backup"
printf 'Backup: %s\n' "$backup"
cp -a "$target" "$stage/previous.py"
install -m 0755 "$stage/hub.py" "$candidate"
changed=1
mv -f "$candidate" "$target"
systemctl restart owrt-remote
healthy=0
for attempt in {1..10}; do
    if systemctl is-active --quiet owrt-remote && curl -fsS --max-time 5 http://127.0.0.1:8088/health; then
        healthy=1
        break
    fi
    sleep 2
done
test "$healthy" = 1
cmp -s "$target" "$stage/hub.py"
printf '\n'
pid=$(systemctl show owrt-remote -p MainPID --value)
test "$pid" -gt 0
printf 'PID=%s FD=%s\n' "$pid" "$(find /proc/$pid/fd -maxdepth 1 -type l | wc -l)"
systemctl --no-pager --full status owrt-remote | sed -n '1,16p'
echo RECAPTCHA_V3_V112_OK
'@
} elseif ($Action -eq 'Rollback') {
    $RemoteScript = @'
#!/bin/bash
set -Eeuo pipefail
stage=/tmp/__UPLOAD_NAME__
target=/opt/owrt-remote/owrt-remote-hub.py
backup=/opt/owrt-remote/owrt-remote-hub.py.bak-before-recaptcha-v3-v112
captcha_backup=/opt/owrt-remote/captcha-before-recaptcha-v3-v112.json
candidate=$target.rollback-__UPLOAD_NAME__
changed=0
restore_captcha() {
    python3 - "$1" <<'PY'
import json, os, pathlib, sys, tempfile
auth = pathlib.Path('/var/lib/owrt-remote/hub-auth.json')
state = json.loads(auth.read_text(encoding='utf-8'))
state['captcha'] = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
fd, name = tempfile.mkstemp(prefix='.hub-auth-rollback-', dir=auth.parent)
try:
    with os.fdopen(fd, 'w', encoding='utf-8') as output:
        json.dump(state, output, ensure_ascii=False, indent=2)
        output.write('\n')
    os.replace(name, auth)
finally:
    pathlib.Path(name).unlink(missing_ok=True)
PY
}
cleanup() {
    code=$?
    trap - EXIT
    if [ "$code" -ne 0 ] && [ "$changed" = 1 ]; then
        echo 'Rollback failed; restoring the file from this attempt.' >&2
        cp -a "$stage/current.py" "$candidate"
        mv -f "$candidate" "$target"
        restore_captcha "$stage/current-captcha.json"
        systemctl restart owrt-remote || true
    fi
    rm -f "$candidate"
    rm -rf -- "$stage"
    exit "$code"
}
trap cleanup EXIT
test -s "$target"
test -s "$backup"
test -s "$captcha_backup"
python3 - "$backup" <<'PY'
import ast, pathlib, sys
ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
PY
cp -a "$target" "$stage/current.py"
python3 - "$stage/current-captcha.json" <<'PY'
import json, os, pathlib, sys
auth = pathlib.Path('/var/lib/owrt-remote/hub-auth.json')
state = json.loads(auth.read_text(encoding='utf-8'))
with open(os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8') as output:
    json.dump(state.get('captcha', {}), output)
PY
cp -a "$backup" "$candidate"
changed=1
mv -f "$candidate" "$target"
systemctl stop owrt-remote
restore_captcha "$captcha_backup"
systemctl restart owrt-remote
healthy=0
for attempt in {1..10}; do
    if systemctl is-active --quiet owrt-remote && curl -fsS --max-time 5 http://127.0.0.1:8088/health; then
        healthy=1
        break
    fi
    sleep 2
done
test "$healthy" = 1
printf '\n'
echo RECAPTCHA_V3_ROLLBACK_OK
'@
}

$RemoteScript = $RemoteScript.Replace('__UPLOAD_NAME__', $UploadName)
try {
    $StageRoot = Join-Path ([System.IO.Path]::GetTempPath()) $UploadName
    [System.IO.Directory]::CreateDirectory($StageRoot) | Out-Null
    $UploadFiles += $StageRoot
    [System.IO.File]::WriteAllText((Join-Path $StageRoot 'run.sh'), $RemoteScript.Replace("`r`n", "`n") + "`n", $Utf8)
    if ($Action -eq 'Install') {
        $Payloads = @{
            'hub.py' = Join-Path $RepoRoot 'vps\owrt-remote-hub.py'
            'resources.py' = Join-Path $RepoRoot 'tests\test_resource_lifecycle.py'
            'recaptcha.py' = Join-Path $RepoRoot 'tests\test_recaptcha.py'
        }
        foreach ($Item in $Payloads.GetEnumerator()) {
            $FileText = [System.IO.File]::ReadAllText($Item.Value)
            [System.IO.File]::WriteAllText((Join-Path $StageRoot $Item.Key), $FileText.Replace("`r`n", "`n"), $Utf8)
        }
    }
    if ($PrepareOnly) {
        Write-Host "Prepared only: $StageRoot"
        $UploadFiles = @()
        return
    }
    & scp -r -- $StageRoot "${RemoteTarget}:/tmp/"
    if ($LASTEXITCODE -ne 0) { throw 'Upload failed; installation has not started.' }
    & ssh $RemoteTarget "bash /tmp/$UploadName/run.sh"
    if ($LASTEXITCODE -ne 0) { throw "$Action failed. See the output above; success was not confirmed." }
    Write-Host "$Action finished successfully."
} finally {
    foreach ($UploadFile in $UploadFiles) {
        $Resolved = [System.IO.Path]::GetFullPath($UploadFile)
        $Expected = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetTempPath()) $UploadName))
        if ($Resolved -ne $Expected -or (Split-Path -Leaf $Resolved) -ne $UploadName) {
            throw 'Unsafe staging cleanup path.'
        }
        if (Test-Path -LiteralPath $Resolved) { Remove-Item -LiteralPath $Resolved -Recurse -Force }
    }
}
