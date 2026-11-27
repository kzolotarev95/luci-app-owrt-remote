package ru.owrthub.app;

import android.app.UiAutomation;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.graphics.Rect;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.View;
import android.view.accessibility.AccessibilityNodeInfo;
import android.widget.ScrollView;
import java.io.File;
import java.io.FileOutputStream;
import java.lang.reflect.Field;

/** Real device layout and state checks against native_server.py --showcase only. */
public final class NativeRedesignInstrumentation extends NativeSmokeInstrumentation {
  private boolean orderOnly;
  private boolean routerNameOnly;
  private boolean deviceLayoutOnly;
  private boolean brightRedOnly;
  private boolean modelCatalogOnly;
  @Override public void onCreate(Bundle arguments) {
    orderOnly = arguments != null && "true".equals(arguments.getString("routerOrderOnly"));
    routerNameOnly = arguments != null && "true".equals(arguments.getString("routerNameOnly"));
    deviceLayoutOnly = arguments != null && "true".equals(arguments.getString("deviceLayoutOnly"));
    brightRedOnly = arguments != null && "true".equals(arguments.getString("brightRedOnly"));
    modelCatalogOnly = arguments != null && "true".equals(arguments.getString("modelCatalogOnly"));
    super.onCreate(arguments);
  }
  @Override
  public void onStart() {
    Bundle result = new Bundle();
    try {
      getUiAutomation().setRotation(UiAutomation.ROTATION_FREEZE_0);
      HubNotifications.prefs(getTargetContext())
          .edit()
          .putBoolean("offered", true)
          .putBoolean("enabled", false)
          .commit();
      new HubSessionStore(getTargetContext()).clear();
      getTargetContext().getSharedPreferences("hub_connection", 0).edit().clear().commit();
      getTargetContext().getSharedPreferences("native_design", 0).edit().clear().commit();
      launch(false);
      waitFor("connection", 10000, () -> shown("hub_host"));
      if (modelCatalogOnly) {
        onUi(() -> {
          NativeUi palette = new NativeUi(getTargetContext(), false);
          for (String model : new String[] {"Netcore N60 Pro", "Routerich AX3000 v1.0",
              "Routerich AX3000 v2", "Routerich AX3000 v3", "Routerich AX3000 v12.3"}) {
            org.json.JSONObject status = new org.json.JSONObject().put("model", model);
            org.json.JSONObject row = new org.json.JSONObject().put("status", status);
            RouterPhotoView photo = new RouterPhotoView(getTargetContext(), palette, row, 88);
            require(field(photo, "image") != null, "Decoded catalog image: " + model);
            require(field(photo, "file").equals(model.startsWith("Netcore") ? "netis-nx62.webp" : "routerich-ax3000.webp"), "Correct image: " + model);
            require(status.getString("model").equals(model), "Reported model remains intact");
            require(RouterPhotoView.displayModel(row).equals(model.startsWith("Netcore") ? "Netcore N60 Pro (Netis NX62)" : model), "Correct model label");
          }
          for (String model : new String[] {"Routerich AX3000 Pro v2", "Routerich AX3000 v2 Pro", "Routerich AX30000 v2", "Cudy WBR3000UAX v10"}) {
            org.json.JSONObject row = new org.json.JSONObject().put("status", new org.json.JSONObject().put("model", model));
            RouterPhotoView photo = new RouterPhotoView(getTargetContext(), palette, row, 88);
            require(field(photo, "image") == null, "Different hardware keeps fallback: " + model);
          }
          return true;
        });
        result.putString("status", "passed");
        result.putString("results", "netcore_alias_label_routerich_revisions_and_scoped_fallback");
        finish(0, result); return;
      }
      assertFullscreen();
      assertNative("connect");
      assertHorizontalBounds();
      capture("01-connect");
      fill("hub_host", fixture);
      clickId("hub_connect");
      waitFor("challenge", 15000, () -> hasText("2468") && shown("hub_login"));
      fill("hub_user", USERNAME);
      waitFor("OTP", 5000, () -> shown("hub_otp"));
      require(!hasText("Адрес скрыт"), "Login omits the duplicate address bar");
      assertCentered(tag("welcome-logo"), activity.getWindow().getDecorView());
      require(onUi(() -> {
        View logo = tag("welcome-logo");
        for (View view : views())
          if (view instanceof android.widget.TextView
              && "С возвращением".contentEquals(((android.widget.TextView) view).getText()))
            return screenRect(logo).bottom <= screenRect(view).top;
        return false;
      }), "Welcome title follows the centred logo");
      checks.add("welcome_logo_centered_above_title_without_private_address_bar");
      capture("02-login");
      fillLogin("2468");
      clickId("hub_login");
      waitForNativeHome();
      waitFor("nine fixture routers", 15000, () -> tag("network-map") != null && hasText("9"));
      if (brightRedOnly) {
        onUi(() -> {
          for (boolean winter : new boolean[] {false, true}) {
            NativeUi palette = new NativeUi(getTargetContext(), winter);
            require(palette.RED == Color.rgb(255, 48, 48), "One bright red in both palettes");
            android.widget.Button danger = palette.button("Проверка", NativeUi.Style.DANGER, null);
            require(danger.getCurrentTextColor() == Color.rgb(255, 48, 48), "Danger button uses bright red");
          }
          return true;
        });
        checkRouterNameRendering();
        scrollTo(tag("network-map"));
        assertBrightOfflineText();
        capture("04-bright-red-map");
        clickText("Роутеры"); clickText("Офлайн");
        waitFor("offline card", 5000, () -> tag("router:offline") != null);
        scrollTo(tag("router:offline"));
        assertBrightOfflineText();
        capture("06-bright-red-offline");
        checks.add("bright_red_native_palettes_danger_offline_and_gradient");
        result.putString("status", "passed");
        result.putString("results", String.join(",", checks));
        finish(0, result); return;
      }
      if (deviceLayoutOnly) {
        checkDeviceLayoutAndHeaders();
        result.putString("status", "passed");
        result.putString("results", String.join(",", checks));
        finish(0, result); return;
      }
      if (routerNameOnly) {
        checkRouterNameRendering();
        capture("04-gradient-map");
        clickText("Роутеры");
        waitFor("gradient router card", 10000, () -> tag("router-name:main") != null);
        scrollTo(tag("router:main"));
        View original = tag("router-name:main");
        require(original instanceof RouterNameView, "Router card uses the native gradient TextView");
        capture("06-gradient-routers");
        require(onUi(() -> ((android.widget.TextView) original).getPaint().getShader()
            instanceof android.graphics.LinearGradient), "Attached router name has a rendered gradient");
        SystemClock.sleep(6500);
        require(tag("router-name:main") == original, "Status polling preserves the attached name View");
        assertHorizontalBounds();
        clickText("Основа");
        waitFor("router details", 5000, () -> hasText("Настройки Xray"));
        scrollTop();
        capture("07-gradient-detail");
        require(onUi(() -> {
          for (View view : views()) if (view instanceof RouterNameView
              && "Основа".contentEquals(((android.widget.TextView) view).getText())) return true;
          return false;
        }), "Router detail preserves the same gradient style");
        checks.add("router_names_gradient_live_refresh_long_names_resize_and_detail");
        result.putString("status", "passed");
        result.putString("results", String.join(",", checks));
        finish(0, result); return;
      }
      if (orderOnly) {
        clickText("Роутеры");
        waitFor("model cards", 10000, () -> tag("router:main") != null);
        checkRouterOrdering();
        result.putString("status", "passed");
        result.putString("results", String.join(",", checks));
        finish(0, result); return;
      }
      assertNative("dashboard");
      require(backgroundColor() == Color.rgb(13, 14, 16), "Graphite palette matches PC");
      require(
          hasText("ЦП VPS") && hasText("ОЗУ VPS") && hasText("Диск VPS"),
          "Native resources and disk banner");
      require(
          onUi(
                  () -> {
                    for (View view : views())
                      if (view.getTag() instanceof String
                          && ((String) view.getTag()).startsWith("router:")) return false;
                    return true;
                  })
              && !hasText("Все роутеры"),
          "Overview contains the map and resources, without a duplicate router-card list");
      checks.add("overview_does_not_duplicate_router_cards");
      assertDashboardLayout();
      checks.add("compact_dashboard_centered_headings_and_disk_values");
      View availabilityChart = tag("availability-chart");
      for (final int hours : new int[] {6, 12, 24, 6}) {
        scrollTo(tag("availability-period:" + hours));
        onUi(() -> tag("availability-period:" + hours).performClick());
        require(tag("availability-chart") == availabilityChart, "Changing the period patches the attached chart");
        require(onUi(() -> ((Number) field(availabilityChart, "periodEnd")).longValue()
            - ((Number) field(availabilityChart, "periodStart")).longValue()) == hours * 3600000L,
            "Real chart window is " + hours + " hours");
        require(onUi(() -> tag("availability-period:" + hours).isActivated()), "Selected period has active appearance");
      }
      SystemClock.sleep(6500);
      require(onUi(() -> ((Number) field(availabilityChart, "periodEnd")).longValue()
          - ((Number) field(availabilityChart, "periodStart")).longValue()) == 21600000L,
          "Polling preserves six-hour period");
      capture("03a-availability-6h");
      assertFullscreen();
      checks.add("availability_6_12_24_hours_patch_without_flicker");
      capture("03-dashboard");
      scrollTo(tag("disk-banner"));
      capture("03b-disk");
      checks.add("native_graphite_login_dashboard_resources");
      require(branches() == 7, "Connection map initially limits the complete list to seven");
      View map = tag("network-map");
      scrollTo(map);
      capture("04-map");
      View renderer = tag("network-map-wires");
      require(onUi(() -> renderer.getGlobalVisibleRect(new Rect())), "Animation renderer is on screen before sampling");
      long beforeFrames = frameCount(renderer);
      SystemClock.sleep(600);
      long deliveredFrames = frameCount(renderer) - beforeFrames;
      result.putLong("animation_frames_600ms", deliveredFrames);
      require(
          deliveredFrames >= 3, "Visible native wire layer continues delivering animation frames");
      checks.add("map_visible_animation_delivers_frames");
      SystemClock.sleep(6500);
      require(
          tag("network-map") == map && branches() == 7,
          "Refresh patches the attached map in place");
      checks.add("map_seven_routers_stable_during_refresh");
      clickText("Показать все");
      waitFor("expanded map", 4000, () -> branches() == 9);
      View ninthRouter = tag("branch:node-8");
      scrollTo(ninthRouter);
      waitFor(
          "visible connection beyond seven animates",
          5000,
          () -> onUi(() -> ((Number) field(renderer, "lastAnimatedBranchIndex")).intValue() >= 7));
      require(
          connectionPixelsChange(ninthRouter),
          "Pixels on the newly revealed connection move between presented frames");
      capture("04b-expanded-map");
      checks.add("expanded_map_animates_visible_connections_after_seven");
      SystemClock.sleep(6500);
      require(branches() == 9, "Polling preserves expansion");
      scrollTo(ninthRouter);
      waitFor(
          "expanded connection survives refresh",
          5000,
          () -> onUi(() -> ((Number) field(renderer, "lastAnimatedBranchIndex")).intValue() >= 7));
      clickText("Скрыть все");
      waitFor("collapsed map", 4000, () -> branches() == 7);
      checks.add("map_show_all_hide_all_and_refresh_persistence");
      clickDescription("Открыть меню Hub");
      clickWindowText("Управление  ⌄");
      require(windowHas("Терминал VPS"), "Management section exposes native VPS tools");
      capture("05-menu");
      clickWindowText("Рабочее пространство  ⌃");
      clickWindowText("Управление  ⌃");
      capture("05b-community-menu");
      clickWindowText("Управление  ⌄");
      clickDescriptionInWindow("Закрыть меню");
      clickDescription("Открыть меню Hub");
      require(
          windowHas("Управление  ⌃") && windowHas("Рабочее пространство  ⌄"),
          "Menu sections survive reopening");
      clickDescriptionInWindow("Закрыть меню");
      checks.add("native_drawer_sections_remember_selection");
      clickText("Роутеры");
      SystemClock.sleep(200);
      long detachedFrames = frameCount(renderer);
      SystemClock.sleep(350);
      require(
          onUi(() -> !renderer.isAttachedToWindow()) && frameCount(renderer) == detachedFrames,
          "Leaving the overview stops its animation callbacks");
      checks.add("map_animation_stops_when_detached");
      waitFor("model cards", 10000, () -> tag("router:main") != null);
      scrollTo(tag("router:main"));
      assertHorizontalBounds();
      capture("06-routers");
      require(
          onUi(
              () -> {
                for (View view : views())
                  if (view instanceof RouterPhotoView && field(view, "image") != null) return true;
                return false;
              }),
          "Actual model photograph loaded from APK");
      checks.add("local_model_photographs_and_compact_router_cards");
      checkRouterOrdering();
      clickText("Офлайн");
      waitFor(
          "offline filter",
          5000,
          () -> tag("router:main") == null && tag("router:offline") != null);
      clickText("Обзор");
      waitFor("complete map", 5000, () -> branches() == 7);
      checks.add("connection_map_independent_of_router_filters");
      clickText("Роутеры");
      clickText("Все");
      clickText("Основа");
      waitFor("detail", 5000, () -> hasText("Настройки Xray"));
      scrollTop();
      assertCenteredRouter();
      assertHorizontalBounds();
      capture("07-router-detail");
      checks.add("centered_native_model_detail");
      clickText("Настройки");
      scrollTop();
      capture("08-settings");
      clickText("Скрыть статистику");
      clickText("Обзор");
      require(onUi(() -> tag("dashboard-statistics").getVisibility() == View.GONE), "Stats hidden");
      relaunch();
      waitForNativeHome();
      waitFor("map restored", 10000, () -> tag("network-map") != null);
      require(onUi(() -> tag("availability-period:6").isActivated()), "Availability period survives relaunch");
      assertFullscreen();
      checks.add("availability_period_survives_relaunch_and_immersive_fullscreen");
      require(
          onUi(() -> tag("dashboard-statistics").getVisibility() == View.GONE),
          "Stats preference survives relaunch");
      clickText("Настройки");
      clickText("Открыть статистику");
      clickText("Обзор");
      checks.add("dashboard_statistics_visibility_survives_relaunch");
      require(tag("heading-icon:map") != null && tag("heading-icon:availability") != null, "Both dashboard headings have icons");
      for (String key : new String[] {"map", "availability", "disk"}) {
        scrollTo(tag("panel-toggle:" + key));
        onUi(() -> tag("panel-toggle:" + key).performClick());
        require(onUi(() -> tag("panel-body:" + key).getVisibility() == View.GONE), "Panel collapses: " + key);
      }
      View collapsedWires = tag("network-map-wires");
      SystemClock.sleep(200);
      long collapsedFrames = frameCount(collapsedWires);
      SystemClock.sleep(350);
      require(frameCount(collapsedWires) == collapsedFrames, "Collapsed map does not schedule animation frames");
      relaunch();
      waitForNativeHome();
      waitFor("collapsed dashboard restored", 10000, () -> tag("panel-toggle:map") != null);
      for (String key : new String[] {"map", "availability", "disk"}) {
        require(onUi(() -> tag("panel-body:" + key).getVisibility() == View.GONE), "Panel preference survives relaunch: " + key);
        scrollTo(tag("panel-toggle:" + key));
        onUi(() -> tag("panel-toggle:" + key).performClick());
        require(onUi(() -> tag("panel-body:" + key).getVisibility() == View.VISIBLE), "Panel reopens: " + key);
      }
      checks.add("dashboard_panels_and_heading_icons_survive_relaunch");
      clickText("Настройки");
      openSettingsTool("Поддержать разработку");
      waitFor("support banner", 5000, () -> containsText("Основная ссылка"));
      scrollTo(tag("support-banks"));
      require(onUi(() -> {
        android.view.ViewGroup banks = (android.view.ViewGroup) tag("support-banks");
        if (banks.getChildCount() != 5) return false;
        Rect first = screenRect(banks.getChildAt(0));
        for (int i = 0; i < 5; i++) {
          android.view.ViewGroup brand = (android.view.ViewGroup) banks.getChildAt(i);
          Rect box = screenRect(brand);
          if (box.top != first.top || box.bottom != first.bottom) return false;
          android.widget.TextView name = (android.widget.TextView) brand.getChildAt(2);
          if (name.getLineCount() != 1 || name.getLayout().getEllipsisCount(0) > 0) return false;
        }
        return true;
      }), "All five bank marks and names share one aligned row");
      assertHorizontalBounds();
      capture("09-support");
      backToMain();
      checks.add("support_opens_native_banner");
      openTool("ru.owrthub.app.NativeRouterActivity", "Терминал VPS");
      waitFor("VPS terminal", 5000, () -> hasText("Открыть терминал VPS"));
      require(!hasText("Адрес скрыт"), "VPS terminal does not show the hidden-address placeholder");
      require(onUi(() -> {
        for (View view : views())
          if (view.getContentDescription() != null
              && "Показать или скрыть адрес Hub".contentEquals(view.getContentDescription())) return false;
        return true;
      }), "VPS terminal has no address-eye control");
      capture("09b-vps-terminal");
      backToMain();
      checks.add("vps_terminal_omits_address_and_eye");
      clickText("Оформление");
      waitFor("design choices", 3000, () -> hasText("Для слабых устройств"));
      capture("10-design");
      clickText("Для слабых устройств");
      require(
          getTargetContext().getSharedPreferences("native_design", 0).getBoolean("lite", false),
          "Light design preference saved");
      checks.add("light_mode_preference");
      sendKeyDownUpSync(android.view.KeyEvent.KEYCODE_BACK);
      waitForIdleSync();
      clickText("Роутеры");
      clickText("Все");
      checkLandscapeOverview();
      assertHorizontalBounds();
      checks.add("router_list_landscape_bounds");
      clickText("Обзор");
      scrollTop();
      View staticRenderer = tag("network-map-wires");
      long staticFrames = frameCount(staticRenderer);
      SystemClock.sleep(350);
      require(
          frameCount(staticRenderer) == staticFrames,
          "Light design does not schedule animation frames");
      checks.add("light_mode_map_does_not_animate");
      capture("11-final-dashboard");
      result.putString("status", "passed");
      result.putInt("checks", checks.size());
      result.putString("results", String.join(",", checks));
      finish(0, result);
    } catch (Throwable failure) {
      result.putString("status", "failed");
      result.putString("error", failure.toString());
      result.putString("labels", visibleLabels());
      result.putString("results", String.join(",", checks));
      failure.printStackTrace();
      finish(1, result);
    }
  }

