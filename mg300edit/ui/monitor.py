"""Raw MIDI monitor: timestamped hex of every message in/out (for reverse engineering)."""

from __future__ import annotations

import time
from typing import Callable, Optional

from gi.repository import Gdk, Gtk, Pango

from .. import protocol as P

MAX_LINES = 4000
TRIM_LINES = 1000


class MonitorPanel(Gtk.Box):
    def __init__(self, send_raw: Callable[[bytes], None]):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.send_raw = send_raw
        self._last_t: Optional[float] = None

        bar = Gtk.Box(spacing=6, margin_start=8, margin_end=8, margin_top=4, margin_bottom=4)
        bar.append(Gtk.Label(label="MIDI monitor", css_classes=["heading"]))
        self.pause = Gtk.ToggleButton(icon_name="media-playback-pause-symbolic", tooltip_text="Szünet")
        self.hide_rt = Gtk.CheckButton(label="Realtime elrejtése", active=True)
        clear = Gtk.Button(icon_name="edit-clear-all-symbolic", tooltip_text="Törlés")
        clear.connect("clicked", lambda *_: self.clear())
        copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Összes másolása")
        copy.connect("clicked", self._copy)
        for w in (self.pause, self.hide_rt, clear, copy):
            bar.append(w)
        spacer = Gtk.Box(hexpand=True)
        bar.append(spacer)
        self.entry = Gtk.Entry(placeholder_text="nyers hex, pl. F0 43 58 70 15 00 F7", width_chars=34,
                               tooltip_text="Ezeket a bájtokat változtatás nélkül elküldi a pedálnak")
        self.entry.connect("activate", self._send)
        send = Gtk.Button(label="Küldés")
        send.connect("clicked", self._send)
        bar.append(self.entry)
        bar.append(send)
        self.append(bar)

        self.view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                                 wrap_mode=Gtk.WrapMode.CHAR, css_classes=["monitor-view"],
                                 left_margin=8, right_margin=8, top_margin=4, bottom_margin=4)
        self.buf = self.view.get_buffer()
        self.buf.create_tag("in", foreground="#62a0ea")
        self.buf.create_tag("out", foreground="#ffa348")
        self.buf.create_tag("dim", foreground="#9a9996")
        self.buf.create_tag("err", foreground="#ed333b", weight=Pango.Weight.BOLD)
        self._end = self.buf.create_mark("end", self.buf.get_end_iter(), False)
        sw = Gtk.ScrolledWindow(vexpand=True, min_content_height=160)
        sw.set_child(self.view)
        self.append(sw)

    def add(self, direction: str, msg: bytes, t: float):
        if self.pause.get_active():
            return
        if self.hide_rt.get_active() and P.is_realtime(msg):
            return
        delta = "" if self._last_t is None else f"+{(t - self._last_t) * 1000:7.1f}ms"
        self._last_t = t
        ts = time.strftime("%H:%M:%S", time.localtime(t)) + f".{int((t % 1) * 1000):03d}"
        arrow = "IN " if direction == "in" else "OUT"
        desc = P.describe(msg)
        it = self.buf.get_end_iter()
        self.buf.insert_with_tags_by_name(it, f"{ts} {delta:>11} ", "dim")
        self.buf.insert_with_tags_by_name(it, f"{arrow} [{len(msg):3d}] {P.hexs(msg)}", direction)
        if desc:
            self.buf.insert_with_tags_by_name(it, f"   ; {desc}", "dim")
        self.buf.insert(it, "\n")
        self._trim()
        self.view.scroll_mark_onscreen(self._end)

    def note(self, text: str, error: bool = False):
        it = self.buf.get_end_iter()
        self.buf.insert_with_tags_by_name(it, f"-- {text}\n", "err" if error else "dim")
        self.view.scroll_mark_onscreen(self._end)

    def _trim(self):
        if self.buf.get_line_count() > MAX_LINES:
            start = self.buf.get_start_iter()
            ok, cut = self.buf.get_iter_at_line(TRIM_LINES)
            if ok:
                self.buf.delete(start, cut)

    def clear(self):
        self.buf.set_text("")
        self._last_t = None

    def _copy(self, *_):
        text = self.buf.get_text(self.buf.get_start_iter(), self.buf.get_end_iter(), False)
        Gdk.Display.get_default().get_clipboard().set(text)

    def _send(self, *_):
        try:
            data = P.parse_hex(self.entry.get_text())
        except ValueError as e:
            self.note(f"bad hex: {e}", error=True)
            return
        if not data:
            return
        self.send_raw(data)
