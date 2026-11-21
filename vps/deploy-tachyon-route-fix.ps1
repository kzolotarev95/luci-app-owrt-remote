param(
    [string]$Vps = 'root@132.243.19.232',
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$UploadName = 'owrt-tachyon-route-' + [guid]::NewGuid().ToString('N')
$StageRoot = Join-Path ([IO.Path]::GetTempPath()) $UploadName
$Utf8 = [Text.UTF8Encoding]::new($false)
$RemoteScript = @'
#!/bin/sh
set -eu
umask 077
stage=/tmp/__UPLOAD_NAME__
backup=/root/owrt-remote-backup-before-tachyon-route-fix-v121
hub=/opt/owrt-remote/owrt-remote-hub.py
xray_config=/etc/xray/owrt-remote.json
python_bin=/opt/owrt-remote/venv/bin/python
[ -x "$python_bin" ] || python_bin=python3
changed=0
step=preflight
snapshot() {
    mkdir -p "$1"; chmod 0700 "$1"
    cp -p "$hub" "$1/hub.py"
    cp -p "$xray_config" "$1/xray.json"
    : >"$1/complete"
}
apply_files() {
    cp "$1/hub.py" "$hub.new-__UPLOAD_NAME__"
    chmod 0755 "$hub.new-__UPLOAD_NAME__"; mv -f "$hub.new-__UPLOAD_NAME__" "$hub"
    cp "$1/xray.json" "$xray_config.new-__UPLOAD_NAME__"
    chmod 0600 "$xray_config.new-__UPLOAD_NAME__"; mv -f "$xray_config.new-__UPLOAD_NAME__" "$xray_config"
}
restart_services() {
    systemctl restart owrt-remote
    systemctl restart owrt-remote-xray
}
cleanup() {
    code=$?; trap - EXIT; set +e
    if [ "$code" -ne 0 ]; then
        echo "Tachyon route fix failed during $step (rc=$code)." >&2
        if [ "$changed" = 1 ]; then
            echo 'Restoring Hub and Xray configuration from before this attempt.' >&2
            apply_files "$stage/previous" && restart_services || echo 'Automatic restoration was not confirmed.' >&2
        fi
    fi
    rm -f "$hub.new-__UPLOAD_NAME__" "$xray_config.new-__UPLOAD_NAME__"
    rm -rf "$stage"
    exit "$code"
}
trap cleanup EXIT
trap 'exit 1' INT TERM
test -s "$hub"; test -s "$xray_config"; test -x /usr/local/bin/xray
if [ '__ACTION__' = Install ]; then
    "$python_bin" -m py_compile "$stage/hub.py"
    step=prepare-xray
    cp -p "$xray_config" "$stage/baseline-xray.json"
    "$python_bin" - "$stage/baseline-xray.json" "$stage/xray.json" <<'PY'
import json,os,pathlib,sys
config=json.loads(pathlib.Path(sys.argv[1]).read_text())
changed=0
for inbound in config.get('inbounds',[]):
    if inbound.get('protocol')!='tunnel' or not str(inbound.get('tag','')).startswith(('entry-','ssh-','tachyon-')):
        continue
    settings=inbound.get('settings',{})
    if not all(k in settings for k in ('allowedNetwork','rewriteAddress','rewritePort')):
        continue
    port=settings['rewritePort']
    if not isinstance(port,int) or isinstance(port,bool) or not 1<=port<=65535:
        raise SystemExit('Invalid reverse target port')
    settings.update(network=settings['allowedNetwork'],address=settings['rewriteAddress'],port=port)
    changed+=1
out=pathlib.Path(sys.argv[2])
out.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n')
os.chmod(out,0o600)
print('Prepared compatible reverse listeners: '+str(changed))
PY
    source_dir="$stage"
else
    test -e "$backup/complete" && test -s "$backup/hub.py" && test -s "$backup/xray.json" || {
        echo "No complete backup: $backup. Nothing changed." >&2; exit 1;
    }
    source_dir="$backup"
fi
step=validate-xray
/usr/local/bin/xray -test -config "$source_dir/xray.json"
step=backup
if [ '__ACTION__' = Install ] && [ ! -e "$backup/complete" ]; then
    test ! -e "$backup" || { echo "Incomplete backup exists: $backup" >&2; exit 1; }
    snapshot "$backup"
fi
snapshot "$stage/previous"
if [ '__ACTION__' = Install ]; then
    cmp "$stage/baseline-xray.json" "$stage/previous/xray.json" || {
        echo 'Xray configuration changed during preflight; retry the installation.' >&2; exit 1;
    }
fi
changed=1
step=apply-files
apply_files "$source_dir"
step=restart
restart_services
step=verify-health
"$python_bin" - <<'PY'
import hashlib,json,pathlib,time,urllib.request,subprocess
expected=hashlib.sha256(pathlib.Path('/opt/owrt-remote/owrt-remote-hub.py').read_bytes()).hexdigest()
client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
for attempt in range(25):
    try:
        with client.open('http://127.0.0.1:8088/health?panel-update=1',timeout=2) as response:
            data=json.load(response)
        running=subprocess.run(['systemctl','is-active','--quiet','owrt-remote-xray']).returncode==0
        if data.get('ok') and data.get('revision')==expected and running: break
    except (OSError,ValueError): pass
    time.sleep(1)
else: raise SystemExit('Hub revision or Xray service was not confirmed')
print('Running Hub and Xray verified: '+expected)
PY
cmp "$source_dir/hub.py" "$hub"
cmp "$source_dir/xray.json" "$xray_config"
echo "Backup: $backup"
echo 'TACHYON_ROUTE_FIX___ACTION___OK'
'@
try {
    [IO.Directory]::CreateDirectory($StageRoot) | Out-Null
    $RemoteScript = $RemoteScript.Replace('__UPLOAD_NAME__', $UploadName).Replace('__ACTION__', $Action).Replace([string][char]13, [string]::Empty)
    [IO.File]::WriteAllText((Join-Path $StageRoot 'run.sh'), $RemoteScript + [char]10, $Utf8)
    if ($Action -eq 'Install') {
        $Source = [IO.File]::ReadAllText((Join-Path $RepoRoot 'vps\owrt-remote-hub.py')).Replace([string][char]13, [string]::Empty)
        [IO.File]::WriteAllText((Join-Path $StageRoot 'hub.py'), $Source, $Utf8)
    }
    if ($PrepareOnly) { Write-Host "Prepared only: $StageRoot"; return }
    & scp -O -o ConnectTimeout=20 -r -- $StageRoot "${Vps}:/tmp/"
    if ($LASTEXITCODE -ne 0) { throw 'Upload failed. VPS was not changed.' }
    & ssh -T -o ConnectTimeout=20 -o ServerAliveInterval=15 $Vps "sh /tmp/$UploadName/run.sh"
    if ($LASTEXITCODE -ne 0) { throw "$Action failed; success was not confirmed." }
    Write-Host "Tachyon route fix $Action finished successfully."
} finally {
    if (-not $PrepareOnly -and (Test-Path -LiteralPath $StageRoot)) {
        $Resolved = [IO.Path]::GetFullPath($StageRoot)
        $Expected = [IO.Path]::GetFullPath((Join-Path ([IO.Path]::GetTempPath()) $UploadName))
        if ($Resolved -ne $Expected) { throw 'Unsafe staging cleanup path.' }
        Remove-Item -LiteralPath $Resolved -Recurse -Force
    }
}
