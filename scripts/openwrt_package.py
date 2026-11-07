#!/usr/bin/env python3
"""Stage one overlay, build it with official SDKs, verify native packages."""
from __future__ import annotations

import argparse
import gzip
import hashlib
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
import zlib

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "luci-app-owrt-remote"
RECIPE = ROOT / "package" / PACKAGE
DOWNLOADS = "https://downloads.openwrt.org/releases/"
EXECUTABLES = {
    "usr/sbin/owrt-remote", "www/cgi-bin/owrt-remote",
    "etc/init.d/owrt-remote", "etc/init.d/owrt-remote-logs",
    "etc/hotplug.d/iface/99-owrt-remote",
}


class Links(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.links = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.extend(value for key, value in attrs if key == "href" and value)


def get_text(url):
    request = urllib.request.Request(url, headers={"User-Agent": "OpenWrt-Remote-package-builder"})
    with urllib.request.urlopen(request, timeout=45) as response:
        data = response.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024:
        raise ValueError("oversized SDK metadata: " + url)
    return data.decode("utf-8")


def versions():
    text = (RECIPE / "Makefile").read_text(encoding="utf-8")
    version = re.search(r"^PKG_VERSION:=([0-9][0-9.]*)$", text, re.M).group(1)
    release = re.search(r"^PKG_RELEASE:=([0-9]+)$", text, re.M).group(1)
    return version, release


def latest_release(series, html):
    candidates = [link.rstrip("/") for link in Links(html).links
                  if re.fullmatch(re.escape(series) + r"\.[0-9]+/", link)]
    if not candidates:
        raise ValueError("no stable OpenWrt release for " + series)
    return max(candidates, key=lambda v: tuple(map(int, v.split("."))))


def sdk_name(html):
    names = sorted({link for link in Links(html).links if re.fullmatch(
        r"openwrt-sdk-[A-Za-z0-9_.+-]+Linux-x86_64\.tar\.(?:xz|zst)", link)})
    if len(names) != 1:
        raise ValueError("expected exactly one official Linux x86_64 SDK; found " + str(names))
    return names[0]


def checksum(text, filename):
    values = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1].lstrip("*") == filename:
            if re.fullmatch(r"[a-fA-F0-9]{64}", fields[0]):
                values.append(fields[0].lower())
    if len(set(values)) != 1:
        raise ValueError("missing or ambiguous SHA256 for " + filename)
    return values[0]


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(url, path, expected):
    path = Path(path)
    if path.is_file() and sha256(path) == expected:
        print("Verified download cache: " + path.name, flush=True)
        return
    partial = path.with_name(path.name + ".partial")
    for attempt in range(3):
        try:
            if partial.is_file() and sha256(partial) == expected:
                partial.replace(path)
                print("Recovered verified download: " + path.name, flush=True)
                return
            started = progress = time.monotonic()
            offset = partial.stat().st_size if partial.is_file() else 0
            if offset > 2 * 1024 ** 3:
                raise ValueError("SDK download exceeded 2 GiB")
            print(f"Downloading {path.name}; attempt {attempt + 1}/3; resume_bytes={offset}", flush=True)
            headers = {"User-Agent": "OpenWrt-Remote-package-builder"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=60) as response:
                response_headers = getattr(response, "headers", {})
                status = getattr(response, "status", 200)
                if status == 206 and offset:
                    span = re.fullmatch(r"bytes ([0-9]+)-([0-9]+)/([0-9]+)", response_headers.get("Content-Range", ""))
                    if not span or int(span[1]) != offset or not offset <= int(span[2]) < int(span[3]) <= 2 * 1024 ** 3:
                        raise ValueError("invalid SDK download Content-Range")
                    total = int(span[3])
                elif status == 200:
                    # A server may ignore Range; replace the partial file.
                    offset = 0
                    length = response_headers.get("Content-Length")
                    total = int(length) if length is not None else None
                    if total is not None and not 0 <= total <= 2 * 1024 ** 3:
                        raise ValueError("invalid SDK download Content-Length")
                else:
                    raise ValueError("unexpected SDK download HTTP status: " + str(status))
                digest = hashlib.sha256()
                if offset:
                    with partial.open("rb") as existing:
                        while data := existing.read(1024 * 1024):
                            digest.update(data)
                size = offset
                with partial.open("ab" if offset else "wb") as output:
                    while data := response.read(1024 * 1024):
                        size += len(data)
                        if size > 2 * 1024 ** 3 or (total is not None and size > total):
                            raise ValueError("SDK download exceeded declared size or 2 GiB")
                        output.write(data)
                        digest.update(data)
                        now = time.monotonic()
                        if now - progress >= 15:
                            print(f"Download progress: {path.name}: {size / 1024 ** 2:.1f} MiB; elapsed={now - started:.0f}s", flush=True)
                            progress = now
                if total is not None and size != total:
                    raise OSError("incomplete SDK download")
            if digest.hexdigest() != expected:
                raise ValueError("SDK SHA256 mismatch: " + url)
            partial.replace(path)
            print(f"Download SHA256 verified: {path.name}; elapsed={time.monotonic() - started:.1f}s", flush=True)
            return
        except Exception as error:
            # Preserve bounded partial bytes only for transport failures.
            # Invalid range, content, or digest always starts a fresh download.
            if isinstance(error, ValueError) or getattr(error, "code", None) == 416 or attempt == 2:
                partial.unlink(missing_ok=True)
            if attempt == 2:
                raise
            print(f"Download retry: {path.name}: {type(error).__name__}: {error}", flush=True)
            time.sleep(2)


