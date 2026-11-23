package ru.owrthub.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Intent;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.PersistableBundle;
import android.os.SystemClock;
import android.text.InputFilter;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;
import java.io.IOException;
import java.io.OutputStream;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.text.DateFormat;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.function.Consumer;
import org.json.JSONArray;
import org.json.JSONObject;

/** Native tools scoped to one explicitly selected router. No browser or JavaScript bridge. */
public final class NativeRouterActivity extends Activity {
  public static final String EXTRA_ROUTER_JSON = "router_json",
      EXTRA_HUB_ORIGIN = "hub_origin",
      EXTRA_TOOL = "tool";
  private static final int SAVE_CONFIG = 8101;
  private final Handler handler = new Handler(Looper.getMainLooper());
  private final ExecutorService network = Executors.newSingleThreadExecutor();
  private final List<Button> actions = new ArrayList<>();
  private NativeUi ui;
  private HubApiClient api;
  private JSONObject router, snapshot;
  private String routerId,
      base,
      tool,
      sshSession = "",
      lastHistory = "",
      configuration = "",
      pendingConfiguration = "";
  private LinearLayout content, history;
  private TextView error, status, detail, result, timer, output;
  private ProgressBar progress;
  private EditText notes, mac, iface, password, command;
  private Button clearButton, liveButton, sshConnect, sshSend, sshInterrupt;
  private volatile boolean visible;
  private volatile int generation;
  private boolean busy, sessionReady, live = true, snapshotFresh, notesLoaded, vpsOwner;
  private Boolean notesCanAppend;
  private long serverTime, monotonicBase;
  private final StringBuilder console = new StringBuilder();
  private final Runnable poll = this::refresh;
  private final Runnable tick =
      new Runnable() {
        @Override
        public void run() {
          if (!visible || !"xray".equals(tool)) return;
          updateTimer();
          if (hasActiveCommand()) handler.postDelayed(this, 1000);
        }
      };

