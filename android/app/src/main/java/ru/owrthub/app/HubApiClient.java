package ru.owrthub.app;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URISyntaxException;
import java.net.URLConnection;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import javax.net.ssl.HttpsURLConnection;
import org.json.JSONException;
import org.json.JSONObject;
import org.json.JSONTokener;

/**
 * Synchronous native Hub JSON API. Call from an executor, never the UI thread. Owns only its
 * origin's HttpOnly session, independently of WebView cookies.
 */
public final class HubApiClient {
  public static final String SESSION_COOKIE = "owrt_remote_session";
  public static final String MOBILE_BASE = "/api/mobile/v1";
  private static final int MAX_JSON_BYTES = 4 * 1024 * 1024;
  private static final int MAX_REDIRECTS = 3;
  private static final long DEFAULT_SESSION_MILLIS = 30L * 24 * 60 * 60 * 1000;
  private static final long MAX_SESSION_MILLIS = 366L * 24 * 60 * 60 * 1000;
  private final URI hub;
  private final HubSessionStore store;
  private final Object authLock = new Object();
  private String username = "";
  private String cookie = "";
  private long expiresAtMillis;
  private String csrfToken = "";
  private String xrayCsrf = "";
  private String hubUpdateCsrf = "";
  private String routerUpdateCsrf = "";
  private long authGeneration;

  public HubApiClient(URI hub, HubSessionStore store) throws IOException {
    this.hub =
        HubUrlPolicy.normalizeHub(hub == null ? null : hub.toString(), "", BuildConfig.DEBUG);
    this.store = store;
    if (store != null) {
      HubSessionStore.SavedSession saved = store.load();
      if (saved != null && saved.hub.equals(this.hub)) {
        username = saved.username;
        cookie = saved.cookie;
        expiresAtMillis = saved.expiresAtMillis;
      }
    }
  }

  public URI getHub() {
    return hub;
  }

  public String getUsername() {
    synchronized (authLock) {
      return username;
    }
  }

  public boolean hasSession() {
    return !getCookieHeader().isEmpty();
  }

  public String getCookieHeader() {
    synchronized (authLock) {
      return expiresAtMillis > System.currentTimeMillis() ? cookie : "";
    }
  }

  /**
   * Pass only to CookieManager.setCookie(hub origin, value, callback), before loading a tool page.
   */
  public String exportCookieForWebView() {
    synchronized (authLock) {
      long remaining = Math.max(0, (expiresAtMillis - System.currentTimeMillis()) / 1000);
      String pair = remaining > 0 && !cookie.isEmpty() ? cookie : SESSION_COOKIE + "=";
      return pair
          + "; Path=/; HttpOnly; SameSite=Lax; Max-Age="
          + remaining
          + (hub.getScheme().equals("https") ? "; Secure" : "");
    }
  }

  /**
   * Import only CookieManager.getCookie(getHub().toString()) after a visible, same-origin browser
   * login. The caller must not pass third-party cookies. Validate it using session() before showing
   * native account data.
   */
  public void adoptCookie(String cookieHeader) throws IOException {
    String accepted = "";
    if (cookieHeader != null) {
      for (String part : cookieHeader.split(";")) {
        String pair = part.trim();
        if (pair.startsWith(SESSION_COOKIE + "=")) {
          if (!accepted.isEmpty() || !isSessionCookiePair(pair)) {
            throw new IOException("Недопустимая сессия Hub");
          }
          accepted = pair;
        }
      }
    }
    if (accepted.isEmpty()) throw new AuthRequiredException("session_missing");
    synchronized (authLock) {
      authGeneration++;
      cookie = accepted;
      clearCsrfLocked();
      expiresAtMillis = System.currentTimeMillis() + DEFAULT_SESSION_MILLIS;
      persistLocked();
    }
  }

  public JSONObject capabilities() throws IOException {
    return get(MOBILE_BASE + "/capabilities");
  }

  public JSONObject loginChallenge() throws IOException {
    return get(MOBILE_BASE + "/login/challenge");
  }

