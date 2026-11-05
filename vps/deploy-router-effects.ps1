param(
    [string]$Router = 'root@192.168.2.1',
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install'
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$UploadName = 'owrt-router-effects-' + [guid]::NewGuid().ToString('N')
$StageRoot = Join-Path ([System.IO.Path]::GetTempPath()) $UploadName
$Utf8 = [System.Text.UTF8Encoding]::new($false)

$RemoteScript = @'
#!/bin/sh
set -eu
stage=/tmp/__UPLOAD_NAME__
cgi=/www/cgi-bin/owrt-remote
effects=/www/luci-static/resources/owrt_remote_effects.js
backup=/root/owrt-remote-backup-before-router-effects-v121
changed=0

restore_file() {
    path="$1"
    name="$2"
    if [ -e "$backup/$name.absent" ]; then
        rm -f "$path"
    else
        test -s "$backup/$name"
        mkdir -p "$(dirname "$path")"
        cp -p "$backup/$name" "$path.new"
        mv -f "$path.new" "$path"
    fi
}

cleanup() {
    code=$?
    trap - EXIT
    if [ "$code" -ne 0 ] && [ "$changed" = 1 ] && [ -d "$backup" ]; then
        restore_file "$cgi" cgi || true
        restore_file "$effects" effects.js || true
        rm -rf /tmp/luci-indexcache /tmp/luci-modulecache 2>/dev/null || true
        /etc/init.d/uhttpd reload >/dev/null 2>&1 || true
    fi
    rm -rf "$stage"
    exit "$code"
}
trap cleanup EXIT INT TERM

if [ '__ACTION__' = Install ]; then
    test -s "$stage/cgi"
    test -s "$stage/effects.js"
    sh -n "$stage/cgi"
    grep -F -q 'id="routerEffectsPanel"' "$stage/cgi"
    grep -F -q 'data-season-mode="prism"' "$stage/cgi"
    grep -F -q 'data-hub-design="lite"' "$stage/cgi"
    grep -F -q 'function initHubAppearance()' "$stage/effects.js"
    grep -F -q 'function seasonFxHandlePointerMove(' "$stage/effects.js"
    grep -F -q 'const HUB_DESIGN_MODES' "$stage/effects.js"
    mkdir -p "$backup"
    if [ ! -e "$backup/cgi" ] && [ ! -e "$backup/cgi.absent" ]; then
        if [ -e "$cgi" ]; then cp -p "$cgi" "$backup/cgi"; else : >"$backup/cgi.absent"; fi
    fi
    if [ ! -e "$backup/effects.js" ] && [ ! -e "$backup/effects.js.absent" ]; then
        if [ -e "$effects" ]; then cp -p "$effects" "$backup/effects.js"; else : >"$backup/effects.js.absent"; fi
    fi
    cp "$stage/cgi" "$cgi.new"
    chmod 0755 "$cgi.new"
    mv -f "$cgi.new" "$cgi"
    cp "$stage/effects.js" "$effects.new"
    chmod 0644 "$effects.new"
    mv -f "$effects.new" "$effects"
else
    test -d "$backup"
    restore_file "$cgi" cgi
    restore_file "$effects" effects.js
    chmod 0755 "$cgi" 2>/dev/null || true
fi

changed=1
rm -rf /tmp/luci-indexcache /tmp/luci-modulecache 2>/dev/null || true
/etc/init.d/uhttpd reload >/dev/null 2>&1 || /etc/init.d/uhttpd restart >/dev/null 2>&1 || true
chmod 0755 "$cgi" 2>/dev/null || true
test -x "$cgi"
test -s "$effects"
if [ '__ACTION__' = Install ]; then
    grep -F -q 'id="routerEffectsPanel"' "$cgi"
    grep -F -q 'function initHubAppearance()' "$effects"
fi
printf '\nBackup: %s\nROUTER_EFFECTS_V121_%s_OK\n' "$backup" '__ACTION__'
'@

$RemoteScript = $RemoteScript.Replace('__UPLOAD_NAME__', $UploadName).Replace('__ACTION__', $Action)

try {
    [System.IO.Directory]::CreateDirectory($StageRoot) | Out-Null
    [System.IO.File]::WriteAllText(
        (Join-Path $StageRoot 'run.sh'),
        $RemoteScript.Replace("`r`n", "`n") + "`n",
        $Utf8
    )

    if ($Action -eq 'Install') {
        $cgi = [System.IO.File]::ReadAllText((Join-Path $RepoRoot 'files\www\cgi-bin\owrt-remote')).Replace("`r`n", "`n")
        $effects = [System.IO.File]::ReadAllText((Join-Path $RepoRoot 'files\www\luci-static\resources\owrt_remote_effects.js')).Replace("`r`n", "`n")
        [System.IO.File]::WriteAllText((Join-Path $StageRoot 'cgi'), $cgi, $Utf8)
        [System.IO.File]::WriteAllText((Join-Path $StageRoot 'effects.js'), $effects, $Utf8)
    }

    & scp -O -r -- $StageRoot "${Router}:/tmp/"
    if ($LASTEXITCODE -ne 0) { throw 'Upload failed. Router was not changed.' }
    & ssh -T -o ConnectTimeout=10 $Router "sh /tmp/$UploadName/run.sh"
    if ($LASTEXITCODE -ne 0) { throw "$Action failed. See the output above; success was not confirmed." }
    Write-Host "Router effects $Action finished successfully. Refresh the OpenWrt panel."
}
finally {
    $Resolved = [System.IO.Path]::GetFullPath($StageRoot)
    $Expected = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetTempPath()) $UploadName))
    if ($Resolved -ne $Expected -or (Split-Path -Leaf $Resolved) -ne $UploadName) {
        throw 'Unsafe staging cleanup path.'
    }
    if (Test-Path -LiteralPath $Resolved) {
        Remove-Item -LiteralPath $Resolved -Recurse -Force
    }
}
