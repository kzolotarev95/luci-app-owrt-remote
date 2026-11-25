# Native Android fixture

Run the separate native API fixture; the original `server.py` remains available
for the older WebView wrapper tests.

```powershell
python .\android\test-fixtures\test_native_server.py
python .\android\test-fixtures\native_server.py --port 8766
```

The server binds only to `127.0.0.1`. Use `http://10.0.2.2:8766` in the Android
emulator's debug app, or forward port 8766 with `adb reverse tcp:8766 tcp:8766`
and connect to `http://127.0.0.1:8766`. Release builds require HTTPS. Optional
`--cert` and `--key` provide local TLS for certificate rejection checks.

All values are disposable fixture values:

| Field | Value |
| --- | --- |
| Owner username | `fixture-user` |
| Limited viewer username | `fixture-viewer` |
| Password for both users | `fixture-only-password` |
| CAPTCHA | `2468` |
| Owner OTP | `123456` (the limited viewer does not use owner 2FA) |
| Main router | `main` / `Домашний роутер`, online |
| Other router | `offline` / `Дача`, offline |
| Groups | `home` / `Дом`, `remote` / `Загородные` |
| Fake WoL MAC | `02:00:00:00:00:01` |

The fixture implements `/api/mobile/v1/capabilities`, `/login/challenge`,
`/login`, `/session`, and `/logout` under that prefix. Login sets the actual
`owrt_remote_session` HttpOnly cookie name. Session returns account permissions,
mobile CSRF, and the permission-filtered operation CSRF maps. The viewer sees
only the main router with read access and receives 403 on management writes.

Existing Hub-shaped endpoints include routers and availability, router groups,
VPS CPU/memory/disk resources, sessions, notes, WoL device lists, and
`/api/router/main/xray-control`. Xray writes require `X-Xray-Control-CSRF` from
the session response and a 32-character lowercase hexadecimal `request_id`.
The POST returns `queued`; the first GET returns `running`; the second GET
returns `succeeded`. Retrying a request ID does not enqueue another command.
Nothing launches a shell, connects to a router/VPS, or sends a WoL packet.

`GET /api/auth/meta` returns the public owner 2FA metadata used to show the OTP
field only for the matching username. Native notes use
`/api/mobile/v1/router/main/notes`; POST `{text: "..."}` appends an entry with
`X-Mobile-CSRF`, preserving prior entries and their attachments.

The same native session cookie opens minimal disposable browser tool pages,
including `/ssh/main/` and `/access/main/`. `GET /fixture/expire` revokes the
current cookie for an expired-session test. Logout requires `X-Mobile-CSRF`.
Authenticated `GET /api/fixture/logout-fail-next` makes the next valid logout
return 503 without revoking the server cookie, for testing the application's
local logout and its unconfirmed-server message.
