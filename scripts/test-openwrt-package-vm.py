#!/usr/bin/env python3
"""Exercise package install/upgrade/removal in a disposable local OpenWrt VM."""
import argparse
import contextlib
import functools
import http.server
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import tempfile
import threading
import time

import openwrt_package as package


def free_port():
    with socket.socket() as stream:
        stream.bind(("127.0.0.1", 0))
        return stream.getsockname()[1]


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def stop_vm(proc):
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True)
    parser.add_argument("--package-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--qemu", default="qemu-system-x86_64")
    args = parser.parse_args()
    if not re.fullmatch(r"(?:23\.05|24\.10|25\.12)\.[0-9]+", args.release):
        parser.error("unsupported test release")
    source = Path(args.package_dir).resolve()
    candidates = list(source.glob("*.apk")) + list(source.glob("*.ipk"))
    fixtures = list((source / "test-fixtures").glob("*.apk")) + list((source / "test-fixtures").glob("*.ipk"))
    if len(candidates) != 1 or len(fixtures) != 1:
        parser.error("one main package and one real upgrade fixture are required")
    candidate, fixture = candidates[0], fixtures[0]
    apk = candidate.suffix == ".apk"
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    port = free_port()
    ssh = ["ssh", "-p", str(port), "-o", "BatchMode=yes", "-o", "ConnectTimeout=3",
           "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=" + str(output / "known_hosts"),
           "root@127.0.0.1"]
    handler = functools.partial(QuietHandler, directory=str(source))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log = (output / "package-install.log").open("w", encoding="utf-8")

    def remote(command, timeout=180, check=True):
        # The only target is the newly created loopback VM. No user router or
        # configured SSH profile can be selected by this test program.
        result = subprocess.run(ssh + [command], capture_output=True, timeout=timeout)
        text = (result.stdout + result.stderr).decode("utf-8", "replace")
        log.write(text + "\n")
        log.flush()
        if check and result.returncode:
            raise RuntimeError(f"VM command failed (rc={result.returncode}): {command}\n{text}")
        return result

    proc = None
    try:
        with contextlib.ExitStack() as stack:
            directory = stack.enter_context(tempfile.TemporaryDirectory(prefix="owrt-native-vm-"))
            work = Path(directory)
            base = package.DOWNLOADS + args.release + "/targets/x86/64/"
            name = "openwrt-" + args.release + "-x86-64-generic-ext4-combined.img.gz"
            digest = package.checksum(package.get_text(base + "sha256sums"), name)
            image = work / name
            print("Downloading verified OpenWrt VM " + args.release, flush=True)
            package.download(base + name, image, digest)
            disk = work / "router.img"
            package.unpack_image(image, disk)
            flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
            qemu_log = stack.enter_context((output / "qemu.log").open("wb"))
            proc = subprocess.Popen([args.qemu, "-display", "none", "-monitor", "none", "-serial", "file:" + str(output / "console.log"),
                                     "-accel", "tcg", "-m", "256", "-smp", "1", "-no-reboot",
                                     "-drive", "file=" + str(disk) + ",format=raw",
                                     "-device", "virtio-net-pci,netdev=lan", "-netdev",
                                     "user,id=lan,net=192.168.1.0/24,host=192.168.1.2,dhcpstart=192.168.1.100,hostfwd=tcp:127.0.0.1:" + str(port) + "-192.168.1.1:22"],
                                    stdin=subprocess.DEVNULL, stdout=qemu_log, stderr=qemu_log, **flags)
            # Stop QEMU before closing its log and deleting the disk, including
            # on Windows where an open image cannot be unlinked.
            stack.callback(stop_vm, proc)
            print("Waiting for the local VM SSH agent", flush=True)
            for attempt in range(40):
                if proc.poll() is not None:
                    raise RuntimeError("QEMU exited before OpenWrt booted; see qemu.log")
                try:
                    if remote("true", timeout=8, check=False).returncode == 0:
                        break
                except subprocess.TimeoutExpired:
                    pass
                time.sleep(2)
            else:
                raise RuntimeError("OpenWrt VM did not boot within the test deadline")
            remote("set -e; uci set network.lan.gateway=192.168.1.2; uci set network.lan.dns=192.168.1.3; uci commit network; /etc/init.d/network reload; sleep 2")
            remote("apk update" if apk else "opkg update", timeout=300)
            origin = "http://192.168.1.2:" + str(server.server_port) + "/"
            remote("set -e; wget -qO /tmp/remote-package" + candidate.suffix + " " + shlex.quote(origin + candidate.name) +
                   "; wget -qO /tmp/remote-upgrade" + candidate.suffix + " " + shlex.quote(origin + "test-fixtures/" + fixture.name))
            public_key = source / "owrt-remote-packages.pem"
            if apk and public_key.is_file():
                remote("wget -qO /etc/apk/keys/owrt-remote-packages.pem " + shlex.quote(origin + public_key.name))
            install = ("apk add" if public_key.is_file() else "apk add --allow-untrusted") if apk else "opkg install"
            remove = "apk del" if apk else "opkg remove"
            print("Testing first install and dependency resolution", flush=True)
            remote(install + " /tmp/remote-package" + candidate.suffix, timeout=300)
            remote("set -e; test -s /etc/owrt-remote/web.key; test $(wc -c </etc/owrt-remote/web.key) -eq 65; "
                   "test -x /usr/sbin/owrt-remote; test -x /www/cgi-bin/owrt-remote; "
                   "test -f /usr/share/luci/menu.d/luci-app-owrt-remote.json; "
                   "test $(uci -q get owrtremote.main.enabled) = 0; "
                   "/etc/init.d/owrt-remote-logs running; "
                   "for script in /usr/sbin/owrt-remote /usr/lib/owrt-remote/*.sh /etc/init.d/owrt-remote* /www/cgi-bin/owrt-remote; do /bin/ash -n $script; done; "
                   "curl -fsS \"http://127.0.0.1/cgi-bin/owrt-remote?key=$(cat /etc/owrt-remote/web.key)\" | grep -q 'OpenWrt Remote Hub'")
            remote("set -e; uci set owrtremote.main.router_name='Package upgrade test'; uci set owrtremote.main.hub_token='local-test-only'; uci commit owrtremote; "
                   "mkdir -p /etc/xray /tmp/owrt-xray; printf 'preserve JSON\\n' >/etc/xray/owrt-remote-client.json; "
                   "printf 'preserve Xray binary\\n' >/tmp/owrt-xray/xray; chmod 755 /tmp/owrt-xray/xray; "
                   "sha256sum /etc/config/owrtremote /etc/owrt-remote/web.key /etc/xray/owrt-remote-client.json /tmp/owrt-xray/xray >/tmp/remote-before.sha256")
            print("Testing a genuine package version upgrade", flush=True)
            remote(install + " /tmp/remote-upgrade" + candidate.suffix, timeout=300)
            remote("set -e; sha256sum -c /tmp/remote-before.sha256; /etc/init.d/owrt-remote-logs running")
            print("Testing remove, preserved state, and migration from unowned overlay files", flush=True)
            remote(remove + " " + package.PACKAGE, timeout=180)
            remote("set -e; test ! -e /usr/sbin/owrt-remote; test ! -e /www/cgi-bin/owrt-remote; test ! -e /etc/init.d/owrt-remote; sha256sum -c /tmp/remote-before.sha256")
            # Configuration left by install.sh is unowned by a package manager.
            # A fresh package install must preserve it just like an upgrade.
            remote(install + " /tmp/remote-package" + candidate.suffix, timeout=300)
            remote("set -e; sha256sum -c /tmp/remote-before.sha256; test -x /usr/sbin/owrt-remote")
            remote(remove + " " + package.PACKAGE, timeout=180)
            (output / "result.txt").write_text("OPENWRT_NATIVE_PACKAGE_TEST_OK " + args.release + "\n")
            print("OPENWRT_NATIVE_PACKAGE_TEST_OK " + args.release, flush=True)
    finally:
        stop_vm(proc)
        server.shutdown()
        server.server_close()
        log.close()


if __name__ == "__main__":
    main()