def unpack_image(source, destination):
    # Some official OpenWrt .img.gz files have fwtool metadata appended after
    # the gzip member. The full download SHA256 is checked before this step.
    # Python gzip.open treats that signed/image metadata as a second header.
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    total = 0
    with Path(source).open("rb") as compressed, Path(destination).open("wb") as raw:
        while not decoder.eof:
            block = compressed.read(65536)
            if not block:
                raise ValueError("truncated OpenWrt disk image")
            data = decoder.decompress(block)
            total += len(data)
            if total > 2 * 1024 ** 3:
                raise ValueError("OpenWrt disk image exceeded 2 GiB")
            raw.write(data)


def payload():
    result = {}
    for base in (ROOT / "files", RECIPE / "root"):
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if path.is_symlink():
                raise ValueError("unexpected source symlink: " + str(path))
            name = path.relative_to(base).as_posix()
            if name in result:
                raise ValueError("duplicate package payload: " + name)
            data = path.read_bytes().replace(b"\r\n", b"\n")
            mode = 0o755 if name in EXECUTABLES else 0o644
            if name == "etc/config/owrtremote":
                mode = 0o600
            result[name] = (data, mode)
    return result


def package_hook(name):
    text = (RECIPE / "Makefile").read_text(encoding="utf-8")
    match = re.search(r"^define Package/" + PACKAGE + "/" + name + r"\n(.*?)^endef$", text, re.M | re.S)
    return match.group(1).replace("$$", "$") if match else "#!/bin/sh\nexit 0\n"


def dependencies():
    text = (RECIPE / "Makefile").read_text(encoding="utf-8")
    line = re.search(r"^\s+DEPENDS:=(.+)$", text, re.M).group(1)
    return [item.removeprefix("+") for item in line.split()]


def native_wrappers():
    common = '#!/bin/sh\n[ -s "${IPKG_INSTROOT}/lib/functions.sh" ] || exit 0\n. "${IPKG_INSTROOT}/lib/functions.sh"\n'
    return {
        "postinst": common + 'default_postinst "$0" "$@"\n',
        "prerm": common + 'default_prerm "$0" "$@"\n',
        "postinst-pkg": package_hook("postinst"),
        "postrm": package_hook("postrm"),
    }


def tar_bytes(files):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        directories = {parent.as_posix() for name in files for parent in PurePosixPath(name).parents
                       if parent.as_posix() != "."}
        for name in sorted(directories):
            info = tarfile.TarInfo("./" + name + "/")
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            info.uid = info.gid = 0
            archive.addfile(info)
        for name, (content, mode) in sorted(files.items()):
            info = tarfile.TarInfo("./" + name)
            info.size = len(content)
            info.mode = mode
            info.uid = info.gid = 0
            archive.addfile(info, io.BytesIO(content))
    return gzip.compress(data.getvalue(), mtime=0)


