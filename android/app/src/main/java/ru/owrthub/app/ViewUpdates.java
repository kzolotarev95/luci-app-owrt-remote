package ru.owrthub.app;

import android.view.View;
import android.view.ViewGroup;
import android.widget.EditText;
import android.widget.ProgressBar;
import android.widget.TextView;
import java.util.Objects;

/** Update attached values without resetting the window, focus, keyboard or scroll. */
final class ViewUpdates {
  static void patchChildren(ViewGroup attached, ViewGroup fresh) {
    int count = fresh.getChildCount();
    for (int i = 0; i < count; i++) {
      View next = fresh.getChildAt(i),
          old = i < attached.getChildCount() ? attached.getChildAt(i) : null;
      if (old == null
          || old.getClass() != next.getClass()
          || !Objects.equals(old.getTag(), next.getTag())) {
        fresh.removeViewAt(i);
        // Keep a placeholder so indices in the replacement tree remain stable.
        fresh.addView(new View(fresh.getContext()), i);
        if (old != null) attached.removeViewAt(i);
        attached.addView(next, i);
        continue;
      }
      if (old instanceof EditText) continue;
      old.setVisibility(next.getVisibility());
      old.setActivated(next.isActivated());
      if (old.getTag() instanceof String && ((String) old.getTag()).startsWith("router-order:"))
        old.setEnabled(next.isEnabled());
      old.setContentDescription(next.getContentDescription());
      if (old instanceof NativeNetworkMap)
        ((NativeNetworkMap) old).copyState((NativeNetworkMap) next);
      if (old instanceof TextView) {
        TextView a = (TextView) old, b = (TextView) next;
        if (!a.getText().toString().equals(b.getText().toString())) {
          a.setText(b.getText());
          a.setBackground(b.getBackground());
        }
        if (a.getCurrentTextColor() != b.getCurrentTextColor()) a.setTextColor(b.getTextColors());
      } else if (old instanceof NativeUi.IconView)
        ((NativeUi.IconView) old).copyFrom((NativeUi.IconView) next);
      else if (old instanceof RouterPhotoView)
        ((RouterPhotoView) old).copyFrom((RouterPhotoView) next);
      else if (old instanceof DiskGaugeView) ((DiskGaugeView) old).copyFrom((DiskGaugeView) next);
      else if (old instanceof ChartView) ((ChartView) old).copyObservations((ChartView) next);
      else if (old instanceof ProgressBar)
        ((ProgressBar) old).setProgress(((ProgressBar) next).getProgress());
      else if (old instanceof ViewGroup) patchChildren((ViewGroup) old, (ViewGroup) next);
    }
    while (attached.getChildCount() > count) attached.removeViewAt(attached.getChildCount() - 1);
  }
}
