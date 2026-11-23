package ru.owrthub.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import java.io.IOException;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.KeyStore;
import java.util.Arrays;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import org.json.JSONException;
import org.json.JSONObject;

/** Private encrypted connection/session state. Passwords are never accepted or stored. */
public final class HubSessionStore {
  private static final String PREFERENCES = "hub_native_session_v1";
  private static final String RECORD = "encrypted_record";
  private static final String KEY_ALIAS = "owrthub_native_session_v1";
  private static final byte[] AAD = "ru.owrthub.app/session/v1".getBytes(StandardCharsets.UTF_8);
  private static final int MAX_RECORD_LENGTH = 32768;
  private final SharedPreferences preferences;

  public HubSessionStore(Context context) {
    preferences =
        context.getApplicationContext().getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE);
  }

  public static final class SavedSession {
    public final URI hub;
    public final String origin;
    public final String username;

    /** Only the single session cookie pair, never a complete server response. */
    public final String cookie;

    public final long expiresAtMillis;

    private SavedSession(URI hub, String username, String cookie, long expiresAtMillis) {
      this.hub = hub;
      this.origin = hub.toString();
      this.username = username;
      this.cookie = cookie;
      this.expiresAtMillis = expiresAtMillis;
    }
  }

  /** An expired session retains the connection form, with an empty cookie. */
  public SavedSession load() throws IOException {
    synchronized (HubSessionStore.class) {
      String envelope = preferences.getString(RECORD, "");
      if (envelope.isEmpty()) return null;
      byte[] cleartext = null;
      try {
        if (envelope.length() > MAX_RECORD_LENGTH) throw new GeneralSecurityException();
        String[] parts = envelope.split(":", -1);
        if (parts.length != 3 || !parts[0].equals("1")) throw new GeneralSecurityException();
        byte[] iv = Base64.decode(parts[1], Base64.NO_WRAP);
        byte[] ciphertext = Base64.decode(parts[2], Base64.NO_WRAP);
        if (iv.length != 12 || ciphertext.length < 16) throw new GeneralSecurityException();
        SecretKey key = key(false);
        if (key == null) throw new GeneralSecurityException();
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, key, new GCMParameterSpec(128, iv));
        cipher.updateAAD(AAD);
        cleartext = cipher.doFinal(ciphertext);
        JSONObject saved = new JSONObject(new String(cleartext, StandardCharsets.UTF_8));
        URI hub = HubUrlPolicy.normalizeHub(saved.getString("origin"), "", BuildConfig.DEBUG);
        String username = validUsername(saved.optString("username", ""));
        String cookie = saved.optString("cookie", "");
        long expiry = saved.optLong("expires_at", 0);
        if (!cookie.isEmpty() && !HubApiClient.isSessionCookiePair(cookie)) {
          throw new GeneralSecurityException();
        }
        if (expiry <= System.currentTimeMillis()) cookie = "";
        return new SavedSession(hub, username, cookie, expiry);
      } catch (GeneralSecurityException | JSONException | IllegalArgumentException exception) {
        // App restore, invalidated Keystore keys, and modified records all fail closed.
        clear();
        return null;
      } finally {
        if (cleartext != null) Arrays.fill(cleartext, (byte) 0);
      }
    }
  }

  public void save(URI hub, String username, String cookie, long expiresAtMillis)
      throws IOException {
    synchronized (HubSessionStore.class) {
      URI canonical =
          HubUrlPolicy.normalizeHub(hub == null ? null : hub.toString(), "", BuildConfig.DEBUG);
      if (cookie == null) cookie = "";
      if (!cookie.isEmpty() && !HubApiClient.isSessionCookiePair(cookie)) {
        throw new IOException("Недопустимая сессия Hub");
      }
      byte[] cleartext = null;
      try {
        JSONObject saved = new JSONObject();
        saved.put("origin", canonical.toString());
        saved.put("username", validUsername(username));
        saved.put("cookie", cookie);
        saved.put("expires_at", cookie.isEmpty() ? 0 : expiresAtMillis);
        cleartext = saved.toString().getBytes(StandardCharsets.UTF_8);
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, key(true));
        cipher.updateAAD(AAD);
        byte[] ciphertext = cipher.doFinal(cleartext);
        String envelope =
            "1:"
                + Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP)
                + ":"
                + Base64.encodeToString(ciphertext, Base64.NO_WRAP);
        if (!preferences.edit().putString(RECORD, envelope).commit()) {
          throw new IOException("Не удалось сохранить сессию Hub");
        }
      } catch (GeneralSecurityException | JSONException exception) {
        // Do not include platform exception text or credentials in errors.
        throw new IOException("Не удалось защитить сессию Hub");
      } finally {
        if (cleartext != null) Arrays.fill(cleartext, (byte) 0);
      }
    }
  }

  public void saveConnection(URI hub, String username) throws IOException {
    synchronized (HubSessionStore.class) {
      SavedSession previous = load();
      URI canonical =
          HubUrlPolicy.normalizeHub(hub == null ? null : hub.toString(), "", BuildConfig.DEBUG);
      boolean same =
          previous != null
              && previous.hub.equals(canonical)
              && previous.username.equals(validUsername(username));
      save(canonical, username, same ? previous.cookie : "", same ? previous.expiresAtMillis : 0);
    }
  }

  /** Keep the encrypted host/username so the next sign-in can reuse the form. */
  public boolean clearSessionIfMatches(URI hub, String cookie) throws IOException {
    synchronized (HubSessionStore.class) {
      SavedSession current = load();
      if (current == null || !current.hub.equals(hub) || !current.cookie.equals(cookie))
        return false;
      save(current.hub, current.username, "", 0);
      return true;
    }
  }

  public boolean saveIfMatches(
      URI hub, String expectedCookie, String username, String cookie, long expiresAtMillis)
      throws IOException {
    synchronized (HubSessionStore.class) {
      SavedSession current = load();
      if (current == null || !current.hub.equals(hub) || !current.cookie.equals(expectedCookie))
        return false;
      if (current.cookie.equals(cookie)
          && current.username.equals(username)
          && current.expiresAtMillis == expiresAtMillis) return true;
      save(hub, username, cookie, expiresAtMillis);
      return true;
    }
  }

  public void clearSession() throws IOException {
    synchronized (HubSessionStore.class) {
      SavedSession previous = load();
      if (previous != null) save(previous.hub, previous.username, "", 0);
    }
  }

  public void clear() throws IOException {
    synchronized (HubSessionStore.class) {
      if (!preferences.edit().clear().commit()) {
        throw new IOException("Не удалось удалить сохранённую сессию Hub");
      }
    }
  }

  public String getLastOrigin() throws IOException {
    synchronized (HubSessionStore.class) {
      SavedSession saved = load();
      return saved == null ? "" : saved.origin;
    }
  }

  public String getLastUsername() throws IOException {
    synchronized (HubSessionStore.class) {
      SavedSession saved = load();
      return saved == null ? "" : saved.username;
    }
  }

  private static String validUsername(String username) {
    String result = username == null ? "" : username.trim();
    if (result.length() > 256)
      throw new IllegalArgumentException("Слишком длинное имя пользователя");
    for (int index = 0; index < result.length(); index++) {
      if (Character.isISOControl(result.charAt(index))) {
        throw new IllegalArgumentException("Недопустимое имя пользователя");
      }
    }
    return result;
  }

  private static SecretKey key(boolean create) throws GeneralSecurityException, IOException {
    KeyStore store = KeyStore.getInstance("AndroidKeyStore");
    store.load(null);
    SecretKey existing = (SecretKey) store.getKey(KEY_ALIAS, null);
    if (existing != null || !create) return existing;
    KeyGenerator generator =
        KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
    generator.init(
        new KeyGenParameterSpec.Builder(
                KEY_ALIAS, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256)
            .setRandomizedEncryptionRequired(true)
            .build());
    return generator.generateKey();
  }
}