def pack_ipk(output, revision=None):
    """Portable noarch preview; release CI also builds with the official SDK."""
    version, release = versions()
    revision = str(revision or release)
    files = payload()
    control = (f"Package: {PACKAGE}\nVersion: {version}-{revision}\nArchitecture: all\n"
               f"Depends: {', '.join(dependencies())}\nSection: luci\nLicense: MIT\n"
               "Maintainer: kzolotarev95\nURL: https://github.com/kzolotarev95/luci-app-owrt-remote\n"
               f"Installed-Size: {sum(len(data) for data, _ in files.values())}\n"
               "Description: OpenWrt Remote Hub router agent and LuCI panel\n")
    metadata = {"control": (control.encode(), 0o644), "conffiles": (b"/etc/config/owrtremote\n", 0o644)}
    metadata.update({name: (script.encode(), 0o755) for name, script in native_wrappers().items()})
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(tar_bytes({"debian-binary": (b"2.0\n", 0o644),
                                 "control.tar.gz": (tar_bytes(metadata), 0o644),
                                 "data.tar.gz": (tar_bytes(files), 0o644)}))
    return output


def prepare_apk(folder, revision=None):
    """Prepare portable input for the genuine apk-tools v3 mkpkg command."""
    folder = Path(folder).resolve()
    staged = prepare(folder / "package") / "root"
    version, release = versions()
    meta = staged / "lib/apk/packages"
    meta.mkdir(parents=True, exist_ok=True)
    config = staged / "etc/config/owrtremote"
    (meta / f"{PACKAGE}.conffiles").write_text("/etc/config/owrtremote\n", newline="\n")
    (meta / f"{PACKAGE}.conffiles_static").write_text(f"/etc/config/owrtremote {sha256(config)}\n", newline="\n")
    (meta / f"{PACKAGE}.list").write_text("".join("/" + f.relative_to(staged).as_posix() + "\n" for f in sorted(staged.rglob("*")) if f.is_file()), newline="\n")
    common = '#!/bin/sh\n[ -s "${IPKG_INSTROOT}/lib/functions.sh" ] || exit 0\n. "${IPKG_INSTROOT}/lib/functions.sh"\n' + f'export pkgname="{PACKAGE}"\n'
    install = common + "default_postinst\n" + package_hook("postinst").removeprefix("#!/bin/sh\n")
    scripts = {"post-install": install,
               "post-upgrade": "#!/bin/sh\nexport PKG_UPGRADE=1\n" + install.removeprefix("#!/bin/sh\n"),
               "pre-deinstall": common + "default_prerm\n",
               "post-deinstall": package_hook("postrm")}
    args = ["mkpkg", "--info", "name:" + PACKAGE,
            "--info", f"version:{version}-r{revision or release}", "--info", "arch:noarch",
            "--info", "description:OpenWrt Remote Hub router agent and LuCI panel",
            "--info", "license:MIT", "--info", "depends:" + " ".join(dependencies()),
            "--info", "url:https://github.com/kzolotarev95/luci-app-owrt-remote",
            "--files", "package/root", "--output", PACKAGE + ".apk"]
    for name, script in scripts.items():
        (folder / name).write_text(script, newline="\n")
        args.extend(["--script", name + ":" + name])
    (folder / "build.sh").write_text('#!/bin/sh\nset -eu\ncd "$(dirname "$0")"\nexec "${APK_TOOL:-apk}" ' + shlex.join(args) + "\n", newline="\n")
    return args


def pack_apk(output, apk, revision=None):
    """Use the real apk-tools v3 packer, never rename an IPK or tar as APK."""
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="owrt-apk-") as folder:
        args = prepare_apk(folder, revision)
        subprocess.run([str(apk), *args], cwd=folder, check=True)
        shutil.copyfile(Path(folder) / (PACKAGE + ".apk"), output)
    return output


def prepare(output):
    output = Path(output).resolve()
    # Fail instead of recursively deleting a caller-supplied directory.
    if output.exists() and any(output.iterdir()):
        raise ValueError("package staging directory must be empty: " + str(output))
    output.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(RECIPE / "Makefile", output / "Makefile")
    for name, (data, mode) in payload().items():
        target = output / "root" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(mode)
    return output


def ar_members(data):
    if not data.startswith(b"!<arch>\n"):
        raise ValueError("invalid ar package")
    offset = 8
    result = {}
    while offset < len(data):
        header = data[offset:offset + 60]
        if len(header) != 60 or header[58:] != b"`\n":
            raise ValueError("invalid ar member")
        size = int(header[48:58].strip())
        name = header[:16].decode("ascii").strip().rstrip("/")
        result[name] = data[offset + 60:offset + 60 + size]
        if len(result[name]) != size:
            raise ValueError("truncated ar member")
        offset += 60 + size + size % 2
    return result


