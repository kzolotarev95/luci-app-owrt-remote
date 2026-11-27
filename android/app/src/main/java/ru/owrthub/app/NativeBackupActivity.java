package ru.owrthub.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.res.ColorStateList;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Base64;
import android.util.Base64OutputStream;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.FilterOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.zip.GZIPInputStream;
import javax.net.ssl.HttpsURLConnection;
import org.json.JSONObject;

/** Owner-only native Hub backup through Android's document picker. */
public final class NativeBackupActivity extends Activity {
  public static final String EXTRA_HUB_ORIGIN = "hub_origin";
  private static final int EXPORT = 401, IMPORT = 402;
  private static final long MAX_ARCHIVE = 60L * 1024 * 1024;
  private final ExecutorService network = Executors.newSingleThreadExecutor();
  private final Handler handler = new Handler(Looper.getMainLooper());
  private NativeUi ui;
  private HubApiClient api;
  private Button exportButton, importButton;
  private EditText vpsHost, publicUrl;
  private TextView status;
  private ProgressBar progress;
  private boolean busy, ownerReady, restored, restoreUnconfirmed;
  private String pendingVps = "", pendingPublic = "";
  private String transferCookie = "";

  @Override
  public void onCreate(Bundle state) {
    super.onCreate(state);
    ui =
        new NativeUi(
            this, getSharedPreferences("native_design", MODE_PRIVATE).getBoolean("winter", false));
    getWindow().setStatusBarColor(ui.BG);
    getWindow().setNavigationBarColor(ui.BG);
    try {
      api =
          new HubApiClient(
              HubUrlPolicy.normalizeHub(
                  getIntent().getStringExtra(EXTRA_HUB_ORIGIN), "", BuildConfig.DEBUG),
              new HubSessionStore(this));
      if (state != null) {
        pendingVps = state.getString("pending_vps", "");
        pendingPublic = state.getString("pending_public", "");
      }
      clearAbandonedArchives();
      build();
      verifyOwner();
    } catch (Exception invalid) {
      LinearLayout page = ui.column();
      page.setPadding(ui.dp(20), ui.dp(40), ui.dp(20), ui.dp(20));
      page.setBackgroundColor(ui.BG);
      page.addView(ui.title("Резервные копии недоступны"));
      page.addView(ui.spacer(20));
      page.addView(ui.body("Откройте раздел из подключённого Hub."));
      page.addView(ui.button("Назад", false, this::finish));
      setContentView(page);
      api = null;
    }
  }

  private void build() {
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
    bar.setTag("native-toolbar");
    bar.addView(ui.text("Резервные копии", 20, true), new LinearLayout.LayoutParams(0, -2, 1));
    root.addView(bar);
    bar.addView(
        ui.iconButton(
            "refresh",
            "Повторить проверку доступа",
            () -> {
              if (!busy) verifyOwner();
            }));
    progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
    progress.setIndeterminate(true);
    progress.setIndeterminateTintList(ColorStateList.valueOf(ui.BLUE));
    root.addView(progress, new LinearLayout.LayoutParams(-1, ui.dp(2)));
    ScrollView scroll = new ScrollView(this);
    LinearLayout content = ui.column();
    content.setPadding(ui.dp(14), ui.dp(14), ui.dp(14), ui.dp(24));
    scroll.addView(content);
    root.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
    content.addView(ui.title("Состояние вашего Hub"));
    content.addView(ui.spacer(16));
    content.addView(
        ui.body(
            "Архив содержит настройки Hub и роутеров, данные доступа и историю. Сохраните его в"
                + " выбранном вами месте. Доступен владельцу Hub."));
    content.addView(ui.spacer(24));
    exportButton =
        ui.button(
            "Сохранить резервную копию",
            true,
            () -> {
              Intent picker =
                  new Intent(Intent.ACTION_CREATE_DOCUMENT)
                      .addCategory(Intent.CATEGORY_OPENABLE)
                      .setType("application/gzip");
              picker.putExtra(
                  Intent.EXTRA_TITLE,
                  "owrt-hub-backup-"
                      + new SimpleDateFormat("yyyyMMdd-HHmmss", Locale.ROOT).format(new Date())
                      + ".tar.gz");
              launch(picker, EXPORT);
            });
    content.addView(exportButton);
    content.addView(ui.divider());
    content.addView(ui.text("Восстановление", 21, true));
    content.addView(ui.spacer(12));
    content.addView(
        ui.body(
            "Восстановление заменяет состояние Hub содержимым архива. Поддерживаются архивы .tar.gz"
                + " до 60 МиБ. Для переноса на другой VPS можно указать новые адреса."));
    content.addView(ui.spacer(16));
    vpsHost = ui.field("Оставьте пустым без переноса", "", false);
    vpsHost.setSaveEnabled(false);
    publicUrl = ui.field("https://hub.example", "", false);
    publicUrl.setSaveEnabled(false);
    vpsHost.setText(pendingVps);
    publicUrl.setText(pendingPublic);
    content.addView(ui.labeledField("Новый адрес VPS (необязательно)", vpsHost));
    content.addView(ui.spacer(14));
    content.addView(ui.labeledField("Новый HTTPS-адрес Hub (необязательно)", publicUrl));
    content.addView(ui.spacer(16));
    importButton =
        ui.button(
            "Выбрать архив для восстановления",
            NativeUi.Style.DANGER,
            () -> {
              try {
                pendingVps = vpsHost.getText().toString().trim();
                pendingPublic = publicUrl.getText().toString().trim();
                if (!pendingVps.isEmpty()) {
                  URI parsed = new URI("https://" + pendingVps);
                  if (parsed.getHost() == null
                      || parsed.getPort() != -1
                      || parsed.getRawUserInfo() != null
                      || !parsed.getRawPath().isEmpty()
                      || parsed.getRawQuery() != null
                      || parsed.getRawFragment() != null)
                    throw new IllegalArgumentException("VPS host required");
                }
                if (!pendingPublic.isEmpty())
                  pendingPublic =
                      HubUrlPolicy.normalizeHub(pendingPublic, "", BuildConfig.DEBUG).toString();
              } catch (Exception invalid) {
                show("Проверьте адрес VPS и HTTPS-адрес Hub.", true);
                return;
              }
              Intent picker =
                  new Intent(Intent.ACTION_OPEN_DOCUMENT)
                      .addCategory(Intent.CATEGORY_OPENABLE)
                      .setType("*/*");
              picker.putExtra(
                  Intent.EXTRA_MIME_TYPES,
                  new String[] {
                    "application/gzip",
                    "application/x-gzip",
                    "application/octet-stream",
                    "application/x-compressed-tar"
                  });
              launch(picker, IMPORT);
            });
    content.addView(importButton);
    content.addView(ui.spacer(20));
    status = ui.text("Проверка доступа…", 15, false);
    status.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
    content.addView(status);
    NativeEffects.install(this, root);
    root.requestApplyInsets();
    controls();
  }

