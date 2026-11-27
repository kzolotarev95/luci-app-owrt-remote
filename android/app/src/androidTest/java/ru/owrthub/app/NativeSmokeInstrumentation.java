package ru.owrthub.app;

import android.app.Activity;
import android.app.Instrumentation;
import android.app.UiAutomation;
import android.accessibilityservice.AccessibilityServiceInfo;
import android.content.Intent;
import android.graphics.Rect;
import android.graphics.Bitmap;
import android.graphics.drawable.ColorDrawable;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.View;
import android.view.ViewGroup;
import android.view.accessibility.AccessibilityNodeInfo;
import android.webkit.CookieManager;
import android.webkit.WebView;
import android.widget.EditText;
import android.widget.TextView;

import java.net.URI;
import java.io.File;
import java.io.FileOutputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/** Device UI checks for the native application, using only disposable loopback fixture data. */
public class NativeSmokeInstrumentation extends Instrumentation {
    protected static final String USERNAME = "fixture-user";
    protected static final String PASSWORD = "fixture-only-password";
    protected Activity activity;
    protected MainActivity mainActivity;
    protected String fixture;
    protected final List<String> checks = new ArrayList<>();

    @Override public void onCreate(Bundle arguments) {
        if (!BuildConfig.DEBUG) throw new IllegalArgumentException("Native smoke requires a debug build");
        String candidate = arguments == null ? "http://127.0.0.1:8766"
                : arguments.getString("fixtureUrl", "http://127.0.0.1:8766");
        URI local = HubUrlPolicy.normalizeHub(candidate, "", true);
        if (!("127.0.0.1".equals(local.getHost()) || "10.0.2.2".equals(local.getHost())
                || "localhost".equals(local.getHost())) || local.getPort() < 1) {
            throw new IllegalArgumentException("Only explicit loopback fixture origins are allowed");
        }
        fixture = local.toString();
        super.onCreate(arguments);
        start();
    }