  public JSONObject session() throws IOException {
    return get(MOBILE_BASE + "/session");
  }

  /** Password and OTP exist only for this request; neither is copied into saved state. */
  public JSONObject login(
      String username, String password, String captchaToken, String captchaAnswer, String otp)
      throws IOException {
    if (username == null
        || username.trim().isEmpty()
        || username.length() > 256
        || password == null
        || password.isEmpty()
        || password.length() > 4096) {
      throw new IOException("Укажите логин и пароль Hub");
    }
    clearSession();
    synchronized (authLock) {
      this.username = username.trim();
      if (store != null) store.saveConnection(hub, this.username);
    }
    JSONObject body = new JSONObject();
    try {
      body.put("username", username.trim());
      body.put("password", password);
      body.put("captcha_token", captchaToken == null ? "" : captchaToken);
      body.put("captcha_answer", captchaAnswer == null ? "" : captchaAnswer.trim());
      body.put("otp", otp == null ? "" : otp.trim());
      post(MOBILE_BASE + "/login", body);
    } catch (JSONException exception) {
      throw new IOException("Не удалось подготовить вход в Hub");
    } finally {
      body.remove("password");
      body.remove("otp");
    }
    if (!hasSession()) throw new IOException("Hub не создал сессию входа");
    return session();
  }

  public JSONObject logout() throws IOException {
    try {
      if (!hasSession()) return new JSONObject();
      if (authSnapshot().csrf.isEmpty()) session();
      return post(MOBILE_BASE + "/logout", new JSONObject());
    } finally {
      clearSession();
    }
  }

  public void clearSession() throws IOException {
    synchronized (authLock) {
      authGeneration++;
      cookie = "";
      expiresAtMillis = 0;
      clearCsrfLocked();
      if (store != null) store.clearSession();
    }
  }

  public JSONObject get(String apiPath) throws IOException {
    return request("GET", apiPath, null);
  }

  public byte[] noteImage(String path) throws IOException {
    URI target = resolveApiPath(hub, path);
    if (!target.getPath().matches("/api/mobile/v1/router/[^/]+/notes-media/[^/]+/[^/]+"))
      throw new IOException("Недопустимый путь изображения");
    synchronizeSession();
    AuthSnapshot auth = authSnapshot();
    HttpURLConnection connection = (HttpURLConnection) target.toURL().openConnection();
    try {
      connection.setInstanceFollowRedirects(false);
      connection.setConnectTimeout(15000);
      connection.setReadTimeout(20000);
      connection.setRequestProperty("Cookie", auth.cookie);
      connection.setRequestProperty("Accept", "image/*");
      connection.setRequestProperty("Accept-Encoding", "identity");
      if (connection.getResponseCode() != 200)
        throw new IOException("Изображение недоступно. Обновите заметки и проверьте доступ.");
      String type = connection.getContentType();
      if (type == null || !type.startsWith("image/"))
        throw new IOException("Hub вернул файл другого типа");
      ByteArrayOutputStream data = new ByteArrayOutputStream();
      try (InputStream input = connection.getInputStream()) {
        byte[] buffer = new byte[8192];
        int n;
        while ((n = input.read(buffer)) != -1) {
          if (data.size() + n > 8 * 1024 * 1024)
            throw new IOException("Изображение слишком большое");
          data.write(buffer, 0, n);
        }
      }
      ensureCurrentSession(auth);
      return data.toByteArray();
    } finally {
      connection.disconnect();
    }
  }

  public JSONObject post(String apiPath, JSONObject body) throws IOException {
    resolveApiPath(hub, apiPath);
    if (!apiPath.equals(MOBILE_BASE + "/login")) {
      String intendedSession = getCookieHeader();
      synchronizeSession();
      if (!intendedSession.equals(getCookieHeader()))
        throw new AuthRequiredException("session_changed");
      // A Hub restart rotates operation CSRF tokens. Refresh before, never replay after,
      // a mutation; also do not submit an old screen's command under a different account.
      if (hasSession()) {
        session();
        if (!intendedSession.equals(getCookieHeader()))
          throw new AuthRequiredException("session_changed");
      }
    }
    return request("POST", apiPath, body == null ? new JSONObject() : body);
  }