  @Override
  public void onCreate(Bundle savedState) {
    super.onCreate(savedState);
    ui =
        new NativeUi(
            this, getSharedPreferences("native_design", MODE_PRIVATE).getBoolean("winter", false));
    getWindow().setStatusBarColor(ui.BG);
    getWindow().setNavigationBarColor(ui.BG);
    getWindow().setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);
    try {
      tool = getIntent().getStringExtra(EXTRA_TOOL);
      router =
          "vps".equals(tool)
              ? new JSONObject().put("id", "__vps__").put("name", "VPS")
              : new JSONObject(getIntent().getStringExtra(EXTRA_ROUTER_JSON));
      routerId = router.optString("id", "");
      if (routerId.isEmpty() || routerId.length() > 64 || !routerId.matches("[\\p{L}\\p{N}._-]+"))
        throw new IOException("Не выбран роутер");
      if (!Arrays.asList("xray", "logs", "notes", "wol", "ssh", "vps", "config").contains(tool))
        throw new IOException("Неизвестный инструмент");
      URI hub =
          HubUrlPolicy.normalizeHub(
              getIntent().getStringExtra(EXTRA_HUB_ORIGIN), "", BuildConfig.DEBUG);
      api = new HubApiClient(hub, new HubSessionStore(this));
      base = "/api/router/" + Uri.encode(routerId);
      buildScreen();
    } catch (Exception failure) {
      LinearLayout view = ui.column();
      view.setPadding(ui.dp(20), ui.dp(50), ui.dp(20), ui.dp(20));
      view.setBackgroundColor(ui.BG);
      view.addView(ui.title("Инструмент недоступен"));
      view.addView(ui.spacer(16));
      view.addView(ui.body("Откройте инструмент из карточки выбранного роутера."));
      view.addView(ui.spacer(24));
      view.addView(ui.button("Назад", false, this::finish));
      setContentView(view);
      api = null;
    }
  }

  private void buildScreen() {
    LinearLayout root = ui.column();
    root.setBackgroundColor(ui.BG);
    root.setOnApplyWindowInsetsListener(
        (view, insets) -> {
          view.setPadding(
              insets.getSystemWindowInsetLeft(),
              insets.getSystemWindowInsetTop(),
              insets.getSystemWindowInsetRight(),
              insets.getSystemWindowInsetBottom());
          return insets;
        });
    LinearLayout toolbar = ui.row();
    toolbar.setPadding(ui.dp(6), ui.dp(4), ui.dp(10), ui.dp(4));
    toolbar.addView(ui.iconButton("back", "Назад к роутеру", this::finish));
    TextView heading = ui.text(toolTitle(), 20, true);
    toolbar.addView(heading, new LinearLayout.LayoutParams(0, -2, 1));
    toolbar.addView(
        ui.iconButton(
            "refresh",
            "Обновить состояние",
            () -> {
              if (!busy) {
                handler.removeCallbacks(poll);
                bootstrap();
              }
            }));
    root.addView(toolbar);
    progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
    progress.setIndeterminate(true);
    progress.setIndeterminateTintList(android.content.res.ColorStateList.valueOf(ui.BLUE));
    root.addView(progress, new LinearLayout.LayoutParams(-1, ui.dp(2)));
    error = ui.text("", 14, false);
    error.setTextColor(ui.RED);
    error.setPadding(ui.dp(12), ui.dp(12), ui.dp(12), ui.dp(12));
    error.setBackground(ui.shape(NativeUi.alpha(ui.RED, .06f), NativeUi.alpha(ui.RED, .25f), 12));
    error.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
    error.setVisibility(View.GONE);
    root.addView(error);

    ScrollView scroll = new ScrollView(this);
    scroll.setFillViewport(true);
    scroll.setClipToPadding(false);
    content = ui.column();
    content.setPadding(ui.dp(18), ui.dp(14), ui.dp(18), ui.dp(28));
    scroll.addView(content);
    root.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
    setContentView(root);
    root.requestApplyInsets();

    LinearLayout hero = ui.row();
    hero.addView(
        ui.iconBadge(
            tool.equals("xray")
                ? "play"
                : tool.equals("wol")
                    ? "wifi"
                    : isTerminal()
                        ? "terminal"
                        : tool.equals("notes")
                            ? "notes"
                            : tool.equals("config") ? "key" : "history",
            ui.BLUE,
            48));
    LinearLayout copy = ui.column();
    copy.setPadding(ui.dp(12), 0, 0, 0);
    copy.addView(ui.text(router.optString("name", routerId), 20, true));
    copy.addView(ui.spacer(5));
    copy.addView(ui.label(tool.equals("vps") ? api.getHub().getHost() : routerId));
    hero.addView(copy, new LinearLayout.LayoutParams(0, -2, 1));
    content.addView(hero);
    content.addView(ui.spacer(20));
    switch (tool) {
      case "xray":
        buildXray();
        break;
      case "logs":
        buildLogs();
        break;
      case "notes":
        buildNotes();
        break;
      case "wol":
        buildWol();
        break;
      case "ssh":
        buildSsh();
        break;
      case "vps":
        buildSsh();
        break;
      case "config":
        buildConfig();
        break;
    }
    updateControls();
  }

  private String toolTitle() {
    switch (tool) {
      case "xray":
        return "Управление Xray";
      case "logs":
        return "Журнал роутера";
      case "notes":
        return "Заметки";
      case "wol":
        return "Wake-on-LAN";
      case "config":
        return "Конфигурация OpenWrt";
      case "vps":
        return "Терминал VPS";
      default:
        return "SSH-консоль";
    }
  }

  private void buildXray() {
    intro(
        "Команды доставляются агенту при следующем Heartbeat. Статус и результат подтверждает"
            + " Hub.");
    LinearLayout card = ui.card();
    status = ui.text("Получаю состояние…", 18, true);
    detail = ui.body("");
    card.addView(status);
    card.addView(ui.spacer(8));
    card.addView(detail);
    add(card);
    action("Запустить Xray", NativeUi.Style.PRIMARY, () -> xrayAction("start"));
    action(
        "Остановить Xray",
        NativeUi.Style.DANGER,
        () ->
            confirm(
                "Остановить Xray?",
                "Команда будет отправлена выбранному роутеру. Агент сохранит связь с Hub.",
                () -> xrayAction("stop")));
    action("Перезапустить Xray", NativeUi.Style.SECONDARY, () -> xrayAction("restart"));
    LinearLayout resultCard = ui.card();
    resultCard.addView(ui.text("Результат команды", 16, true));
    resultCard.addView(ui.spacer(12));
    result = ui.text("Команд пока нет", 16, true);
    output = monospace("");
    timer = ui.text("", 14, false);
    timer.setTextColor(ui.BLUE);
    resultCard.addView(result);
    resultCard.addView(ui.spacer(8));
    resultCard.addView(output);
    resultCard.addView(ui.spacer(12));
    resultCard.addView(timer);
    add(resultCard);
    content.addView(ui.text("Журнал действий", 18, true));
    content.addView(ui.spacer(12));
    clearButton =
        ui.button(
            "Очистить завершённые записи",
            NativeUi.Style.DANGER,
            () ->
                confirm(
                    "Очистить журнал Xray?",
                    "Завершённые записи и их вывод будут удалены. Текущая команда продолжит"
                        + " работу.",
                    () -> xrayAction("clear_history")));
    add(clearButton);
    history = ui.column();
    content.addView(history);
  }

  private void buildLogs() {
    intro(
        "Журнал Heartbeat, WAN и Xray выбранного роутера. Для роутера оффлайн доступен последний"
            + " архив Hub.");
    LinearLayout card = ui.card();
    status = ui.text("Получаю журнал…", 16, true);
    detail = ui.body("");
    card.addView(status);
    card.addView(ui.spacer(8));
    card.addView(detail);
    add(card);
    liveButton =
        ui.button(
            "Приостановить обновление",
            false,
            () -> {
              live = !live;
              liveButton.setText(live ? "Приостановить обновление" : "Включить живой журнал");
              handler.removeCallbacks(poll);
              if (live && !busy) refresh();
            });
    add(liveButton);
    clearButton =
        ui.button(
            "Очистить журнал",
            NativeUi.Style.DANGER,
            () ->
                confirm(
                    "Очистить журнал роутера?",
                    "Архив Hub будет удалён. Очистка журнала роутера произойдёт при следующем"
                        + " соединении.",
                    () ->
                        request(
                            () -> api.post(base + "/logs/clear", new JSONObject()),
                            this::renderLogs,
                            true)));
    add(clearButton);
    output = monospace("Загрузка…");
    output.setMinHeight(ui.dp(280));
    output.setBackground(ui.shape(NativeUi.color("#090e13"), ui.LINE, 12));
    output.setPadding(ui.dp(14), ui.dp(14), ui.dp(14), ui.dp(14));
    add(output);
  }

  private void buildNotes() {
    intro(
        "Добавляйте отдельные записи о роутере. История и существующие вложения сохраняются в"
            + " Hub.");
    content.addView(ui.label("Новая запись"));
    content.addView(ui.spacer(8));
    notes = ui.field("Что нужно запомнить об этом роутере?", "", false);
    notes.setSingleLine(false);
    notes.setInputType(
        InputType.TYPE_CLASS_TEXT
            | InputType.TYPE_TEXT_FLAG_MULTI_LINE
            | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES);
    notes.setTypeface(ui.regular);
    notes.setGravity(Gravity.TOP);
    notes.setMinLines(9);
    notes.setFilters(new InputFilter[] {new InputFilter.LengthFilter(2000)});
    add(notes);
    action(
        "Добавить заметку",
        NativeUi.Style.PRIMARY,
        () -> {
          final String draft = notes.getText().toString().trim();
          if (draft.isEmpty()) {
            showError("Введите текст новой заметки.");
            return;
          }
          request(
              () -> api.post(notesPath(), new JSONObject().put("text", draft)),
              data -> {
                renderNotes(data);
                notes.setText("");
                status.setText("Новая запись добавлена в Hub");
                status.setTextColor(ui.GREEN);
              },
              false);
        });
    status = ui.text("До 2000 символов в одной записи", 14, false);
    status.setTextColor(ui.MUTED);
    add(status);
    content.addView(ui.text("История заметок", 18, true));
    content.addView(ui.spacer(12));
    history = ui.column();
    content.addView(history);
  }

  private void buildWol() {
    intro(
        "Hub отправит Magic Packet из локальной сети выбранного роутера. Устройство должно"
            + " поддерживать пробуждение по сети.");
    mac = ui.field("AA:BB:CC:DD:EE:FF", "", false);
    mac.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
    add(ui.labeledField("MAC-адрес устройства", mac));
    iface = ui.field("Автоматический выбор", "", false);
    add(ui.labeledField("Интерфейс роутера (необязательно)", iface));
    password = ui.field("Пароль SSH роутера", "", true);
    protectInput(password);
    add(ui.labeledField("Пароль SSH, если требуется", password));
    action(
        "Разбудить устройство",
        NativeUi.Style.PRIMARY,
        () -> {
          String target = mac.getText().toString().trim().replace('-', ':');
          if (!target.matches("(?i)([0-9a-f]{2}:){5}[0-9a-f]{2}")) {
            showError("Укажите MAC-адрес в формате AA:BB:CC:DD:EE:FF.");
            return;
          }
          String interfaceName = iface.getText().toString().trim();
          if (!interfaceName.matches("[A-Za-z0-9_.:@-]{0,32}")) {
            showError("Проверьте имя сетевого интерфейса.");
            return;
          }
          String secret = password.getText().toString();
          password.setText("");
          request(
              () -> {
                JSONObject payload =
                    new JSONObject()
                        .put("mac", target)
                        .put("iface", interfaceName)
                        .put("ssh_password", secret);
                try {
                  return api.post(base + "/wol", payload);
                } finally {
                  payload.remove("ssh_password");
                }
              },
              data -> {
                requireRouter(data);
                status.setText("Пакет пробуждения отправлен");
                status.setTextColor(ui.GREEN);
                detail.setText(
                    data.optString(
                        "output", "Пробуждение устройства может занять некоторое время."));
              },
              false);
        });
    LinearLayout resultCard = ui.card();
    status = ui.text("Готово к отправке", 16, true);
    detail = ui.body("Пароль используется для одного запроса и не сохраняется.");
    resultCard.addView(status);
    resultCard.addView(ui.spacer(8));
    resultCard.addView(detail);
    add(resultCard);
  }

  private void buildSsh() {
    intro(
        tool.equals("vps")
            ? "Root-консоль вашего VPS. Построчный ввод команд; для интерактивных редакторов нужен"
                  + " полноценный терминал."
            : "Построчная SSH-консоль выбранного роутера. Для интерактивных редакторов нужен"
                  + " полноценный терминал.");
    status = ui.text("Соединение не открыто", 14, false);
    status.setTextColor(ui.MUTED);
    add(status);
    sshConnect =
        ui.button(
            tool.equals("vps") ? "Открыть терминал VPS" : "Подключить SSH", true, this::openSsh);
    add(sshConnect);
    output = monospace("");
    output.setMinHeight(ui.dp(280));
    output.setBackground(ui.shape(NativeUi.color("#090e13"), ui.LINE, 12));
    output.setPadding(ui.dp(14), ui.dp(14), ui.dp(14), ui.dp(14));
    add(output);
    command = ui.field("Команда или ответ на запрос SSH", "", false);
    protectInput(command);
    add(command);
    Switch hidden = new Switch(this);
    hidden.setText("Скрывать ввод пароля");
    hidden.setTextColor(ui.MUTED);
    hidden.setTextSize(14);
    hidden.setTypeface(ui.regular);
    hidden.setMinHeight(ui.dp(48));
    hidden.setThumbTintList(
        new android.content.res.ColorStateList(
            new int[][] {{android.R.attr.state_checked}, {}}, new int[] {ui.BLUE, ui.MUTED}));
    hidden.setTrackTintList(
        new android.content.res.ColorStateList(
            new int[][] {{android.R.attr.state_checked}, {}},
            new int[] {NativeUi.alpha(ui.BLUE, .4f), ui.LINE}));
    hidden.setOnCheckedChangeListener(
        (view, checked) -> {
          command.setInputType(
              InputType.TYPE_CLASS_TEXT
                  | (checked
                      ? InputType.TYPE_TEXT_VARIATION_PASSWORD
                      : InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS));
          command.setTypeface(ui.regular);
        });
    add(hidden);
    sshSend =
        ui.button(
            "Отправить ↵",
            true,
            () -> {
              String value = command.getText().toString() + "\r";
              command.setText("");
              writeSsh(value);
            });
    add(sshSend);
    sshInterrupt = ui.button("Прервать · Ctrl+C", false, () -> writeSsh("\u0003"));
    add(sshInterrupt);
    add(
        ui.button(
            "Закрыть соединение",
            NativeUi.Style.QUIET,
            () -> {
              closeSsh();
              updateControls();
            }));
  }

  private void buildConfig() {
    getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
    intro(
        "Команды настройки OpenWrt для выбранного роутера. Они содержат ключи подключения к Hub."
            + " Скопируйте или сохраните их в файл и выполните на этом роутере.");
    status = ui.text("Получаю конфигурацию…", 14, false);
    status.setTextColor(ui.MUTED);
    add(status);
    action(
        "Скопировать команды",
        NativeUi.Style.PRIMARY,
        () -> {
          if (configuration.isEmpty()) return;
          ClipData clip = ClipData.newPlainText("Конфигурация OpenWrt", configuration);
          PersistableBundle extras = new PersistableBundle();
          extras.putBoolean("android.content.extra.IS_SENSITIVE", true);
          clip.getDescription().setExtras(extras);
          ((ClipboardManager) getSystemService(CLIPBOARD_SERVICE)).setPrimaryClip(clip);
          Toast.makeText(this, "Команды скопированы", Toast.LENGTH_SHORT).show();
        });
    action(
        "Сохранить файл .sh",
        NativeUi.Style.SECONDARY,
        () -> {
          if (configuration.isEmpty()) return;
          pendingConfiguration = configuration;
          Intent save =
              new Intent(Intent.ACTION_CREATE_DOCUMENT)
                  .addCategory(Intent.CATEGORY_OPENABLE)
                  .setType("text/x-shellscript")
                  .putExtra(Intent.EXTRA_TITLE, "owrt-" + routerId + "-config.sh");
          startActivityForResult(save, SAVE_CONFIG);
        });
    output = monospace("");
    output.setMinHeight(ui.dp(280));
    output.setPadding(ui.dp(14), ui.dp(14), ui.dp(14), ui.dp(14));
    output.setBackground(ui.shape(NativeUi.color("#090e13"), ui.LINE, 12));
    add(output);
  }

  private boolean isTerminal() {
    return "ssh".equals(tool) || "vps".equals(tool);
  }

  private void protectInput(EditText field) {
    field.setSaveEnabled(false);
    field.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS);
  }

  private void intro(String text) {
    add(ui.body(text));
  }

  private void add(View view) {
    content.addView(view);
    content.addView(ui.spacer(12));
  }

  private TextView monospace(String value) {
    TextView view = ui.text(value, 14, false);
    view.setTypeface(Typeface.MONOSPACE);
    view.setTextColor(ui.GREEN);
    view.setTextIsSelectable(true);
    view.setLineSpacing(ui.dp(4), 1);
    return view;
  }

  private void action(String label, NativeUi.Style style, Runnable callback) {
    Button button = ui.button(label, style, callback);
    actions.add(button);
    add(button);
  }

  @Override
  protected void onResume() {
    super.onResume();
    visible = true;
    generation++;
    if (api != null && !busy) bootstrap();
  }

  @Override
  protected void onPause() {
    visible = false;
    generation++;
    handler.removeCallbacks(poll);
    handler.removeCallbacks(tick);
    if (password != null) password.setText("");
    if (command != null) command.setText("");
    closeSsh();
    super.onPause();
  }

  @Override
  protected void onDestroy() {
    handler.removeCallbacksAndMessages(null);
    network.shutdown();
    super.onDestroy();
  }

  private void bootstrap() {
    sessionReady = false;
    snapshotFresh = false;
    if (tool.equals("notes")) notesLoaded = false;
    request(
        api::session,
        session -> {
          JSONObject account = session.optJSONObject("account");
          vpsOwner = account != null && account.optBoolean("is_owner");
          if (tool.equals("vps") && !vpsOwner) {
            deny("Терминал VPS доступен только владельцу Hub.");
            return;
          }
          if (!tool.equals("vps")) {
            JSONObject assigned = account == null ? null : account.optJSONObject("router_flags");
            JSONArray flags = assigned == null ? null : assigned.optJSONArray(routerId);
            boolean admin = vpsOwner, ssh = vpsOwner, manage = vpsOwner;
            for (int i = 0; flags != null && i < flags.length(); i++) {
              String flag = flags.optString(i);
              manage |= flag.equals("G");
              ssh |= flag.equals("B") || flag.equals("G");
              admin |= flag.equals("A") || flag.equals("B") || flag.equals("G");
            }
            try {
              router.put(
                  "permissions",
                  new JSONObject()
                      .put("admin", admin)
                      .put("ssh", ssh)
                      .put("manage", manage)
                      .put("notes", admin)
                      .put("notes_edit", manage)
                      .put("logs", manage)
                      .put("config", manage)
                      .put("wol", manage));
            } catch (org.json.JSONException invalid) {
              showError("Не удалось проверить права на роутер.");
              return;
            }
            if (!admin) {
              deny("Доступ к этому роутеру больше не предоставлен.");
              return;
            }
            if ((!tool.equals("notes") && !tool.equals("ssh") && !manage) || (tool.equals("ssh") && !ssh)) {
              deny("У аккаунта больше нет доступа к этому инструменту.");return;
            }
          }
          content.setVisibility(View.VISIBLE);
          sessionReady = true;
          refresh();
        },
        false);
  }

  private void refresh() {
    if (!visible || busy || api == null || !sessionReady) return;
    if (tool.equals("xray")) request(() -> api.get(base + "/xray-control"), this::renderXray, true);
    else if (tool.equals("logs")) request(() -> api.get(base + "/logs"), this::renderLogs, true);
    else if (tool.equals("notes")) request(() -> api.get(notesPath()), this::renderNotes, false);
    else if (tool.equals("config"))
      request(
          () -> api.get(HubApiClient.MOBILE_BASE + "/router/" + Uri.encode(routerId) + "/config"),
          data -> {
            requireRouter(data);
            String text = data.optString("config", "");
            if (text.isEmpty() || text.length() > 262144)
              throw new IllegalStateException("Invalid configuration");
            configuration = text;
            snapshotFresh = true;
            output.setText(configuration);
            status.setText("Конфигурация получена от Hub");
          },
          false);
    else if (isTerminal() && !sshSession.isEmpty()) {
      final String sid = sshSession;
      request(
          () -> api.get(sshPath(sid) + "/read"),
          data -> {
            if (!sid.equals(sshSession)) return;
            appendConsole(data.optString("data", ""));
            if (!data.optBoolean("alive", false)) {
              sshSession = "";
              status.setText(tool.equals("vps") ? "Терминал VPS закрыт" : "SSH-соединение закрыто");
            }
          },
          true);
    } else updateControls();
  }

  private void request(Callable<JSONObject> call, Consumer<JSONObject> success, boolean repeat) {
    if (busy || !visible || api == null || network.isShutdown()) return;
    handler.removeCallbacks(poll);
    busy = true;
    final int requestGeneration = generation;
    updateControls();
    network.execute(
        () -> {
          JSONObject response = null;
          Exception failure = null;
          try {
            response = call.call();
          } catch (Exception exception) {
            failure = exception;
          }
          // An opening GET may complete after the Activity stopped; close its PTY on this
          // executor even when onDestroy has already rejected new queued work.
          if (response != null
              && response.has("sid")
              && (!visible || generation != requestGeneration)) {
            String abandoned = response.optString("sid", "");
            if (abandoned.matches("[A-Za-z0-9_-]{16,128}"))
              try {
                api.post(sshPath(abandoned) + "/close", new JSONObject());
              } catch (Exception ignored) {
              }
          }
          final JSONObject data = response;
          final Exception problem = failure;
          handler.post(
              () -> {
                busy = false;
                if (!visible || generation != requestGeneration || isFinishing() || isDestroyed()) {
                  if (data != null && data.has("sid")) abandonSsh(data.optString("sid", ""));
                  if (visible && !isFinishing() && !isDestroyed()) bootstrap();
                  return;
                }
                if (problem == null) {
                  error.setVisibility(View.GONE);
                  try {
                    success.accept(data);
                  } catch (RuntimeException invalidData) {
                    snapshotFresh = false;
                    showError("Hub вернул данные другого роутера или неподдерживаемый ответ.");
                  }
                } else {
                  snapshotFresh = false;
                if (problem instanceof HubApiClient.PermissionException || problem instanceof HubApiClient.AuthRequiredException) {
                  sessionReady = false;content.setVisibility(View.GONE);
                }
                  showError(
                      problem instanceof HubApiClient.AuthRequiredException
                          ? "Сессия Hub завершилась. Вернитесь на главный экран и войдите снова."
                          : safeError(problem));
                }
                updateControls();
              if (repeat && !busy && !(problem instanceof HubApiClient.AuthRequiredException)
                  && !(problem instanceof HubApiClient.PermissionException))
                  schedulePoll();
              });
        });
  }

  private String safeError(Exception exception) {
    String message = exception.getMessage();
    return message == null || message.isEmpty()
        ? "Не удалось связаться с Hub. Обновите экран и проверьте результат перед повторной"
              + " отправкой."
        : message.length() > 300 ? "Не удалось выполнить запрос к Hub." : message;
  }

  private void schedulePoll() {
    handler.removeCallbacks(poll);
    if (!visible) return;
    if (tool.equals("xray") || tool.equals("logs") && live) handler.postDelayed(poll, 3000);
    else if (isTerminal() && !sshSession.isEmpty()) handler.postDelayed(poll, 650);
  }

  private void requireRouter(JSONObject data) {
    if (data == null || !routerId.equals(data.optString("router_id")))
      throw new IllegalStateException("Wrong router response");
  }

  private void renderXray(JSONObject data) {
    requireRouter(data);
    snapshot = data;
    snapshotFresh = true;
    serverTime = data.optLong("server_time", 0);
    monotonicBase = SystemClock.elapsedRealtime();
    boolean online = data.optBoolean("online");
    String xray = data.optString("xray", "unknown");
    status.setText(
        (online ? "Xray: " : "Последний статус Xray: ")
            + (xray.equals("running")
                ? "запущен"
                : xray.equals("stopped") ? "остановлен" : "неизвестен"));
    status.setTextColor(xray.equals("running") && online ? ui.GREEN : ui.AMBER);
    detail.setText(
        (online ? "Агент онлайн" : "Агент оффлайн")
            + " · "
            + stamp(data.optLong("last_seen"))
            + (data.optBoolean("supported")
                ? ""
                : "\nАгент пока не поддерживает управление Xray через Heartbeat."));
    JSONArray entries = data.optJSONArray("history");
    JSONObject latest = entries == null ? null : entries.optJSONObject(0);
    result.setText(latest == null ? "Команд пока нет" : stateLabel(latest.optString("status")));
    result.setTextColor(latest == null ? ui.MUTED : stateColor(latest.optString("status")));
    output.setText(latest == null ? "" : latest.optString("result", "Ожидается результат агента"));
    String historyJson = entries == null ? "[]" : entries.toString();
    if (!historyJson.equals(lastHistory)) {
      lastHistory = historyJson;
      history.removeAllViews();
      if (entries != null)
        for (int i = 0; i < entries.length(); i++) {
          JSONObject entry = entries.optJSONObject(i);
          if (entry == null) continue;
          LinearLayout card = ui.card();
          TextView entryTitle =
              ui.text(
                  actionLabel(entry.optString("action"))
                      + " · "
                      + stateLabel(entry.optString("status")),
                  15,
                  true);
          entryTitle.setTextColor(stateColor(entry.optString("status")));
          card.addView(entryTitle);
          card.addView(ui.spacer(8));
          card.addView(ui.label(stamp(entry.optLong("created_at"))));
          String text = entry.optString("result", "");
          if (!text.isEmpty()) {
            card.addView(ui.spacer(10));
            card.addView(monospace(text));
          }
          history.addView(card);
          history.addView(ui.spacer(10));
        }
    }
    handler.removeCallbacks(tick);
    tick.run();
  }

  private void updateTimer() {
    if (snapshot == null || timer == null) return;
    JSONArray entries = snapshot.optJSONArray("history");
    JSONObject latest = entries == null ? null : entries.optJSONObject(0);
    if (latest == null) {
      timer.setText("");
      return;
    }
    long now = serverTime + (SystemClock.elapsedRealtime() - monotonicBase) / 1000;
    long created = latest.optLong("created_at", now), completed = latest.optLong("completed_at", 0);
    String state = latest.optString("status");
    if (!isActive(state)) {
      timer.setText(
          "Ожидание и выполнение: " + duration((completed > 0 ? completed : now) - created));
      return;
    }
    long remaining = Math.max(0, created + snapshot.optLong("command_timeout_seconds", 180) - now);
    String text =
        "Прошло " + duration(now - created) + " · До предела ожидания " + duration(remaining);
    if (remaining == 0) text += "\nПредел ожидания достигнут. Ожидается итоговый статус Hub.";
    else if (!snapshot.optBoolean("online"))
      text += "\nАгент оффлайн. Ожидаем восстановления Heartbeat.";
    else if (state.equals("queued")) {
      long heartbeat =
          snapshot.optLong("last_seen") + snapshot.optLong("heartbeat_interval", 30) - now;
      text +=
          heartbeat > 0
              ? "\nБлижайший Heartbeat: примерно через " + duration(heartbeat)
              : "\nОжидаем ближайший Heartbeat.";
    } else text += "\nОжидаем подтверждения результата от агента.";
    timer.setText(text);
  }

  private boolean hasActiveCommand() {
    JSONArray entries = snapshot == null ? null : snapshot.optJSONArray("history");
    if (entries != null)
      for (int i = 0; i < entries.length(); i++) {
        JSONObject entry = entries.optJSONObject(i);
        if (entry != null && isActive(entry.optString("status"))) return true;
      }
    return false;
  }

  private void xrayAction(String action) {
    if (busy || !snapshotFresh || snapshot == null) return;
    final String requestId = UUID.randomUUID().toString().replace("-", "");
    request(
        () ->
            api.post(
                base + "/xray-control",
                new JSONObject().put("action", action).put("request_id", requestId)),
        this::renderXray,
        true);
  }

  private void renderLogs(JSONObject data) {
    requireRouter(data);
    snapshotFresh = true;
    status.setText(data.optBoolean("online") ? "Роутер онлайн" : "Роутер оффлайн · архив Hub");
    status.setTextColor(data.optBoolean("online") ? ui.GREEN : ui.AMBER);
    JSONObject diagnosis = data.optJSONObject("diagnosis");
    detail.setText(
        (diagnosis == null ? "" : diagnosis.optString("text", "") + "\n")
            + "Последняя доставка: "
            + stamp(data.optLong("last_sync"))
            + (data.optBoolean("pending_clear") ? "\nОчистка роутера ожидает связи." : ""));
    String text = data.optString("text", "");
    output.setText(
        text.isEmpty()
            ? "В журнале пока нет записей."
            : text.substring(Math.max(0, text.length() - 65536)));
  }

  private void updateControls() {
    if (progress == null) return;
    progress.setVisibility(busy ? View.VISIBLE : View.INVISIBLE);
    boolean ready = sessionReady && !busy && permitted() && (!tool.equals("notes") || notesLoaded);
    for (Button action : actions)
      action.setEnabled(
          ready
              && (!tool.equals("xray")
                  || snapshotFresh
                      && snapshot != null
                      && snapshot.optBoolean("supported")
                      && !hasActiveCommand())
              && (!tool.equals("config") || snapshotFresh && !configuration.isEmpty()));
    if (clearButton != null)
      clearButton.setEnabled(
          ready && snapshotFresh && (!tool.equals("xray") || hasCompletedCommand()));
    if (liveButton != null) liveButton.setEnabled(sessionReady);
    if (sshConnect != null) sshConnect.setEnabled(ready && sshSession.isEmpty());
    if (sshSend != null) sshSend.setEnabled(ready && !sshSession.isEmpty());
    if (sshInterrupt != null) sshInterrupt.setEnabled(ready && !sshSession.isEmpty());
    if (notes != null) notes.setEnabled(ready);
  }

  private boolean permitted() {
    if (tool.equals("vps")) return vpsOwner;
    if (tool.equals("notes") && notesCanAppend != null) return notesCanAppend;
    JSONObject permissions = router == null ? null : router.optJSONObject("permissions");
    if (permissions == null) return true; // Older snapshots rely on the authoritative server check.
    if (tool.equals("ssh")) return permissions.optBoolean("ssh");
    if (tool.equals("notes"))
      return permissions.optBoolean("notes_edit") || permissions.optBoolean("manage");
    return permissions.optBoolean(tool.equals("xray") ? "manage" : tool)
        || permissions.optBoolean("manage");
  }

  private String notesPath() {
    return HubApiClient.MOBILE_BASE + "/router/" + Uri.encode(routerId) + "/notes";
  }

  private void renderNotes(JSONObject data) {
    requireRouter(data);
    JSONArray entries = data.optJSONArray("notes_entries");
    if (entries == null) throw new IllegalStateException("Invalid notes response");
    notesLoaded = true;
    notesCanAppend = data.optBoolean("can_append", permitted());
    status.setText(
        notesCanAppend ? "До 2000 символов в одной записи" : "Заметки доступны для просмотра");
    status.setTextColor(ui.MUTED);
    history.removeAllViews();
    if (entries.length() == 0) {
      history.addView(ui.body("Записей пока нет."));
      return;
    }
    for (int i = entries.length() - 1; i >= 0; i--) {
      JSONObject entry = entries.optJSONObject(i);
      if (entry == null) continue;
      LinearLayout card = ui.card();
      card.addView(ui.label(stamp(entry.optLong("created_at"))));
      card.addView(ui.spacer(10));
      TextView text = ui.text(entry.optString("text", ""), 16, false);
      text.setTextIsSelectable(true);
      card.addView(text);
      JSONArray images = entry.optJSONArray("images");
      if (images != null && images.length() > 0) {
        card.addView(ui.spacer(12));
        card.addView(ui.chip("Вложений в Hub: " + images.length(), ui.BLUE));
      }
      history.addView(card);
      history.addView(ui.spacer(10));
    }
  }

  private boolean hasCompletedCommand() {
    JSONArray entries = snapshot == null ? null : snapshot.optJSONArray("history");
    if (entries != null)
      for (int i = 0; i < entries.length(); i++) {
        JSONObject entry = entries.optJSONObject(i);
        if (entry != null && !isActive(entry.optString("status"))) return true;
      }
    return false;
  }

  private void openSsh() {
    if (!sshSession.isEmpty()) return;
    request(
        () -> api.get("/api/ssh/" + Uri.encode(routerId) + "/session?cols=80&rows=24"),
        data -> {
          requireRouter(data);
          String sid = data.optString("sid", "");
          if (!sid.matches("[A-Za-z0-9_-]{16,128}"))
            throw new IllegalStateException("Invalid terminal session");
          sshSession = sid;
          status.setText(
              tool.equals("vps") ? "Терминал VPS подключён" : "SSH подключён · " + routerId);
          status.setTextColor(ui.GREEN);
          console.setLength(0);
          output.setText("");
        },
        true);
  }

  private String sshPath(String sid) {
    return "/api/ssh-session/" + Uri.encode(sid);
  }

  private void writeSsh(String text) {
    if (sshSession.isEmpty() || busy) return;
    final String sid = sshSession;
    request(
        () -> {
          JSONObject payload = new JSONObject().put("data", text);
          try {
            return api.post(sshPath(sid) + "/write", payload);
          } finally {
            payload.remove("data");
          }
        },
        ignored -> {},
        true);
  }

  private void appendConsole(String chunk) {
    // Keep a readable line console; do not claim to emulate cursor-addressed TUI programs.
    String plain =
        chunk
            .replaceAll("\\x1B\\][^\\x07]*(?:\\x07|\\x1B\\\\)", "")
            .replaceAll("\\x1B\\[[0-?]*[ -/]*[@-~]", "")
            .replaceAll("\\x1B[@-_]", "");
    for (int i = 0; i < plain.length(); i++) {
      char character = plain.charAt(i);
      if (character == '\b' || character == 127) {
        if (console.length() > 0) console.setLength(console.length() - 1);
      } else if (character == '\r') {
        if (i + 1 >= plain.length() || plain.charAt(i + 1) != '\n') {
          int start = console.lastIndexOf("\n") + 1;
          console.setLength(start);
        }
      } else if (character == '\n' || character == '\t' || character >= 32)
        console.append(character);
    }
    if (console.length() > 65536) console.delete(0, console.length() - 65536);
    output.setText(console);
  }

  private void closeSsh() {
    String sid = sshSession;
    sshSession = "";
    if (status != null && isTerminal()) {
      status.setText(tool.equals("vps") ? "Терминал VPS закрыт" : "SSH-соединение закрыто");
      status.setTextColor(ui.MUTED);
    }
    handler.removeCallbacks(poll);
    abandonSsh(sid);
  }

  private void abandonSsh(String sid) {
    if (sid == null || !sid.matches("[A-Za-z0-9_-]{16,128}") || api == null || network.isShutdown())
      return;
    network.execute(
        () -> {
          try {
            api.post(sshPath(sid) + "/close", new JSONObject());
          } catch (Exception ignored) {
          }
        });
  }

  @Override
  protected void onActivityResult(int requestCode, int resultCode, Intent resultIntent) {
    super.onActivityResult(requestCode, resultCode, resultIntent);
    if (requestCode != SAVE_CONFIG) return;
    String text = pendingConfiguration;
    pendingConfiguration = "";
    if (resultCode != RESULT_OK || resultIntent == null || resultIntent.getData() == null) return;
    if (text.isEmpty()) {
      showError("Обновите конфигурацию и повторите сохранение файла.");
      return;
    }
    Uri destination = resultIntent.getData();
    if (destination == null || !"content".equals(destination.getScheme()) || network.isShutdown()) {
      showError("Выберите документ через системный проводник.");
      return;
    }
    network.execute(
        () -> {
          try (OutputStream stream = getContentResolver().openOutputStream(destination, "wt")) {
            if (stream == null) throw new IOException();
            stream.write(text.getBytes(StandardCharsets.UTF_8));
            handler.post(
                () -> {
                  if (!isDestroyed())
                    Toast.makeText(this, "Файл сохранён", Toast.LENGTH_SHORT).show();
                });
          } catch (Exception failure) {
            handler.post(
                () -> {
                  if (!isDestroyed()) showError("Не удалось сохранить файл в выбранное место.");
                });
          }
        });
  }

  private void confirm(String title, String message, Runnable action) {
    new AlertDialog.Builder(this)
        .setTitle(title)
        .setMessage(message)
        .setNegativeButton("Отмена", null)
        .setPositiveButton("Продолжить", (dialog, which) -> action.run())
        .show();
  }

  private void showError(String message) {
    error.setText(message);
    error.setVisibility(View.VISIBLE);
  }

  private void deny(String message) { sessionReady=false;content.setVisibility(View.GONE);showError(message); }

  private static boolean isActive(String state) {
    return state.equals("queued") || state.equals("delivered") || state.equals("running");
  }

  private String actionLabel(String action) {
    return action.equals("start") ? "Запуск" : action.equals("stop") ? "Остановка" : "Перезапуск";
  }

  private int stateColor(String state) {
    return state.equals("succeeded")
        ? ui.GREEN
        : state.equals("failed") || state.equals("interrupted") ? ui.RED : ui.AMBER;
  }

  private String stateLabel(String state) {
    switch (state) {
      case "queued":
        return "Ожидает Heartbeat";
      case "delivered":
        return "Доставлена агенту";
      case "running":
        return "Выполняется на роутере";
      case "succeeded":
        return "Успешно";
      case "failed":
        return "Ошибка";
      case "interrupted":
        return "Выполнение прервано";
      case "expired":
        return "Не доставлена";
      case "unconfirmed":
        return "Результат не подтверждён";
      default:
        return "Статус неизвестен";
    }
  }

  private String stamp(long seconds) {
    return seconds <= 0
        ? "нет данных"
        : DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT)
            .format(new Date(seconds * 1000));
  }

  private static String duration(long seconds) {
    seconds = Math.max(0, seconds);
    return String.format(Locale.ROOT, "%02d:%02d", seconds / 60, seconds % 60);
  }
}
