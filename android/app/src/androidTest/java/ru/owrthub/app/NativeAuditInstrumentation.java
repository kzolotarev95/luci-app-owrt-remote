package ru.owrthub.app;

import android.app.Activity;
import android.app.UiAutomation;
import android.content.Intent;
import android.content.IntentFilter;
import android.net.Uri;
import android.os.Bundle;
import android.os.SystemClock;
import android.widget.EditText;
import java.io.*;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.*;
import java.util.zip.GZIPInputStream;

/** Broader error, lifecycle, permission and actual ContentResolver transfer checks. */
public final class NativeAuditInstrumentation extends NativeSmokeInstrumentation {
    private HubSessionStore store;
    private HubApiClient control;
    @Override public void onStart(){
        Bundle result=new Bundle();
        HubNotifications.prefs(getTargetContext()).edit().putBoolean("offered",true).putBoolean("enabled",false).commit();
        try{
            store=new HubSessionStore(getTargetContext());store.clear();
            getUiAutomation().setRotation(UiAutomation.ROTATION_FREEZE_0);
            loginClient(USERNAME);
            control=new HubApiClient(URI.create(fixture),store);
            checkSessionRaces();
            checkMutationSession();
            checkErrors();
            launch(false);waitForNativeHome();
            checkStableOverview();
            checkNativeAlerts();
            checkAddRouterDefaults();
            checkPartialResourceFailure();
            clickText("Роутеры");waitFor("routers",10000,()->hasText("Домашний роутер"));clickText("Домашний роутер");
            assertCenteredRouter();
            checkPhotoNotes();
            checkNoteDeletion();
            checkNotesRotation();
            checkAutomaticSshPassword();
            checkWol();
            checkConsole("SSH-терминал",false);
            checkConfig();
            clickText("Настройки");
            assertCompactOwner();capture("native-owner-compact");
            checkNotifications();
            openSettingsTool("Обновление Hub");
            waitFor("incompatible source notice",10000,()->containsText("не поддерживает приложение Android"));
            require(!hasText("Установить обновление"),"Incompatible source never offers install");
            checks.add("native_update_source_compatibility_notice");backToMain();
            checkHubUpdateFlow();
            checkDonateBanner();
            checkConsole("Терминал VPS",true);
            checkUsers();
            checkRouterQueue();
            checkTwoFactorSwitch();
            checkViewerReturn();
            checkBackups();
            checkBrowserLogin();
            checkBrowserFiles();
            checkViewerLogin();
            result.putString("stream","\nOWRT_ANDROID_NATIVE_AUDIT_OK "+checks.size()+" checks\n"+String.join("\n",checks)+"\n");
            finish(Activity.RESULT_OK,result);
        }catch(Throwable e){
            result.putString("stream","\nOWRT_ANDROID_NATIVE_AUDIT_FAILED after "+checks+"\n"+android.util.Log.getStackTraceString(e)+"\nLabels: "+visibleLabels()+"\n");
            finish(Activity.RESULT_CANCELED,result);
        }finally{try{getUiAutomation().setRotation(UiAutomation.ROTATION_UNFREEZE);}catch(Exception ignored){}}
    }
    private HubApiClient loginClient(String username)throws Exception{
        HubApiClient client=new HubApiClient(URI.create(fixture),store);
        org.json.JSONObject challenge=client.loginChallenge().getJSONObject("captcha");
        client.login(username,PASSWORD,challenge.getString("token"),challenge.getString("code"),username.equals(USERNAME)?"123456":"");
        return client;
    }
    private void checkSessionRaces()throws Exception{
        ExecutorService io=Executors.newSingleThreadExecutor();
        try{
            for(String endpoint:new String[]{"/api/fixture/late-401","/api/fixture/late-session"}){
                HubApiClient old=loginClient(USERNAME);old.get("/api/fixture/late-reset");
                Future<Boolean> stale=io.submit(()->{try{old.get(endpoint);return false;}catch(HubApiClient.AuthRequiredException expected){return true;}});
                long end=SystemClock.uptimeMillis()+3000;
                while(!old.get("/api/fixture/late-started").optBoolean("started")&&SystemClock.uptimeMillis()<end)SystemClock.sleep(30);
                HubApiClient replacement=loginClient("fixture-viewer");String token=replacement.getCookieHeader();
                require(stale.get(5,TimeUnit.SECONDS),"Reject an old in-flight auth response");
                require(store.load().cookie.equals(token),"Old response cannot clear or overwrite a newer account session");
                require(old.session().getJSONObject("account").getString("username").equals("fixture-viewer"),"Old client synchronizes the current stored account");
            }
            checks.add("cross_screen_session_races_do_not_erase_new_login");
        }finally{io.shutdownNow();}
        control=loginClient(USERNAME);
    }
    private void checkErrors()throws Exception{
        for(String path:new String[]{"/api/fixture/validation-error","/api/fixture/false-success"}){
            try{control.get(path);throw new AssertionError("Failed operation must throw");}
            catch(HubApiClient.ApiException expected){require(expected.getMessage().contains(path.endsWith("validation-error")?"имя роутера":"Операция не выполнена"),"Display the real validation reason");}
        }
        checks.add("api_validation_and_false_success_are_reported_correctly");
    }
    private void checkMutationSession()throws Exception{
        HubApiClient stale=loginClient(USERNAME);loginClient("fixture-viewer");
        try{stale.post("/api/router/main/xray-control",new org.json.JSONObject().put("action","stop"));throw new AssertionError("Old action must not change accounts silently");}
        catch(HubApiClient.AuthRequiredException changed){require(changed.errorCode.equals("session_changed"),"Old screen action requires revalidation");}
        require(store.load().username.equals("fixture-viewer"),"An old action does not erase the new account");
        control=loginClient(USERNAME);control.get("/api/fixture/rotate-csrf");
        control.post("/api/router/main/xray-control",new org.json.JSONObject().put("action","clear_history"));
        checks.add("mutations_refresh_rotated_csrf_without_replay_or_account_switch");
    }
    private void checkHubUpdateFlow()throws Exception {
        control.get("/api/fixture/update-available");openSettingsTool("Обновление Hub");
        waitFor("available update",10000,()->hasText("Установить обновление"));
        clickText("Проверить обновление");waitFor("fresh update ready",10000,()->enabledText("Установить обновление"));
        clickText("Установить обновление");acceptConfirmationIfPresent();
        waitFor("confirmed update outcome",10000,()->containsText("Fixture Hub updated"));
        checks.add("native_hub_update_check_offer_confirmation_and_result");backToMain();
    }
    private void checkDonateBanner()throws Exception {
        openSettingsTool("Поддержать разработку");waitFor("donation banner",5000,()->containsText("Буду искренне благодарен")&&hasText("Т-БАНК")&&hasText("СБП"));
        require(containsText("Основная ссылка")&&containsText("Резервная ссылка"),"Donation page retains both links");
        capture("native-donate-banner");backToMain();checks.add("native_donate_banner_links_and_bank_logos");
    }