  /** Activities share the encrypted session; another screen may revoke or replace it. */
  public void synchronizeSession() throws IOException {
    if (store == null) return;
    HubSessionStore.SavedSession saved = store.load();
    synchronized (authLock) {
      String savedCookie = saved != null && saved.hub.equals(hub) ? saved.cookie : "";
      if (!cookie.equals(savedCookie)) {
        authGeneration++;
        cookie = savedCookie;
        username = saved == null ? "" : saved.username;
        expiresAtMillis = savedCookie.isEmpty() ? 0 : saved.expiresAtMillis;
        clearCsrfLocked();
      }
    }
  }

  private JSONObject request(String method, String apiPath, JSONObject body) throws IOException {
    synchronizeSession();
    URI current = resolveApiPath(hub, apiPath);
    AuthSnapshot auth = authSnapshot();
    byte[] payload = body == null ? null : body.toString().getBytes(StandardCharsets.UTF_8);
    if (payload != null && payload.length > MAX_JSON_BYTES) {
      Arrays.fill(payload, (byte) 0);
      throw new IOException("Запрос к Hub слишком большой");
    }
    try {
      for (int redirects = 0; ; redirects++) {
        HttpURLConnection connection = null;
        try {
          URLConnection opened = current.toURL().openConnection();
          if (!(opened instanceof HttpURLConnection)
              || (hub.getScheme().equals("https") && !(opened instanceof HttpsURLConnection))) {
            throw new IOException("Небезопасное соединение с Hub");
          }
          connection = (HttpURLConnection) opened;
          connection.setInstanceFollowRedirects(false);
          connection.setConnectTimeout(15000);
          connection.setReadTimeout(
              current.getPath().equals("/api/notifications/wait") ? 35000
                  : current.getPath().endsWith("/wol") ? 90000
                  : current.getPath().endsWith("/wol/devices") ? 30000 : 20000);
          connection.setUseCaches(false);
          connection.setRequestMethod(method);
          connection.setRequestProperty("Accept", "application/json");
          connection.setRequestProperty("Accept-Encoding", "identity");
          connection.setRequestProperty("Cache-Control", "no-store");
          connection.setRequestProperty("User-Agent", "OWRT-Hub-Android/1 native");
          connection.setRequestProperty("Cookie", auth.cookie);
          if (payload != null) {
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            connection.setRequestProperty("Origin", hub.toString());
            if (!auth.csrf.isEmpty()) connection.setRequestProperty("X-Mobile-CSRF", auth.csrf);
            String operationHeader = operationCsrfHeader(current.getRawPath());
            String operationToken =
                operationHeader.equals("X-Xray-Control-CSRF")
                    ? auth.xrayCsrf
                    : operationHeader.equals("X-Hub-Update-CSRF")
                        ? auth.hubUpdateCsrf
                        : operationHeader.equals("X-Router-Update-CSRF")
                            ? auth.routerUpdateCsrf
                            : "";
            if (!operationToken.isEmpty())
              connection.setRequestProperty(operationHeader, operationToken);
            connection.setDoOutput(true);
            connection.setFixedLengthStreamingMode(payload.length);
            try (OutputStream output = connection.getOutputStream()) {
              output.write(payload);
            }
          }
          int status = connection.getResponseCode();
          if (isRedirect(status)) {
            if (redirects >= MAX_REDIRECTS)
              throw new IOException("Слишком много перенаправлений Hub");
            URI target = checkedRedirect(hub, current, connection.getHeaderField("Location"));
            // API POSTs do not need redirects; never replay credentials or writes.
            if (!method.equals("GET")) throw new IOException("Hub перенаправил запрос API");
            current = target;
            continue;
          }
          if (status == 401 || status == 403) {
            JSONObject error = new JSONObject();
            try {
              error = readJson(connection, status);
            } catch (IOException ignored) {
              /* Status remains authoritative even for broken proxy bodies. */
            }
            if (status == 401) {
              try {
                clearSessionIfCurrent(auth.generation);
              } catch (IOException ignored) {
                /* Memory is already cleared; keep the typed auth result. */
              }
              throw new AuthRequiredException(errorCode(error), serverError(error));
            }
            throw new PermissionException(errorCode(error), serverError(error));
          }
          JSONObject result = readJson(connection, status);
          if (status < 200 || status >= 300 || result.has("ok") && !result.optBoolean("ok"))
            throw new ApiException(status, errorCode(result), serverError(result));
          ensureCurrentSession(auth);
          acceptCookies(connection.getHeaderFields(), auth);
          updateSession(result, auth);
          return result;
        } catch (ApiException exception) {
          throw exception;
        } catch (IOException exception) {
          // Raw platform errors may include URLs, query parameters, or headers.
          throw new IOException(safeNetworkMessage(exception));
        } finally {
          if (connection != null) connection.disconnect();
        }
      }
    } finally {
      if (payload != null) Arrays.fill(payload, (byte) 0);
    }
  }

