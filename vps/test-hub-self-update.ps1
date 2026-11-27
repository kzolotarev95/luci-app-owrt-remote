param(
    [Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$Vps,
    [ValidateSet('Prepare', 'Retest', 'Status', 'Restore')][string]$Action = 'Prepare',
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$UploadName = 'owrt-hub-update-test-' + [guid]::NewGuid().ToString('N')
$StageRoot = Join-Path ([System.IO.Path]::GetTempPath()) $UploadName
$Utf8 = [System.Text.UTF8Encoding]::new($false)
$RemoteTool = @'
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
import warnings

STAGE = Path(__file__).resolve().parent
TARGET = Path('/opt/owrt-remote/owrt-remote-hub.py')
ROOT = TARGET.parent / 'self-update-local-test'
HEALTH = 'http://127.0.0.1:8088/health?panel-update=1'

def digest(data):
    return hashlib.sha256(data).hexdigest()

def atomic(path, data, mode=0o600):
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)

def read_json(path):
    try:
        with path.open('rb') as stream:
            data = stream.read(16385)
        return json.loads(data) if len(data) <= 16384 else {}
    except (OSError, ValueError):
        return {}

def read_health():
    try:
        with urllib.request.urlopen(HEALTH, timeout=3) as response:
            data = json.loads(response.read(4096))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}

def healthy(revision='', attempts=20):
    for _ in range(attempts):
        data = read_health()
        if data.get('ok') and (not revision or data.get('revision') == revision):
            return True
        time.sleep(2)
    return False

def idle():
    result = subprocess.run(['systemctl', 'list-units', '--state=active', '--no-legend',
                             '--plain', 'owrt-remote-panel-update-*'],
                            capture_output=True, text=True, check=True, timeout=15)
    if result.stdout.strip():
        raise RuntimeError('A panel update is running. Wait for its result before preparing or restoring the test.')

def restart():
    subprocess.run(['systemctl', 'restart', 'owrt-remote'], check=True, timeout=45)

def cleanup_test():
    if ROOT.resolve() != TARGET.resolve().parent / 'self-update-local-test':
        raise RuntimeError('Unsafe test cleanup path.')
    job_directory = ROOT / 'job'
    if job_directory.exists():
        for name in ('candidate.py', 'candidate-mobile-api.py', 'previous.py',
                     'previous-mobile-api.py', 'previous-mobile-api.absent', 'job.json', 'worker.lock'):
            (job_directory / name).unlink(missing_ok=True)
        job_directory.rmdir()
    for name in ('candidate.py', 'before.py', 'test.json'):
        (ROOT / name).unlink(missing_ok=True)
    ROOT.rmdir()

def builds(source, candidate_path, token):
    anchor = '\nif __name__ == "__main__":\n    main()'
    if source.count(anchor) != 1 or '    def fetch_candidate(self):' not in source:
        raise RuntimeError('Local source does not contain the testable self-update feature.')
    # Only these disposable builds read a local file. The repository source,
    # RAW_REPO_BASE, router installers and regular HTTPS checks are unchanged.
    patch = '''
# LOCAL SELF-UPDATE TEST: remove with test-hub-self-update.ps1 -Action Restore.
def _owrt_local_test_fetch(self):
    self.source = "\\u041b\\u043e\\u043a\\u0430\\u043b\\u044c\\u043d\\u044b\\u0439 \\u0442\\u0435\\u0441\\u0442 \\u043d\\u0430 VPS (\\u0431\\u0435\\u0437 GitHub)"
    with Path(__CANDIDATE__).open('rb') as stream:
        return stream.read(HUB_UPDATE_LIMIT + 1)
HubUpdateManager.fetch_candidate = _owrt_local_test_fetch

_owrt_local_original_init = HubUpdateManager.__init__
def _owrt_local_test_init(self, *args, **kwargs):
    _owrt_local_original_init(self, *args, **kwargs)
    self.directory = Path(__JOB_DIRECTORY__)
HubUpdateManager.__init__ = _owrt_local_test_init

_owrt_local_original_worker = run_hub_update_worker
def run_hub_update_worker(job_id, directory=None, target=None):
    return _owrt_local_original_worker(job_id, directory or Path(__JOB_DIRECTORY__), target)
'''.replace('__CANDIDATE__', repr(str(candidate_path))).replace(
        '__JOB_DIRECTORY__', repr(str(candidate_path.parent / 'job')))
    source = source.replace(anchor, '\n' + patch + anchor)
    baseline = (source + '\n# LOCAL TEST BASELINE ' + token + '\n').encode('utf-8')
    candidate = (source + '\n# LOCAL TEST UPDATED ' + token + '\n').encode('utf-8')
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', SyntaxWarning)
        compile(baseline, 'local-test-baseline.py', 'exec')
        compile(candidate, 'local-test-candidate.py', 'exec')
    return baseline, candidate

