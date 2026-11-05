param(
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$Vps,
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$UploadName = 'owrt-xray-reverse-vps-' + [guid]::NewGuid().ToString('N')
$StageRoot = Join-Path ([System.IO.Path]::GetTempPath()) $UploadName
$Utf8 = [System.Text.UTF8Encoding]::new($false)

$RemoteScript = @'
#!/bin/sh
set -eu
stage=/tmp/__UPLOAD_NAME__
target=/opt/owrt-remote/owrt-remote-hub.py
backup=/opt/owrt-remote/owrt-remote-hub.py.bak-before-xray-reverse-v121
candidate=$target.new-__UPLOAD_NAME__
changed=0

show_failure() {
    echo '--- owrt-remote status ---' >&2
    systemctl --no-pager -l status owrt-remote >&2 || true
    echo '--- owrt-remote journal ---' >&2
    journalctl -u owrt-remote -n 80 --no-pager -l >&2 || true
}

cleanup() {
    code=$?
    trap - EXIT
    if [ "$code" -ne 0 ]; then
        echo "Remote step failed with rc=$code" >&2
        show_failure
    fi
    if [ "$code" -ne 0 ] && [ "$changed" = 1 ] && [ -s "$stage/previous.py" ]; then
        echo 'Restoring the previous Hub file...' >&2
        cp -p "$stage/previous.py" "$candidate"
        mv -f "$candidate" "$target"
        systemctl restart owrt-remote || true
    fi
    rm -f "$candidate"
    rm -rf "$stage"
    exit "$code"
}
trap cleanup EXIT

test -s "$target"
python_bin=/opt/owrt-remote/venv/bin/python
[ -x "$python_bin" ] || python_bin=python3

if [ '__ACTION__' = Install ]; then
    test -s "$stage/hub.py"
    echo 'Validating candidate...'
    "$python_bin" -m py_compile "$stage/hub.py"
    "$python_bin" - "$stage/hub.py" <<'PY'
from pathlib import Path
import sys
source = Path(sys.argv[1]).read_text(encoding='utf-8')
required = (
    '"finalRules": [{"action": "allow", "network": "tcp", "port": int(row["admin_port"])}]',
    '"finalRules": [{"action": "allow", "network": "tcp", "port": int(row["ssh_port"] or 22)}]',
)
missing = [marker for marker in required if marker not in source]
if missing:
    for marker in missing:
        print('Missing marker:', repr(marker), file=sys.stderr)
    raise SystemExit(1)
print('Candidate validation OK')
PY
    if [ ! -e "$backup" ]; then
        cp -p "$target" "$backup"
    fi
    source_file="$stage/hub.py"
else
    test -s "$backup"
    "$python_bin" -m py_compile "$backup"
    source_file="$backup"
fi

expected_revision=$("$python_bin" - "$source_file" <<'PY'
import hashlib
from pathlib import Path
import sys
print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)

cp -p "$target" "$stage/previous.py"
cp "$source_file" "$candidate"
chmod 0755 "$candidate"
changed=1
mv -f "$candidate" "$target"
echo 'Restarting owrt-remote...'
if ! systemctl restart owrt-remote; then
    show_failure
    exit 1
fi

healthy=0
for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    if systemctl is-active --quiet owrt-remote && "$python_bin" - "$expected_revision" > "$stage/health-check" 2>&1 <<'PY'
import json
import sys
import urllib.request

try:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:8088/health?panel-update=1', timeout=3) as response:
        data = json.loads(response.read(4096))
    revision = data.get('revision', '')
    if not data.get('ok') or revision != sys.argv[1]:
        raise RuntimeError('Running Hub revision does not match the uploaded file: '
                           + (revision or '(not reported)') + '; expected ' + sys.argv[1])
    print('Running Hub revision verified: ' + revision)
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
if [ "$healthy" -ne 1 ]; then
    echo 'The uploaded Hub build did not become ready; installation was not confirmed.' >&2
    cat "$stage/health-check" >&2 2>/dev/null || true
    show_failure
    exit 1
fi
cat "$stage/health-check"
cmp "$source_file" "$target"
printf '\nBackup: %s\nXRAY_REVERSE_VPS_%s_OK\n' "$backup" '__ACTION__'
if [ '__ACTION__' = Install ]; then
    echo 'Hub client generator updated. The router fix must also be installed.'
    echo 'The VPS Xray core and running server configuration are unchanged.'
fi
'@

$RemoteScript = $RemoteScript.Replace('__UPLOAD_NAME__', $UploadName).Replace('__ACTION__', $Action)

try {
    [System.IO.Directory]::CreateDirectory($StageRoot) | Out-Null
    [System.IO.File]::WriteAllText(
        (Join-Path $StageRoot 'run.sh'),
        $RemoteScript.Replace("`r`n", "`n") + "`n",
        $Utf8
    )

    if ($Action -eq 'Install') {
        $Content = [System.IO.File]::ReadAllText((Join-Path $RepoRoot 'vps\owrt-remote-hub.py')).Replace("`r`n", "`n")
        [System.IO.File]::WriteAllText((Join-Path $StageRoot 'hub.py'), $Content, $Utf8)
    }

    if ($PrepareOnly) {
        Write-Host "Prepared only: $StageRoot"
        return
    }

    & scp -O -r -- $StageRoot "${Vps}:/tmp/"
    if ($LASTEXITCODE -ne 0) { throw 'Upload failed. VPS was not changed.' }
    & ssh -T -o ConnectTimeout=10 $Vps "sh /tmp/$UploadName/run.sh"
    if ($LASTEXITCODE -ne 0) { throw "$Action failed. See the output above; success was not confirmed." }
    Write-Host "Xray reverse Hub fix $Action finished successfully."
}
finally {
    if (-not $PrepareOnly) {
        $Resolved = [System.IO.Path]::GetFullPath($StageRoot)
        $Expected = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetTempPath()) $UploadName))
        if ($Resolved -ne $Expected -or (Split-Path -Leaf $Resolved) -ne $UploadName) {
            throw 'Unsafe staging cleanup path.'
        }
        if (Test-Path -LiteralPath $Resolved) {
            Remove-Item -LiteralPath $Resolved -Recurse -Force
        }
    }
}