  private Object field(Object owner, String name) throws Exception {
    Field f = owner.getClass().getDeclaredField(name);
    f.setAccessible(true);
    return f.get(owner);
  }

  private void checkRouterOrdering() throws Exception {
    java.util.List<String> originalOrder = routerIds();
    int originalIndex = originalOrder.indexOf("main");
    require(originalIndex > 0, "Fixture main has a previous peer");
    String previousId = originalOrder.get(originalIndex - 1);
    onUi(() -> tag("router-order:main:-1").performClick());
    waitFor("arrow order saved", 10000, () -> routerIds().indexOf("main") < routerIds().indexOf(previousId));
    java.util.List<String> savedOrder = routerIds();
    SystemClock.sleep(6500);
    require(routerIds().equals(savedOrder), "Polling preserves saved card positions");
    relaunch(); waitForNativeHome();
    waitFor("card order from Hub", 10000, () -> tag("network-map") != null);
    clickText("Роутеры");
    require(routerIds().equals(savedOrder), "New Activity restores shared order from Hub");
    checks.add("router_arrow_order_persists_after_poll_and_relaunch");
    scrollTo(tag("router:main"));
    dragCard("main", previousId);
    waitFor("drag order saved", 10000, () -> routerIds().indexOf("main") > routerIds().indexOf(previousId));
    require(routerIds().equals(originalOrder), "Native drag changes the same saved order as arrows");
    capture("06b-router-order");
    checks.add("native_handle_long_press_drag_and_drop");
  }