    private void checkStableOverview()throws Exception {
        android.view.View eye=onUi(()->{for(android.view.View v:views())if("Показать или скрыть адрес Hub".contentEquals(v.getContentDescription()==null?"":v.getContentDescription()))return v;return null;});
        require(eye==null && !hasText("Адрес скрыт") && !hasText("127.0.0.1"),"Header omits address, placeholder and eye");
        android.view.View root=onUi(()->activity.getWindow().getDecorView());
        android.view.View refresh=onUi(()->{for(android.view.View v:views())if(v instanceof android.widget.Button && ((android.widget.Button)v).getText().toString().equals("Обновить состояние"))return v;return null;});
        require(refresh!=null,"Refresh button exists");
        for(int n=0;n<35;n++) {SystemClock.sleep(200);require(onUi(()->refresh.isAttachedToWindow() && refresh.isEnabled() && activity.getWindow().getDecorView()==root),"Background refresh keeps window and controls attached and enabled");}
        checks.add("live_refresh_preserves_attached_window_and_enabled_controls");
    }
    private void checkNativeAlerts()throws Exception {
        if(android.os.Build.VERSION.SDK_INT>=33)getUiAutomation().grantRuntimePermission(getTargetContext().getPackageName(),android.Manifest.permission.POST_NOTIFICATIONS);
        runOnMainSync(()->HubNotifications.enable(activity));
        try {
          waitFor("background notifications connected",15000,()->HubNotifications.prefs(getTargetContext()).getString("status","").startsWith("Подключено"));
          control.get("/api/fixture/new-alert");
          waitFor("real Android notification delivered",12000,()->{
            for(android.service.notification.StatusBarNotification n:getTargetContext().getSystemService(android.app.NotificationManager.class).getActiveNotifications())
              if("Fixture background alert".equals(n.getNotification().extras.getString(android.app.Notification.EXTRA_TITLE)))return true;
            return false;
          });
          checks.add("background_hub_event_creates_system_android_notification");
        }finally{runOnMainSync(()->HubNotifications.disable(getTargetContext()));getTargetContext().getSystemService(android.app.NotificationManager.class).cancelAll();}
    }
    private void checkAddRouterDefaults()throws Exception {
        clickText("Настройки");openSettingsTool("Добавить роутер");
        waitFor("server suggested router",10000,()->onUi(()->{boolean id=false,web=false,ssh=false;for(android.view.View v:views())if(v instanceof EditText){String t=((EditText)v).getText().toString();id|=t.equals("node-2");web|=t.equals("18090");ssh|=t.equals("19090");}return id&&web&&ssh;}));
        Activity adding=activity;
        clickText("Добавить роутер");
        waitFor("created router config action",10000,()->hasText("Получить настройки OpenWrt"));
        openTool("ru.owrthub.app.NativeRouterActivity", "Получить настройки OpenWrt");
        waitFor("configuration after router creation",10000,()->containsText("Конфигурация получена"));
        runOnMainSync(()->{activity.finish();adding.finish();}); activity=mainActivity;waitForNativeHome();
        clickText("Обзор");
        checks.add("server_router_defaults_create_and_open_ready_configuration");
    }