  private static String safeNetworkMessage(IOException exception) {
    if (exception instanceof javax.net.ssl.SSLException) {
      return "Не удалось проверить TLS-сертификат Hub";
    }
    if (exception instanceof java.net.SocketTimeoutException) return "Hub не ответил вовремя";
    // Messages created here contain no URL or response values.
    String message = exception.getMessage();
    if (message != null
        && (message.startsWith("Hub ")
            || message.startsWith("Ответ Hub ")
            || message.equals("Небезопасное перенаправление Hub")
            || message.equals("Недопустимый путь API Hub")
            || message.equals("Слишком много перенаправлений Hub")
            || message.equals("Небезопасное соединение с Hub"))) return message;
    return "Не удалось связаться с Hub. Проверьте адрес, сеть и сертификат";
  }

  private static JSONObject readJson(HttpURLConnection connection, int status) throws IOException {
    long declaredLength = connection.getContentLengthLong();
    if (declaredLength > MAX_JSON_BYTES) throw new IOException("Ответ Hub слишком большой");
    InputStream input = status >= 400 ? connection.getErrorStream() : connection.getInputStream();
    if (input == null) return new JSONObject();
    byte[] bytes;
    try (InputStream stream = input;
        ByteArrayOutputStream output = new ByteArrayOutputStream()) {
      byte[] buffer = new byte[8192];
      int count;
      while ((count = stream.read(buffer)) != -1) {
        if (output.size() > MAX_JSON_BYTES - count)
          throw new IOException("Ответ Hub слишком большой");
        output.write(buffer, 0, count);
      }
      bytes = output.toByteArray();
    }
    if (bytes.length == 0 && (status == 204 || status >= 400)) return new JSONObject();
    try {
      String text =
          StandardCharsets.UTF_8
              .newDecoder()
              .onMalformedInput(CodingErrorAction.REPORT)
              .onUnmappableCharacter(CodingErrorAction.REPORT)
              .decode(ByteBuffer.wrap(bytes))
              .toString();
      String type = connection.getContentType();
      if (status >= 400
          && type != null
          && type.toLowerCase(Locale.ROOT).startsWith("text/plain")
          && text.trim().length() <= 400
          && !text.trim().startsWith("<")) return new JSONObject().put("error", text);
      JSONTokener parser = new JSONTokener(text);
      Object value = parser.nextValue();
      if (!(value instanceof JSONObject) || parser.nextClean() != 0)
        throw new JSONException("object required");
      return (JSONObject) value;
    } catch (JSONException | CharacterCodingException exception) {
      // Preserve typed authentication/permission errors even if a proxy returns HTML.
      if (status >= 400) return new JSONObject();
      throw new IOException("Ответ Hub не является JSON API");
    } finally {
      Arrays.fill(bytes, (byte) 0);
    }
  }