  private java.util.List<String> routerIds() {
    return onUi(() -> {
      java.util.List<String> ids = new java.util.ArrayList<>();
      for (View view : views())
        if (view.getTag() instanceof String && ((String) view.getTag()).startsWith("router:"))
          ids.add(((String) view.getTag()).substring(7));
      return ids;
    });
  }

  private void dragCard(String id, String target) throws Exception {
    capture("06a-order-before-drag");
    Rect source = onUi(() -> screenRect(tag("router-order:" + id + ":0")));
    Rect destination = onUi(() -> screenRect(tag("router:" + target)));
    require(onUi(() -> tag("router-order:" + id + ":0").getGlobalVisibleRect(new Rect())), "Drag source is visible: " + source);
    long started = SystemClock.uptimeMillis();
    float x = source.exactCenterX(), y = source.exactCenterY();
    try {
      touch(started, android.view.MotionEvent.ACTION_DOWN, x, y);
      waitFor("handle starts native drag", 4000, () -> onUi(() -> (Boolean) field(mainActivity, "routerDragging")));
      Rect scroll = onUi(() -> {
        for (View view : views()) if (view instanceof ScrollView) return screenRect(view);
        throw new AssertionError("Router scroll view");
      });
      if (destination.top + destination.height() * .7f > scroll.bottom - 70 * activity.getResources().getDisplayMetrics().density) {
        touch(started, android.view.MotionEvent.ACTION_MOVE, x, scroll.bottom - 12);
        waitFor("drag autoscroll reveals target", 8000, () -> onUi(() -> {
          Rect now = screenRect(tag("router:" + target));
          return now.top + now.height() * .7f < scroll.bottom - 90 * activity.getResources().getDisplayMetrics().density;
        }));
        touch(started, android.view.MotionEvent.ACTION_MOVE, x, scroll.exactCenterY());
        SystemClock.sleep(80);
        destination = onUi(() -> screenRect(tag("router:" + target)));
        y = scroll.exactCenterY();
      }
      float endX = destination.exactCenterX(), endY = destination.top + destination.height() * .7f;
      for (int i = 1; i <= 14; i++) {
        touch(started, android.view.MotionEvent.ACTION_MOVE, x + (endX-x)*i/14f, y + (endY-y)*i/14f);
        SystemClock.sleep(25);
      }
      touch(started, android.view.MotionEvent.ACTION_UP, endX, endY);
      waitForIdleSync();
    } finally { touch(started, android.view.MotionEvent.ACTION_CANCEL, x, y); }
  }

