package ru.owrthub.app;

import android.content.Context;
import android.graphics.*;
import android.view.View;
import java.util.Locale;

final class DiskGaugeView extends View {
  private final NativeUi ui;
  private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
  private double value;

  DiskGaugeView(Context context, NativeUi ui, double percent) {
    super(context);
    this.ui = ui;
    setValue(percent);
    setLayoutParams(new android.widget.LinearLayout.LayoutParams(ui.dp(104), ui.dp(104)));
  }

  private void setValue(double percent) {
    value = Double.isFinite(percent) && percent >= 0 ? Math.min(100, percent) : -1;
    setContentDescription(value < 0 ? "Диск: нет данных" : "Диск занят на " + label());
    invalidate();
  }

  private String label() {
    return value < 0
        ? "—"
        : String.format(Locale.forLanguageTag("ru"), "%.1f", value).replace(",0", "") + "%";
  }

  void copyFrom(DiskGaugeView other) {
    if (Double.compare(value, other.value) != 0) setValue(other.value);
  }

  @Override
  protected void onDraw(Canvas c) {
    float w = getWidth(), h = getHeight(), s = Math.min(w, h), line = ui.dp(5), r = (s - line) / 2;
    RectF rect = new RectF(w / 2 - r, h / 2 - r, w / 2 + r, h / 2 + r);
    paint.setStyle(Paint.Style.STROKE);
    paint.setStrokeWidth(line);
    paint.setColor(ui.LINE);
    c.drawOval(rect, paint);
    if (value >= 0) {
      paint.setColor(value >= 90 ? ui.RED : ui.AMBER);
      c.drawArc(rect, -90, (float) value * 3.6f, false, paint);
    }
    paint.setStyle(Paint.Style.FILL);
    paint.setTypeface(ui.semibold);
    paint.setColor(ui.TEXT);
    paint.setTextSize(ui.sp(23));
    String label = label();
    if (paint.measureText(label) > s * .8f)
      paint.setTextSize(paint.getTextSize() * s * .8f / paint.measureText(label));
    c.drawText(label, (w - paint.measureText(label)) / 2, h * .49f - paint.ascent() / 2, paint);
    paint.setTypeface(ui.regular);
    paint.setColor(ui.MUTED);
    paint.setTextSize(ui.sp(10));
    String sub = "занято";
    c.drawText(sub, (w - paint.measureText(sub)) / 2, h * .73f, paint);
  }
}
