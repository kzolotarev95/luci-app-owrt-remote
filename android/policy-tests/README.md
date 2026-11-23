# Hub URL policy tests

These tests use only the JDK. They do not start Android, contact the Hub, or use credentials.

From the repository root in Windows PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\android\policy-tests\run.ps1
```

If Java is not on `PATH`, pass the JDK directory:

```powershell
.\android\policy-tests\run.ps1 -JavaHome 'C:\path\to\jdk'
```

The runner compiles into a temporary directory and removes only that directory afterward.

On other platforms, a plain Java invocation works as well (run from the repository root):

```sh
mkdir -p /tmp/owrt-policy-classes
javac -encoding UTF-8 -source 8 -target 8 -d /tmp/owrt-policy-classes \
  android/app/src/main/java/ru/owrthub/app/HubUrlPolicy.java \
  android/policy-tests/HubUrlPolicyTest.java
java -cp /tmp/owrt-policy-classes ru.owrthub.app.HubUrlPolicyTest
```

## WebView integration contract

- Normalize the address before saving it with `normalizeHub(host, port, BuildConfig.DEBUG)`. Production accepts HTTPS only. Explicit debug HTTP is restricted to `localhost`, `127.0.0.1`, `[::1]`, and Android's `10.0.2.2` alias. Bare hosts always default to HTTPS. International hostnames must use ASCII punycode.
- Store the returned origin and derive subsequent loads from it. `sameOrigin` compares scheme, canonical host, and effective port. It accepts absolute HTTP(S) targets and rejects credentials and ambiguous authorities. Scheme-relative and relative inputs must be resolved against the trusted origin by the caller if needed; WebView navigation callbacks normally supply absolute URLs.
- Only `INTERNAL` belongs in the WebView. `EXTERNAL` may open an `ACTION_VIEW` browser intent only for a main-frame navigation with a user gesture. Block unsolicited external redirects and subframe external navigation. Do not handle external URLs in `onPageStarted` by launching an app.
- `EXTERNAL_BROWSER` is the exact same-origin `/oauth/{provider}/start` route. The Hub currently starts login from JavaScript, so `hasGesture()` may be false. Allow this route only as a deliberate login action in the main frame, guard repeat launches, and open the system browser. Do not infer an OAuth exception from a prefix or an external provider host. This class classifies the route; the caller owns launch authorization and UI state.
- `BLOCKED` includes unsafe schemes, malformed authorities, all URL credentials, and HTTPS-to-HTTP navigation to the configured Hub host. Different ports and subdomains never become internal. Cancel TLS errors; do not treat this policy as permission to bypass certificate checks.
- New-window requests must pass the same policy and gesture checks. Never copy cookies, credentials, or authorization headers to an external browser or origin.

The policy suite covers normalization, malformed and encoded authorities, numeric IPv4 aliases, IPv6 canonicalization, default and invalid ports, local debug HTTP, origin boundaries, external routing, dangerous schemes, downgrade attempts, and exact OAuth route matching.
