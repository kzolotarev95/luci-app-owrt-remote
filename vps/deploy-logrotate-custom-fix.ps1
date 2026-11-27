param(
    [ValidateNotNullOrEmpty()][string]$Vps = 'root@13.143.167.219',
    [ValidateSet('Install', 'Rollback')][string]$Action = 'Install',
    [switch]$PrepareOnly
)
$ErrorActionPreference = 'Stop'
$BackupName = 'owrt-remote-backup-before-logrotate-custom-v125'
if ($Action -eq 'Install' -or $PrepareOnly) {
    & (Join-Path $PSScriptRoot 'deploy-native-mobile-api.ps1') -Vps $Vps -Action $Action `
        -BackupName $BackupName -PrepareOnly:$PrepareOnly
}
if ($PrepareOnly) { return }

$Component = @'
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.request

action = '__ACTION__'
backup = Path('/root/owrt-remote-backup-before-logrotate-custom-v125/maintenance')
target = Path('/opt/owrt-remote/owrt-remote-hub.py')
spec = importlib.util.spec_from_file_location('local_logrotate_repair', target)
hub = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = hub
spec.loader.exec_module(hub)
payload, digest = hub.hub_log_maintenance_payload()
directory = hub.STATE_DIR / 'panel-update'
job_path = directory / 'job.json'
saved_job = backup / 'job.before-repair.json'

with tempfile.TemporaryDirectory(prefix='owrt-logrotate-custom-') as folder:
    script = Path(folder) / 'log-maintenance.py'
    script.write_bytes(payload)
    if action == 'Rollback':
        if (backup / 'manifest.json').is_file():
            subprocess.run([sys.executable, str(script), 'rollback', '--backup', str(backup)], check=True)
        if saved_job.is_file():
            with hub.hub_update_lock(directory):
                if not job_path.exists():
                    hub.hub_update_write(job_path, saved_job.read_bytes())
    else:
        m_spec = importlib.util.spec_from_file_location('verified_local_maintenance', script)
        maintenance = importlib.util.module_from_spec(m_spec)
        m_spec.loader.exec_module(maintenance)
        custom = maintenance.LEGACY
        retained = custom.read_bytes() if custom.is_file() and not maintenance.legacy_policy() else None
        try:
            subprocess.run([sys.executable, str(script), 'install', '--backup', str(backup)], check=True)
            subprocess.run([sys.executable, str(maintenance.INSTALL_PATH), 'run', '--force'], check=True)
            if maintenance.INSTALL_PATH.read_bytes() != payload:
                raise RuntimeError('Installed maintenance differs from the verified bundle')
            if retained is not None and custom.read_bytes() != retained:
                raise RuntimeError('Custom logrotate configuration was changed')
            result = json.loads((maintenance.STATE / 'result.json').read_text())
            if result.get('ok') is not True:
                raise RuntimeError('Maintenance completion was not confirmed')
            subprocess.run(['systemctl', 'is-active', '--quiet', 'logrotate.timer'], check=True)
            client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with client.open('http://127.0.0.1:8088/health?panel-update=1', timeout=5) as response:
                health = json.load(response)
            if health.get('ok') is not True or health.get('revision') != hashlib.sha256(target.read_bytes()).hexdigest():
                raise RuntimeError('Running Hub revision was not confirmed')
            # Retain the original failed record after verified local recovery.
            # Another update may have started while maintenance was running.
            with hub.hub_update_lock(directory):
                if job_path.is_file() and not job_path.is_symlink():
                    original = job_path.read_bytes()
                    job = json.loads(original)
                    message = str(job.get('message', ''))
                    if job.get('status') == 'failed' and message.startswith((
                            '\u041e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u0438\u0435 \u043d\u0435 \u0432\u044b\u043f\u043e\u043b\u043d\u0435\u043d\u043e: \u041e\u0431\u0441\u043b\u0443\u0436\u0438\u0432\u0430\u043d\u0438\u0435 \u0436\u0443\u0440\u043d\u0430\u043b\u043e\u0432:',
                            '\u041e\u0431\u0441\u043b\u0443\u0436\u0438\u0432\u0430\u043d\u0438\u0435 \u0436\u0443\u0440\u043d\u0430\u043b\u043e\u0432 \u043d\u0435 \u0443\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d\u043e:')):
                        if not saved_job.exists():
                            hub.hub_update_write(saved_job, original)
                        import secrets
                        archive = directory / ('job.failed-maintenance-' + secrets.token_hex(12) + '.json')
                        job_path.replace(archive)
                        print('PREVIOUS_MAINTENANCE_FAILURE_ARCHIVED')
            print('CUSTOM_LOGROTATE_MAINTENANCE_VERIFIED ' + digest)
        except Exception:
            if (backup / 'manifest.json').is_file():
                subprocess.run([sys.executable, str(script), 'rollback', '--backup', str(backup)], check=True)
            raise
'@
$Component.Replace('__ACTION__', $Action) | & ssh $Vps 'python3 -'
if ($LASTEXITCODE -ne 0) {
    if ($Action -eq 'Install') {
        & (Join-Path $PSScriptRoot 'deploy-native-mobile-api.ps1') -Vps $Vps -Action Rollback -BackupName $BackupName
    }
    throw 'Logrotate recovery failed; see output above. Success was not confirmed.'
}
if ($Action -eq 'Rollback') {
    & (Join-Path $PSScriptRoot 'deploy-native-mobile-api.ps1') -Vps $Vps -Action Rollback -BackupName $BackupName
}
Write-Host "Custom logrotate fix $Action completed."
