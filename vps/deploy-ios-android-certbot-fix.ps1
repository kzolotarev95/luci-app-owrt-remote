param(
    [ValidateNotNullOrEmpty()][string]$Vps = 'root@13.143.167.219',
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'deploy-native-mobile-api.ps1') -Vps $Vps -Action $Action `
    -BackupName 'owrt-remote-backup-before-ios-android-certbot-v125' -PrepareOnly:$PrepareOnly
if ($PrepareOnly) { return }

# A direct file deployment has no panel-update job to install the bundled
# maintenance component. Apply it explicitly, using its own reversible snapshot.
$Component = @'
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

backup = Path('/root/owrt-remote-backup-before-ios-android-certbot-v125/maintenance')
action = '__ACTION__'
source = Path('/opt/owrt-remote/owrt-remote-hub.py')
spec = importlib.util.spec_from_file_location('local_certbot_fix', source)
hub = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = hub
spec.loader.exec_module(hub)
# This verifies the embedded SHA256 and bounds before any script is executed.
payload, digest = hub.hub_log_maintenance_payload()
with tempfile.TemporaryDirectory(prefix='owrt-certbot-fix-') as folder:
    script = Path(folder) / 'log-maintenance.py'
    script.write_bytes(payload)
    if action == 'Rollback':
        if (backup / 'manifest.json').is_file():
            subprocess.run([sys.executable, str(script), 'rollback', '--backup', str(backup)], check=True)
    else:
        try:
            subprocess.run([sys.executable, str(script), 'install', '--backup', str(backup)], check=True)
            subprocess.run([sys.executable, '/opt/owrt-remote/log-maintenance.py', 'run'], check=True)
            if Path('/opt/owrt-remote/log-maintenance.py').read_bytes() != payload:
                raise RuntimeError('Installed maintenance differs from the verified bundle')
            print('CERTBOT_MAINTENANCE_VERIFIED ' + digest)
        except Exception:
            if (backup / 'manifest.json').is_file():
                subprocess.run([sys.executable, str(script), 'rollback', '--backup', str(backup)], check=True)
            raise
'@
$Component.Replace('__ACTION__', $Action) | & ssh $Vps 'python3 -'
if ($LASTEXITCODE -ne 0) {
    if ($Action -eq 'Install') {
        & (Join-Path $PSScriptRoot 'deploy-native-mobile-api.ps1') -Vps $Vps -Action Rollback `
            -BackupName 'owrt-remote-backup-before-ios-android-certbot-v125'
    }
    throw 'Maintenance component failed. See the output above.'
}
Write-Host "iOS, Android API and Certbot fix $Action completed."