  private void acceptCookies(Map<String, List<String>> headers, AuthSnapshot auth)
      throws IOException {
    if (headers == null) return;
    for (Map.Entry<String, List<String>> entry : headers.entrySet()) {
      if (entry.getKey() == null
          || !entry.getKey().equalsIgnoreCase("Set-Cookie")
          || entry.getValue() == null) continue;
      for (String value : entry.getValue()) {
        SessionCookie accepted =
            parseSetCookie(value, hub.getScheme().equals("https"), System.currentTimeMillis());
        if (accepted == null) continue;
        synchronized (authLock) {
          if (authGeneration != auth.generation) throw new AuthRequiredException("session_changed");
          if (store != null
              && !store.saveIfMatches(
                  hub, auth.cookie, username, accepted.pair, accepted.expiresAtMillis))
            throw new AuthRequiredException("session_changed");
          cookie = accepted.pair;
          expiresAtMillis = accepted.expiresAtMillis;
          if (cookie.isEmpty()) clearCsrfLocked();
        }
      }
    }
  }

  private void updateSession(JSONObject result, AuthSnapshot auth) throws IOException {
    JSONObject account = result.optJSONObject("account");
    JSONObject session = result.optJSONObject("session");
    String csrf = result.optString("csrf_token", "");
    if (account == null || session == null || !csrf.matches("[A-Za-z0-9_-]{16,512}")) return;
    synchronized (authLock) {
      if (authGeneration != auth.generation) throw new AuthRequiredException("session_changed");
      String name = account.optString("username", "");
      if (!name.isEmpty() && name.length() <= 256) username = name;
      csrfToken = csrf;
      JSONObject operationTokens = result.optJSONObject("csrf");
      JSONObject operationHeaders = result.optJSONObject("csrf_headers");
      xrayCsrf =
          permittedCsrf(operationTokens, operationHeaders, "xray_control", "X-Xray-Control-CSRF");
      hubUpdateCsrf =
          permittedCsrf(operationTokens, operationHeaders, "hub_update", "X-Hub-Update-CSRF");
      routerUpdateCsrf =
          permittedCsrf(
              operationTokens, operationHeaders, "router_updates", "X-Router-Update-CSRF");
      long seconds = session.optLong("expires_at", 0);
      if (seconds > 0 && seconds < Long.MAX_VALUE / 1000) {
        expiresAtMillis = Math.min(seconds * 1000, System.currentTimeMillis() + MAX_SESSION_MILLIS);
      }
      if (store != null && !store.saveIfMatches(hub, cookie, username, cookie, expiresAtMillis))
        throw new AuthRequiredException("session_changed");
    }
  }

  private void persistLocked() throws IOException {
    if (store != null) store.save(hub, username, cookie, expiresAtMillis);
  }

  private void clearCsrfLocked() {
    csrfToken = "";
    xrayCsrf = "";
    hubUpdateCsrf = "";
    routerUpdateCsrf = "";
  }

  private static String permittedCsrf(
      JSONObject tokens, JSONObject headers, String key, String fixedHeader) {
    if (tokens == null || headers == null || !headers.optString(key, "").equals(fixedHeader))
      return "";
    String token = tokens.optString(key, "");
    return token.matches("[A-Za-z0-9_-]{16,512}") ? token : "";
  }

  /** Fixed names and exact route families; server data never becomes an arbitrary HTTP header. */
  static String operationCsrfHeader(String path) {
    if (path.matches("/api/router/[^/]+/xray-control")) return "X-Xray-Control-CSRF";
    if (path.startsWith("/api/hub-update/")) return "X-Hub-Update-CSRF";
    if (path.startsWith("/api/router-updates/")) return "X-Router-Update-CSRF";
    return "";
  }

  private AuthSnapshot authSnapshot() {
    synchronized (authLock) {
      boolean live = expiresAtMillis > System.currentTimeMillis() && !cookie.isEmpty();
      return new AuthSnapshot(
          live ? cookie : "",
          live ? csrfToken : "",
          live ? xrayCsrf : "",
          live ? hubUpdateCsrf : "",
          live ? routerUpdateCsrf : "",
          authGeneration);
    }
  }

