package ru.owrthub.app;

import android.app.*;
import android.content.*;
import android.graphics.Rect;
import android.os.*;
import android.text.*;
import android.view.*;
import android.view.inputmethod.InputMethodManager;
import android.webkit.CookieManager;
import android.widget.*;
import java.net.URI;
import java.util.*;
import java.util.concurrent.*;
import org.json.*;

/** Native navigation and Hub screens; third-party web interfaces are separate tools. */
public final class MainActivity extends Activity {
  private final Handler main = new Handler(Looper.getMainLooper());
  private final ExecutorService network = Executors.newSingleThreadExecutor();
  private NativeUi ui;
  private HubSessionStore store;
  private HubApiClient api;
  private SharedPreferences prefs;
  private LinearLayout root, content, otpBox, routerResults;
  private ScrollView scrollView;
  private ProgressBar progress;
  private TextView message, captchaCode;
  private EditText user, password, captcha, otp;
  private LinearLayout credentials;
  private Button browserSignIn;
  private JSONObject session = new JSONObject(),
      availability = new JSONObject(),
      resources = new JSONObject(),
      hubUpdateStatus = new JSONObject(),
      loginMeta = new JSONObject();
  private JSONArray routers = new JSONArray(), groups = new JSONArray();
  private String screen = "connect",
      selectedId = "",
      search = "",
      filter = "Все",
      groupFilter = "",
      captchaToken = "";
  private int generation;
  private int restoreScrollY = -1;
  private Runnable pendingUiWork;
  private boolean resumed,
      requesting,
      browserLogin,
      returning,
      pendingRevalidation,
      quietRequest,
      updatingViews,
      addressVisible;
  private final Runnable refreshTick =
      () -> {
        if (resumed && api != null && api.hasSession() && isDataScreen()) {
          refresh(false);
        }
      };

  interface Work {
    JSONObject run(HubApiClient client) throws Exception;
  }

  interface Done {
    void accept(JSONObject value);
  }