    @Override public void onStart() {
        Bundle result = new Bundle();
        HubNotifications.prefs(getTargetContext()).edit().putBoolean("offered",true).putBoolean("enabled",false).commit();
        try {
            require(getUiAutomation().setRotation(UiAutomation.ROTATION_FREEZE_0), "Set portrait fixture orientation");
            AccessibilityServiceInfo automation = getUiAutomation().getServiceInfo();
            automation.flags |= AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS;
            getUiAutomation().setServiceInfo(automation);
            checkChartGaps();
            new HubSessionStore(getTargetContext()).clear();
            require(getTargetContext().getSharedPreferences("hub_connection", 0).edit().clear().commit(), "Clear connection preferences");
            require(getTargetContext().getSharedPreferences("native_design", 0).edit().clear().commit(), "Reset fixture theme");
            CountDownLatch cleared = new CountDownLatch(1);
            runOnMainSync(() -> CookieManager.getInstance().removeAllCookies(ok -> cleared.countDown()));
            require(cleared.await(5, TimeUnit.SECONDS), "Clear browser cookies");
            launch(false);
            waitFor("connection form", 10000, () -> shown("hub_host"));
            require(onUi(()->editorWithHintOnUi("https://hub.example.com").getText().length()==0),"Connection address starts empty");
            require(!hasText("Нативное приложение"),"No native app promotional block");
            assertCenteredFields();
            checks.add("empty_connection_form_without_preset_server");
            fill("hub_host", "http://198.51.100.1");
            clickId("hub_connect");
            waitFor("public HTTP rejection", 5000, () -> containsText("HTTPS"));
            require(shown("hub_host") && !shown("hub_captcha"), "Public HTTP stays on the connection screen");
            assertNative("connection");
            checks.add("reject_public_http_without_network_login");

            fill("hub_host", fixture);
            if (shown("hub_port")) fill("hub_port", "");
            clickId("hub_connect");
            waitFor("native CAPTCHA form", 20000, () -> shown("hub_captcha") && shown("hub_login") && hasText("2468"));
            require(!shown("hub_otp"), "Owner OTP is initially hidden before the username matches");
            fill("hub_user", USERNAME);
            waitFor("owner OTP field", 5000, () -> shown("hub_otp"));
            assertCenteredFields();
            checks.add("login_and_captcha_fields_centered");
            capture("native-login");
            assertNative("login");
            fillLogin("0000");
            clickId("hub_login");
            waitFor("CAPTCHA failure feedback", 15000, () -> containsAnyText(
                    "Invalid fixture CAPTCHA", "Неверная капча", "Неверная CAPTCHA", "Капча не", "Нужен вход", "Не удалось войти", "Ошибка входа"));
            require(shown("hub_login"), "Wrong CAPTCHA must keep the native sign-in form");
            HubSessionStore.SavedSession rejected = new HubSessionStore(getTargetContext()).load();
            require(rejected == null || rejected.cookie.isEmpty(), "Wrong CAPTCHA must not create a stored session");
            checks.add("native_captcha_failure_does_not_authenticate");

            // Failed login refreshes the challenge on a short delay.
            SystemClock.sleep(750);
            waitFor("renewed CAPTCHA after failure", 10000, () -> hasText("2468") && shown("hub_otp"));
            fillLogin("2468");fill("hub_otp","000000");clickId("hub_login");
            waitFor("incorrect OTP feedback",10000,()->containsAnyText("Invalid fixture login or OTP","Неверный или пустой код 2FA"));
            require(shown("hub_login"),"Incorrect OTP cannot open authenticated screens");
            checks.add("incorrect_owner_otp_is_rejected_without_authentication");
            SystemClock.sleep(750);waitFor("fresh challenge after OTP failure",10000,()->hasText("2468"));
            fillLogin("2468");clickId("hub_login");waitForNativeHome();
            assertNative("authenticated overview");
            require(!shown("hub_password"), "Password editor is removed after sign-in");
            HubSessionStore.SavedSession saved = new HubSessionStore(getTargetContext()).load();
            require(saved != null && !saved.cookie.isEmpty(), "Native sign-in creates a persistent session");
            String stored = getTargetContext().getSharedPreferences("hub_native_session_v1", 0).getAll().toString();
            require(!stored.contains(PASSWORD) && !stored.contains(saved.cookie) && !stored.contains(USERNAME),
                    "Saved credentials and session metadata remain encrypted");
            checks.add("native_login_encrypted_session_without_webview");
            require(!hasText("Адрес скрыт") && !hasText("127.0.0.1"),"Header omits the address and its placeholder");
            checks.add("hub_header_without_address_or_placeholder");
            capture("native-overview");
            checkLandscapeOverview();

            relaunch();
            waitForNativeHome();
            assertNative("restored overview");
            require(!shown("hub_login"), "Activity relaunch restores the session without another password");
            checks.add("keystore_session_survives_activity_relaunch");

            clickText("Роутеры");
            waitFor("router cards", 15000, () -> hasText("Домашний роутер") && hasText("Дача"));
            fillSearch("Дача");
            waitFor("offline router search", 5000, () -> hasText("Дача") && !hasText("Домашний роутер"));
            fillSearch("Домашний");
            waitFor("online router search", 5000, () -> hasText("Домашний роутер") && !hasText("Дача"));
            EditText activeSearch = editorWithHint("поиск");
            runOnMainSync(() -> { activeSearch.requestFocus(); activeSearch.setSelection(3); });
            SystemClock.sleep(11500);
            require(onUi(() -> editorWithHintOnUi("поиск") == activeSearch && activeSearch.hasFocus()
                    && activeSearch.getText().toString().equals("Домашний") && activeSearch.getSelectionStart() == 3),
                    "Automatic refresh preserves the active search editor, text, focus, and cursor");
            checks.add("native_auto_refresh_preserves_active_search_input");
            fillSearch("");
            waitFor("cleared search", 5000, () -> hasText("Домашний роутер") && hasText("Дача"));
            assertNative("router search");
            checks.add("native_router_search_filters_fixture_results");
            capture("native-routers");

            clickText("Домашний роутер");
            waitFor("router details", 10000, () -> hasText("Настройки Xray"));
            assertNative("router detail");
            assertCenteredRouter();
            checks.add("native_selected_router_detail");
            capture("native-router-detail");
            clickText("Роутеры");clickText("Дача");
            waitFor("offline details",8000,()->containsText("Нет связи с агентом"));
            assertCenteredRouter();capture("native-router-offline");
            clickText("Роутеры");clickText("Домашний роутер");
            waitFor("online details again",8000,()->hasText("Настройки Xray"));
            checks.add("online_offline_hero_and_equal_centered_stat_columns");

            openRouterTool("Настройки Xray");
            waitFor("native Xray controls", 15000, () -> enabledText("Перезапустить Xray"));
            assertNative("Xray tool");
            require(!enabledText("Запустить Xray"), "Start is disabled while Xray is running");
            TextView stableStatus = onUi(() -> { for (View v : views()) if (v instanceof TextView && ((TextView)v).getText().toString().equals("Xray: запущен")) return (TextView)v; return null; });
            SystemClock.sleep(3400);
            require(onUi(() -> stableStatus != null && stableStatus.isAttachedToWindow()), "Polling keeps the Xray screen attached");
            checks.add("running_xray_start_disabled_and_stable_status");
            clickText("Перезапустить Xray");
            acceptConfirmationIfPresent();
            waitFor("queued or running Xray command", 8000, () -> containsAnyText(
                    "queued", "running", "В очереди", "Выполняется", "Ожидает", "ожидает", "отправлена"));
            require(containsText("Прошло ") && containsText("До предела ожидания"), "Pending command shows elapsed time and its waiting limit");
            capture("native-xray-pending");
            waitFor("confirmed Xray result", 25000, () -> containsAnyText(
                    "succeeded", "Успешно", "успешно", "подтверждена", "Выполнена"));
            require(containsAnyText("Перезапуск", "Перезапустить", "restart"), "Completed command remains visible in history");
            assertNative("confirmed Xray result");
            checks.add("native_xray_command_waits_for_confirmed_result");
            capture("native-xray-success");
            clickText("Очистить завершённые записи");
            acceptConfirmationIfPresent();
            waitFor("cleared Xray history", 10000, () -> containsAnyText("История пуста", "Нет команд", "Команд пока нет", "пока нет"));
            checks.add("native_xray_history_clear");

            backToMain();
            waitFor("router detail after tool", 8000, () -> hasText("Настройки Xray"));
            openRouterTool("Wake-on-LAN");
            waitFor("WOL device list", 12000, () -> containsText("Найдено устройств: 1"));
            onUi(() -> { for (View v : views()) if (v instanceof android.widget.Spinner) ((android.widget.Spinner)v).setSelection(1); return null; });
            waitFor("WOL selected device", 5000, () -> enabledText("Разбудить"));
            clickText("Разбудить");
            waitFor("WOL result", 10000, () -> hasText("Пакет пробуждения отправлен"));
            capture("native-wol-devices"); checks.add("wol_discovers_selects_and_wakes_device");
            backToMain();
            checkNotesWhenAvailable();

            clickText("Настройки");
            waitFor("native settings", 8000, () -> hasText("Оформление"));
            assertCompactOwner();
            capture("native-settings");
            clickText("Подключение");
            waitFor("connection security label", 5000, () -> hasText("Сессия зашифрована в Android"));
            require(onUi(() -> { for (View v : views()) if (v instanceof TextView && ((TextView)v).getText().toString().equals("Сессия зашифрована в Android")) {
                View parent=(View)v.getParent(); return Math.abs((v.getLeft()+v.getRight())/2f-parent.getWidth()/2f)<3;
              } return false; }), "Connection security label is centered");
            sendKeyDownUpSync(android.view.KeyEvent.KEYCODE_BACK); waitForIdleSync();
            checks.add("connection_security_label_centered");
            openSettingsTool("Xray на VPS");
            waitFor("VPS Xray controls", 10000, () -> enabledText("Обновить Xray CFG"));
            clickText("Обновить Xray CFG"); acceptConfirmationIfPresent();
            waitFor("VPS CFG result", 10000, () -> containsText("Конфигурация обновлена"));
            clickText("Перезапустить Xray на VPS"); acceptConfirmationIfPresent();
            waitFor("VPS restart result", 10000, () -> containsText("Xray на VPS перезапущен"));
            capture("native-vps-xray"); checks.add("vps_xray_cfg_and_restart"); backToMain();
            openSettingsTool("Обновить роутеры");
            waitFor("router selection", 10000, () -> hasText("Обновить выбранные"));
            clickText("Снять выбор");
            onUi(() -> { for (View v : views()) if (v instanceof android.widget.CheckBox && ((android.widget.CheckBox)v).getText().toString().contains("Домашний")) ((android.widget.CheckBox)v).setChecked(true); return null; });
            require(containsText("Выбрано 1 из 2"), "Router selection count");
            capture("native-router-update-selection");
            clickText("Обновить выбранные"); acceptConfirmationIfPresent();
            waitFor("selected queue", 10000, () -> hasText("Остановить очередь"));
            require(!hasText("Дача"), "Unselected router is not in queue results");
            clickText("Остановить очередь"); acceptConfirmationIfPresent();
            waitFor("cancelled selected queue", 10000, () -> hasText("Убрать результаты"));
            checks.add("native_update_only_selected_router"); backToMain();
            openSettingsTool("Активные сессии");
            waitFor("current native session data", 10000, () -> hasText("Android fixture") && hasText("Текущая сессия"));
            assertNative("session settings");
            capture("native-sessions");
            backToMain();
            openSettingsTool("Группы роутеров");
            waitFor("native group data", 10000, () -> hasText("Дом") && hasText("Загородные"));
            assertNative("group settings");
            capture("native-groups");
            backToMain();
            checks.add("native_session_and_group_settings_show_server_data");
            clickText("Оформление");
            waitFor("theme options", 5000, () -> hasText("Зимний"));
            clickText("Потоки");
            require(getTargetContext().getSharedPreferences("native_design",0).getString("effect", "").equals("streams"), "Streams preference");
            clickText("Снег");
            require(getTargetContext().getSharedPreferences("native_design",0).getString("effect", "").equals("snow"), "Snow preference");
            capture("native-snow-effect"); checks.add("native_streams_and_snow_effects");
            clickText("Графитовый");
            int dark = backgroundColor();
            clickText("Зимний");
            waitFor("winter theme colors", 5000, () -> backgroundColor() != dark);
            int light = backgroundColor();
            relaunch();
            waitForNativeHome();
            require(backgroundColor() == light, "Winter theme remains after Activity relaunch");
            capture("native-winter-overview");
            assertNative("theme restore");
            checks.add("native_theme_changes_and_survives_relaunch");

            clickText("Настройки");
            clickAnyText("Выйти", "Выйти из аккаунта", "Выйти из Hub");
            acceptConfirmationIfPresent();
            waitFor("logged-out screen", 15000, () -> shown("hub_login") || shown("hub_host"));
            HubSessionStore.SavedSession loggedOut = new HubSessionStore(getTargetContext()).load();
            require(loggedOut == null || loggedOut.cookie.isEmpty(), "Logout clears the encrypted native token");
            relaunch();
            waitFor("signed-out relaunch", 15000, () -> shown("hub_login") || shown("hub_host"));
            require(!hasText("Обзор"), "Relaunch after logout does not restore authenticated navigation");
            if (shown("hub_password")) require(editorText("hub_password").isEmpty(), "Logout never restores a password");
            assertNative("logout");
            checks.add("native_logout_revokes_session_and_stays_logged_out");
            checkUnconfirmedLogout();

            result.putString("stream", "\nOWRT_ANDROID_NATIVE_SMOKE_OK " + checks.size() + " checks\n"
                    + String.join("\n", checks) + "\n");
            result.putInt("checks", checks.size());
            finish(Activity.RESULT_OK, result);
        } catch (Throwable error) {
            result.putString("stream", "\nOWRT_ANDROID_NATIVE_SMOKE_FAILED after " + checks + "\n" + error
                    + "\nVisible native labels: " + visibleLabels() + "\n");
            result.putString("error", error.toString());
            finish(Activity.RESULT_CANCELED, result);
        } finally {
            try { getUiAutomation().setRotation(UiAutomation.ROTATION_UNFREEZE); }
            catch (RuntimeException ignored) { }
        }
    }

