package ru.owrthub.app;

import android.app.Activity;
import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Path;
import android.os.PowerManager;
import android.os.SystemClock;
import android.provider.Settings;
import android.view.View;
import android.widget.FrameLayout;

/** One bounded native background per screen; no timer runs while the window is hidden. */
final class NativeEffects extends View {
  private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
  private final Path path = new Path();
  private String mode = "off";
  private boolean animate;
  private final Runnable frame = () -> invalidate();

  private NativeEffects(Context context) {
    super(context);
    setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
    setClickable(false);
    setFocusable(false);
  }

  static void install(Activity activity, View page) {
    FrameLayout layer = new FrameLayout(activity);
    layer.setBackground(page.getBackground());
    page.setBackground(null);
    layer.addView(new NativeEffects(activity), new FrameLayout.LayoutParams(-1, -1));
    layer.addView(page, new FrameLayout.LayoutParams(-1, -1));
    activity.setContentView(layer);
  }

  @Override
  protected void onWindowVisibilityChanged(int visibility) {
    super.onWindowVisibilityChanged(visibility);
    removeCallbacks(frame);
    mode =
        getContext()
            .getSharedPreferences("native_design", Context.MODE_PRIVATE)
            .getString("effect", "off");
    if (getContext()
        .getSharedPreferences("native_design", Context.MODE_PRIVATE)
        .getBoolean("lite", false)) mode = "off";
    PowerManager power = (PowerManager) getContext().getSystemService(Context.POWER_SERVICE);
    animate =
        visibility == VISIBLE
            && (power == null || !power.isPowerSaveMode())
            && Settings.Global.getFloat(
                    getContext().getContentResolver(), Settings.Global.ANIMATOR_DURATION_SCALE, 1f)
                > 0;
    invalidate();
  }

  @Override
  protected void onDetachedFromWindow() {
    removeCallbacks(frame);
    super.onDetachedFromWindow();
  }

  @Override
  protected void onDraw(Canvas canvas) {
    super.onDraw(canvas);
    if ("off".equals(mode)) return;
    float w = getWidth(), h = getHeight();
    double t = animate ? SystemClock.uptimeMillis() / 1000.0 : 0;
    if ("snow".equals(mode)) {
      paint.setStyle(Paint.Style.FILL);
      paint.setColor(Color.argb(115, 218, 240, 255));
      for (int i = 0; i < 36; i++) {
        float x = (float) (((i * 0.6180339) % 1) * w + Math.sin(t * .4 + i) * 12);
        float y = (float) (((i * .371 + t * (.012 + i % 3 * .004)) % 1) * h);
        canvas.drawCircle(
            x, y, getResources().getDisplayMetrics().density * (i % 3 == 0 ? 1.8f : 1f), paint);
      }
    } else if ("streams".equals(mode)) {
      paint.setStyle(Paint.Style.STROKE);
      paint.setStrokeWidth(getResources().getDisplayMetrics().density);
      for (int i = 0; i < 5; i++) {
        paint.setColor(Color.argb(50 + i * 9, 104, 183, 255));
        float y = (float) (((t * .018 + i * .21) % 1.4 - .2) * h);
        path.reset();
        path.moveTo(-30, y);
        path.cubicTo(w * .25f, y - h * .15f, w * .72f, y + h * .16f, w + 30, y - h * .03f);
        canvas.drawPath(path, paint);
      }
    }
    removeCallbacks(frame);
    if (animate && isShown() && getWindowVisibility() == VISIBLE) postDelayed(frame, 33);
  }
}
