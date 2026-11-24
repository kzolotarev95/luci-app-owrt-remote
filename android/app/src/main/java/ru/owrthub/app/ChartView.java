package ru.owrthub.app;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.DashPathEffect;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.util.AttributeSet;
import android.view.View;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.TimeZone;

/** A native availability chart. Timestamps and period boundaries are Unix milliseconds. */
public final class ChartView extends View {
  public static final long DAY_MILLIS = 24L * 60 * 60 * 1000;

  public static final class Point {
    public final long timestamp;
    public final int online, total;

    public Point(long timestamp, int online, int total) {
      this.timestamp = timestamp;
      this.online = online;
      this.total = total;
    }
  }

  private final NativeUi ui;
  private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
  private final Path path = new Path();
  private final Path fill = new Path();
  private final RectF plot = new RectF();
  private final SimpleDateFormat hourFormat = new SimpleDateFormat("HH:mm", Locale.getDefault());
  private final SimpleDateFormat dateFormat =
      new SimpleDateFormat("d MMM, HH:mm", Locale.getDefault());
  private final List<Point> points = new ArrayList<>();
  private long periodStart, periodEnd, expectedIntervalMillis, gapAfterMillis;
  private int accent;
  private boolean showOffline = true;
  private String emptyMessage = "За этот период нет наблюдений";

  public ChartView(Context context) {
    this(context, new NativeUi(context));
  }

  public ChartView(Context context, AttributeSet attrs) {
    this(context, new NativeUi(context));
  }

