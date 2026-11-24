package ru.owrthub.app;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.NotificationManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Build;
import android.provider.Settings;

/** Explicit opt-in for a user-visible connection to the self-hosted Hub. */
final class HubNotifications {
  static final int PERMISSION = 8304;

  static SharedPreferences prefs(Context c) {
    return c.getSharedPreferences("native_alerts", 0);
  }

  static boolean allowed(Context c) {
    return (Build.VERSION.SDK_INT < 33
            || c.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                == PackageManager.PERMISSION_GRANTED)
        && c.getSystemService(NotificationManager.class).areNotificationsEnabled();
  }

  static void offer(Activity a, boolean owner) {
    if (!owner) return;
    if (prefs(a).getBoolean("enabled", false)) {
      start(a);
      return;
    }
    if (prefs(a).getBoolean("offered", false)) return;
    prefs(a).edit().putBoolean("offered", true).apply();
    new AlertDialog.Builder(a)
        .setTitle("Уведомления OWRT Hub")
        .setMessage(
            "Получать события Hub, когда приложение свёрнуто? Android будет показывать постоянное"
                + " уведомление о подключении. Включить или выключить можно в разделе"
                + " «Уведомления».")
        .setPositiveButton("Включить", (d, w) -> enable(a))
        .setNegativeButton("Позже", null)
        .show();
  }

  static void enable(Activity a) {
    if (Build.VERSION.SDK_INT >= 33
        && a.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED) {
      a.requestPermissions(new String[] {Manifest.permission.POST_NOTIFICATIONS}, PERMISSION);
      return;
    }
    if (!allowed(a)) {
      a.startActivity(
          new Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
              .putExtra(Settings.EXTRA_APP_PACKAGE, a.getPackageName()));
      return;
    }
    prefs(a).edit().putBoolean("enabled", true).apply();
    start(a);
  }

  static void permissionResult(Activity a, int request, int[] result) {
    if (request == PERMISSION
        && result.length > 0
        && result[0] == PackageManager.PERMISSION_GRANTED) enable(a);
  }

  static void start(Context c) {
    if (!prefs(c).getBoolean("enabled", false) || !allowed(c)) return;
    try {
      c.startForegroundService(new Intent(c, HubNotificationService.class));
    } catch (RuntimeException unavailable) {
      prefs(c)
          .edit()
          .putString("status", "Откройте приложение для подключения уведомлений")
          .apply();
    }
  }

  static void disable(Context c) {
    prefs(c).edit().putBoolean("enabled", false).putString("status", "Выключены").apply();
    c.stopService(new Intent(c, HubNotificationService.class));
  }
}