  private void touch(long started, int action, float x, float y) {
    android.view.MotionEvent.PointerProperties pointer = new android.view.MotionEvent.PointerProperties();
    pointer.id = 0; pointer.toolType = android.view.MotionEvent.TOOL_TYPE_FINGER;
    android.view.MotionEvent.PointerCoords coordinates = new android.view.MotionEvent.PointerCoords();
    coordinates.x = x; coordinates.y = y; coordinates.pressure = action == android.view.MotionEvent.ACTION_UP ? 0 : 1; coordinates.size = 1;
    android.view.MotionEvent event = android.view.MotionEvent.obtain(started, SystemClock.uptimeMillis(), action, 1,
        new android.view.MotionEvent.PointerProperties[] {pointer}, new android.view.MotionEvent.PointerCoords[] {coordinates},
        0, 0, 1, 1, 0, 0, android.view.InputDevice.SOURCE_TOUCHSCREEN, 0);
    try {
      boolean delivered = getUiAutomation().injectInputEvent(event, true);
      // After a successful UP there is no live gesture left to cancel.
      if (action != android.view.MotionEvent.ACTION_CANCEL) require(delivered, "Touch event delivered: " + action);
    }
    finally { event.recycle(); }
  }

  private Rect screenRect(View view) {
    int[] xy = new int[2];
    view.getLocationOnScreen(xy);
    return new Rect(xy[0], xy[1], xy[0] + view.getWidth(), xy[1] + view.getHeight());
  }

