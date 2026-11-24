package ru.owrthub.app;

import android.app.Activity;
import android.content.Context;
import android.content.res.ColorStateList;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.graphics.drawable.StateListDrawable;
import android.os.Build;
import android.text.InputType;
import android.text.TextUtils;
import android.util.AttributeSet;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.WindowInsetsController;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import java.util.Locale;
import java.util.function.IntConsumer;

/** Native, density-aware building blocks shared by every Hub screen. */
public final class NativeUi {
  public enum Style {
    PRIMARY,
    SECONDARY,
    DANGER,
    QUIET
  }

  public final Context context;
  public final Typeface regular, medium, semibold, bold;
  private static Typeface cachedRegular, cachedMedium, cachedSemibold, cachedBold;
  public final int BG, PANEL, PANEL2, LINE, TEXT, MUTED, BLUE, SOFT, GREEN, RED, AMBER;
  // Lower-case aliases make screen composition readable without duplicating the palette.
  public final int bg, panel, panel2, line, foreground, muted, blue, soft, green, red, amber;

  public NativeUi(Context context) {
    this(context, false);
  }

  public NativeUi(Context context, boolean winter) {
    this.context = context;
    synchronized (NativeUi.class) {
      if (cachedRegular == null) {
        cachedRegular = loadTypeface(context, 400, "sans-serif");
        cachedMedium = loadTypeface(context, 500, "sans-serif-medium");
        cachedSemibold = loadTypeface(context, 600, "sans-serif-medium");
        cachedBold = loadTypeface(context, 700, "sans-serif-bold");
      }
    }
    regular = cachedRegular;
    medium = cachedMedium;
    semibold = cachedSemibold;
    bold = cachedBold;
    BG = color(winter ? "#061b2b" : "#0d1014");
    PANEL = color(winter ? "#0c293e" : "#171c23");
    PANEL2 = color(winter ? "#14374f" : "#202730");
    LINE = color(winter ? "#2b4a60" : "#2c3540");
    TEXT = color(winter ? "#eaf7ff" : "#f1f4f8");
    MUTED = color(winter ? "#9bb7cb" : "#98a6b7");
    BLUE = color(winter ? "#93e0ff" : "#83baff");
    GREEN = color(winter ? "#89e1cd" : "#75d5b4");
    RED = color(winter ? "#f3adbd" : "#ef95a9");
    AMBER = color("#e2c17b");
    SOFT = alpha(BLUE, 0.09f);
    bg = BG;
    panel = PANEL;
    panel2 = PANEL2;
    line = LINE;
    foreground = TEXT;
    muted = MUTED;
    blue = BLUE;
    soft = SOFT;
    green = GREEN;
    red = RED;
    amber = AMBER;
    if (context instanceof Activity) applySystemBars((Activity) context);
  }

  public void applySystemBars(Activity activity) {
    activity.getWindow().setStatusBarColor(BG);
    activity.getWindow().setNavigationBarColor(BG);
    View decor = activity.getWindow().getDecorView();
    decor.setSystemUiVisibility(
        decor.getSystemUiVisibility()
            & ~View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR
            & ~View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR);
    if (Build.VERSION.SDK_INT >= 29) activity.getWindow().setNavigationBarContrastEnforced(false);
    if (Build.VERSION.SDK_INT >= 30)
      decor.post(
          () -> {
            WindowInsetsController controller = decor.getWindowInsetsController();
            if (controller != null)
              controller.setSystemBarsAppearance(
                  0,
                  WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS
                      | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS);
          });
  }

  public int dp(float value) {
    return Math.round(value * context.getResources().getDisplayMetrics().density);
  }

  public int sp(float value) {
    return Math.round(value * context.getResources().getDisplayMetrics().scaledDensity);
  }

  public static int color(String hex) {
    return Color.parseColor(hex);
  }

  public static int alpha(int color, float opacity) {
    return (Math.round(Math.max(0f, Math.min(1f, opacity)) * Color.alpha(color)) << 24)
        | (color & 0x00ffffff);
  }

  private static Typeface loadTypeface(Context context, int weight, String fallback) {
    try {
      return new Typeface.Builder(context.getAssets(), "fonts/Inter.ttf")
          .setFontVariationSettings("'wght' " + weight + ", 'opsz' 14")
          .setWeight(weight)
          .build();
    } catch (RuntimeException unavailableFont) {
      return Typeface.create(fallback, Typeface.NORMAL);
    }
  }