  private void clearSessionIfCurrent(long generation) throws IOException {
    synchronized (authLock) {
      if (authGeneration == generation) {
        String rejected = cookie;
        authGeneration++;
        cookie = "";
        expiresAtMillis = 0;
        clearCsrfLocked();
        if (store != null) store.clearSessionIfMatches(hub, rejected);
      }
    }
  }

  private void ensureCurrentSession(AuthSnapshot auth) throws IOException {
    if (store != null && !auth.cookie.isEmpty()) {
      HubSessionStore.SavedSession saved = store.load();
      if (saved == null || !saved.hub.equals(hub) || !saved.cookie.equals(auth.cookie))
        throw new AuthRequiredException("session_changed");
    }
    synchronized (authLock) {
      if (authGeneration != auth.generation) throw new AuthRequiredException("session_changed");
    }
  }

  static URI resolveApiPath(URI origin, String path) throws IOException {
    if (path == null || path.length() > 8192 || !path.startsWith("/api/")) {
      throw new IOException("Недопустимый путь API Hub");
    }
    try {
      for (int index = 0; index < path.length(); index++) {
        char character = path.charAt(index);
        if (character <= 0x20 || character >= 0x7f || character == '\\')
          throw new URISyntaxException("", "invalid");
      }
      URI relative = new URI(path);
      String rawPath = relative.getRawPath();
      String lowerPath = rawPath.toLowerCase(Locale.ROOT);
      if (relative.isAbsolute()
          || relative.getRawAuthority() != null
          || relative.getRawFragment() != null
          || !rawPath.startsWith("/api/")
          || !relative.normalize().equals(relative)
          || rawPath.contains("//")
          || rawPath.contains(";")
          || lowerPath.contains("%2e")
          || lowerPath.contains("%2f")
          || lowerPath.contains("%5c")
          || lowerPath.contains("%25")) {
        throw new URISyntaxException("", "invalid");
      }
      URI result = origin.resolve(relative);
      if (!HubUrlPolicy.sameOrigin(origin, result.toString()))
        throw new URISyntaxException("", "invalid");
      return result;
    } catch (URISyntaxException | IllegalArgumentException exception) {
      throw new IOException("Недопустимый путь API Hub");
    }
  }

  static URI checkedRedirect(URI origin, URI current, String location) throws IOException {
    try {
      if (location == null || location.length() > 8192) throw new IllegalArgumentException();
      URI resolved = current.resolve(new URI(location));
      if (!HubUrlPolicy.sameOrigin(origin, resolved.toString())
          || resolved.getRawFragment() != null) {
        throw new IllegalArgumentException();
      }
      return resolveApiPath(
          origin,
          resolved.getRawPath()
              + (resolved.getRawQuery() == null ? "" : "?" + resolved.getRawQuery()));
    } catch (URISyntaxException | IllegalArgumentException exception) {
      throw new IOException("Небезопасное перенаправление Hub");
    }
  }

  static boolean isSessionCookiePair(String pair) {
    return pair != null && pair.matches(SESSION_COOKIE + "=[A-Za-z0-9_-]{16,4096}");
  }

