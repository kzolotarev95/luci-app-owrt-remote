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
  private boolean routerOrderSupported, routerDragging;
  private String draggedRouter = "";
  private int dragScrollDelta;
  private final Runnable dragScroll = new Runnable() {
    @Override public void run() {
      if (!routerDragging || scrollView == null) return;
      if (dragScrollDelta != 0) scrollView.scrollBy(0, dragScrollDelta);
      main.postDelayed(this, 32);
    }
  };
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
      mapExpanded;
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
    main.removeCallbacks(dragScroll);
    routerDragging = false;
    draggedRouter = "";
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
    NativeEffects.install(this, root);
    root.requestApplyInsets();
    LinearLayout header = ui.row();
    header.setTag("native-toolbar");
    header.setPadding(ui.dp(6), ui.dp(4), ui.dp(6), ui.dp(4));
    if (bottom && Arrays.asList("overview", "routers", "settings").contains(name))
      header.addView(ui.iconButton("menu", "Открыть меню Hub", this::openNavigation));
    else if (Arrays.asList("connect", "login").contains(name)) header.addView(ui.logoBadge(40));
    if (!Arrays.asList("connect", "login").contains(name)) header.addView(ui.logoBadge(26));
    LinearLayout labels = ui.column();
    labels.setPadding(ui.dp(10), 0, 0, 0);
    TextView heading =
        ui.text(
            name.equals("overview")
                ? "OWRT Hub " + hubUpdateStatus.optString("current_version", "v125")
                : title,
            18,
            true);
    heading.setGravity(Gravity.START);
    heading.setSingleLine(true);
    heading.setEllipsize(TextUtils.TruncateAt.END);
    labels.addView(heading);
    if (api == null) {
      TextView sub = ui.text("Ваша сеть всегда рядом", 12, false);
      sub.setGravity(Gravity.START);
      sub.setTextColor(ui.MUTED);
      labels.addView(ui.spacer(4));
      labels.addView(sub);
    }
    header.addView(labels, new LinearLayout.LayoutParams(0, -2, 1));
    if (api != null && api.hasSession() && owner())
      header.addView(ui.iconButton("bell", "Уведомления", () -> settingsTool("notifications")));
    if (!name.equals("login")) {
      root.addView(header);
      root.addView(ui.compactDivider());
    }
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
    content.setPadding(ui.dp(14), ui.dp(14), ui.dp(14), ui.dp(20));
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
    if (view instanceof Button) {
      boolean allowed = enabled;
      if (view.getTag() instanceof String && ((String) view.getTag()).startsWith("router-order:")) {
        String[] parts = ((String) view.getTag()).split(":");
        List<String> peers = routerOrderPeers(parts[1]);
        int direction = Integer.parseInt(parts[2]), index = peers.indexOf(parts[1]);
        allowed &= routerOrderSupported && (direction == 0 ? peers.size() > 1 : direction < 0 ? index > 0 : index >= 0 && index < peers.size() - 1);
      }
      view.setEnabled(allowed);
    }
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
    if (resumed && isDataScreen())
      main.postDelayed(refreshTick, prefs.getBoolean("lite", false) ? 10000 : 5000);
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
    space(12);
    add(ui.logoBadge(48));
    space(16);
    title("Подключение к Hub", "Управление вашей сетью. С любого расстояния.");
    LinearLayout connectionPage = content;
    LinearLayout connectCard = ui.card();
    add(connectCard);
    content = connectCard;
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
    TextView security = ui.text("Защищённое соединение HTTPS", 12, false);
    security.setTextColor(ui.GREEN);
    add(security);
    content = connectionPage;
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
    mapExpanded = false;
  }

  private void loginScreen() {
    clearAccountData();
    captchaToken = "";
    loginMeta = new JSONObject();
    frame("login", "OWRT Hub", false);
    space(10);
    LinearLayout welcomeBrand = ui.column();
    welcomeBrand.setGravity(Gravity.CENTER_HORIZONTAL);
    View welcomeLogo = ui.logoBadge(64);
    welcomeLogo.setTag("welcome-logo");
    welcomeBrand.addView(welcomeLogo);
    add(welcomeBrand);
    space(14);
    title("С возвращением", "Вход в OWRT Hub");
    String username = "";
    try {
      username = store.getLastUsername();
    } catch (Exception ignored) {
    }
    LinearLayout loginPage = content;
    credentials = ui.card();
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
    if (requesting || routerDragging) {
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
          routerOrderSupported = value.optBoolean("router_order_supported");
          groups = value.optJSONArray("groups");
          if (groups == null) groups = new JSONArray();
          availability = obj(value, "availability");
          resources = obj(value, "resources");
          hubUpdateStatus = obj(value, "hub_update");
          if (!navigate && (destination.equals("overview") || destination.equals("router"))) {
            updateVisibleValues(destination);
          } else if (!navigate && destination.equals("routers") && routerResults != null && !routerDragging) {
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
    sectionHeading(
        "Дашборд",
        online() == routers.length() ? "Все роутеры на связи" : "Есть роутеры без связи");
    TextView summary =
        ui.text(online() + " из " + routers.length() + " на связи · данные от агентов", 12, false);
    summary.setTextColor(ui.MUTED);
    summary.setGravity(Gravity.CENTER);
    add(summary);
    space(14);
    LinearLayout stats = ui.column();
    stats.setTag("dashboard-statistics");
    stats.setVisibility(prefs.getBoolean("statsVisible", true) ? View.VISIBLE : View.GONE);
    LinearLayout previous = content;
    content = stats;
    LinearLayout counts = ui.row();
    metric(counts, "Всего роутеров", routers.length(), ui.TEXT);
    metric(counts, "В сети", online(), ui.GREEN);
    add(counts);
    space(8);
    LinearLayout more = ui.row();
    metric(more, "Не в сети", routers.length() - online(), ui.RED);
    metric(more, "Xray запущен", xrayRunning(), ui.BLUE);
    add(more);
    if (owner()) {
      space(8);
      LinearLayout host = ui.row();
      percentMetric(host, "ЦП VPS", resources.opt("cpu"), NativeUi.color("#ac94eb"));
      percentMetric(host, "ОЗУ VPS", resources.opt("memory"), NativeUi.color("#73b6ff"));
      add(host);
    }
    space(14);
    content = previous;
    add(stats);
    add(
        new NativeNetworkMap(
            this,
            ui,
            orderedRouters(false),
            mapExpanded,
            panelVisible("map"),
            () -> toggleDashboardPanel("map"),
            this::detail,
            expanded -> {
              mapExpanded = expanded;
              updateVisibleValues("overview");
            }));
    space(14);
    LinearLayout graph = ui.card();
    graph.setTag("availability-panel");
    Button graphToggle = ui.panelToggle(panelVisible("availability"), "Доступность роутеров", () -> toggleDashboardPanel("availability"));
    graphToggle.setTag("panel-toggle:availability");
    graph.addView(ui.panelHeading("availability", "Доступность роутеров", "activity", graphToggle));
    LinearLayout graphBody = ui.column();
    graphBody.setTag("panel-body:availability");
    graphBody.setVisibility(panelVisible("availability") ? View.VISIBLE : View.GONE);
    graph.addView(graphBody);
    graphBody.addView(ui.spacer(12));
    LinearLayout legend = ui.row();
    TextView on = ui.text("● Онлайн " + online(), 11, true);
    on.setTextColor(ui.GREEN);
    on.setGravity(Gravity.START);
    TextView off = ui.text("● Офлайн " + (routers.length() - online()), 11, true);
    off.setTextColor(ui.RED);
    off.setGravity(Gravity.END);
    legend.addView(on, new LinearLayout.LayoutParams(0, -2, 1));
    legend.addView(off, new LinearLayout.LayoutParams(0, -2, 1));
    graphBody.addView(legend);
    int savedHours = prefs.getInt("availabilityHours", 24);
    final int hours = savedHours == 6 || savedHours == 12 ? savedHours : 24;
    LinearLayout periods = ui.row();
    periods.setGravity(Gravity.CENTER);
    periods.setPadding(0, ui.dp(10), 0, ui.dp(4));
    for (final int period : new int[] {6, 12, 24}) {
      Button choice = ui.button(period + " ч", false, () -> {
        prefs.edit().putInt("availabilityHours", period).apply();
        updateVisibleValues("overview");
      });
      choice.setTag("availability-period:" + period);
      choice.setActivated(hours == period);
      choice.setContentDescription("Доступность роутеров за " + period + " ч");
      android.graphics.drawable.StateListDrawable background = new android.graphics.drawable.StateListDrawable();
      background.addState(new int[] {android.R.attr.state_activated}, ui.shape(ui.SOFT, ui.BLUE, 8));
      background.addState(new int[] {}, ui.ripple(ui.PANEL2, ui.LINE, 8));
      choice.setBackground(background);
      choice.setTextColor(hours == period ? ui.BLUE : ui.MUTED);
      LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(0, ui.dp(42), 1);
      params.setMargins(ui.dp(3), 0, ui.dp(3), 0);
      periods.addView(choice, params);
    }
    graphBody.addView(periods);
    ChartView chart = new ChartView(this, ui);
    chart.setTag("availability-chart");
    List<ChartView.Point> pts = new ArrayList<>();
    JSONArray a = availability.optJSONArray("points");
    if (a != null)
      for (int i = 0; i < a.length(); i++) {
        JSONArray point = a.optJSONArray(i);
        if (point != null)
          pts.add(new ChartView.Point(point.optLong(0) * 1000, point.optInt(1), point.optInt(2)));
      }
    long end = availability.optLong("to", System.currentTimeMillis() / 1000) * 1000;
    chart.setData(pts, end - hours * 3600000L, end);
    chart.setExpectedIntervalMillis(availability.optLong("interval", 30) * 1000);
    chart.setGapAfterMillis(availability.optLong("gap_after", 0) * 1000);
    graphBody.addView(chart, new LinearLayout.LayoutParams(-1, ui.dp(150)));
    add(graph);
    space(14);
    if (owner()) {
      diskBanner();
      space(14);
    }
    add(ui.button("Обновить состояние", false, () -> refresh(false)));
    schedule();
  }

  private boolean panelVisible(String key) {
    return prefs.getBoolean("panelVisible:" + key, true);
  }

  private void toggleDashboardPanel(String key) {
    prefs.edit().putBoolean("panelVisible:" + key, !panelVisible(key)).apply();
    updateVisibleValues("overview");
  }

  private void sectionHeading(String title, String caption) {
    LinearLayout section = ui.column();
    section.setGravity(Gravity.CENTER_HORIZONTAL);
    section.addView(ui.text(title, 17, true));
    if (caption != null && !caption.isEmpty()) {
      section.addView(ui.spacer(5));
      TextView hint = ui.text(caption, 11, false);
      hint.setTextColor(ui.MUTED);
      section.addView(hint);
    }
    add(section);
    space(8);
  }

  private double percent(Object raw) {
    double n =
        raw instanceof Number
            ? ((Number) raw).doubleValue()
            : raw instanceof JSONObject ? ((JSONObject) raw).optDouble("percent", -1) : -1;
    return Double.isFinite(n) && n >= 0 ? Math.min(n, 100) : -1;
  }

  private void percentMetric(LinearLayout row, String label, Object data, int color) {
    LinearLayout card =
        metricCard(
            row,
            label,
            percent(data) < 0
                ? "—"
                : String.format(Locale.forLanguageTag("ru"), "%.1f%%", percent(data))
                    .replace(",0%", "%"),
            ui.TEXT);
    ProgressBar bar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
    bar.setMax(1000);
    bar.setProgress((int) Math.max(0, percent(data) * 10));
    bar.setProgressTintList(android.content.res.ColorStateList.valueOf(color));
    bar.setProgressBackgroundTintList(android.content.res.ColorStateList.valueOf(ui.LINE));
    card.addView(ui.spacer(5));
    card.addView(bar, new LinearLayout.LayoutParams(-1, ui.dp(3)));
  }

  private LinearLayout metricCard(LinearLayout row, String label, String value, int color) {
    LinearLayout card = ui.card();
    card.setGravity(Gravity.CENTER);
    card.setTag("dashboard-metric:" + label);
    card.setPadding(ui.dp(8), ui.dp(7), ui.dp(8), ui.dp(7));
    card.setMinimumHeight(ui.dp(64));
    TextView caption = ui.text(label, 11, false);
    caption.setTextColor(ui.MUTED);
    card.addView(caption);
    card.addView(ui.spacer(5));
    TextView n = ui.text(value, 22, true);
    n.setTextColor(color);
    card.addView(n);
    LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, -1, 1);
    if (row.getChildCount() > 0) lp.setMargins(ui.dp(8), 0, 0, 0);
    row.addView(card, lp);
    return card;
  }

  private String sizeText(double n) {
    if (!Double.isFinite(n) || n < 0) return "—";
    String[] units = {"Б", "КиБ", "МиБ", "ГиБ", "ТиБ"};
    int i = 0;
    while (n >= 1024 && i < 4) {
      n /= 1024;
      i++;
    }
    return String.format(Locale.forLanguageTag("ru"), "%.1f", n).replace(",0", "") + " " + units[i];
  }

  private void diskBanner() {
    JSONObject disk = obj(resources, "disk");
    LinearLayout card = ui.card();
    card.setTag("disk-banner");
    card.setGravity(Gravity.CENTER_HORIZONTAL);
    Button diskToggle = ui.panelToggle(panelVisible("disk"), "Диск VPS", () -> toggleDashboardPanel("disk"));
    diskToggle.setTag("panel-toggle:disk");
    card.addView(ui.panelHeading("disk", "Диск VPS", "disk", diskToggle));
    LinearLayout diskBody = ui.column();
    diskBody.setGravity(Gravity.CENTER_HORIZONTAL);
    diskBody.setTag("panel-body:disk");
    diskBody.setVisibility(panelVisible("disk") ? View.VISIBLE : View.GONE);
    card.addView(diskBody);
    diskBody.addView(ui.spacer(18));
    diskBody.addView(new DiskGaugeView(this, ui, percent(disk)));
    diskBody.addView(ui.spacer(18));
    LinearLayout values = ui.row();
    values.setTag("disk-values");
    values.setGravity(Gravity.CENTER);
    double used = disk.optDouble("used_bytes", -1),
        total = disk.optDouble("total_bytes", -1),
        free = disk.optDouble("free_bytes", used >= 0 && total >= used ? total - used : -1);
    diskValue(values, "Свободно", sizeText(free), ui.AMBER);
    diskValue(values, "Занято", sizeText(used), ui.TEXT);
    diskValue(values, "Всего", sizeText(total), ui.TEXT);
    diskBody.addView(values);
    add(card);
  }

  private void diskValue(LinearLayout box, String label, String value, int color) {
    LinearLayout column = ui.column();
    column.setGravity(Gravity.CENTER_HORIZONTAL);
    column.setPadding(ui.dp(8), 0, ui.dp(8), 0);
    TextView key = ui.text(label, 12, false);
    key.setTextColor(ui.MUTED);
    column.addView(key, new LinearLayout.LayoutParams(-2, -2));
    column.addView(ui.spacer(4));
    TextView text = ui.text(value, 12, true);
    text.setTextColor(color);
    column.addView(text, new LinearLayout.LayoutParams(-2, -2));
    box.addView(column, new LinearLayout.LayoutParams(-2, -2));
  }

  private void metric(LinearLayout row, String label, int value, int color) {
    metricCard(row, label, String.valueOf(value), color);
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
    TextView orderHint = ui.text(routerOrderSupported
        ? "Удерживайте ✥ для перетаскивания или используйте стрелки. Порядок сохраняется в Hub."
        : "Для перестановки карточек обновите панель Hub.", 11, false);
    orderHint.setTextColor(ui.MUTED);
    add(orderHint);
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

  private List<JSONObject> orderedRouters(boolean filtered) {
    List<JSONObject> rows = new ArrayList<>();
    for (int i = 0; i < routers.length(); i++) {
      JSONObject r = routers.optJSONObject(i);
      if (r == null) continue;
      String value = (r.optString("name") + " " + r.optString("id")).toLowerCase(Locale.ROOT);
      boolean on = r.optBoolean("online");
      if (filtered
          && (!value.contains(search.toLowerCase(Locale.ROOT))
              || filter.equals("Онлайн") && !on
              || filter.equals("Офлайн") && on
              || !groupFilter.isEmpty() && !groupFilter.equals(r.optString("group_id")))) continue;
      rows.add(r);
    }
    java.text.Collator order = java.text.Collator.getInstance(Locale.forLanguageTag("ru"));
    rows.sort(
        (a, b) -> {
          int group =
              filtered && !filter.equals("Все")
                  ? 0
                  : order.compare(
                      groupName(a.optString("group_id")), groupName(b.optString("group_id")));
          return group != 0
              ? group
              : a.optInt("display_order", Integer.MAX_VALUE) != b.optInt("display_order", Integer.MAX_VALUE)
                  ? Integer.compare(a.optInt("display_order", Integer.MAX_VALUE), b.optInt("display_order", Integer.MAX_VALUE))
                  : order.compare(a.optString("name", a.optString("id")), b.optString("name", b.optString("id")));
        });
    return rows;
  }

  private void renderList(LinearLayout list, String query) {
    list.removeAllViews();
    LinearLayout previous = content;
    content = list;
    List<JSONObject> rows = orderedRouters(true);
    String last = "";
    for (JSONObject r : rows) {
      String group = groupName(r.optString("group_id"));
      if (filter.equals("Все") && !group.equals(last)) {
        space(10);
        sectionHeading(group, "");
        last = group;
      }
      routerCard(r);
    }
    if (rows.isEmpty()) empty("Роутеры не найдены", "Измените поиск или фильтр.");
    content = previous;
  }

  private void routerCard(JSONObject r) {
    space(8);
    String id = r.optString("id");
    boolean online = r.optBoolean("online");
    LinearLayout card = ui.card();
    card.setTag("router:" + id);
    card.setGravity(Gravity.CENTER_HORIZONTAL);
    card.addView(ui.chip(online ? "● Онлайн" : "● Офлайн", online ? ui.GREEN : ui.RED));
    card.addView(routerOrderControls(id));
    card.setOnDragListener((view, event) -> {
      if (event.getAction() == android.view.DragEvent.ACTION_DRAG_STARTED)
        return routerDragging && event.getLocalState() instanceof String;
      if (event.getAction() == android.view.DragEvent.ACTION_DRAG_ENDED) {
        routerDragging = false; draggedRouter = ""; dragScrollDelta = 0;
        main.removeCallbacks(dragScroll); view.setBackground(ui.ripple(ui.PANEL, ui.LINE, 9));
        return true;
      }
      if (event.getAction() == android.view.DragEvent.ACTION_DRAG_LOCATION && routerDragging) {
        int[] position = new int[2], scroll = new int[2];
        view.getLocationOnScreen(position); scrollView.getLocationOnScreen(scroll);
        float y = position[1] + event.getY() - scroll[1];
        dragScrollDelta = y < ui.dp(70) ? -ui.dp(9) : y > scrollView.getHeight() - ui.dp(70) ? ui.dp(9) : 0;
        return true;
      }
      if (!routerDragging || !routerOrderPeers(draggedRouter).contains(id) || id.equals(draggedRouter)) return false;
      if (event.getAction() == android.view.DragEvent.ACTION_DRAG_ENTERED)
        view.setBackground(ui.shape(ui.PANEL, ui.AMBER, 9));
      if (event.getAction() == android.view.DragEvent.ACTION_DRAG_EXITED)
        view.setBackground(ui.ripple(ui.PANEL, ui.LINE, 9));
      if (event.getAction() == android.view.DragEvent.ACTION_DROP) {
        String moving = draggedRouter;
        routerDragging = false; main.removeCallbacks(dragScroll);
        saveRouterMove(moving, id, event.getY() < view.getHeight() / 2f ? "before" : "after");
      }
      return true;
    });
    card.addView(ui.spacer(12));
    JSONObject status = obj(r, "status");
    String x = status.optString("xray");
    LinearLayout device = ui.row();
    device.setGravity(Gravity.CENTER);
    device.setTag("router-device-summary");
    device.addView(new RouterPhotoView(this, ui, r, 88));
    LinearLayout metrics = ui.column();
    metrics.setPadding(ui.dp(12), 0, 0, 0);
    metrics.setGravity(Gravity.CENTER_HORIZONTAL);
    deviceMetric(
        metrics,
        "Xray",
        !online
            ? "Нет связи"
            : x.equals("running") ? "Запущен" : x.equals("stopped") ? "Остановлен" : "—",
        !online ? ui.MUTED : x.equals("running") ? ui.GREEN : ui.RED);
    metrics.addView(ui.spacer(8));
    String temperature = str(status, "temperature", "—");
    deviceMetric(
        metrics,
        "Температура",
        online && !temperature.equals("—")
            ? temperature.replaceAll("\\s*°?\\s*[Cc]$", "") + " °C"
            : "—",
        ui.TEXT);
    metrics.addView(ui.spacer(8));
    TextView memory = ui.text("ОЗУ · " + (online ? memoryPercent(status) : "—"), 11, false);
    memory.setTextColor(ui.MUTED);
    metrics.addView(memory);
    device.addView(metrics, new LinearLayout.LayoutParams(ui.dp(132), -2));
    card.addView(device);
    card.addView(ui.spacer(10));
    LinearLayout labels = ui.column();
    labels.setGravity(Gravity.CENTER_HORIZONTAL);
    TextView name = ui.routerName(str(r, "name", id), 16);
    name.setTag("router-name:" + id);
    labels.addView(name);
    labels.addView(ui.spacer(5));
    TextView ident = ui.text(id, 11, false);
    ident.setTextColor(ui.MUTED);
    labels.addView(ident);
    card.addView(labels);
    String preview = str(r, "notes_preview", "");
    int count = r.optInt("notes_count", r.optInt("note_count", 0));
    if (!preview.isEmpty() || count > 0 || r.optBoolean("has_notes")) {
      card.addView(ui.spacer(12));
      TextView note =
          ui.text("Заметки" + (count > 0 ? " · " + count : "") + "\n" + preview, 12, false);
      note.setMaxLines(3);
      note.setEllipsize(TextUtils.TruncateAt.END);
      note.setPadding(ui.dp(8), ui.dp(8), ui.dp(8), ui.dp(8));
      note.setBackground(ui.shape(ui.PANEL2, ui.LINE, 6));
      card.addView(note);
      if (obj(r, "permissions").optBoolean("notes")) note.setOnClickListener(v -> tool("notes", r));
    }
    card.addView(ui.spacer(12));
    card.addView(ui.button("Открыть данные", false, () -> detail(id)));
    card.setOnClickListener(v -> detail(id));
    card.setFocusable(true);
    card.setContentDescription("Открыть роутер " + r.optString("name"));
    add(card);
  }

  private List<String> routerOrderPeers(String id) {
    String group = "";
    for (int i = 0; i < routers.length(); i++)
      if (routers.optJSONObject(i).optString("id").equals(id)) group = routers.optJSONObject(i).optString("group_id");
    List<String> ids = new ArrayList<>();
    for (JSONObject row : orderedRouters(true))
      if (!filter.equals("Все") || group.equals(row.optString("group_id"))) ids.add(row.optString("id"));
    return ids;
  }

  private View routerOrderControls(String id) {
    LinearLayout row = ui.row();
    row.setGravity(Gravity.CENTER);
    row.setPadding(0, ui.dp(5), 0, 0);
    row.setTag("router-order-controls:" + id);
    List<String> peers = routerOrderPeers(id);
    for (int direction : new int[] {-1, 0, 1}) {
      Button button = ui.button(direction < 0 ? "↑" : direction > 0 ? "↓" : "✥", NativeUi.Style.QUIET, () -> {
        if (direction == 0) {
          notice("Удерживайте значок и перетащите карточку. Стрелки меняют порядок на одну позицию.", false);
          return;
        }
        List<String> current = routerOrderPeers(id);
        int target = current.indexOf(id) + direction;
        if (target >= 0 && target < current.size()) saveRouterMove(id, current.get(target), direction < 0 ? "before" : "after");
      });
      button.setTag("router-order:" + id + ":" + direction);
      button.setContentDescription(direction == 0 ? "Перетащить " + id : "Переместить " + id + (direction < 0 ? " раньше" : " позже"));
      button.setEnabled(routerOrderSupported && (direction == 0 ? peers.size() > 1 : direction < 0 ? peers.indexOf(id) > 0 : peers.indexOf(id) < peers.size() - 1));
      button.setTextSize(11);
      button.setPadding(ui.dp(4), 0, ui.dp(4), 0);
      button.setMinHeight(ui.dp(32)); button.setMinimumHeight(ui.dp(32));
      row.addView(button, new LinearLayout.LayoutParams(ui.dp(36), ui.dp(32)));
      if (direction == 0) button.setOnLongClickListener(view -> {
        if (!routerOrderSupported || (requesting && !quietRequest)) return false;
        routerDragging = true; draggedRouter = id;
        boolean started = view.startDragAndDrop(android.content.ClipData.newPlainText("router", id), new View.DragShadowBuilder(view), id, 0);
        if (!started) { routerDragging = false; draggedRouter = ""; return false; }
        main.post(dragScroll);
        return true;
      });
    }
    return row;
  }

  private void saveRouterMove(String id, String target, String placement) {
    if ((requesting && !quietRequest) || !routerOrderSupported) return;
    JSONArray base = new JSONArray();
    for (JSONObject row : orderedRouters(false)) base.put(row.optString("id"));
    call(client -> client.post("/api/routers/order", json("id", id, "target", target, "placement", placement, "base_order", base)), value -> {
      JSONArray order = value.optJSONArray("router_order");
      if (order == null) { notice("Hub не подтвердил порядок карточек", true); return; }
      for (int i = 0; i < routers.length(); i++) {
        JSONObject router = routers.optJSONObject(i);
        int rank = Integer.MAX_VALUE;
        for (int n = 0; n < order.length(); n++) if (order.optString(n).equals(router.optString("id"))) { rank = n; break; }
        try { router.put("display_order", rank); } catch (JSONException ignored) {}
      }
      if (routerResults != null && screen.equals("routers")) {
        LinearLayout replacement = ui.column(); renderList(replacement, search);
        ViewUpdates.patchChildren(routerResults, replacement);
      }
      notice("Порядок карточек сохранён", false);
    });
  }

  private void deviceMetric(LinearLayout column, String title, String value, int color) {
    TextView label = ui.text(title, 11, false);
    label.setTextColor(ui.MUTED);
    column.addView(label);
    column.addView(ui.spacer(3));
    TextView n = ui.text(value, 12, true);
    n.setTextColor(color);
    n.setMaxLines(2);
    n.setEllipsize(TextUtils.TruncateAt.END);
    column.addView(n);
  }

  private String memoryPercent(JSONObject status) {
    double reported = status.optDouble("memory_percent", Double.NaN);
    if (Double.isFinite(reported) && reported >= 0 && reported <= 100)
      return Math.round(reported) + "%";
    java.util.regex.Matcher match =
        java.util.regex.Pattern.compile("(?i)([\\d.]+)\\s*(?:MB\\s*free)?\\s*/\\s*([\\d.]+)")
            .matcher(status.optString("memory"));
    if (match.find())
      try {
        double free = Double.parseDouble(match.group(1)),
            total = Double.parseDouble(match.group(2));
        if (total > 0)
          return Math.max(0, Math.min(100, Math.round((1 - free / total) * 100))) + "%";
      } catch (NumberFormatException ignored) {
      }
    return "—";
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
    hero.addView(new RouterPhotoView(this, ui, r, 104));
    hero.addView(ui.spacer(14));
    hero.addView(ui.routerName(str(r, "name", id), 24));
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
          "Настройки Xray",
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
    c.addView(ui.spacer(10));
    LinearLayout row = ui.row();
    row.setTag("device-kv:" + k);
    row.setGravity(Gravity.CENTER);
    TextView l = ui.text(k, 14, false);
    l.setTextColor(ui.MUTED);
    l.setGravity(Gravity.CENTER);
    l.setMaxWidth(ui.dp(96));
    row.addView(l, new LinearLayout.LayoutParams(-2, -2));
    boolean flash = k.equals("Flash");
    TextView value = ui.text(flash ? v.replaceAll("\\s+", " ").trim() : v, 14, true);
    value.setGravity(Gravity.CENTER);
    if (flash) {
      // Let the row measure the real remaining width after the short label.
      // The old generic 110–200 dp cap wrapped otherwise fitting Flash values.
      value.setMaxLines(1);
      value.setMinWidth(ui.dp(24));
      value.setHorizontallyScrolling(false);
      value.setEllipsize(TextUtils.TruncateAt.END);
      int maxSp = Math.max(10, (int) (value.getTextSize() / getResources().getDisplayMetrics().scaledDensity));
      value.setAutoSizeTextTypeUniformWithConfiguration(10, maxSp, 1, android.util.TypedValue.COMPLEX_UNIT_SP);
    } else {
      int width = (int) (getResources().getDisplayMetrics().widthPixels / getResources().getDisplayMetrics().density);
      value.setMaxWidth(ui.dp(Math.max(110, Math.min(200, width - 180))));
    }
    LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-2, -2);
    params.leftMargin = ui.dp(10);
    row.addView(value, params);
    c.addView(row);
  }

  private void action(String icon, String title, String subtitle, Runnable run) {
    LinearLayout card = ui.card();
    card.setTag("action:" + title);
    card.setGravity(Gravity.CENTER_HORIZONTAL);
    card.setPadding(ui.dp(12), ui.dp(12), ui.dp(12), ui.dp(12));
    boolean support = title.equals("Поддержать разработку");
    card.addView(ui.iconBadge(icon, support ? ui.AMBER : ui.MUTED, 26));
    card.addView(ui.spacer(6));
    TextView actionTitle = ui.text(title, 14, true);
    if (support) actionTitle.setTextColor(ui.AMBER);
    card.addView(actionTitle);
    if (subtitle != null && !subtitle.isEmpty()) {
      card.addView(ui.spacer(5));
      TextView s = ui.text(subtitle, 12, false);
      s.setTextColor(ui.MUTED);
      card.addView(s);
    }
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
    sectionHeading("Ваш Hub", "");
    LinearLayout account = ui.card();
    account.setTag("account-card");
    account.setGravity(Gravity.CENTER_HORIZONTAL);
    account.setPadding(ui.dp(12), ui.dp(12), ui.dp(12), ui.dp(12));
    if (owner()) {
      View crown = ui.iconBadge("crown", ui.AMBER, 32);
      crown.setContentDescription("Владелец Hub · золотая корона");
      account.addView(crown);
      account.addView(ui.spacer(6));
    }
    account.addView(ui.text(str(obj(session, "account"), "username", "Пользователь"), 15, true));
    account.addView(ui.spacer(5));
    account.addView(ui.text(owner() ? "Владелец Hub" : "Доступ по назначенным правам", 12, false));
    add(account);
    space(18);
    sectionHeading("Рабочее пространство", "");
    action("grid", "Дашборд", "Карта и состояние сети", this::overview);
    action("router", "Роутеры", routers.length() + " в вашем Hub", this::routerList);
    if (owner())
      action("router", "Группы роутеров", groups.length() + " групп", () -> settingsTool("groups"));
    sectionHeading("Управление", "");
    if (owner() || obj(session, "account").optBoolean("can_add_router"))
      action(
          "plus",
          "Добавить роутер",
          "Автоматические параметры OpenWrt",
          () -> settingsTool("addRouter"));
    if (owner()) {
      action(
          "terminal", "Терминал VPS", "Консоль сервера Hub", () -> tool("vps", new JSONObject()));
      action(
          "refresh",
          "Обновить роутеры",
          "Выбор устройств и отдельные пароли",
          () -> settingsTool("routerUpdates"));
      action("shield", "Xray на VPS", "Обновление CFG и перезапуск", () -> settingsTool("vpsXray"));
      action("server", "Резервные копии", "Экспорт и восстановление Hub", this::backups);
    }
    sectionHeading("Настройки", "");
    action("shield", "Защита приложения", AppLock.enabled(this) ? "Включена · защита телефона" : "PIN, графический ключ или отпечаток",
        () -> startActivity(new Intent(this, AppSecurityActivity.class)));
    action("server", "Подключение", "", this::showConnectionInfo);
    action(
        "settings",
        "Оформление",
        prefs.getBoolean("lite", false)
            ? "Для слабых устройств"
            : prefs.getBoolean("winter", false) ? "Зимняя тема" : "Графитовый · как на ПК",
        this::design);
    action(
        "grid",
        prefs.getBoolean("statsVisible", true) ? "Скрыть статистику" : "Открыть статистику",
        "Выбор сохраняется на устройстве",
        this::toggleStatistics);
    if (owner()) {
      action("bell", "Уведомления", "События Hub на телефоне", () -> settingsTool("notifications"));
      action(
          "shield",
          "Пользователи и доступ",
          "Права доступа к роутерам",
          () -> settingsTool("users"));
      action(
          "key", "Безопасность", "Пароль и двухфакторная защита", () -> settingsTool("security"));
      action(
          "refresh",
          "Обновление Hub",
          hubUpdateStatus.optBoolean("available")
              ? "Доступно обновление " + hubUpdateStatus.optString("remote_version")
              : "Проверка и установка сборки",
          () -> settingsTool("hubUpdate"));
    }
    action("shield", "Активные сессии", "Контроль входов в Hub", () -> settingsTool("sessions"));
    sectionHeading("Сообщество", "");
    action(
        "crown",
        "Поддержать разработку",
        "Спасибо за поддержку проекта",
        () -> settingsTool("donate"));
    action("info", "О приложении", BuildConfig.VERSION_NAME, () -> settingsTool("about"));
    space(8);
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

  private void showConnectionInfo() {
    frame("connectionInfo", "Подключение", false);
    title("Сервер Hub", api.getHub().toString());
    LinearLayout secured = ui.row();
    secured.setGravity(Gravity.CENTER);
    secured.addView(ui.chip("Сессия зашифрована в Android", ui.GREEN));
    add(secured);
    space(24);
    add(
        ui.button(
            "Подключить другой Hub",
            false,
            () -> confirm("Сменить Hub?", "Текущая сессия будет завершена.", () -> logout(true))));
  }

  private void design() {
    frame("design", "Дизайн и эффекты", false);
    title("Оформление Hub", "Выбор применяется ко всем экранам приложения.");
    String[] names = {"Графитовый", "Зимний", "Для слабых устройств"};
    for (int i = 0; i < names.length; i++) {
      final int index = i;
      boolean active =
          i == 2
              ? prefs.getBoolean("lite", false)
              : !prefs.getBoolean("lite", false) && prefs.getBoolean("winter", false) == (i == 1);
      action(
          i == 2 ? "menu" : "grid",
          names[i],
          active
              ? "Выбрано"
              : i == 2
                  ? "Без эффектов и анимаций"
                  : i == 1 ? "Ледяные оттенки" : "Как в ПК-панели OWRT Hub",
          () -> {
            prefs.edit().putBoolean("winter", index == 1).putBoolean("lite", index == 2).apply();
            ui = new NativeUi(this, index == 1);
            design();
          });
    }
    space(12);
    sectionHeading("Эффекты", prefs.getBoolean("lite", false) ? "Отключены в лёгком режиме" : "");
    for (String effect : new String[] {"off", "streams", "snow"}) {
      String label =
          effect.equals("off") ? "Выключены" : effect.equals("streams") ? "Потоки" : "Снег";
      action(
          "activity",
          label,
          prefs.getString("effect", "off").equals(effect) ? "Выбрано" : "Включить эффект",
          () -> {
            prefs.edit().putString("effect", effect).apply();
            design();
          });
    }
  }

  private void openNavigation() {
    Dialog drawer = new Dialog(this);
    LinearLayout page = ui.column();
    page.setBackgroundColor(ui.BG);
    page.setPadding(ui.dp(16), ui.dp(24), ui.dp(16), ui.dp(18));
    LinearLayout brand = ui.row();
    brand.addView(ui.logoBadge(36));
    TextView title = ui.text("OWRT Hub v125", 17, true);
    brand.addView(title, new LinearLayout.LayoutParams(0, -2, 1));
    brand.addView(ui.iconButton("close", "Закрыть меню", drawer::dismiss));
    page.addView(brand);
    page.addView(ui.divider());
    ScrollView scroll = new ScrollView(this);
    LinearLayout navigation = ui.column();
    scroll.addView(navigation);
    page.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
    menuSection(
        navigation,
        "workspace",
        "Рабочее пространство",
        new String[] {"Дашборд", "Роутеры"},
        new Runnable[] {this::overview, this::routerList},
        drawer);
    List<String> management = new ArrayList<>();
    List<Runnable> actions = new ArrayList<>();
    if (owner() || obj(session, "account").optBoolean("can_add_router")) {
      management.add("Добавить роутер");
      actions.add(() -> settingsTool("addRouter"));
    }
    if (owner()) {
      management.add("Терминал VPS");
      actions.add(() -> tool("vps", new JSONObject()));
      management.add("Обновить роутеры");
      actions.add(() -> settingsTool("routerUpdates"));
      management.add("Xray на VPS");
      actions.add(() -> settingsTool("vpsXray"));
      management.add("Группы роутеров");
      actions.add(() -> settingsTool("groups"));
    }
    if (!management.isEmpty())
      menuSection(
          navigation,
          "management",
          "Управление",
          management.toArray(new String[0]),
          actions.toArray(new Runnable[0]),
          drawer);
    List<String> options =
        new ArrayList<>(
            Arrays.asList(
                "Доступ к Hub",
                "Дизайн и эффекты",
                prefs.getBoolean("statsVisible", true)
                    ? "Скрыть статистику"
                    : "Открыть статистику"));
    List<Runnable> optionActions =
        new ArrayList<>(
            Arrays.asList(this::showConnectionInfo, this::design, this::toggleStatistics));
    if (owner()) {
      options.add("Обновление панели");
      optionActions.add(() -> settingsTool("hubUpdate"));
    }
    menuSection(
        navigation,
        "settings",
        "Настройки",
        options.toArray(new String[0]),
        optionActions.toArray(new Runnable[0]),
        drawer);
    TextView community = ui.label("СООБЩЕСТВО");
    community.setGravity(Gravity.START | Gravity.CENTER_VERTICAL);
    community.setPadding(0, ui.dp(12), 0, ui.dp(12));
    navigation.addView(menuIconRow(community, "globe", ui.MUTED));
    navigation.addView(
        menuButton(
            "NetHaven · чат",
            () ->
                startActivity(
                    new Intent(
                        Intent.ACTION_VIEW,
                        android.net.Uri.parse("https://t.me/+LZDsQJhUfcNhYWEy"))),
            drawer,
            false));
    navigation.addView(
        menuButton("Поддержать разработку", () -> settingsTool("donate"), drawer, true));
    page.addView(ui.divider());
    TextView account =
        ui.text(
            str(obj(session, "account"), "username", "Пользователь")
                + (owner() ? " · Владелец" : ""),
            12,
            true);
    account.setGravity(Gravity.START | Gravity.CENTER_VERTICAL);
    account.setMinimumHeight(ui.dp(40));
    page.addView(menuIconRow(account, "user", ui.MUTED));
    page.addView(
        menuButton(
            "Выйти из Hub",
            () ->
                confirm(
                    "Выйти из Hub?",
                    "Сохранённая сессия будет удалена с устройства.",
                    () -> logout(false)),
            drawer,
            false));
    drawer.setContentView(page);
    android.view.Window window = drawer.getWindow();
    if (window != null) {
      window.setBackgroundDrawableResource(android.R.color.transparent);
      window.setGravity(Gravity.START);
      window.setLayout(
          Math.min(ui.dp(310), getResources().getDisplayMetrics().widthPixels - ui.dp(24)), -1);
      window.addFlags(WindowManager.LayoutParams.FLAG_DIM_BEHIND);
      window.setDimAmount(.65f);
    }
    drawer.show();
    if (window != null)
      window.setLayout(
          Math.min(ui.dp(310), getResources().getDisplayMetrics().widthPixels - ui.dp(24)), -1);
  }

  private void menuSection(
      LinearLayout parent,
      String key,
      String title,
      String[] labels,
      Runnable[] actions,
      Dialog drawer) {
    LinearLayout items = ui.column();
    String saved = "menu:" + key;
    boolean open = prefs.getBoolean(saved, key.equals("workspace"));
    items.setVisibility(open ? View.VISIBLE : View.GONE);
    Button toggle = ui.button(title + (open ? "  ⌃" : "  ⌄"), NativeUi.Style.QUIET, null);
    toggle.setGravity(Gravity.START | Gravity.CENTER_VERTICAL);
    toggle.setTextColor(ui.MUTED);
    toggle.setOnClickListener(
        v -> {
          boolean show = items.getVisibility() != View.VISIBLE;
          items.setVisibility(show ? View.VISIBLE : View.GONE);
          toggle.setText(title + (show ? "  ⌃" : "  ⌄"));
          prefs.edit().putBoolean(saved, show).apply();
        });
    parent.addView(menuIconRow(toggle, menuIcon(title), ui.MUTED));
    for (int i = 0; i < labels.length; i++)
      items.addView(menuButton(labels[i], actions[i], drawer, false));
    parent.addView(items);
  }

  private View menuButton(String title, Runnable action, Dialog drawer, boolean gold) {
    Button button =
        ui.button(
            title,
            NativeUi.Style.QUIET,
            () -> {
              drawer.dismiss();
              action.run();
            });
    button.setGravity(Gravity.START | Gravity.CENTER_VERTICAL);
    if (gold) button.setTextColor(ui.AMBER);
    return menuIconRow(button, menuIcon(title), gold ? ui.AMBER : ui.MUTED);
  }

  private View menuIconRow(TextView label, String name, int color) {
    FrameLayout row = new FrameLayout(this);
    row.setLayoutParams(new LinearLayout.LayoutParams(-1, -2));
    label.setPadding(ui.dp(44), label.getPaddingTop(), ui.dp(10), label.getPaddingBottom());
    row.addView(label, new FrameLayout.LayoutParams(-1, -2));
    View icon = ui.icon(name, color);
    FrameLayout.LayoutParams placement =
        new FrameLayout.LayoutParams(ui.dp(20), ui.dp(20), Gravity.START | Gravity.CENTER_VERTICAL);
    placement.setMarginStart(ui.dp(14));
    row.addView(icon, placement);
    return row;
  }

  private String menuIcon(String title) {
    switch (title) {
      case "Рабочее пространство":
      case "Дашборд":
        return "grid";
      case "Роутеры":
        return "router";
      case "Группы роутеров":
        return "folder";
      case "Управление":
        return "activity";
      case "Настройки":
        return "settings";
      case "Добавить роутер":
        return "plus";
      case "Терминал VPS":
        return "terminal";
      case "Обновить роутеры":
        return "refresh";
      case "Xray на VPS":
      case "Доступ к Hub":
        return "shield";
      case "Дизайн и эффекты":
        return "activity";
      case "Скрыть статистику":
      case "Открыть статистику":
        return "grid";
      case "Обновление панели":
        return "download";
      case "NetHaven · чат":
        return "chat";
      case "Поддержать разработку":
        return "heart";
      case "Выйти из Hub":
        return "logout";
      default:
        return "info";
    }
  }

  private void toggleStatistics() {
    prefs.edit().putBoolean("statsVisible", !prefs.getBoolean("statsVisible", true)).apply();
    overview();
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
