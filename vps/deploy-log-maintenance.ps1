param(
    [string]$Vps = 'root@132.243.19.232',
    [ValidateSet('Install', 'Rollback', 'Status', 'Run')][string]$Action = 'Install'
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$UploadName = 'owrt-log-maintenance-' + [guid]::NewGuid().ToString('N')
$StageRoot = Join-Path ([System.IO.Path]::GetTempPath()) $UploadName
$Utf8 = [System.Text.UTF8Encoding]::new($false)
$RemoteScript = @'
#!/bin/sh
set -eu
umask 077
stage=/tmp/__UPLOAD_NAME__
trap 'rm -rf "$stage"' EXIT
trap 'exit 1' INT TERM
case '__ACTION__' in
    Install)
        command -v logrotate >/dev/null || { echo 'Install prerequisite first: apt-get install logrotate' >&2; exit 1; }
        python3 "$stage/log-maintenance.py" install --hub-candidate "$stage/hub.py"
        if ! systemctl start logrotate.service; then
            journalctl -u logrotate.service -n 40 --no-pager >&2
            exit 1
        fi
        python3 /opt/owrt-remote/log-maintenance.py status
        ;;
    Rollback) python3 "$stage/log-maintenance.py" rollback ;;
    Status) python3 /opt/owrt-remote/log-maintenance.py status ;;
    Run) python3 /opt/owrt-remote/log-maintenance.py run --force ;;
esac
echo 'LOG_MAINTENANCE___ACTION___OK'
'@
$RemoteScript = $RemoteScript.Replace('__UPLOAD_NAME__', $UploadName).Replace('__ACTION__', $Action)
try {
    [System.IO.Directory]::CreateDirectory($StageRoot) | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $StageRoot 'run.sh'), $RemoteScript.Replace("`r`n", "`n") + "`n", $Utf8)
    foreach ($Pair in @(@('vps\log-maintenance.py', 'log-maintenance.py'), @('vps\owrt-remote-hub.py', 'hub.py'))) {
        if ($Pair[1] -eq 'hub.py' -and $Action -ne 'Install') { continue }
        $Content = [System.IO.File]::ReadAllText((Join-Path $RepoRoot $Pair[0])).Replace("`r`n", "`n")
        [System.IO.File]::WriteAllText((Join-Path $StageRoot $Pair[1]), $Content, $Utf8)
    }
    & scp -O -r -- $StageRoot "${Vps}:/tmp/"
    if ($LASTEXITCODE -ne 0) { throw 'Upload failed. VPS was not changed.' }
    & ssh -T -o ConnectTimeout=15 $Vps "sh /tmp/$UploadName/run.sh"
    if ($LASTEXITCODE -ne 0) { throw "$Action failed; success was not confirmed. See the error above." }
    Write-Host "Log maintenance $Action finished successfully."
}
finally {
    $Resolved = [System.IO.Path]::GetFullPath($StageRoot)
    $Expected = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetTempPath()) $UploadName))
    if ($Resolved -ne $Expected -or (Split-Path -Leaf $Resolved) -ne $UploadName) { throw 'Unsafe staging cleanup path.' }
    if (Test-Path -LiteralPath $Resolved) { Remove-Item -LiteralPath $Resolved -Recurse -Force }
}
