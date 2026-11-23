# Local Android WebView fixture

This disposable Python standard-library server imitates Hub browser behavior. It
never imports the real Hub, opens a shell, contacts a router or VPS, or reads real
credentials. Sessions and terminal echoes exist only in memory. Uploaded bytes are
counted and discarded. Downloaded archives contain only `FIXTURE-ONLY.txt`.

From the repository root in Windows PowerShell:

```powershell
python .\android\test-fixtures\test_server.py
python .\android\test-fixtures\server.py --port 8765
```

The server binds **127.0.0.1 only**. For an emulator or USB device, use the Android
SDK's `adb reverse` so it stays local:

```powershell
adb reverse tcp:8765 tcp:8765
```

Connect the app to `http://127.0.0.1:8765`. The emulator can alternatively reach
the host with `http://10.0.2.2:8765`; a reverse mapping also works with physical
devices. To undo the mapping, run `adb reverse --remove tcp:8765`.

Fake login: `fixture-user` / `fixture-only-password`, CAPTCHA `2468`, 2FA `123456`.
These values are public fixture inputs. The login requires a CSRF token and the
pre-login HttpOnly SameSite=Lax cookie. A successful login retains that session;
child views, downloads, multipart uploads, and the terminal require its cookie.

## Endpoints

| Endpoint | Purpose |
| --- | --- |
| `/health` | Public JSON readiness check |
| `/login`, `/2fa` | HTML form with password, CAPTCHA, 2FA, hidden CSRF |
| `/` | Authenticated dashboard with stable element IDs |
| `/child?case=anchor` | Authenticated child window and nested-window links |
| `/oauth/popup` | Same-origin opener/postMessage/window.close callback |
| `/history/1`, `/history/2` | Back/forward navigation inside each view |
| `/api/session`, `/api/sessions` | Authenticated JSON probe; 401 when session expires |
| `/api/check-csrf` | POST requires matching session CSRF |
| `/download`, `/download/redirect` | Authenticated attachment and redirect |
| `/upload` | Authenticated multipart upload; returns names, types and sizes |
| `/terminal` | Mobile defaults to HTTP; buttons also exercise WebSocket |
| `/api/ssh/session`, `/api/ssh/read` | HTTP terminal start/read |
| `/api/ssh/write`, `/api/ssh/resize`, `/api/ssh/close` | POST terminal operations |
| `/ws/echo` | Authenticated WebSocket echo; no command execution |
| `/logout`, `/expire` | Revoke session, clear cookie, redirect to login |
| `/error` | Main-frame HTTP 503 |
| `/timeout?seconds=45` | Stall a navigation (maximum 90 seconds) |
| `/disconnect` | Close TCP connection before sending a response |
| `/missing.js`, `/missing-image.png` | Intentionally broken subresources |
| `/manifest.webmanifest`, `/sw.js` | Optional SW registration with no caching |

The server also accepts `--cert local-cert.pem --key local-key.pem` for a local
HTTPS test. Supply disposable local test material; the fixture does not generate
or trust certificates. An untrusted certificate must display an error and stop
the load. The Android wrapper must not bypass SSL errors.

## Essential emulator smoke

Use an isolated emulator/app data. The app must connect to a fixture URL entered
in the same native form used for a real Hub, not to a hardcoded test-only origin.
Instrumented tests can obtain the top WebView on the UI thread and use
`evaluateJavascript`; wait for DOM markers rather than fixed sleeps.

1. Invalid URL/port remains on native form. Valid local HTTP address reaches
   `Fixture login`; an optional username fills `#hubUsername`. With no native
   password supplied, `#hubPassword` remains blank. If the native form offers an
   optional password, check that it fills this known Hub form once, without
   submitting it; CAPTCHA and 2FA remain blank. Check that the password is absent
   from preferences and cleared after leaving the Activity. Submit wrong CAPTCHA and wrong 2FA separately;
   both must fail. Submit the four fake values and assert `#dashboardTitle` plus
   `#sessionStatus == "Cookie session OK"`. `document.cookie` must not expose the
   HttpOnly cookie. Tap `#csrfCheck`, assert `CSRF OK`.
2. Tap `#anchorBlank`, `#adminBlank`, `#scriptBlank`, and `#emptyBlank`, one at a
   time. Each must create one child, preserve the dashboard, and show
   `Child cookie OK`. `noopener` children must show `No opener`. Child Back
   returns through its own history before closing it; Back on an empty child
   returns to the original dashboard. Check `#nestedBlank` too.
3. Open `#oauthPopup`, tap `#oauthComplete`, and assert the popup closes and the
   dashboard says `OAuth callback OK`. This checks that a WebView child preserves
   a real opener when the page requested one. Repeat with `#asyncOauthPopup`:
   Hub account linking opens its window after an awaited request, so its popup
   may have `isUserGesture == false` in Android's `onCreateWindow` callback.
4. Tap download and redirect download, choose a destination, and inspect the
   resulting `.tar.gz` contents. A saved login page is a failure: the native
   download must send CookieManager cookies and the current WebView user-agent.
   Open the file picker for images, cancel, reopen, select two disposable images;
   select a disposable backup file, then upload. Verify the returned sizes/names.
   Run this from a child view too. Cancelled pickers must resolve their callback.
5. Open the terminal child; an Android UA must select `HTTP terminal`. Send
   `fixture hello`, wait for echo, then switch to WebSocket and verify echo again.
   Rotate and background/foreground; resizing and IME must not cover the input.
   Returning from the terminal must not reload the main dashboard or lose cookies.
6. Force-stop and reopen the app after authenticated navigation: persistent
   cookies should restore the session. Logout and reopen: login must remain
   logged out. Repeat with `/expire`. Do not recreate a session with native
   password injection or suppress the CAPTCHA/2FA form.
7. `/error`, `/disconnect`, a refused localhost port, and `/timeout` must offer
   a usable retry/change-address path. A missing image/script must leave the
   dashboard usable. Test an untrusted local HTTPS certificate separately; it
   must fail closed. The app must not silently downgrade an HTTPS URL to HTTP.

The fixture cannot validate real Google CAPTCHA, provider OAuth policy, WebAuthn,
web push delivery, or the production Hub's terminal CDN. Those require separate
explicit environment tests. The Android WebView process uses a separate cookie
store from Chrome/Custom Tabs, so opening an OAuth flow outside the WebView alone
does not transfer its resulting Hub session back into the app.
