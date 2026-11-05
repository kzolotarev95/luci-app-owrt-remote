# Persistent client journal (v121 local fix)

The helper is `/usr/lib/owrt-remote/logs.sh`. It changes only Remote's private
journal, not logd, network configuration, policy routing or Xray configuration.

## Storage and limits

- Persistent: normally `/overlay/owrt-remote/logs`, only when `/overlay` is an
  actual writable JFFS2/UBIFS/ext4/f2fs/ext2/ext3/btrfs mount. A directly mounted
  persistent root uses `/etc/owrt-remote/logs`. Root OverlayFS and tmpfs are
  rejected. Existing ancestors/symlinks are resolved against `/proc/mounts`;
  the longest containing mount wins. An explicit `OWRT_REMOTE_LOG_DIR` is
  validated the same way.
- Runtime: `/tmp/owrt-remote-log-runtime`, which must be on tmpfs. Contains a
  buffer <=16 KiB, <=64 hashed dedup entries, a heartbeat fingerprint, PID locks,
  one export <=16 KiB, one HTTP body about 22 KiB, one viewer snapshot <=16 KiB
  plus a bounded filename list. No whole-journal shell variable or RAM archive.
  HTTP response files are limited by a sender-only RLIMIT_FSIZE (32 KiB in
  BusyBox ash); this does not alter heartbeat or Xray process limits.
- Flash data: <=2 MiB across <=256 immutable chunks, each <=16 KiB. This is an
  upper bound, not guaranteed capacity: many small chunks reach the file-count
  bound first. Metadata, filesystem allocation units and the one quarantined
  damaged chunk add small overhead beyond the raw-data bound.
- Rotation runs each minute. Acknowledged chunks expire after 24 hours or
  earlier to admit new chunks. Unacknowledged chunks do not expire. If the
  bounded queue/RAM buffer fills, **new** events are skipped; existing
  unacknowledged chunks remain intact. A warning and dropped-event count make
  that loss visible. A bounded queue cannot preserve infinitely many events
  during an infinitely long outage.
- Default batch flush: once per 60 seconds, at most 16 KiB of new data; explicit
  `flush force`, normal service stop and shutdown can flush sooner. There is no
  fsync per line. Idle polls do not rewrite persistent state. Metadata and
  filesystem write amplification are additional to the data rate; no universal
  Flash-lifetime guarantee is possible across devices/filesystems.
- Less than 2 MiB free space or write/mount failure stops Flash writes and
  switches this boot to a warning-marked RAM fallback: <=64 KiB data in <=8
  chunks +16 KiB buffer. The old Flash queue is left intact. Restore storage
  and reboot to resume that queue. No automatic mid-boot merging of two journal
  identities, which could acknowledge the wrong records.

## Crash and synchronization rules

Each chunk filename includes journal identity, start/end byte offsets and a validated SHA-256/MD5/POSIX cksum digest. Data is written to `chunk.new` on the persistent filesystem and renamed;
state is written to `state.new` on that filesystem and atomically replaced.
Batch sync (`sync -f`, or BusyBox `sync` fallback) completes durability. A small
RAM in-flight marker reconciles a killed flusher before another append. At
boot, checksums reject partial/corrupt chunks, incomplete temporary files are
removed, and a completed rename beyond the saved cursor is recovered. One
corrupt chunk can be retained as `damaged.log` for inspection. A reboot never
clears a valid journal.

Last-minute events still in RAM can be lost on sudden power removal. This is
the explicit buffering tradeoff, not a claim of per-line durable logging.

Only a successful authenticated Hub reply advances the ack. The recent ack is
checkpointed with the next periodic batch before pruning; if power is lost
first, records may replay with the same journal/offset. Existing Hub ingestion
suppresses these duplicates. Packets are <=16 KiB raw and transport them from
files without an entire packet in a shell variable. One `logs-sync` sender is
allowed; stale locks and stale payloads recover on the next attempt. Explicit
local/Hub clear intentionally discards both confirmed and unconfirmed data.
Log-sender curl temporaries use a private runtime subdirectory, reclaimed on
the next attempt after SIGKILL; heartbeat's temporary directory is untouched.