  static SessionCookie parseSetCookie(String header, boolean requireSecure, long now) {
    if (header == null
        || header.length() > 8192
        || header.indexOf('\r') >= 0
        || header.indexOf('\n') >= 0) return null;
    String[] parts = header.split(";", -1);
    String pair = parts[0].trim();
    if (!pair.startsWith(SESSION_COOKIE + "=")) return null;
    boolean empty = pair.equals(SESSION_COOKIE + "=");
    if (!empty && !isSessionCookiePair(pair)) return null;
    boolean secure = false;
    boolean httpOnly = false;
    boolean rootPath = false;
    long maxAge = DEFAULT_SESSION_MILLIS / 1000;
    for (int index = 1; index < parts.length; index++) {
      String attribute = parts[index].trim();
      int equals = attribute.indexOf('=');
      String name =
          (equals == -1 ? attribute : attribute.substring(0, equals))
              .trim()
              .toLowerCase(Locale.ROOT);
      String value = equals == -1 ? "" : attribute.substring(equals + 1).trim();
      if (name.equals("domain"))
        return null; // Host-only sessions cannot escape the configured origin.
      if (name.equals("secure")) secure = true;
      if (name.equals("httponly")) httpOnly = true;
      if (name.equals("path")) {
        if (!value.equals("/")) return null;
        rootPath = true;
      }
      if (name.equals("max-age")) {
        try {
          maxAge = Long.parseLong(value);
        } catch (NumberFormatException exception) {
          return null;
        }
      }
    }
    if (!httpOnly || !rootPath || (requireSecure && !secure)) return null;
    if (empty || maxAge <= 0) return new SessionCookie("", 0);
    long lifetime = Math.min(maxAge, MAX_SESSION_MILLIS / 1000) * 1000;
    return new SessionCookie(pair, now + lifetime);
  }

  private static boolean isRedirect(int status) {
    return status == 301 || status == 302 || status == 303 || status == 307 || status == 308;
  }

  private static String errorCode(JSONObject result) {
    String value = result.optString("code", result.optString("error_code", ""));
    return value.matches("[a-zA-Z][a-zA-Z0-9_-]{0,63}") ? value : "";
  }

  private static String serverError(JSONObject result) {
    Object value = result.opt("error");
    return value instanceof String ? (String) value : "";
  }

  static String errorMessage(int status, String code, String message) {
    if (message != null) {
      String clean = message.replaceAll("[\\p{Cntrl}\\p{Cf}]", " ").replaceAll("\\s+", " ").trim();
      if (!clean.isEmpty()) return clean.length() > 400 ? clean.substring(0, 400) + "…" : clean;
    }
    if ("captcha_failed".equals(code)) return "Капча не прошла проверку. Введите новые цифры.";
    if ("login_failed".equals(code)) return "Не удалось войти. Проверьте логин, пароль и код 2FA.";
    if (status >= 200 && status < 300) return "Hub не подтвердил выполнение операции.";
    return status == 401
        ? "Нужен вход в Hub"
        : status == 403
            ? "У аккаунта нет разрешения на это действие"
            : "Hub отклонил запрос (HTTP " + status + ")";
  }

  static final class SessionCookie {
    final String pair;
    final long expiresAtMillis;

    SessionCookie(String pair, long expiresAtMillis) {
      this.pair = pair;
      this.expiresAtMillis = expiresAtMillis;
    }
  }

  private static final class AuthSnapshot {
    final String cookie;
    final String csrf;
    final String xrayCsrf;
    final String hubUpdateCsrf;
    final String routerUpdateCsrf;
    final long generation;

    AuthSnapshot(
        String cookie,
        String csrf,
        String xrayCsrf,
        String hubUpdateCsrf,
        String routerUpdateCsrf,
        long generation) {
      this.cookie = cookie;
      this.csrf = csrf;
      this.xrayCsrf = xrayCsrf;
      this.hubUpdateCsrf = hubUpdateCsrf;
      this.routerUpdateCsrf = routerUpdateCsrf;
      this.generation = generation;
    }
  }

  public static class ApiException extends IOException {
    public final int statusCode;
    public final String errorCode;

    ApiException(int statusCode, String errorCode) {
      this(statusCode, errorCode, "");
    }

    ApiException(int statusCode, String errorCode, String serverMessage) {
      super(errorMessage(statusCode, errorCode, serverMessage));
      this.statusCode = statusCode;
      this.errorCode = errorCode;
    }
  }

  public static final class AuthRequiredException extends ApiException {
    AuthRequiredException(String errorCode) {
      super(401, errorCode);
    }

    AuthRequiredException(String errorCode, String message) {
      super(401, errorCode, message);
    }
  }

  public static final class PermissionException extends ApiException {
    PermissionException(String errorCode) {
      super(403, errorCode);
    }

    PermissionException(String errorCode, String message) {
      super(403, errorCode, message);
    }
  }
}
