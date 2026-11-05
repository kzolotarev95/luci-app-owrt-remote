# Journal collector liveness fix (local v121)

## What the reported evidence does and does not prove

The previous `log_new_fact_locked()` normalized syslog timestamps and stored the
first successful `Heartbeat: OK; ...` in `heartbeat-ok`. The identical success
was then suppressed **without an expiry**, even after hours. A 136-byte chunk,
`ack=total`, empty pending and live logread therefore do not prove a stalled
collector. They also do not establish a memory leak. A shell syntax check and
`persistent=1` prove neither event delivery nor process health.

The old supervisor checked only `kill -0` for its background writer and reader;
it ignored `log_append` failure. This could conceal write/lock errors while the
supervisor remained alive. These failure paths are corrected independently of
the deduplication issue. No claim is made that they caused this particular
AX3000T report without measurements from that device.

## Changes

- One supervising collect shell consumes its private FIFO with BusyBox ash
  timed reads. There is one logread child, no separate persistent writer shell
  and no five-second sleep child. At most one 4096-byte input fragment is kept
  in a shell variable. Oversized/fragmented input is diagnosed and discarded;
  it cannot grow a shell string without a bound.
- Each complete line is handed to `log_append` over an explicit short pipe,
  isolated from the live FIFO. Append failure is checked, recorded and causes
  a nonzero exit so procd can recover. Lock-operation traps run in subshells
  and cannot replace the collector's cleanup trap.
- Normal stop terminates/reaps logread, closes/removes the FIFO, flushes a final
  bounded batch and releases only its own collector lock. SIGKILL recovery
  recognizes a stale owner and terminates its recorded orphan reader only if
  PID **and kernel start time** match. Stale-lock reaping is serialized.
- procd's journal instance retries every 10 seconds without exhausting a
  five-crash budget. The archive instance and networking services are unchanged.
- Builds without fractional BusyBox sleep use whole-second waits. Lock and
  child cleanup deadlines account for that duration; unsupported `sleep 0.1`
  cannot produce a fast loop, repeated errors or a 100-second lock wait.
- A successful heartbeat is saved at most once per 300 seconds when unchanged,
  plus changes and recovery. Intervening duplicates update small RAM health
  metadata only. Flash flush is still once per 60 seconds, <=16 KiB per batch,
  with existing size/age/part limits. No per-heartbeat Flash write is introduced.
- Starting logread requests only a 64-message system-ring tail plus follow,
  to recover a short interruption. Existing fact dedup and bounded pending
  apply; this is not a guarantee of lossless collection during a long outage.
- `status` adds input and accepted-event times, counters, collector state,
  append/flush errors, the last completed batch/commit, allocated runtime bytes
  and RSS for the collector/reader when proc provides it. `receiving` means
  recently processed input; `idle-unverified` explicitly does not assert health.
- `diagnostics` is a private RAM-only <=4 KiB reason-code history (plus <=2 KiB
  compaction), with duplicate-rate limiting. It records FIFO creation/open,
  fragmented input, lock timeout, append, flush and reader exit failures without
  storing event text, keys or command lines. It is not sent back through logger,
  avoiding diagnostic feedback loops.
- Existing validated persistent storage, immutable checksummed chunks, ack
  protocol and limited RAM fallback remain. Runtime health is lost on reboot;
  retained journal/ack data are not cleared by a collector restart.

## Local install and rollback (Windows PowerShell 5.1/11)

The dedicated installer replaces **only** `logs.sh` and `owrt-remote-logs`.
It backs up code/service state, preserves journal chunks and UCI configuration,
restarts only the log service, verifies a real logger marker through the FIFO
and performs one explicit deployment flush. Failure restores the previous code.
It does not remove `xray.zip`, update Xray, restart the tunnel, modify Podkop,
WAN/DNS or policy routing. VPS files and hosting URLs are untouched.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\k.zolotarev95\Documents\openwrt overlay\vps\deploy-log-collector-fix.ps1" -Router "root@192.168.2.1" -Action Install
```

Retained backup: `/root/owrt-remote-backup-before-log-collector-v121`.
Rollback leaves journal data intact:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\k.zolotarev95\Documents\openwrt overlay\vps\deploy-log-collector-fix.ps1" -Router "root@192.168.2.1" -Action Rollback
```

After installation compare two samples a minute apart. `last_input_epoch` and
`suppressed_events` should advance on stable heartbeat. An unchanged heartbeat
becomes a new retained sample after five minutes. A distinct marker is visible
immediately in read, then committed by the next normal flush (up to ~65 seconds).

```powershell
ssh root@192.168.2.1 'logger -t owrt-remote collector-user-check; sleep 3; sh /usr/lib/owrt-remote/logs.sh read | tail -n 12; sh /usr/lib/owrt-remote/logs.sh status; sh /usr/lib/owrt-remote/logs.sh diagnostics; free'
```

Measure memory using `MemAvailable`, tmpfs allocation and process RSS over time;
`free` memory alone includes reclaimable kernel/filesystem cache. Do not attribute
all process RSS or low available RAM to the log spool without these measurements.
No router or VPS is modified by automated tests.

## Automated coverage and limits

- Real FIFO tests using MSYS shell on Windows: live heartbeat sampling, distinct
  events through pending/Flash, duplicate collector rejection, reader death,
  append failure, owner SIGKILL and successor recovery, TERM cleanup/final flush.
- Real BusyBox ash/applets: spool bounds, atomic/checksum/ack recovery, sampling,
  limited diagnostics, fallback and cleanup paths; Windows BusyBox lacks FIFOs.
- Sandboxed installer: install, first-backup preservation, rollback, bad syntax,
  failed verification restoration, journal retention.
- Existing redaction/CGI/Hub transport regressions and shell syntax checks.

This is not a claim of physical AX3000T / OpenWrt 24.10.3 UBIFS testing, hardware
power-cut testing or a measured RAM reduction in MiB. The actual device still
needs event/flush/reboot and memory observations before release.

Implementation references checked:
[OpenWrt 24.10 ubox version](https://github.com/openwrt/openwrt/blob/openwrt-24.10/package/system/ubox/Makefile),
[its logread](https://github.com/openwrt/ubox/blob/85f1053019caf4cd333795760950235ee4529ba7/log/logread.c),
[BusyBox ash timed read](https://github.com/mirror/busybox/blob/1_36_stable/shell/ash.c),
[procd respawn](https://github.com/openwrt/procd/blob/master/service/instance.c).