  public TextView text(String value, int sizeSp, boolean bold) {
    TextView view = new TextView(context);
    view.setText(value == null ? "" : value);
    view.setTextSize(TypedValue.COMPLEX_UNIT_SP, Math.max(11, sizeSp * .86f));
    view.setGravity(Gravity.CENTER);
    view.setTextColor(TEXT);
    view.setTypeface(bold ? semibold : regular);
    view.setIncludeFontPadding(false);
    view.setLineSpacing(dp(3), 1.05f);
    view.setLayoutParams(new LinearLayout.LayoutParams(-1, -2));
    return view;
  }

  public TextView label(String value) {
    TextView view = text(value, 14, true);
    view.setTextColor(MUTED);
    return view;
  }

  public TextView title(String value) {
    TextView view = text(value, 26, true);
    view.setTypeface(bold);
    view.setLetterSpacing(-0.025f);
    if (Build.VERSION.SDK_INT >= 28) view.setAccessibilityHeading(true);
    return view;
  }

  public TextView body(String value) {
    TextView view = text(value, 16, false);
    view.setTextColor(MUTED);
    view.setLineSpacing(dp(4), 1.1f);
    return view;
  }

  public LinearLayout column() {
    LinearLayout view = new LinearLayout(context);
    view.setOrientation(LinearLayout.VERTICAL);
    view.setLayoutParams(new LinearLayout.LayoutParams(-1, -2));
    return view;
  }

  public LinearLayout row() {
    LinearLayout view = new LinearLayout(context);
    view.setOrientation(LinearLayout.HORIZONTAL);
    view.setGravity(Gravity.CENTER_VERTICAL);
    view.setLayoutParams(new LinearLayout.LayoutParams(-1, -2));
    return view;
  }

  public LinearLayout card() {
    LinearLayout view = column();
    view.setPadding(dp(16), dp(16), dp(16), dp(16));
    view.setBackground(shape(PANEL, LINE, 16));
    return view;
  }

  public View spacer(int heightDp) {
    View view = new View(context);
    view.setLayoutParams(new LinearLayout.LayoutParams(1, dp(heightDp)));
    view.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
    return view;
  }

