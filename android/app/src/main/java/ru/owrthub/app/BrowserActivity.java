package ru.owrthub.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.Message;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.inputmethod.InputMethodManager;
import android.webkit.CookieManager;
import android.webkit.HttpAuthHandler;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.SslErrorHandler;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebStorage;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.PopupMenu;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import org.json.JSONObject;

/** Embedded browser restricted to third-party tools and visible browser-based authentication. */
public final class BrowserActivity extends Activity {
  private int BG, PANEL, LINE, TEXT, MUTED, BLUE;
  private static final int FILE_REQUEST = 7001, SAVE_REQUEST = 7002;
  public static final int HOST_ID = R.id.hub_host,
      PORT_ID = R.id.hub_port,
      USER_ID = R.id.hub_user,
      PASSWORD_ID = R.id.hub_password;
  public static final int CONNECT_ID = R.id.hub_connect, WEB_ID = R.id.hub_web;
  private final Handler ui = new Handler(Looper.getMainLooper());
  private final ExecutorService io = Executors.newFixedThreadPool(2);
  private final List<Tab> tabs = new ArrayList<>(), allTabs = new ArrayList<>();
  private LinearLayout root, toolbar;
  private FrameLayout body;
  private ScrollView connection;
  private TextView title, subtitle, connectionStatus;
  private Button back, connectButton;
  private ProgressBar progress;
  private EditText hostField, portField, userField, passwordField;
  private SharedPreferences prefs;
  private URI hub;
  private int connectionAttempt;
  private boolean connectingScreen = true;
  private String pendingUsername = "", pendingPassword = "";
  private ValueCallback<Uri[]> fileCallback;
  private Download pendingDownload;

  private final class Tab {
    final WebView web;
    final FrameLayout frame;
    final boolean popupGesture;
    LinearLayout error;
    String lastUrl = "";
    boolean promoted, disposed;

    Tab(boolean userGesture) {
      popupGesture = userGesture;
      frame = new FrameLayout(BrowserActivity.this);
      frame.setBackgroundColor(BG);
      web = new WebView(BrowserActivity.this);
      web.setId(WEB_ID);
      frame.addView(web, new FrameLayout.LayoutParams(-1, -1));
    }
  }

  private static final class Download {
    final String url, mime, filename, cookie, userAgent;

    Download(String u, String m, String n, String c, String a) {
      url = u;
      mime = m;
      filename = n;
      cookie = c;
      userAgent = a;
    }
  }

