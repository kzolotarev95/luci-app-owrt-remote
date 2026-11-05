param(
    [Parameter(Mandatory = $true, ParameterSetName = 'Vps')][string]$Vps,
    [Parameter(Mandatory = $true, ParameterSetName = 'Router')][string]$Router,
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [ValidateSet('xray-control', 'xray-control-ui')][string]$BackupName = 'xray-control',
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$UploadName = 'owrt-xray-control-' + [guid]::NewGuid().ToString('N')
$StageRoot = Join-Path ([System.IO.Path]::GetTempPath()) $UploadName
$Utf8 = [System.Text.UTF8Encoding]::new($false)
$TargetKind = $PSCmdlet.ParameterSetName
$Destination = if ($TargetKind -eq 'Vps') { $Vps } else { $Router }

$RemoteScript = @'
#!/bin/sh
set -eu
stage=/tmp/__UPLOAD_NAME__
kind=__KIND__
action=__ACTION__
backup=/root/owrt-remote-backup-before-__BACKUP_NAME__-v121
changed=0
step=preflight

if [ "$kind" = Vps ]; then
    names=hub.py
else
    names='agent control.sh init'
fi
target() {
    case "$1" in
        hub.py) echo /opt/owrt-remote/owrt-remote-hub.py ;;
        agent) echo /usr/sbin/owrt-remote ;;
        control.sh) echo /usr/lib/owrt-remote/control.sh ;;
        init) echo /etc/init.d/owrt-remote ;;
    esac
}
snapshot() {
    folder="$1"
    mkdir -p "$folder"
    for name in $names; do
        path="$(target "$name")"
        if [ -e "$path" ]; then cp -p "$path" "$folder/$name"; else : >"$folder/$name.absent"; fi
    done
    printf '%s\n' "$kind" >"$folder/complete"
}
apply_files() {
    folder="$1"
    for name in $names; do
        path="$(target "$name")"
        if [ -e "$folder/$name.absent" ]; then
            rm -f "$path"
        else
            test -s "$folder/$name"
            mkdir -p "$(dirname "$path")"
            cp "$folder/$name" "$path.new-__UPLOAD_NAME__"
            if [ "$name" = control.sh ]; then chmod 0644 "$path.new-__UPLOAD_NAME__"; else chmod 0755 "$path.new-__UPLOAD_NAME__"; fi
            mv -f "$path.new-__UPLOAD_NAME__" "$path"
        fi
    done
}
restart() {
    if [ "$kind" = Vps ]; then systemctl restart owrt-remote; else /etc/init.d/owrt-remote restart; fi
}
cleanup() {
    code=$?
    trap - EXIT
    set +e
    if [ "$code" -ne 0 ]; then echo "Failed during $step (rc=$code)." >&2; fi
    if [ "$code" -ne 0 ] && [ "$kind" = Router ]; then diagnose_router || true; fi
    if [ "$code" -ne 0 ] && [ "$changed" = 1 ]; then
        echo 'Deployment failed; restoring files from before this attempt.' >&2
        apply_files "$stage/previous" && restart || true
    fi
    if [ "$code" -ne 0 ] && [ "$kind" = Vps ]; then journalctl -u owrt-remote -n 35 --no-pager >&2 || true; fi
    for name in $names; do path="$(target "$name")"; rm -f "$path.new-__UPLOAD_NAME__"; done
    rm -rf "$stage"
    exit "$code"
}
diagnose_router() {
    [ "$kind" = Router ] || return 0
    echo 'Router deployment diagnostics:' >&2
    echo "stage=$stage action=$action" >&2
    for command_name in jsonfilter ubus timeout busybox; do
        if command -v "$command_name" >/dev/null 2>&1; then
            echo "command $command_name=$(command -v "$command_name")" >&2
        else
            echo "command $command_name=missing" >&2
        fi
    done
    echo 'owrt-remote service:' >&2
    ubus call service list '{"name":"owrt-remote"}' >&2 || true
    echo 'recent owrt-remote log:' >&2
    logread -e owrt-remote 2>/dev/null | tail -n 80 >&2 || true
}
fail_router() {
    echo "Router deployment error: $*" >&2
    exit 1
}
trap cleanup EXIT
trap 'exit 1' INT TERM

if [ "$action" = Install ]; then
    if [ "$kind" = Vps ]; then
        python_bin=/opt/owrt-remote/venv/bin/python
        [ -x "$python_bin" ] || python_bin=python3
        "$python_bin" -m py_compile "$stage/hub.py"
        "$python_bin" - "$stage/hub.py" <<'PY'
import pathlib, sys
source = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')
for marker in ('def xray_control_exchange(', 'def router_xray_control_html(', 'X-Xray-Control-CSRF', 'router_xray_commands'):
    if marker not in source: raise SystemExit('Candidate missing: ' + marker)