  public View divider() {
    View view = new View(context);
    LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, dp(1));
    params.setMargins(0, dp(14), 0, dp(14));
    view.setLayoutParams(params);
    view.setBackgroundColor(LINE);
    view.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
    return view;
  }

  /** One-pixel app-bar rule without the section spacing used by divider(). */
  public View compactDivider() {
    View view = new View(context);
    view.setLayoutParams(new LinearLayout.LayoutParams(-1, dp(1)));
    view.setBackgroundColor(LINE);
    view.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
    return view;
  }

  public GradientDrawable shape(int fill, int stroke, int radiusDp) {
    GradientDrawable drawable = new GradientDrawable();
    drawable.setColor(fill);
    drawable.setCornerRadius(dp(radiusDp));
    if (stroke != Color.TRANSPARENT) drawable.setStroke(dp(1), stroke);
    return drawable;
  }

  public RippleDrawable ripple(int fill, int border, int radiusDp) {
    return new RippleDrawable(
        ColorStateList.valueOf(alpha(BLUE, 0.16f)),
        shape(fill, border, radiusDp),
        shape(Color.WHITE, Color.TRANSPARENT, radiusDp));
  }

  public Button button(String title, boolean primary, Runnable action) {
    return button(title, primary ? Style.PRIMARY : Style.SECONDARY, action);
  }

  public Button button(String title, Style style, Runnable action) {
    Button button = new Button(context);
    button.setText(title);
    button.setAllCaps(false);
    button.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
    button.setTypeface(semibold);
    button.setGravity(Gravity.CENTER);
    button.setIncludeFontPadding(false);
    button.setMinHeight(dp(50));
    button.setMinimumHeight(dp(50));
    button.setMinWidth(0);
    button.setMinimumWidth(0);
    button.setPadding(dp(16), dp(13), dp(16), dp(13));
    button.setElevation(0f);
    button.setStateListAnimator(null);
    int fill =
        style == Style.PRIMARY
            ? color("#235a8a")
            : style == Style.DANGER
                ? alpha(RED, 0.08f)
                : style == Style.QUIET ? Color.TRANSPARENT : PANEL;
    int border =
        style == Style.PRIMARY
            ? color("#3879b4")
            : style == Style.DANGER
                ? alpha(RED, 0.3f)
                : style == Style.QUIET ? Color.TRANSPARENT : LINE;
    int ink = style == Style.PRIMARY ? color("#f3f9ff") : style == Style.DANGER ? RED : BLUE;
    StateListDrawable backgrounds = new StateListDrawable();
    backgrounds.addState(
        new int[] {-android.R.attr.state_enabled}, shape(alpha(fill, 0.4f), alpha(LINE, 0.5f), 12));
    backgrounds.addState(new int[] {}, ripple(fill, border, 12));
    button.setBackground(backgrounds);
    button.setBackgroundTintList(null);
    button.setTextColor(
        new ColorStateList(
            new int[][] {{-android.R.attr.state_enabled}, {}}, new int[] {alpha(ink, 0.38f), ink}));
    button.setLayoutParams(new LinearLayout.LayoutParams(-1, -2));
    if (action != null) button.setOnClickListener(view -> action.run());
    return button;
  }

  /** A native editable field. Use labeledField() when a persistent label is needed. */
  public EditText field(String hint, String value, boolean password) {
    EditText field = new EditText(context);
    field.setSingleLine(true);
    field.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
    field.setTextColor(TEXT);
    field.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
    field.setHintTextColor(alpha(MUTED, 0.7f));
    field.setHint(hint);
    field.setInputType(
        InputType.TYPE_CLASS_TEXT
            | (password
                ? InputType.TYPE_TEXT_VARIATION_PASSWORD
                : InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS));
    field.setTypeface(regular);
    field.setPadding(dp(14), dp(13), dp(14), dp(13));
    field.setMinHeight(dp(52));
    field.setSelectAllOnFocus(false);
    field.setText(value == null ? "" : value);
    StateListDrawable backgrounds = new StateListDrawable();
    backgrounds.addState(new int[] {android.R.attr.state_focused}, shape(PANEL, BLUE, 12));
    backgrounds.addState(new int[] {}, shape(PANEL, LINE, 12));
    field.setBackground(backgrounds);
    field.setBackgroundTintList(null);
    field.setLayoutParams(new LinearLayout.LayoutParams(-1, -2));
    field.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_AUTO);
    if (password) field.setSaveEnabled(false);
    return field;
  }

  public LinearLayout labeledField(String title, EditText field) {
    LinearLayout column = column();
    TextView label = label(title);
    if (field.getId() == View.NO_ID) field.setId(View.generateViewId());
    label.setLabelFor(field.getId());
    column.addView(label);
    column.addView(spacer(8));
    column.addView(field);
    return column;
  }

  public TextView chip(String title, int color) {
    TextView view = text(title, 12, true);
    view.setSingleLine(true);
    view.setTextColor(color);
    view.setGravity(Gravity.CENTER);
    view.setPadding(dp(9), dp(5), dp(9), dp(5));
    view.setBackground(shape(alpha(color, 0.07f), alpha(color, 0.24f), 8));
    view.setLayoutParams(new LinearLayout.LayoutParams(-2, -2));
    return view;
  }

  public IconView icon(String name, int color) {
    IconView icon = new IconView(context, name, color);
    icon.setLayoutParams(new LinearLayout.LayoutParams(dp(24), dp(24)));
    icon.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
    return icon;
  }

  /** The icon occupies 24dp inside a real 48dp touch target. */
  public FrameLayout iconButton(String name, String description, Runnable action) {
    FrameLayout target = new FrameLayout(context);
    target.setLayoutParams(new LinearLayout.LayoutParams(dp(48), dp(48)));
    target.setMinimumWidth(dp(48));
    target.setMinimumHeight(dp(48));
    target.setBackground(ripple(Color.TRANSPARENT, Color.TRANSPARENT, 12));
    target.setContentDescription(description);
    target.setFocusable(true);
    target.setClickable(true);
    IconView icon = icon(name, TEXT);
    FrameLayout.LayoutParams params = new FrameLayout.LayoutParams(dp(24), dp(24), Gravity.CENTER);
    target.addView(icon, params);
    if (action != null) target.setOnClickListener(view -> action.run());
    return target;
  }

  public LinearLayout iconBadge(String name, int color, int sizeDp) {
    LinearLayout badge = row();
    badge.setGravity(Gravity.CENTER);
    badge.setLayoutParams(new LinearLayout.LayoutParams(dp(sizeDp), dp(sizeDp)));
    badge.setBackground(shape(alpha(color, 0.09f), Color.TRANSPARENT, 12));
    badge.addView(icon(name, color));
    return badge;
  }

  public LinearLayout iconBadge(String name, int color) {
    return iconBadge(name, color, 40);
  }

  /** A standard, accessible three-tab navigation bar; selection is owned by the screen. */
  public LinearLayout bottomNav(String[] titles, String[] icons, int selected, IntConsumer action) {
    if (titles == null || icons == null || titles.length != icons.length || titles.length == 0)
      throw new IllegalArgumentException(
          "Navigation titles and icons must have equal nonzero lengths");
    LinearLayout container = column();
    container.setBackgroundColor(BG);
    View border = new View(context);
    border.setBackgroundColor(LINE);
    container.addView(border, new LinearLayout.LayoutParams(-1, dp(1)));
    LinearLayout navigation = row();
    navigation.setPadding(dp(6), dp(5), dp(6), dp(5));
    container.addView(navigation);
    for (int i = 0; i < titles.length; i++) {
      final int index = i;
      boolean active = i == selected;
      LinearLayout item = column();
      item.setGravity(Gravity.TOP | Gravity.CENTER_HORIZONTAL);
      item.setPadding(dp(4), dp(3), dp(4), dp(3));
      item.setMinimumHeight(dp(62));
      item.setLayoutParams(new LinearLayout.LayoutParams(0, -1, 1f));
      item.setBackground(ripple(Color.TRANSPARENT, Color.TRANSPARENT, 12));
      item.setFocusable(true);
      item.setClickable(true);
      item.setSelected(active);
      item.setContentDescription(titles[i]);
      item.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_YES);
      LinearLayout badge = row();
      badge.setGravity(Gravity.CENTER);
      badge.setLayoutParams(new LinearLayout.LayoutParams(dp(60), dp(31)));
      if (active) badge.setBackground(shape(SOFT, Color.TRANSPARENT, 13));
      badge.addView(icon(icons[i], active ? BLUE : MUTED));
      item.addView(badge);
      TextView title = text(titles[i], 12, active);
      title.setTextColor(active ? BLUE : MUTED);
      title.setGravity(Gravity.CENTER);
      title.setMaxLines(context.getResources().getConfiguration().fontScale > 1.4f ? 2 : 1);
      title.setEllipsize(TextUtils.TruncateAt.END);
      title.setPadding(0, dp(4), 0, 0);
      title.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
      item.addView(title);
      item.setOnClickListener(
          view -> {
            if (action != null) action.accept(index);
          });
      navigation.addView(item);
    }
    return container;
  }

  /** Crisp, scalable outline icons drawn directly on the Android Canvas. */
  public static final class IconView extends View {
    private final Paint ink = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF rect = new RectF();
    private final Path path = new Path();
    private String name;
    private int color;

    public IconView(Context context) {
      this(context, "grid", Color.rgb(131, 186, 255));
    }

    public IconView(Context context, AttributeSet attrs) {
      this(context);
    }

    public IconView(Context context, String name, int color) {
      super(context);
      this.name = name == null ? "grid" : name.toLowerCase(Locale.ROOT);
      this.color = color;
      setContentDescription(this.name);
      ink.setStrokeCap(Paint.Cap.ROUND);
      ink.setStrokeJoin(Paint.Join.ROUND);
    }

    void copyFrom(IconView other) {
      if (color != other.color || !name.equals(other.name)) {
        color = other.color;
        name = other.name;
        invalidate();
      }
    }

    public void setColor(int color) {
      this.color = color;
      invalidate();
    }

    public void setIcon(String name) {
      this.name = name == null ? "grid" : name.toLowerCase(Locale.ROOT);
      invalidate();
    }

    @Override
    protected void onDraw(Canvas canvas) {
      super.onDraw(canvas);
      float scale = Math.min(getWidth(), getHeight()) / 24f;
      if (scale <= 0) return;
      canvas.save();
      canvas.translate((getWidth() - 24 * scale) / 2f, (getHeight() - 24 * scale) / 2f);
      canvas.scale(scale, scale);
      ink.setColor(color);
      ink.setAlpha(isEnabled() ? 255 : 90);
      ink.setStyle(Paint.Style.STROKE);
      ink.setStrokeWidth(1.7f);
      switch (name) {
        case "grid":
        case "dashboard":
          round(canvas, 3, 3, 10, 10, 2);
          round(canvas, 14, 3, 21, 10, 2);
          round(canvas, 3, 14, 10, 21, 2);
          round(canvas, 14, 14, 21, 21, 2);
          break;
        case "router":
          round(canvas, 3, 12, 21, 20, 2.5f);
          line(canvas, 6, 12, 4, 5);
          line(canvas, 18, 12, 20, 5);
          dot(canvas, 7, 16, 0.8f);
          dot(canvas, 11, 16, 0.8f);
          line(canvas, 15, 16, 18, 16);
          break;
        case "server":
          round(canvas, 4, 3, 20, 11, 2);
          round(canvas, 4, 13, 20, 21, 2);
          dot(canvas, 8, 7, 0.8f);
          dot(canvas, 8, 17, 0.8f);
          line(canvas, 13, 7, 17, 7);
          line(canvas, 13, 17, 17, 17);
          break;
        case "settings":
        case "gear":
          for (int i = 0; i < 8; i++) {
            double angle = Math.PI * i / 4;
            line(
                canvas,
                12 + (float) Math.cos(angle) * 8,
                12 + (float) Math.sin(angle) * 8,
                12 + (float) Math.cos(angle) * 10,
                12 + (float) Math.sin(angle) * 10);
          }
          circle(canvas, 12, 12, 7.5f);
          circle(canvas, 12, 12, 2.8f);
          break;
        case "bell":
        case "notifications":
          path.reset();
          path.moveTo(5, 17);
          path.lineTo(6.5f, 14.5f);
          path.lineTo(6.5f, 9);
          path.cubicTo(6.5f, 1.5f, 17.5f, 1.5f, 17.5f, 9);
          path.lineTo(17.5f, 14.5f);
          path.lineTo(19, 17);
          path.close();
          canvas.drawPath(path, ink);
          arc(canvas, 9, 17, 15, 22, 25, 130);
          line(canvas, 12, 2, 12, 3);
          break;
        case "search":
          circle(canvas, 10.5f, 10.5f, 6.3f);
          line(canvas, 15, 15, 20.5f, 20.5f);
          break;
        case "plus":
        case "add":
          line(canvas, 12, 5, 12, 19);
          line(canvas, 5, 12, 19, 12);
          break;
        case "back":
        case "arrow-left":
          poly(canvas, 13, 5, 6, 12, 13, 19);
          line(canvas, 6, 12, 21, 12);
          break;
        case "arrow":
        case "chevron":
        case "chevron-right":
          poly(canvas, 9, 5, 16, 12, 9, 19);
          break;
        case "check":
          poly(canvas, 4, 12, 9, 17, 20, 6);
          break;
        case "close":
        case "x":
          line(canvas, 6, 6, 18, 18);
          line(canvas, 6, 18, 18, 6);
          break;
        case "wifi":
          arc(canvas, 1, 3, 23, 23, 220, 100);
          arc(canvas, 5, 8, 19, 22, 220, 100);
          arc(canvas, 9, 13, 15, 19, 220, 100);
          dot(canvas, 12, 20, 1);
          break;
        case "terminal":
          round(canvas, 2.5f, 4, 21.5f, 20, 3);
          poly(canvas, 6, 8, 10, 12, 6, 16);
          line(canvas, 13, 16, 18, 16);
          break;
        case "crown":
          poly(canvas, 3, 7, 7, 11, 12, 4, 17, 11, 21, 7, 18, 18, 6, 18, 3, 7);
          line(canvas, 7, 21, 17, 21);
          break;
        case "refresh":
          arc(canvas, 4, 4, 20, 20, 35, 290);
          poly(canvas, 20, 4, 20, 9, 15, 9);
          break;
        case "play":
          poly(canvas, 8, 4, 20, 12, 8, 20, 8, 4);
          break;
        case "stop":
          round(canvas, 5, 5, 19, 19, 2);
          break;
        case "key":
          circle(canvas, 7.5f, 8, 4.5f);
          line(canvas, 11, 11, 20, 20);
          line(canvas, 16, 16, 19, 13);
          line(canvas, 18.5f, 18.5f, 21.5f, 15.5f);
          break;
        case "shield":
          path.reset();
          path.moveTo(12, 2.5f);
          path.lineTo(20, 6);
          path.lineTo(19, 13);
          path.cubicTo(18.5f, 17, 15, 20, 12, 21.5f);
          path.cubicTo(9, 20, 5.5f, 17, 5, 13);
          path.lineTo(4, 6);
          path.close();
          canvas.drawPath(path, ink);
          poly(canvas, 8, 11, 11, 14, 16, 9);
          break;
        case "logout":
          poly(canvas, 10, 3, 4, 3, 4, 21, 10, 21);
          line(canvas, 10, 12, 21, 12);
          poly(canvas, 17, 8, 21, 12, 17, 16);
          break;
        case "copy":
          round(canvas, 8, 8, 21, 21, 2);
          poly(canvas, 16, 4, 16, 3, 3, 3, 3, 16, 4, 16);
          break;
        case "link":
        case "external":
          poly(canvas, 14, 3, 21, 3, 21, 10);
          line(canvas, 21, 3, 10, 14);
          poly(canvas, 10, 5, 4, 5, 4, 21, 19, 21, 19, 15);
          break;
        case "info":
          circle(canvas, 12, 12, 9);
          dot(canvas, 12, 7.5f, 0.9f);
          line(canvas, 12, 11, 12, 17);
          break;
        case "clock":
        case "history":
          circle(canvas, 12, 12, 9);
          poly(canvas, 12, 6, 12, 12, 16, 14);
          break;
        case "lock":
          round(canvas, 5, 10, 19, 21, 2);
          arc(canvas, 8, 2, 16, 16, 180, 180);
          line(canvas, 8, 9, 8, 10);
          line(canvas, 16, 9, 16, 10);
          line(canvas, 12, 14, 12, 17);
          break;
        case "globe":
          circle(canvas, 12, 12, 9);
          line(canvas, 3, 12, 21, 12);
          rect.set(8, 3, 16, 21);
          canvas.drawOval(rect, ink);
          break;
        case "activity":
          poly(canvas, 2, 13, 6, 13, 9, 5, 14, 20, 17, 10, 20, 13, 22, 13);
          break;
        case "download":
          poly(canvas, 7, 10, 12, 15, 17, 10);
          line(canvas, 12, 3, 12, 15);
          poly(canvas, 4, 16, 4, 21, 20, 21, 20, 16);
          break;
        case "notes":
          round(canvas, 4, 3, 20, 21, 2);
          line(canvas, 8, 8, 16, 8);
          line(canvas, 8, 12, 16, 12);
          line(canvas, 8, 16, 13, 16);
          break;
        case "user":
          circle(canvas, 12, 8, 4);
          arc(canvas, 4, 12, 20, 28, 180, 180);
          break;
        case "eye":
          path.reset();
          path.moveTo(2, 12);
          path.cubicTo(7, 3, 17, 3, 22, 12);
          path.cubicTo(17, 21, 7, 21, 2, 12);
          canvas.drawPath(path, ink);
          circle(canvas, 12, 12, 3);
          break;
        default:
          round(canvas, 4, 4, 20, 20, 4);
          line(canvas, 8, 12, 16, 12);
      }
      canvas.restore();
    }

    private void round(Canvas c, float l, float t, float r, float b, float radius) {
      rect.set(l, t, r, b);
      c.drawRoundRect(rect, radius, radius, ink);
    }

    private void line(Canvas c, float x1, float y1, float x2, float y2) {
      c.drawLine(x1, y1, x2, y2, ink);
    }

    private void circle(Canvas c, float x, float y, float r) {
      c.drawCircle(x, y, r, ink);
    }

    private void dot(Canvas c, float x, float y, float r) {
      ink.setStyle(Paint.Style.FILL);
      c.drawCircle(x, y, r, ink);
      ink.setStyle(Paint.Style.STROKE);
    }

    private void arc(Canvas c, float l, float t, float r, float b, float start, float sweep) {
      rect.set(l, t, r, b);
      c.drawArc(rect, start, sweep, false, ink);
    }

    private void poly(Canvas c, float... points) {
      path.reset();
      path.moveTo(points[0], points[1]);
      for (int i = 2; i < points.length; i += 2) path.lineTo(points[i], points[i + 1]);
      c.drawPath(path, ink);
    }
  }
}
