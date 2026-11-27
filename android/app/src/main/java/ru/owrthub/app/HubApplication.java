package ru.owrthub.app;

import android.app.*;
import android.content.Intent;
import android.os.Bundle;
import android.view.*;
import android.widget.*;
import java.util.IdentityHashMap;

/** Covers every private Activity before a frame is presented, including tools. */
public final class HubApplication extends Application implements Application.ActivityLifecycleCallbacks {
  private final IdentityHashMap<Activity, Shield> shields = new IdentityHashMap<>();
  private final IdentityHashMap<Activity, Boolean> secureBefore = new IdentityHashMap<>();
  private int started;
  private NativeLockActivity gate;
  private boolean opening;
  private static final class Shield {
    View cover, content; int accessibility;
  }
  @Override public void onCreate() { super.onCreate(); registerActivityLifecycleCallbacks(this); }
  private void cover(Activity activity) {
    if (shields.containsKey(activity)) return;
    NativeUi ui = new NativeUi(activity, getSharedPreferences("native_design", 0).getBoolean("winter", false));
    FrameLayout overlay = new FrameLayout(activity);
    overlay.setBackgroundColor(ui.BG); overlay.setClickable(true); overlay.setFocusable(true);
    overlay.setTag("app-lock-shield");
    LinearLayout message = ui.column(); message.setGravity(Gravity.CENTER);
    message.setPadding(ui.dp(24), ui.dp(24), ui.dp(24), ui.dp(24));
    message.addView(ui.logoBadge(64)); message.addView(ui.spacer(18));
    message.addView(ui.text("Приложение заблокировано", 20, true)); message.addView(ui.spacer(18));
    message.addView(ui.button("Разблокировать", true, () -> openGate(activity)));
    overlay.addView(message, new FrameLayout.LayoutParams(-1, -2, Gravity.CENTER));
    Shield shield = new Shield(); shield.cover = overlay;
    shield.content = activity.findViewById(android.R.id.content);
    shield.accessibility = shield.content.getImportantForAccessibility();
    shield.content.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS);
    shields.put(activity, shield);
    ((android.view.ViewGroup) activity.getWindow().getDecorView()).addView(overlay, new android.view.ViewGroup.LayoutParams(-1, -1));
  }
  private void uncover(Activity activity) {
    Shield shield = shields.remove(activity);
    if (shield != null) {
      shield.content.setImportantForAccessibility(shield.accessibility);
      if (shield.cover.getParent() instanceof ViewGroup) ((ViewGroup) shield.cover.getParent()).removeView(shield.cover);
    }
  }
  private void secure(Activity activity) {
    if (AppLock.enabled(this)) {
      if (!secureBefore.containsKey(activity)) secureBefore.put(activity,
          (activity.getWindow().getAttributes().flags & WindowManager.LayoutParams.FLAG_SECURE) != 0);
      activity.getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
    } else if (secureBefore.containsKey(activity)) {
      if (!secureBefore.remove(activity)) activity.getWindow().clearFlags(WindowManager.LayoutParams.FLAG_SECURE);
    }
  }
  void openGate(Activity activity) {
    if (opening || activity.isFinishing()) return;
    opening = true;
    try { activity.startActivity(new Intent(activity, NativeLockActivity.class).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)); }
    catch (RuntimeException unavailable) { opening = false; }
  }
  @Override public void onActivityCreated(Activity activity, Bundle state) {
    if (activity instanceof NativeLockActivity) { gate = (NativeLockActivity) activity; return; }
    secure(activity);
    if (AppLock.needsUnlock(this)) cover(activity);
  }
  @Override public void onActivityStarted(Activity activity) { started++; }
  @Override public void onActivityResumed(Activity activity) {
    if (activity instanceof NativeLockActivity) { opening = false; return; }
    secure(activity);
    if (AppLock.needsUnlock(this)) { cover(activity); openGate(activity); }
    else uncover(activity);
  }
  @Override public void onActivityPaused(Activity activity) {
    if (!(activity instanceof NativeLockActivity) && AppLock.enabled(this)) cover(activity);
  }
  @Override public void onActivityStopped(Activity activity) {
    started = Math.max(0, started - 1);
    if (started == 0 && !activity.isChangingConfigurations()) AppLock.lock();
  }
  @Override public void onActivityDestroyed(Activity activity) {
    shields.remove(activity); secureBefore.remove(activity);
    if (activity == gate) { gate = null; opening = false; }
  }
  @Override public void onActivitySaveInstanceState(Activity activity, Bundle state) {}
}