  private void assertCentered(View view, View parent) {
    require(onUi(() -> Math.abs(screenRect(view).exactCenterX() - screenRect(parent).exactCenterX()) < 3),
        "View centred: " + view.getTag());
  }

  private void assertDashboardLayout() {
    onUi(() -> {
      float dp = activity.getResources().getDisplayMetrics().density;
      for (String key : new String[] {"map", "availability", "disk"}) {
        View label = tag("panel-label:" + key), icon = tag("heading-icon:" + key);
        View parent = (View) tag("panel-heading:" + key).getParent();
        Rect text = screenRect(label), mark = screenRect(icon), frame = screenRect(parent);
        require(Math.abs((mark.left + text.right) / 2f - frame.exactCenterX()) < 3,
            "Icon and title centred independently of toggle: " + key);
        Rect action = screenRect(tag("panel-toggle:" + key));
        require(text.right + 2 <= action.left || text.bottom + 2 <= action.top,
            "Title does not overlap action: " + key);
        require(((android.widget.TextView) label).getLineCount() == 1,
            "Heading remains readable without fragmented words: " + key);
      }
      for (View view : views())
        if (view.getTag() instanceof String && ((String) view.getTag()).startsWith("dashboard-metric:"))
          require(view.getHeight() <= 86 * dp, "Compact statistic card: " + view.getTag());
      android.view.ViewGroup values = (android.view.ViewGroup) tag("disk-values");
      Rect first = screenRect(values.getChildAt(0)), last = screenRect(values.getChildAt(2));
      require(Math.abs((first.left + last.right) / 2f - screenRect(values).exactCenterX()) < 3,
          "Disk values form one centred group");
      for (int i = 0; i < 3; i++) {
        android.view.ViewGroup column = (android.view.ViewGroup) values.getChildAt(i);
        Rect key = screenRect(column.getChildAt(0)), value = screenRect(column.getChildAt(2));
        require(value.top - key.bottom <= 5 * dp, "Disk label and value stay close");
        if (i > 0) require(screenRect(column).left - screenRect(values.getChildAt(i - 1)).right <= dp,
            "No stretched space between disk metrics");
      }
      return null;
    });
  }

