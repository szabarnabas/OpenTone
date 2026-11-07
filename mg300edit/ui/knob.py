"""Vector arc knob (vertical drag, wheel, keys, double-click = default) and the
labelled KnobControl used by the block editor.

Colours come from CSS: the knob's `color` is the value-arc colour (the block
accent via `--blk`), the parent's `color` is the neutral track/pointer colour,
so light/dark mode and HiDPI need nothing special here."""

from __future__ import annotations

import math
from typing import Callable, Optional

import cairo
from gi.repository import Gdk, GObject, Graphene, Gtk

from . import knobmath as KM

_KEYS = {
    Gdk.KEY_Up: "up", Gdk.KEY_KP_Up: "up", Gdk.KEY_Right: "up", Gdk.KEY_KP_Right: "up",
    Gdk.KEY_Down: "down", Gdk.KEY_KP_Down: "down", Gdk.KEY_Left: "down", Gdk.KEY_KP_Left: "down",
    Gdk.KEY_Page_Up: "pgup", Gdk.KEY_KP_Page_Up: "pgup",
    Gdk.KEY_Page_Down: "pgdn", Gdk.KEY_KP_Page_Down: "pgdn",
    Gdk.KEY_Home: "home", Gdk.KEY_KP_Home: "home", Gdk.KEY_End: "end", Gdk.KEY_KP_End: "end",
}


class Knob(Gtk.Widget, Gtk.AccessibleRange):
    """Integer knob. Emits `changed(int)` only for user interaction and only when
    the integer value actually changes; set_value() never emits."""

    __gtype_name__ = "Mg300Knob"
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, (int,))}

    def __init__(self, lower: int = 0, upper: int = 100, value: int = 0, default: int = 0,
                 label: str = "", size: int = 66, origin: Optional[float] = None):
        super().__init__(accessible_role=Gtk.AccessibleRole.SLIDER, focusable=True,
                         css_classes=["knob"], halign=Gtk.Align.CENTER)
        self.lower, self.upper = int(lower), max(int(lower), int(upper))
        self.default = int(KM.clamp(default, self.lower, self.upper))
        self.size = size
        self.origin = origin            # raw value the value arc starts from (bipolar)
        self.label = label
        self.value_text: Callable[[int], str] = str
        self._value = int(KM.clamp(value, self.lower, self.upper))
        self._drag: Optional[tuple[float, float, bool]] = None   # base value, base dy, fine
        self._fvalue = float(self._value)
        self._hover = False
        self.set_cursor(Gdk.Cursor.new_from_name("ns-resize"))

        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        drag.connect("drag-end", self._drag_end)
        self.add_controller(drag)
        click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        click.connect("pressed", self._pressed)
        self.add_controller(click)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL
                                           | Gtk.EventControllerScrollFlags.DISCRETE)
        scroll.connect("scroll", self._scroll)
        self.add_controller(scroll)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: self._set_hover(True))
        motion.connect("leave", lambda *_: self._set_hover(False))
        self.add_controller(motion)
        self._update_accessible()

    # ---- public API ----
    def get_value(self) -> int:
        return self._value

    def set_value(self, v: int) -> None:
        """Programmatic update: redraw, never emits."""
        v = int(KM.clamp(int(v), self.lower, self.upper))
        self._fvalue = float(v)
        if v != self._value:
            self._value = v
            self._update_accessible()
            self.queue_draw()

    def reset(self) -> None:
        self._user_set(self.default)

    # ---- user input ----
    def _user_set(self, v: int) -> None:
        v = int(KM.clamp(int(v), self.lower, self.upper))
        if v == self._value:
            return
        self._value = v
        self._update_accessible()
        self.queue_draw()
        self.emit("changed", v)

    def _pressed(self, gesture, n_press, _x, _y):
        self.grab_focus()
        if n_press == 2:
            self._drag = None
            self._fvalue = float(self.default)
            self._user_set(self.default)
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _drag_begin(self, gesture, _x, _y):
        self._fvalue = float(self._value)
        self._drag = (self._fvalue, 0.0, self._fine(gesture))

    def _drag_update(self, gesture, _dx, dy):
        if self._drag is None:
            return
        base, base_dy, fine = self._drag
        now_fine = self._fine(gesture)
        if now_fine != fine:            # Shift pressed/released mid-drag: rebase
            base, base_dy, fine = self._fvalue, dy, now_fine
            self._drag = (base, base_dy, fine)
        self._fvalue = KM.drag_value(base, dy - base_dy, self.lower, self.upper, fine)
        self._user_set(KM.to_int(self._fvalue, self.lower, self.upper))

    def _drag_end(self, *_):
        self._drag = None
        self._fvalue = float(self._value)

    @staticmethod
    def _fine(controller) -> bool:
        return bool(controller.get_current_event_state() & Gdk.ModifierType.SHIFT_MASK)

    def _scroll(self, ctrl, _dx, dy):
        if dy == 0:
            return False
        step = KM.scroll_step(self.lower, self.upper, self._fine(ctrl))
        self._user_set(self._value + (step if dy < 0 else -step))
        return True

    def _key(self, _ctrl, keyval, _code, state):
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            return False
        key = _KEYS.get(keyval)
        if key is None:
            if keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace):
                self.reset()
                return True
            return False
        if key in ("up", "down") and state & Gdk.ModifierType.SHIFT_MASK:
            key = "pgup" if key == "up" else "pgdn"
        self._user_set(KM.key_value(self._value, self.lower, self.upper, key))
        return True

    def _set_hover(self, on: bool):
        self._hover = on
        self.queue_draw()

    # ---- accessibility ----
    def do_set_current_value(self, value):     # Gtk.AccessibleRange
        self._user_set(KM.to_int(value, self.lower, self.upper))
        return True

    def _update_accessible(self):
        self.update_property(
            [Gtk.AccessibleProperty.VALUE_MIN, Gtk.AccessibleProperty.VALUE_MAX,
             Gtk.AccessibleProperty.VALUE_NOW, Gtk.AccessibleProperty.VALUE_TEXT,
             Gtk.AccessibleProperty.LABEL],
            [float(self.lower), float(self.upper), float(self._value),
             self.value_text(self._value), self.label])

    # ---- layout / drawing ----
    def do_measure(self, orientation, for_size):
        return self.size, self.size, -1, -1

    def do_snapshot(self, snapshot):
        w, h = self.get_width(), self.get_height()
        cr = snapshot.append_cairo(Graphene.Rect().init(0, 0, w, h))
        accent = self.get_color()
        parent = self.get_parent()
        fg = parent.get_color() if parent is not None else accent
        dim = 1.0 if self.is_sensitive() else 0.45

        s = min(w, h)
        if s < 12:
            return
        cx, cy = w / 2.0, h / 2.0
        lw = max(3.0, s * 0.075)
        r = s / 2.0 - lw / 2.0 - 1.0
        a0 = KM.START_ANGLE
        a1 = KM.START_ANGLE + KM.SWEEP
        av = KM.value_to_angle(self._value, self.lower, self.upper)

        cr.set_line_cap(1)          # ROUND
        # track
        cr.set_line_width(lw)
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.13 * dim)
        cr.arc(cx, cy, r, a0, a1)
        cr.stroke()
        # value arc (from origin for bipolar params)
        ao = a0 if self.origin is None else KM.value_to_angle(self.origin, self.lower, self.upper)
        lo_a, hi_a = (ao, av) if av >= ao else (av, ao)
        cr.set_source_rgba(accent.red, accent.green, accent.blue, accent.alpha * dim)
        if hi_a - lo_a > 1e-3:
            cr.arc(cx, cy, r, lo_a, hi_a)
            cr.stroke()
        elif self.origin is not None:   # at the centre detent: small tick
            cr.arc(cx, cy, r, ao - 0.03, ao + 0.03)
            cr.stroke()

        # body
        rb = r - lw / 2.0 - max(3.0, s * 0.06)
        top = 0.20 if self._hover else 0.15
        grad_top, grad_bot = top * dim, 0.05 * dim
        pat = cairo.LinearGradient(cx, cy - rb, cx, cy + rb)
        pat.add_color_stop_rgba(0, fg.red, fg.green, fg.blue, grad_top)
        pat.add_color_stop_rgba(1, fg.red, fg.green, fg.blue, grad_bot)
        cr.arc(cx, cy, rb, 0, 2 * math.pi)
        cr.set_source(pat)
        cr.fill_preserve()
        cr.set_line_width(1.0)
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.22 * dim)
        cr.stroke()

        # pointer
        cos, sin = math.cos(av), math.sin(av)
        cr.set_line_width(max(2.0, s * 0.045))
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.92 * dim)
        cr.move_to(cx + cos * rb * 0.30, cy + sin * rb * 0.30)
        cr.line_to(cx + cos * rb * 0.86, cy + sin * rb * 0.86)
        cr.stroke()