def prepare():
    idle()
    if ROOT.exists():
        raise RuntimeError('A local test already exists. Use -Action Status or -Action Restore first.')
    original = TARGET.read_bytes()
    source = (STAGE / 'hub.py').read_text(encoding='utf-8')
    baseline, candidate = builds(source, ROOT / 'candidate.py', uuid.uuid4().hex)
    ROOT.mkdir(mode=0o700)
    changed = False
    try:
        atomic(ROOT / 'before.py', original, 0o700)
        atomic(ROOT / 'candidate.py', candidate, 0o700)
        metadata = {'baseline': digest(baseline), 'candidate': digest(candidate),
                    'before': digest(original), 'created_at': time.time()}
        atomic(ROOT / 'test.json', json.dumps(metadata).encode('ascii'))
        atomic(TARGET, baseline, 0o755)
        changed = True
        restart()
        if not healthy(metadata['baseline']):
            raise RuntimeError('Test baseline did not become healthy.')
    except Exception:
        if changed:
            atomic(TARGET, original, 0o755)
            restart()
            revision = digest(original) if b'HUB_LOADED_REVISION' in original else ''
            if not healthy(revision):
                raise RuntimeError('Previous file restored but service health is not confirmed. Backup: ' + str(ROOT / 'before.py'))
        cleanup_test()
        raise
    print('SELF_UPDATE_LOCAL_TEST_READY')
    print('Open the Hub, press Ctrl+F5, wait for the YELLOW update button, then confirm the update.')
    print('After success and the automatic reload, the update button must turn GREEN.')
    print('No GitHub/GitVerse push or installation link changes were made.')
    print('Backup: ' + str(ROOT / 'before.py'))

def status():
    metadata = read_json(ROOT / 'test.json')
    current = digest(TARGET.read_bytes())
    job = read_json(ROOT / 'job/job.json')
    service = subprocess.run(['systemctl', 'is-active', '--quiet', 'owrt-remote'], timeout=10).returncode == 0
    health = read_health()
    loaded = health.get('revision', '')
    build = ('baseline' if current == metadata.get('baseline') else
             'candidate' if current == metadata.get('candidate') else 'other')
    passed = (bool(metadata) and build == 'candidate' and service and health.get('ok')
              and loaded == metadata['candidate'] and job.get('status') == 'succeeded'
              and job.get('revision') == metadata['candidate'])
    print('SELF_UPDATE_LOCAL_TEST_NOT_PREPARED' if not metadata else
          'SELF_UPDATE_LOCAL_TEST_PASSED' if passed else 'SELF_UPDATE_LOCAL_TEST_WAITING')
    print(json.dumps({'service_active': service, 'job_status': job.get('status', 'not started'),
                      'test_prepared': bool(metadata), 'installed_build': build,
                      'running_installed_file': bool(health.get('ok') and loaded == current),
                      'installed_test_candidate': build == 'candidate',
                      'candidate_revision': metadata.get('candidate', ''),
                      'installed_revision': current, 'loaded_revision': loaded}, indent=2))
    if not metadata or build == 'other':
        print('The disposable local test build is not installed. Its local source is not active.')
    elif loaded != current:
        print('The running Hub does not match the installed test file. Check the owrt-remote service.')
    else:
        print('The local test build is running on this VPS. If the browser reports a GitHub error, verify that its domain points to this VPS.')

def restore():
    idle()
    metadata = read_json(ROOT / 'test.json')
    original = (ROOT / 'before.py').read_bytes()
    if not metadata or digest(original) != metadata.get('before'):
        raise RuntimeError('Test backup validation failed. Nothing changed.')
    atomic(TARGET, original, 0o755)
    restart()
    revision = digest(original) if b'HUB_LOADED_REVISION' in original else ''
    if not healthy(revision):
        raise RuntimeError('Previous file restored but service health is not confirmed. Backup was kept.')
    cleanup_test()
    print('SELF_UPDATE_LOCAL_TEST_RESTORED')
    print('The previous Hub file and its usual update source are restored. Press Ctrl+F5.')

def retest():
    if ROOT.exists():
        restore()
    prepare()

if __name__ == '__main__':
    try:
        {'Prepare': prepare, 'Retest': retest, 'Status': status, 'Restore': restore}[sys.argv[1]]()
    finally:
        if STAGE.parent == Path('/tmp') and re.fullmatch(r'owrt-hub-update-test-[0-9a-f]{32}', STAGE.name):
            shutil.rmtree(STAGE)
'@

try {
    [System.IO.Directory]::CreateDirectory($StageRoot) | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $StageRoot 'run.py'), $RemoteTool.Replace("`r`n", "`n") + "`n", $Utf8)
    if ($Action -in @('Prepare', 'Retest')) {
        $Content = [System.IO.File]::ReadAllText((Join-Path $RepoRoot 'vps\owrt-remote-hub.py')).Replace("`r`n", "`n")
        [System.IO.File]::WriteAllText((Join-Path $StageRoot 'hub.py'), $Content, $Utf8)
    }
    if ($PrepareOnly) {
        Write-Host "Prepared only: $StageRoot"
        return
    }
    & scp -O -r -- $StageRoot "${Vps}:/tmp/"
    if ($LASTEXITCODE -ne 0) { throw 'Upload failed. VPS was not changed.' }
    & ssh -T -o ConnectTimeout=10 $Vps "if [ -x /opt/owrt-remote/venv/bin/python ]; then /opt/owrt-remote/venv/bin/python /tmp/$UploadName/run.py $Action; else python3 /tmp/$UploadName/run.py $Action; fi"
    if ($LASTEXITCODE -ne 0) { throw "$Action failed. See the output above; success was not confirmed." }
}
finally {
    if (-not $PrepareOnly) {
        $Resolved = [System.IO.Path]::GetFullPath($StageRoot)
        $Expected = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetTempPath()) $UploadName))
        if ($Resolved -ne $Expected -or (Split-Path -Leaf $Resolved) -ne $UploadName) { throw 'Unsafe staging cleanup path.' }
        if (Test-Path -LiteralPath $Resolved) { Remove-Item -LiteralPath $Resolved -Recurse -Force }
    }
}