def ipk_contents(path):
    data = Path(path).read_bytes()
    if data.startswith(b"!<arch>\n"):
        members = ar_members(data)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
            members = {item.name.removeprefix("./"): archive.extractfile(item).read()
                       for item in archive if item.isfile()}
    if members.get("debian-binary") != b"2.0\n":
        raise ValueError("invalid IPK version marker")
    result = {}
    for kind in ("control", "data"):
        compressed = members.get(kind + ".tar.gz")
        if compressed is None:
            raise ValueError("IPK lacks " + kind)
        with tarfile.open(fileobj=io.BytesIO(compressed), mode="r:gz") as archive:
            result[kind] = {}
            for item in archive:
                if not item.isfile():
                    continue
                name = item.name.removeprefix("./")
                if ".." in PurePosixPath(name).parts or name.startswith("/"):
                    raise ValueError("unsafe package path: " + name)
                result[kind][name] = (archive.extractfile(item).read(), item.mode & 0o777, item.uid, item.gid)
    return result


def validate_payload(actual):
    for name, (expected, mode) in payload().items():
        if name not in actual:
            raise ValueError("package file missing: " + name)
        data, found_mode, uid, gid = actual[name]
        if data != expected or found_mode != mode or uid != 0 or gid != 0:
            raise ValueError("package content/mode/ownership mismatch: " + name)


def verify_package(path, apk=None, revision=None):
    path = Path(path).resolve()
    version, release = versions()
    release = str(revision or release)
    allowed_versions = {f"{version}-{release}", f"{version}-r{release}"}
    if path.suffix == ".ipk":
        content = ipk_contents(path)
        validate_payload(content["data"])
        control = content["control"]["control"][0].decode("utf-8")
        fields = dict(line.split(": ", 1) for line in control.splitlines() if ": " in line and not line.startswith(" "))
        if fields.get("Package") != PACKAGE or fields.get("Architecture") != "all":
            raise ValueError("invalid IPK package identity/architecture")
        if fields.get("Version") not in allowed_versions:
            raise ValueError("IPK version mismatch")
        if b"/etc/config/owrtremote" not in content["control"]["conffiles"][0]:
            raise ValueError("UCI config must be a conffile")
        for script in ("postinst", "prerm"):
            if b"/lib/functions.sh" not in content["control"][script][0]:
                raise ValueError("missing native OpenWrt lifecycle wrapper")
        declared_dependencies = {x.strip().split(" ", 1)[0] for x in fields.get("Depends", "").split(",")}
    else:
        if not apk:
            raise ValueError("APK validation requires the SDK host apk tool")
        manifest = json.loads(subprocess.check_output([str(apk), "adbdump", "--format", "json", str(path)], text=True))
        info = manifest["info"]
        if info["name"] != PACKAGE or info["arch"] != "noarch" or info["version"] not in allowed_versions:
            raise ValueError("invalid APK identity/version/architecture")
        owners = {}
        for directory in manifest.get("paths", []):
            for entry in directory.get("files", []):
                name = str(PurePosixPath(directory.get("name", "")) / entry["name"])
                acl = entry.get("acl", directory.get("acl", {}))
                if acl.get("user") != "root" or acl.get("group") != "root":
                    raise ValueError("APK payload must be owned by root: " + name)
                owners[name] = acl["mode"]
        with tempfile.TemporaryDirectory(prefix="owrt-apk-verify-") as folder:
            subprocess.run([str(apk), "extract", "--allow-untrusted", "--no-chown", "--destination", folder, str(path)], check=True)
            actual = {}
            for name in payload():
                file = Path(folder) / name
                if file.is_file():
                    stat = file.stat()
                    if owners.get(name) != stat.st_mode & 0o777:
                        raise ValueError("APK mode metadata mismatch: " + name)
                    actual[name] = (file.read_bytes(), stat.st_mode & 0o777, 0, 0)
            validate_payload(actual)
            conffiles = Path(folder) / "lib/apk/packages" / f"{PACKAGE}.conffiles"
            if "/etc/config/owrtremote" not in conffiles.read_text():
                raise ValueError("APK missing OpenWrt conffiles metadata")
        declared_dependencies = {re.split(r"[<=>~]", x, maxsplit=1)[0] for x in info.get("depends", [])}
    for dependency in dependencies():
        if dependency not in declared_dependencies:
            raise ValueError("package dependency missing: " + dependency)
    return {"file": path.name, "sha256": sha256(path), "bytes": path.stat().st_size, "verified": True}


