param(
    [ValidateSet('Install', 'Rollback', 'Monitor')][string]$Action = 'Install',
    [ValidateRange(1, 1440)][int]$Minutes = 10,
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$Vps,
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$RemoteTarget = $Vps
$UploadName = 'owrt-resource-' + [guid]::NewGuid().ToString('N')
$Utf8 = [System.Text.UTF8Encoding]::new($false)
$UploadFiles = @()

if ($Action -eq 'Install') {
    $RemoteScript = @'
#!/bin/bash
set -Eeuo pipefail
stage=/tmp/__UPLOAD_NAME__
target=/opt/owrt-remote/owrt-remote-hub.py
backup=/opt/owrt-remote/owrt-remote-hub.py.bak-before-resource-fix-v112
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
factory = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'connect')
assert any(isinstance(item, ast.Name) and item.id == 'contextmanager' for item in factory.decorator_list)
assert any(isinstance(item, ast.Try) and item.finalbody for item in ast.walk(factory))
PY
# The Linux regression suite uses its own temporary DB and loopback ports.
# It also exercises a real PTY and verifies child reaping.
OWRT_RESOURCE_HUB_SOURCE="$stage/hub.py" "$python_bin" "$stage/tests.py" -v

if [ ! -e "$backup" ]; then cp -a "$target" "$backup"; fi
test -s "$backup"
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
echo RESOURCE_FIX_V112_OK
'@
} elseif ($Action -eq 'Rollback') {
    $RemoteScript = @'
#!/bin/bash
set -Eeuo pipefail
stage=/tmp/__UPLOAD_NAME__
target=/opt/owrt-remote/owrt-remote-hub.py
backup=/opt/owrt-remote/owrt-remote-hub.py.bak-before-resource-fix-v112
candidate=$target.rollback-__UPLOAD_NAME__
changed=0
cleanup() {
    code=$?
    trap - EXIT
    if [ "$code" -ne 0 ] && [ "$changed" = 1 ]; then
        echo 'Rollback failed; restoring the file from this attempt.' >&2
        cp -a "$stage/current.py" "$candidate"
        mv -f "$candidate" "$target"
        systemctl restart owrt-remote || true
    fi
    rm -f "$candidate"
    rm -rf -- "$stage"
    exit "$code"
}
trap cleanup EXIT
test -s "$target"
test -s "$backup"
python3 - "$backup" <<'PY'
import ast, pathlib, sys
ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
PY
cp -a "$target" "$stage/current.py"
cp -a "$backup" "$candidate"
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
printf '\n'
echo RESOURCE_FIX_ROLLBACK_OK
'@
} else {
    $RemoteScript = @'
#!/bin/bash
set -Eeuo pipefail
stage=/tmp/__UPLOAD_NAME__
trap 'rm -rf -- "$stage"' EXIT
python3 "$stage/monitor.py" __MINUTES__
'@
}

$RemoteScript = $RemoteScript.Replace('__UPLOAD_NAME__', $UploadName).Replace('__MINUTES__', [string]$Minutes)
try {
    $StageRoot = Join-Path ([System.IO.Path]::GetTempPath()) $UploadName
    [System.IO.Directory]::CreateDirectory($StageRoot) | Out-Null
    $UploadFiles += $StageRoot
    [System.IO.File]::WriteAllText((Join-Path $StageRoot 'run.sh'), $RemoteScript.Replace("`r`n", "`n") + "`n", $Utf8)
    if ($Action -eq 'Install') {
        $Payloads = @{
            'hub.py' = Join-Path $RepoRoot 'vps\owrt-remote-hub.py'
            'tests.py' = Join-Path $RepoRoot 'tests\test_resource_lifecycle.py'
        }
        foreach ($Item in $Payloads.GetEnumerator()) {
            $FileText = [System.IO.File]::ReadAllText($Item.Value)
            [System.IO.File]::WriteAllText((Join-Path $StageRoot $Item.Key), $FileText.Replace("`r`n", "`n"), $Utf8)
        }
    } elseif ($Action -eq 'Monitor') {
        $MonitorText = [System.IO.File]::ReadAllText((Join-Path $RepoRoot 'tests\monitor_vps_resources.py'))
        [System.IO.File]::WriteAllText((Join-Path $StageRoot 'monitor.py'), $MonitorText.Replace("`r`n", "`n"), $Utf8)
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