  private void assertFullscreen() throws Exception {
    waitFor("system bars hidden", 5000, () -> onUi(() -> {
      android.view.WindowInsets insets = activity.getWindow().getDecorView().getRootWindowInsets();
      return insets != null && !insets.isVisible(android.view.WindowInsets.Type.statusBars())
          && !insets.isVisible(android.view.WindowInsets.Type.navigationBars());
    }));
  }

  private long frameCount(View renderer) {
    return onUi(() -> ((Number) field(renderer, "animatedFrameCount")).longValue());
  }

  private boolean connectionPixelsChange(View router) {
    Rect area =
        onUi(
            () -> {
              int[] position = new int[2];
              router.getLocationOnScreen(position);
              float density = activity.getResources().getDisplayMetrics().density;
              int middle = position[1] + router.getHeight() / 2;
              return new Rect(
                  Math.max(0, position[0] - (int) (30 * density)),
                  Math.max(0, middle - (int) (12 * density)),
                  position[0],
                  middle + (int) (12 * density));
            });
    Bitmap first = getUiAutomation().takeScreenshot();
    require(first != null, "Presented animation frame");
    try {
      // Sample a complete pulse cycle; two frames can both catch the dot
      // outside this short connection's visible crop after a fullscreen resize.
      for (int sample = 0; sample < 5; sample++) {
        SystemClock.sleep(240);
        Bitmap second = getUiAutomation().takeScreenshot();
        require(second != null, "Presented animation frame");
        try {
          int changed = 0;
          for (int y = area.top; y < Math.min(area.bottom, first.getHeight()); y++)
            for (int x = area.left; x < Math.min(area.right, first.getWidth()); x++)
              if (first.getPixel(x, y) != second.getPixel(x, y)) changed++;
          if (changed > 3) return true;
        } finally { second.recycle(); }
      }
      return false;
    } finally {
      first.recycle();
    }
  }

  private View tag(String name) {
    if (android.os.Looper.myLooper() == android.os.Looper.getMainLooper()) return findTag(name);
    return onUi(() -> findTag(name));
  }

  private View findTag(String name) {
    for (View v : views()) if (name.equals(v.getTag())) return v;
    return null;
  }

  private int branches() {
    return onUi(
        () -> {
          int n = 0;
          for (View v : views())
            if (v.getTag() instanceof String && ((String) v.getTag()).startsWith("branch:")) n++;
          return n;
        });
  }

  private void scrollTop() {
    onUi(
        () -> {
          for (View v : views())
            if (v instanceof ScrollView) {
              ((ScrollView) v).scrollTo(0, 0);
              break;
            }
          return null;
        });
    SystemClock.sleep(400);
  }

  private void scrollTo(View target) {
    onUi(
        () -> {
          for (View v : views())
            if (v instanceof ScrollView) {
              ScrollView s = (ScrollView) v;
              Rect r = new Rect(0, 0, target.getWidth(), target.getHeight());
              s.offsetDescendantRectToMyCoords(target, r);
                s.scrollTo(0, r.top - 8);
              break;
            }
          return null;
        });
    SystemClock.sleep(400);
  }

  private void clickDescription(String value) {
    onUi(
        () -> {
          for (View v : views())
            if (v.getContentDescription() != null
                && value.contentEquals(v.getContentDescription())) {
              clickOnUi(v);
              return null;
            }
          throw new AssertionError("Missing " + value);
        });
    waitForIdleSync();
  }

  private AccessibilityNodeInfo node(String value, boolean description) {
    AccessibilityNodeInfo root = getUiAutomation().getRootInActiveWindow();
    return find(root, value, description);
  }

