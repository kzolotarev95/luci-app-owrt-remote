"""Install a pinned Windows Android toolchain inside the ignored local cache."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
from xml.sax.saxutils import escape
import zipfile


REPOSITORY = Path(__file__).resolve().parents[2]
ROOT = REPOSITORY / '.test-output' / 'android-build-tools'
DOWNLOADS = ROOT / 'downloads'
GOOGLE = 'https://dl.google.com/android/repository/'
PACKAGES = [
    {
        'name': 'jdk', 'version': '17.0.21.1+2', 'path': 'jdk-17',
        'url': 'https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.21.1%2B2/OpenJDK17U-jdk_x64_windows_hotspot_17.0.21.1_2.zip',
        'sha256': 'eafba0944f777abdc74d5aad16049c9b5a1275b6ce99e2939ba9f7f3e6f08c87',
    },
    {
        'name': 'gradle', 'version': '8.11.1', 'path': 'gradle-8.11.1',
        'url': 'https://services.gradle.org/distributions/gradle-8.11.1-bin.zip',
        'sha256': 'f397b287023acdba1e9f6fc5ea72d22dd63669d59ed4a289a29b1a76eee151c6',
    },
    {
        'name': 'cmdline-tools', 'version': '19.0', 'path': 'sdk/cmdline-tools/19.0',
        'url': GOOGLE + 'commandlinetools-win-13114758_latest.zip',
        'sha256': '98b565cb657b012dae6794cefc0f66ae1efb4690c699b78a614b4a6a3505b003',
        'sha1': '54a582f3bf73e04253602f2d1c80bd5868aac115',
    },
    {
        'name': 'platforms;android-35', 'version': '2', 'path': 'sdk/platforms/android-35',
        'url': GOOGLE + 'platform-35_r02.zip',
        'sha256': '0988cacad01b38a18a47bac14a0695f246bc76c1b06c0eeb8eb0dc825ab0c8e0',
        'sha1': '0bb560a90a7a2cbd0dd8348224d518b638fe7949',
    },
    {
        'name': 'build-tools', 'version': '35.0.0', 'path': 'sdk/build-tools/35.0.0',
        'url': GOOGLE + 'build-tools_r35_windows.zip',
        'sha256': '5753c679a1b90bcf6fbc9945a2ce39dfb9e74f1df0831a1e1866ae5b594326f0',
        'sha1': 'af059bb67cf7786f45ee0db85e2d24985df1b4b6',
    },
    {
        'name': 'platform-tools', 'version': '37.0.3', 'path': 'sdk/platform-tools',
        'url': GOOGLE + 'platform-tools_r37.0.3-win.zip',
        'sha256': '89d108360a3bf2924d3bea837239edaccc22fd6ff5befd3787c4348a9fe4f09d',
        'sha1': '7fea85bc18d0480bc81d3603952d666f49496e5a',
    },
]


def download(package):
    archive = DOWNLOADS / package['url'].rsplit('/', 1)[-1]
    if not archive.is_file():
        temporary = archive.with_suffix(archive.suffix + '.partial')
        print(f"Downloading {package['name']} {package['version']}", flush=True)
        subprocess.run([
            'curl.exe', '--silent', '--show-error', '--fail', '--location',
            '--retry', '3', '--max-time', '900', '--continue-at', '-',
            '--output', str(temporary), package['url'],
        ], check=True)
        temporary.replace(archive)
    for algorithm in ('sha256', 'sha1'):
        expected = package.get(algorithm)
        if expected is None:
            continue
        with archive.open('rb') as data:
            actual = hashlib.file_digest(data, algorithm).hexdigest()
        if actual != expected:
            raise RuntimeError(f'{archive.name}: {algorithm} mismatch; remove this cache file and retry')
    print(f"Verified {archive.name}", flush=True)
    return archive


def ensure_generic_package_metadata(package, destination):
    if package['name'] not in ('cmdline-tools', 'emulator'):
        return
    metadata = destination / 'package.xml'
    if metadata.exists():
        return
    package_id = 'cmdline-tools;' + package['version'] if package['name'] == 'cmdline-tools' else 'emulator'
    revision = ''.join(
        f'<{field}>{int(value)}</{field}>'
        for field, value in zip(('major', 'minor', 'micro'), package['version'].split('.'))
    )
    # Generic SDK packages need local metadata when installed directly from official ZIPs.
    metadata.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<ns2:repository xmlns:ns2="http://schemas.android.com/repository/android/common/02" '
        'xmlns:ns3="http://schemas.android.com/repository/android/generic/02" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f'<localPackage path="{escape(package_id)}" obsolete="false">'
        '<type-details xsi:type="ns3:genericDetailsType"/>'
        f'<revision>{revision}</revision>'
        f'<display-name>{escape(package["name"])}</display-name>'
        '</localPackage></ns2:repository>\n', encoding='utf8',
    )


def install(package, archive):
    destination = ROOT / package['path']
    marker = destination / '.owrt-toolchain-sha256'
    if marker.is_file() and marker.read_text(encoding='utf8').strip() == package['sha256']:
        ensure_generic_package_metadata(package, destination)
        return
    if destination.exists():
        # An existing SDK/JDK can be kept only if it was installed from this exact archive.
        raise RuntimeError(f'Unmanaged or incomplete tool directory: {destination}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(destination.name + '.extracting')
    if staging.exists():
        raise RuntimeError(f'Previous extraction is incomplete; inspect and remove {staging}')
    staging.mkdir()
    with zipfile.ZipFile(archive) as contents:
        prefixes = {name.split('/')[0] for name in contents.namelist() if name.strip('/')}
        if len(prefixes) != 1:
            raise RuntimeError(f'Unexpected archive layout: {archive.name}')
        prefix = prefixes.pop() + '/'
        for member in contents.infolist():
            relative = member.filename[len(prefix):]
            if not relative:
                continue
            target = staging / relative
            if not target.resolve().is_relative_to(staging.resolve()):
                raise RuntimeError(f'Invalid archive member: {member.filename}')
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with contents.open(member) as source, target.open('wb') as output:
                    shutil.copyfileobj(source, output)
    (staging / '.owrt-toolchain-sha256').write_text(package['sha256'] + '\n', encoding='utf8')
    staging.rename(destination)
    ensure_generic_package_metadata(package, destination)
    print(f"Installed {package['name']} at {destination}", flush=True)


def main():
    if sys.platform != 'win32' or sys.version_info < (3, 11):
        raise RuntimeError('This bootstrap requires Windows and Python 3.11 or later.')
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        archives = list(pool.map(download, PACKAGES))
    for package, archive in zip(PACKAGES, archives):
        install(package, archive)

    environment = os.environ.copy()
    environment['JAVA_HOME'] = str(ROOT / 'jdk-17')
    environment['ANDROID_HOME'] = str(ROOT / 'sdk')
    environment['ANDROID_SDK_ROOT'] = str(ROOT / 'sdk')
    environment['ANDROID_USER_HOME'] = str(ROOT / 'android-user-home')
    sdkmanager = ROOT / 'sdk/cmdline-tools/19.0/bin/sdkmanager.bat'
    # SDK licenses remain under the ignored SDK directory and are never copied to Git.
    subprocess.run(
        [str(sdkmanager), '--sdk_root=' + environment['ANDROID_HOME'], '--licenses'],
        input='y\n' * 100, text=True, env=environment, check=True,
    )
    manifest = [{
        'name': package['name'], 'version': package['version'],
        'path': str(ROOT / package['path']),
        'sha256': package['sha256'],
        **({'sha1': package['sha1']} if 'sha1' in package else {}),
    } for package in PACKAGES]
    (ROOT / 'tools-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf8')
    subprocess.run([str(ROOT / 'jdk-17/bin/java.exe'), '-version'], env=environment, check=True)
    subprocess.run([str(ROOT / 'gradle-8.11.1/bin/gradle.bat'), '--version'], env=environment, check=True)
    subprocess.run([str(ROOT / 'sdk/platform-tools/adb.exe'), 'version'], env=environment, check=True)


if __name__ == '__main__':
    main()
