param(
    [ValidateNotNullOrEmpty()][string]$Router = 'root@192.168.2.1',
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$UploadName = 'owrt-wan-dns-' + [guid]::NewGuid().ToString('N')
$StageRoot = Join-Path ([System.IO.Path]::GetTempPath()) $UploadName
$Utf8 = [System.Text.UTF8Encoding]::new($false)
$RemoteScript = @'
#!/bin/sh
set -eu
stage=/tmp/__UPLOAD_NAME__
target=/usr/lib/owrt-remote/wan.sh
backup_dir=/root/owrt-remote-backup-before-wan-dns-v121
changed=0
phase=preflight
heartbeat_pid() {
    local state
    state=$(ubus call service list '{"name":"owrt-remote"}') || return 1
    printf '%s' "$state" | jsonfilter -e '@["owrt-remote"].instances.heartbeat.pid' 2>/dev/null || true
}
refresh_heartbeat() {
    local old_pid new_pid attempt
    old_pid=$(heartbeat_pid) || return 1
    case "$old_pid" in ''|0) echo 'Heartbeat is stopped; the fix will load at its next start.'; return 0 ;; *[!0-9]*) return 1 ;; esac
    ubus call service signal '{"name":"owrt-remote","instance":"heartbeat","signal":9}' >/dev/null || return 1
    attempt=0
    while [ "$attempt" -lt 20 ]; do
        sleep 1
        new_pid=$(heartbeat_pid) || return 1
        case "$new_pid" in ''|0|*[!0-9]*) ;; *)
            [ "$new_pid" = "$old_pid" ] || { echo 'Heartbeat reloaded; Xray was not restarted.'; return 0; } ;;
        esac
        attempt=$((attempt+1))
    done
    echo 'Heartbeat did not respawn.' >&2
    return 1
}
restore_file() {
    cp -p "$1" "$target.new-__UPLOAD_NAME__" && mv -f "$target.new-__UPLOAD_NAME__" "$target"
}
cleanup() {
    code=$?
    trap - EXIT
    if [ "$code" -ne 0 ]; then
        printf 'Failed during %s (rc=%s).\n' "$phase" "$code" >&2
        if [ "$changed" = 1 ]; then
            echo 'Restoring WAN helper from this attempt.' >&2
            restore_file "$stage/previous" && refresh_heartbeat || echo 'Restore failed; retained backup is available.' >&2
        fi
    fi
    rm -f "$target.new-__UPLOAD_NAME__"
    rm -rf "$stage"
    exit "$code"
}
trap cleanup EXIT
test -s "$target"
heartbeat_pid >/dev/null
if [ '__ACTION__' = Install ]; then
    test -s "$stage/dns.sh"
    sh -n "$stage/dns.sh"
    grep -F -q 'nslookup -type=A -timeout=3 -retry=1' "$stage/dns.sh"
    # Patch DNS functions only, preserving the router's other WAN code and
    # compatibility with both the published and locally updated agent.
    awk -v bundle="$stage/dns.sh" '
        /^(wan_dns_metric|wan_resolve_host)\(\) \{$/ && !seen {
            while ((getline line < bundle)>0) print line
            close(bundle); seen=1; skip=1
        }
        /^wan_endpoint_ip\(\) \{$/ && skip { skip=0; finished=1 }
        !skip { print }
        END { if(!seen || !finished) exit 1 }
    ' "$target" >"$stage/candidate"
    sh -n "$stage/candidate"
    if [ ! -e "$backup_dir" ]; then
        mkdir "$stage/backup"
        chmod 700 "$stage/backup"
        cp -p "$target" "$stage/backup/wan.sh"
        mv "$stage/backup" "$backup_dir"
    fi
    test -s "$backup_dir/wan.sh"
    source_file="$stage/candidate"
else
    [ -s "$backup_dir/wan.sh" ] || { echo "No backup: $backup_dir. Nothing changed." >&2; exit 1; }
    sh -n "$backup_dir/wan.sh"
    source_file="$backup_dir/wan.sh"
fi
printf 'Backup: %s/wan.sh\n' "$backup_dir"
cp -p "$target" "$stage/previous"
phase=install
changed=1
cp -p "$target" "$target.new-__UPLOAD_NAME__"
cat "$source_file" >"$target.new-__UPLOAD_NAME__"
mv -f "$target.new-__UPLOAD_NAME__" "$target"
phase=verify
sh -n "$target"
cmp "$source_file" "$target"
phase=heartbeat-reload
refresh_heartbeat
printf '\nWAN_DNS_ROUTER_%s_OK\n' '__ACTION__'
'@
$RemoteScript = $RemoteScript.Replace('__UPLOAD_NAME__', $UploadName).Replace('__ACTION__', $Action)
try {
    [System.IO.Directory]::CreateDirectory($StageRoot) | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $StageRoot 'run.sh'), $RemoteScript.Replace("`r`n", "`n") + "`n", $Utf8)
    if ($Action -eq 'Install') {
        $Source = [System.IO.File]::ReadAllText((Join-Path $RepoRoot 'files\usr\lib\owrt-remote\wan.sh')).Replace("`r`n", "`n")
        $Block = [regex]::Match($Source, '(?ms)^wan_dns_metric\(\) \{.*?(?=^wan_endpoint_ip\(\) \{)')
        if (-not $Block.Success) { throw 'Local WAN DNS functions not found.' }
        [System.IO.File]::WriteAllText((Join-Path $StageRoot 'dns.sh'), $Block.Value, $Utf8)
    }
    if ($PrepareOnly) { Write-Host "Prepared only: $StageRoot"; return }
    & scp -O -r -- $StageRoot "${Router}:/tmp/"
    if ($LASTEXITCODE -ne 0) { throw 'Upload failed. The installation has not started.' }
    & ssh -T -o ConnectTimeout=10 $Router "sh /tmp/$UploadName/run.sh"
    if ($LASTEXITCODE -ne 0) { throw "$Action failed. See the output above; success was not confirmed." }
    Write-Host "WAN DNS fix $Action finished successfully."
} finally {
    if (-not $PrepareOnly) {
        $Resolved = [System.IO.Path]::GetFullPath($StageRoot)
        $Expected = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetTempPath()) $UploadName))
        if ($Resolved -ne $Expected -or (Split-Path -Leaf $Resolved) -ne $UploadName) { throw 'Unsafe staging cleanup path.' }
        if (Test-Path -LiteralPath $Resolved) { Remove-Item -LiteralPath $Resolved -Recurse -Force }
    }
}