  private AccessibilityNodeInfo find(
      AccessibilityNodeInfo item, String value, boolean description) {
    if (item == null) return null;
    CharSequence label = description ? item.getContentDescription() : item.getText();
    if (label != null && value.contentEquals(label) && item.isVisibleToUser()) return item;
    for (int i = 0; i < item.getChildCount(); i++) {
      AccessibilityNodeInfo found = find(item.getChild(i), value, description);
      if (found != null) return found;
    }
    return null;
  }

  private boolean windowHas(String value) {
    return node(value, false) != null;
  }

  private void clickWindowText(String value) {
    windowClick(value, false);
  }

  private void assertBrightOfflineText() {
    onUi(() -> {
      int count = 0;
      for (View view : views()) if (view instanceof android.widget.TextView && !(view instanceof android.widget.Button)) {
        android.widget.TextView text = (android.widget.TextView) view;
        if (text.getText().toString().contains("Офлайн")) {
          require(text.getCurrentTextColor() == Color.rgb(255, 48, 48), "Offline text is bright red");
          count++;
        }
      }
      require(count > 0, "Offline state is visible in the native fixture");
      return true;
    });
  }

  private void checkDeviceLayoutAndHeaders() throws Exception {
    assertCleanToolbar();
    clickText("Роутеры"); clickText("Основа");
    waitFor("device stats", 5000, () -> tag("device-kv:Flash") != null);
    assertCleanToolbar();
    scrollTo(tag("device-kv:Flash"));
    assertOneLineFlash();
    assertCenteredRouter();
    assertHorizontalBounds();
    checkFlashSamples();
    capture("03-flash-single-line");
    SystemClock.sleep(6500);
    assertOneLineFlash();
    checks.add("flash_single_line_complete_values_after_live_refresh");
    openRouterTool("Настройки Xray");
    assertCleanToolbar();
    capture("04-router-tool-header");
    backToMain();
    require(tag("device-kv:Flash") != null, "System Back returns to the router");
    openTool("ru.owrthub.app.BrowserActivity", "Админка OpenWrt");
    SystemClock.sleep(800);
    assertCleanToolbar();
    capture("05-browser-before-back");
    // The embedded browser keeps its existing close confirmation/history.
    for (int attempt = 0; attempt < 4 && !activity.isFinishing() && !activity.isDestroyed(); attempt++) {
      sendKeyDownUpSync(android.view.KeyEvent.KEYCODE_BACK);
      acceptConfirmationIfPresent();
      SystemClock.sleep(200);
    }
    require(activity.isFinishing() || activity.isDestroyed(), "System Back returns from the embedded browser");
    activity = mainActivity;
    waitForNativeHome();
    clickText("Настройки");
    assertCleanToolbar();
    openSettingsTool("О приложении");
    assertCleanToolbar();
    backToMain();
    openTool("ru.owrthub.app.NativeBackupActivity", "Резервные копии");
    assertCleanToolbar();
    backToMain();
    clickText("Подключение");
    assertCleanToolbar();
    sendKeyDownUpSync(android.view.KeyEvent.KEYCODE_BACK);
    waitForNativeHome();
    checks.add("headers_no_back_arrow_address_placeholder_or_address_eye_system_back_works");
  }

  private void assertCleanToolbar() {
    onUi(() -> {
      View bar = tag("native-toolbar");
      require(bar instanceof android.view.ViewGroup, "Native toolbar exists");
      java.util.ArrayList<View> pending = new java.util.ArrayList<>();
      pending.add(bar);
      for (int i = 0; i < pending.size(); i++) {
        View child = pending.get(i);
        String description = String.valueOf(child.getContentDescription());
        require(!description.equals("Назад") && !description.equals("Назад к роутеру")
            && !description.equals("Показать или скрыть адрес Hub"), "No obsolete header back/address control");
        if (child instanceof android.widget.TextView) {
          String text = ((android.widget.TextView) child).getText().toString();
          require(!text.contains("Адрес скрыт") && !text.contains("127.0.0.1"), "Header omits address and placeholder");
        }
        if (child instanceof android.view.ViewGroup) {
          android.view.ViewGroup group = (android.view.ViewGroup) child;
          for (int n = 0; n < group.getChildCount(); n++) pending.add(group.getChildAt(n));
        }
      }
      return true;
    });
  }

  private void assertOneLineFlash() {
    onUi(() -> {
      android.view.ViewGroup row = (android.view.ViewGroup) tag("device-kv:Flash");
      android.widget.TextView value = (android.widget.TextView) row.getChildAt(1);
      require(value.getLineCount() == 1 && value.getLayout().getEllipsisCount(0) == 0,
          "Complete Flash value fits on one line: text=" + value.getText() + "; lines=" + value.getLineCount()
              + "; ellipsis=" + (value.getLayout() == null ? -1 : value.getLayout().getEllipsisCount(0))
              + "; width=" + value.getWidth() + "; row=" + row.getWidth());
      return true;
    });
  }