class KnobControl(Gtk.Box):
    """Readout above, knob, name below (+ optional developer line)."""

    def __init__(self, name: str, lower: int, upper: int, value: int, default: int,
                 fmt: Callable[[int], str], origin: Optional[float] = None,
                 on_change: Optional[Callable[[int], None]] = None, dev_text: str = ""):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                         css_classes=["knob-control"], halign=Gtk.Align.CENTER)
        self.fmt = fmt
        self.name = name
        self.readout = Gtk.Label(css_classes=["param-readout"], width_chars=7)
        self.knob = Knob(lower, upper, value, default, label=name, origin=origin)
        self.knob.value_text = fmt
        self.name_label = Gtk.Label(label=name, css_classes=["param-name"], ellipsize=3,
                                    max_width_chars=11, justify=Gtk.Justification.CENTER)
        self.append(self.readout)
        self.append(self.knob)
        self.append(self.name_label)
        self.dev_label = Gtk.Label(label=dev_text, css_classes=["dev-info"], visible=bool(dev_text))
        self.append(self.dev_label)
        self.knob.connect("changed", self._changed)
        self._on_change = on_change
        self._refresh(value)

    def _changed(self, _knob, v: int):
        self._refresh(v)
        if self._on_change:
            self._on_change(v)

    def _refresh(self, v: int):
        text = self.fmt(v)
        self.readout.set_label(text)
        self.knob._update_accessible()
        default = self.fmt(self.knob.default)
        self.set_tooltip_text(f"{self.name}: {text} (nyers érték: {int(v)})\n"
                              f"Húzás fel/le, Shift = finom · görgő · nyilak\n"
                              f"Dupla kattintás: alapérték ({default})")

    def set_value(self, v: int) -> None:
        self.knob.set_value(v)
        self._refresh(self.knob.get_value())

    def get_value(self) -> int:
        return self.knob.get_value()
