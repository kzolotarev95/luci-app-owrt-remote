package ru.owrthub.app;

import android.app.*;
import android.content.*;
import android.os.Bundle;
import android.view.Gravity;
import android.widget.*;

/** Device-local protection; unrelated to the server account's password/2FA. */
public final class AppSecurityActivity extends Activity {
  @Override public void onCreate(Bundle state) { super.onCreate(state); }
  @Override protected void onResume() { super.onResume(); render(); }
  private void render() {
    NativeUi ui = new NativeUi(this, getSharedPreferences("native_design", 0).getBoolean("winter", false));
    LinearLayout page = ui.column(); page.setGravity(Gravity.CENTER_HORIZONTAL);
    page.setBackgroundColor(ui.BG); page.setPadding(ui.dp(18), ui.dp(24), ui.dp(18), ui.dp(24));
    page.addView(ui.iconBadge("shield", ui.AMBER, 56)); page.addView(ui.spacer(16));
    page.addView(ui.text("Защита приложения", 22, true)); page.addView(ui.spacer(12));
    page.addView(ui.body("При открытии и возвращении из фона OWRT Hub запросит PIN, графический ключ или пароль телефона. Если биометрия настроена, можно использовать отпечаток."));
    page.addView(ui.spacer(18));
    page.addView(ui.chip(AppLock.enabled(this) ? "Защита включена" : "Защита выключена", AppLock.enabled(this) ? ui.GREEN : ui.MUTED));
    page.addView(ui.spacer(18));
    if (!AppLock.deviceSecure(this)) {
      page.addView(ui.body("Сначала настройте блокировку экрана в Android.")); page.addView(ui.spacer(12));
      page.addView(ui.button("Настроить блокировку телефона", true, () -> startActivity(new Intent(android.provider.Settings.ACTION_SECURITY_SETTINGS))));
    } else page.addView(ui.button(AppLock.enabled(this) ? "Отключить защиту" : "Включить защиту", !AppLock.enabled(this),
        () -> startActivityForResult(new Intent(this, NativeLockActivity.class).putExtra("operation", AppLock.enabled(this) ? "disable" : "enable"), 732)));
    page.addView(ui.spacer(12));
    Switch biometric = new Switch(this); biometric.setText("Отпечаток / биометрия");
    biometric.setTextColor(ui.TEXT); biometric.setChecked(AppLock.biometric(this));
    biometric.setEnabled(AppLock.enabled(this) && AppLock.hasBiometric(this));
    biometric.setOnCheckedChangeListener((button, value) -> AppLock.setBiometric(this, value));
    page.addView(biometric);
    if (!AppLock.hasBiometric(this)) page.addView(ui.body("Биометрия пока не настроена или не поддерживается. Доступны PIN, графический ключ и пароль телефона."));
    if (AppLock.enabled(this)) {
      page.addView(ui.spacer(12));
      page.addView(ui.button("Заблокировать сейчас", false, () -> { AppLock.lock(); ((HubApplication) getApplication()).openGate(this); }));
    }
    page.addView(ui.spacer(20)); page.addView(ui.button("Назад", NativeUi.Style.QUIET, this::finish));
    ScrollView scroll = new ScrollView(this); scroll.setFillViewport(true); scroll.addView(page); setContentView(scroll);
  }
}
