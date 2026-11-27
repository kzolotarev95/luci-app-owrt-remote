package ru.owrthub.app;

import android.content.*;
import android.graphics.*;
import android.os.*;
import android.provider.Settings;
import android.view.*;
import android.widget.*;
import java.util.*;
import java.util.function.Consumer;
import org.json.*;

/** Accessible native rows and a separate, vsync-driven layer for connection flow. */
final class NativeNetworkMap extends LinearLayout {
  private final NativeUi ui;
  private final ViewTreeObserver.OnScrollChangedListener scrollChanged = this::viewportChanged;
  private final LinearLayout hub, branches, body;
  private final Button collapse;
  private final FrameLayout diagram;
  private final WireLayer wires;
  private boolean expanded;

  NativeNetworkMap(
      Context context,
      NativeUi ui,
      List<JSONObject> routers,
      boolean expanded,
      boolean visible,
      Runnable toggleVisibility,
      Consumer<String> open,
      Consumer<Boolean> expand) {
    super(context);
    this.ui = ui;
    this.expanded = expanded;
    setTag("network-map");
    setOrientation(VERTICAL);
    setWillNotDraw(true);
    setPadding(ui.dp(14), ui.dp(14), ui.dp(14), ui.dp(12));
    setBackground(ui.shape(ui.PANEL, ui.LINE, 9));
    collapse = ui.panelToggle(visible, "Карта подключений", toggleVisibility);
    collapse.setTag("panel-toggle:map");
    addView(ui.panelHeading("map", "Карта подключений", "network", collapse));
    body = ui.column();
    body.setTag("panel-body:map");
    body.setVisibility(visible ? VISIBLE : GONE);
    addView(body);
    body.addView(ui.spacer(8));
    int online = 0;
    for (JSONObject row : routers) if (row.optBoolean("online")) online++;
    TextView totals = ui.text(online + " / " + routers.size() + " в сети", 11, false);
    totals.setTextColor(ui.GREEN);
    body.addView(totals, new LayoutParams(-1, -2));
    body.addView(ui.spacer(16));
    diagram = new FrameLayout(context);
    diagram.setLayoutParams(new LayoutParams(-1, -2));
    wires = new WireLayer(context);
    diagram.addView(wires, new FrameLayout.LayoutParams(-1, -1));
    LinearLayout nodes = ui.column();
    diagram.addView(nodes, new FrameLayout.LayoutParams(-1, -2));
    body.addView(diagram);
    hub = ui.row();
    hub.setGravity(Gravity.CENTER);
    hub.setPadding(ui.dp(12), ui.dp(10), ui.dp(12), ui.dp(10));
    hub.setBackground(ui.shape(NativeUi.alpha(ui.BLUE, .08f), NativeUi.alpha(ui.BLUE, .4f), 7));
    hub.addView(ui.logoBadge(32));
    LinearLayout copy = ui.column();
    copy.setPadding(ui.dp(10), 0, 0, 0);
    TextView server = ui.text("VPS · OWRT Hub", 14, true);
    server.setGravity(Gravity.START);
    copy.addView(server);
    copy.addView(ui.spacer(4));
    TextView hint = ui.text("Центр управления", 11, false);
    hint.setTextColor(ui.MUTED);
    hint.setGravity(Gravity.START);
    copy.addView(hint);
    hub.addView(copy, new LayoutParams(0, -2, 1));
    LayoutParams hp = new LayoutParams(-1, -2);
    hp.setMargins(ui.dp(24), 0, ui.dp(8), ui.dp(16));
    nodes.addView(hub, hp);
    branches = ui.column();
    LayoutParams bp = new LayoutParams(-1, -2);
    bp.setMargins(ui.dp(30), 0, 0, 0);
    nodes.addView(branches, bp);
    int limit = expanded ? routers.size() : Math.min(7, routers.size());
    for (int i = 0; i < limit; i++) {
      JSONObject router = routers.get(i);
      String id = router.optString("id"), name = router.optString("name", id);
      boolean on = router.optBoolean("online");
      LinearLayout row = ui.row();
      row.setTag("branch:" + id);
      row.setActivated(on);
      row.setPadding(ui.dp(8), ui.dp(8), ui.dp(8), ui.dp(8));
      row.setMinimumHeight(ui.dp(54));
      row.setBackground(ui.ripple(ui.PANEL2, ui.LINE, 7));
      row.addView(new RouterPhotoView(context, ui, router, 42));
      LinearLayout labels = ui.column();
      labels.setPadding(ui.dp(9), 0, ui.dp(5), 0);
      TextView n = ui.routerName(name, 13);
      n.setGravity(Gravity.START);
      labels.addView(n);
      labels.addView(ui.spacer(3));
      TextView ident = ui.text(id, 11, false);
      ident.setTextColor(ui.MUTED);
      ident.setGravity(Gravity.START);
      labels.addView(ident);
      row.addView(labels, new LayoutParams(0, -2, 1));
      TextView state = ui.text(on ? "Онлайн" : "Офлайн", 11, true);
      state.setTextColor(on ? ui.GREEN : ui.RED);
      row.addView(state, new LayoutParams(-2, -2));
      row.setContentDescription("Открыть роутер " + name + " · " + id);
      row.setFocusable(true);
      row.setOnClickListener(v -> open.accept(id));
      LayoutParams rp = new LayoutParams(-1, -2);
      rp.setMargins(0, 0, 0, ui.dp(7));
      branches.addView(row, rp);
    }
    if (routers.isEmpty()) {
      TextView empty = ui.body("Подключённые роутеры появятся здесь");
      empty.setPadding(0, ui.dp(12), 0, ui.dp(12));
      branches.addView(empty);
    }
    if (routers.size() > 7) {
      body.addView(ui.spacer(6));
      LinearLayout footer = ui.row();
      TextView count = ui.text("Показано " + limit + " из " + routers.size(), 11, false);
      count.setTextColor(ui.MUTED);
      count.setGravity(Gravity.START);
      footer.addView(count, new LayoutParams(0, -2, 1));
      Button all = ui.button(expanded ? "Скрыть все" : "Показать все", false, null);
      all.setActivated(expanded);
      all.setOnClickListener(v -> expand.accept(!v.isActivated()));
      footer.addView(all, new LayoutParams(-2, -2));
      body.addView(footer);
    }
  }