  @Override
  public void onCreate(Bundle state) {
    super.onCreate(state);
    prefs = getSharedPreferences("native_design", MODE_PRIVATE);
    ui = new NativeUi(this, prefs.getBoolean("winter", false));
    store = new HubSessionStore(this);
    getWindow().setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);
    try {
      HubSessionStore.SavedSession saved = store.load();
      String debug = getIntent().getStringExtra("debugHubUrl");
      if (BuildConfig.DEBUG && debug != null) {
        URI local = HubUrlPolicy.normalizeHub(debug, "", true);
        if (!Arrays.asList("127.0.0.1", "10.0.2.2", "localhost").contains(local.getHost()))
          throw new IllegalArgumentException();
        api = new HubApiClient(local, store);
        if (api.hasSession()) resumeSession();
        else {
          loginScreen();
          challenge();
        }
      } else if (saved != null) {
        api = new HubApiClient(saved.hub, store);
        if (api.hasSession()) resumeSession();
        else {
          loginScreen();
          challenge();
        }
      } else connection();
    } catch (Exception e) {
      connection();
      notice(e.getMessage(), true);
    }
  }

  private void resumeSession() {
    frame("overview", "OWRT Hub", false);
    add(ui.button("Повторить подключение", false, this::resumeSession));
    call(
        client -> client.session(),
        v -> {
          session = v;
          refresh(true);
        });
  }

  @Override
  protected void onResume() {
    super.onResume();
    resumed = true;
    if (browserLogin && api != null) {
      browserLogin = false;
      call(
          client -> {
            client.adoptCookie(CookieManager.getInstance().getCookie(client.getHub().toString()));
            return client.session();
          },
          v -> {
            session = v;
            refresh(true);
          });
    } else if (returning && api != null && !screen.equals("login") && !screen.equals("connect")) {
      if (requesting) pendingRevalidation = true;
      else revalidateSession();
    } else schedule();
    returning = false;
  }

  private void revalidateSession() {
    call(
        client -> client.session(),
        value -> {
          if (!obj(session, "account").toString().equals(obj(value, "account").toString()))
            clearAccountData();
          session = value;
          refresh(false);
        });
  }

  @Override
  protected void onPause() {
    returning = true;
    resumed = false;
    main.removeCallbacks(refreshTick);
    super.onPause();
  }

  @Override
  protected void onDestroy() {
    generation++;
    main.removeCallbacksAndMessages(null);
    network.shutdownNow();
    super.onDestroy();
  }

  @Override
  public void onBackPressed() {
    if (screen.equals("design") || screen.equals("connectionInfo")) {
      settings();
      return;
    }
    if (screen.equals("router")) routerList();
    else if (Arrays.asList("overview", "connect", "login").contains(screen)) super.onBackPressed();
    else overview();
  }

  private boolean isDataScreen() {
    return Arrays.asList("overview", "routers", "router").contains(screen);
  }

  private JSONObject obj(JSONObject o, String k) {
    JSONObject v = o.optJSONObject(k);
    return v == null ? new JSONObject() : v;
  }

  private String str(JSONObject o, String k, String fallback) {
    String s = o.optString(k, "");
    return s.isEmpty() || s.equals("null") ? fallback : s;
  }

  private boolean owner() {
    return obj(session, "account").optBoolean("is_owner");
  }

  private JSONObject json(Object... a) {
    JSONObject o = new JSONObject();
    try {
      for (int i = 0; i < a.length; i += 2) o.put((String) a[i], a[i + 1]);
    } catch (JSONException ignored) {
    }
    return o;
  }

  private String enc(String s) {
    return android.net.Uri.encode(s);
  }

  private void frame(String name, String title, boolean bottom) {
    if (updatingViews) {
      content = ui.column();
      return;
    }
    screen = name;
    generation++;
    routerResults = null;
    main.removeCallbacks(refreshTick);
    addressVisible = false;
    root = ui.column();
    root.setBackgroundColor(ui.BG);
    root.setOnApplyWindowInsetsListener(
        (v, i) -> {
          v.setPadding(
              i.getSystemWindowInsetLeft(),
              i.getSystemWindowInsetTop(),
              i.getSystemWindowInsetRight(),
              i.getSystemWindowInsetBottom());
          return i;
        });
    setContentView(root);
    root.requestApplyInsets();
    LinearLayout header = ui.row();
    header.setPadding(ui.dp(16), ui.dp(9), ui.dp(12), ui.dp(9));
    if (!Arrays.asList("overview", "routers", "settings", "connect", "login").contains(name))
      header.addView(ui.iconButton("back", "Назад", this::onBackPressed));
    else header.addView(ui.iconBadge("server", ui.BLUE));
    LinearLayout labels = ui.column();
    labels.setPadding(ui.dp(10), 0, 0, 0);
    TextView heading = ui.text(title, 20, true);
    heading.setSingleLine(true);
    heading.setEllipsize(TextUtils.TruncateAt.END);
    labels.addView(heading);
    TextView sub = ui.text(api == null ? "Ваша сеть всегда рядом" : "Адрес скрыт", 12, false);
    sub.setTextColor(ui.MUTED);
    sub.setSingleLine(true);
    sub.setEllipsize(TextUtils.TruncateAt.END);
    labels.addView(ui.spacer(4));
    labels.addView(sub);
    header.addView(labels, new LinearLayout.LayoutParams(0, -2, 1));
    if (api != null)
      header.addView(
          ui.iconButton(
              "eye",
              "Показать или скрыть адрес Hub",
              () -> {
                addressVisible = !addressVisible;
                sub.setText(addressVisible ? api.getHub().getAuthority() : "Адрес скрыт");
              }));
    if (api != null && api.hasSession() && owner())
      header.addView(ui.iconButton("bell", "Уведомления", () -> settingsTool("notifications")));
    root.addView(header);
    root.addView(ui.compactDivider());
    progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
    progress.setIndeterminate(true);
    progress.setVisibility(requesting ? View.VISIBLE : View.GONE);
    root.addView(progress, new LinearLayout.LayoutParams(-1, ui.dp(2)));
    message = ui.body("");
    message.setPadding(ui.dp(20), ui.dp(10), ui.dp(20), ui.dp(10));
    message.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
    message.setVisibility(View.GONE);
    root.addView(message);
    ScrollView scroll = new ScrollView(this);
    scrollView = scroll;
    scroll.setFillViewport(true);
    content = ui.column();
    content.setPadding(ui.dp(20), ui.dp(8), ui.dp(20), ui.dp(20));
    scroll.addView(content);
    root.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
    if (restoreScrollY >= 0) {
      final int savedScroll = restoreScrollY;
      restoreScrollY = -1;
      scroll.post(() -> scroll.scrollTo(0, savedScroll));
    }
    if (bottom) {
      int idx =
          name.equals("routers") || name.equals("router") ? 1 : name.equals("settings") ? 2 : 0;
      root.addView(
          ui.bottomNav(
              new String[] {"Обзор", "Роутеры", "Настройки"},
              new String[] {"grid", "router", "settings"},
              idx,
              n -> {
                hideKeyboard();
                if (n == 0) overview();
                else if (n == 1) routerList();
                else settings();
              }));
    }
  }

  private void add(View v) {
    content.addView(v);
    if (requesting && !quietRequest) setButtonsEnabled(v, false);
  }

  private void space(int h) {
    add(ui.spacer(h));
  }

  private void title(String text, String sub) {
    add(ui.title(text));
    space(8);
    add(ui.body(sub));
    space(22);
  }

  private void notice(String text, boolean error) {
    message.setText(text == null ? "Ошибка соединения" : text);
    message.setTextColor(error ? ui.RED : ui.GREEN);
    message.setVisibility(View.VISIBLE);
  }

  private void call(Work work, Done done) {
    call(work, done, false);
  }

  private void call(Work work, Done done, boolean quiet) {
    if (api == null || network.isShutdown()) return;
    if (requesting) {
      if (!quiet && quietRequest && pendingUiWork == null) {
        int expectedGeneration = generation;
        HubApiClient expectedClient = api;
        pendingUiWork =
            () -> {
              if (expectedGeneration == generation && expectedClient == api) call(work, done);
            };
      }
      return;
    }
    int gen = generation;
    HubApiClient client = api;
    requesting = true;
    quietRequest = quiet;
    if (!quiet) {
      progress.setVisibility(View.VISIBLE);
      setButtonsEnabled(root, false);
    }
    network.execute(
        () -> {
          JSONObject result = null;
          Exception failure = null;
          try {
            result = work.run(client);
          } catch (Exception e) {
            failure = e;
          }
          JSONObject value = result;
          Exception error = failure;
          main.post(
              () -> {
                requesting = false;
                if (isDestroyed()) return;
                if (!quiet) {
                  progress.setVisibility(View.GONE);
                  setButtonsEnabled(root, true);
                }
                if (pendingRevalidation) {
                  pendingRevalidation = false;
                  revalidateSession();
                  return;
                }
                if (gen != generation || client != api) {
                  schedule();
                  return;
                }
                if (error != null) {
                  pendingUiWork = null;
                  if (screen.equals("connect") && unsupportedApi(error))
                    notice(
                        "На этом Hub ещё нет нативного API. Обновите сервер Hub до сборки с"
                            + " поддержкой приложения и повторите подключение.",
                        true);
                  else if (error instanceof HubApiClient.AuthRequiredException
                      && ((HubApiClient.ApiException) error).errorCode.equals("session_changed")) {
                    revalidateSession();
                    return;
                  } else if (error instanceof HubApiClient.AuthRequiredException
                      && !screen.equals("login")) {
                    loginScreen();
                    notice("Сессия завершена. Войдите снова.", true);
                    challenge();
                  } else {
                    notice(friendly(error), true);
                    if (screen.equals("login")
                        && error instanceof HubApiClient.ApiException
                        && Arrays.asList("captcha_failed", "login_failed")
                            .contains(((HubApiClient.ApiException) error).errorCode)) {
                      captchaToken = "";
                      main.postDelayed(
                          () -> {
                            if (screen.equals("login")) challenge();
                          },
                          400);
                    }
                  }
                  schedule();
                  return;
                }
                try {
                  done.accept(value);
                  Runnable pending = pendingUiWork;
                  pendingUiWork = null;
                  if (pending != null) main.post(pending);
                } catch (RuntimeException invalid) {
                  notice("Hub вернул неподдерживаемые данные. Повторите обновление.", true);
                  schedule();
                }
              });
        });
  }

  @Override
  public void onRequestPermissionsResult(int request, String[] permissions, int[] grants) {
    super.onRequestPermissionsResult(request, permissions, grants);
    HubNotifications.permissionResult(this, request, grants);
  }

  private boolean unsupportedApi(Exception error) {
    return error instanceof HubApiClient.ApiException
            && Arrays.asList(401, 404, 405).contains(((HubApiClient.ApiException) error).statusCode)
        || "Ответ Hub не является JSON API".equals(error.getMessage());
  }

  private void setButtonsEnabled(View view, boolean enabled) {
    if (view instanceof Button) view.setEnabled(enabled);
    if (view instanceof ViewGroup) {
      ViewGroup group = (ViewGroup) view;
      for (int i = 0; i < group.getChildCount(); i++)
        setButtonsEnabled(group.getChildAt(i), enabled);
    }
  }

  private String friendly(Exception e) {
    if (e instanceof javax.net.ssl.SSLException)
      return "Сертификат HTTPS не прошёл проверку. Проверьте домен и сертификат Hub.";
    if (e instanceof java.net.SocketTimeoutException)
      return "Hub не ответил вовремя. Проверьте сеть и повторите.";
    if (e instanceof java.net.UnknownHostException)
      return "Не удалось найти сервер. Проверьте адрес Hub.";
    return e.getMessage() == null ? "Не удалось связаться с Hub" : e.getMessage();
  }

  private void schedule() {
    main.removeCallbacks(refreshTick);
    if (resumed && isDataScreen()) main.postDelayed(refreshTick, 5000);
  }

  private boolean keyboardOpen() {
    if (root == null) return false;
    if (Build.VERSION.SDK_INT >= 30) {
      WindowInsets insets = root.getRootWindowInsets();
      if (insets != null) return insets.isVisible(WindowInsets.Type.ime());
    }
    Rect visibleFrame = new Rect();
    root.getWindowVisibleDisplayFrame(visibleFrame);
    return root.getRootView().getHeight() - visibleFrame.height() > ui.dp(120);
  }

  private void hideKeyboard() {
    View f = getCurrentFocus();
    if (f != null)
      ((InputMethodManager) getSystemService(INPUT_METHOD_SERVICE))
          .hideSoftInputFromWindow(f.getWindowToken(), 0);
  }

  private void connection() {
    clearAccountData();
    api = null;
    frame("connect", "OWRT Hub", false);
    space(22);
    add(ui.iconBadge("shield", ui.BLUE));
    space(24);
    title(
        "Ваша сеть.\nВсегда рядом.",
        "Подключите свой Hub для управления роутерами из одного приложения.");
    String origin = "";
    EditText host = ui.field("https://hub.example.com", origin, false);
    host.setId(R.id.hub_host);
    host.setInputType(17);
    host.setGravity(Gravity.CENTER);
    add(ui.labeledField("Адрес Hub", host));
    space(16);
    EditText port = ui.field("443 — по умолчанию", "", false);
    port.setId(R.id.hub_port);
    port.setInputType(2);
    port.setGravity(Gravity.CENTER);
    add(ui.labeledField("Порт", port));
    space(24);
    Button b =
        ui.button(
            "Подключить Hub",
            true,
            () -> {
              if (requesting) return;
              try {
                URI endpoint =
                    HubUrlPolicy.normalizeHub(
                        host.getText().toString(), port.getText().toString(), BuildConfig.DEBUG);
                api = new HubApiClient(endpoint, store);
                call(
                    client -> client.capabilities(),
                    v -> {
                      if (v.optInt("api_version") != 1) {
                        notice(
                            "Hub нужно обновить до сборки с поддержкой нативного API приложения.",
                            true);
                        return;
                      }
                      try {
                        store.saveConnection(api.getHub(), "");
                      } catch (Exception e) {
                        notice(e.getMessage(), true);
                        return;
                      }
                      loginScreen();
                      challenge();
                    });
              } catch (Exception e) {
                notice("Введите правильный HTTPS-адрес и порт.", true);
              }
            });
    b.setId(R.id.hub_connect);
    add(b);
    space(20);
    add(ui.body("Защищённое соединение HTTPS. Учётные данные вводятся на следующем шаге."));
  }

  private void clearAccountData() {
    session = new JSONObject();
    resources = new JSONObject();
    availability = new JSONObject();
    routers = new JSONArray();
    groups = new JSONArray();
    search = "";
    filter = "Все";
    groupFilter = "";
    selectedId = "";
  }

  private void loginScreen() {
    clearAccountData();
    captchaToken = "";
    loginMeta = new JSONObject();
    frame("login", "OWRT Hub", false);
    space(20);
    title("С возвращением", "Войдите в свой Hub. Пароль не сохраняется в приложении.");
    String username = "";
    try {
      username = store.getLastUsername();
    } catch (Exception ignored) {
    }
    LinearLayout loginPage = content;
    credentials = ui.column();
    add(credentials);
    content = credentials;
    user = ui.field("Ваш логин", username, false);
    user.setId(R.id.hub_user);
    user.setGravity(Gravity.CENTER);
    add(ui.labeledField("Логин", user));
    space(15);
    password = ui.field("Ваш пароль", "", true);
    password.setId(R.id.hub_password);
    password.setGravity(Gravity.CENTER);
    add(ui.labeledField("Пароль", password));
    space(15);
    otpBox = ui.column();
    otp = ui.field("6 цифр из приложения", "", false);
    otp.setInputType(2);
    otp.setGravity(Gravity.CENTER);
    otp.setSaveEnabled(false);
    otp.setId(R.id.hub_otp);
    otpBox.addView(ui.labeledField("Код 2FA", otp));
    otpBox.addView(ui.spacer(15));
    otpBox.setVisibility(View.GONE);
    add(otpBox);
    LinearLayout c = ui.card(), row = ui.row();
    captchaCode = ui.text("Загрузка капчи…", 22, true);
    captchaCode.setTextColor(ui.BLUE);
    View captchaBalance = ui.spacer(1);
    row.addView(captchaBalance, new LinearLayout.LayoutParams(ui.dp(48), ui.dp(48)));
    row.addView(captchaCode, new LinearLayout.LayoutParams(0, -2, 1));
    row.addView(ui.iconButton("refresh", "Новая капча", this::challenge));
    c.addView(row);
    c.addView(ui.spacer(8));
    captcha = ui.field("Введите цифры", "", false);
    captcha.setInputType(2);
    captcha.setGravity(Gravity.CENTER);
    captcha.setId(R.id.hub_captcha);
    c.addView(captcha);
    add(c);
    space(20);
    Button login =
        ui.button(
            "Войти в Hub",
            true,
            () -> {
              if (requesting) return;
              String u = user.getText().toString().trim(),
                  p = password.getText().toString(),
                  answer = captcha.getText().toString(),
                  code = otp.getText().toString(),
                  token = captchaToken;
              if (u.isEmpty() || p.isEmpty()) {
                notice("Введите логин и пароль.", true);
                return;
              }
              if (token.isEmpty()) {
                notice("Дождитесь загрузки капчи.", true);
                return;
              }
              hideKeyboard();
              call(
                  client -> client.login(u, p, token, answer, code),
                  v -> {
                    password.setText("");
                    otp.setText("");
                    session = v;
                    refresh(true);
                  });
            });
    login.setId(R.id.hub_login);
    add(login);
    content = loginPage;
    browserSignIn =
        ui.button(
            "Пройти защищённый вход",
            true,
            () -> {
              browserLogin = true;
              openWeb("/login");
            });
    browserSignIn.setVisibility(View.GONE);
    add(browserSignIn);
    space(12);
    add(ui.button("Другой сервер", NativeUi.Style.QUIET, this::connection));
    user.addTextChangedListener(
        new TextWatcher() {
          public void beforeTextChanged(CharSequence s, int a, int c, int f) {}

          public void onTextChanged(CharSequence s, int a, int b, int c) {
            updateOtp();
          }

          public void afterTextChanged(Editable e) {}
        });
  }

  private void challenge() {
    if (api == null) return;
    call(
        client -> {
          JSONObject v = client.loginChallenge();
          v.put("meta", client.get("/api/auth/meta"));
          return v;
        },
        v -> {
          if (v.optBoolean("browser_login_required")) {
            captchaToken = "";
            credentials.setVisibility(View.GONE);
            browserSignIn.setVisibility(View.VISIBLE);
            notice(
                "Для reCAPTCHA откройте защищённый вход. После авторизации приложение вернёт вас на"
                    + " нативный экран.",
                false);
          } else {
            credentials.setVisibility(View.VISIBLE);
            browserSignIn.setVisibility(View.GONE);
            JSONObject c = obj(v, "captcha");
            captchaCode.setText(str(c, "code", "Капча"));
            captchaToken = c.optString("token");
            captcha.setText("");
          }
          loginMeta = obj(v, "meta");
          updateOtp();
        });
  }

  private void updateOtp() {
    JSONObject auth = loginMeta.optJSONObject("auth");
    if (auth == null) auth = loginMeta;
    String name = auth.optString("username");
    otpBox.setVisibility(
        auth.optBoolean("totp_enabled")
                && (name.isEmpty() || name.equals(user.getText().toString().trim()))
            ? View.VISIBLE
            : View.GONE);
  }

  private void refresh(boolean navigate) {
    if (api == null) return;
    if (requesting) {
      schedule();
      return;
    }
    String destination = screen;
    boolean isOwner = owner();
    call(
        client -> {
          JSONObject currentSession = client.session();
          JSONObject data = client.get("/api/routers?availability=1");
          data.put("session_meta", currentSession);
          if (obj(currentSession, "account").optBoolean("is_owner"))
            try {
              data.put("resources", client.get("/api/vps/resources"));
            } catch (HubApiClient.AuthRequiredException expired) {
              throw expired;
            } catch (java.io.IOException unavailable) {
              data.put("resources", resources);
              data.put("partial_warning", "Роутеры обновлены. Ресурсы VPS временно недоступны.");
            }
          if (obj(currentSession, "account").optBoolean("is_owner"))
            try {
              data.put("hub_update", client.get("/api/hub-update"));
            } catch (HubApiClient.AuthRequiredException expired) {
              throw expired;
            } catch (java.io.IOException ignored) {
            }
          try {
            data.put("groups", client.get("/api/router-groups").optJSONArray("groups"));
          } catch (HubApiClient.AuthRequiredException expired) {
            throw expired;
          } catch (java.io.IOException unavailable) {
            data.put("groups", groups);
            data.put("partial_warning", "Роутеры обновлены. Список групп временно недоступен.");
          }
          return data;
        },
        value -> {
          boolean accountChanged =
              !obj(session, "account")
                  .toString()
                  .equals(obj(obj(value, "session_meta"), "account").toString());
          session = obj(value, "session_meta");
          if (accountChanged) {
            if (destination.equals("overview")) {
              overview();
            } else if (destination.equals("router")) {
              detail(selectedId);
            } else if (destination.equals("routers")) {
              routerList();
            }
          }
          routers = value.optJSONArray("routers");
          if (routers == null) routers = new JSONArray();
          groups = value.optJSONArray("groups");
          if (groups == null) groups = new JSONArray();
          availability = obj(value, "availability");
          resources = obj(value, "resources");
          hubUpdateStatus = obj(value, "hub_update");
          if (!navigate && (destination.equals("overview") || destination.equals("router"))) {
            updateVisibleValues(destination);
          } else if (!navigate && destination.equals("routers") && routerResults != null) {
            LinearLayout replacement = ui.column();
            renderList(replacement, search);
            ViewUpdates.patchChildren(routerResults, replacement);
          } else {
            if (navigate || destination.equals("overview")) overview();
            else if (destination.equals("router")) detail(selectedId);
            else if (destination.equals("settings")) settings();
          }
          if (resumed) HubNotifications.offer(this, owner());
          if (!value.optString("partial_warning").isEmpty())
            notice(value.optString("partial_warning"), true);
          schedule();
        },
        !navigate);
  }

  private void updateVisibleValues(String destination) {
    LinearLayout attached = content;
    updatingViews = true;
    try {
      if (destination.equals("overview")) overview();
      else detail(selectedId);
      ViewUpdates.patchChildren(attached, content);
    } finally {
      content = attached;
      updatingViews = false;
    }
  }

  private int online() {
    int n = 0;
    for (int i = 0; i < routers.length(); i++)
      if (routers.optJSONObject(i).optBoolean("online")) n++;
    return n;
  }

  private int xrayRunning() {
    int count = 0;
    for (int i = 0; i < routers.length(); i++) {
      JSONObject router = routers.optJSONObject(i);
      if (router != null
          && router.optBoolean("online")
          && obj(router, "status").optString("xray").equals("running")) count++;
    }
    return count;
  }

  private void overview() {
    frame("overview", "OWRT Hub", true);
    title(
        "Всё под контролем", str(obj(session, "account"), "username", "Ваш Hub") + " · Обзор сети");
    LinearLayout counts = ui.row();
    metric(counts, "Всего роутеров", routers.length(), ui.TEXT);
    metric(counts, "В сети", online(), ui.GREEN);
    add(counts);
    space(12);
    LinearLayout moreCounts = ui.row();
    metric(moreCounts, "Не в сети", routers.length() - online(), ui.RED);
    metric(moreCounts, "Xray запущен", xrayRunning(), ui.GREEN);
    add(moreCounts);
    space(18);
    LinearLayout graph = ui.card(), top = ui.row();
    top.addView(ui.text("Доступность роутеров", 16, true), new LinearLayout.LayoutParams(0, -2, 1));
    top.addView(ui.chip("24 ч", ui.MUTED));
    graph.addView(top);
    graph.addView(ui.spacer(14));
    ChartView chart = new ChartView(this, ui);
    List<ChartView.Point> pts = new ArrayList<>();
    JSONArray a = availability.optJSONArray("points");
    if (a != null)
      for (int i = 0; i < a.length(); i++) {
        JSONArray p = a.optJSONArray(i);
        if (p != null) pts.add(new ChartView.Point(p.optLong(0) * 1000, p.optInt(1), p.optInt(2)));
      }
    long end = availability.optLong("to", System.currentTimeMillis() / 1000) * 1000;
    chart.setData(pts, availability.optLong("from", end / 1000 - 86400) * 1000, end);
    chart.setExpectedIntervalMillis(availability.optLong("interval", 30) * 1000);
    chart.setGapAfterMillis(availability.optLong("gap_after", 0) * 1000);
    graph.addView(chart, new LinearLayout.LayoutParams(-1, ui.dp(170)));
    graph.addView(ui.label("По Heartbeat · пропуски без выдуманных данных"));
    add(graph);
    space(18);
    if (owner()) {
      LinearLayout r = ui.card();
      r.addView(ui.text("Ресурсы VPS", 16, true));
      r.addView(ui.spacer(16));
      resource(r, "ЦП", resources.opt("cpu"), ui.BLUE);
      resource(r, "ОЗУ", resources.opt("memory"), ui.GREEN);
      resource(r, "Диск", resources.opt("disk"), ui.AMBER);
      add(r);
      space(18);
    }
    LinearLayout h = ui.row();
    h.addView(ui.text("Ваши роутеры", 18, true), new LinearLayout.LayoutParams(0, -2, 1));
    h.addView(ui.iconButton("router", "Все роутеры", this::routerList));
    add(h);
    for (int i = 0; i < Math.min(routers.length(), 3); i++) routerCard(routers.optJSONObject(i));
    if (routers.length() == 0)
      empty("Здесь появятся роутеры", "Добавьте устройство и подключите агент к Hub.");
    space(14);
    add(ui.button("Обновить состояние", false, () -> refresh(false)));
    schedule();
  }

  private void metric(LinearLayout row, String label, int value, int color) {
    LinearLayout c = ui.card();
    c.setGravity(Gravity.CENTER);
    c.setMinimumHeight(ui.dp(92));
    c.setPadding(ui.dp(15), ui.dp(16), ui.dp(15), ui.dp(16));
    TextView caption = ui.text(label, 14, false);
    caption.setTextColor(ui.MUTED);
    c.addView(caption);
    c.addView(ui.spacer(18), new LinearLayout.LayoutParams(-1, ui.dp(18), 1));
    TextView n = ui.text(String.valueOf(value), 28, true);
    n.setTextColor(color);
    c.addView(n);
    LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(0, -1, 1);
    if (row.getChildCount() > 0) p.setMargins(ui.dp(12), 0, 0, 0);
    row.addView(c, p);
  }

  private void resource(LinearLayout c, String name, Object raw, int color) {
    double p =
        raw instanceof Number
            ? ((Number) raw).doubleValue()
            : raw instanceof JSONObject ? ((JSONObject) raw).optDouble("percent", -1) : -1;
    if (Double.isNaN(p) || Double.isInfinite(p)) p = -1;
    p = Math.min(100, p);
    LinearLayout row = ui.row();
    row.addView(ui.label(name), new LinearLayout.LayoutParams(0, -2, 1));
    row.addView(
        ui.text(p < 0 ? "—" : String.format(Locale.getDefault(), "%.1f%%", p), 14, true),
        new LinearLayout.LayoutParams(-2, -2));
    c.addView(row);
    c.addView(ui.spacer(8));
    ProgressBar bar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
    bar.setMax(1000);
    bar.setProgress((int) Math.max(0, p * 10));
    bar.setProgressTintList(android.content.res.ColorStateList.valueOf(color));
    bar.setProgressBackgroundTintList(android.content.res.ColorStateList.valueOf(ui.LINE));
    c.addView(bar, new LinearLayout.LayoutParams(-1, ui.dp(5)));
    c.addView(ui.spacer(18));
  }

  private String groupName(String id) {
    for (int i = 0; i < groups.length(); i++)
      if (groups.optJSONObject(i).optString("id").equals(id))
        return groups.optJSONObject(i).optString("name");
    return "Без группы";
  }

  private void routerList() {
    frame("routers", "Роутеры", true);
    LinearLayout h = ui.row();
    h.addView(ui.title("Моя сеть"), new LinearLayout.LayoutParams(0, -2, 1));
    if (owner() || obj(session, "account").optBoolean("can_add_router"))
      h.addView(ui.iconButton("plus", "Добавить роутер", () -> settingsTool("addRouter")));
    add(h);
    space(16);
    EditText field = ui.field("Поиск по имени или ID", search, false);
    field.setId(R.id.native_search);
    add(field);
    space(14);
    LinearLayout filters = ui.row();
    for (String f : new String[] {"Все", "Онлайн", "Офлайн"}) {
      Button b =
          ui.button(
              f,
              filter.equals(f) ? NativeUi.Style.PRIMARY : NativeUi.Style.QUIET,
              () -> {
                search = field.getText().toString();
                filter = f;
                routerList();
              });
      LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(0, -2, 1);
      p.setMargins(0, 0, ui.dp(6), 0);
      filters.addView(b, p);
    }
    add(filters);
    space(8);
    if (groups.length() > 0) {
      add(
          ui.button(
              groupFilter.isEmpty() ? "Все группы" : groupName(groupFilter),
              false,
              () -> {
                List<String> names = new ArrayList<>(), ids = new ArrayList<>();
                names.add("Все группы");
                ids.add("");
                for (int i = 0; i < groups.length(); i++) {
                  JSONObject g = groups.optJSONObject(i);
                  names.add(g.optString("name"));
                  ids.add(g.optString("id"));
                }
                new AlertDialog.Builder(this)
                    .setTitle("Группа")
                    .setItems(
                        names.toArray(new String[0]),
                        (d, n) -> {
                          groupFilter = ids.get(n);
                          routerList();
                        })
                    .show();
              }));
      space(6);
    }
    LinearLayout list = ui.column();
    routerResults = list;
    add(list);
    renderList(list, search);
    field.addTextChangedListener(
        new TextWatcher() {
          public void beforeTextChanged(CharSequence s, int a, int c, int f) {}

          public void onTextChanged(CharSequence s, int a, int b, int c) {
            search = s.toString();
            renderList(list, search);
          }

          public void afterTextChanged(Editable e) {}
        });
    schedule();
  }

  private void renderList(LinearLayout list, String query) {
    list.removeAllViews();
    LinearLayout previous = content;
    content = list;
    int count = 0;
    for (int i = 0; i < routers.length(); i++) {
      JSONObject r = routers.optJSONObject(i);
      boolean on = r.optBoolean("online");
      String text = (r.optString("name") + " " + r.optString("id")).toLowerCase(Locale.ROOT);
      if (!text.contains(query.toLowerCase(Locale.ROOT))
          || filter.equals("Онлайн") && !on
          || filter.equals("Офлайн") && on
          || !groupFilter.isEmpty() && !groupFilter.equals(r.optString("group_id"))) continue;
      routerCard(r);
      count++;
    }
    if (count == 0) empty("Роутеры не найдены", "Измените поиск или фильтр.");
    content = previous;
  }

  private void routerCard(JSONObject r) {
    space(10);
    LinearLayout card = ui.card(), row = ui.row();
    card.setTag("router:" + r.optString("id"));
    row.addView(ui.iconBadge("router", r.optBoolean("online") ? ui.GREEN : ui.MUTED));
    LinearLayout n = ui.column();
    n.setPadding(ui.dp(12), 0, ui.dp(8), 0);
    n.addView(ui.text(str(r, "name", r.optString("id")), 16, true));
    TextView id =
        ui.text(r.optString("id") + " · " + groupName(r.optString("group_id")), 12, false);
    id.setTextColor(ui.MUTED);
    n.addView(ui.spacer(5));
    n.addView(id);
    row.addView(n, new LinearLayout.LayoutParams(0, -2, 1));
    row.addView(
        ui.chip(
            r.optBoolean("online") ? "Онлайн" : "Офлайн",
            r.optBoolean("online") ? ui.GREEN : ui.RED));
    card.addView(row);
    card.addView(ui.spacer(16));
    String x = obj(r, "status").optString("xray");
    card.addView(
        ui.text(
            "Xray  ·  "
                + (x.equals("running")
                    ? "Запущен"
                    : x.equals("stopped") ? "Остановлен" : "Нет данных"),
            13,
            false));
    String preview = str(r, "notes_preview", "");
    if (preview.isEmpty()) preview = str(r, "notes", "");
    int noteCount = r.optInt("notes_count", 0);
    if (!preview.isEmpty() || noteCount > 0 || r.optBoolean("has_notes")) {
      card.addView(ui.spacer(10));
      TextView note =
          ui.text("Заметки" + (noteCount > 0 ? " · " + noteCount : "") + "\n" + preview, 13, false);
      note.setMaxLines(4);
      note.setEllipsize(TextUtils.TruncateAt.END);
      card.addView(note);
      if (obj(r, "permissions").optBoolean("notes")) note.setOnClickListener(v -> tool("notes", r));
    }
    card.setOnClickListener(v -> detail(r.optString("id")));
    card.setFocusable(true);
    card.setContentDescription("Открыть роутер " + r.optString("name"));
    add(card);
  }

  private JSONObject selected() {
    for (int i = 0; i < routers.length(); i++)
      if (routers.optJSONObject(i).optString("id").equals(selectedId))
        return routers.optJSONObject(i);
    return null;
  }

  private void detail(String id) {
    selectedId = id;
    JSONObject r = selected();
    if (r == null) {
      routerList();
      notice("Роутер больше недоступен", true);
      return;
    }
    frame("router", str(r, "name", id), true);
    LinearLayout hero = ui.card();
    hero.setGravity(Gravity.CENTER_HORIZONTAL);
    hero.setTag("router-hero");
    hero.addView(ui.iconBadge("router", ui.BLUE));
    hero.addView(ui.spacer(14));
    hero.addView(ui.text(str(r, "name", id), 24, true));
    hero.addView(ui.spacer(12));
    hero.addView(
        ui.chip(
            r.optBoolean("online") ? "● Heartbeat активен" : "● Нет связи с агентом",
            r.optBoolean("online") ? ui.GREEN : ui.RED));
    hero.addView(ui.spacer(12));
    hero.addView(ui.body(str(obj(r, "status"), "model", "Модель не передана")));
    add(hero);
    space(18);
    JSONObject s = obj(r, "status"), p = obj(r, "permissions");
    LinearLayout stats = ui.card();
    stats.setGravity(Gravity.CENTER_HORIZONTAL);
    stats.setTag("router-stats");
    stats.addView(ui.text("Состояние устройства", 16, true));
    kv(
        stats,
        "Xray",
        s.optString("xray").equals("running")
            ? "Запущен"
            : s.optString("xray").equals("stopped") ? "Остановлен" : "Нет данных");
    kv(stats, "Система", str(s, "release", "—"));
    kv(stats, "Время работы", uptime(s.opt("uptime")));
    kv(stats, "Память", str(s, "memory", "—"));
    kv(stats, "Flash", str(s, "flash", "—"));
    kv(stats, "Температура", str(s, "temperature", "—"));
    kv(stats, "Нагрузка", str(s, "load", "—"));
    add(stats);
    space(18);
    if (p.optBoolean("manage"))
      action(
          "play",
          "Remote управление Xray",
          "Запуск, остановка и журнал через Heartbeat",
          () -> tool("xray", selected()));
    if (p.optBoolean("admin"))
      action(
          "router",
          "Админка OpenWrt",
          "Веб-интерфейс роутера",
          () -> openWeb(r.optString("access_url", "/access/" + enc(id) + "/")));
    JSONObject t = obj(r, "tachyon");
    if (t.optBoolean("installed") && p.optBoolean("admin"))
      action(
          "crown",
          "Tachyon " + t.optString("version"),
          t.optBoolean("online") ? "Online" : "Offline",
          () -> openWeb(t.optString("url", "/tachyon/" + enc(id) + "/")));
    if (p.optBoolean("ssh"))
      action(
          "terminal", "SSH-терминал", "Консоль выбранного роутера", () -> tool("ssh", selected()));
    if (p.optBoolean("logs"))
      action("grid", "Глобальные логи", "Состояние агента и Xray", () -> tool("logs", selected()));
    if (p.optBoolean("notes"))
      action(
          "grid",
          "Заметки",
          str(r, "notes_preview", "Записи об устройстве"),
          () -> tool("notes", selected()));
    if (p.optBoolean("wol"))
      action(
          "wifi",
          "Wake-on-LAN",
          "Включение устройства в локальной сети",
          () -> tool("wol", selected()));
    if (p.optBoolean("config"))
      action(
          "key",
          "OpenWrt config",
          "Команды подключения и конфигурация",
          () -> tool("config", selected()));
    if (p.optBoolean("manage"))
      action("settings", "Настройки роутера", "Изменить имя", () -> rename(selected()));
    schedule();
  }

  private String uptime(Object value) {
    if (value == null || value == JSONObject.NULL) return "—";
    try {
      long seconds = Long.parseLong(String.valueOf(value));
      return (seconds / 86400)
          + " д "
          + (seconds / 3600 % 24)
          + " ч "
          + (seconds / 60 % 60)
          + " мин";
    } catch (Exception e) {
      return value == null ? "—" : String.valueOf(value);
    }
  }

  private void kv(LinearLayout c, String k, String v) {
    c.addView(ui.spacer(15));
    LinearLayout row = ui.row();
    TextView l = ui.text(k, 14, false);
    l.setTextColor(ui.MUTED);
    l.setGravity(Gravity.CENTER);
    row.addView(l, new LinearLayout.LayoutParams(0, -2, 1));
    TextView value = ui.text(v, 14, true);
    value.setGravity(Gravity.CENTER);
    row.addView(value, new LinearLayout.LayoutParams(0, -2, 1));
    c.addView(row);
  }

  private void action(String icon, String title, String subtitle, Runnable run) {
    LinearLayout card = ui.card(), row = ui.row();
    card.setTag("action:" + icon);
    card.setPadding(ui.dp(14), ui.dp(14), ui.dp(10), ui.dp(14));
    row.addView(ui.iconBadge(icon, ui.BLUE));
    LinearLayout labels = ui.column();
    labels.setPadding(ui.dp(12), 0, ui.dp(8), 0);
    labels.addView(ui.text(title, 15, true));
    if (subtitle != null && !subtitle.isEmpty()) {
      labels.addView(ui.spacer(5));
      TextView s = ui.text(subtitle, 12, false);
      s.setTextColor(ui.MUTED);
      labels.addView(s);
    }
    row.addView(labels, new LinearLayout.LayoutParams(0, -2, 1));
    row.addView(ui.icon("chevron", ui.MUTED));
    card.addView(row);
    card.setOnClickListener(v -> run.run());
    card.setContentDescription(title);
    card.setFocusable(true);
    add(card);
    space(10);
  }

  private void empty(String title, String text) {
    LinearLayout c = ui.card();
    c.addView(ui.text(title, 17, true));
    c.addView(ui.spacer(8));
    c.addView(ui.body(text));
    add(c);
    space(14);
  }

  private void rename(JSONObject r) {
    EditText name = ui.field("Название", r.optString("name"), false);
    new AlertDialog.Builder(this)
        .setTitle("Имя роутера")
        .setView(name)
        .setNegativeButton("Отмена", null)
        .setPositiveButton(
            "Сохранить",
            (d, n) -> {
              String routerId = r.optString("id"), proposed = name.getText().toString();
              call(
                  client ->
                      client.post(
                          "/api/router/" + enc(routerId) + "/rename", json("name", proposed)),
                  v -> refresh(false));
            })
        .show();
  }

  private void tool(String tool, JSONObject router) {
    Intent i = new Intent(this, NativeRouterActivity.class);
    i.putExtra("hub_origin", api.getHub().toString());
    i.putExtra("router_json", router.toString());
    i.putExtra("tool", tool);
    startActivity(i);
  }

  private void backups() {
    Intent i = new Intent(this, NativeBackupActivity.class);
    i.putExtra("hub_origin", api.getHub().toString());
    startActivity(i);
  }

  private void settingsTool(String tool) {
    Intent i = new Intent(this, NativeSettingsActivity.class);
    i.putExtra("hub_origin", api.getHub().toString());
    i.putExtra("tool", tool);
    startActivity(i);
  }

  private void openWeb(String path) {
    try {
      URI target = api.getHub().resolve(path);
      if (!HubUrlPolicy.sameOrigin(api.getHub(), target.toString()))
        throw new IllegalArgumentException();
      Intent i = new Intent(this, BrowserActivity.class);
      i.putExtra("hub_origin", api.getHub().toString());
      i.putExtra("tool_url", target.toString());
      i.putExtra("authentication", browserLogin && path.equals("/login"));
      i.putExtra("session_cookie", api.exportCookieForWebView());
      startActivity(i);
    } catch (Exception e) {
      notice("Недопустимый адрес инструмента Hub", true);
    }
  }

  private void settings() {
    frame("settings", "Настройки", true);
    TextView settingsTitle = ui.text("Ваш Hub", 16, true);
    settingsTitle.setTag("settings-heading");
    add(settingsTitle);
    space(6);
    add(ui.label("Подключение, безопасность и оформление"));
    space(14);
    LinearLayout c = ui.card();
    c.setGravity(Gravity.CENTER_HORIZONTAL);
    c.setTag("account-card");
    c.setPadding(ui.dp(14), ui.dp(12), ui.dp(14), ui.dp(12));
    if (owner()) {
      View crown = ui.iconBadge("crown", ui.AMBER, 32);
      crown.setContentDescription("Владелец Hub · золотая корона");
      c.addView(crown);
      c.addView(ui.spacer(6));
    }
    c.addView(ui.text(str(obj(session, "account"), "username", "Пользователь"), 15, true));
    c.addView(ui.spacer(5));
    c.addView(ui.text(owner() ? "Владелец Hub" : "Доступ по назначенным правам", 13, false));
    add(c);
    space(18);
    action(
        "server",
        "Подключение",
        "Адрес скрыт",
        () -> {
          frame("connectionInfo", "Подключение", false);
          title("Сервер Hub", api.getHub().toString());
          add(ui.chip("Сессия зашифрована в Android", ui.GREEN));
          space(24);
          add(
              ui.button(
                  "Подключить другой Hub",
                  false,
                  () ->
                      confirm(
                          "Сменить Hub?", "Текущая сессия будет завершена.", () -> logout(true))));
        });
    action(
        "settings",
        "Оформление",
        prefs.getBoolean("winter", false) ? "Зимняя тема" : "Графитовая тема",
        this::design);
    if (owner() || obj(session, "account").optBoolean("can_add_router"))
      action(
          "plus",
          "Добавить роутер",
          "Автоматические параметры и настройка OpenWrt",
          () -> settingsTool("addRouter"));
    if (owner())
      action("bell", "Уведомления", "События Hub на телефоне", () -> settingsTool("notifications"));
    action("shield", "Активные сессии", "Контроль входов в Hub", () -> settingsTool("sessions"));
    if (owner()) {
      action(
          "shield",
          "Пользователи и доступ",
          "Права доступа к выбранным роутерам",
          () -> settingsTool("users"));
      action("server", "Резервные копии", "Сохранение и восстановление Hub", this::backups);
      action(
          "key", "Безопасность", "Пароль и двухфакторная защита", () -> settingsTool("security"));
      action("router", "Группы роутеров", groups.length() + " групп", () -> settingsTool("groups"));
      action(
          "refresh",
          "Обновление Hub",
          hubUpdateStatus.optBoolean("available")
              ? "Доступно обновление " + hubUpdateStatus.optString("remote_version")
              : "Проверка и установка новой сборки",
          () -> settingsTool("hubUpdate"));
      action(
          "refresh",
          "Обновить роутеры",
          "Последовательное обновление и перезапуск",
          () -> settingsTool("routerUpdates"));
      action(
          "terminal", "Терминал VPS", "Root-консоль сервера", () -> tool("vps", new JSONObject()));
    }
    action(
        "crown",
        "Поддержать разработку",
        "Спасибо за поддержку проекта",
        () -> settingsTool("donate"));
    action("grid", "О приложении", BuildConfig.VERSION_NAME, () -> settingsTool("about"));
    space(12);
    add(
        ui.button(
            "Выйти из Hub",
            NativeUi.Style.DANGER,
            () ->
                confirm(
                    "Выйти из Hub?",
                    "Сохранённая сессия будет удалена с устройства.",
                    () -> logout(false))));
  }

  private void design() {
    frame("design", "Оформление", false);
    title("Ваш стиль", "Тема применяется ко всем нативным экранам приложения.");
    for (String theme : new String[] {"Графитовый", "Зимний"})
      action(
          "grid",
          theme,
          theme.equals("Графитовый")
              ? "Спокойные тёмные оттенки"
              : "Ледяные оттенки и голубые акценты",
          () -> {
            prefs.edit().putBoolean("winter", theme.equals("Зимний")).apply();
            ui = new NativeUi(this, theme.equals("Зимний"));
            design();
          });
    empty(
        "Лёгкий интерфейс",
        "Без браузерных эффектов и фоновых анимаций. Основные экраны состоят из нативных элементов"
            + " Android.");
  }

  private void confirm(String title, String text, Runnable yes) {
    new AlertDialog.Builder(this)
        .setTitle(title)
        .setMessage(text)
        .setNegativeButton("Отмена", null)
        .setPositiveButton("Подтвердить", (d, n) -> yes.run())
        .show();
  }

  private void logout(boolean other) {
    call(
        client -> {
          try {
            client.logout();
            return json("confirmed", true);
          } catch (Exception unavailable) {
            client.clearSession();
            return json("confirmed", false);
          }
        },
        value -> {
          CookieManager.getInstance()
              .removeAllCookies(ignored -> CookieManager.getInstance().flush());
          session = new JSONObject();
          if (other) connection();
          else {
            loginScreen();
            challenge();
          }
          if (!value.optBoolean("confirmed"))
            notice(
                "Локальный выход выполнен. Hub недоступен: завершение сессии на сервере не"
                    + " подтверждено.",
                true);
        });
  }
}