  private void launch(Intent picker, int request) {
    try {
      startActivityForResult(picker, request);
    } catch (RuntimeException unavailable) {
      show("Не удалось открыть системный выбор документов.", true);
    }
  }

  @Override
  protected void onSaveInstanceState(Bundle state) {
    state.putString("pending_vps", pendingVps);
    state.putString("pending_public", pendingPublic);
    super.onSaveInstanceState(state);
  }

  @Override
  protected void onDestroy() {
    network.shutdown();
    super.onDestroy();
  }

  @Override
  public void onBackPressed() {
    if (busy) {
      android.widget.Toast.makeText(
              this, "Дождитесь результата файловой операции.", android.widget.Toast.LENGTH_SHORT)
          .show();
      return;
    }
    super.onBackPressed();
  }

  private void clearAbandonedArchives() {
    File[] files = getCacheDir().listFiles();
    if (files == null) return;
    long cutoff = System.currentTimeMillis() - 3600000;
    for (File file : files)
      if (file.isFile()
          && file.lastModified() < cutoff
          && file.getName().matches("hub-(backup|restore)-[A-Za-z0-9_-]+\\.tar\\.gz"))
        file.delete();
  }

  @Override
  protected void onActivityResult(int requestCode, int resultCode, Intent result) {
    super.onActivityResult(requestCode, resultCode, result);
    if (resultCode != RESULT_OK
        || result == null
        || result.getData() == null
        || busy
        || api == null) return;
    Uri uri = result.getData();
    if (!"content".equals(uri.getScheme())) {
      show("Выберите документ через системный проводник.", true);
      return;
    }
    if (requestCode == EXPORT)
      execute(
          () -> {
            assertOwner();
            exportArchive(uri);
            return "Резервная копия сохранена в выбранный документ.";
          },
          false);
    else if (requestCode == IMPORT)
      new AlertDialog.Builder(this)
          .setTitle("Восстановить Hub из архива?")
          .setMessage(
              "Текущее состояние будет заменено. Используйте архив вашего Hub. После восстановления"
                  + " может потребоваться новый вход и перезапуск Hub на VPS.")
          .setNegativeButton("Отмена", null)
          .setPositiveButton(
              "Восстановить",
              (dialog, which) -> {
                final String host = pendingVps, origin = pendingPublic;
                execute(
                    () -> {
                      assertOwner();
                      return importArchive(uri, host, origin);
                    },
                    true);
              })
          .show();
  }

