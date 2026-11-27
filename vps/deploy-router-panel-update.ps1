param(
    [ValidateNotNullOrEmpty()][string]$Router = 'root@192.168.2.1',
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [ValidatePattern('^owrt-remote-backup-[A-Za-z0-9_-]{1,96}$')]
    [string]$BackupName = 'owrt-remote-backup-before-router-panel-update-v125',
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$UploadName = 'owrt-router-panel-update-v125-' + [guid]::NewGuid().ToString('N')
$StageRoot = Join-Path ([System.IO.Path]::GetTempPath()) $UploadName
$Utf8 = [System.Text.UTF8Encoding]::new($false)

$RemoteScript = @'
#!/bin/sh
set -eu
umask 077
stage=/tmp/__UPLOAD_NAME__
backup=/root/owrt-remote-backup-before-router-panel-update-v125
lock=/tmp/owrt-remote-router-panel-update-deploy.lock
names='cgi luci effects updater downloader updatejs version'
changed=0
locked=0
step=preflight

target() {
    case "$1" in
        cgi) printf '%s\n' /www/cgi-bin/owrt-remote ;;
        luci) printf '%s\n' /www/luci-static/resources/view/owrt_remote.js ;;
        effects) printf '%s\n' /www/luci-static/resources/owrt_remote_effects.js ;;
        updater) printf '%s\n' /usr/lib/owrt-remote/panel-update.sh ;;
        downloader) printf '%s\n' /usr/lib/owrt-remote/panel-download.sh ;;
        updatejs) printf '%s\n' /www/luci-static/resources/owrt_remote_update.js ;;
        version) printf '%s\n' /usr/share/owrt-remote/panel-version ;;
        *) return 1 ;;
    esac
}

valid_snapshot() {
    directory=$1
    [ -f "$directory/complete" ] || return 1
    for name in $names; do
        if [ -f "$directory/$name.missing" ]; then
            [ ! -e "$directory/$name" ] || return 1
        else
            [ -s "$directory/$name" ] || return 1
        fi
    done
    # A pre-existing Remote installation always has these entry points.
    [ -s "$directory/cgi" ] && [ -s "$directory/luci" ]
}

save_snapshot() {
    directory=$1
    mkdir -m 700 "$directory"
    for name in $names; do
        path=$(target "$name")
        if [ -f "$path" ]; then
            cp -p "$path" "$directory/$name"
        else
            : >"$directory/$name.missing"
        fi
    done
    : >"$directory/complete"
    valid_snapshot "$directory"
}

restore_previous() {
    echo 'Restoring UI files from before this attempt.' >&2
    failed=0
    for name in $names; do
        path=$(target "$name")
        if [ -f "$stage/previous/$name.missing" ]; then
            rm -f "$path" || failed=1
        else
            cp -p "$stage/previous/$name" "$path.new-__UPLOAD_NAME__" &&
                mv -f "$path.new-__UPLOAD_NAME__" "$path" || failed=1
        fi
    done
    if [ "$failed" -ne 0 ]; then
        echo "Automatic restoration incomplete. Recovery files retained at $stage/previous" >&2
        return 1
    fi
}

