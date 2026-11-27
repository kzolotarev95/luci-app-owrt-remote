package ru.owrthub.app;

import android.app.*;
import android.content.*;
import android.hardware.biometrics.BiometricPrompt;
import android.os.*;
import android.view.*;
import android.widget.*;

/** Success is accepted only from Android's credential or biometric callback. */
public final class NativeLockActivity extends Activity {
  private static final int CREDENTIAL = 731;
  private NativeUi ui;
  private TextView status;
  private boolean credentialPending, authenticating, visible;
  private CancellationSignal cancellation;
  private String operation;
  @Override public void onCreate(Bundle state) {
    super.onCreate(state);
    operation = getIntent().getStringExtra("operation");
    if (!"enable".equals(operation) && !"disable".equals(operation)) operation = "unlock";
    ui = new NativeUi(this, getSharedPreferences("native_design", 0).getBoolean("winter", false));
    getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
    LinearLayout page = ui.column(); page.setGravity(Gravity.CENTER);
    page.setBackgroundColor(ui.BG); page.setPadding(ui.dp(24), ui.dp(24), ui.dp(24), ui.dp(24));
    page.addView(ui.logoBadge(72)); page.addView(ui.spacer(20));
    page.addView(ui.text("Защищённый вход в OWRT Hub", 22, true)); page.addView(ui.spacer(12));
    status = ui.body("Подтвердите вход с помощью защиты телефона."); page.addView(status); page.addView(ui.spacer(20));
    page.addView(ui.button("Разблокировать", true, this::authenticate)); page.addView(ui.spacer(10));
    page.addView(ui.button("PIN, графический ключ или пароль", false, this::credential));
    if (!AppLock.deviceSecure(this)) {
      status.setText("Сначала настройте блокировку экрана телефона, затем вернитесь в приложение.");
      page.addView(ui.spacer(10));
      page.addView(ui.button("Настроить блокировку телефона", false,
          () -> startActivity(new Intent(android.provider.Settings.ACTION_SECURITY_SETTINGS))));
    }
    page.addView(ui.spacer(12)); page.addView(ui.button("Закрыть приложение", NativeUi.Style.QUIET, this::leave));
    setContentView(page);
    // Pending credential state survives an Activity recreation; no duplicate prompt.
    credentialPending = state != null && state.getBoolean("credentialPending");
    if (state == null) page.post(this::authenticate);
  }
  private void authenticate() {
    if (authenticating || credentialPending || isFinishing()) return;
    if (!operation.equals("unlock") || !AppLock.biometric(this) || !AppLock.hasBiometric(this)) { credential(); return; }
    authenticating = true;
    cancellation = new CancellationSignal();
    if (Build.VERSION.SDK_INT < 28) {
      status.setText("Приложите палец к датчику отпечатка или используйте пароль телефона.");
      try {
        getSystemService(android.hardware.fingerprint.FingerprintManager.class).authenticate(null, cancellation, 0,
            new android.hardware.fingerprint.FingerprintManager.AuthenticationCallback() {
              @Override public void onAuthenticationSucceeded(android.hardware.fingerprint.FingerprintManager.AuthenticationResult result) { complete(false); }
              @Override public void onAuthenticationError(int code, CharSequence message) { authenticating = false; status.setText("Отпечаток не подтверждён. Повторите проверку или используйте пароль телефона."); }
              @Override public void onAuthenticationFailed() { status.setText("Отпечаток не распознан. Попробуйте ещё раз."); }
            }, new Handler(Looper.getMainLooper()));
      } catch (RuntimeException unavailable) { authenticating = false; credential(); }
      return;
    }
    BiometricPrompt.Builder builder = new BiometricPrompt.Builder(this).setTitle("Вход в OWRT Hub")
        .setSubtitle("Подтвердите личность для открытия приложения");
    if (Build.VERSION.SDK_INT >= 30) builder.setAllowedAuthenticators(
        android.hardware.biometrics.BiometricManager.Authenticators.BIOMETRIC_STRONG);
    builder.setNegativeButton("PIN, ключ или пароль", getMainExecutor(), (dialog, which) -> { authenticating = false; credential(); });
    try {
      builder.build().authenticate(cancellation, getMainExecutor(), new BiometricPrompt.AuthenticationCallback() {
        @Override public void onAuthenticationSucceeded(BiometricPrompt.AuthenticationResult result) { complete(false); }
        @Override public void onAuthenticationError(int code, CharSequence message) {
          authenticating = false;
          if (!isFinishing()) status.setText("Вход не подтверждён. Повторите проверку или используйте пароль телефона.");
        }
        @Override public void onAuthenticationFailed() { status.setText("Биометрия не распознана. Попробуйте ещё раз."); }
      });
    } catch (RuntimeException unavailable) { authenticating = false; credential(); }
  }
  private void credential() {
    if (credentialPending || isFinishing()) return;
    if (cancellation != null) cancellation.cancel();
    authenticating = false;
    KeyguardManager keyguard = getSystemService(KeyguardManager.class);
    Intent confirm = keyguard == null ? null : keyguard.createConfirmDeviceCredentialIntent("OWRT Hub", "Подтвердите вход в приложение");
    if (confirm == null) { status.setText("На телефоне не настроен PIN, графический ключ или пароль."); return; }
    credentialPending = true;
    try { startActivityForResult(confirm, CREDENTIAL); }
    catch (RuntimeException unavailable) { credentialPending = false; status.setText("Не удалось открыть проверку Android. Повторите попытку."); }
  }
  @Override protected void onActivityResult(int request, int result, Intent data) {
    super.onActivityResult(request, result, data);
    if (request != CREDENTIAL || !credentialPending) return;
    credentialPending = false;
    if (result == RESULT_OK) complete(true);
    else status.setText(operation.equals("enable") ? "Подтверждение отменено. Защита не включена."
        : operation.equals("disable") ? "Подтверждение отменено. Защита остаётся включённой."
        : "Вход не подтверждён. Приложение остаётся заблокированным.");
  }
  private void complete(boolean credentialResult) {
    if (isFinishing()) return;
    if (!credentialResult && !visible) { authenticating = false; return; }
    authenticating = false;
    if (!operation.equals("unlock") && !AppLock.configure(this, operation.equals("enable"))) {
      status.setText("Не удалось сохранить настройку защиты."); return;
    }
    AppLock.unlock(); setResult(RESULT_OK); finish();
  }
  private void leave() {
    if (!operation.equals("unlock")) { setResult(RESULT_CANCELED); finish(); }
    else { AppLock.lock(); moveTaskToBack(true); }
  }
  @Override public void onBackPressed() { leave(); }
  @Override protected void onStart() { super.onStart(); visible = true; }
  @Override protected void onStop() {
    visible = false;
    if (!credentialPending && cancellation != null) { cancellation.cancel(); authenticating = false; }
    super.onStop();
  }
  @Override protected void onSaveInstanceState(Bundle state) { state.putBoolean("credentialPending", credentialPending); super.onSaveInstanceState(state); }
  @Override protected void onDestroy() { if (cancellation != null) cancellation.cancel(); super.onDestroy(); }
}