  private void assertOwner() throws Exception {
    JSONObject session = api.session();
    JSONObject account = session.optJSONObject("account");
    if (account == null || !account.optBoolean("is_owner"))
      throw new TransferFailure("Раздел доступен только владельцу Hub.");
  }

  private void verifyOwner() {
    execute(
        () -> {
          assertOwner();
          return "Выберите сохранение копии или восстановление из архива.";
        },
        false);
  }

  private void execute(Callable<String> call, boolean restore) {
    if (busy || api == null || network.isShutdown()) return;
    busy = true;
    controls();
    show(restore ? "Восстановление Hub…" : "Подготовка резервной копии…", false);
    network.execute(
        () -> {
          String message = "";
          boolean failed = false;
          try {
            message = call.call();
          } catch (Exception problem) {
            failed = true;
            if (restore
                && !(problem instanceof HubApiClient.ApiException)
                && !(problem instanceof TransferFailure)) restoreUnconfirmed = true;
            message =
                problem instanceof HubApiClient.AuthRequiredException
                    ? "Сессия завершилась. Вернитесь и войдите в Hub заново."
                    : problem instanceof HubApiClient.PermissionException
                            || problem instanceof TransferFailure
                        ? problem.getMessage()
                        : restore
                            ? "Результат восстановления не подтверждён. Проверьте состояние Hub"
                                + " перед повторной попыткой."
                            : "Не удалось выполнить операцию. Проверьте сеть, доступ к документу и"
                                + " сертификат Hub.";
          }
          final String text = message;
          final boolean error = failed;
          handler.post(
              () -> {
                busy = false;
                if (isFinishing() || isDestroyed()) return;
                if (!error) {
                  ownerReady = true;
                  restoreUnconfirmed = false;
                  if (restore) restored = true;
                }
                show(text, error);
                controls();
              });
        });
  }

  private void controls() {
    if (progress == null) return;
    progress.setVisibility(busy ? View.VISIBLE : View.INVISIBLE);
    exportButton.setEnabled(!busy && ownerReady && !restored);
    importButton.setEnabled(!busy && ownerReady && !restored && !restoreUnconfirmed);
    vpsHost.setEnabled(!busy && !restored);
    publicUrl.setEnabled(!busy && !restored);
  }

  private void show(String message, boolean error) {
    if (status != null) {
      status.setText(message);
      status.setTextColor(error ? ui.RED : ui.MUTED);
    }
  }

  private HttpURLConnection connection(String path, String method) throws Exception {
    api.synchronizeSession();
    if (!path.equals("/api/backup/download") && !path.equals("/api/backup/restore"))
      throw new IOException("Unexpected backup path");
    URI target = api.getHub().resolve(path);
    if (!target.getScheme().equals(api.getHub().getScheme())
        || !target.getRawAuthority().equals(api.getHub().getRawAuthority()))
      throw new IOException("Backup origin mismatch");
    HttpURLConnection conn = (HttpURLConnection) target.toURL().openConnection();
    if (api.getHub().getScheme().equals("https") && !(conn instanceof HttpsURLConnection)) {
      conn.disconnect();
      throw new IOException("HTTPS required");
    }
    String cookie = api.getCookieHeader();
    transferCookie = cookie;
    if (cookie.isEmpty()) {
      conn.disconnect();
      throw new TransferFailure("Сессия завершилась. Войдите в Hub заново.");
    }
    conn.setInstanceFollowRedirects(false);
    conn.setConnectTimeout(15000);
    conn.setReadTimeout(120000);
    conn.setUseCaches(false);
    conn.setRequestMethod(method);
    conn.setRequestProperty("Cookie", cookie);
    conn.setRequestProperty("User-Agent", "OWRT-Hub-Android/1 native-backup");
    conn.setRequestProperty("Cache-Control", "no-store");
    conn.setRequestProperty("Accept-Encoding", "identity");
    conn.setRequestProperty("Origin", api.getHub().toString());
    return conn;
  }

  private void checkStatus(HttpURLConnection conn) throws Exception {
    int status = conn.getResponseCode();
    if (status == 401) {
      // Revalidate through the shared client so a late export cannot erase a newer login.
      try {
        api.session();
      } catch (IOException unavailable) {
      }
      throw new TransferFailure("Сессия завершилась. Войдите в Hub заново.");
    }
    if (status == 403)
      throw new TransferFailure("Восстановление и экспорт доступны только владельцу Hub.");
    if (status >= 300 && status < 400)
      throw new TransferFailure("Hub перенаправил запрос. Проверьте HTTPS-адрес подключения.");
    if (status != 200) throw new TransferFailure("Hub отклонил операцию (HTTP " + status + ").");
  }