  @Override
  public void onCreate(Bundle state) {
    super.onCreate(state);
    NativeUi design =
        new NativeUi(
            this, getSharedPreferences("native_design", MODE_PRIVATE).getBoolean("winter", false));
    BG = design.BG;
    PANEL = design.PANEL;
    LINE = design.LINE;
    TEXT = design.TEXT;
    MUTED = design.MUTED;
    BLUE = design.BLUE;
    WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG);
    prefs = getSharedPreferences("hub_connection", MODE_PRIVATE);
    root = new LinearLayout(this);
    root.setOrientation(LinearLayout.VERTICAL);
    root.setBackgroundColor(BG);
    root.setOnApplyWindowInsetsListener(
        (view, insets) -> {
          view.setPadding(
              insets.getSystemWindowInsetLeft(),
              insets.getSystemWindowInsetTop(),
              insets.getSystemWindowInsetRight(),
              insets.getSystemWindowInsetBottom());
          return insets;
        });
    setContentView(root);
    root.requestApplyInsets();
    buildToolbar();
    progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
    progress.setIndeterminate(false);
    progress.setVisibility(View.GONE);
    root.addView(progress, new LinearLayout.LayoutParams(-1, dp(2)));
    body = new FrameLayout(this);
    root.addView(body, new LinearLayout.LayoutParams(-1, 0, 1));
    CookieManager.getInstance().setAcceptCookie(true);
    String saved = getIntent().getStringExtra("hub_origin");
    if (saved == null) {
      finish();
      return;
    }
    if (BuildConfig.DEBUG && getIntent().hasExtra("debugHubUrl")) {
      String candidate = getIntent().getStringExtra("debugHubUrl");
      try {
        URI local = HubUrlPolicy.normalizeHub(candidate, "", true);
        String h = local.getHost();
        if ("127.0.0.1".equals(h) || "10.0.2.2".equals(h) || "localhost".equals(h))
          saved = candidate;
      } catch (IllegalArgumentException ignored) {
      }
    }
    if (!saved.isEmpty()) {
      try {
        hub = HubUrlPolicy.normalizeHub(saved, "", BuildConfig.DEBUG);
        String destination = getIntent().getStringExtra("tool_url");
        if (destination == null || !HubUrlPolicy.sameOrigin(hub, destination)) {
          finish();
          return;
        }
        String cookie = getIntent().getStringExtra("session_cookie");
        if (cookie != null)
          CookieManager.getInstance().setCookie(hub.toString(), cookie, accepted -> showBrowser());
        else showBrowser();
      } catch (IllegalArgumentException ignored) {
        showConnection();
      }
    } else showConnection();
  }

  private void buildToolbar() {
    toolbar = new LinearLayout(this);
    toolbar.setGravity(Gravity.CENTER_VERTICAL);
    toolbar.setPadding(dp(6), dp(2), dp(6), dp(2));
    back = smallButton("‹", "Назад");
    back.setTextSize(31);
    back.setOnClickListener(v -> navigateBack());
    toolbar.addView(back, new LinearLayout.LayoutParams(dp(44), dp(48)));
    LinearLayout labels = new LinearLayout(this);
    labels.setOrientation(LinearLayout.VERTICAL);
    title = label("OWRT Hub", 17, TEXT, true);
    title.setSingleLine(true);
    subtitle = label("Android · " + BuildConfig.VERSION_NAME, 10, MUTED, false);
    subtitle.setSingleLine(true);
    labels.addView(title);
    labels.addView(subtitle);
    toolbar.addView(labels, new LinearLayout.LayoutParams(0, -2, 1));
    Button menu = smallButton("⋮", "Меню приложения");
    menu.setId(R.id.hub_menu);
    menu.setTextSize(24);
    menu.setOnClickListener(this::showMenu);
    toolbar.addView(menu, new LinearLayout.LayoutParams(dp(44), dp(48)));
    root.addView(toolbar, new LinearLayout.LayoutParams(-1, dp(56)));
  }

  private void showConnection() {
    if (getIntent().hasExtra("tool_url")) {
      finish();
      return;
    }
    connectionAttempt++;
    connectingScreen = true;
    pendingPassword = "";
    pendingUsername = "";
    progress.setVisibility(View.GONE);
    for (Tab tab : tabs) tab.frame.setVisibility(View.GONE);
    if (connection != null) body.removeView(connection);
    connection = new ScrollView(this);
    connection.setFillViewport(true);
    LinearLayout form = new LinearLayout(this);
    form.setOrientation(LinearLayout.VERTICAL);
    form.setPadding(dp(24), dp(12), dp(24), dp(22));
    connection.addView(form, new ScrollView.LayoutParams(-1, -2));
    TextView logo = label("ϟ", 32, BLUE, true);
    logo.setGravity(Gravity.CENTER);
    logo.setBackground(shape(PANEL, LINE, 20));
    LinearLayout.LayoutParams logoParams = new LinearLayout.LayoutParams(dp(50), dp(50));
    logoParams.gravity = Gravity.CENTER_HORIZONTAL;
    logoParams.bottomMargin = dp(12);
    form.addView(logo, logoParams);
    TextView heading = label("Ваш Hub.\nНа вашем сервере.", 22, TEXT, true);
    heading.setGravity(Gravity.CENTER);
    form.addView(heading);
    addText(form, "Подключение к панели OWRT Hub", 11, MUTED, 5, 17)
        .setGravity(Gravity.CENTER);
    hostField =
        field(
            form,
            "Адрес Hub · IP или домен",
            "https://hub.example.org",
            HOST_ID,
            InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
    hostField.setText(prefs.getString("hub_origin", hub == null ? "" : hub.toString()));
    portField =
        field(form, "Порт HTTPS", "443 — по умолчанию", PORT_ID, InputType.TYPE_CLASS_NUMBER);
    userField =
        field(
            form,
            "Логин панели · необязательно",
            "Ввести на странице входа",
            USER_ID,
            InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD);
    userField.setText(prefs.getString("hub_username", ""));
    passwordField =
        field(
            form,
            "Пароль панели · не сохраняется",
            "Ввести на странице входа",
            PASSWORD_ID,
            InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
    passwordField.setSaveEnabled(false);
    passwordField.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO);
    addText(
        form,
        "Используйте учётную запись Hub. Капча и 2FA проверяются на странице панели.",
        12,
        MUTED,
        2,
        16);
    connectButton = button("Проверить и подключиться", true);
    connectButton.setId(CONNECT_ID);
    connectButton.setOnClickListener(v -> connect());
    form.addView(connectButton, new LinearLayout.LayoutParams(-1, dp(50)));
    connectionStatus =
        addText(
            form,
            "Адрес и сессия сохраняются на этом устройстве. Пароль root от VPS здесь не нужен.",
            11,
            MUTED,
            14,
            0);
    body.addView(connection, new FrameLayout.LayoutParams(-1, -1));
    title.setText("OWRT Hub");
    subtitle.setText("Подключение · " + BuildConfig.VERSION_NAME);
    back.setVisibility(tabs.isEmpty() ? View.INVISIBLE : View.VISIBLE);
  }

  private void connect() {
    final URI candidate;
    try {
      candidate =
          HubUrlPolicy.normalizeHub(
              hostField.getText().toString(), portField.getText().toString(), BuildConfig.DEBUG);
    } catch (IllegalArgumentException ex) {
      connectionStatus.setText(
          "Проверьте адрес и порт. Нужен HTTPS-адрес Hub без пути, логина и пароля в URL.");
      connectionStatus.setTextColor(Color.rgb(239, 149, 169));
      return;
    }
    final int attempt = ++connectionAttempt;
    pendingUsername = userField.getText().toString().trim();
    pendingPassword = passwordField.getText().toString();
    passwordField.setText("");
    connectButton.setEnabled(false);
    connectionStatus.setTextColor(MUTED);
    connectionStatus.setText("Проверяю Hub…");
    io.execute(
        () -> {
          String error = null;
          try {
            HttpURLConnection c =
                openConnection(candidate.resolve("/health?android-client=1").toString());
            try {
              if (c.getResponseCode() != 200) throw new java.io.IOException("health unavailable");
              byte[] bytes;
              try (InputStream stream = c.getInputStream();
                  java.io.ByteArrayOutputStream buffer = new java.io.ByteArrayOutputStream()) {
                byte[] part = new byte[2048];
                int count;
                while (buffer.size() < 16385
                    && (count = stream.read(part, 0, Math.min(part.length, 16385 - buffer.size())))
                        != -1) buffer.write(part, 0, count);
                bytes = buffer.toByteArray();
              }
              if (bytes.length > 16384
                  || !new JSONObject(new String(bytes, StandardCharsets.UTF_8)).optBoolean("ok"))
                throw new java.io.IOException("invalid health");
            } finally {
              c.disconnect();
            }
          } catch (Exception ex) {
            if (ex instanceof javax.net.ssl.SSLException)
              error =
                  "Не удалось подтвердить HTTPS-сертификат. Укажите домен панели с действительным"
                      + " сертификатом.";
            else
              error =
                  "Hub не ответил на проверку. Проверьте адрес, порт, интернет и доступность"
                      + " панели.";
          }
          final String result = error;
          ui.post(
              () -> {
                if (isDestroyed() || attempt != connectionAttempt) return;
                connectButton.setEnabled(true);
                if (result != null) {
                  pendingPassword = "";
                  connectionStatus.setText(result);
                  return;
                }
                boolean changed = hub != null && !hub.equals(candidate);
                boolean credentialsEntered =
                    !pendingPassword.isEmpty()
                        || !pendingUsername.equals(prefs.getString("hub_username", ""));
                hub = candidate;
                prefs
                    .edit()
                    .putString("hub_origin", hub.toString())
                    .putString("hub_username", pendingUsername)
                    .apply();
                ((InputMethodManager) getSystemService(INPUT_METHOD_SERVICE))
                    .hideSoftInputFromWindow(hostField.getWindowToken(), 0);
                if (changed || credentialsEntered) {
                  if (activeTab() != null) activeTab().web.clearCache(true);
                  closeAllTabs();
                  WebStorage.getInstance().deleteAllData();
                  CookieManager.getInstance()
                      .removeAllCookies(
                          value -> {
                            CookieManager.getInstance().flush();
                            showBrowser();
                          });
                } else {
                  Tab previous = activeTab();
                  showBrowser();
                  if (previous != null) previous.web.reload();
                }
              });
        });
  }

  private void showBrowser() {
    connectingScreen = false;
    if (connection != null) {
      body.removeView(connection);
      connection = null;
    }
    if (tabs.isEmpty()) {
      Tab rootTab = createTab(false);
      promote(rootTab);
      rootTab.web.loadUrl(getIntent().getStringExtra("tool_url"));
    } else showTopTab();
    updateToolbar();
  }

  @SuppressWarnings("deprecation")
  private Tab createTab(boolean userGesture) {
    Tab tab = new Tab(userGesture);
    allTabs.add(tab);
    WebSettings settings = tab.web.getSettings();
    settings.setJavaScriptEnabled(
        true); // Hub's own UI, captcha and terminals require JS; no Java bridge is exposed.
    settings.setDomStorageEnabled(true);
    settings.setSupportMultipleWindows(true);
    settings.setJavaScriptCanOpenWindowsAutomatically(false);
    settings.setAllowFileAccess(false);
    settings.setAllowContentAccess(false);
    settings.setAllowFileAccessFromFileURLs(false);
    settings.setAllowUniversalAccessFromFileURLs(false);
    settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
    settings.setSafeBrowsingEnabled(true);
    settings.setBuiltInZoomControls(true);
    settings.setDisplayZoomControls(false);
    settings.setTextZoom(100);
    settings.setUserAgentString(
        settings.getUserAgentString() + " OWRTHubAndroid/" + BuildConfig.VERSION_NAME);
    CookieManager.getInstance().setAcceptThirdPartyCookies(tab.web, false);
    tab.web.setBackgroundColor(BG);
    tab.web.setWebViewClient(new BrowserClient(tab));
    tab.web.setWebChromeClient(new BrowserChrome(tab));
    tab.web.setDownloadListener(
        (url, agent, disposition, mime, length) -> beginDownload(url, agent, disposition, mime));
    return tab;
  }

  private void promote(Tab tab) {
    if (tab.disposed || tab.promoted) return;
    tab.promoted = true;
    tabs.add(tab);
    body.addView(tab.frame, new FrameLayout.LayoutParams(-1, -1));
    showTopTab();
  }

  private void showTopTab() {
    for (int i = 0; i < tabs.size(); i++)
      tabs.get(i)
          .frame
          .setVisibility(!connectingScreen && i == tabs.size() - 1 ? View.VISIBLE : View.GONE);
    updateToolbar();
  }

  private Tab activeTab() {
    return tabs.isEmpty() ? null : tabs.get(tabs.size() - 1);
  }

  private void updateToolbar() {
    if (connectingScreen) return;
    Tab tab = activeTab();
    title.setText(
        tabs.size() > 1 && tab != null && tab.web.getTitle() != null
            ? tab.web.getTitle()
            : "OWRT Hub");
    subtitle.setText(
        (hub == null ? "" : hub.getAuthority())
            + (tabs.size() > 1 ? " · экран " + tabs.size() : " · " + BuildConfig.VERSION_NAME));
    back.setVisibility(View.VISIBLE);
  }

  private boolean navigation(Tab tab, String url, boolean gesture) {
    if (tab.disposed) return true;
    if ("about:blank".equals(url) && !tab.promoted) return false;
    HubUrlPolicy.Decision decision = HubUrlPolicy.classify(hub, url);
    if (decision == HubUrlPolicy.Decision.INTERNAL) {
      promote(tab);
      return false;
    }
    if (decision == HubUrlPolicy.Decision.EXTERNAL_BROWSER) {
      new AlertDialog.Builder(this)
          .setTitle("Вход через сервис")
          .setMessage(
              "В первой сборке используйте логин и пароль Hub с капчей и 2FA. Вход через GitHub/VK"
                  + " и Passkey будет добавлен отдельно.")
          .setPositiveButton("Понятно", null)
          .show();
    } else if (decision == HubUrlPolicy.Decision.EXTERNAL && (gesture || tab.popupGesture)) {
      openExternal(url);
    } else if (gesture) toast("Эту ссылку нельзя открыть в приложении.");
    if (!tab.promoted) ui.post(() -> closeTab(tab));
    return true;
  }

  private final class BrowserClient extends WebViewClient {
    private final Tab tab;

    BrowserClient(Tab t) {
      tab = t;
    }

    @Override
    public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
      if (!req.isForMainFrame()) return false;
      return navigation(tab, req.getUrl().toString(), req.hasGesture());
    }

    @Override
    public boolean shouldOverrideUrlLoading(WebView view, String url) {
      return navigation(tab, url, false);
    }

    @Override
    public void onPageStarted(WebView view, String url, android.graphics.Bitmap icon) {
      if (tab.disposed || ("about:blank".equals(url) && !tab.promoted)) return;
      if (!HubUrlPolicy.sameOrigin(hub, url)) {
        view.stopLoading();
        navigation(tab, url, false);
        return;
      }
      tab.lastUrl = url;
      promote(tab);
      hideError(tab);
      if (tab == activeTab()) {
        progress.setVisibility(View.VISIBLE);
        progress.setProgress(10);
      }
    }

    @Override
    public void onPageFinished(WebView view, String url) {
      if (tab.disposed || !HubUrlPolicy.sameOrigin(hub, url)) return;
      CookieManager.getInstance().flush();
      if (tab == activeTab()) progress.setVisibility(View.GONE);
      updateToolbar();
      fillHubLogin(tab, url);
      if (getIntent().getBooleanExtra("authentication", false)
          && !Uri.parse(url).getPath().startsWith("/login")) {
        String cookie = CookieManager.getInstance().getCookie(hub.toString());
        if (cookie != null && cookie.contains(HubApiClient.SESSION_COOKIE + "=")) finish();
      }
    }

    @Override
    public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError error) {
      if (req.isForMainFrame() && !tab.disposed && !"about:blank".equals(req.getUrl().toString()))
        showError(
            tab,
            "Нет соединения с Hub",
            "Проверьте интернет и доступность панели. Состояние роутеров сейчас неизвестно.");
    }

    @Override
    public void onReceivedHttpError(
        WebView view, WebResourceRequest req, WebResourceResponse response) {
      if (req.isForMainFrame() && response.getStatusCode() >= 500)
        showError(
            tab,
            "Hub временно недоступен",
            "Сервер вернул HTTP "
                + response.getStatusCode()
                + ". Можно повторить запрос или изменить адрес.");
    }

    @Override
    public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
      handler.cancel();
      pendingPassword = "";
      if (error.getUrl().equals(tab.lastUrl) || error.getUrl().equals(view.getUrl()))
        showError(
            tab,
            "HTTPS не подтверждён",
            "Сертификат панели недействителен или не соответствует адресу. Укажите правильный"
                + " HTTPS-домен Hub.");
    }

    @Override
    public void onReceivedHttpAuthRequest(
        WebView view, HttpAuthHandler handler, String host, String realm) {
      if (hub == null
          || !hub.getHost().equalsIgnoreCase(host)
          || !HubUrlPolicy.sameOrigin(hub, view.getUrl())) {
        handler.cancel();
        return;
      }
      LinearLayout fields = new LinearLayout(BrowserActivity.this);
      fields.setOrientation(LinearLayout.VERTICAL);
      fields.setPadding(dp(22), dp(8), dp(22), 0);
      EditText name = field(fields, "Пользователь", "", 0, InputType.TYPE_CLASS_TEXT);
      EditText pass =
          field(
              fields,
              "Пароль",
              "",
              0,
              InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
      new AlertDialog.Builder(BrowserActivity.this)
          .setTitle("HTTP-авторизация · " + hub.getHost())
          .setView(fields)
          .setPositiveButton(
              "Войти",
              (d, w) -> {
                handler.proceed(name.getText().toString(), pass.getText().toString());
                pass.setText("");
              })
          .setNegativeButton("Отмена", (d, w) -> handler.cancel())
          .setOnCancelListener(d -> handler.cancel())
          .show();
    }

    @Override
    public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail detail) {
      if (tab.disposed) return true;
      // A renderer may be shared by all tabs. Do not call into dead renderers.
      for (Tab affected : new ArrayList<>(allTabs)) {
        affected.disposed = true;
        body.removeView(affected.frame);
        affected.frame.removeView(affected.web);
        affected.web.destroy();
      }
      tabs.clear();
      allTabs.clear();
      toast("Android остановил веб-интерфейс. Откройте инструмент повторно из приложения.");
      finish();
      return true;
    }
  }

  private final class BrowserChrome extends WebChromeClient {
    private final Tab owner;

    BrowserChrome(Tab t) {
      owner = t;
    }

    @Override
    public void onProgressChanged(WebView web, int value) {
      if (owner == activeTab() && !connectingScreen) {
        progress.setProgress(value);
        progress.setVisibility(value == 100 ? View.GONE : View.VISIBLE);
      }
    }

    @Override
    public void onReceivedTitle(WebView view, String value) {
      if (owner == activeTab()) updateToolbar();
    }

    @Override
    public boolean onCreateWindow(
        WebView view, boolean dialog, boolean userGesture, Message message) {
      if (!userGesture
          || hub == null
          || allTabs.size() >= 6
          || !HubUrlPolicy.sameOrigin(hub, view.getUrl())) return false;
      Tab child = createTab(true);
      WebView.WebViewTransport transport = (WebView.WebViewTransport) message.obj;
      transport.setWebView(child.web);
      message.sendToTarget();
      ui.postDelayed(
          () -> {
            if (!child.promoted && !child.disposed) closeTab(child);
          },
          15000);
      return true;
    }

    @Override
    public void onCloseWindow(WebView window) {
      for (Tab t : new ArrayList<>(allTabs))
        if (t.web == window && t != (tabs.isEmpty() ? null : tabs.get(0))) closeTab(t);
    }

    @Override
    public boolean onShowFileChooser(
        WebView web, ValueCallback<Uri[]> callback, FileChooserParams params) {
      if (!HubUrlPolicy.sameOrigin(hub, web.getUrl())) {
        callback.onReceiveValue(null);
        return true;
      }
      if (fileCallback != null) fileCallback.onReceiveValue(null);
      fileCallback = callback;
      Intent intent =
          new Intent(Intent.ACTION_OPEN_DOCUMENT)
              .addCategory(Intent.CATEGORY_OPENABLE)
              .setType("*/*");
      String[] types = params.getAcceptTypes();
      ArrayList<String> mimeTypes = new ArrayList<>();
      if (types != null)
        for (String accept : types) {
          if (accept == null) continue;
          for (String candidate : accept.split(",")) {
            candidate = candidate.trim();
            if (candidate.matches("[A-Za-z0-9!#$&^_.+*\\-]+/[A-Za-z0-9!#$&^_.+*\\-]+"))
              mimeTypes.add(candidate);
          }
        }
      if (!mimeTypes.isEmpty()) {
        intent.putExtra(Intent.EXTRA_MIME_TYPES, mimeTypes.toArray(new String[0]));
        if (mimeTypes.size() == 1) intent.setType(mimeTypes.get(0));
      }
      intent.putExtra(
          Intent.EXTRA_ALLOW_MULTIPLE, params.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE);
      try {
        startActivityForResult(intent, FILE_REQUEST);
      } catch (ActivityNotFoundException ex) {
        callback.onReceiveValue(null);
        fileCallback = null;
        toast("На устройстве нет выбора файлов.");
      }
      return true;
    }
  }

  private void fillHubLogin(Tab tab, String url) {
    if (hub == null || !HubUrlPolicy.sameOrigin(hub, url)) return;
    String path;
    try {
      path = URI.create(url).getPath();
    } catch (IllegalArgumentException ex) {
      return;
    }
    if (!("/login".equals(path) || "/".equals(path) || "".equals(path))) return;
    String name = pendingUsername;
    String password = pendingPassword;
    // Match the known top-level Hub form; never autofill LuCI, proxied routers or other origins.
    String script =
        "(function(){if(location.origin!==new URL("
            + JSONObject.quote(hub.toString())
            + ").origin||!['/','/login',''].includes(location.pathname))return 'skip';const"
            + " f=document.getElementById('passwordLoginForm');if(!f||new"
            + " URL(f.action,location.href).origin!==location.origin||new"
            + " URL(f.action,location.href).pathname!=='/login')return 'skip';const"
            + " u=f.querySelector('#hubUsername'),p=f.querySelector('#hubPassword');if(!u||!p)return"
            + " 'skip';if(!u.value&&"
            + JSONObject.quote(name)
            + "){u.value="
            + JSONObject.quote(name)
            + ";u.dispatchEvent(new Event('input',{bubbles:true}));u.dispatchEvent(new"
            + " Event('change',{bubbles:true}));}if(!p.value&&"
            + JSONObject.quote(password)
            + "){p.value="
            + JSONObject.quote(password)
            + ";p.dispatchEvent(new Event('input',{bubbles:true}));}const"
            + " b=document.getElementById('passkeyLoginBtn');if(b){b.disabled=true;b.title='Passkey"
            + " ещё не поддерживается в этой сборке Android';}return 'filled';})()";
    tab.web.evaluateJavascript(
        script,
        value -> {
          if ("\"filled\"".equals(value)) {
            pendingUsername = "";
            pendingPassword = "";
          }
        });
  }

  private void showError(Tab tab, String heading, String message) {
    if (tab.disposed) return;
    promote(tab);
    if (tab == activeTab()) progress.setVisibility(View.GONE);
    hideError(tab);
    tab.error = new LinearLayout(this);
    tab.error.setOrientation(LinearLayout.VERTICAL);
    tab.error.setGravity(Gravity.CENTER);
    tab.error.setPadding(dp(25), dp(24), dp(25), dp(24));
    tab.error.setBackgroundColor(BG);
    TextView h = label(heading, 23, TEXT, true);
    h.setGravity(Gravity.CENTER);
    tab.error.addView(h);
    TextView text = addText(tab.error, message, 14, MUTED, 15, 24);
    text.setGravity(Gravity.CENTER);
    Button retry = button("Повторить", true);
    retry.setOnClickListener(
        v -> {
          hideError(tab);
          if (!tab.lastUrl.isEmpty()) tab.web.loadUrl(tab.lastUrl);
          else tab.web.loadUrl(hub.resolve("/").toString());
        });
    tab.error.addView(retry, new LinearLayout.LayoutParams(-1, dp(48)));
    Button address = button("Изменить адрес Hub", false);
    address.setOnClickListener(v -> showConnection());
    LinearLayout.LayoutParams margin = new LinearLayout.LayoutParams(-1, dp(48));
    margin.topMargin = dp(12);
    tab.error.addView(address, margin);
    tab.frame.addView(tab.error, new FrameLayout.LayoutParams(-1, -1));
  }

  private void hideError(Tab tab) {
    if (tab.error != null) {
      tab.frame.removeView(tab.error);
      tab.error = null;
    }
  }

  private void beginDownload(String url, String agent, String disposition, String mime) {
    if (hub == null || !HubUrlPolicy.sameOrigin(hub, url)) {
      toast("Внешние файлы открывайте по ссылке в браузере.");
      return;
    }
    if (pendingDownload != null) {
      toast("Сначала завершите выбор файла.");
      return;
    }
    String filename =
        android.webkit.URLUtil.guessFileName(url, disposition, mime)
            .replaceAll("[\\\\/:*?\"<>|\\p{Cntrl}]", "_");
    if (filename.length() > 160) filename = filename.substring(filename.length() - 160);
    pendingDownload =
        new Download(
            url,
            mime == null ? "application/octet-stream" : mime,
            filename,
            CookieManager.getInstance().getCookie(url),
            agent);
    Intent save =
        new Intent(Intent.ACTION_CREATE_DOCUMENT)
            .addCategory(Intent.CATEGORY_OPENABLE)
            .setType(pendingDownload.mime)
            .putExtra(Intent.EXTRA_TITLE, filename);
    try {
      startActivityForResult(save, SAVE_REQUEST);
    } catch (ActivityNotFoundException ex) {
      pendingDownload = null;
      toast("На устройстве нет выбора папки для сохранения.");
    }
  }

  @Override
  protected void onActivityResult(int request, int result, Intent data) {
    super.onActivityResult(request, result, data);
    if (request == FILE_REQUEST) {
      ValueCallback<Uri[]> callback = fileCallback;
      fileCallback = null;
      if (callback != null) {
        ArrayList<Uri> uris = new ArrayList<>();
        if (result == RESULT_OK && data != null) {
          if (data.getClipData() != null)
            for (int i = 0; i < data.getClipData().getItemCount(); i++)
              addContentUri(uris, data.getClipData().getItemAt(i).getUri());
          else addContentUri(uris, data.getData());
        }
        callback.onReceiveValue(uris.isEmpty() ? null : uris.toArray(new Uri[0]));
      }
    } else if (request == SAVE_REQUEST) {
      Download download = pendingDownload;
      pendingDownload = null;
      if (download != null
          && result == RESULT_OK
          && data != null
          && data.getData() != null
          && "content".equals(data.getData().getScheme())) saveDownload(download, data.getData());
    }
  }

  private void addContentUri(List<Uri> out, Uri uri) {
    if (uri != null && "content".equals(uri.getScheme())) out.add(uri);
  }

  private void saveDownload(Download task, Uri destination) {
    final URI origin = hub;
    toast("Сохраняю файл…");
    io.execute(
        () -> {
          HttpURLConnection connection = null;
          String error = null;
          try {
            String url = task.url;
            for (int redirects = 0; redirects < 5; redirects++) {
              if (!HubUrlPolicy.sameOrigin(origin, url))
                throw new java.io.IOException("external redirect");
              connection = openConnection(url);
              if (task.cookie != null) connection.setRequestProperty("Cookie", task.cookie);
              if (task.userAgent != null)
                connection.setRequestProperty("User-Agent", task.userAgent);
              int status = connection.getResponseCode();
              if (status >= 300 && status < 400) {
                String next = connection.getHeaderField("Location");
                if (next == null) throw new java.io.IOException("missing redirect");
                url = URI.create(url).resolve(next).toString();
                connection.disconnect();
                connection = null;
                if ("/login".equals(URI.create(url).getPath()))
                  throw new ReauthenticationException();
                continue;
              }
              if (status != 200) throw new java.io.IOException("download failed");
              break;
            }
            if (connection == null || connection.getResponseCode() != 200)
              throw new java.io.IOException("redirect limit");
            String contentType = connection.getContentType();
            String disposition = connection.getHeaderField("Content-Disposition");
            if ("/login".equals(URI.create(url).getPath())
                || (contentType != null
                    && contentType.toLowerCase(java.util.Locale.ROOT).contains("text/html")
                    && (disposition == null
                        || !disposition.toLowerCase(java.util.Locale.ROOT).contains("attachment"))))
              throw new ReauthenticationException();
            if (connection.getContentLengthLong() > 128L * 1024 * 1024)
              throw new java.io.IOException("oversized");
            try (InputStream in = connection.getInputStream();
                OutputStream out = getContentResolver().openOutputStream(destination, "wt")) {
              if (out == null) throw new java.io.IOException("no output");
              byte[] buffer = new byte[32768];
              long written = 0;
              int n;
              while ((n = in.read(buffer)) != -1) {
                written += n;
                if (written > 128L * 1024 * 1024 || Thread.currentThread().isInterrupted())
                  throw new java.io.IOException("interrupted");
                out.write(buffer, 0, n);
              }
            }
          } catch (ReauthenticationException ex) {
            error = "Сессия Hub закончилась. Войдите в панель повторно и повторите скачивание.";
          } catch (Exception ex) {
            error = "Скачать файл не удалось. Если файл уже создан, он может быть неполным.";
          } finally {
            if (connection != null) connection.disconnect();
          }
          final String outcome = error;
          ui.post(
              () -> {
                if (!isDestroyed())
                  toast(outcome == null ? "Файл сохранён: " + task.filename : outcome);
              });
        });
  }

  private static final class ReauthenticationException extends java.io.IOException {}

  private HttpURLConnection openConnection(String url) throws java.io.IOException {
    HttpURLConnection c = (HttpURLConnection) new java.net.URL(url).openConnection();
    c.setConnectTimeout(10000);
    c.setReadTimeout(15000);
    c.setInstanceFollowRedirects(false);
    c.setRequestProperty("User-Agent", "OWRTHubAndroid/" + BuildConfig.VERSION_NAME);
    c.setRequestProperty("Cache-Control", "no-cache");
    return c;
  }

  private void showMenu(View anchor) {
    PopupMenu menu = new PopupMenu(this, anchor);
    menu.getMenu().add(0, 2, 0, "Обновить инструмент");
    menu.getMenu().add(0, 3, 1, "Закрыть инструмент");
    menu.setOnMenuItemClickListener(
        item -> {
          Tab active = activeTab();
          switch (item.getItemId()) {
            case 2:
              if (active != null && !connectingScreen) {
                hideError(active);
                active.web.reload();
              }
              break;
            case 3:
              if (tabs.size() > 1) closeTab(active);
              else finish();
              break;
            default:
              return false;
          }
          return true;
        });
    menu.show();
  }

  private void confirmLogout() {
    new AlertDialog.Builder(this)
        .setTitle("Выйти из Hub?")
        .setMessage("Сохранённая сессия приложения будет удалена. Адрес панели останется.")
        .setNegativeButton("Отмена", null)
        .setPositiveButton(
            "Выйти",
            (d, w) -> {
              final URI logoutHub = hub;
              final String cookie =
                  logoutHub == null
                      ? null
                      : CookieManager.getInstance()
                          .getCookie(logoutHub.resolve("/logout").toString());
              if (activeTab() != null) activeTab().web.clearCache(true);
              closeAllTabs();
              pendingUsername = "";
              pendingPassword = "";
              WebStorage.getInstance().deleteAllData();
              CookieManager.getInstance()
                  .removeAllCookies(
                      ok -> {
                        CookieManager.getInstance().flush();
                        showConnection();
                      });
              if (logoutHub != null && cookie != null)
                io.execute(
                    () -> {
                      boolean revoked = false;
                      HttpURLConnection request = null;
                      try {
                        request = openConnection(logoutHub.resolve("/logout").toString());
                        request.setRequestProperty("Cookie", cookie);
                        int code = request.getResponseCode();
                        revoked = code >= 200 && code < 400;
                      } catch (Exception ignored) {
                      } finally {
                        if (request != null) request.disconnect();
                      }
                      if (!revoked)
                        ui.post(
                            () -> {
                              if (!isDestroyed())
                                toast(
                                    "Локальный выход выполнен. Закрытие сессии на Hub не"
                                        + " подтверждено: сервер недоступен.");
                            });
                    });
            })
        .show();
  }

  private void openExternal(String url) {
    try {
      startActivity(
          new Intent(Intent.ACTION_VIEW, Uri.parse(url)).addCategory(Intent.CATEGORY_BROWSABLE));
    } catch (ActivityNotFoundException ex) {
      toast("Нет приложения для этой ссылки.");
    }
  }

  @SuppressWarnings("deprecation")
  @Override
  public void onBackPressed() {
    navigateBack();
  }

  private void navigateBack() {
    if (connectingScreen) {
      connectionAttempt++;
      pendingPassword = "";
      if (!tabs.isEmpty()) showBrowser();
      else finish();
      return;
    }
    Tab active = activeTab();
    if (active != null && active.web.canGoBack()) active.web.goBack();
    else if (tabs.size() > 1) closeTab(active);
    else
      new AlertDialog.Builder(this)
          .setTitle("Закрыть приложение?")
          .setMessage("Сессия Hub сохранится для следующего входа.")
          .setNegativeButton("Остаться", null)
          .setPositiveButton("Закрыть", (d, w) -> finish())
          .show();
  }

  private void closeTab(Tab tab) {
    if (tab == null || tab.disposed) return;
    tab.disposed = true;
    tabs.remove(tab);
    allTabs.remove(tab);
    body.removeView(tab.frame);
    tab.web.stopLoading();
    tab.web.loadUrl("about:blank");
    tab.frame.removeView(tab.web);
    tab.web.destroy();
    showTopTab();
  }

  private void closeAllTabs() {
    for (Tab tab : new ArrayList<>(allTabs)) closeTab(tab);
  }

  @Override
  protected void onPause() {
    super.onPause();
    pendingPassword = "";
    CookieManager.getInstance().flush();
  }

  @Override
  protected void onDestroy() {
    connectionAttempt++;
    pendingPassword = "";
    if (fileCallback != null) {
      fileCallback.onReceiveValue(null);
      fileCallback = null;
    }
    closeAllTabs();
    io.shutdownNow();
    super.onDestroy();
  }

  private int dp(int value) {
    return Math.round(value * getResources().getDisplayMetrics().density);
  }

  private TextView label(String text, int size, int color, boolean bold) {
    TextView view = new TextView(this);
    view.setText(text);
    view.setTextSize(size);
    view.setTextColor(color);
    if (bold) view.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
    return view;
  }

  private TextView addText(
      LinearLayout parent, String text, int size, int color, int top, int bottom) {
    TextView view = label(text, size, color, false);
    LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
    params.topMargin = dp(top);
    params.bottomMargin = dp(bottom);
    parent.addView(view, params);
    return view;
  }

  private EditText field(LinearLayout parent, String text, String hint, int id, int type) {
    addText(parent, text, 12, MUTED, 0, 7);
    EditText input = new EditText(this);
    if (id != 0) input.setId(id);
    input.setInputType(type);
    input.setSingleLine(true);
    input.setTextColor(TEXT);
    input.setHintTextColor(MUTED);
    input.setTextSize(14);
    input.setHint(hint);
    input.setPadding(dp(13), dp(10), dp(13), dp(10));
    input.setBackground(shape(PANEL, LINE, 11));
    LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, dp(44));
    params.bottomMargin = dp(10);
    parent.addView(input, params);
    return input;
  }

  private Button button(String text, boolean primary) {
    Button button = new Button(this);
    button.setText(text);
    button.setAllCaps(false);
    button.setTextSize(13);
    button.setTextColor(primary ? TEXT : BLUE);
    button.setBackground(
        shape(
            primary ? Color.rgb(35, 90, 138) : PANEL,
            primary ? Color.rgb(56, 121, 180) : LINE,
            12));
    return button;
  }

  private Button smallButton(String text, String description) {
    Button button = new Button(this);
    button.setText(text);
    button.setTextColor(TEXT);
    button.setTextSize(18);
    button.setAllCaps(false);
    button.setContentDescription(description);
    button.setMinWidth(0);
    button.setMinimumWidth(0);
    button.setPadding(0, 0, 0, 0);
    button.setBackgroundColor(Color.TRANSPARENT);
    return button;
  }

  private GradientDrawable shape(int color, int stroke, int radius) {
    GradientDrawable drawable = new GradientDrawable();
    drawable.setColor(color);
    drawable.setCornerRadius(dp(radius));
    drawable.setStroke(dp(1), stroke);
    return drawable;
  }

  private void toast(String message) {
    Toast.makeText(this, message, Toast.LENGTH_LONG).show();
  }
}
