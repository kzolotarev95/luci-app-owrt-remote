package ru.owrthub.app;

import java.io.IOException;
import java.net.URI;

/** Offline JVM checks: no sockets, credentials, Android runtime, or external dependencies. */
public final class HubApiClientTest {
    private static int assertions;
    private static final URI HUB = URI.create("https://hub.example:8443");
    private static final String PAIR = "owrt_remote_session=abcdefghijklmnopqrstuvwxyz0123456789_-";

    public static void main(String[] arguments) throws Exception {
        equal("Неверный текущий пароль", HubApiClient.errorMessage(400, "", "Неверный текущий пароль"));
        equal("Первая Вторая", HubApiClient.errorMessage(400, "", "Первая\n\u202eВторая"));
        check(HubApiClient.errorMessage(401, "captcha_failed", "").contains("Капча"));
        equal("Нужен вход в Hub", HubApiClient.errorMessage(401, "session_expired", ""));
        check(HubApiClient.errorMessage(400, "", "a".repeat(900)).length() <= 401);
        equal("У аккаунта нет разрешения на это действие", HubApiClient.errorMessage(403, "", ""));
        equal("https://hub.example:8443/api/routers?availability=1",
                HubApiClient.resolveApiPath(HUB, "/api/routers?availability=1").toString());
        equal("https://hub.example:8443/api/router/test%20name/logs",
                HubApiClient.resolveApiPath(HUB, "/api/router/test%20name/logs").toString());
        for (String path : new String[] {"https://evil.example/api/routers", "//evil.example/api/routers",
                "/login", "/api/../login", "/api/x/../../api/routers", "/api/%2e%2e/login",
                "/api/x%2fy", "/api/%255c", "/api/x\\y", "/api//x", "/api/x;path=y",
                "/api/routers#fragment", "/api/routers\r\nX-Test:true", "/api/x?a=hello world"}) {
            fails(() -> HubApiClient.resolveApiPath(HUB, path));
        }
        URI original = HubApiClient.resolveApiPath(HUB, "/api/routers");
        equal("https://hub.example:8443/api/router-groups",
                HubApiClient.checkedRedirect(HUB, original, "router-groups").toString());
        equal("https://hub.example:8443/api/routers?availability=1",
                HubApiClient.checkedRedirect(HUB, original, "/api/routers?availability=1").toString());
        for (String target : new String[] {"https://evil.example/api/routers", "http://hub.example:8443/api/routers",
                "https://hub.example/api/routers", "//evil.example/api/routers", "https://user@hub.example:8443/api/routers",
                "/login", "/api/routers#token", "/api/%2e%2e/login", "\\\\evil.example\\api\\routers"}) {
            fails(() -> HubApiClient.checkedRedirect(HUB, original, target));
        }
        fails(() -> HubApiClient.checkedRedirect(HUB, original, null));
        equal("X-Xray-Control-CSRF", HubApiClient.operationCsrfHeader("/api/router/main/xray-control"));
        equal("X-Hub-Update-CSRF", HubApiClient.operationCsrfHeader("/api/hub-update/check"));
        equal("X-Router-Update-CSRF", HubApiClient.operationCsrfHeader("/api/router-updates/start"));
        equal("", HubApiClient.operationCsrfHeader("/api/router/main/xray-control/other"));
        equal("", HubApiClient.operationCsrfHeader("/api/hub-update-other/check"));
        equal("", HubApiClient.operationCsrfHeader("/api/router/main/notes"));

        long now = 123456789L;
        HubApiClient.SessionCookie accepted = HubApiClient.parseSetCookie(
                PAIR + "; HttpOnly; Secure; Path=/; Max-Age=120; SameSite=Lax", true, now);
        check(accepted != null);
        equal(PAIR, accepted.pair);
        equal(now + 120000L, accepted.expiresAtMillis);
        check(HubApiClient.parseSetCookie(PAIR + "; Secure; Path=/", true, now) == null);
        check(HubApiClient.parseSetCookie(PAIR + "; HttpOnly; Path=/", true, now) == null);
        check(HubApiClient.parseSetCookie(PAIR + "; HttpOnly; Secure; Path=/api", true, now) == null);
        check(HubApiClient.parseSetCookie(PAIR + "; HttpOnly; Secure; Path=/; Domain=hub.example", true, now) == null);
        check(HubApiClient.parseSetCookie(PAIR + "; HttpOnly; Secure; Path=/; Max-Age=abc", true, now) == null);
        check(HubApiClient.parseSetCookie(PAIR + "; HttpOnly; Secure; Path=/\r\nInjected: 1", true, now) == null);
        check(HubApiClient.parseSetCookie("unrelated_cookie=value; HttpOnly; Secure; Path=/", true, now) == null);
        equal("", HubApiClient.parseSetCookie("owrt_remote_session=; HttpOnly; Secure; Path=/; Max-Age=0", true, now).pair);
        equal("", HubApiClient.parseSetCookie(PAIR + "; HttpOnly; Secure; Path=/; Max-Age=-1", true, now).pair);
        check(HubApiClient.parseSetCookie(PAIR + "; HttpOnly; Path=/; Max-Age=120", false, now) != null);

        HubApiClient client = new HubApiClient(HUB, null);
        check(!client.hasSession());
        client.adoptCookie("unrelated=value; " + PAIR);
        check(client.hasSession());
        equal(PAIR, client.getCookieHeader());
        String exported = client.exportCookieForWebView();
        check(exported.startsWith(PAIR + "; Path=/; HttpOnly; SameSite=Lax; Max-Age="));
        check(exported.endsWith("; Secure"));
        fails(() -> client.adoptCookie(PAIR + "; " + PAIR));
        fails(() -> client.adoptCookie("owrt_remote_session=short"));
        fails(() -> client.adoptCookie("another=not-a-session"));
        client.clearSession();
        check(!client.hasSession());
        equal("", client.getCookieHeader());
        check(client.exportCookieForWebView().contains("owrt_remote_session=;"));
        check(client.exportCookieForWebView().contains("Max-Age=0"));
        equal(401, new HubApiClient.AuthRequiredException("session_expired").statusCode);
        equal(403, new HubApiClient.PermissionException("forbidden").statusCode);
        System.out.println("HubApiClient: " + assertions + " offline assertions passed");
    }

    private interface CheckedAction { void run() throws Exception; }
    private static void fails(CheckedAction action) throws Exception {
        try { action.run(); }
        catch (IOException expected) { assertions++; return; }
        throw new AssertionError("Expected a rejected API path, redirect, or cookie");
    }
    private static void equal(Object expected, Object actual) {
        if (!expected.equals(actual)) throw new AssertionError("Expected " + expected + ", received " + actual);
        assertions++;
    }
    private static void check(boolean condition) {
        if (!condition) throw new AssertionError("Condition failed");
        assertions++;
    }
}
