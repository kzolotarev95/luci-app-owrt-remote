package ru.owrthub.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.res.ColorStateList;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputFilter;
import android.text.InputType;
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
import java.net.URI;
import java.text.DateFormat;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Date;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.function.Consumer;
import org.json.JSONArray;
import org.json.JSONObject;

/** Native Hub settings. Every mutation uses the existing authenticated Hub API. */
public final class NativeSettingsActivity extends Activity {
  public static final String EXTRA_HUB_ORIGIN = "hub_origin", EXTRA_TOOL = "tool";
  private final Handler handler = new Handler(Looper.getMainLooper());
  private final ExecutorService network = Executors.newSingleThreadExecutor();
  private final List<Button> actions = new ArrayList<>();
  private final List<EditText> sensitiveFields = new ArrayList<>();
  private NativeUi ui;
  private HubApiClient api;
  private JSONObject session, snapshot;
  private LinearLayout content;
  private ProgressBar progress;
  private TextView error, secretView;
  private String tool = "about", totpTicket = "", requestId = "", requestOperation = "";
  private boolean visible, busy;
  private int generation;
  private final Runnable poll = this::refresh;

  @Override
  public void onCreate(Bundle state) {
    super.onCreate(state);
    if (state != null) {
      requestId = state.getString("request_id", "");
      requestOperation = state.getString("request_operation", "");
      if (!requestId.matches("[a-zA-Z0-9_-]{16,80}")) requestId = "";
    }
    ui =
        new NativeUi(
            this, getSharedPreferences("native_design", MODE_PRIVATE).getBoolean("winter", false));
    getWindow().setStatusBarColor(ui.BG);
    getWindow().setNavigationBarColor(ui.BG);
    getWindow().setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);
    try {
      String selected = getIntent().getStringExtra(EXTRA_TOOL);
      if (!Arrays.asList(
              "notifications",
              "sessions",
              "security",
              "users",
              "groups",
              "hubUpdate",
              "routerUpdates",
              "addRouter",
              "about",
              "donate")
          .contains(selected)) throw new IllegalArgumentException("Unknown settings page");
      tool = selected;
      URI origin =
          HubUrlPolicy.normalizeHub(
              getIntent().getStringExtra(EXTRA_HUB_ORIGIN), "", BuildConfig.DEBUG);
      api = new HubApiClient(origin, new HubSessionStore(this));
      if (tool.equals("security") || tool.equals("users"))
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
      buildShell();
    } catch (Exception invalid) {
      LinearLayout page = ui.column();
      page.setBackgroundColor(ui.BG);
      page.setPadding(ui.dp(20), ui.dp(40), ui.dp(20), ui.dp(20));
      page.addView(ui.title("Настройки недоступны"));
      page.addView(ui.spacer(20));
      page.addView(ui.body("Откройте настройки из подключённого Hub."));
      page.addView(ui.spacer(20));
      page.addView(ui.button("Назад", false, this::finish));
      setContentView(page);
      api = null;
    }
  }

  private void buildShell() {
    LinearLayout root = ui.column();
    root.setBackgroundColor(ui.BG);
    root.setLayoutParams(new LinearLayout.LayoutParams(-1, -1));
    root.setOnApplyWindowInsetsListener(
        (view, insets) -> {
          view.setPadding(
              insets.getSystemWindowInsetLeft(),
              insets.getSystemWindowInsetTop(),
              insets.getSystemWindowInsetRight(),
              insets.getSystemWindowInsetBottom());
          return insets;
        });
    LinearLayout bar = ui.row();
    bar.setPadding(ui.dp(6), ui.dp(4), ui.dp(6), ui.dp(4));
    bar.addView(ui.iconButton("back", "Назад", this::finish));
    bar.addView(ui.text(title(), 20, true), new LinearLayout.LayoutParams(0, -2, 1));
    bar.addView(
        ui.iconButton(
            "refresh",
            "Обновить",
            () -> {
              if (!busy) bootstrap();
            }));
    root.addView(bar);
    progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
    progress.setIndeterminate(true);
    progress.setIndeterminateTintList(ColorStateList.valueOf(ui.BLUE));
    root.addView(progress, new LinearLayout.LayoutParams(-1, ui.dp(2)));
    error = ui.text("", 14, false);
    error.setTextColor(ui.RED);
    error.setPadding(ui.dp(18), ui.dp(8), ui.dp(18), ui.dp(8));
    error.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
    error.setVisibility(View.GONE);
    root.addView(error);
    ScrollView scroll = new ScrollView(this);
    scroll.setFillViewport(true);
    content = ui.column();
    content.setPadding(ui.dp(18), ui.dp(20), ui.dp(18), ui.dp(28));
    scroll.addView(content);
    root.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
    setContentView(root);
    root.requestApplyInsets();
    reset();
  }

  private String title() {
    switch (tool) {
      case "notifications":
        return "Уведомления";
      case "sessions":
        return "Активные сессии";
      case "security":
        return "Безопасность";
      case "users":
        return "Пользователи и доступ";
      case "groups":
        return "Группы роутеров";
      case "hubUpdate":
        return "Обновление Hub";
      case "routerUpdates":
        return "Обновление роутеров";
      case "addRouter":
        return "Добавить роутер";
      case "donate":
        return "Поддержать разработку";
      default:
        return "О приложении";
    }
  }

  private void reset() {
    sensitiveFields.clear();
    actions.clear();
    content.removeAllViews();
    add(ui.title(title()));
    error.setText("");
    error.setVisibility(View.GONE);
  }

  private void add(View view) {
    content.addView(view);
    content.addView(ui.spacer(12));
  }

  private void section(String text) {
    add(ui.text(text, 19, true));
  }

  private void body(String text) {
    add(ui.body(text));
  }

  private void action(String label, NativeUi.Style style, Runnable callback) {
    Button button = ui.button(label, style, callback);
    actions.add(button);
    add(button);
  }

  private EditText field(String label, String value, boolean secret) {
    EditText edit = ui.field(label, value, secret);
    edit.setSaveEnabled(false);
    edit.setFilters(new InputFilter[] {new InputFilter.LengthFilter(secret ? 4096 : 512)});
    if (secret) {
      edit.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS);
      sensitiveFields.add(edit);
    }
    add(ui.labeledField(label, edit));
    return edit;
  }

  private String value(EditText field) {
    return field.getText().toString();
  }

  private String date(long seconds) {
    return seconds <= 0
        ? "—"
        : DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT)
            .format(new Date(seconds * 1000));
  }

  private JSONObject json(Object... values) {
    JSONObject result = new JSONObject();
    try {
      for (int i = 0; i < values.length; i += 2) result.put((String) values[i], values[i + 1]);
    } catch (Exception invalid) {
      throw new IllegalArgumentException("Invalid request", invalid);
    }
    return result;
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
    for (EditText field : sensitiveFields) field.setText("");
    super.onPause();
  }

  @Override
  protected void onDestroy() {
    handler.removeCallbacksAndMessages(null);
    network.shutdown();
    super.onDestroy();
  }

  @Override
  protected void onSaveInstanceState(Bundle state) {
    state.putString("request_id", requestId);
    state.putString("request_operation", requestOperation);
    super.onSaveInstanceState(state);
  }

  private void bootstrap() {
    request(
        api::session,
        data -> {
          session = data;
          JSONObject account = session.optJSONObject("account");
          boolean owner = account != null && account.optBoolean("is_owner");
          boolean ownerPage =
              Arrays.asList(
                      "notifications", "security", "users", "groups", "hubUpdate", "routerUpdates")
                  .contains(tool);
          if ((ownerPage && !owner)
              || (tool.equals("addRouter")
                  && (account == null || !account.optBoolean("can_add_router")))) {
            reset();
            body("У аккаунта нет разрешения на этот раздел.");
            return;
          }
          // The user must be able to switch to an authenticator app without losing this
          // in-memory setup. Nothing is put in saved state; this Activity is FLAG_SECURE.
          if (tool.equals("security")
              && !totpTicket.isEmpty()
              && secretView != null
              && secretView.isAttachedToWindow()
              && secretView.getVisibility() == View.VISIBLE
              && session.optJSONObject("auth") != null
              && !session.optJSONObject("auth").optBoolean("totp_enabled")) return;
          refresh();
        });
  }

  private void refresh() {
    if (!visible || api == null || session == null) return;
    switch (tool) {
      case "sessions":
        request(() -> api.get("/api/sessions"), this::renderSessions);
        break;
      case "notifications":
        request(() -> api.get("/api/notifications?limit=80"), this::renderNotifications);
        break;
      case "groups":
        request(() -> api.get("/api/router-groups"), this::renderGroups);
        break;
      case "hubUpdate":
        request(() -> api.get("/api/hub-update"), this::renderHubUpdate);
        break;
      case "routerUpdates":
        request(() -> api.get("/api/router-updates"), this::renderRouterUpdates);
        break;
      case "security":
        renderSecurity();
        break;
      case "users":
        request(
            () -> {
              JSONObject auth = api.get("/api/auth/meta").optJSONObject("auth");
              if (auth == null || !auth.optBoolean("is_owner"))
                throw new IllegalStateException("Owner required");
              return json(
                  "users",
                  auth.optJSONArray("delegated_users"),
                  "routers",
                  auth.optJSONArray("router_options"));
            },
            this::renderUsers);
        break;
      case "addRouter":
        request(
            () -> api.get(HubApiClient.MOBILE_BASE + "/router-defaults"),
            data -> {
              snapshot = data;
              renderAddRouter();
            });
        break;
      case "donate":
        renderDonate();
        break;
      default:
        request(api::capabilities, this::renderAbout);
    }
  }

  private void request(Callable<JSONObject> call, Consumer<JSONObject> success) {
    if (busy || !visible || api == null || network.isShutdown()) return;
    handler.removeCallbacks(poll);
    busy = true;
    controls();
    int expected = generation;
    network.execute(
        () -> {
          JSONObject response = null;
          Exception failure = null;
          try {
            response = call.call();
          } catch (Exception problem) {
            failure = problem;
          }
          final JSONObject data = response;
          final Exception problem = failure;
          handler.post(
              () -> {
                busy = false;
                if (!visible || expected != generation || isFinishing() || isDestroyed()) {
                  if (visible && !isFinishing() && !isDestroyed()) bootstrap();
                  return;
                }
                if (problem == null) {
                  error.setVisibility(View.GONE);
                  try {
                    success.accept(data);
                  } catch (RuntimeException invalid) {
                    showError("Hub вернул неподдерживаемый ответ.");
                  }
                } else {
                  showError(
                      tool.equals("addRouter")
                              && problem instanceof HubApiClient.ApiException
                              && ((HubApiClient.ApiException) problem).statusCode == 404
                          ? "Обновите панель Hub: в этой сборке сервера ещё нет автоматических"
                              + " параметров роутера."
                          : problem instanceof HubApiClient.AuthRequiredException
                              ? "Сессия завершилась. Вернитесь на главный экран и войдите снова."
                              : problem instanceof HubApiClient.ApiException
                                  ? problem.getMessage()
                                  : "Не удалось связаться с Hub. Проверьте сеть и сертификат.");
                  if ((tool.equals("hubUpdate") || tool.equals("routerUpdates"))
                      && !(problem instanceof HubApiClient.AuthRequiredException)
                      && !(problem instanceof HubApiClient.PermissionException))
                    handler.postDelayed(poll, 5000);
                }
                controls();
              });
        });
  }

  private void controls() {
    progress.setVisibility(busy ? View.VISIBLE : View.INVISIBLE);
    for (Button button : actions) button.setEnabled(!busy);
  }

  private void showError(String message) {
    error.setText(message);
    error.setVisibility(View.VISIBLE);
  }

  private void confirm(String heading, String description, Runnable action) {
    new AlertDialog.Builder(this)
        .setTitle(heading)
        .setMessage(description)
        .setNegativeButton("Отмена", null)
        .setPositiveButton("Подтвердить", (dialog, which) -> action.run())
        .show();
  }

  private void post(String path, JSONObject payload, Runnable success) {
    request(
        () -> {
          try {
            return api.post(path, payload);
          } finally {
            payload.remove("current_password");
            payload.remove("password");
            payload.remove("password_confirm");
            payload.remove("ssh_password");
            payload.remove("code");
          }
        },
        data -> {
          if (success != null) success.run();
          else refresh();
        });
  }

  private void renderSessions(JSONObject data) {
    reset();
    body("Сессии вашего аккаунта. Владелец Hub также видит сессии других пользователей.");
    JSONArray rows = data.optJSONArray("sessions");
    if (rows == null || rows.length() == 0) body("Активных сессий нет.");
    for (int i = 0; rows != null && i < Math.min(rows.length(), 80); i++) {
      JSONObject row = rows.optJSONObject(i);
      if (row == null) continue;
      LinearLayout card = ui.card();
      card.addView(ui.text(row.optString("client", "Устройство"), 17, true));
      card.addView(ui.spacer(8));
      card.addView(
          ui.body(
              row.optString("username")
                  + " · "
                  + row.optString("ip")
                  + "\nПоследняя активность: "
                  + date(row.optLong("last_seen"))
                  + "\nВход: "
                  + row.optString("auth_method")));
      if (row.optBoolean("current")) {
        card.addView(ui.spacer(10));
        card.addView(ui.chip("Текущая сессия", ui.GREEN));
      } else {
        String id = row.optString("id");
        Button revoke =
            ui.button(
                "Завершить сессию",
                NativeUi.Style.DANGER,
                () ->
                    confirm(
                        "Завершить сессию?",
                        row.optString("client"),
                        () -> post("/api/session/revoke", json("id", id), null)));
        actions.add(revoke);
        card.addView(ui.spacer(12));
        card.addView(revoke);
      }
      add(card);
    }
    action(
        "Завершить остальные сессии",
        NativeUi.Style.DANGER,
        () ->
            confirm(
                "Завершить остальные сессии?",
                "Текущая сессия приложения останется активной.",
                () -> post("/api/session/revoke-others", json(), null)));
  }

  private void renderNotifications(JSONObject data) {
    reset();
    section("Уведомления на телефоне");
    body(HubNotifications.prefs(this).getString("status", "Фоновые уведомления выключены"));
    action(
        "Включить уведомления Android",
        NativeUi.Style.PRIMARY,
        () -> HubNotifications.enable(this));
    action(
        "Выключить фоновые уведомления",
        NativeUi.Style.QUIET,
        () -> {
          HubNotifications.disable(this);
          refresh();
        });
    body(
        "Фоновое подключение показывает события Hub без открытого экрана. После принудительной"
            + " остановки Android нужно снова открыть приложение.");
    JSONArray items = data.optJSONArray("notifications");
    if (items == null || items.length() == 0) body("Уведомлений пока нет.");
    for (int i = 0; items != null && i < Math.min(items.length(), 80); i++) {
      JSONObject item = items.optJSONObject(i);
      if (item == null) continue;
      LinearLayout card = ui.card();
      card.addView(ui.text(item.optString("title", "Событие"), 17, true));
      card.addView(ui.spacer(8));
      card.addView(ui.body(item.optString("body")));
      card.addView(ui.spacer(10));
      card.addView(ui.label(date(item.optLong("ts"))));
      add(card);
    }
    action(
        "Очистить уведомления",
        NativeUi.Style.DANGER,
        () ->
            confirm(
                "Очистить уведомления?",
                "История уведомлений Hub будет удалена.",
                () -> post("/api/notifications/clear", json(), null)));
  }

  private void renderSecurity() {
    totpTicket = "";
    reset();
    section("Пароль владельца");
    body("Изменение пароля завершает текущую сессию. После сохранения войдите заново.");
    EditText current = field("Текущий пароль", "", true);
    EditText next = field("Новый пароль", "", true);
    EditText repeat = field("Повтор нового пароля", "", true);
    action(
        "Изменить пароль",
        NativeUi.Style.PRIMARY,
        () -> {
          if (value(current).isEmpty()
              || value(next).length() < 4
              || !value(next).equals(value(repeat))) {
            showError("Укажите текущий пароль и одинаковый новый пароль длиной от 4 символов.");
            return;
          }
          JSONObject payload =
              json(
                  "current_password",
                  value(current),
                  "password",
                  value(next),
                  "password_confirm",
                  value(repeat));
          current.setText("");
          next.setText("");
          repeat.setText("");
          request(
              () -> {
                try {
                  JSONObject result = api.post("/api/auth", payload);
                  api.clearSession();
                  return result;
                } finally {
                  payload.remove("current_password");
                  payload.remove("password");
                  payload.remove("password_confirm");
                }
              },
              result -> {
                Toast.makeText(this, "Пароль изменён. Войдите в Hub заново.", Toast.LENGTH_LONG)
                    .show();
                finish();
              });
        });
    add(ui.divider());
    section("Двухфакторная защита");
    JSONObject auth = session.optJSONObject("auth");
    boolean enabled = auth != null && auth.optBoolean("totp_enabled");
    body(
        enabled
            ? "2FA включена. Для входа владельца требуется код из приложения-аутентификатора."
            : "2FA выключена. Добавьте второй фактор через приложение-аутентификатор.");
    EditText password = field("Пароль для изменения 2FA", "", true);
    if (!enabled) {
      action(
          "Начать настройку 2FA",
          NativeUi.Style.SECONDARY,
          () -> {
            if (value(password).isEmpty()) {
              showError("Введите текущий пароль.");
              return;
            }
            JSONObject payload = json("current_password", value(password));
            password.setText("");
            request(
                () -> {
                  try {
                    return api.post("/api/auth/totp/setup", payload);
                  } finally {
                    payload.remove("current_password");
                  }
                },
                result -> {
                  totpTicket = result.optString("ticket");
                  secretView.setText(
                      "Ключ для приложения-аутентификатора:\n"
                          + result.optString("secret")
                          + "\n\n"
                          + "Добавьте ключ как TOTP, 6 цифр, период 30 секунд. Затем введите новый"
                          + " код и пароль.");
                  secretView.setVisibility(View.VISIBLE);
                });
          });
      secretView = ui.text("", 15, false);
      secretView.setTextIsSelectable(true);
      secretView.setVisibility(View.GONE);
      add(secretView);
    }
    EditText code = field("Код 2FA", "", true);
    code.setInputType(InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_VARIATION_PASSWORD);
    code.setFilters(new InputFilter[] {new InputFilter.LengthFilter(6)});
    action(
        enabled ? "Выключить 2FA" : "Подтвердить и включить 2FA",
        enabled ? NativeUi.Style.DANGER : NativeUi.Style.PRIMARY,
        () -> {
          if (value(password).isEmpty()
              || !value(code).matches("[0-9]{6}")
              || (!enabled && totpTicket.isEmpty())) {
            showError("Укажите пароль и 6-значный код. Для включения сначала создайте ключ 2FA.");
            return;
          }
          JSONObject payload = json("current_password", value(password), "code", value(code));
          if (!enabled)
            try {
              payload.put("ticket", totpTicket);
            } catch (Exception ignored) {
              return;
            }
          password.setText("");
          code.setText("");
          Runnable execute =
              () ->
                  post(
                      "/api/auth/totp/" + (enabled ? "disable" : "enable"),
                      payload,
                      this::bootstrap);
          if (enabled)
            confirm("Выключить 2FA?", "Вход владельца будет защищён только паролем.", execute);
          else execute.run();
        });
  }

  private void renderGroups(JSONObject data) {
    reset();
    JSONArray groups = data.optJSONArray("groups");
    EditText name = field("Название новой группы", "", false);
    action(
        "Создать группу",
        NativeUi.Style.PRIMARY,
        () -> {
          if (value(name).trim().isEmpty()) {
            showError("Введите название группы.");
            return;
          }
          post("/api/router-groups/save", json("name", value(name).trim()), null);
        });
    for (int i = 0; groups != null && i < Math.min(groups.length(), 100); i++) {
      JSONObject group = groups.optJSONObject(i);
      if (group == null) continue;
      String id = group.optString("id");
      LinearLayout card = ui.card();
      card.addView(ui.text(group.optString("name"), 18, true));
      card.addView(ui.spacer(12));
      Button rename =
          ui.button(
              "Переименовать",
              false,
              () -> {
                EditText edit = ui.field("Название", group.optString("name"), false);
                new AlertDialog.Builder(this)
                    .setTitle("Название группы")
                    .setView(edit)
                    .setNegativeButton("Отмена", null)
                    .setPositiveButton(
                        "Сохранить",
                        (dialog, which) -> {
                          if (!value(edit).trim().isEmpty())
                            post(
                                "/api/router-groups/save",
                                json("id", id, "name", value(edit).trim()),
                                null);
                        })
                    .show();
              });
      Button remove =
          ui.button(
              "Удалить группу",
              NativeUi.Style.DANGER,
              () ->
                  confirm(
                      "Удалить группу?",
                      "Роутеры останутся в Hub без этой группы.",
                      () -> post("/api/router-groups/delete", json("id", id), null)));
      actions.add(rename);
      actions.add(remove);
      card.addView(rename);
      card.addView(ui.spacer(8));
      card.addView(remove);
      add(card);
    }
    action(
        "Назначить группу роутеру",
        NativeUi.Style.SECONDARY,
        () ->
            request(
                () -> api.get("/api/routers"),
                routers -> {
                  JSONArray rows = routers.optJSONArray("routers");
                  if (rows == null || rows.length() == 0) {
                    showError("Роутеров пока нет.");
                    return;
                  }
                  String[] labels = new String[rows.length()];
                  for (int i = 0; i < labels.length; i++)
                    labels[i] =
                        rows.optJSONObject(i)
                            .optString("name", rows.optJSONObject(i).optString("id"));
                  new AlertDialog.Builder(this)
                      .setTitle("Выберите роутер")
                      .setItems(
                          labels,
                          (dialog, selected) -> {
                            JSONObject router = rows.optJSONObject(selected);
                            int count = groups == null ? 0 : groups.length();
                            String[] groupNames = new String[count + 1];
                            groupNames[0] = "Без группы";
                            for (int j = 0; j < count; j++)
                              groupNames[j + 1] = groups.optJSONObject(j).optString("name");
                            new AlertDialog.Builder(this)
                                .setTitle("Группа роутера")
                                .setItems(
                                    groupNames,
                                    (choice, groupIndex) ->
                                        post(
                                            "/api/router/"
                                                + Uri.encode(router.optString("id"))
                                                + "/group",
                                            json(
                                                "group_id",
                                                groupIndex == 0
                                                    ? ""
                                                    : groups
                                                        .optJSONObject(groupIndex - 1)
                                                        .optString("id")),
                                            null))
                                .show();
                          })
                      .show();
                }));
  }

  private void renderUsers(JSONObject data) {
    reset();
    body(
        "Создайте отдельные аккаунты и назначьте доступ к выбранным роутерам. Изменения"
            + " подтверждаются паролем владельца.");
    JSONArray users = data.optJSONArray("users"), routers = data.optJSONArray("routers");
    action(
        "Добавить пользователя",
        NativeUi.Style.PRIMARY,
        () -> renderUserEditor(new JSONObject(), routers));
    if (users == null || users.length() == 0) body("Дополнительных пользователей пока нет.");
    for (int i = 0; users != null && i < Math.min(users.length(), 150); i++) {
      JSONObject user = users.optJSONObject(i);
      if (user == null) continue;
      LinearLayout card = ui.card();
      card.addView(ui.text(user.optString("username"), 18, true));
      card.addView(ui.spacer(10));
      card.addView(
          ui.chip(
              user.optBoolean("enabled") ? "Доступ включён" : "Доступ выключен",
              user.optBoolean("enabled") ? ui.GREEN : ui.MUTED));
      card.addView(ui.spacer(10));
      card.addView(
          ui.body(
              "Роутеров: "
                  + user.optInt("router_count")
                  + " · Сессий: "
                  + user.optInt("session_count")
                  + "\nАктивность: "
                  + date(user.optLong("last_seen_at"))));
      JSONArray access = user.optJSONArray("router_access");
      for (int j = 0; access != null && j < Math.min(access.length(), 30); j++) {
        JSONObject row = access.optJSONObject(j);
        if (row != null)
          card.addView(
              ui.body(
                  row.optString("name", row.optString("id"))
                      + " · "
                      + row.optString("flag_label")));
      }
      Button edit = ui.button("Изменить доступ", false, () -> renderUserEditor(user, routers));
      Button revoke =
          ui.button(
              "Завершить сессии",
              false,
              () ->
                  userPasswordAction(
                      "Завершить сессии?",
                      "Пользователю потребуется заново войти в Hub.",
                      "/api/auth/users/revoke-sessions",
                      user.optString("id")));
      Button remove =
          ui.button(
              "Удалить пользователя",
              NativeUi.Style.DANGER,
              () ->
                  userPasswordAction(
                      "Удалить пользователя?",
                      "Аккаунт "
                          + user.optString("username")
                          + " будет удалён, его сессии завершатся.",
                      "/api/auth/users/delete",
                      user.optString("id")));
      for (Button button : new Button[] {edit, revoke, remove}) {
        actions.add(button);
        card.addView(ui.spacer(10));
        card.addView(button);
      }
      add(card);
    }
  }

  private void userPasswordAction(String title, String description, String path, String userId) {
    LinearLayout panel = ui.column();
    panel.setPadding(ui.dp(20), ui.dp(8), ui.dp(20), ui.dp(8));
    panel.addView(ui.body(description));
    panel.addView(ui.spacer(16));
    EditText password = ui.field("Текущий пароль владельца", "", true);
    password.setSaveEnabled(false);
    password.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS);
    sensitiveFields.add(password);
    panel.addView(ui.labeledField("Пароль владельца", password));
    AlertDialog dialog =
        new AlertDialog.Builder(this)
            .setTitle(title)
            .setView(panel)
            .setNegativeButton("Отмена", null)
            .setPositiveButton("Подтвердить", null)
            .create();
    dialog.setOnDismissListener(
        ignored -> {
          password.setText("");
          sensitiveFields.remove(password);
        });
    dialog.setOnShowListener(
        ignored ->
            dialog
                .getButton(AlertDialog.BUTTON_POSITIVE)
                .setOnClickListener(
                    view -> {
                      if (value(password).isEmpty()) {
                        password.setError("Введите пароль владельца");
                        return;
                      }
                      JSONObject payload = json("id", userId, "current_password", value(password));
                      password.setText("");
                      dialog.dismiss();
                      post(path, payload, null);
                    }));
    dialog.show();
  }

  private void renderUserEditor(JSONObject user, JSONArray routers) {
    reset();
    boolean existing = !user.optString("id").isEmpty();
    section(existing ? "Изменить пользователя" : "Новый пользователь");
    EditText username = field("Логин пользователя", user.optString("username"), false);
    EditText password =
        field(existing ? "Новый пароль (необязательно)" : "Пароль пользователя", "", true);
    EditText repeated = field("Повтор пароля пользователя", "", true);
    Switch enabled = new Switch(this);
    enabled.setText("Доступ к Hub включён");
    enabled.setTextColor(ui.TEXT);
    enabled.setChecked(!existing || user.optBoolean("enabled"));
    enabled.setMinHeight(ui.dp(52));
    add(enabled);
    body(
        "Для каждого роутера выберите уровень: A — Web, B — Web и SSH, G — полный доступ, включая"
            + " добавление роутеров.");
    JSONObject flags;
    try {
      flags =
          new JSONObject(
              user.optJSONObject("router_flags") == null
                  ? "{}"
                  : user.optJSONObject("router_flags").toString());
    } catch (Exception invalid) {
      flags = new JSONObject();
    }
    final JSONObject selectedFlags = flags;
    for (int i = 0; routers != null && i < Math.min(routers.length(), 200); i++) {
      JSONObject router = routers.optJSONObject(i);
      if (router == null) continue;
      String id = router.optString("id"), name = router.optString("name", id);
      JSONArray current = selectedFlags.optJSONArray(id);
      int currentLevel = 0;
      if (current != null)
        for (int j = 0; j < current.length(); j++) {
          String flag = current.optString(j);
          int rank = flag.equals("G") ? 3 : flag.equals("B") ? 2 : flag.equals("A") ? 1 : 0;
          currentLevel = Math.max(currentLevel, rank);
        }
      final int[] level = {currentLevel};
      String[] labels = {"Нет доступа", "A · Web", "B · Web + SSH", "G · Полный доступ"};
      Button access = ui.button(name + "\n" + labels[level[0]], false, null);
      access.setOnClickListener(
          view ->
              new AlertDialog.Builder(this)
                  .setTitle(name)
                  .setSingleChoiceItems(
                      labels,
                      level[0],
                      (dialog, index) -> {
                        level[0] = index;
                        selectedFlags.remove(id);
                        if (index > 0)
                          try {
                            selectedFlags.put(
                                id, new JSONArray().put(index == 3 ? "G" : index == 2 ? "B" : "A"));
                          } catch (Exception ignored) {
                            return;
                          }
                        access.setText(name + "\n" + labels[index]);
                        dialog.dismiss();
                      })
                  .setNegativeButton("Отмена", null)
                  .show());
      actions.add(access);
      add(access);
    }
    EditText ownerPassword = field("Текущий пароль владельца", "", true);
    action(
        "Сохранить пользователя",
        NativeUi.Style.PRIMARY,
        () -> {
          String name = value(username).trim(), next = value(password);
          if (!name.matches("[\\p{L}\\p{N}._@-]{1,64}")
              || value(ownerPassword).isEmpty()
              || (!existing && next.length() < 4)
              || (!next.isEmpty() && next.length() < 4)
              || !next.equals(value(repeated))) {
            showError(
                "Проверьте логин, пароль владельца и одинаковые пароли пользователя (от 4"
                    + " символов).");
            return;
          }
          JSONObject payload =
              json(
                  "id",
                  user.optString("id"),
                  "username",
                  name,
                  "enabled",
                  enabled.isChecked(),
                  "password",
                  next,
                  "password_confirm",
                  value(repeated),
                  "current_password",
                  value(ownerPassword),
                  "router_flags",
                  selectedFlags);
          password.setText("");
          repeated.setText("");
          ownerPassword.setText("");
          post("/api/auth/users/save", payload, null);
        });
    action("Отмена", NativeUi.Style.QUIET, this::refresh);
  }

  private void renderHubUpdate(JSONObject data) {
    snapshot = data;
    reset();
    if (data.optBoolean("available"))
      section("Доступно обновление Hub " + data.optString("remote_version"));
    body(
        "Установлена "
            + data.optString("current_version", "—")
            + "\nВ источнике: "
            + data.optString("remote_version", "—"));
    JSONObject job = data.optJSONObject("job");
    String state = job == null ? "" : job.optString("status");
    if (job != null && !state.isEmpty()) body(job.optString("message", state));
    if (!data.optString("error").isEmpty()) body(data.optString("error"));
    boolean active =
        Arrays.asList("starting", "installing", "restarting", "rollback").contains(state);
    if (!active)
      action(
          "Проверить обновление",
          NativeUi.Style.SECONDARY,
          () -> request(() -> api.post("/api/hub-update/check", json()), this::renderHubUpdate));
    if (data.optBoolean("available") && !active && !data.optBoolean("checking")) {
      String revision = data.optString("remote_revision");
      if (revision.matches("[a-f0-9]{64}"))
        action(
            "Установить обновление",
            NativeUi.Style.PRIMARY,
            () ->
                confirm(
                    "Обновить Hub?",
                    "Панель временно перезапустится. При ошибке Hub восстановит предыдущую сборку.",
                    () ->
                        request(
                            () -> api.post("/api/hub-update/start", json("revision", revision)),
                            this::renderHubUpdate)));
    } else if (!active
        && data.optBoolean("checked")
        && !data.optBoolean("checking")
        && data.optString("error").isEmpty()) {
      body(
          data.optBoolean("source_incompatible")
              ? data.optString(
                  "notice", "Сборка в источнике пока не поддерживает приложение Android.")
              : data.optBoolean("source_older")
                  ? "В источнике более старая версия. Понижение не предлагается."
                  : "Обновлений нет.");
    }
    if (active || data.optBoolean("checking")) handler.postDelayed(poll, 3000);
  }

  private void renderRouterUpdates(JSONObject data) {
    snapshot = data;
    reset();
    JSONObject job = data.optJSONObject("job");
    boolean active = job != null && job.optString("state").equals("running");
    body("Очередь выполняется на VPS. Закрытие приложения не прерывает запущенные операции.");
    if (job != null) {
      body(
          (job.optString("operation").equals("reboot") ? "Перезагрузка" : "Обновление")
              + " · "
              + queueState(job.optString("state")));
      JSONArray items = job.optJSONArray("items");
      for (int i = 0; items != null && i < Math.min(items.length(), 100); i++) {
        JSONObject item = items.optJSONObject(i);
        if (item == null) continue;
        LinearLayout card = ui.card();
        card.addView(ui.text(item.optString("name", item.optString("id")), 17, true));
        card.addView(ui.spacer(8));
        card.addView(
            ui.body(queueState(item.optString("state")) + " · " + item.optString("message")));
        String output = item.optString("output");
        if (!output.isEmpty()) {
          TextView log =
              ui.text(
                  output.length() > 8192 ? output.substring(output.length() - 8192) : output,
                  13,
                  false);
          log.setTextIsSelectable(true);
          card.addView(ui.spacer(10));
          card.addView(log);
        }
        add(card);
      }
      if (active && !job.optBoolean("cancel_requested"))
        action(
            "Остановить очередь",
            NativeUi.Style.DANGER,
            () ->
                confirm(
                    "Остановить очередь?",
                    "Hub отменит текущую задачу и оставшиеся операции.",
                    () ->
                        post("/api/router-updates/cancel", json("id", job.optString("id")), null)));
      if (!active)
        action(
            "Убрать результаты",
            NativeUi.Style.SECONDARY,
            () -> post("/api/router-updates/refresh", json("id", job.optString("id")), null));
    }
    if (!active) {
      section("Новая очередь");
      EditText command =
          field(
              "Команда обновления",
              "wget -O -"
                  + " \"https://raw.githubusercontent.com/kzolotarev95/luci-app-owrt-remote/main/install.sh?v=$(date"
                  + " +%s)\" | sh",
              false);
      command.setSingleLine(false);
      command.setMinLines(3);
      command.setMaxLines(6);
      command.setFilters(new InputFilter[] {new InputFilter.LengthFilter(16384)});
      EditText password = field("SSH-пароль роутеров (если нужен)", "", true);
      action(
          "Обновить все роутеры",
          NativeUi.Style.PRIMARY,
          () -> {
            if (value(command).trim().isEmpty()) {
              showError("Укажите команду обновления.");
              return;
            }
            confirm(
                "Обновить все роутеры?",
                "Команда будет по очереди запущена на роутерах Hub. Проверьте её перед"
                    + " подтверждением.",
                () -> {
                  JSONObject payload =
                      json(
                          "command",
                          value(command),
                          "ssh_password",
                          value(password),
                          "request_id",
                          requestToken("update"));
                  password.setText("");
                  post(
                      "/api/router-updates/start",
                      payload,
                      () -> {
                        requestId = "";
                        refresh();
                      });
                });
          });
      action(
          "Перезагрузить все роутеры",
          NativeUi.Style.DANGER,
          () ->
              confirm(
                  "Перезагрузить все роутеры?",
                  "Связь с роутерами временно пропадёт. Hub выполнит перезагрузки по очереди.",
                  () -> {
                    JSONObject payload =
                        json("ssh_password", value(password), "request_id", requestToken("reboot"));
                    password.setText("");
                    post(
                        "/api/router-updates/reboot",
                        payload,
                        () -> {
                          requestId = "";
                          refresh();
                        });
                  }));
    } else handler.postDelayed(poll, 3000);
    JSONArray routers = data.optJSONArray("routers");
    if (routers != null) {
      section("Текущая связь");
      for (int i = 0; i < Math.min(routers.length(), 100); i++) {
        JSONObject router = routers.optJSONObject(i);
        if (router != null)
          body(
              router.optString("name", router.optString("id"))
                  + " · "
                  + (router.optBoolean("online") ? "На связи" : "Не на связи"));
      }
    }
  }

  private String requestToken(String operation) {
    if (requestId.isEmpty() || !requestOperation.equals(operation))
      requestId = UUID.randomUUID().toString().replace("-", "");
    requestOperation = operation;
    return requestId;
  }

  private String queueState(String state) {
    switch (state) {
      case "running":
        return "Выполняется";
      case "finished":
        return "Завершено";
      case "cancelled":
        return "Остановлено";
      case "queued":
        return "В очереди";
      case "connecting":
        return "Подключение";
      case "installing":
        return "Установка";
      case "success":
        return "Успешно";
      case "error":
        return "Ошибка";
      case "unknown":
        return "Результат не подтверждён";
      case "skipped":
        return "Пропущено";
      default:
        return state;
    }
  }

  private void renderAddRouter() {
    reset();
    body(
        "Создайте запись роутера в Hub. Затем настройте агент на самом роутере для подключения к"
            + " VPS.");
    JSONObject defaults = snapshot == null ? new JSONObject() : snapshot.optJSONObject("defaults");
    if (defaults == null) {
      showError("Обновите Hub для автоматического добавления роутеров.");
      return;
    }
    EditText id = field("ID роутера", defaults.optString("id"), false);
    EditText name = field("Название", defaults.optString("name"), false);
    EditText vpsHost =
        field(
            "Адрес VPS для туннеля", defaults.optString("vps_host", api.getHub().getHost()), false);
    EditText port = field("Порт Web-туннеля", defaults.optString("entry_port"), false);
    port.setInputType(InputType.TYPE_CLASS_NUMBER);
    EditText ssh = field("Порт SSH-туннеля", defaults.optString("ssh_entry_port"), false);
    ssh.setInputType(InputType.TYPE_CLASS_NUMBER);
    android.widget.Spinner role = new android.widget.Spinner(this);
    role.setAdapter(
        new android.widget.ArrayAdapter<String>(
            this, android.R.layout.simple_spinner_dropdown_item, new String[] {"node", "main"}));
    role.setSelection(defaults.optString("role").equals("main") ? 1 : 0);
    add(role);
    JSONArray options = snapshot.optJSONArray("groups");
    java.util.List<String> groupNames = new ArrayList<>(), groupIds = new ArrayList<>();
    groupNames.add("Без группы");
    groupIds.add("");
    if (options != null)
      for (int i = 0; i < options.length(); i++) {
        JSONObject g = options.optJSONObject(i);
        if (g != null) {
          groupNames.add(g.optString("name"));
          groupIds.add(g.optString("id"));
        }
      }
    android.widget.Spinner group = new android.widget.Spinner(this);
    group.setAdapter(
        new android.widget.ArrayAdapter<String>(
            this, android.R.layout.simple_spinner_dropdown_item, groupNames));
    add(group);
    body("ID, свободные порты и адрес получены от Hub. Можно изменить их перед сохранением.");
    action(
        "Добавить роутер",
        NativeUi.Style.PRIMARY,
        () -> {
          String routerId = value(id).trim();
          int entry, sshEntry;
          try {
            entry = Integer.parseInt(value(port));
            sshEntry = value(ssh).trim().isEmpty() ? entry + 1000 : Integer.parseInt(value(ssh));
          } catch (NumberFormatException invalid) {
            showError("Укажите числовые порты туннелей.");
            return;
          }
          if (!routerId.matches("[\\p{L}\\p{N}._-]{1,64}")
              || value(vpsHost).trim().isEmpty()
              || value(vpsHost).contains(" ")
              || entry < 1
              || entry > 65535
              || sshEntry < 1
              || sshEntry > 65535
              || entry == sshEntry) {
            showError("Проверьте ID, адрес VPS и разные порты от 1 до 65535.");
            return;
          }
          JSONObject payload =
              json(
                  "id",
                  routerId,
                  "name",
                  value(name).trim(),
                  "entry_port",
                  entry,
                  "ssh_entry_port",
                  sshEntry,
                  "role",
                  role.getSelectedItem().toString(),
                  "group_id",
                  groupIds.get(group.getSelectedItemPosition()),
                  "vps_host",
                  value(vpsHost).trim(),
                  "public_url",
                  api.getHub().toString());
          request(
              () -> api.post("/api/router", payload),
              result -> {
                reset();
                section("Роутер добавлен");
                body(
                    "ID: "
                        + routerId
                        + "\nНастройте агент роутера для связи с "
                        + api.getHub().getHost()
                        + ".");
                action(
                    "Получить настройки OpenWrt",
                    NativeUi.Style.PRIMARY,
                    () -> {
                      JSONObject created = result.optJSONObject("router");
                      if (created == null) created = json("id", routerId, "name", value(name));
                      startActivity(
                          new Intent(this, NativeRouterActivity.class)
                              .putExtra(
                                  NativeRouterActivity.EXTRA_HUB_ORIGIN, api.getHub().toString())
                              .putExtra(NativeRouterActivity.EXTRA_ROUTER_JSON, created.toString())
                              .putExtra(NativeRouterActivity.EXTRA_TOOL, "config"));
                    });
                action("Вернуться к роутерам", NativeUi.Style.SECONDARY, this::finish);
              });
        });
  }

  @Override
  public void onRequestPermissionsResult(int request, String[] permissions, int[] grants) {
    super.onRequestPermissionsResult(request, permissions, grants);
    HubNotifications.permissionResult(this, request, grants);
  }

  private void renderAbout(JSONObject data) {
    reset();
    body(
        "OWRT Hub для Android\nВерсия приложения: "
            + BuildConfig.VERSION_NAME
            + "\nВерсия Hub: "
            + data.optString("hub_version", "—")
            + "\nМобильный API: "
            + data.optInt("api_version"));
    body(
        "Приложение управляет вашим Hub через защищённое соединение HTTPS. Разрешения определяет"
            + " аккаунт Hub.");
    action(
        "Исходный код на GitHub",
        NativeUi.Style.SECONDARY,
        () -> external("https://github.com/kzolotarev95/luci-app-owrt-remote"));
  }

  private void renderDonate() {
    reset();
    LinearLayout card = ui.card();
    card.setGravity(android.view.Gravity.CENTER);
    card.addView(ui.iconBadge("crown", ui.AMBER));
    card.addView(ui.spacer(18));
    card.addView(ui.text("Буду искренне благодарен за любой донат!", 18, true));
    card.addView(ui.spacer(12));
    card.addView(
        ui.body(
            "Совершенно не важно, какая сумма — важна ваша поддержка. Даже небольшой вклад помогает"
                + " развивать проект и двигаться дальше."));
    card.addView(ui.spacer(12));
    card.addView(ui.body("Спасибо каждому, кто рядом! Всех обнял! 🤝🚀"));
    card.addView(ui.spacer(18));
    card.addView(
        ui.button(
            "Основная ссылка\ndonate.zks95.ugnss.ru",
            NativeUi.Style.PRIMARY,
            () -> external("https://donate.zks95.ugnss.ru")));
    card.addView(ui.spacer(10));
    card.addView(
        ui.button(
            "Резервная ссылка\ndonate.zks95.ugnss.ru:8443",
            NativeUi.Style.SECONDARY,
            () -> external("https://donate.zks95.ugnss.ru:8443")));
    card.addView(ui.spacer(20));
    card.addView(ui.compactDivider());
    card.addView(ui.spacer(14));
    String[] names = {"СБП", "СБЕР", "Альфа Банк", "ВТБ", "Т-БАНК"};
    int[] marks = {
      R.drawable.bank_sbp,
      R.drawable.bank_sber,
      R.drawable.bank_alfa,
      R.drawable.bank_vtb,
      R.drawable.bank_tbank
    };
    for (int start = 0; start < names.length; start += 3) {
      LinearLayout row = ui.row();
      row.setGravity(android.view.Gravity.CENTER);
      for (int i = start; i < Math.min(start + 3, names.length); i++) {
        LinearLayout brand = ui.column();
        brand.setGravity(android.view.Gravity.CENTER);
        brand.setPadding(ui.dp(5), ui.dp(6), ui.dp(5), ui.dp(6));
        android.widget.ImageView mark = new android.widget.ImageView(this);
        mark.setImageResource(marks[i]);
        mark.setContentDescription(names[i]);
        brand.addView(mark, new LinearLayout.LayoutParams(ui.dp(24), ui.dp(24)));
        brand.addView(ui.text(names[i], 12, true));
        row.addView(brand, new LinearLayout.LayoutParams(0, -2, 1));
      }
      card.addView(row);
    }
    add(card);
  }

  private void external(String url) {
    try {
      startActivity(
          new Intent(Intent.ACTION_VIEW, Uri.parse(url)).addCategory(Intent.CATEGORY_BROWSABLE));
    } catch (RuntimeException noBrowser) {
      showError("Не удалось открыть браузер.");
    }
  }
}
