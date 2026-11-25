[CmdletBinding(DefaultParameterSetName = 'Vps')]
param(
    [Parameter(ParameterSetName = 'Vps')][ValidateNotNullOrEmpty()][string]$Vps = 'root@13.143.167.219',
    [Parameter(Mandatory = $true, ParameterSetName = 'Router')][ValidateNotNullOrEmpty()][string]$Router,
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [ValidatePattern('^owrt-remote-backup-[A-Za-z0-9_-]{1,96}$')]
    [string]$BackupName = 'owrt-remote-backup-before-remove-global-logs-v124',
    [switch]$PrepareOnly
)
$ErrorActionPreference = 'Stop'
if ($PSCmdlet.ParameterSetName -eq 'Vps') {
    & (Join-Path $PSScriptRoot 'deploy-native-mobile-api.ps1') -Vps $Vps -Action $Action `
        -BackupName $BackupName -PrepareOnly:$PrepareOnly
    return
}
$RepoRoot = Split-Path -Parent $PSScriptRoot
$UploadName = 'owrt-retire-journal-' + [guid]::NewGuid().ToString('N')
$StageRoot = Join-Path ([System.IO.Path]::GetTempPath()) $UploadName
$Utf8 = [System.Text.UTF8Encoding]::new($false)
$RemoteScript = @'
#!/bin/sh
set -eu
umask 077
stage=/tmp/__UPLOAD_NAME__
backup=/root/owrt-remote-backup-before-remove-global-logs-v124
lock=/tmp/owrt-remote-retire-journal.lock
action=__ACTION__
names='agent init cgi hotplug control effects luci retire tachyon'
snapshot_names="$names old-helper old-init"
changed=0
locked=0
step=preflight
target() {
    case "$1" in
        agent) echo /usr/sbin/owrt-remote ;;
        init) echo /etc/init.d/owrt-remote ;;
        cgi) echo /www/cgi-bin/owrt-remote ;;
        hotplug) echo /etc/hotplug.d/iface/99-owrt-remote ;;
        control) echo /usr/lib/owrt-remote/control.sh ;;
        effects) echo /www/luci-static/resources/owrt_remote_effects.js ;;
        luci) echo /www/luci-static/resources/view/owrt_remote.js ;;
        retire) echo /usr/lib/owrt-remote/retire-logs.sh ;;
        tachyon) echo /usr/lib/owrt-remote/tachyon.sh ;;
        old-helper) echo /usr/lib/owrt-remote/logs.sh ;;
        old-init) echo /etc/init.d/owrt-remote-logs ;;
        *) return 1 ;;
    esac
}
snapshot() {
    folder=$1
    mkdir -m 700 "$folder"
    for name in $snapshot_names; do
        path=$(target "$name")
        if [ -f "$path" ]; then cp -p "$path" "$folder/$name"; else : >"$folder/$name.absent"; fi
    done
    if /etc/init.d/owrt-remote running >/dev/null 2>&1; then : >"$folder/running"; fi
    if [ -x /etc/init.d/owrt-remote-logs ]; then
        if /etc/init.d/owrt-remote-logs running >/dev/null 2>&1; then : >"$folder/old-running"; fi
        if /etc/init.d/owrt-remote-logs enabled >/dev/null 2>&1; then : >"$folder/old-enabled"; fi
    fi
    : >"$folder/complete"
}
validate_snapshot() {
    [ -f "$1/complete" ] && [ -s "$1/agent" ] && [ -s "$1/cgi" ] && [ -s "$1/init" ] || return 1
    for name in $snapshot_names; do
        [ -s "$1/$name" ] || [ -f "$1/$name.absent" ] || return 1
    done
    sh -n "$1/agent" && sh -n "$1/init" && sh -n "$1/cgi"
}
apply() {
    folder=$1
    list=$2
    for name in $list; do
        path=$(target "$name")
        if [ -f "$folder/$name.absent" ]; then rm -f "$path" || return
        else
            mkdir -p "${path%/*}" || return
            cp "$folder/$name" "$path.new-__UPLOAD_NAME__" || return
            case "$name" in agent|init|cgi|hotplug|old-init) chmod 0755 "$path.new-__UPLOAD_NAME__" ;; *) chmod 0644 "$path.new-__UPLOAD_NAME__" ;; esac || return
            mv -f "$path.new-__UPLOAD_NAME__" "$path" || return
        fi
    done
}
resume() {
    folder=$1
    if [ -f "$folder/old-enabled" ] && [ -x /etc/init.d/owrt-remote-logs ]; then /etc/init.d/owrt-remote-logs enable || return; fi
    if [ -f "$folder/old-running" ] && [ -x /etc/init.d/owrt-remote-logs ]; then /etc/init.d/owrt-remote-logs start || return; fi
    if [ -f "$folder/running" ]; then /etc/init.d/owrt-remote start || return; fi
}
cleanup() {
    code=$?
    trap - EXIT INT TERM
    set +e
    keep=0
    if [ "$code" -ne 0 ]; then
        echo "$action failed during $step (rc=$code)." >&2
        if [ "$changed" = 1 ]; then
            /etc/init.d/owrt-remote stop >/dev/null 2>&1
            if ! apply "$stage/previous" "$snapshot_names" || ! resume "$stage/previous"; then
                echo "Recovery files retained: $stage/previous" >&2
                keep=1
            fi
        fi
    fi
    for name in $snapshot_names; do path=$(target "$name"); rm -f "$path.new-__UPLOAD_NAME__"; done
    [ "$locked" != 1 ] || rmdir "$lock"
    [ "$keep" != 0 ] || rm -rf "$stage"
    exit "$code"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir "$lock" || { echo 'Another journal-removal deployment is active.' >&2; exit 1; }
locked=1
if [ "$action" = Install ]; then
    step=validate
    for name in $names; do [ -s "$stage/$name" ]; done
    for name in agent init cgi hotplug control retire tachyon; do sh -n "$stage/$name"; done
    if [ ! -e "$backup" ]; then snapshot "$stage/retained"; mv "$stage/retained" "$backup"; fi
else
    validate_snapshot "$backup"
fi
validate_snapshot "$backup"
snapshot "$stage/previous"
step=stop
changed=1
if [ -f "$stage/previous/running" ]; then /etc/init.d/owrt-remote stop; fi
step=replace
if [ "$action" = Install ]; then
    apply "$stage" "$names"
    step=retire
    sh /usr/lib/owrt-remote/retire-logs.sh
    step=resume
    if [ -f "$stage/previous/running" ]; then /etc/init.d/owrt-remote start; fi
    step=verify
    for name in $names; do cmp "$stage/$name" "$(target "$name")"; done
    test ! -e /etc/init.d/owrt-remote-logs
    test ! -e /usr/lib/owrt-remote/logs.sh
else
    apply "$backup" "$snapshot_names"
    resume "$backup"
fi
sh -n /www/cgi-bin/owrt-remote
if [ -f "$stage/previous/running" ]; then /etc/init.d/owrt-remote running; fi
printf '\nBackup: %s\nREMOVE_GLOBAL_LOGS_%s_OK\n' "$backup" "$action"
'@
$RemoteScript = $RemoteScript.Replace('__UPLOAD_NAME__', $UploadName).Replace('__ACTION__', $Action)
$RemoteScript = $RemoteScript.Replace('/root/owrt-remote-backup-before-remove-global-logs-v124', '/root/' + $BackupName)
try {
    [System.IO.Directory]::CreateDirectory($StageRoot) | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $StageRoot 'run.sh'), $RemoteScript.Replace("`r`n", "`n") + "`n", $Utf8)
    if ($Action -eq 'Install') {
        $Files = @{
            agent = 'files\usr\sbin\owrt-remote'; init = 'files\etc\init.d\owrt-remote'
            cgi = 'files\www\cgi-bin\owrt-remote'; hotplug = 'files\etc\hotplug.d\iface\99-owrt-remote'
            control = 'files\usr\lib\owrt-remote\control.sh'; retire = 'files\usr\lib\owrt-remote\retire-logs.sh'
            tachyon = 'files\usr\lib\owrt-remote\tachyon.sh'
            effects = 'files\www\luci-static\resources\owrt_remote_effects.js'; luci = 'files\www\luci-static\resources\view\owrt_remote.js'
        }
        foreach ($Name in $Files.Keys) {
            $Content = [System.IO.File]::ReadAllText((Join-Path $RepoRoot $Files[$Name])).Replace("`r`n", "`n")
            [System.IO.File]::WriteAllText((Join-Path $StageRoot $Name), $Content, $Utf8)
        }
    }
    if ($PrepareOnly) { Write-Host "Prepared only: $StageRoot"; return }
    & scp -O -r -- $StageRoot "${Router}:/tmp/"
    if ($LASTEXITCODE -ne 0) { throw 'Upload failed. Router was not changed.' }
    & ssh -T -o ConnectTimeout=15 $Router "sh /tmp/$UploadName/run.sh"
    if ($LASTEXITCODE -ne 0) { throw "$Action failed. Success was not confirmed." }
    Write-Host "Journal removal $Action completed. Refresh the router panel."
} finally {
    if (-not $PrepareOnly) {
        $Resolved = [System.IO.Path]::GetFullPath($StageRoot)
        $Expected = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetTempPath()) $UploadName))
        if ($Resolved -ne $Expected -or (Split-Path -Leaf $Resolved) -ne $UploadName) { throw 'Unsafe staging cleanup path.' }
        if (Test-Path -LiteralPath $Resolved) { Remove-Item -LiteralPath $Resolved -Recurse -Force }
    }
}