    protected void checkChartGaps() {
        onUi(() -> {
            NativeUi ui = new NativeUi(getTargetContext());
            ChartView chart = new ChartView(getTargetContext(), ui);
            int width = ui.dp(400), height = ui.dp(200);
            chart.layout(0, 0, width, height);
            List<ChartView.Point> observations = new ArrayList<>();
            observations.add(new ChartView.Point(60000, 2, 4));
            observations.add(new ChartView.Point(180000, 2, 4));
            chart.setData(observations, 0, 240000);
            require(chart.getContentDescription().toString().contains("Доступность роутеров"), "Chart accessibility and labels use correct Russian encoding");
            chart.setExpectedIntervalMillis(60000);
            chart.setGapAfterMillis(90000);
            Bitmap gap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888);
            Bitmap continuous = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888);
            try {
                chart.draw(new android.graphics.Canvas(gap));
                chart.setGapAfterMillis(180000);
                chart.draw(new android.graphics.Canvas(continuous));
                int missingGreen = 0, connectedGreen = 0;
                for (int y = height * 40 / 100; y < height * 65 / 100; y++) {
                    for (int x = width * 45 / 100; x < width * 65 / 100; x++) {
                        if (gap.getPixel(x, y) == ui.GREEN) missingGreen++;
                        if (continuous.getPixel(x, y) == ui.GREEN) connectedGreen++;
                    }
                }
                require(missingGreen == 0 && connectedGreen > 0,
                        "Graph displays actual continuity and leaves server-defined missing observations blank");
            } finally { gap.recycle(); continuous.recycle(); }
            return null;
        });
        checks.add("chart_respects_server_gap_threshold_without_invented_observations");
    }

    protected void checkNotesWhenAvailable() throws Exception {
        require(!hasText("Глобальные логи"), "Retired router journal is absent");
        if (hasAnyText("Заметки", "Заметка")) {
            openRouterTool("Заметки");
            waitFor("native note editor", 10000, () -> editorWithHint("запомнить") != null);
            EditText note = editorWithHint("запомнить");
            runOnMainSync(() -> note.setText("Native smoke fixture note"));
            waitFor("enabled note append", 10000, () -> enabledText("Добавить заметку"));
            clickText("Добавить заметку");
            waitFor("saved native note", 10000, () -> hasText("Новая запись добавлена в Hub") && hasText("Native smoke fixture note"));
            require(hasText("Тестовая заметка о домашнем роутере"), "Appending a note preserves prior entries");
            assertNative("notes");
            checks.add("native_notes_save_fixture_text");
            backToMain();
        }
    }

    protected void checkUnconfirmedLogout() throws Exception {
        if (shown("hub_host")) { fill("hub_host", fixture); clickId("hub_connect"); }
        waitFor("new login challenge", 15000, () -> shown("hub_login") && hasText("2468"));
        fill("hub_user", USERNAME);
        waitFor("owner OTP for logout regression", 5000, () -> shown("hub_otp"));
        fillLogin("2468"); clickId("hub_login"); waitForNativeHome();
        HubApiClient fixtureControl = new HubApiClient(URI.create(fixture), new HubSessionStore(getTargetContext()));
        fixtureControl.get("/api/fixture/logout-fail-next");
        clickText("Настройки"); clickText("Выйти из Hub"); acceptConfirmationIfPresent();
        waitFor("local logout despite server failure", 15000, () -> shown("hub_login")
                && containsText("Локальный выход выполнен") && containsText("не подтверждено"));
        HubSessionStore.SavedSession saved = new HubSessionStore(getTargetContext()).load();
        require(saved == null || saved.cookie.isEmpty(), "Unconfirmed logout still clears the local encrypted session");
        relaunch();
        waitFor("unconfirmed logout stays signed out", 15000, () -> shown("hub_login") || shown("hub_host"));
        require(!hasText("Обзор"), "Unavailable server logout cannot resurrect a local session");
        checks.add("native_unconfirmed_logout_clears_local_session");
    }

    protected void launch(boolean debugOrigin) {
        Intent intent = new Intent(getTargetContext(), MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        if (debugOrigin) intent.putExtra("debugHubUrl", fixture);
        mainActivity = (MainActivity) startActivitySync(intent);
        activity = mainActivity;
        waitForIdleSync();
    }

    protected void capture(String name) throws Exception {
        waitForIdleSync();
        // Widget assertions observe the UI tree before SurfaceFlinger necessarily
        // commits that frame; allow the emulator to present the asserted state.
        SystemClock.sleep(250);
        Bitmap bitmap = getUiAutomation().takeScreenshot();
        require(bitmap != null, "Capture native test screen");
        String folder = getTargetContext().getResources().getConfiguration().fontScale < 1f
                ? "native-smoke-minfont" : "native-smoke";
        File directory = getTargetContext().getExternalFilesDir(folder);
        require(directory != null && (directory.isDirectory() || directory.mkdirs()), "Prepare fixture screenshot directory");
        try (FileOutputStream output = new FileOutputStream(new File(directory, name + ".png"))) {
            require(bitmap.compress(Bitmap.CompressFormat.PNG, 100, output), "Write native test screenshot");
        } finally { bitmap.recycle(); }
    }

    protected void checkLandscapeOverview() throws Exception {
        require(getUiAutomation().setRotation(UiAutomation.ROTATION_FREEZE_90), "Rotate native overview");
        try {
            waitFor("landscape native layout", 8000, () -> onUi(() -> {
                View root = activity.getWindow().getDecorView();
                return root.getWidth() > root.getHeight();
            }));
            require(hasText("Обзор") && hasText("Роутеры") && hasText("Настройки"), "Landscape keeps native navigation");
            assertNative("landscape overview");
            assertHorizontalBounds();
            SystemClock.sleep(800); // Wait for the platform rotation animation to finish before capture.
            capture("native-overview-landscape");
        } finally {
            require(getUiAutomation().setRotation(UiAutomation.ROTATION_FREEZE_0), "Restore portrait native overview");
        }
        waitFor("portrait native layout", 8000, () -> onUi(() -> {
            View root = activity.getWindow().getDecorView();
            return root.getWidth() < root.getHeight();
        }));
        checks.add("native_landscape_preserves_session_and_navigation");
    }

    protected void assertHorizontalBounds() {
        onUi(() -> {
            View root = activity.getWindow().getDecorView();
            int[] origin = new int[2]; root.getLocationOnScreen(origin);
            for (View view : views()) if (view.isShown() && view.getWidth() > 0 && view.getHeight() > 0) {
                int[] position = new int[2]; view.getLocationOnScreen(position);
                if (position[1] + view.getHeight() < origin[1] || position[1] > origin[1] + root.getHeight()) continue;
                require(position[0] >= origin[0] - 1 && position[0] + view.getWidth() <= origin[0] + root.getWidth() + 1,
                        "Visible native view stays within the horizontal window bounds");
            }
            return null;
        });
    }

    protected void relaunch() {
        runOnMainSync(() -> activity.finish());
        waitForIdleSync();
        launch(false);
    }

    protected void openRouterTool(String title) {
        openTool("ru.owrthub.app.NativeRouterActivity", title);
    }

    protected void openSettingsTool(String title) {
        openTool("ru.owrthub.app.NativeSettingsActivity", title);
    }

    protected void openTool(String className, String title) {
        ActivityMonitor monitor = addMonitor(className, null, false);
        try {
            clickText(title);
            Activity opened = monitor.waitForActivityWithTimeout(10000);
            require(opened != null, "Open native tool Activity");
            activity = opened;
            waitForIdleSync();
        } finally { removeMonitor(monitor); }
    }

    protected void backToMain() throws Exception {
        sendKeyDownUpSync(android.view.KeyEvent.KEYCODE_BACK);
        waitForIdleSync();
        SystemClock.sleep(150);
        if(!activity.isFinishing()&&!activity.isDestroyed()){
            sendKeyDownUpSync(android.view.KeyEvent.KEYCODE_BACK);waitForIdleSync();
        }
        activity = mainActivity;
        waitFor("main navigation after native tool", 10000, () -> hasText("Настройки"));
    }

    protected void waitForNativeHome() throws Exception {
        waitFor("authenticated native navigation", 20000, () -> hasText("Обзор") && hasText("Роутеры")
                && hasText("Настройки") && !shown("hub_login"));
    }

    protected void fillLogin(String captcha) {
        fill("hub_user", USERNAME);
        fill("hub_password", PASSWORD);
        fill("hub_captcha", captcha);
        fill("hub_otp", "123456");
    }

    protected int resourceId(String name) { return getTargetContext().getResources().getIdentifier(name, "id", getTargetContext().getPackageName()); }
    protected boolean shown(String name) {
        return onUi(() -> { View view = activity.findViewById(resourceId(name)); return view != null && view.isShown(); });
    }
    protected void fill(String id, String value) {
        onUi(() -> {
            View view = activity.findViewById(resourceId(id));
            require(view instanceof EditText && view.isShown(), "Visible native editor " + id);
            ((EditText) view).setText(value);
            return null;
        });
    }
    protected String editorText(String id) { return onUi(() -> ((EditText) activity.findViewById(resourceId(id))).getText().toString()); }
    protected void assertCenteredFields() {
        onUi(()->{
            for(View view:views())if(view instanceof EditText && view.isShown())
                require((((EditText)view).getGravity() & android.view.Gravity.HORIZONTAL_GRAVITY_MASK)==android.view.Gravity.CENTER_HORIZONTAL,"Login hints and entered data are centered");
            return null;
        });
    }
    protected void assertCenteredRouter() {
        onUi(()->{
            android.widget.LinearLayout hero=null,stats=null;
            for(View view:views()) {
                if("router-hero".equals(view.getTag()))hero=(android.widget.LinearLayout)view;
                if("router-stats".equals(view.getTag()))stats=(android.widget.LinearLayout)view;
            }
            require(hero!=null && stats!=null,"Router detail cards present");
            require((hero.getGravity() & android.view.Gravity.HORIZONTAL_GRAVITY_MASK)==android.view.Gravity.CENTER_HORIZONTAL,"Heartbeat chip and icon centered");
            for(int n=0;n<stats.getChildCount();n++)if(stats.getChildAt(n) instanceof android.widget.LinearLayout){
                android.widget.LinearLayout row=(android.widget.LinearLayout)stats.getChildAt(n);
                if(row.getChildCount()!=2)continue;
                int gap=row.getChildAt(1).getLeft()-row.getChildAt(0).getRight();
                float dp=getTargetContext().getResources().getDisplayMetrics().density;
                require(gap>=8*dp && gap<=11*dp,"Device stat values stay close to their labels");
                require(Math.abs((row.getChildAt(0).getLeft()+row.getChildAt(1).getRight())/2f-row.getWidth()/2f)<=2,"Compact device stat pairs centered");
                for(int j=0;j<2;j++)require((((TextView)row.getChildAt(j)).getGravity() & android.view.Gravity.HORIZONTAL_GRAVITY_MASK)==android.view.Gravity.CENTER_HORIZONTAL,"Device labels and values centered");
            }
            return null;
        });
    }
    protected void assertCompactOwner() {
        onUi(()->{
            android.widget.LinearLayout card=null;boolean crown=false;
            for(View view:views()) {
                if("account-card".equals(view.getTag()))card=(android.widget.LinearLayout)view;
                if("Владелец Hub · золотая корона".contentEquals(view.getContentDescription()==null?"":view.getContentDescription()))crown=true;
            }
            require(card!=null && crown,"Owner card displays gold crown");
            require((card.getGravity() & android.view.Gravity.HORIZONTAL_GRAVITY_MASK)==android.view.Gravity.CENTER_HORIZONTAL,"Compact owner contents centered");
            for(int i=0;i<card.getChildCount();i++)if(card.getChildAt(i) instanceof TextView)
                require(((TextView)card.getChildAt(i)).getTextSize()/getTargetContext().getResources().getDisplayMetrics().scaledDensity<=15.1f,"Compact owner typography");
            return null;
        });
        checks.add("compact_centered_owner_card_with_gold_crown");
    }
    protected void fillSearch(String value) {
        EditText editor = editorWithHint("поиск");
        require(editor != null, "Router search editor");
        runOnMainSync(() -> editor.setText(value));
    }
    protected EditText editorWithHint(String fragment) {
        return onUi(() -> editorWithHintOnUi(fragment));
    }
    protected EditText editorWithHintOnUi(String fragment) {
        for (View view : views()) if (view instanceof EditText && view.isShown()) {
            CharSequence hint = ((EditText) view).getHint();
            if (hint != null && hint.toString().toLowerCase(Locale.ROOT).contains(fragment.toLowerCase(Locale.ROOT))) return (EditText) view;
        }
        return null;
    }
    protected void clickId(String id) {
        onUi(() -> {
            View view = activity.findViewById(resourceId(id));
            require(view != null && view.isShown() && view.isEnabled(), "Enabled native action " + id);
            clickOnUi(view);
            return null;
        });
        waitForIdleSync();
    }
    protected void clickText(String value) { clickAnyText(value); }
    protected void clickAnyText(String... values) {
        onUi(() -> {
            for (String value : values) for (View view : views()) {
                if (view instanceof TextView && !(view instanceof EditText) && view.isShown()
                        && ((TextView) view).getText().toString().equals(value) && clickableAncestor(view) != null) {
                    clickOnUi(view);
                    return null;
                }
            }
            throw new AssertionError("Native action missing: " + String.join(" / ", values));
        });
        waitForIdleSync();
    }
    protected void clickOnUi(View view) {
        View target = clickableAncestor(view);
        require(target != null && target.isEnabled(), "Enabled clickable native widget");
        target.requestRectangleOnScreen(new Rect(0, 0, target.getWidth(), target.getHeight()), true);
        require(target.performClick(), "Native widget dispatches a click");
    }
    protected View clickableAncestor(View view) {
        View target = view;
        while (target != null && !target.isClickable()) target = target.getParent() instanceof View ? (View) target.getParent() : null;
        return target;
    }

    protected void acceptConfirmationIfPresent() {
        long end = SystemClock.uptimeMillis() + 1200;
        while (SystemClock.uptimeMillis() < end) {
            AccessibilityNodeInfo root = getUiAutomation().getRootInActiveWindow();
            if (root != null) {
                List<AccessibilityNodeInfo> buttons = root.findAccessibilityNodeInfosByViewId("android:id/button1");
                for (AccessibilityNodeInfo button : buttons) {
                    if (button.isVisibleToUser() && button.isEnabled()) {
                        require(button.performAction(AccessibilityNodeInfo.ACTION_CLICK), "Confirm native dialog");
                        waitForIdleSync();
                        return;
                    }
                }
            }
            SystemClock.sleep(100);
        }
    }

    protected boolean hasText(String value) { return hasAnyText(value); }
    protected boolean enabledText(String value) {
        return onUi(() -> {
            for (View view : views()) if (view instanceof TextView && view.isShown()
                    && ((TextView) view).getText().toString().equals(value)) {
                View target = clickableAncestor(view);
                if (target != null && target.isEnabled()) return true;
            }
            return false;
        });
    }
    protected boolean hasAnyText(String... values) {
        return onUi(() -> {
            for (View view : views()) if (view instanceof TextView && !(view instanceof EditText) && view.isShown()) {
                String text = ((TextView) view).getText().toString();
                for (String value : values) if (text.equals(value)) return true;
            }
            return false;
        });
    }
    protected boolean containsText(String value) { return containsAnyText(value); }
    protected boolean containsAnyText(String... values) {
        return onUi(() -> {
            for (View view : views()) if (view instanceof TextView && !(view instanceof EditText) && view.isShown()) {
                String text = ((TextView) view).getText().toString();
                for (String value : values) if (text.contains(value)) return true;
            }
            return false;
        });
    }
    protected void assertNative(String screen) {
        require(onUi(() -> {
            for (View view : views()) if (view instanceof WebView && view.isShown()) return false;
            return true;
        }), "Native " + screen + " must not display a WebView");
    }
    protected int backgroundColor() {
        return onUi(() -> {
            ViewGroup content = activity.findViewById(android.R.id.content);
            View page = content.getChildAt(0);
            require(page.getBackground() instanceof ColorDrawable, "Native page has an inspectable background color");
            return ((ColorDrawable) page.getBackground()).getColor();
        });
    }
    protected List<View> views() {
        List<View> result = new ArrayList<>();
        collect(activity.getWindow().getDecorView(), result);
        return result;
    }
    protected void collect(View view, List<View> target) {
        target.add(view);
        if (view instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) view;
            for (int index = 0; index < group.getChildCount(); index++) collect(group.getChildAt(index), target);
        }
    }
    protected String visibleLabels() {
        if (activity == null) return "Activity was not created";
        return onUi(() -> {
            List<String> labels = new ArrayList<>();
            for (View view : views()) if (view instanceof TextView && !(view instanceof EditText) && view.isShown()) {
                String text = ((TextView) view).getText().toString();
                if (!text.isEmpty()) labels.add(text.length() > 240 ? text.substring(0, 240) : text);
            }
            return String.join(" | ", labels);
        });
    }
    protected <T> T onUi(Callable<T> action) {
        AtomicReference<T> result = new AtomicReference<>();
        AtomicReference<Throwable> failure = new AtomicReference<>();
        runOnMainSync(() -> {
            try { result.set(action.call()); }
            catch (Throwable error) { failure.set(error); }
        });
        if (failure.get() != null) throw new AssertionError("Native UI inspection failed: " + failure.get(), failure.get());
        return result.get();
    }
    protected interface Condition { boolean holds(); }
    protected void waitFor(String description, long timeout, Condition condition) throws Exception {
        long deadline = SystemClock.uptimeMillis() + timeout;
        while (SystemClock.uptimeMillis() < deadline) {
            if (condition.holds()) return;
            SystemClock.sleep(150);
        }
        throw new AssertionError("Timed out waiting for " + description);
    }
    protected void require(boolean condition, String message) { if (!condition) throw new AssertionError(message); }
}