  private void checkFlashSamples() throws Exception {
    onUi(() -> {
      try {
        NativeUi ui = new NativeUi(activity);
        java.lang.reflect.Method method = MainActivity.class.getDeclaredMethod("kv", android.widget.LinearLayout.class, String.class, String.class);
        method.setAccessible(true);
        for (String sample : new String[] {"47.7 MB free / 91.9 MB", "1024 MB free / 2048 MB", "Свободно: 47.7 МБ / 91.9 МБ", "42.6 MiB free / 91.9 MiB", "—"}) {
          android.widget.LinearLayout card = ui.card();
          method.invoke(mainActivity, card, "Flash", sample);
          int width = getTargetContext().getResources().getDisplayMetrics().widthPixels - ui.dp(28);
          for (int pass = 0; pass < 2; pass++) {
            card.measure(View.MeasureSpec.makeMeasureSpec(width, View.MeasureSpec.EXACTLY),
                View.MeasureSpec.makeMeasureSpec(ui.dp(300), View.MeasureSpec.AT_MOST));
            card.layout(0, 0, width, card.getMeasuredHeight());
          }
          android.view.ViewGroup row = (android.view.ViewGroup) card.getChildAt(card.getChildCount() - 1);
          android.widget.TextView value = (android.widget.TextView) row.getChildAt(1);
          require(value.getLineCount() == 1 && value.getLayout().getEllipsisCount(0) == 0
              && value.getText().toString().equals(sample), "Full one-line Flash sample: " + sample);
        }
        return true;
      } catch (ReflectiveOperationException error) { throw new AssertionError(error); }
    });
  }

  private void checkRouterNameRendering() throws Exception {
    onUi(() -> {
      NativeUi ui = new NativeUi(getTargetContext());
      android.widget.LinearLayout old = ui.column();
      android.widget.TextView view = ui.routerName("Основа", 20);
      view.setTag("gradient-unit");
      old.addView(view);
      android.graphics.Shader previous = null;
      for (String name : new String[] {"Основа", "Советская 30", "Очень длинное название домашнего роутера node-3", "موجه المنزل"}) {
        android.widget.LinearLayout fresh = ui.column();
        android.widget.TextView next = ui.routerName(name, 20);
        next.setTag("gradient-unit");
        fresh.addView(next);
        ViewUpdates.patchChildren(old, fresh);
        require(old.getChildAt(0) == view, "Rename patches the existing name View");
        for (int width : new int[] {130, 260}) {
          int pixels = ui.dp(width);
          view.measure(View.MeasureSpec.makeMeasureSpec(pixels, View.MeasureSpec.EXACTLY),
              View.MeasureSpec.makeMeasureSpec(ui.dp(240), View.MeasureSpec.AT_MOST));
          view.layout(0, 0, pixels, view.getMeasuredHeight());
          Bitmap bitmap = Bitmap.createBitmap(pixels, Math.max(1, view.getMeasuredHeight()), Bitmap.Config.ARGB_8888);
          view.draw(new android.graphics.Canvas(bitmap));
          android.graphics.Shader shader = view.getPaint().getShader();
          require(shader instanceof android.graphics.LinearGradient && shader != previous,
              "Name gradient is recomputed after rename/resize");
          previous = shader;
          require(view.getLayout().getLineCount() >= 1 && view.getText().toString().equals(name), "Name remains real accessible text");
          int blue = 0, pink = 0;
          for (int y = 0; y < bitmap.getHeight(); y++) for (int x = 0; x < bitmap.getWidth(); x++) {
            int pixel = bitmap.getPixel(x, y);
            if (Color.alpha(pixel) > 180) {
              if (Color.blue(pixel) > Color.red(pixel) + 25) blue++;
              if (Color.red(pixel) > Color.blue(pixel) + 25) pink++;
            }
          }
          bitmap.recycle();
          require(blue > 0 && pink > 0, "Both gradient endpoint colours reach visible glyphs");
        }
      }
      return true;
    });
  }

  private void clickDescriptionInWindow(String value) {
    windowClick(value, true);
  }

  private void windowClick(String value, boolean description) {
    AccessibilityNodeInfo n = node(value, description);
    require(n != null, "Window action " + value);
    while (!n.isClickable() && n.getParent() != null) n = n.getParent();
    require(n.performAction(AccessibilityNodeInfo.ACTION_CLICK), "Click " + value);
    waitForIdleSync();
    SystemClock.sleep(300);
  }

  @Override
  protected void capture(String name) throws Exception {
    waitForIdleSync();
    SystemClock.sleep(900);
    Bitmap image = getUiAutomation().takeScreenshot();
    require(image != null, "Screenshot");
    File folder = getTargetContext().getExternalFilesDir("native-redesign");
    require(folder != null && (folder.isDirectory() || folder.mkdirs()), "Screenshot folder");
    try (FileOutputStream out = new FileOutputStream(new File(folder, name + ".png"))) {
      image.compress(Bitmap.CompressFormat.PNG, 100, out);
    } finally {
      image.recycle();
    }
  }
}