print('Hub candidate validated')
PY
    else
        sh -n "$stage/agent" || fail_router 'agent shell syntax check failed'
        sh -n "$stage/control.sh" || fail_router 'control.sh shell syntax check failed'
        sh -n "$stage/init" || fail_router 'init shell syntax check failed'
        command -v jsonfilter >/dev/null 2>&1 || fail_router 'jsonfilter is missing'
        command -v ubus >/dev/null 2>&1 || fail_router 'ubus is missing'
        # The agent has a bounded shell fallback when the optional BusyBox
        # timeout applet is absent, so this is informational rather than a
        # reason to reject an otherwise valid OpenWrt installation.
        if command -v timeout >/dev/null 2>&1; then
            echo 'Router preflight: timeout applet available' >&2
        elif command -v busybox >/dev/null 2>&1 && busybox timeout 45 true >/dev/null 2>&1; then
            echo 'Router preflight: BusyBox timeout applet available' >&2
        else
            echo 'Router preflight: timeout applet unavailable; agent shell timeout fallback will be used' >&2
        fi
        grep -Fq xray_control_dispatch "$stage/agent" || fail_router 'agent does not contain xray_control_dispatch'
        grep -Fq xray_control_worker "$stage/control.sh" || fail_router 'control.sh does not contain xray_control_worker'
    fi
    step=backup
    if [ ! -e "$backup/complete" ]; then
        test ! -e "$backup" || { echo "Incomplete backup already exists: $backup" >&2; exit 1; }
        snapshot "$backup"
    fi
    test "$(cat "$backup/complete")" = "$kind"
    source_dir="$stage"
else
    step=backup
    test "$(cat "$backup/complete" 2>/dev/null)" = "$kind" || { echo "No complete backup: $backup. Nothing changed." >&2; exit 1; }
    source_dir="$backup"
fi
for name in $names; do test -s "$source_dir/$name" || test -e "$source_dir/$name.absent"; done
snapshot "$stage/previous"
changed=1
step=apply-files
apply_files "$source_dir"
step=restart
restart

step=verify-health
if [ "$kind" = Vps ]; then
    python_bin=/opt/owrt-remote/venv/bin/python
    [ -x "$python_bin" ] || python_bin=python3
    "$python_bin" - <<'PY'
import hashlib, json, pathlib, time, urllib.request
expected = hashlib.sha256(pathlib.Path('/opt/owrt-remote/owrt-remote-hub.py').read_bytes()).hexdigest()
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for attempt in range(20):
    try:
        with opener.open('http://127.0.0.1:8088/health?panel-update=1', timeout=3) as response:
            data = json.load(response)
        if data.get('ok') and data.get('revision') == expected: break
    except Exception: pass
    time.sleep(1)
else: raise SystemExit('Running Hub revision/health was not confirmed')
print('Running Hub revision verified: ' + expected)
PY
else
    healthy=0
    for attempt in 1 2 3 4 5 6 7 8 9 10; do
        state="$(ubus call service list '{"name":"owrt-remote"}' 2>/dev/null || true)"
        if [ "$(printf '%s' "$state" | jsonfilter -e '@["owrt-remote"].instances.heartbeat.running')" = true ]; then healthy=1; break; fi
        sleep 1
    done
    [ "$healthy" = 1 ] || fail_router 'Heartbeat instance did not start; deployment is being rolled back.'
fi
step=verify-files
for name in $names; do
    path="$(target "$name")"
    if [ -e "$source_dir/$name.absent" ]; then test ! -e "$path"; else cmp "$source_dir/$name" "$path"; fi
done
printf '\nBackup: %s\nXRAY_CONTROL_%s_%s_OK\n' "$backup" "$kind" "$action"
'@

try {
    [System.IO.Directory]::CreateDirectory($StageRoot) | Out-Null
    $RemoteScript = $RemoteScript.Replace('__UPLOAD_NAME__', $UploadName).Replace('__KIND__', $TargetKind).Replace('__ACTION__', $Action).Replace('__BACKUP_NAME__', $BackupName)
    [System.IO.File]::WriteAllText((Join-Path $StageRoot 'run.sh'), $RemoteScript.Replace("`r`n", "`n") + "`n", $Utf8)
    if ($Action -eq 'Install') {
        $Sources = if ($TargetKind -eq 'Vps') {
            @{ 'hub.py' = 'vps\owrt-remote-hub.py' }
        } else {
            @{ 'agent' = 'files\usr\sbin\owrt-remote'; 'control.sh' = 'files\usr\lib\owrt-remote\control.sh'; 'init' = 'files\etc\init.d\owrt-remote' }
        }
        foreach ($Entry in $Sources.GetEnumerator()) {
            $Content = [System.IO.File]::ReadAllText((Join-Path $RepoRoot $Entry.Value)).Replace("`r`n", "`n")
            [System.IO.File]::WriteAllText((Join-Path $StageRoot $Entry.Key), $Content, $Utf8)
        }
    }
    if ($PrepareOnly) { Write-Host "Prepared only: $StageRoot"; return }
    & scp -O -o ConnectTimeout=20 -r -- $StageRoot "${Destination}:/tmp/"
    if ($LASTEXITCODE -ne 0) { throw 'Upload failed. Target was not changed.' }
    & ssh -T -o ConnectTimeout=20 -o ServerAliveInterval=15 $Destination "sh /tmp/$UploadName/run.sh"
    if ($LASTEXITCODE -ne 0) { throw "$Action failed. Success was not confirmed; see output above." }
    Write-Host "Xray remote control $TargetKind $Action finished successfully."
} finally {
    if (-not $PrepareOnly -and (Test-Path -LiteralPath $StageRoot)) {
        $Resolved = [System.IO.Path]::GetFullPath($StageRoot)
        $Expected = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetTempPath()) $UploadName))
        if ($Resolved -eq $Expected) { Remove-Item -LiteralPath $Resolved -Recurse -Force }
    }
}