    private void checkPartialResourceFailure()throws Exception{
        control.get("/api/fixture/resources-fail-next");
        clickText("Обновить состояние");
        waitFor("partial refresh result",12000,()->containsText("Ресурсы VPS временно недоступны"));
        require(hasText("Домашний роутер"),"Optional metric failure does not hide routers");
        checks.add("vps_metric_failure_does_not_break_router_refresh");
    }
    private void checkPhotoNotes()throws Exception {
        require(containsText("Заметки"),"Router card exposes note entry point");
        openRouterTool("Заметки");waitFor("notes loaded",10000,()->enabledText("Прикрепить изображения"));
        Uri image=document("photo.png");
        try(OutputStream out=getContext().getContentResolver().openOutputStream(image,"wt")){out.write(android.util.Base64.decode("iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAAO0lEQVR4nO3RQREAMAjEwKMKK7FykFUJ4cMvK+CYCXVfZ9NZXY8HBvwBMhEyETIRMhEyETIRMhEyUcgHZIIB+IPjugkAAAAASUVORK5CYII=",0));}
        documentResult(Intent.ACTION_OPEN_DOCUMENT,image,()->clickText("Прикрепить изображения"));
        waitFor("photo selected",12000,()->hasText("Фото 1 · удалить"));
        EditText draft=editorWithHint("запомнить");runOnMainSync(()->draft.setText("Фото из приложения"));
        waitFor("photo note can send",10000,()->enabledText("Добавить заметку"));
        waitFor("photo note can send",10000,()->enabledText("Добавить заметку"));
        clickText("Добавить заметку");
        waitFor("photo metadata in history",10000,()->onUi(()->{for(android.view.View v:views())if("note-image".equals(v.getTag()))return true;return false;}));
        onUi(()->{for(android.view.View v:views())if("note-image".equals(v.getTag())){v.requestRectangleOnScreen(new android.graphics.Rect(0,0,v.getWidth(),v.getHeight()),true);break;}return null;});
        waitFor("note image decoded",15000,()->onUi(()->{for(android.view.View v:views())if(v instanceof android.widget.ImageView && "note-image".equals(v.getTag()) && ((android.widget.ImageView)v).getDrawable()!=null)return true;return false;}));
        require(containsText("Фото из приложения") && containsText("Тестовая заметка"),"Photo append preserves old notes");
        capture("native-note-photo");backToMain();
        checks.add("notes_attach_actual_saf_image_upload_display_preserve_history");
    }