  public ChartView(Context context, NativeUi ui) {
    super(context);
    this.ui = ui;
    paint.setTypeface(ui.regular);
    accent = ui.GREEN;
    periodEnd = System.currentTimeMillis();
    periodStart = periodEnd - DAY_MILLIS;
    setMinimumHeight(ui.dp(190));
    setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_YES);
    updateDescription();
  }

  /** Pass the authoritative server period even if only a few observations exist. */
  public void setData(List<Point> observations, long startMillis, long endMillis) {
    if (endMillis <= startMillis)
      throw new IllegalArgumentException("Chart period must have positive duration");
    periodStart = startMillis;
    periodEnd = endMillis;
    points.clear();
    if (observations != null) {
      for (Point point : observations) {
        if (point != null
            && point.timestamp >= periodStart
            && point.timestamp <= periodEnd
            && point.online >= 0
            && point.total >= 0
            && point.online <= point.total) points.add(point);
      }
    }
    Collections.sort(points, Comparator.comparingLong(point -> point.timestamp));
    // Duplicate timestamps cannot describe two different positions on the time axis.
    for (int i = points.size() - 2; i >= 0; i--)
      if (points.get(i).timestamp == points.get(i + 1).timestamp) points.remove(i);
    updateDescription();
    invalidate();
  }

  /** Convenience for a 24-hour chart ending at the newest actual observation. */
  public void setData(List<Point> observations) {
    long end = 0;
    if (observations != null)
      for (Point point : observations) if (point != null) end = Math.max(end, point.timestamp);
    if (end <= 0) end = System.currentTimeMillis();
    setData(observations, end - DAY_MILLIS, end);
  }

  void copyObservations(ChartView source) {
    setData(new ArrayList<>(source.points), source.periodStart, source.periodEnd);
    setExpectedIntervalMillis(source.expectedIntervalMillis);
    setGapAfterMillis(source.gapAfterMillis);
  }

  public void setAccent(int color) {
    accent = color;
    invalidate();
  }

  public void setShowOffline(boolean offline) {
    showOffline = offline;
    invalidate();
  }

  /** Set the server sampling interval when known, so missing samples stay visibly missing. */
  public void setExpectedIntervalMillis(long intervalMillis) {
    expectedIntervalMillis = Math.max(0, intervalMillis);
    invalidate();
  }

  /** Prefer the server's continuity threshold over an inferred sampling cadence. */
  public void setGapAfterMillis(long thresholdMillis) {
    gapAfterMillis = Math.max(0, thresholdMillis);
    invalidate();
  }

  public void setEmptyMessage(String message) {
    emptyMessage = message == null ? "" : message;
    updateDescription();
    invalidate();
  }

  public void setTimeZone(TimeZone timeZone) {
    if (timeZone == null) return;
    hourFormat.setTimeZone(timeZone);
    dateFormat.setTimeZone(timeZone);
    updateDescription();
    invalidate();
  }

  @Override
  protected void onMeasure(int widthSpec, int heightSpec) {
    int desiredHeight = ui.dp(190) + getPaddingTop() + getPaddingBottom();
    setMeasuredDimension(
        resolveSize(ui.dp(320), widthSpec), resolveSize(desiredHeight, heightSpec));
  }

  @Override
  protected void onDraw(Canvas canvas) {
    super.onDraw(canvas);
    float left = getPaddingLeft() + ui.dp(30);
    float right = getWidth() - getPaddingRight() - ui.dp(3);
    float legendHeight = drawLegend(canvas);
    float top = getPaddingTop() + Math.max(ui.dp(34), legendHeight + ui.dp(12));
    float bottom = getHeight() - getPaddingBottom() - ui.dp(30);
    if (right <= left || bottom <= top) return;
    plot.set(left, top, right, bottom);

    int max = 1;
    for (Point point : points) max = Math.max(max, point.total);
    int ceiling = niceCeiling(max);
    drawGrid(canvas, ceiling);
    if (points.isEmpty()) {
      drawEmpty(canvas);
      return;
    }

    long allowedGap = allowedGap();
    canvas.save();
    canvas.clipRect(
        plot.left - ui.dp(3), plot.top - ui.dp(3), plot.right + ui.dp(3), plot.bottom + ui.dp(3));
    drawSeries(canvas, ceiling, allowedGap, true);
    drawSeries(canvas, ceiling, allowedGap, false);
    canvas.restore();
  }

  private float drawLegend(Canvas canvas) {
    paint.setPathEffect(null);
    paint.setStyle(Paint.Style.FILL);
    paint.setTextSize(ui.sp(12));
    paint.setTextAlign(Paint.Align.LEFT);
    Point latest = points.isEmpty() ? null : points.get(points.size() - 1);
    String onlineLabel = "В сети" + (latest == null ? "" : " " + latest.online);
    String otherLabel =
        (showOffline ? "Не в сети" : "Всего")
            + (latest == null
                ? ""
                : " " + (showOffline ? latest.total - latest.online : latest.total));
    Paint.FontMetrics metrics = paint.getFontMetrics();
    float lineHeight = Math.max(ui.dp(18), metrics.descent - metrics.ascent + ui.dp(4));
    float x = getPaddingLeft() + ui.dp(2);
    float baseline = getPaddingTop() - metrics.ascent;
    boolean stacked =
        paint.measureText(onlineLabel) + paint.measureText(otherLabel) + ui.dp(45)
            > getWidth() - getPaddingLeft() - getPaddingRight();
    drawLegendLabel(canvas, onlineLabel, x, baseline, metrics, accent);
    if (stacked) baseline += lineHeight;
    else x = getWidth() - getPaddingRight() - paint.measureText(otherLabel) - ui.dp(15);
    drawLegendLabel(canvas, otherLabel, x, baseline, metrics, showOffline ? ui.RED : ui.BLUE);
    return lineHeight * (stacked ? 2 : 1);
  }

  private void drawLegendLabel(
      Canvas canvas, String label, float x, float baseline, Paint.FontMetrics metrics, int color) {
    paint.setColor(color);
    canvas.drawCircle(
        x + ui.dp(3), baseline + (metrics.ascent + metrics.descent) / 2, ui.dp(3), paint);
    paint.setColor(ui.MUTED);
    canvas.drawText(label, x + ui.dp(13), baseline, paint);
  }

  private void drawGrid(Canvas canvas, int ceiling) {
    paint.setStyle(Paint.Style.STROKE);
    paint.setStrokeWidth(ui.dp(1));
    paint.setColor(ui.LINE);
    paint.setPathEffect(new DashPathEffect(new float[] {ui.dp(3), ui.dp(4)}, 0));
    for (int i = 0; i <= 2; i++) {
      float y = plot.bottom - plot.height() * i / 2f;
      canvas.drawLine(plot.left, y, plot.right, y, paint);
    }
    paint.setPathEffect(null);
    paint.setStyle(Paint.Style.FILL);
    paint.setTextSize(ui.sp(11));
    paint.setTextAlign(Paint.Align.RIGHT);
    paint.setColor(ui.MUTED);
    for (int i = 0; i <= 2; i++) {
      float value = ceiling * i / 2f;
      // A count axis only labels integer observations, including for a single router.
      if (value != (int) value) continue;
      canvas.drawText(
          Integer.toString((int) value),
          plot.left - ui.dp(8),
          plot.bottom - plot.height() * i / 2f + ui.dp(4),
          paint);
    }
    for (int i = 0; i < 5; i++) {
      long time = periodStart + (long) ((periodEnd - periodStart) * (i / 4.0));
      float x = plot.left + plot.width() * i / 4f;
      paint.setColor(ui.LINE);
      canvas.drawRect(
          x - ui.dp(0.5f), plot.bottom + ui.dp(2), x + ui.dp(0.5f), plot.bottom + ui.dp(5), paint);
      paint.setColor(ui.MUTED);
      paint.setTextAlign(
          i == 0 ? Paint.Align.LEFT : i == 4 ? Paint.Align.RIGHT : Paint.Align.CENTER);
      canvas.drawText(hourFormat.format(new Date(time)), x, plot.bottom + ui.dp(22), paint);
    }
  }

  private void drawSeries(Canvas canvas, int ceiling, long allowedGap, boolean total) {
    int color = total ? (showOffline ? ui.RED : ui.BLUE) : accent;
    path.reset();
    fill.reset();
    float startX = 0, lastX = 0;
    int runLength = 0;
    Point previous = null;
    for (int i = 0; i < points.size(); i++) {
      Point point = points.get(i);
      float x = x(point.timestamp);
      float y = y(value(point, total), ceiling);
      boolean startsRun = previous == null || point.timestamp - previous.timestamp > allowedGap;
      if (startsRun) {
        if (!total && runLength > 1) closeFill(canvas, startX, lastX, color);
        path.moveTo(x, y);
        fill.reset();
        fill.moveTo(x, y);
        startX = x;
        runLength = 1;
      } else {
        float previousY = y(value(previous, total), ceiling);
        path.lineTo(x, previousY);
        path.lineTo(x, y);
        fill.lineTo(x, previousY);
        fill.lineTo(x, y);
        runLength++;
      }
      lastX = x;
      previous = point;
    }
    if (!total && runLength > 1) closeFill(canvas, startX, lastX, color);

    paint.setStyle(Paint.Style.STROKE);
    paint.setStrokeWidth(ui.dp(total ? 1.5f : 2));
    paint.setStrokeJoin(Paint.Join.ROUND);
    paint.setStrokeCap(Paint.Cap.ROUND);
    paint.setColor(total ? NativeUi.alpha(color, 0.85f) : color);
    paint.setPathEffect(
        total && !showOffline ? new DashPathEffect(new float[] {ui.dp(4), ui.dp(4)}, 0) : null);
    canvas.drawPath(path, paint);
    paint.setPathEffect(null);

    // Mark real observations; a sparse series remains visible without extrapolated lines.
    for (int i = 0; i < points.size(); i++) {
      Point point = points.get(i);
      boolean isolated =
          (i == 0 || point.timestamp - points.get(i - 1).timestamp > allowedGap)
              && (i == points.size() - 1
                  || points.get(i + 1).timestamp - point.timestamp > allowedGap);
      if (!isolated && i != points.size() - 1) continue;
      float x = x(point.timestamp), y = y(value(point, total), ceiling);
      paint.setStyle(Paint.Style.FILL);
      paint.setColor(ui.PANEL);
      canvas.drawCircle(x, y, ui.dp(total ? 2.5f : 3.5f), paint);
      paint.setColor(total ? NativeUi.alpha(color, 0.8f) : color);
      canvas.drawCircle(x, y, ui.dp(total ? 1.5f : 2.2f), paint);
    }
  }

  private int value(Point point, boolean secondary) {
    return secondary ? (showOffline ? point.total - point.online : point.total) : point.online;
  }

  private void closeFill(Canvas canvas, float startX, float endX, int color) {
    fill.lineTo(endX, plot.bottom);
    fill.lineTo(startX, plot.bottom);
    fill.close();
    paint.setStyle(Paint.Style.FILL);
    paint.setColor(NativeUi.alpha(color, 0.065f));
    paint.setPathEffect(null);
    canvas.drawPath(fill, paint);
  }

  private void drawEmpty(Canvas canvas) {
    paint.setTextSize(ui.sp(13));
    paint.setTextAlign(Paint.Align.CENTER);
    paint.setStyle(Paint.Style.FILL);
    String message = emptyMessage;
    // Keep the empty state readable within compact cards and large accessibility fonts.
    float availableWidth = Math.max(0, plot.width() - ui.dp(12));
    while (message.length() > 1 && paint.measureText(message) > availableWidth)
      message = message.substring(0, message.length() - 1);
    if (!message.equals(emptyMessage) && message.length() > 1)
      message = message.substring(0, message.length() - 1) + "…";
    float y = plot.centerY() + ui.dp(4);
    float width = paint.measureText(message);
    paint.setColor(ui.PANEL);
    canvas.drawRoundRect(
        plot.centerX() - width / 2 - ui.dp(8),
        y - ui.sp(15),
        plot.centerX() + width / 2 + ui.dp(8),
        y + ui.dp(7),
        ui.dp(6),
        ui.dp(6),
        paint);
    paint.setColor(ui.MUTED);
    canvas.drawText(message, plot.centerX(), y, paint);
  }

  private long allowedGap() {
    if (gapAfterMillis > 0) return gapAfterMillis;
    if (expectedIntervalMillis > 0) return saturatedTriple(expectedIntervalMillis);
    if (points.size() < 2) return 0;
    List<Long> intervals = new ArrayList<>();
    for (int i = 1; i < points.size(); i++)
      intervals.add(points.get(i).timestamp - points.get(i - 1).timestamp);
    Collections.sort(intervals);
    long median = intervals.get((intervals.size() - 1) / 2);
    // Two distant observations do not establish continuous monitoring between them.
    return Math.min(90L * 60 * 1000, saturatedTriple(median));
  }

  private long saturatedTriple(long value) {
    return value > Long.MAX_VALUE / 3 ? Long.MAX_VALUE : value * 3;
  }

  private float x(long time) {
    return plot.left
        + plot.width() * (float) ((time - periodStart) / (double) (periodEnd - periodStart));
  }

  private float y(int value, int ceiling) {
    return plot.bottom - plot.height() * (value / (float) ceiling);
  }

  private int niceCeiling(int max) {
    if (max <= 4) return max;
    double magnitude = Math.pow(10, Math.floor(Math.log10(max)));
    double scaled = max / magnitude;
    double result = (scaled <= 2 ? 2 : scaled <= 5 ? 5 : 10) * magnitude;
    return (int) Math.min(Integer.MAX_VALUE, Math.max(max, result));
  }

  private void updateDescription() {
    String period =
        dateFormat.format(new Date(periodStart)) + " — " + dateFormat.format(new Date(periodEnd));
    if (points.isEmpty()) {
      setContentDescription("Доступность роутеров. " + period + ". " + emptyMessage);
    } else {
      Point last = points.get(points.size() - 1);
      setContentDescription(
          "Доступность роутеров. "
              + period
              + ". Последнее наблюдение: "
              + dateFormat.format(new Date(last.timestamp))
              + ". В сети "
              + last.online
              + " из "
              + last.total
              + ". Наблюдений: "
              + points.size()
              + ".");
    }
  }
}
