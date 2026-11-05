param(
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$Router,
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$UploadName = 'owrt-heartbeat-redirect-' + [guid]::NewGuid().ToString('N')
$StageRoot = Join-Path ([System.IO.Path]::GetTempPath()) $UploadName
$Utf8 = [System.Text.UTF8Encoding]::new($false)
$RemoteScript = @'
#!/bin/sh
set -eu
stage=/tmp/__UPLOAD_NAME__
backup=/root/owrt-remote-backup-before-heartbeat-redirect-v121
agent=/usr/sbin/owrt-remote
wan=/usr/lib/owrt-remote/wan.sh
changed=0
cleanup() {
    code=$?
    trap - EXIT
    if [ "$code" -ne 0 ] && [ "$changed" = 1 ]; then
        /etc/init.d/owrt-remote stop >/dev/null 2>&1 || true
        cp -p "$stage/previous-agent" "$agent" || true
        cp -p "$stage/previous-wan" "$wan" || true
        /etc/init.d/owrt-remote restart >/dev/null 2>&1 || true
    fi
    rm -f "$agent.new-__UPLOAD_NAME__" "$wan.new-__UPLOAD_NAME__"
    rm -rf "$stage"
    exit "$code"
}
trap cleanup EXIT
test -s "$agent"
test -s "$wan"
if [ '__ACTION__' = Install ]; then
    test -s "$stage/agent"
    test -s "$stage/wan.sh"
    sh -n "$stage/agent"
    sh -n "$stage/wan.sh"
    grep -F -q -- '--post301' "$stage/agent"
    grep -F -q -- '--post301' "$stage/wan.sh"
    mkdir -p "$backup"
    chmod 700 "$backup"
    if [ ! -e "$backup/agent" ]; then cp -p "$agent" "$backup/agent"; fi
    if [ ! -e "$backup/wan.sh" ]; then cp -p "$wan" "$backup/wan.sh"; fi
    source_agent="$stage/agent"
    source_wan="$stage/wan.sh"
else
    test -s "$backup/agent"
    test -s "$backup/wan.sh"
    source_agent="$backup/agent"
    source_wan="$backup/wan.sh"
fi
cp -p "$agent" "$stage/previous-agent"
cp -p "$wan" "$stage/previous-wan"
changed=1
cp "$source_agent" "$agent.new-__UPLOAD_NAME__"
chmod 0755 "$agent.new-__UPLOAD_NAME__"
mv -f "$agent.new-__UPLOAD_NAME__" "$agent"
cp "$source_wan" "$wan.new-__UPLOAD_NAME__"
chmod 0644 "$wan.new-__UPLOAD_NAME__"
mv -f "$wan.new-__UPLOAD_NAME__" "$wan"
/etc/init.d/owrt-remote restart
sh -n "$agent"
sh -n "$wan"
cmp "$source_agent" "$agent"
cmp "$source_wan" "$wan"
printf '\nBackup: %s\nHEARTBEAT_REDIRECT_ROUTER_%s_OK\n' "$backup" '__ACTION__'
'@
$RemoteScript = $RemoteScript.Replace('__UPLOAD_NAME__', $UploadName).Replace('__ACTION__', $Action)
try {
    [System.IO.Directory]::CreateDirectory($StageRoot) | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $StageRoot 'run.sh'), $RemoteScript.Replace("`r`n", "`n") + "`n", $Utf8)
    if ($Action -eq 'Install') {
        foreach ($Item in @{ 'agent' = 'files\usr\sbin\owrt-remote'; 'wan.sh' = 'files\usr\lib\owrt-remote\wan.sh' }.GetEnumerator()) {
            $Content = [System.IO.File]::ReadAllText((Join-Path $RepoRoot $Item.Value)).Replace("`r`n", "`n")
            [System.IO.File]::WriteAllText((Join-Path $StageRoot $Item.Key), $Content, $Utf8)
        }
    }
    if ($PrepareOnly) { Write-Host "Prepared only: $StageRoot"; return }
    & scp -O -r -- $StageRoot "${Router}:/tmp/"
    if ($LASTEXITCODE -ne 0) { throw 'Upload failed. The router was not changed.' }
    & ssh -T -o ConnectTimeout=10 $Router "sh /tmp/$UploadName/run.sh"
    if ($LASTEXITCODE -ne 0) { throw "$Action failed. See the output above; success was not confirmed." }
    Write-Host "Heartbeat redirect fix $Action finished successfully."
} finally {
    if (-not $PrepareOnly) {
        $Resolved = [System.IO.Path]::GetFullPath($StageRoot)
        $Expected = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetTempPath()) $UploadName))
        if ($Resolved -ne $Expected -or (Split-Path -Leaf $Resolved) -ne $UploadName) { throw 'Unsafe staging cleanup path.' }
        if (Test-Path -LiteralPath $Resolved) { Remove-Item -LiteralPath $Resolved -Recurse -Force }
    }
}
