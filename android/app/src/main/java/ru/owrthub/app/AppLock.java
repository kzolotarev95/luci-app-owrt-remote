package ru.owrthub.app;

import android.app.KeyguardManager;
import android.content.Context;
import android.os.Build;

/** Process-local unlock; the authenticator is owned by Android, never by Hub. */
final class AppLock {
  private static volatile boolean unlocked;
  static boolean enabled(Context context) { return context.getSharedPreferences("app_lock", 0).getBoolean("enabled", false); }
  static boolean needsUnlock(Context context) { return enabled(context) && !unlocked; }
  static boolean biometric(Context context) { return context.getSharedPreferences("app_lock", 0).getBoolean("biometric", true); }
  static void setBiometric(Context context, boolean value) { context.getSharedPreferences("app_lock", 0).edit().putBoolean("biometric", value).apply(); }
  static boolean deviceSecure(Context context) {
    KeyguardManager manager = context.getSystemService(KeyguardManager.class);
    return manager != null && manager.isDeviceSecure();
  }
  static boolean hasBiometric(Context context) {
    if (Build.VERSION.SDK_INT >= 29) {
      android.hardware.biometrics.BiometricManager manager = context.getSystemService(android.hardware.biometrics.BiometricManager.class);
      return manager != null && (Build.VERSION.SDK_INT >= 30
          ? manager.canAuthenticate(android.hardware.biometrics.BiometricManager.Authenticators.BIOMETRIC_STRONG)
          : manager.canAuthenticate()) == android.hardware.biometrics.BiometricManager.BIOMETRIC_SUCCESS;
    }
    android.hardware.fingerprint.FingerprintManager manager = context.getSystemService(android.hardware.fingerprint.FingerprintManager.class);
    return manager != null && manager.isHardwareDetected() && manager.hasEnrolledFingerprints();
  }
  static void unlock() { unlocked = true; }
  static void lock() { unlocked = false; }
  static boolean configure(Context context, boolean enabled) {
    boolean stored = context.getSharedPreferences("app_lock", 0).edit().putBoolean("enabled", enabled).commit();
    if (stored) unlocked = true;
    return stored;
  }
}