  private long copy(InputStream input, OutputStream output, long maximum) throws Exception {
    byte[] buffer = new byte[32768];
    long total = 0;
    int count;
    while ((count = input.read(buffer)) != -1) {
      total += count;
      if (total > maximum) throw new TransferFailure("Архив превышает лимит 60 МиБ.");
      output.write(buffer, 0, count);
    }
    return total;
  }

  private void requireGzip(File file) throws Exception {
    try (InputStream input = new GZIPInputStream(new FileInputStream(file))) {
      byte[] buffer = new byte[32768];
      long unpacked = 0;
      int count;
      while ((count = input.read(buffer)) != -1) {
        unpacked += count;
        if (unpacked > 512L * 1024 * 1024)
          throw new TransferFailure("Распакованный архив превышает лимит 512 МиБ.");
      }
    } catch (java.util.zip.ZipException | java.io.EOFException corrupt) {
      throw new TransferFailure(
          "Архив gzip повреждён или загружен не полностью. Выберите исправный .tar.gz.");
    }
  }

  private void exportArchive(Uri destination) throws Exception {
    File temporary = File.createTempFile("hub-backup-", ".tar.gz", getCacheDir());
    HttpURLConnection conn = null;
    try {
      conn = connection("/api/backup/download", "GET");
      conn.setRequestProperty("Accept", "application/gzip");
      checkStatus(conn);
      if (conn.getContentLengthLong() > MAX_ARCHIVE)
        throw new TransferFailure("Архив превышает лимит 60 МиБ.");
      try (InputStream input = conn.getInputStream();
          OutputStream output = new FileOutputStream(temporary)) {
        copy(input, output, MAX_ARCHIVE);
      }
      requireGzip(temporary);
      api.synchronizeSession();
      if (!api.getCookieHeader().equals(transferCookie))
        throw new TransferFailure("Аккаунт изменился во время загрузки. Копия не сохранена.");
      try (InputStream input = new FileInputStream(temporary);
          OutputStream output = getContentResolver().openOutputStream(destination, "wt")) {
        if (output == null) throw new IOException("Document unavailable");
        copy(input, output, MAX_ARCHIVE);
      }
    } finally {
      if (conn != null) conn.disconnect();
      temporary.delete();
    }
  }

  private String importArchive(Uri source, String host, String origin) throws Exception {
    File temporary = File.createTempFile("hub-restore-", ".tar.gz", getCacheDir());
    HttpURLConnection conn = null;
    try {
      try (InputStream input = getContentResolver().openInputStream(source);
          OutputStream output = new FileOutputStream(temporary)) {
        if (input == null) throw new IOException("Document unavailable");
        copy(input, output, MAX_ARCHIVE);
      }
      requireGzip(temporary);
      byte[] prefix = "{\"archive_b64\":\"".getBytes(StandardCharsets.UTF_8);
      byte[] suffix =
          ("\",\"vps_host\":"
                  + JSONObject.quote(host)
                  + ",\"public_url\":"
                  + JSONObject.quote(origin)
                  + "}")
              .getBytes(StandardCharsets.UTF_8);
      long encodedLength = ((temporary.length() + 2) / 3) * 4;
      conn = connection("/api/backup/restore", "POST");
      conn.setRequestProperty("Accept", "application/json");
      conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
      conn.setDoOutput(true);
      conn.setFixedLengthStreamingMode(prefix.length + encodedLength + suffix.length);
      try (OutputStream output = conn.getOutputStream()) {
        output.write(prefix);
        OutputStream nonClosing =
            new FilterOutputStream(output) {
              @Override
              public void write(byte[] bytes, int offset, int length) throws IOException {
                out.write(bytes, offset, length);
              }

              @Override
              public void close() throws IOException {
                flush();
              }
            };
        try (Base64OutputStream encoder = new Base64OutputStream(nonClosing, Base64.NO_WRAP);
            InputStream input = new FileInputStream(temporary)) {
          copy(input, encoder, MAX_ARCHIVE);
        }
        output.write(suffix);
      }
      checkStatus(conn);
      JSONObject result;
      try (InputStream input = conn.getInputStream();
          ByteArrayOutputStream bytes = new ByteArrayOutputStream()) {
        copy(input, bytes, 1024 * 1024);
        result = new JSONObject(bytes.toString(StandardCharsets.UTF_8.name()));
      }
      if (!result.optBoolean("ok"))
        throw new TransferFailure("Hub не подтвердил восстановление архива.");
      api.clearSession();
      return "Hub подтвердил восстановление. Вернитесь на главный экран и войдите с учётными"
          + " данными из архива. Перезапустите сервис Hub на VPS для применения состояния.";
    } finally {
      if (conn != null) conn.disconnect();
      temporary.delete();
    }
  }

  private static final class TransferFailure extends IOException {
    TransferFailure(String message) {
      super(message);
    }
  }
}