    private void checkNoteDeletion()throws Exception {
        openRouterTool("Заметки");waitFor("deletable notes",10000,()->enabledText("Удалить заметку"));
        EditText draft=editorWithHint("запомнить");runOnMainSync(()->draft.setText("Черновик сохраняется при удалении"));
        clickText("Удалить заметку");
        waitFor("deletion confirmation",5000,()->{
            android.view.accessibility.AccessibilityNodeInfo root=getUiAutomation().getRootInActiveWindow();
            if(root==null)return false;
            for(android.view.accessibility.AccessibilityNodeInfo button:root.findAccessibilityNodeInfosByViewId("android:id/button2"))
                if(button.isVisibleToUser() && button.isEnabled())return button.performAction(android.view.accessibility.AccessibilityNodeInfo.ACTION_CLICK);
            return false;
        });
        waitFor("cancel keeps note",5000,()->containsText("Фото из приложения"));
        clickText("Удалить заметку");acceptConfirmationIfPresent();
        waitFor("selected note deleted",10000,()->hasText("Заметка удалена") && !hasText("Фото из приложения"));
        require(containsText("Тестовая заметка"),"Deleting photo note preserves older entries");
        require(onUi(()->draft.getText().toString().equals("Черновик сохраняется при удалении")),"Deleting history does not discard unsaved draft");
        capture("native-note-deleted");backToMain();checks.add("selected_note_delete_confirmation_cancel_photo_removal_and_draft_preservation");
    }
    private void checkNotesRotation()throws Exception{
        openRouterTool("Заметки");waitFor("note editor",10000,()->enabledText("Добавить заметку"));
        EditText note=editorWithHint("запомнить");runOnMainSync(()->note.setText("Черновик после поворота"));
        Activity original=activity;getUiAutomation().setRotation(UiAutomation.ROTATION_FREEZE_90);SystemClock.sleep(800);
        require(activity==original&&onUi(()->note.getText().toString().equals("Черновик после поворота")),"Rotation preserves unsaved note and Activity");
        assertHorizontalBounds();getUiAutomation().setRotation(UiAutomation.ROTATION_FREEZE_0);SystemClock.sleep(600);
        clickText("Добавить заметку");waitFor("saved rotated note",10000,()->hasText("Новая запись добавлена в Hub"));
        checks.add("note_draft_survives_orientation_change");backToMain();
    }
    private void checkConsole(String label,boolean vps)throws Exception{
        int previousSessions=control.get("/api/fixture/counters").optInt("terminals");
        if(vps)openTool("ru.owrthub.app.NativeRouterActivity",label);else openRouterTool(label);
        String connect=vps?"Открыть терминал VPS":"Подключить SSH";
        waitFor("console ready",10000,()->enabledText(connect));require(!hasText("Отправить ↵") && editorWithHint("Команда или ответ")==null,"No terminal controls before connect");clickText(connect);
        waitFor("console output",12000,()->containsText("Fixture console ready"));
        for(int sample=0;sample<12;sample++){SystemClock.sleep(100);require(enabledText("Отправить ↵"),"Terminal send never disables during background reads");}
        if(!vps){
          SshCredentialStore secrets=new SshCredentialStore(getTargetContext());
          secrets.save(fixture,USERNAME,"main","fixture-ssh-secret");
          require(secrets.load(fixture,USERNAME,"main").equals("fixture-ssh-secret"),"Keystore restores router password");
          require(secrets.load(fixture,USERNAME,"offline").isEmpty() && secrets.load(fixture,"another-account","main").isEmpty(),"SSH credentials scoped to router and account");
          require(!getTargetContext().getSharedPreferences("ssh_credentials",0).getAll().toString().contains("fixture-ssh-secret"),"No plaintext saved SSH secret");
          secrets.save(fixture,USERNAME,"main","");
          require(secrets.load(fixture,USERNAME,"main").isEmpty(),"Forget removes secret");
          checks.add("ssh_credentials_encrypted_scoped_and_removable");
        }
        EditText entry=editorWithHint("Команда или ответ");require(entry!=null,"Native console editor");runOnMainSync(()->entry.setText("audit-text-only"));
        waitFor("console can send",10000,()->enabledText("Отправить ↵"));clickText("Отправить ↵");
        waitFor("console echo",10000,()->containsText("Fixture echo: audit-text-only"));
        backToMain();
        long end=SystemClock.uptimeMillis()+7000;
        while(control.get("/api/fixture/counters").optInt("terminals")!=previousSessions&&SystemClock.uptimeMillis()<end)SystemClock.sleep(100);
        require(control.get("/api/fixture/counters").optInt("terminals")==previousSessions,"Leaving a console closes its server session");
        checks.add(vps?"native_vps_console_opens_sends_closes":"native_router_console_opens_sends_closes");
    }
    private void checkAutomaticSshPassword()throws Exception {
        control.get("/api/fixture/password-next");
        openRouterTool("SSH-терминал");waitFor("SSH password controls",10000,()->enabledText("Подключить SSH"));
        require(editorWithHint("Пароль root")==null && !hasText("Отправить ↵"),"Terminal content hidden before connection");
        clickText("Подключить SSH");
        waitFor("SSH password prompt",10000,()->containsText("password:"));
        EditText secret=editorWithHint("Пароль root");require(secret!=null,"SSH credential editor");
        runOnMainSync(()->{secret.setText("fixture-ssh-secret");for(android.view.View v:views())if(v instanceof android.widget.Switch && ((android.widget.Switch)v).getText().toString().startsWith("Запомнить пароль"))((android.widget.Switch)v).setChecked(true);});
        clickText("Отправить пароль SSH");
        waitFor("saved password authenticates only on SSH password prompt",12000,()->containsText("Fixture authenticated SSH"));
        require(!containsText("fixture-ssh-secret"),"Password never appears in terminal output");
        backToMain();openRouterTool("SSH-терминал");
        waitFor("saved credential ready",10000,()->enabledText("Подключить SSH"));clickText("Подключить SSH");
        waitFor("saved credential restored",10000,()->onUi(()->{EditText e=editorWithHintOnUi("Пароль root");return e!=null&&e.getText().toString().equals("fixture-ssh-secret");}));
        clickText("Забыть пароль этого роутера");
        require(new SshCredentialStore(getTargetContext()).load(fixture,USERNAME,"main").isEmpty(),"Forget clears the selected router secret");
        backToMain();checks.add("saved_ssh_password_answers_prompt_and_can_be_forgotten");
    }

