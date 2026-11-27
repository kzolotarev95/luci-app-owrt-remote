package ru.owrthub.app;

import android.content.Context;
import android.graphics.*;
import android.util.LruCache;
import android.view.View;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.text.Normalizer;
import java.util.*;
import org.json.*;

/** Local, verified product photographs. A user-assigned router name is never a model. */
final class RouterPhotoView extends View {
  private static final Map<String, String> aliases = new HashMap<>();
  private static final Map<String, String> revisionAliases = new HashMap<>();
  private static final Map<String, String> displayAliases = new HashMap<>();
  private static boolean catalogLoaded;
  private static final LruCache<String, Bitmap> images =
      new LruCache<String, Bitmap>(3072) {
        @Override
        protected int sizeOf(String key, Bitmap value) {
          return value.getAllocationByteCount() / 1024 + 1;
        }
      };
  private final NativeUi ui;
  private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.FILTER_BITMAP_FLAG);
  private final RectF box = new RectF();
  private String file = "";
  private Bitmap image;

  RouterPhotoView(Context context, NativeUi ui, JSONObject router, int widthDp) {
    super(context);
    this.ui = ui;
    setLayoutParams(
        new android.widget.LinearLayout.LayoutParams(ui.dp(widthDp), ui.dp(widthDp * .78f)));
    setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
    try {
      synchronized (aliases) {
        if (!catalogLoaded) {
          try (InputStream input = context.getAssets().open("router-models/catalog.json")) {
            ByteArrayOutputStream data = new ByteArrayOutputStream();
            byte[] buffer = new byte[4096];
            int read;
            while ((read = input.read(buffer)) != -1) data.write(buffer, 0, read);
            JSONArray rows = new JSONArray(new String(data.toByteArray(), StandardCharsets.UTF_8));
            for (int i = 0; i < rows.length(); i++) {
              JSONObject row = rows.getJSONObject(i);
              String name = row.optString("file");
              if (!name.matches("[a-z0-9-]+\\.webp")) continue;
              JSONArray names = row.getJSONArray("aliases");
              for (int j = 0; j < names.length(); j++)
                aliases.put(normalize(names.getString(j)), name);
              JSONArray revisions = row.optJSONArray("revision_aliases");
              if (revisions != null) for (int j = 0; j < revisions.length(); j++)
                revisionAliases.put(normalize(revisions.getString(j)), name);
              JSONObject labels = row.optJSONObject("display_aliases");
              if (labels != null) for (Iterator<String> keys = labels.keys(); keys.hasNext();) {
                String key = keys.next();
                displayAliases.put(normalize(key), labels.getString(key));
              }
            }
          }
          catalogLoaded = true;
        }
      }
      JSONObject status = router.optJSONObject("status");
      String[] models = {
        status == null ? "" : status.optString("model"),
        status == null ? "" : status.optString("board"),
        router.optString("model")
      };
      for (String name : models) {
        if (name.isEmpty() || name.matches("(?i)unknown|generic|openwrt|null|unavailable"))
          continue;
        String key = normalize(name);
        file = aliases.getOrDefault(key, "");
        if (file.isEmpty()) for (Map.Entry<String, String> revision : revisionAliases.entrySet()) {
          String prefix = revision.getKey() + "v";
          if (key.startsWith(prefix) && key.substring(prefix.length()).matches("[0-9]+")) {
            file = revision.getValue();
            break;
          }
        }
        break;
      }
      if (!file.isEmpty()) {
        int sample = widthDp > 80 ? 1 : 2;
        String key = file + "@" + sample;
        image = images.get(key);
        if (image == null)
          try (InputStream input = context.getAssets().open("router-models/" + file)) {
            BitmapFactory.Options options = new BitmapFactory.Options();
            options.inSampleSize = sample;
            image = BitmapFactory.decodeStream(input, null, options);
            if (image != null) images.put(key, image);
          }
      }
    } catch (Exception unavailable) {
      image = null;
    }
  }

  private static String normalize(String text) {
    return Normalizer.normalize(text.toLowerCase(Locale.ROOT), Normalizer.Form.NFKD)
        .replaceAll("\\([^)]*(?:openwrt|u-?boot|layout|stock|ubi)[^)]*\\)", "")
        .replaceAll("[^a-z0-9]", "");
  }

  static String displayModel(JSONObject router) {
    JSONObject status = router.optJSONObject("status");
    String model = status == null ? "" : status.optString("model");
    if (model.isEmpty() && status != null) model = status.optString("board");
    if (model.isEmpty()) model = router.optString("model", "Модель не передана");
    synchronized (aliases) {
      return displayAliases.getOrDefault(normalize(model), model);
    }
  }

  void copyFrom(RouterPhotoView other) {
    if (!file.equals(other.file) || image != other.image) {
      file = other.file;
      image = other.image;
      invalidate();
    }
  }

  @Override
  protected void onDraw(Canvas canvas) {
    float w = getWidth(), h = getHeight(), r = ui.dp(8);
    box.set(.5f, .5f, w - .5f, h - .5f);
    paint.setStyle(Paint.Style.FILL);
    paint.setShader(
        new LinearGradient(
            0, 0, w, h, new int[] {ui.PANEL2, ui.PANEL}, null, Shader.TileMode.CLAMP));
    canvas.drawRoundRect(box, r, r, paint);
    paint.setShader(null);
    canvas.save();
    Path clip = new Path();
    clip.addRoundRect(box, r, r, Path.Direction.CW);
    canvas.clipPath(clip);
    paint.setShader(
        new RadialGradient(
            w * .5f,
            h * .96f,
            w * .62f,
            NativeUi.alpha(ui.AMBER, .24f),
            Color.TRANSPARENT,
            Shader.TileMode.CLAMP));
    canvas.drawRect(0, 0, w, h, paint);
    paint.setShader(null);
    if (image != null) {
      float scale = Math.min((w - ui.dp(4)) / image.getWidth(), (h - ui.dp(4)) / image.getHeight());
      float iw = image.getWidth() * scale, ih = image.getHeight() * scale;
      box.set((w - iw) / 2, (h - ih) / 2, (w + iw) / 2, (h + ih) / 2);
      canvas.drawBitmap(image, null, box, paint);
    } else {
      canvas.save();
      canvas.translate(w / 2 - ui.dp(12), h / 2 - ui.dp(12));
      canvas.scale(ui.dp(24) / 24f, ui.dp(24) / 24f);
      paint.setStyle(Paint.Style.STROKE);
      paint.setStrokeWidth(1.4f);
      paint.setColor(ui.MUTED);
      box.set(3, 12, 21, 20);
      canvas.drawRoundRect(box, 2, 2, paint);
      canvas.drawLine(7, 12, 7, 5, paint);
      canvas.drawLine(17, 12, 17, 5, paint);
      canvas.drawLine(7, 16, 11, 16, paint);
      canvas.restore();
    }
    canvas.restore();
    box.set(.5f, .5f, w - .5f, h - .5f);
    paint.setStyle(Paint.Style.STROKE);
    paint.setStrokeWidth(ui.dp(1));
    paint.setColor(ui.LINE);
    canvas.drawRoundRect(box, r, r, paint);
  }
}