The local installer replaces the helper, log service, agent, CGI and LuCI view. It
does not restart the tunnel, heartbeat or network. With the previous collector
stopped, it streams the old bounded RAM journal into Flash chunks, preserving
its journal ID, total cursor, ack and clear token. Already delivered bytes are
not resent; undelivered records remain eligible for delivery. No FIFO, lock or
temporary request is migrated. A same-filesystem staging manifest allows a
power-interrupted migration to finish even after the old /tmp has disappeared.
Migration needs 6 MiB free Flash; if it cannot safely migrate legacy data, the
local installer fails and restores code instead of silently deleting history.
With no usable Flash, only a bounded legacy tail is retained in RAM with an
explicit loss warning, and the former large RAM files are removed. An ordinary
upgrade stops the previous collector before replacing files and invokes this
migration on log-service startup. Rollback restores code/service state and
leaves Flash logs intact for recovery; the old helper starts a fresh RAM log.

## Automated checks

```powershell
$env:OWRT_LOG_TEST_SHELL = "$PWD\.test-output\busybox\busybox64.exe"
python -m unittest discover -s tests -p test_persistent_logs.py -v
Remove-Item Env:OWRT_LOG_TEST_SHELL
python -m unittest discover -s tests -p test_global_logs.py -v
python -m unittest discover -s tests -p test_router_log_transport.py -v
```

Tests use real shell functions and disposable mount/filesystem fixtures. The
BusyBox Windows build exercises ash and its applets; it is **not** a claim of
testing OpenWrt 24.10.3's exact BusyBox/kernel/UBIFS or physical power loss.

## Device checks before release

Install locally, then inspect from Windows PowerShell:

```powershell
ssh root@192.168.2.1 'sh /usr/lib/owrt-remote/logs.sh status; df -h /overlay /tmp; free; ubus call service list; owrt-remote heartbeat-diagnostics'
```

Expect `persistent=1`, a persistent storage path and tmpfs runtime. Confirm the
mount explicitly using `mount` and verify `state` and `part.*` appear on Flash.
Append a marker and flush before a user-planned reboot:

```powershell
ssh root@192.168.2.1 'printf "persistent-check\n" | sh /usr/lib/owrt-remote/logs.sh append; sh /usr/lib/owrt-remote/logs.sh flush force; sh /usr/lib/owrt-remote/logs.sh status'
```

After reboot, `read` must still show the marker and the same journal ID. Test an
outage only on a controlled router with a separate management path; do not
change firewall/WAN settings on a remote production router merely for this
test. During outage, watch data/chunk/runtime bounds; after reconnect, watch
`ack_bytes` advance without duplicate Hub entries. Launch concurrent
`owrt-remote logs-sync` calls and inspect service/process counts. Confirm UI,
heartbeat, Xray and WAN direct remain functional. Actual sustained RAM,
Flash-write and reboot observations must be collected on the device.

## VPS archive and malformed-fragment recovery

The Hub retains its existing persistent SQLite archive, normally at
`/var/lib/owrt-remote/hub.db`, <=2 MiB of content per router. No new RAM archive
or whole-journal queue is introduced on the VPS. The live view still transfers
only the last 64 KiB. A storage diagnostic resolves DB symlinks and the longest
containing Linux mount, marking tmpfs/ramfs as nonpersistent. The dedicated
`deploy-persistent-logs-vps.ps1` verifies the running Hub's actual DB path and
writable disk before installing code. It preserves the database and all settings;
rollback replaces code only. It refuses installation over an unconfirmed/RAM DB
rather than automatically relocating a live database.

Some OpenWrt builds lack the optional `cksum` applet. The previous local helper
failed to check its exit status and could publish names ending in an empty digest.
Export skipped these files, but the empty status packet claimed the full cursor;
the previous Hub accepted it and falsely acknowledged missing data. Regression
checks now simulate missing cksum and failed checksum tools. New chunks use an
available SHA-256/MD5/POSIX checksum with validated output. An empty status packet
cannot advance the delivered cursor on either side.

On upgrade, a versioned recovery marker forces inspection even during the same
boot. Retained malformed fragments of the expected byte length are copied into a
same-filesystem recovery generation with real digests and a committed migration
manifest. Old falsely acknowledged offsets cannot suppress that generation.
Recovery requires enough free disk for the bounded copy and retains originals
until the new manifest is committed. This recovers retained bytes, not already
removed files, and cannot retroactively verify their former integrity. A warning
records that limitation. A new generation can repeat already delivered history
if mixed with affected fragments; normal retries remain deduplicated by offsets.

Run the real client spool-to-Hub ingestion regression as well as storage/deploy
checks:

```powershell
python -m unittest discover -s tests -p test_router_log_archive.py -v
python -m unittest discover -s tests -p test_persistent_logs_vps_deploy.py -v
```
