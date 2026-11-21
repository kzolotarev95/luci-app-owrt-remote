[CmdletBinding(DefaultParameterSetName = 'Router')]
param(
    [Parameter(ParameterSetName = 'Router')][string]$Router = 'root@192.168.2.1',
    [Parameter(Mandatory = $true, ParameterSetName = 'Vps')][string]$Vps,
    [Parameter(ParameterSetName = 'Vps')][ValidatePattern('^[A-Za-z0-9_.-]+$')][string]$RouterId,
    [ValidateSet('Install', 'Rollback', 'Status')][string]$Action = 'Install',
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
$Deploy = Join-Path $PSScriptRoot 'deploy-tachyon.ps1'
if ($Action -eq 'Status') {
    if (-not $Vps -or -not $RouterId) { throw 'Status requires -Vps and -RouterId, for example node-12.' }
    $Status = @'
import json, sqlite3, time, urllib.request
from contextlib import closing
with closing(sqlite3.connect('file:/var/lib/owrt-remote/hub.db?mode=ro', uri=True)) as db:
    row = db.execute('select last_seen,status_json,tachyon_entry_port from routers where id=? and coalesce(deleted_at,0)=0', ('__ROUTER_ID__',)).fetchone()
if not row:
    raise SystemExit('Router __ROUTER_ID__ not found in Hub')
status = json.loads(row[1] or '{}')
tachyon = status.get('tachyon') or {}
age = max(0, int(time.time() - float(row[0] or 0)))
api_ok = False
reason = 'standalone route unavailable'
if tachyon.get('mode') == 'standalone' and row[2]:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('http://127.0.0.1:' + str(int(row[2])) + '/api/agent/v1/health', timeout=4) as response:
            health = json.loads(response.read(65536))
        api_ok = type(health.get('core_running')) is bool
        reason = 'Tachyon API confirmed through the reverse tunnel' if api_ok else 'Unexpected API response'
    except Exception as error:
        reason = 'Reverse route: ' + type(error).__name__
print(json.dumps({'router':'__ROUTER_ID__', 'version':tachyon.get('version') or '',
    'package_version':tachyon.get('package_version') or '', 'mode':tachyon.get('mode') or 'unknown',
    'port':tachyon.get('port'), 'xray':status.get('xray'), 'heartbeat_age_seconds':age,
    'api_ok':api_ok, 'reason':reason}, ensure_ascii=True, indent=2))
ok = age <= 120 and status.get('xray') == 'running' and tachyon.get('available') and api_ok
print('TACHYON_CHECK_OK: __ROUTER_ID__' if ok else 'TACHYON_CHECK_FAILED: __ROUTER_ID__')
raise SystemExit(0 if ok else 1)
'@
    $Status = $Status.Replace('__ROUTER_ID__', $RouterId)
    $Status.Replace("`r`n", "`n") | & ssh -T -o ConnectTimeout=20 $Vps 'python3 -'
    if ($LASTEXITCODE -ne 0) { throw 'Router check failed. See mode, version and API result above. No files were changed.' }
    return
}
$Options = @{ Action = $Action; BackupName = 'tachyon-detection'; BackupVersion = 'v122'; PrepareOnly = $PrepareOnly }
if ($PSCmdlet.ParameterSetName -eq 'Router') {
    & $Deploy -Router $Router @Options
} elseif (-not $RouterId) {
    & $Deploy -Vps $Vps @Options
} else {
    # Query only this router's loopback SSH listener. RouterId cannot contain
    # shell/SQL quoting characters, and the database is opened read-only.
    $Query = @'
import sqlite3, time
from contextlib import closing
with closing(sqlite3.connect('file:/var/lib/owrt-remote/hub.db?mode=ro', uri=True)) as db:
    row = db.execute('select ssh_entry_port from routers where id=? and coalesce(deleted_at,0)=0', ('__ROUTER_ID__',)).fetchone()
if not row or not isinstance(row[0], int) or not 1024 <= row[0] <= 65535:
    raise SystemExit('Router has no valid loopback SSH entry in Hub')
print('TACHYON_SSH_PORT=' + str(row[0]))
print('TACHYON_MIN_SEEN=' + str(int(time.time())))
'@
    $Query = $Query.Replace('__ROUTER_ID__', $RouterId)
    $Output = $Query.Replace("`r`n", "`n") | & ssh -T -o ConnectTimeout=20 $Vps 'python3 -'
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read the selected router SSH port. Router was not changed.' }
    $PortLine = @($Output | Where-Object { $_ -match '^TACHYON_SSH_PORT=([0-9]+)$' })
    if ($PortLine.Count -ne 1) { throw 'Hub did not return one SSH port. Router was not changed.' }
    $RouterPort = [int]($PortLine[0] -replace '^TACHYON_SSH_PORT=', '')
    if ($RouterPort -lt 1024 -or $RouterPort -gt 65535) { throw 'Invalid SSH listener port.' }
    Write-Host "Selected router: $RouterId; SSH via $Vps and loopback port $RouterPort"
    $SinceLine = @($Output | Where-Object { $_ -match '^TACHYON_MIN_SEEN=([0-9]+)$' })
    if ($SinceLine.Count -ne 1) { throw 'Hub did not return a verification timestamp.' }
    $Since = [long]($SinceLine[0] -replace '^TACHYON_MIN_SEEN=', '')
    & $Deploy -Router root@127.0.0.1 -Port $RouterPort -JumpHost $Vps -DeferRestart @Options
    if ($PrepareOnly) { return }
    # Verification uses the VPS, so a reconnecting reverse SSH tunnel does
    # not interrupt it. It also verifies the real route, not just a green card.
    $Verify = @'
import json, sqlite3, time, urllib.request
from contextlib import closing
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
last = 'Waiting for a fresh Heartbeat and the standalone route'
for attempt in range(40):
    with closing(sqlite3.connect('file:/var/lib/owrt-remote/hub.db?mode=ro', uri=True)) as db:
        row = db.execute('select last_seen,status_json,tachyon_entry_port from routers where id=?', ('__ROUTER_ID__',)).fetchone()
    try:
        status = json.loads(row[1] or '{}') if row else {}
        tachyon = status.get('tachyon') or {}
        fresh = row and float(row[0] or 0) >= __SINCE__
        if fresh and status.get('xray') == 'running':
            if '__ACTION__' == 'Rollback':
                print('TACHYON_DETECTION_Router_Rollback_OK: fresh Heartbeat; Xray running')
                break
            if tachyon.get('mode') == 'standalone' and tachyon.get('available') and tachyon.get('version') and row[2]:
                with opener.open('http://127.0.0.1:' + str(int(row[2])) + '/api/agent/v1/health', timeout=3) as response:
                    health = json.loads(response.read(65536))
                if type(health.get('core_running')) is bool:
                    print('TACHYON_DETECTION_Router_Install_OK: ' + str(tachyon.get('version')) + '; standalone API verified through the router tunnel')
                    break
        last = 'Waiting for router __ROUTER_ID__: mode=' + str(tachyon.get('mode')) + '; Xray=' + str(status.get('xray'))
    except Exception as error:
        last = 'Reverse route not ready: ' + type(error).__name__
    time.sleep(2)
else:
    raise SystemExit('Result not confirmed: ' + last + '; backup retained at /root/owrt-remote-backup-before-tachyon-detection-v122')
'@
    $Verify = $Verify.Replace('__ROUTER_ID__', $RouterId).Replace('__SINCE__', [string]$Since).Replace('__ACTION__', $Action)
    $Verify.Replace("`r`n", "`n") | & ssh -T -o ConnectTimeout=20 -o ServerAliveInterval=15 $Vps 'python3 -'
    if ($LASTEXITCODE -ne 0) { throw 'Router result not confirmed. See diagnostic above; retained backup is available for Rollback.' }
}
