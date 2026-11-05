# WAN IPv4 DNS fallback (local v121 fix)

`files/usr/lib/owrt-remote/wan.sh` now explicitly queries
`nslookup -type=A -timeout=3 -retry=1 "$host" "$dns"`.
BusyBox's default lookup sends both A and AAAA queries; explicit `-type=A`
and the timeout/retry options are supported by its internal resolver. See
[BusyBox 1.36 nslookup source](https://github.com/mirror/busybox/blob/1_36_stable/networking/nslookup.c)
and [OpenWrt 24.10 BusyBox options](https://github.com/openwrt/openwrt/blob/openwrt-24.10/package/utils/busybox/config/networking/Config.in).

The parser preserves the nslookup exit status and accepts only a valid IPv4
answer for the queried name or its CNAME chain after a successful query.
Resolver headers, unrelated names, malformed IPv4, IPv6 answers and
198.18.0.0/15 fake-IP addresses cannot populate the IPv4 cache. Existing
explicit IPv6 literals keep their prior behavior.

The original cache policy is retained: a successful IPv4 is reused for
5 minutes; on a failed refresh the last successful address remains usable
until its original timestamp is 1 hour old. Failed refreshes do not extend
that timestamp. Corrupt timestamps, future timestamps and invalid cached
addresses are rejected. The cache remains under `/tmp/owrt-remote-wan`.

Failure diagnostics distinguish `reason=timeout`, `reason=no_a_record`,
`reason=parse_error`, `reason=resolver_error` and `reason=rejected_address`.
They include the domain, selected resolver and original exit status, without
raw DNS output. A successful A answer emits no `WAN DNS unavailable` warning.
WAN/DNS selection, Podkop, Xray and route/policy-rule implementations are
unchanged by this fix.

## Verification

```powershell
$env:PYTHONPATH = 'tests'
python -m unittest test_wan_dns test_wan_dns_deploy -v
# Optional second run with actual BusyBox ash and built-in awk:
$env:OWRT_TEST_BUSYBOX = 'C:\path\to\busybox64.exe'
python -m unittest test_wan_dns test_wan_dns_deploy -v
```

22 tests cover A-only arguments for both reported resolvers, BusyBox answer
formats, CNAME chains, DNS errors, validation, cache bounds, syntax, repeated
installation, exact rollback and recovery after installation failures.
The BusyBox ash run uses busybox-w32 1.38.0-FRP-6075 and its built-in applets,
with fixture nslookup replies. The executable is obtained from the
[maintainer](https://frippery.org/busybox/) and checked against its SHA256SUM.
This is not a live OpenWrt DNS/network test; OpenWrt 24.10 option compatibility
is additionally checked against the linked BusyBox sources.

## Local installation and rollback

`vps/deploy-wan-dns-fix.ps1` extracts only the DNS functions from the local
source and replaces the corresponding block in the router's existing
`wan.sh`. Everything before and after that block is preserved, including
differences between the published agent and the previous local heartbeat
transport fix. The agent itself is not replaced.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\k.zolotarev95\Documents\openwrt overlay\vps\deploy-wan-dns-fix.ps1" -Router "root@192.168.2.1" -Action Install
```

The installer saves the first backup at
`/root/owrt-remote-backup-before-wan-dns-v121/wan.sh`, checks shell syntax and
reloads only the supervised heartbeat instance so it reads the new helper.
It does not restart Xray or regenerate configuration/routing. Installation
verification/reload failures restore the file from that attempt.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\k.zolotarev95\Documents\openwrt overlay\vps\deploy-wan-dns-fix.ps1" -Router "root@192.168.2.1" -Action Rollback
```

Rollback restores the entire saved `wan.sh`; use it before installing later
changes to that file. No service is started if heartbeat was already stopped.
All uploads use UTF-8 without BOM and LF. `-PrepareOnly` creates local payloads
for inspection without contacting the router.