  void copyState(NativeNetworkMap other) {
    expanded = other.expanded;
    body.setVisibility(other.body.getVisibility());
    collapse.setText(other.collapse.getText());
    collapse.setContentDescription(other.collapse.getContentDescription());
    wires.geometryChanged();
    wires.refreshMotion();
    post(wires::refreshMotion);
  }

  private void viewportChanged() {
    wires.viewportChanged();
  }

  @Override
  protected void onLayout(boolean changed, int left, int top, int right, int bottom) {
    super.onLayout(changed, left, top, right, bottom);
    wires.geometryChanged();
  }

  @Override
  protected void onAttachedToWindow() {
    super.onAttachedToWindow();
    getViewTreeObserver().addOnScrollChangedListener(scrollChanged);
  }

  @Override
  protected void onDetachedFromWindow() {
    if (getViewTreeObserver().isAlive())
      getViewTreeObserver().removeOnScrollChangedListener(scrollChanged);
    super.onDetachedFromWindow();
  }

  @Override
  protected void onWindowVisibilityChanged(int visibility) {
    super.onWindowVisibilityChanged(visibility);
    if (wires != null) wires.refreshMotion();
  }

  private static final class Connection {
    final View row;
    final Path path = new Path();
    final PathMeasure measure = new PathMeasure();
    final RectF area = new RectF();
    float length;

    Connection(View row) {
      this.row = row;
    }
  }

  /** Only this leaf view is invalidated per frame; text and photographs stay cached. */
  private final class WireLayer extends View {
    private final Paint ink = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Rect bounds = new Rect(), viewport = new Rect();
    private final Path trunk = new Path();
    private final PathMeasure trunkMeasure = new PathMeasure();
    private final float[] point = new float[2];
    private final List<Connection> connections = new ArrayList<>();
    private final PowerManager power;
    private boolean dirty = true, motionAllowed, hasOnline, frameScheduled;
    private float trunkLength, curveLength, curveEndY;
    // Used by on-device regression checks to observe the actual drawing loop.
    private long animatedFrameCount;
    private int lastAnimatedBranchIndex = -1;
    private final Runnable frame =
        () -> {
          frameScheduled = false;
          if (motionAllowed
              && isShown()
              && getWindowVisibility() == VISIBLE
              && getLocalVisibleRect(viewport)) invalidate();
        };

    WireLayer(Context context) {
      super(context);
      setTag("network-map-wires");
      setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
      power = (PowerManager) context.getSystemService(Context.POWER_SERVICE);
      ink.setStrokeCap(Paint.Cap.ROUND);
      ink.setStrokeJoin(Paint.Join.ROUND);
    }

    void geometryChanged() {
      dirty = true;
      invalidate();
    }

    void refreshMotion() {
      motionAllowed =
          !getContext().getSharedPreferences("native_design", 0).getBoolean("lite", false)
              && Settings.Global.getFloat(
                      getContext().getContentResolver(), Settings.Global.ANIMATOR_DURATION_SCALE, 1)
                  > 0
              && (power == null || !power.isPowerSaveMode());
      viewportChanged();
    }

    void viewportChanged() {
      if (isAttachedToWindow()
          && isShown()
          && getWindowVisibility() == VISIBLE
          && getLocalVisibleRect(viewport)) invalidate();
      else stopFrames();
    }

    private void stopFrames() {
      removeCallbacks(frame);
      frameScheduled = false;
    }