def sign_apk(path, apk, private_key, public_key):
    """Use a persistent maintainer key; never generate a new trust root per build."""
    subprocess.run(["openssl", "pkey", "-in", str(private_key), "-pubout", "-out", str(public_key)], check=True)
    subprocess.run([str(apk), "adbsign", "--allow-untrusted", "--sign-key", str(private_key), str(path)], check=True)


def build(args):
    if sys.platform != "linux":
        raise ValueError("OpenWrt SDKs require Linux. Use a Linux machine or the prepared GitHub Actions workflow; no files on the router are changed.")
    if not re.fullmatch(r"(?:23\.05|24\.10|25\.12)\.[0-9]+", args.release):
        raise ValueError("unsupported OpenWrt release: " + args.release)
    for value in (args.target, args.subtarget, args.arch):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise ValueError("invalid target/architecture")
    if any(data.startswith(b"\x7fELF") for data, _ in payload().values()):
        raise ValueError("noarch script-only build cannot contain ELF binaries")
    series = ".".join(args.release.split(".")[:2])
    base = DOWNLOADS + f"{args.release}/targets/{args.target}/{args.subtarget}/"
    name = sdk_name(get_text(base))
    expected = checksum(get_text(base + "sha256sums"), name)
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="owrt-package-") as folder:
        work = Path(folder)
        cache = os.environ.get("OPENWRT_SDK_CACHE")
        archive = (Path(cache).expanduser() if cache else work) / name
        archive.parent.mkdir(parents=True, exist_ok=True)
        print("Downloading verified SDK: " + base + name, flush=True)
        download(base + name, archive, expected)
        subprocess.run(["tar", "-xf", str(archive), "-C", str(work)], check=True)
        directories = list(work.glob("openwrt-sdk-*/"))
        if len(directories) != 1:
            raise ValueError("SDK archive layout changed")
        sdk = directories[0]
        prepare(sdk / "package" / PACKAGE)
        # Use feed revisions from THIS SDK, not today's unrelated feed heads.
        subprocess.run(["./scripts/feeds", "update", "base", "packages", "luci"], cwd=sdk, check=True)
        subprocess.run(["./scripts/feeds", "install", "luci-base", "rpcd-mod-file", "uhttpd", "jsonfilter", "curl", "ca-bundle", "etherwake"], cwd=sdk, check=True)
        with (sdk / ".config").open("a") as config:
            config.write(f"\nCONFIG_PACKAGE_{PACKAGE}=m\nCONFIG_SIGNED_PACKAGES=n\n")
        subprocess.run(["make", "defconfig"], cwd=sdk, check=True)
        config = (sdk / ".config").read_text()
        is_apk = "CONFIG_USE_APK=y" in config
        if is_apk != (series == "25.12"):
            raise ValueError("SDK package format disagrees with the selected OpenWrt series")
        # This package only copies scripts. Keep runtime dependencies declared
        # and resolved by Kconfig, but do not rebuild curl/LuCI/crypto libraries
        # for each SDK. Real installation tests resolve those from OpenWrt.
        subprocess.run(["make", f"package/{PACKAGE}/compile", "NO_DEPS=1", "V=s", f"-j{args.jobs}"], cwd=sdk, check=True)
        extension = "apk" if is_apk else "ipk"
        packages = list((sdk / "bin/packages").rglob(f"{PACKAGE}*.{extension}"))
        if len(packages) != 1:
            raise ValueError("expected one native package, found " + str(packages))
        verified = verify_package(packages[0], sdk / "staging_dir/host/bin/apk")
        version, release = versions()
        dest = output / f"{PACKAGE}_{version}-r{release}_openwrt-{series}_{'noarch' if is_apk else 'all'}.{extension}"
        shutil.copyfile(packages[0], dest)
        signing_key = None
        if is_apk:
            if args.signing_key:
                signing_key = Path(args.signing_key).resolve()
            elif os.environ.get("OPENWRT_APK_SIGNING_KEY"):
                signing_key = work / "maintainer-signing-key.pem"
                signing_key.touch(mode=0o600)
                signing_key.write_text(os.environ["OPENWRT_APK_SIGNING_KEY"])
            if signing_key:
                sign_apk(dest, sdk / "staging_dir/host/bin/apk", signing_key, output / "owrt-remote-packages.pem")
                verified = verify_package(dest, sdk / "staging_dir/host/bin/apk")
            else:
                print("APK is unsigned: SSH needs --allow-untrusted; LuCI requires a trusted maintainer signing key.", flush=True)
        verified["file"] = dest.name
        report = {"package": PACKAGE, "version": version, "package_release": release,
                  "source_commit": os.environ.get("GITHUB_SHA"),
                  "openwrt": args.release, "series": series, "target": f"{args.target}/{args.subtarget}",
                  "architecture_test": args.arch, "sdk": {"url": base + name, "sha256": expected},
                  "package_asset": verified, "signed": bool(signing_key)}
        (output / f"report-{series}-{args.arch}.json").write_text(json.dumps(report, indent=2) + "\n")
        (output / "SHA256SUMS").write_text(f"{verified['sha256']}  {dest.name}\n")
        if args.upgrade_fixture:
            # A real native version upgrade is exercised in the VM. This
            # fixture stays in a subdirectory and is never a release asset.
            recipe = sdk / "package" / PACKAGE / "Makefile"
            next_release = str(int(release) + 1)
            recipe.write_text(recipe.read_text().replace("PKG_RELEASE:=" + release, "PKG_RELEASE:=" + next_release))
            subprocess.run(["make", f"package/{PACKAGE}/clean", "V=s"], cwd=sdk, check=True)
            subprocess.run(["make", f"package/{PACKAGE}/compile", "NO_DEPS=1", "V=s"], cwd=sdk, check=True)
            candidate = list((sdk / "bin/packages").rglob(f"{PACKAGE}*.{extension}"))
            if len(candidate) != 1:
                raise ValueError("upgrade fixture was not built")
            verify_package(candidate[0], sdk / "staging_dir/host/bin/apk", revision=next_release)
            fixture = output / "test-fixtures" / f"{PACKAGE}_{version}-r{next_release}_openwrt-{series}.{extension}"
            fixture.parent.mkdir(exist_ok=True)
            shutil.copyfile(candidate[0], fixture)
            if signing_key:
                sign_apk(fixture, sdk / "staging_dir/host/bin/apk", signing_key, output / "owrt-remote-packages.pem")
        print("OPENWRT_PACKAGE_BUILD_OK: " + str(dest), flush=True)


