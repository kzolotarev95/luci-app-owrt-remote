package ru.owrthub.app;

import android.app.*;
import android.content.*;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;
import java.util.concurrent.*;
import org.json.*;

/** One sequential long-poll; no passwords, gateways or public inbound ports. */
public final class HubNotificationService extends Service {
  private final ExecutorService worker = Executors.newSingleThreadExecutor();
  private volatile boolean active;
  private static final String STATUS = "hub_connection", EVENTS = "hub_events";
  private String lastStatus = "";

  @Override
  public IBinder onBind(Intent i) {
    return null;
  }

  @Override
  public void onCreate() {
    super.onCreate();
    NotificationManager manager = getSystemService(NotificationManager.class);
    manager.createNotificationChannel(
        new NotificationChannel(STATUS, "Подключение к Hub", NotificationManager.IMPORTANCE_LOW));
    manager.createNotificationChannel(
        new NotificationChannel(EVENTS, "События Hub", NotificationManager.IMPORTANCE_DEFAULT));
  }

  @Override
  public int onStartCommand(Intent intent, int flags, int id) {
    if (intent != null && "stop".equals(intent.getAction())) {
      HubNotifications.disable(this);
      return START_NOT_STICKY;
    }
    if (!HubNotifications.prefs(this).getBoolean("enabled", false)
        || !HubNotifications.allowed(this)) {
      stopSelf();
      return START_NOT_STICKY;
    }
    if (active) return START_STICKY;
    Notification status = status("Подключение…");
    if (Build.VERSION.SDK_INT >= 34)
      startForeground(4001, status, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
    else startForeground(4001, status);
    if (!active) {
      active = true;
      worker.execute(this::listen);
    }
    return START_STICKY;
  }

  private PendingIntent open() {
    return PendingIntent.getActivity(
        this,
        0,
        new Intent(this, MainActivity.class)
            .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP),
        PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
  }

  private Notification status(String text) {
    PendingIntent stop =
        PendingIntent.getService(
            this,
            1,
            new Intent(this, HubNotificationService.class).setAction("stop"),
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    return new Notification.Builder(this, STATUS)
        .setSmallIcon(R.drawable.ic_notification)
        .setContentTitle("Уведомления OWRT Hub")
        .setContentText(text)
        .setContentIntent(open())
        .setOngoing(true)
        .setOnlyAlertOnce(true)
        .setVisibility(Notification.VISIBILITY_PRIVATE)
        .addAction(new Notification.Action.Builder(null, "Выключить", stop).build())
        .build();
  }

  private void state(String text) {
    if (!active || text.equals(lastStatus)) return;
    lastStatus = text;
    HubNotifications.prefs(this).edit().putString("status", text).apply();
    if (HubNotifications.allowed(this))
      getSystemService(NotificationManager.class).notify(4001, status(text));
  }

  private void listen() {
    long serial = 0;
    String context = "", cursorKey = "";
    int failures = 0;
    while (active) {
      long begin = System.currentTimeMillis();
      try {
        HubSessionStore store = new HubSessionStore(this);
        HubSessionStore.SavedSession saved = store.load();
        if (saved == null || saved.cookie.isEmpty()) {
          state("Нужен вход в Hub");
          pause(10000);
          continue;
        }
        HubApiClient api = new HubApiClient(saved.hub, store);
        if (!context.equals(saved.cookie)) {
          if (!api.session().getJSONObject("account").optBoolean("is_owner")) {
            state("События доступны владельцу Hub");
            pause(30000);
            continue;
          }
          byte[] digest =
              java.security.MessageDigest.getInstance("SHA-256")
                  .digest(
                      (saved.origin + "\n" + saved.username)
                          .getBytes(java.nio.charset.StandardCharsets.UTF_8));
          cursorKey =
              "cursor:" + android.util.Base64.encodeToString(digest, android.util.Base64.NO_WRAP);
          serial =
              HubNotifications.prefs(this).contains(cursorKey)
                  ? HubNotifications.prefs(this).getLong(cursorKey, 0)
                  : api.get("/api/notifications?limit=1").optLong("serial", 0);
          context = saved.cookie;
        }
        JSONObject result =
            api.get(
                "/api/notifications/wait?timeout=25&limit=60&after_serial="
                    + serial
                    + "&client_id=android-background");
        HubSessionStore.SavedSession current = store.load();
        if (!active || current == null || !current.cookie.equals(context)) {
          context = "";
          continue;
        }
        JSONArray items = result.optJSONArray("notifications");
        if (items != null)
          for (int n = 0; n < items.length(); n++) {
            JSONObject item = items.optJSONObject(n);
            if (item == null) continue;
            long event = item.optLong("serial", 0);
            if (event <= serial) continue;
            // Hub returns newest first; compare every event to the original cursor.
            if (HubNotifications.allowed(this))
              getSystemService(NotificationManager.class)
                  .notify(
                      5000 + (int) (event % 10000),
                      new Notification.Builder(this, EVENTS)
                          .setSmallIcon(R.drawable.ic_notification)
                          .setContentTitle(item.optString("title", "Событие Hub"))
                          .setContentText(item.optString("body", ""))
                          .setStyle(
                              new Notification.BigTextStyle().bigText(item.optString("body", "")))
                          .setContentIntent(open())
                          .setAutoCancel(true)
                          .setVisibility(Notification.VISIBILITY_PRIVATE)
                          .build());
          }
        serial = result.optLong("serial", serial);
        HubNotifications.prefs(this).edit().putLong(cursorKey, serial).apply();
        failures = 0;
        state("Подключено · ожидание событий");
        pause(Math.max(1000, 1500 - (System.currentTimeMillis() - begin)));
      } catch (Exception failure) {
        state("Нет связи · повторное подключение");
        failures = Math.min(6, failures + 1);
        pause(Math.min(60000, 1000L << failures));
      }
    }
  }

  private void pause(long ms) {
    try {
      Thread.sleep(ms);
    } catch (InterruptedException ignored) {
      Thread.currentThread().interrupt();
    }
  }

  @Override
  public void onDestroy() {
    active = false;
    worker.shutdownNow();
    super.onDestroy();
  }
}
