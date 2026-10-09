param(
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$Vps,
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$UploadName = 'owrt-vps-widgets-' + [guid]::NewGuid().ToString('N')
$StageRoot = Join-Path ([System.IO.Path]::GetTempPath()) $UploadName
$Utf8 = [System.Text.UTF8Encoding]::new($false)
$RemoteScript = @'
#!/bin/sh
set -eu
stage=/tmp/__UPLOAD_NAME__
target=/opt/owrt-remote/owrt-remote-hub.py
backup=/opt/owrt-remote/owrt-remote-hub.py.bak-before-vps-widgets-v112
candidate=$target.new-__UPLOAD_NAME__
changed=0
cleanup() {
    code=$?
    trap - EXIT
    if [ "$code" -ne 0 ] && [ "$changed" = 1 ]; then
        echo 'Operation failed; restoring the file from this attempt.' >&2
        journalctl -u owrt-remote -n 30 --no-pager >&2 || true
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
    grep -F -q 'v112' "$stage/hub.py"
    "$python_bin" -m py_compile "$stage/hub.py"
    OWRT_VPS_WIDGETS_HUB_SOURCE="$stage/hub.py" "$python_bin" "$stage/tests.py" -v
    if [ ! -e "$backup" ]; then cp -p "$target" "$backup"; fi
    test -s "$backup"
    source_file="$stage/hub.py"
else
    test -s "$backup"
    "$python_bin" -m py_compile "$backup"
    source_file="$backup"
fi
printf 'Backup: %s\n' "$backup"
cp -p "$target" "$stage/previous.py"
cp "$source_file" "$candidate"
chmod 755 "$candidate"
changed=1
mv -f "$candidate" "$target"
systemctl restart owrt-remote
healthy=0
for attempt in 1 2 3 4 5 6 7 8 9 10; do
    if systemctl is-active --quiet owrt-remote && curl -fsS --max-time 5 http://127.0.0.1:8088/health; then healthy=1; break; fi
    sleep 2
done
test "$healthy" = 1
cmp "$source_file" "$target"
if [ '__ACTION__' = Install ]; then
    # Without a Hub login this endpoint must refuse access, rather than leak metrics.
    code=$(curl -sS --max-time 5 -o "$stage/response.json" -w '%{http_code}' http://127.0.0.1:8088/api/vps/resources)
    test "$code" = 401
fi
printf '\nVPS_WIDGETS_V112_%s_OK\n' '__ACTION__'
'@
$RemoteScript = $RemoteScript.Replace('__UPLOAD_NAME__', $UploadName).Replace('__ACTION__', $Action)
try {
    [System.IO.Directory]::CreateDirectory($StageRoot) | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $StageRoot 'run.sh'), $RemoteScript.Replace("`r`n", "`n") + "`n", $Utf8)
    if ($Action -eq 'Install') {
        $Payloads = @{
            'hub.py' = 'vps\owrt-remote-hub.py'
            'tests.py' = 'tests\test_vps_resources.py'
        }
        foreach ($Item in $Payloads.GetEnumerator()) {
            $Content = [System.IO.File]::ReadAllText((Join-Path $RepoRoot $Item.Value)).Replace("`r`n", "`n")
            [System.IO.File]::WriteAllText((Join-Path $StageRoot $Item.Key), $Content, $Utf8)
        }
    }
    if ($PrepareOnly) { Write-Host "Prepared only: $StageRoot"; return }
    & scp -O -r -- $StageRoot "${Vps}:/tmp/"
    if ($LASTEXITCODE -ne 0) { throw 'Upload failed. Installation has not started.' }
    & ssh -T -o ConnectTimeout=10 $Vps "sh /tmp/$UploadName/run.sh"
    if ($LASTEXITCODE -ne 0) { throw "$Action failed. See the output above; success was not confirmed." }
    Write-Host "VPS widgets $Action finished successfully."
} finally {
    if (-not $PrepareOnly) {
        $Resolved = [System.IO.Path]::GetFullPath($StageRoot)
        $Expected = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetTempPath()) $UploadName))
        if ($Resolved -ne $Expected -or (Split-Path -Leaf $Resolved) -ne $UploadName) { throw 'Unsafe staging cleanup path.' }
        if (Test-Path -LiteralPath $Resolved) { Remove-Item -LiteralPath $Resolved -Recurse -Force }
    }
}
