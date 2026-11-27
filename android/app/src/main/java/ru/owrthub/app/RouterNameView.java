package ru.owrthub.app;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.LinearGradient;
import android.graphics.Shader;
import android.text.Layout;
import android.widget.TextView;

/** A real accessible name, with the same static text gradient as the Hub cards. */
final class RouterNameView extends TextView {
  private static final int[] COLORS = {0xff218fc4, 0xff697bd0, NativeUi.BRIGHT_RED};
  private static final float[] STOPS = {0f, .46f, 1f};
  private Layout gradientLayout;
  private float gradientTextSize;
  private boolean dirty = true;

  RouterNameView(Context context) {
    super(context);
  }

  @Override
  protected void onTextChanged(CharSequence text, int start, int lengthBefore, int lengthAfter) {
    super.onTextChanged(text, start, lengthBefore, lengthAfter);
    dirty = true;
  }

  @Override
  protected void onSizeChanged(int width, int height, int oldWidth, int oldHeight) {
    super.onSizeChanged(width, height, oldWidth, oldHeight);
    dirty = true;
  }

  @Override
  protected void onDraw(Canvas canvas) {
    Layout layout = getLayout();
    if (layout != null && (dirty || gradientLayout != layout || gradientTextSize != getTextSize())) {
      float left = Float.MAX_VALUE, right = -Float.MAX_VALUE;
      for (int line = 0; line < layout.getLineCount(); line++) {
        if (layout.getLineWidth(line) <= 0) continue;
        left = Math.min(left, layout.getLineLeft(line));
        right = Math.max(right, layout.getLineRight(line));
      }
      // Layout positions already account for centre/start alignment, RTL,
      // ellipsis and wrapping. The gradient spans the glyphs, not the View.
      getPaint().setShader(right > left
          ? new LinearGradient(left, 0, right, 0, COLORS, STOPS, Shader.TileMode.CLAMP)
          : null);
      gradientLayout = layout;
      gradientTextSize = getTextSize();
      dirty = false;
    }
    super.onDraw(canvas);
  }
}