    private void checkWol()throws Exception{
        openRouterTool("Wake-on-LAN");waitFor("WOL discovery",10000,()->containsText("Найдено устройств: 1"));
        require(!enabledText("Разбудить"),"A device must be selected before waking it");
        onUi(()->{for(android.view.View v:views())if(v instanceof android.widget.Spinner)((android.widget.Spinner)v).setSelection(1);return null;});
        waitFor("WOL device selected",5000,()->enabledText("Разбудить"));clickText("Разбудить");
        waitFor("WOL accepted",10000,()->hasText("Пакет пробуждения отправлен")&&containsText("no packet was sent"));
        checks.add("wol_requires_selection_and_sends_selected_router_request");backToMain();
    }
    private void checkNotifications()throws Exception{
        ActivityMonitor monitor=addMonitor("ru.owrthub.app.NativeSettingsActivity",null,false);
        try{
            onUi(()->{for(android.view.View v:views())if("Уведомления".equals(v.getContentDescription())){clickOnUi(v);return null;}throw new AssertionError("Notification action");});
            activity=monitor.waitForActivityWithTimeout(10000);require(activity!=null,"Notification screen opened");
        }finally{removeMonitor(monitor);}
        waitFor("notification body",10000,()->hasText("Роутер на связи")&&hasText("Fixture notification body"));
        checks.add("notification_screen_shows_server_title_and_body");backToMain();
    }
    private void checkRouterQueue()throws Exception{
        openSettingsTool("Обновить роутеры");waitFor("router queue form",10000,()->enabledText("Перезапустить выбранные"));
        clickText("Перезапустить выбранные");acceptConfirmationIfPresent();
        waitFor("queue started",10000,()->hasText("Остановить очередь"));clickText("Остановить очередь");acceptConfirmationIfPresent();
        waitFor("queue cancelled",10000,()->containsText("Fixture queue cancelled"));
        require(hasText("Убрать результаты"),"Completed queue can be cleared");clickText("Убрать результаты");
        waitFor("queue cleared",10000,()->hasText("Новая очередь")&&!containsText("Fixture queue cancelled"));
        checks.add("router_queue_posts_csrf_confirms_cancels_and_clears_results");backToMain();
    }
    private void checkConfig()throws Exception{
        openRouterTool("OpenWrt config");waitFor("config loaded",10000,()->containsText("Native configuration fixture"));
        Uri file=document("config.sh");documentResult(Intent.ACTION_CREATE_DOCUMENT,file,()->clickText("Сохранить файл .sh"));
        waitFor("config document written",10000,()->readDocument(file).contains("uci set owrtremote"));
        checks.add("native_config_saf_writes_actual_bytes");backToMain();
    }
    private void checkViewerReturn()throws Exception{
        openSettingsTool("Активные сессии");waitFor("session page",10000,()->hasText("Android fixture"));
        loginClient("fixture-viewer");backToMain();
        waitFor("limited account refreshed",10000,()->hasText("fixture-viewer"));
        require(!hasText("Пользователи и доступ")&&!hasText("Терминал VPS"),"Returning from a tool refreshes permissions");
        checks.add("returning_to_main_refreshes_account_and_permissions");
        control=loginClient(USERNAME);relaunch();waitForNativeHome();clickText("Настройки");
    }
    private void checkTwoFactorSwitch()throws Exception{
        control.get("/api/fixture/totp-disable");
        openSettingsTool("Безопасность");waitFor("TOTP setup",10000,()->hasText("Начать настройку 2FA"));
        EditText password=editorWithHint("Пароль для изменения 2FA");require(password!=null,"TOTP password field");
        runOnMainSync(()->password.setText(PASSWORD));clickText("Начать настройку 2FA");
        waitFor("TOTP setup secret",10000,()->containsText("JBSWY3DPEHPK3PXP"));
        runOnMainSync(()->{callActivityOnPause(activity);callActivityOnResume(activity);});
        waitFor("TOTP setup survives authenticator switch",10000,()->containsText("JBSWY3DPEHPK3PXP")&&enabledText("Подтвердить и включить 2FA"));
        require((activity.getWindow().getAttributes().flags&android.view.WindowManager.LayoutParams.FLAG_SECURE)!=0,"TOTP secret screen blocks screenshots");
        onUi(()->{editorWithHintOnUi("Пароль для изменения 2FA").setText(PASSWORD);editorWithHintOnUi("Код 2FA").setText("123456");return null;});
        clickText("Подтвердить и включить 2FA");waitFor("TOTP enabled",10000,()->hasText("Выключить 2FA"));
        checks.add("totp_setup_survives_switch_to_authenticator_without_disk_storage");backToMain();
    }
    private void checkUsers()throws Exception{
        openSettingsTool("Пользователи и доступ");waitFor("user list",10000,()->hasText("Добавить пользователя"));
        clickText("Добавить пользователя");waitFor("user editor",5000,()->editorWithHint("Логин пользователя")!=null);
        onUi(()->{editorWithHintOnUi("Логин пользователя").setText("audit-user");editorWithHintOnUi("Пароль пользователя").setText("audit-password");
            editorWithHintOnUi("Повтор пароля пользователя").setText("audit-password");editorWithHintOnUi("Текущий пароль владельца").setText(PASSWORD);return null;});
        clickText("Сохранить пользователя");waitFor("saved user",12000,()->hasText("audit-user")&&hasText("Добавить пользователя"));
        checks.add("native_user_editor_saves_and_refreshes_account_list");backToMain();
    }
    private void checkBrowserLogin()throws Exception{
        control=loginClient(USERNAME);control.get("/api/fixture/browser-login-on");control.logout();
        relaunch();waitFor("browser sign-in option",10000,()->hasText("Пройти защищённый вход"));
        require(!shown("hub_password")&&!shown("hub_login"),"Browser-only CAPTCHA does not show a misleading native password form");
        openTool("ru.owrthub.app.BrowserActivity","Пройти защищённый вход");
        android.webkit.WebView web=onUi(()->{for(android.view.View v:views())if(v instanceof android.webkit.WebView)return (android.webkit.WebView)v;return null;});
        require(web!=null,"Visible protected web sign-in");
        long end=SystemClock.uptimeMillis()+10000;boolean ready=false;
        while(SystemClock.uptimeMillis()<end&&!ready){SystemClock.sleep(150);ready=onUi(()->web.getProgress()==100&&web.getUrl()!=null&&web.getUrl().contains("/login"));}
        require(ready,"Browser login loaded");
        String script="document.querySelector('[name=username]').value='fixture-user';document.querySelector('[name=password]').value='fixture-only-password';"
            +"document.querySelector('[name=captcha_answer]').value='2468';document.querySelector('[name=otp]').value='123456';document.querySelector('form').submit();";
        runOnMainSync(()->web.evaluateJavascript(script,null));
        waitFor("browser returns automatically",15000,()->activity.isFinishing()||activity.isDestroyed());activity=mainActivity;waitForNativeHome();assertNative("after protected browser sign-in");
        control=new HubApiClient(URI.create(fixture),store);control.get("/api/fixture/browser-login-off");
        checks.add("protected_browser_sign_in_returns_to_native_app_automatically");
    }
    private void checkViewerLogin()throws Exception{
        // The browser file test deliberately expired this session before returning.
        try{control.logout();}catch(HubApiClient.AuthRequiredException expired){
            require(store.load()==null,"An expired logout still clears the local session");
        }
        relaunch();waitFor("viewer login",10000,()->shown("hub_user")&&hasText("2468"));
        fill("hub_user","fixture-viewer");require(!shown("hub_otp"),"Viewer login does not request owner OTP");
        fill("hub_password",PASSWORD);fill("hub_captcha","2468");clickId("hub_login");waitForNativeHome();
        clickText("Роутеры");waitFor("viewer assigned router",10000,()->hasText("Домашний роутер"));require(!hasText("Дача"),"Viewer does not receive unrelated routers");
        clickText("Домашний роутер");require(!hasText("Настройки Xray")&&!hasText("SSH-терминал"),"Viewer does not see management actions");
        checks.add("limited_account_native_login_has_no_owner_otp_or_management_actions");
    }
    private void checkBrowserFiles()throws Exception{
        clickText("Роутеры");waitFor("browser router",10000,()->hasText("Домашний роутер"));clickText("Домашний роутер");
        openTool("ru.owrthub.app.BrowserActivity","Админка OpenWrt");
        android.webkit.WebView web=onUi(()->{for(android.view.View v:views())if(v instanceof android.webkit.WebView)return (android.webkit.WebView)v;return null;});
        require(web!=null,"Embedded router tool");
        waitFor("browser tool loaded",10000,()->onUi(()->web.getProgress()==100));
        Uri download=document("download.txt");documentResult(Intent.ACTION_CREATE_DOCUMENT,download,()->tapElement(web,"download"));
        waitFor("browser download bytes",12000,()->readDocument(download).equals("Fixture browser download bytes\n"));
        Uri upload=document("upload.txt");String payload="Fixture browser upload bytes\n";
        try(OutputStream out=getContext().getContentResolver().openOutputStream(upload,"wt")){out.write(payload.getBytes(StandardCharsets.UTF_8));}
        documentResult(Intent.ACTION_OPEN_DOCUMENT,upload,()->tapElement(web,"upload-file"));
        tapElement(web,"upload-submit");
        long end=SystemClock.uptimeMillis()+10000;
        while(control.get("/api/fixture/counters").optInt("uploaded_length")!=payload.getBytes(StandardCharsets.UTF_8).length&&SystemClock.uptimeMillis()<end)SystemClock.sleep(100);
        org.json.JSONObject counters=control.get("/api/fixture/counters");
        StringBuilder digest=new StringBuilder();for(byte b:java.security.MessageDigest.getInstance("SHA-256").digest(payload.getBytes(StandardCharsets.UTF_8)))digest.append(String.format(java.util.Locale.ROOT,"%02x",b&255));
        require(counters.optInt("uploaded_length")==payload.getBytes(StandardCharsets.UTF_8).length&&counters.optString("uploaded_sha256").equals(digest.toString()),"Embedded browser uploads exact selected document bytes");
        checks.add("embedded_browser_download_and_upload_transfer_actual_document_bytes");
        runOnMainSync(()->activity.finish());waitForIdleSync();activity=mainActivity;waitForNativeHome();
    }
    private void tapElement(android.webkit.WebView web,String id){
        CountDownLatch latch=new CountDownLatch(1);java.util.concurrent.atomic.AtomicReference<org.json.JSONObject> result=new java.util.concurrent.atomic.AtomicReference<>();
        runOnMainSync(()->web.evaluateJavascript("(()=>{let e=document.getElementById('"+id+"');if(!e)return null;e.scrollIntoView();let r=e.getBoundingClientRect();return JSON.stringify({x:r.x+r.width/2,y:r.y+r.height/2,d:devicePixelRatio});})()",value->{try{result.set(new org.json.JSONObject((String)new org.json.JSONTokener(value).nextValue()));}catch(Exception ignored){}latch.countDown();}));
        try{require(latch.await(4,TimeUnit.SECONDS)&&result.get()!=null,"Browser element exists: "+id);}catch(InterruptedException e){throw new AssertionError(e);}
        int[] offset=onUi(()->{int[] xy=new int[2];web.getLocationOnScreen(xy);return xy;});org.json.JSONObject r=result.get();
        float x=(float)(offset[0]+r.optDouble("x")*r.optDouble("d")),y=(float)(offset[1]+r.optDouble("y")*r.optDouble("d"));long now=SystemClock.uptimeMillis();
        sendPointerSync(android.view.MotionEvent.obtain(now,now,android.view.MotionEvent.ACTION_DOWN,x,y,0));
        sendPointerSync(android.view.MotionEvent.obtain(now,now+60,android.view.MotionEvent.ACTION_UP,x,y,0));waitForIdleSync();
    }
    private void checkBackups()throws Exception{
        openTool("ru.owrthub.app.NativeBackupActivity","Резервные копии");
        waitFor("backup owner permission",10000,()->enabledText("Сохранить резервную копию"));
        int restoreBaseline=control.get("/api/fixture/counters").optInt("restores");
        Uri backup=document("backup.tar.gz");documentResult(Intent.ACTION_CREATE_DOCUMENT,backup,()->clickText("Сохранить резервную копию"));
        waitFor("export result",12000,()->containsText("Резервная копия сохранена"));
        try(InputStream stream=getContext().getContentResolver().openInputStream(backup);GZIPInputStream gzip=new GZIPInputStream(stream)){
            require(new String(readBytes(gzip),StandardCharsets.UTF_8).contains("OWRT native backup fixture"),"SAF export preserves a valid archive");
        }
        checks.add("backup_export_transfers_verified_gzip_to_saf_document");
        Uri corrupt=document("corrupt.tar.gz");try(OutputStream out=getContext().getContentResolver().openOutputStream(corrupt,"wt")){out.write(new byte[]{31,(byte)139,8,0});}
        documentResult(Intent.ACTION_OPEN_DOCUMENT,corrupt,()->clickText("Выбрать архив для восстановления"));acceptConfirmationIfPresent();
        waitFor("corrupt import rejected",10000,()->containsText("повреждён"));
        require(control.get("/api/fixture/counters").optInt("restores")==restoreBaseline,"Corrupt gzip is rejected before any restore request");
        checks.add("corrupt_backup_is_rejected_before_server_restore");
        documentResult(Intent.ACTION_OPEN_DOCUMENT,backup,()->clickText("Выбрать архив для восстановления"));acceptConfirmationIfPresent();
        waitFor("restore confirmed",12000,()->containsText("Hub подтвердил восстановление"));
        require(store.load().cookie.isEmpty(),"Restore clears native session");
        checks.add("backup_import_sends_exact_archive_and_requires_new_login");
        runOnMainSync(()->activity.onBackPressed());waitForIdleSync();activity=mainActivity;
        waitFor("login after restore",10000,()->shown("hub_login"));
    }
    private byte[] readBytes(InputStream in)throws IOException{ByteArrayOutputStream out=new ByteArrayOutputStream();byte[] b=new byte[8192];int n;while((n=in.read(b))!=-1){if(out.size()+n>1024*1024)throw new IOException("Fixture output too big");out.write(b,0,n);}return out.toByteArray();}
    private Uri document(String name){return Uri.parse("content://ru.owrthub.app.test.documents/"+name);}
    private String readDocument(Uri uri){try(InputStream in=getContext().getContentResolver().openInputStream(uri)){return new String(readBytes(in),StandardCharsets.UTF_8);}catch(Exception e){return "";}}
    private void documentResult(String action,Uri uri,Runnable click)throws Exception{
        IntentFilter filter=new IntentFilter(action);filter.addCategory(Intent.CATEGORY_OPENABLE);filter.addDataType("*/*");
        Intent result=new Intent().setData(uri).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION|Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
        ActivityMonitor monitor=addMonitor(filter,new ActivityResult(Activity.RESULT_OK,result),true);
        try{click.run();long end=SystemClock.uptimeMillis()+5000;while(monitor.getHits()==0&&SystemClock.uptimeMillis()<end)SystemClock.sleep(50);require(monitor.getHits()>0,"SAF picker was launched");}
        finally{removeMonitor(monitor);}
        waitForIdleSync();
    }
}