    @Override
    protected void onAttachedToWindow() {
      super.onAttachedToWindow();
      refreshMotion();
    }

    @Override
    protected void onDetachedFromWindow() {
      stopFrames();
      super.onDetachedFromWindow();
    }

    @Override
    protected void onWindowVisibilityChanged(int visibility) {
      super.onWindowVisibilityChanged(visibility);
      if (visibility != VISIBLE) stopFrames();
      else if (power != null) refreshMotion();
    }

    @Override
    public void onVisibilityAggregated(boolean visible) {
      super.onVisibilityAggregated(visible);
      if (visible) refreshMotion();
      else stopFrames();
    }

    private void rebuildGeometry() {
      dirty = false;
      connections.clear();
      trunk.reset();
      hasOnline = false;
      trunkLength = 0;
      if (hub.getWidth() == 0 || branches.getChildCount() == 0) return;
      bounds.set(0, 0, hub.getWidth(), hub.getHeight());
      diagram.offsetDescendantRectToMyCoords(hub, bounds);
      float startX = bounds.centerX(), startY = bounds.bottom, spine = ui.dp(8);
      curveEndY = startY + ui.dp(18);
      trunk.moveTo(startX, startY);
      trunk.cubicTo(startX, startY + ui.dp(10), spine, startY + ui.dp(7), spine, curveEndY);
      trunkMeasure.setPath(trunk, false);
      curveLength = trunkMeasure.getLength();
      float endY = curveEndY;
      for (int i = 0; i < branches.getChildCount(); i++) {
        View row = branches.getChildAt(i);
        if (row.getTag() == null) continue;
        bounds.set(0, 0, row.getWidth(), row.getHeight());
        diagram.offsetDescendantRectToMyCoords(row, bounds);
        float x = bounds.left, y = bounds.centerY();
        Connection connection = new Connection(row);
        connection.path.moveTo(spine, y - ui.dp(7));
        connection.path.quadTo(spine, y, x, y);
        connection.measure.setPath(connection.path, false);
        connection.length = connection.measure.getLength();
        connection.area.set(spine - ui.dp(3), y - ui.dp(10), x + ui.dp(3), y + ui.dp(4));
        connections.add(connection);
        hasOnline |= row.isActivated();
        endY = y;
      }
      trunk.lineTo(spine, endY);
      trunkMeasure.setPath(trunk, false);
      trunkLength = trunkMeasure.getLength();
    }

    @Override
    protected void onDraw(Canvas canvas) {
      super.onDraw(canvas);
      if (!getLocalVisibleRect(viewport)) {
        stopFrames();
        return;
      }
      if (dirty) rebuildGeometry();
      boolean animate = motionAllowed && hasOnline && isShown() && getWindowVisibility() == VISIBLE;
      long now = SystemClock.uptimeMillis();
      ink.setStyle(Paint.Style.STROKE);
      ink.setStrokeWidth(ui.dp(1));
      ink.setColor(ui.LINE);
      canvas.drawPath(trunk, ink);
      if (animate && trunkLength > 0) {
        float spacing = ui.dp(64), phase = (now % 1000000) * ui.dp(45) / 1000f % spacing;
        float low =
            viewport.top <= curveEndY ? 0 : curveLength + viewport.top - curveEndY - spacing;
        float high =
            Math.min(trunkLength, curveLength + Math.max(0, viewport.bottom - curveEndY) + spacing);
        float start = Math.max(0, (float) Math.floor(low / spacing) * spacing);
        ink.setStyle(Paint.Style.FILL);
        ink.setColor(ui.BLUE);
        for (float d = start + phase; d < high; d += spacing) {
          if (trunkMeasure.getPosTan(d, point, null))
            canvas.drawCircle(point[0], point[1], ui.dp(1.5f), ink);
        }
      }
      lastAnimatedBranchIndex = -1;
      for (int i = 0; i < connections.size(); i++) {
        Connection connection = connections.get(i);
        if (connection.area.bottom < viewport.top || connection.area.top > viewport.bottom)
          continue;
        boolean online = connection.row.isActivated();
        ink.setStyle(Paint.Style.STROKE);
        ink.setStrokeWidth(ui.dp(1));
        ink.setColor(NativeUi.alpha(online ? ui.GREEN : ui.RED, .45f));
        canvas.drawPath(connection.path, ink);
        if (animate && online && connection.length > 0) {
          float fraction = ((now + i * 311) % 1100) / 1100f;
          connection.measure.getPosTan(connection.length * fraction, point, null);
          ink.setStyle(Paint.Style.FILL);
          ink.setColor(ui.BLUE);
          canvas.drawCircle(point[0], point[1], ui.dp(1.8f), ink);
          lastAnimatedBranchIndex = i;
        }
      }
      if (animate) {
        animatedFrameCount++;
        if (!frameScheduled) {
          frameScheduled = true;
          postOnAnimation(frame);
        }
      } else stopFrames();
    }
  }
}
