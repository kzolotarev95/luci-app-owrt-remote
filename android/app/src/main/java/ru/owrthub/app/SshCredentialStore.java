package ru.owrthub.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.*;
import android.util.Base64;
import java.nio.charset.StandardCharsets;
import java.security.*;
import javax.crypto.*;
import javax.crypto.spec.GCMParameterSpec;

/** Opt-in SSH secrets, scoped to Hub, Hub account and router; excluded from backup. */
final class SshCredentialStore {
  private final SharedPreferences prefs;

  SshCredentialStore(Context context) {
    prefs = context.getSharedPreferences("ssh_credentials", 0);
  }

  private String id(String origin, String user, String router) throws Exception {
    byte[] digest =
        MessageDigest.getInstance("SHA-256")
            .digest((origin + "\n" + user + "\n" + router).getBytes(StandardCharsets.UTF_8));
    return Base64.encodeToString(digest, Base64.NO_WRAP);
  }

  synchronized void save(String origin, String user, String router, String password)
      throws Exception {
    if (password.length() > 4096) throw new IllegalArgumentException("Слишком длинный пароль");
    String name = id(origin, user, router);
    if (password.isEmpty()) {
      prefs.edit().remove(name).commit();
      return;
    }
    Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
    cipher.init(Cipher.ENCRYPT_MODE, key());
    cipher.updateAAD(name.getBytes(StandardCharsets.UTF_8));
    String encrypted =
        Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP)
            + ":"
            + Base64.encodeToString(
                cipher.doFinal(password.getBytes(StandardCharsets.UTF_8)), Base64.NO_WRAP);
    if (!prefs.edit().putString(name, encrypted).commit())
      throw new java.io.IOException("Пароль не сохранён");
  }

  synchronized String load(String origin, String user, String router) throws Exception {
    String name = id(origin, user, router), value = prefs.getString(name, "");
    if (value.isEmpty()) return "";
    try {
      String[] parts = value.split(":");
      if (parts.length != 2 || value.length() > 12000) throw new GeneralSecurityException();
      Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
      cipher.init(
          Cipher.DECRYPT_MODE,
          key(),
          new GCMParameterSpec(128, Base64.decode(parts[0], Base64.NO_WRAP)));
      cipher.updateAAD(name.getBytes(StandardCharsets.UTF_8));
      byte[] clear = cipher.doFinal(Base64.decode(parts[1], Base64.NO_WRAP));
      try {
        return new String(clear, StandardCharsets.UTF_8);
      } finally {
        java.util.Arrays.fill(clear, (byte) 0);
      }
    } catch (GeneralSecurityException | IllegalArgumentException invalid) {
      prefs.edit().remove(name).commit();
      return "";
    }
  }

  private static synchronized SecretKey key() throws Exception {
    KeyStore ks = KeyStore.getInstance("AndroidKeyStore");
    ks.load(null);
    SecretKey key = (SecretKey) ks.getKey("owrthub_ssh_v1", null);
    if (key != null) return key;
    KeyGenerator generator = KeyGenerator.getInstance("AES", "AndroidKeyStore");
    generator.init(
        new KeyGenParameterSpec.Builder(
                "owrthub_ssh_v1", KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256)
            .build());
    return generator.generateKey();
  }
}
