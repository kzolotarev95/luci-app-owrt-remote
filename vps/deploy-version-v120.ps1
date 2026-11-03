param(
    [Parameter(Mandatory = $true, ParameterSetName = 'Vps')][ValidateNotNullOrEmpty()][string]$Vps,
    [Parameter(Mandatory = $true, ParameterSetName = 'Router')][ValidateNotNullOrEmpty()][string]$Router,
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$UploadName = 'owrt-version-v120-' + [guid]::NewGuid().ToString('N')
$StageRoot = Join-Path ([System.IO.Path]::GetTempPath()) $UploadName
$Utf8 = [System.Text.UTF8Encoding]::new($false)

$RemoteScript = @'
#!/bin/sh
set -eu
stage=/tmp/__UPLOAD_NAME__
target=/opt/owrt-remote/owrt-remote-hub.py
backup=/opt/owrt-remote/owrt-remote-hub.py.bak-before-v120
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
required = ('v120', 'def hub_update_require_version(', 'def hub_update_older_version(',
            "else if (state.checked && !state.checking) message('');")
if 'v' + str(120 - 1) in source:
    print('Old UI version remains in candidate', file=sys.stderr)
    raise SystemExit(1)
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
printf '\nBackup: %s\nHUB_V120_%s_OK\n' "$backup" '__ACTION__'
'@

$RouterScript = @'
#!/bin/sh
set -eu
stage=/tmp/__UPLOAD_NAME__
backup=/root/owrt-remote-backup-before-v120
cgi=/www/cgi-bin/owrt-remote
luci=/www/luci-static/resources/view/owrt_remote.js
changed=0
cleanup() {
    code=$?
    trap - EXIT
    if [ "$code" -ne 0 ]; then
        echo "Router version __ACTION__ failed (rc=$code)." >&2
        if [ "$changed" = 1 ]; then
            echo 'Restoring the previous router UI files...' >&2
            cp -p "$stage/previous-cgi" "$cgi.new-__UPLOAD_NAME__" && mv -f "$cgi.new-__UPLOAD_NAME__" "$cgi" || true
            cp -p "$stage/previous-luci" "$luci.new-__UPLOAD_NAME__" && mv -f "$luci.new-__UPLOAD_NAME__" "$luci" || true
        fi
    fi
    rm -f "$cgi.new-__UPLOAD_NAME__" "$luci.new-__UPLOAD_NAME__"
    rm -rf "$stage"
    exit "$code"
}
trap cleanup EXIT
test -s "$cgi"
test -s "$luci"
if [ '__ACTION__' = Install ]; then
    sh -n "$stage/cgi"
    grep -F -q 'v120' "$stage/cgi"
    grep -F -q 'v120' "$stage/luci"
    if grep -F -q "v$((120 - 1))" "$stage/cgi" "$stage/luci"; then
        echo 'Old UI version remains in candidate.' >&2
        exit 1
    fi
    if [ ! -e "$backup" ]; then
        mkdir -m 700 "$stage/retained"
        cp -p "$cgi" "$stage/retained/cgi"
        cp -p "$luci" "$stage/retained/luci"
        : >"$stage/retained/complete"
        mv "$stage/retained" "$backup"
    fi
    source_dir=$stage
else
    source_dir=$backup
fi
if [ ! -f "$backup/complete" ] || [ ! -s "$backup/cgi" ] || [ ! -s "$backup/luci" ]; then
    echo "No complete backup: $backup. Nothing changed." >&2
    exit 1
fi
sh -n "$source_dir/cgi"
cp -p "$cgi" "$stage/previous-cgi"
cp -p "$luci" "$stage/previous-luci"
cp "$source_dir/cgi" "$cgi.new-__UPLOAD_NAME__"
chmod 0755 "$cgi.new-__UPLOAD_NAME__"
cp "$source_dir/luci" "$luci.new-__UPLOAD_NAME__"
chmod 0644 "$luci.new-__UPLOAD_NAME__"
changed=1
mv -f "$cgi.new-__UPLOAD_NAME__" "$cgi"
mv -f "$luci.new-__UPLOAD_NAME__" "$luci"
cmp "$source_dir/cgi" "$cgi"
cmp "$source_dir/luci" "$luci"
[ ! -x /etc/init.d/rpcd ] || /etc/init.d/rpcd restart || true
[ ! -x /etc/init.d/uhttpd ] || /etc/init.d/uhttpd reload || true
printf '\nBackup: %s\nROUTER_V120_%s_OK\n' "$backup" '__ACTION__'
'@

if ($PSCmdlet.ParameterSetName -eq 'Router') {
    $RemoteScript = $RouterScript
    $TargetMachine = $Router
}
else {
    $TargetMachine = $Vps
}
$RemoteScript = $RemoteScript.Replace('__UPLOAD_NAME__', $UploadName).Replace('__ACTION__', $Action)

try {
    [System.IO.Directory]::CreateDirectory($StageRoot) | Out-Null
    [System.IO.File]::WriteAllText(
        (Join-Path $StageRoot 'run.sh'),
        $RemoteScript.Replace("`r`n", "`n") + "`n",
        $Utf8
    )

    if ($Action -eq 'Install') {
        $Files = if ($PSCmdlet.ParameterSetName -eq 'Router') {
            @{ 'cgi' = 'files\www\cgi-bin\owrt-remote'; 'luci' = 'files\www\luci-static\resources\view\owrt_remote.js' }
        } else {
            @{ 'hub.py' = 'vps\owrt-remote-hub.py' }
        }
        foreach ($Name in $Files.Keys) {
            $Content = [System.IO.File]::ReadAllText((Join-Path $RepoRoot $Files[$Name])).Replace("`r`n", "`n")
            [System.IO.File]::WriteAllText((Join-Path $StageRoot $Name), $Content, $Utf8)
        }
    }

    if ($PrepareOnly) {
        Write-Host "Prepared only: $StageRoot"
        return
    }

    & scp -O -r -- $StageRoot "${TargetMachine}:/tmp/"
    if ($LASTEXITCODE -ne 0) { throw 'Upload failed. Target was not changed.' }
    & ssh -T -o ConnectTimeout=10 $TargetMachine "sh /tmp/$UploadName/run.sh"
    if ($LASTEXITCODE -ne 0) { throw "$Action failed. See the output above; success was not confirmed." }
    Write-Host "Version v120 $Action finished successfully. Refresh the panel."
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
