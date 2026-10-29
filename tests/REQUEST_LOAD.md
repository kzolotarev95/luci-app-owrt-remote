# Notification long-poll and VPS log limits

The dashboard keeps one notification long-poll for the current browser profile.
Tabs share a short lease in `localStorage`; the server also admits only one
`/api/notifications/wait` request for a session and client id. A stale cursor
after clearing or pruning the notification file returns an empty response with
the current serial instead of replaying the whole list in a tight loop.

Successful long-poll and HTTP-terminal reads are omitted from the Hub stderr
request log. HTTP errors remain logged. The nginx policy excludes only 2xx
responses for these endpoints from `access.log`.

`install-vps.sh` and `deploy-request-load-fix.ps1` install the same bounded
logging policy: journald is capped at 256 MiB with a seven-day retention limit,
and nginx logs rotate daily with seven compressed archives and a 50 MiB trigger.
The deploy script keeps a backup at
`/root/owrt-remote-backup-before-request-load-v119` and supports Rollback.