cleanup() {
    code=$?
    trap - EXIT INT TERM
    set +e
    keep_stage=0
    if [ "$code" -ne 0 ]; then
        echo "Router panel update __ACTION__ failed during $step (rc=$code)." >&2
        if [ "$changed" = 1 ]; then
            restore_previous || keep_stage=1
        fi
    fi
    for name in $names; do
        path=$(target "$name")
        rm -f "$path.new-__UPLOAD_NAME__"
    done
    if [ "$locked" = 1 ]; then rmdir "$lock"; fi
    if [ "$keep_stage" = 0 ]; then rm -rf "$stage"; fi
    exit "$code"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

[ -d "$stage" ] || { echo 'Upload stage missing.' >&2; exit 1; }
if ! mkdir "$lock" 2>/dev/null; then
    echo "Another router UI deployment is active: $lock. No files changed." >&2
    exit 1
fi
locked=1
for name in cgi luci; do
    path=$(target "$name")
    [ -s "$path" ] || { echo "Remote UI is not installed: $path" >&2; exit 1; }
done

if [ '__ACTION__' = Install ]; then
    step=candidate-validation
    for name in $names; do
        [ -s "$stage/$name" ] || { echo "Candidate file missing: $name" >&2; exit 1; }
    done
    sh -n "$stage/cgi"
    sh -n "$stage/updater"
    sh -n "$stage/downloader"
    grep -Fq "routerUpdateDialog" "$stage/cgi"
    grep -Fq "X-OWRT-Panel-Update" "$stage/updatejs"
    grep -Eq '<title>OWRT Hub v[0-9]+</title>' "$stage/cgi"
    grep -Fq 'routerSidebar' "$stage/cgi"
    grep -Eq 'OWRT Hub v[0-9]+' "$stage/luci"
    ui_version=$(sed -n 's/.*<title>OWRT Hub \(v[0-9][0-9]*\)<\/title>.*/\1/p' "$stage/cgi" | head -n 1)
    [ -n "$ui_version" ] && grep -Fq "OWRT Hub $ui_version" "$stage/luci"
    grep -Fq 'HUB_DESIGN_MODES' "$stage/effects"
    step=backup
    if [ ! -e "$backup" ]; then
        save_snapshot "$stage/retained"
        # This is one rename after a complete snapshot; the lock prevents races.
        mv "$stage/retained" "$backup"
    fi
    if ! valid_snapshot "$backup"; then
        echo "No complete backup: $backup. No files changed." >&2
        exit 1
    fi
    source_dir=$stage
else
    step=backup-validation
    if ! valid_snapshot "$backup"; then
        echo "No complete backup: $backup. No files changed." >&2
        exit 1
    fi
    source_dir=$backup
fi
sh -n "$source_dir/cgi"
step=snapshot-current
save_snapshot "$stage/previous"

# Prepare all replacements before any working file is modified.
step=prepare-replacements
for name in $names; do
    path=$(target "$name")
    mkdir -p "$(dirname "$path")"
    if [ ! -f "$source_dir/$name.missing" ]; then
        cp "$source_dir/$name" "$path.new-__UPLOAD_NAME__"
        if [ "$name" = cgi ]; then mode=0755; else mode=0644; fi
        chmod "$mode" "$path.new-__UPLOAD_NAME__"
        cmp "$source_dir/$name" "$path.new-__UPLOAD_NAME__"
    fi
done
step=replace
changed=1
for name in $names; do
    path=$(target "$name")
    if [ -f "$source_dir/$name.missing" ]; then
        rm -f "$path"
    else
        mv -f "$path.new-__UPLOAD_NAME__" "$path"
    fi
done
step=verify-installed
for name in $names; do
    path=$(target "$name")
    if [ -f "$source_dir/$name.missing" ]; then
        [ ! -e "$path" ]
    else
        cmp "$source_dir/$name" "$path"
    fi
done
sh -n "$(target cgi)"
# CGI and static JS are read on each new request. No service restart is needed.
printf '\nBackup: %s\nROUTER_PANEL_UPDATE_V125_%s_OK\n' "$backup" '__ACTION__'
'@

$RemoteScript = $RemoteScript.Replace('__UPLOAD_NAME__', $UploadName).Replace('__ACTION__', $Action)
$RemoteScript = $RemoteScript.Replace('/root/owrt-remote-backup-before-router-panel-update-v125', '/root/' + $BackupName)
try {
    [System.IO.Directory]::CreateDirectory($StageRoot) | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $StageRoot 'run.sh'), $RemoteScript.Replace("`r`n", "`n") + "`n", $Utf8)
    if ($Action -eq 'Install') {
        $Files = @{
            'cgi' = 'files\www\cgi-bin\owrt-remote'
            'luci' = 'files\www\luci-static\resources\view\owrt_remote.js'
            'effects' = 'files\www\luci-static\resources\owrt_remote_effects.js'
            'updater' = 'files\usr\lib\owrt-remote\panel-update.sh'
            'downloader' = 'files\usr\lib\owrt-remote\panel-download.sh'
            'updatejs' = 'files\www\luci-static\resources\owrt_remote_update.js'
            'version' = 'files\usr\share\owrt-remote\panel-version'
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
    & scp -O -r -- $StageRoot "${Router}:/tmp/"
    if ($LASTEXITCODE -ne 0) { throw 'Upload failed. Router files were not changed.' }
    & ssh -T -o ConnectTimeout=15 $Router "sh /tmp/$UploadName/run.sh"
    if ($LASTEXITCODE -ne 0) { throw "$Action failed. Success was not confirmed; see the diagnostic above." }
    Write-Host "Router panel update $Action finished successfully. Refresh with Ctrl+F5."
}
finally {
    if (-not $PrepareOnly) {
        $Resolved = [System.IO.Path]::GetFullPath($StageRoot)
        $Expected = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetTempPath()) $UploadName))
        if ($Resolved -ne $Expected -or (Split-Path -Leaf $Resolved) -ne $UploadName) { throw 'Unsafe staging cleanup path.' }
        if (Test-Path -LiteralPath $Resolved) { Remove-Item -LiteralPath $Resolved -Recurse -Force }
    }
}