def matrix(baseline=False):
    config = json.loads((ROOT / "package/openwrt-targets.json").read_text())
    index = "" if baseline else get_text(DOWNLOADS)
    result = []
    for release in config["releases"]:
        version = release["baseline"] if baseline else latest_release(release["series"], index)
        for target in config["targets"]:
            result.append({**release, **target, "version": version})
    return {"include": result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    stage = sub.add_parser("prepare")
    stage.add_argument("--output", required=True)
    apk_stage = sub.add_parser("prepare-apk")
    apk_stage.add_argument("--output", required=True)
    targets = sub.add_parser("matrix")
    targets.add_argument("--baseline", action="store_true")
    check = sub.add_parser("verify")
    check.add_argument("package")
    check.add_argument("--apk")
    for fmt in ("ipk", "apk"):
        local = sub.add_parser("local-" + fmt)
        local.add_argument("--output", required=True)
        if fmt == "apk":
            local.add_argument("--apk", default="apk")
    sdk = sub.add_parser("build")
    for option in ("release", "target", "subtarget", "arch", "output"):
        sdk.add_argument("--" + option, required=True)
    sdk.add_argument("--jobs", type=int, default=2)
    sdk.add_argument("--upgrade-fixture", action="store_true")
    sdk.add_argument("--signing-key", help="persistent maintainer PEM private key for APK signing")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            print(prepare(args.output))
        elif args.command == "prepare-apk":
            prepare_apk(args.output)
            print(Path(args.output).resolve() / "build.sh")
        elif args.command == "matrix":
            print(json.dumps(matrix(args.baseline), separators=(",", ":")))
        elif args.command == "verify":
            print(json.dumps(verify_package(args.package, args.apk)))
        elif args.command == "local-ipk":
            print(pack_ipk(args.output))
        elif args.command == "local-apk":
            print(pack_apk(args.output, args.apk))
        else:
            build(args)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, "ERROR: " + str(error) + "\n")


if __name__ == "__main__":
    main()
