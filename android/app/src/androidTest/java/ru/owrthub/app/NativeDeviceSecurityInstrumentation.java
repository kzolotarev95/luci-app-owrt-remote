package ru.owrthub.app;

import android.app.*;
import android.content.*;
import android.os.*;
import android.view.*;
import android.view.accessibility.AccessibilityNodeInfo;
import android.widget.*;
import org.json.JSONObject;
import java.io.*;

/** Only a disposable emulator with the fixture PIN; never use a real Hub/device. */
public final class NativeDeviceSecurityInstrumentation extends NativeSmokeInstrumentation {
  private boolean coldResume;
  private static final String PIN = "7614", WOL = "fixture-wol-secret";
  @Override public void onCreate(Bundle arguments) {
    coldResume = arguments != null && "true".equals(arguments.getString("coldResume"));
    super.onCreate(arguments);
  }
  @Override public void onStart() {
    Bundle result = new Bundle();
    try {
      require(AppLock.deviceSecure(getTargetContext()), "Only run after setting the isolated emulator fixture PIN");
      if (coldResume) {
        require(AppLock.enabled(getTargetContext()) && AppLock.needsUnlock(getTargetContext()), "New process starts locked");
        ActivityMonitor monitor=addMonitor(NativeLockActivity.class.getName(),null,false);
        ActivityMonitor mainMonitor=addMonitor(MainActivity.class.getName(),null,false);
        getTargetContext().startActivity(new Intent(getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        mainActivity=(MainActivity)mainMonitor.waitForActivityWithTimeout(10000);removeMonitor(mainMonitor);
        Activity locked=monitor.waitForActivityWithTimeout(10000); removeMonitor(monitor);
        require(locked != null,"Cold launch presents app lock"); activity=locked;
        assertCovered(mainActivity); confirmCredential();
        waitFor("cold unlock",10000,locked::isFinishing);
        activity=mainActivity;waitForNativeHome();clickText("Настройки");
        ActivityMonitor settingsMonitor=addMonitor(AppSecurityActivity.class.getName(),null,false);
        clickText("Защита приложения");Activity settings=settingsMonitor.waitForActivityWithTimeout(10000);removeMonitor(settingsMonitor);activity=settings;
        monitor=addMonitor(NativeLockActivity.class.getName(),null,false);clickText("Отключить защиту");locked=monitor.waitForActivityWithTimeout(10000);removeMonitor(monitor);activity=locked;
        waitSystemCredential();cancelSystemCredential();
        waitFor("cancelled disable",8000,()->containsText("Защита остаётся включённой"));
        require(AppLock.enabled(getTargetContext()),"Cancellation cannot disable protection");clickText("Закрыть приложение");activity=settings;
        monitor=addMonitor(NativeLockActivity.class.getName(),null,false);clickText("Отключить защиту");locked=monitor.waitForActivityWithTimeout(10000);removeMonitor(monitor);activity=locked;
        confirmCredential();Activity disabledGate=locked;waitFor("disable proof",10000,disabledGate::isFinishing);
        require(!AppLock.enabled(getTargetContext()),"Successful Android proof disables protection");
        result.putString("status","passed"); result.putString("results","cold_process_requires_android_credential,disable_requires_confirmation_and_cancellation_keeps_protection"); finish(0,result);return;
      }
      onUi(()->{getTargetContext().getSharedPreferences("app_lock",0).edit().clear().commit();AppLock.lock();return null;});
      HubNotifications.prefs(getTargetContext()).edit().putBoolean("offered",true).putBoolean("enabled",false).commit();
      new HubSessionStore(getTargetContext()).clear();
      new SshCredentialStore(getTargetContext()).save(fixture,USERNAME,"wol:main","");
      getTargetContext().getSharedPreferences("hub_connection",0).edit().clear().commit();
      launch(false); waitFor("connection",10000,()->shown("hub_host")); fill("hub_host",fixture);clickId("hub_connect");
      waitFor("captcha",15000,()->hasText("2468"));fillLogin("2468");clickId("hub_login");waitForNativeHome();
      clickText("Роутеры");clickText("Основа");waitFor("detail",10000,()->hasText("Wake-on-LAN"));assertCenteredRouter();
      openRouterTool("Wake-on-LAN");waitFor("WOL ready",10000,()->enabledText("Обновить"));
      onUi(()->{editorWithHintOnUi("Пароль SSH роутера").setText(WOL);return null;});clickText("Обновить");
      waitFor("WOL discovery",10000,()->hasText("Найдено устройств: 1"));
      SshCredentialStore secrets=new SshCredentialStore(getTargetContext());
      waitFor("successful WOL password saved encrypted",10000,()->{
        try{return secrets.load(fixture,USERNAME,"wol:main").equals(WOL);}catch(Exception e){throw new AssertionError(e);}
      });
      require(secrets.load(fixture,"another-user","wol:main").isEmpty() && secrets.load(fixture,USERNAME,"wol:offline").isEmpty(),"WOL scope isolates users and routers");
      require(!getTargetContext().getSharedPreferences("ssh_credentials",0).getAll().toString().contains(WOL),"No WOL plaintext in preferences");
      waitFor("discovery request finished",10000,()->enabledText("Обновить"));
      onUi(()->{editorWithHintOnUi("Пароль SSH роутера").setText("fixture-wrong-secret");return null;});clickText("Обновить");
      waitFor("failed WOL discovery",10000,()->containsText("Fixture SSH authentication failed"));
      require(secrets.load(fixture,USERNAME,"wol:main").equals(WOL),"Failed authentication cannot replace working saved WOL password");
      backToMain();openRouterTool("Wake-on-LAN");waitFor("WOL restored",10000,()->onUi(()->editorWithHintOnUi("Пароль SSH роутера").getText().toString().equals(WOL)));
      waitFor("WOL controls ready",10000,()->enabledText("Забыть сохранённый пароль"));
      clickText("Забыть сохранённый пароль");require(secrets.load(fixture,USERNAME,"wol:main").isEmpty(),"Forget removes WOL secret");
      checks.add("wol_autosave_restore_encryption_scope_and_forget");backToMain();clickText("Настройки");
      openSettingsTool("О приложении");waitFor("about crown",8000,()->onUi(()->{
        for(View v:views())if("about-crown".equals(v.getTag()))return true;return false;}));
      checks.add("gold_crown_before_about_text");backToMain();
      openRouterTool("Терминал VPS");require(!hasText("VPS"),"No isolated VPS hero label");backToMain();
      ActivityMonitor settingsMonitor=addMonitor(AppSecurityActivity.class.getName(),null,false);
      clickText("Защита приложения");activity=settingsMonitor.waitForActivityWithTimeout(10000);removeMonitor(settingsMonitor);
      require(activity!=null,"Local security settings open");Activity settings=activity;
      ActivityMonitor gateMonitor=addMonitor(NativeLockActivity.class.getName(),null,false);
      clickText("Включить защиту");Activity gate=gateMonitor.waitForActivityWithTimeout(10000);removeMonitor(gateMonitor);require(gate!=null,"Enable requests Android proof");activity=gate;
      waitSystemCredential();shell("input text 0000");shell("input keyevent 66");SystemClock.sleep(1200);
      require(!AppLock.enabled(getTargetContext()),"Wrong PIN cannot enable protection");cancelSystemCredential();
      waitFor("cancelled enable",8000,()->containsText("Защита не включена"));
      clickText("Закрыть приложение");activity=settings;waitFor("settings after cancel",8000,()->hasText("Включить защиту"));
      gateMonitor=addMonitor(NativeLockActivity.class.getName(),null,false);clickText("Включить защиту");gate=gateMonitor.waitForActivityWithTimeout(10000);removeMonitor(gateMonitor);activity=gate;
      confirmCredential();Activity enabledGate=gate;waitFor("enable success",10000,enabledGate::isFinishing);activity=settings;
      waitFor("enabled settings",8000,()->hasText("Защита включена"));require(AppLock.enabled(getTargetContext())&&!AppLock.needsUnlock(getTargetContext()),"Proof enables and unlocks current foreground");
      checks.add("enable_requires_real_android_pin_wrong_pin_and_cancel_do_not_enable");
      onUi(()->{settings.finish();return null;});activity=mainActivity;waitForNativeHome();
      gateMonitor=addMonitor(NativeLockActivity.class.getName(),null,false);
      getUiAutomation().performGlobalAction(android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_HOME);
      waitFor("background locks",8000,()->AppLock.needsUnlock(getTargetContext()));
      Intent terminal=new Intent(getTargetContext(),NativeRouterActivity.class).putExtra("tool","vps").putExtra("hub_origin",fixture).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
      ActivityMonitor toolMonitor=addMonitor(NativeRouterActivity.class.getName(),null,false);
      getTargetContext().startActivity(terminal);Activity tool=toolMonitor.waitForActivityWithTimeout(10000);removeMonitor(toolMonitor);
      gate=gateMonitor.waitForActivityWithTimeout(10000);removeMonitor(gateMonitor);require(tool!=null&&gate!=null,"Direct tool entry cannot bypass lock");activity=gate;assertCovered(tool);
      waitSystemCredential();cancelSystemCredential();
      waitFor("cancel result delivered before retry",8000,()->hasText("Вход не подтверждён. Приложение остаётся заблокированным."));
      require(AppLock.needsUnlock(getTargetContext()),"Cancel keeps app locked");assertCovered(tool);
      clickText("PIN, графический ключ или пароль");confirmCredential();Activity toolGate=gate;waitFor("tool unlocked",10000,toolGate::isFinishing);
      activity=tool;waitFor("terminal accessible",10000,()->hasText("Открыть терминал VPS"));
      require(!AppLock.needsUnlock(getTargetContext()),"Successful device proof restores tool");checks.add("background_cancel_and_direct_tool_entry_are_shielded_until_real_auth");
      onUi(()->{tool.finish();return null;});activity=mainActivity;waitForNativeHome();
      // Leave enabled so a second instrumentation process can check cold startup.
      result.putString("status","passed");result.putString("results",String.join(",",checks));finish(0,result);
    } catch(Throwable failure) {
      failure.printStackTrace();result.putString("status","failed");result.putString("error",failure.toString());
      result.putString("labels",visibleLabels());result.putString("results",String.join(",",checks));
      try { onUi(()->{AppLock.configure(getTargetContext(),false);return null;});shell("am force-stop com.android.settings"); } catch(Exception ignored) {}
      finish(1,result);
    }
  }
  private void assertCovered(Activity protectedActivity) {
    require(onUi(()->protectedActivity.getWindow().getDecorView().findViewWithTag("app-lock-shield")!=null),"Opaque shield covers protected Activity");
    require(onUi(()->protectedActivity.findViewById(android.R.id.content).getImportantForAccessibility()==View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS),"Accessibility cannot read protected content");
    require(onUi(()->(protectedActivity.getWindow().getAttributes().flags&WindowManager.LayoutParams.FLAG_SECURE)!=0),"Protected task snapshots are secure");
  }
  private void waitSystemCredential() throws Exception {
    waitFor("Android system credential",10000,()->{AccessibilityNodeInfo root=getUiAutomation().getRootInActiveWindow();return root!=null&&root.getPackageName()!=null&&!root.getPackageName().toString().startsWith("ru.owrthub");});
    SystemClock.sleep(500);
  }
  private void confirmCredential() throws Exception { waitSystemCredential();shell("input text "+PIN);shell("input keyevent 66"); }
  private void cancelSystemCredential() throws Exception {
    for(int n=0;n<3;n++) {
      AccessibilityNodeInfo root=getUiAutomation().getRootInActiveWindow();
      if(root!=null&&root.getPackageName()!=null&&root.getPackageName().toString().startsWith("ru.owrthub"))return;
      shell("input keyevent 4");SystemClock.sleep(400);
    }
  }
  private void shell(String command) throws Exception {
    try(InputStream stream=new ParcelFileDescriptor.AutoCloseInputStream(getUiAutomation().executeShellCommand(command))) {
      byte[] bytes=new byte[1024];while(stream.read(bytes)!=-1){}
    }
  }
}
