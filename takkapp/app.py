"""Takk UI: big BPM from the pedal, start/stop, volume, output, beat dots."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from mg300edit.transport import RawMidiTransport  # noqa: E402
from mg300edit.ui.util import glib_dispatch  # noqa: E402

from . import APP_ID, __version__  # noqa: E402
from . import audio, settings  # noqa: E402
from . import tempo as T  # noqa: E402
from .click import BEATS_PER_BAR, OUTPUTS, ClickGenerator  # noqa: E402

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
FALLBACK_BPM = 120
OUTPUT_CHOICES = [("usb12", "USB 1/2"), ("usb34", "USB 3/4"), ("all", "Mind (1–4)")]

CSS = """
.takk-bpm { font-size: 76px; font-weight: 800; font-feature-settings: "tnum"; letter-spacing: -2px; }
.takk-bpm.unknown { opacity: 0.35; }
.takk-unit { font-size: 13px; font-weight: 700; letter-spacing: 3px; opacity: 0.55; }
.takk-status { font-size: 13px; }
.takk-led { min-width: 8px; min-height: 8px; border-radius: 4px; background: alpha(currentColor, 0.3); }
.takk-led.ok { background: @success_color; }
.takk-led.warn { background: @warning_color; }
.takk-led.err { background: @error_color; }
.takk-dot { min-width: 16px; min-height: 16px; border-radius: 8px; background: alpha(currentColor, 0.14);
            transition: background 60ms ease-out; }
.takk-dot.on { background: alpha(currentColor, 0.85); }
.takk-dot.on.accent { background: @accent_bg_color; }
.takk-play { min-height: 52px; min-width: 200px; font-size: 17px; font-weight: 700; }
"""


class TakkWindow(Adw.ApplicationWindow):
    def __init__(self, app: "TakkApp"):
        super().__init__(application=app, title="Takk")
        self.app = app
        self.set_default_size(360, 580)
        self.set_size_request(320, 400)

        tv = Adw.ToolbarView()
        header = Adw.HeaderBar()
        menu = Gio.Menu()
        menu.append("Hangsúly az 1. ütésen", "app.accent")
        menu.append("A Takk névjegye", "app.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       tooltip_text="Főmenü"))
        tv.add_top_bar(header)

        self.banner = Adw.Banner(title="A pedál hangkimenete némítva van a rendszerben",
                                 button_label="Feloldás")
        self.banner.connect("button-clicked", lambda *_: app.unmute_sink())
        tv.add_top_bar(self.banner)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0,
                      margin_start=16, margin_end=16, margin_top=12, margin_bottom=18)

        # status line: LED + text
        st = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        self.led = Gtk.Box(valign=Gtk.Align.CENTER, css_classes=["takk-led"])
        self.status = Gtk.Label(label="Pedál keresése…", wrap=True, justify=Gtk.Justification.CENTER,
                                css_classes=["takk-status", "dim-label"], max_width_chars=34)
        st.append(self.led)
        st.append(self.status)
        box.append(st)

        # big BPM
        self.bpm_label = Gtk.Label(label="—", css_classes=["takk-bpm", "unknown"], margin_top=6)
        box.append(self.bpm_label)
        box.append(Gtk.Label(label="BPM", css_classes=["takk-unit"]))
        self.hint = Gtk.Label(css_classes=["caption", "dim-label"], wrap=True, margin_top=4,
                              justify=Gtk.Justification.CENTER, visible=False)
        box.append(self.hint)

        # beat dots
        dots = Gtk.Box(spacing=14, halign=Gtk.Align.CENTER, margin_top=14, margin_bottom=18)
        self.dots = []
        for i in range(BEATS_PER_BAR):
            d = Gtk.Box(css_classes=["takk-dot"])
            dots.append(d)
            self.dots.append(d)
        box.append(dots)

        # start / stop
        self.play_content = Adw.ButtonContent(icon_name="media-playback-start-symbolic", label="Indítás")
        self.play = Gtk.Button(child=self.play_content, halign=Gtk.Align.CENTER,
                               css_classes=["pill", "suggested-action", "takk-play"],
                               tooltip_text="Indítás / leállítás (Szóköz)")
        self.play.connect("clicked", lambda *_: app.toggle())
        box.append(self.play)

        # settings list
        lb = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE,
                         margin_top=20)
        self.output_row = Adw.ComboRow(title="Kimenet",
                                       model=Gtk.StringList.new([t for _, t in OUTPUT_CHOICES]))
        self.output_row.set_tooltip_text("A pedál USB lejátszási csatornái, amelyekre a kattogás megy")
        lb.append(self.output_row)

        vol_row = Adw.ActionRow(title="Hangerő")
        self.volume = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.volume.set_hexpand(True)
        self.volume.set_size_request(150, -1)
        self.volume.set_valign(Gtk.Align.CENTER)
        self.volume.set_draw_value(False)
        self.vol_label = Gtk.Label(width_chars=5, xalign=1, css_classes=["numeric", "dim-label"])
        vol_row.add_suffix(self.volume)
        vol_row.add_suffix(self.vol_label)
        lb.append(vol_row)
        box.append(lb)

        self.audio_status = Gtk.Label(css_classes=["caption", "warning"], wrap=True, margin_top=10,
                                      justify=Gtk.Justification.CENTER, visible=False)
        box.append(self.audio_status)

        clamp = Adw.Clamp(maximum_size=420, child=box, valign=Gtk.Align.CENTER, vexpand=True)
        tv.set_content(Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                          propagate_natural_height=True))
        self.set_content(tv)

        # Space toggles from anywhere in the window (capture phase: before the
        # focused button/slider sees the key, so it never toggles twice)
        sc = Gtk.ShortcutController(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        sc.add_shortcut(Gtk.Shortcut(trigger=Gtk.KeyvalTrigger(keyval=Gdk.KEY_space, modifiers=0),
                                     action=Gtk.CallbackAction.new(lambda *_: (app.toggle(), True)[1])))
        self.add_controller(sc)

    # -- view updates --
    def set_status(self, text: str, level: str) -> None:
        self.status.set_label(text)
        for c in ("ok", "warn", "err"):
            self.led.remove_css_class(c)
        if level:
            self.led.add_css_class(level)

    def set_bpm(self, bpm) -> None:
        if bpm is None:
            self.bpm_label.set_label("—")
            self.bpm_label.add_css_class("unknown")
        else:
            self.bpm_label.set_label(str(bpm))
            self.bpm_label.remove_css_class("unknown")

    def set_hint(self, text: str) -> None:
        self.hint.set_label(text)
        self.hint.set_visible(bool(text))

    def set_audio_status(self, text: str) -> None:
        self.audio_status.set_label(text)
        self.audio_status.set_visible(bool(text))

    def set_playing(self, playing: bool, can_play: bool) -> None:
        self.play_content.set_label("Leállítás" if playing else "Indítás")
        self.play_content.set_icon_name("media-playback-stop-symbolic" if playing
                                        else "media-playback-start-symbolic")
        if playing:
            self.play.remove_css_class("suggested-action")
        else:
            self.play.add_css_class("suggested-action")
        self.play.set_sensitive(playing or can_play)
        if not playing:
            self.light_beat(None, False)

    def light_beat(self, index, accent: bool) -> None:
        for i, d in enumerate(self.dots):
            on = i == index
            (d.add_css_class if on else d.remove_css_class)("on")
            (d.add_css_class if on and accent else d.remove_css_class)("accent")


class TakkApp(Adw.Application):
    def __init__(self, args):
        super().__init__(application_id=APP_ID)
        self.args = args
        self.win: TakkWindow | None = None
        self.cfg = settings.load()
        self.sink: audio.Sink | None = None
        self.pedal_bpm: int | None = None
        self.last_bpm: int | None = None
        self._save_id = 0
        self._poll_id = 0
        self._play_gen = 0          # bumps on every start/stop: stale beat callbacks are ignored
        self.gen = ClickGenerator(bpm=FALLBACK_BPM, volume=self.cfg["volume"] / 100,
                                  accent=self.cfg["accent"], route=OUTPUTS[self.cfg["output"]])
        self.player = audio.ClickPlayer(self.gen, on_beat=self._beat_from_thread,
                                        on_stopped=lambda err: glib_dispatch(self._on_stopped, err))
        self.transport = RawMidiTransport(device_path=args.device, dispatch=glib_dispatch)
        self.follower = T.TempoFollower(self.transport, on_tempo=self._on_tempo, on_state=self._on_state)
        self.watcher = audio.SinkWatcher(self._on_sinks, dispatch=glib_dispatch)

        accent = Gio.SimpleAction.new_stateful("accent", None, GLib.Variant.new_boolean(self.cfg["accent"]))
        accent.connect("change-state", self._on_accent)
        self.add_action(accent)
        for name, cb, accels in (("about", self._about, []),
                                 ("quit", lambda *_: self.quit(), ["<Control>q"])):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", cb)
            self.add_action(a)
            if accels:
                self.set_accels_for_action(f"app.{name}", accels)

    # -- lifecycle --
    def do_startup(self):
        Adw.Application.do_startup(self)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.PREFER_DARK)
        display = Gdk.Display.get_default()
        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Gtk.IconTheme.get_for_display(display).add_search_path(str(DATA_DIR))
        Gtk.Window.set_default_icon_name(APP_ID)

    def do_activate(self):
        if self.win:
            self.win.present()
            return
        w = self.win = TakkWindow(self)
        w.output_row.set_selected([k for k, _ in OUTPUT_CHOICES].index(self.cfg["output"]))
        w.output_row.connect("notify::selected", self._on_output)
        w.volume.set_value(self.cfg["volume"])
        w.vol_label.set_label(f"{self.cfg['volume']} %")
        w.volume.connect("value-changed", self._on_volume)
        w.connect("close-request", lambda *_: (self.player.stop(), False)[1])
        self._refresh()
        w.present()
        self.follower.start()
        self.watcher.start()
        self._poll_id = GLib.timeout_add(T.POLL_MS, lambda: (self.follower.poll(), True)[1])

    def do_shutdown(self):
        self.player.stop()
        self.follower.stop()
        self.watcher.stop()
        if self._save_id:
            GLib.source_remove(self._save_id)
            self._save_id = 0
        settings.save(self.cfg)
        Adw.Application.do_shutdown(self)

    # -- actions --
    def toggle(self) -> None:
        if self.player.playing:
            self._play_gen += 1
            self.player.stop()
        elif self.sink:
            self._play_gen += 1
            self.player.set_bpm(self.pedal_bpm or self.last_bpm or FALLBACK_BPM)
            try:
                self.player.start(self.sink.name, audio.stream_channel_map(self.sink))
            except OSError as e:
                self.win.set_audio_status(f"A pw-cat nem indítható: {e.strerror or e}")
        self._refresh()

    def unmute_sink(self) -> None:
        sink = self.sink
        if not sink:
            return

        def work():
            audio.pactl("set-sink-mute", sink.name, "0")
            self.watcher.kick()
        threading.Thread(target=work, daemon=True).start()

    def _on_accent(self, action, value):
        action.set_state(value)
        self.cfg["accent"] = value.get_boolean()
        self.player.set_accent(self.cfg["accent"])
        self._save_later()

    def _on_output(self, row, _pspec):
        key = OUTPUT_CHOICES[row.get_selected()][0]
        self.cfg["output"] = key
        self.player.set_route(OUTPUTS[key])       # next click goes there, no restart
        self._save_later()

    def _on_volume(self, scale):
        v = int(round(scale.get_value()))
        self.win.vol_label.set_label(f"{v} %")
        self.cfg["volume"] = v
        self.player.set_volume(v / 100)
        self._save_later()

    def _save_later(self):
        if self._save_id:
            GLib.source_remove(self._save_id)

        def save():
            self._save_id = 0
            settings.save(self.cfg)
            return GLib.SOURCE_REMOVE
        self._save_id = GLib.timeout_add(600, save)

    def _about(self, *_):
        Adw.AboutDialog(application_name="Takk", application_icon=APP_ID, version=__version__,
                        developer_name="barni", license_type=Gtk.License.MIT_X11,
                        comments="Metronóm a NUX MG-300 MKII-hez: a pedál tempóját követi, "
                                 "és a kattogást a pedál hangjába küldi.").present(self.win)

    # -- events --
    def _on_tempo(self, bpm):
        self.pedal_bpm = bpm
        if bpm is not None:
            self.last_bpm = bpm
            self.player.set_bpm(bpm)
        self._refresh()

    def _on_state(self, state, detail):
        self.watcher.kick()           # pedal plugged/unplugged: its sink comes and goes too
        self._refresh()

    def _on_sinks(self, sinks):
        self.sink = audio.find_pedal_sink(sinks, self.args.sink)
        if not self.sink and self.player.playing:
            self._play_gen += 1
            self.player.stop()
        self._refresh()

    def _beat_from_thread(self, index, accent, delay):
        gen = self._play_gen

        def show():
            if gen == self._play_gen and self.win and self.player.playing:
                self.win.light_beat(index, accent)
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(max(0, int(delay * 1000)), show)

    def _on_stopped(self, error):
        if error:
            log.warning("playback stopped: %s", error)
        self._refresh(audio_error=error)

    # -- view --
    def _refresh(self, audio_error=None):
        w = self.win
        if not w:
            return
        st = self.follower.state
        if st == T.STATE_CONNECTED:
            w.set_status("Pedál csatlakoztatva", "ok")
        elif st == T.STATE_BUSY:
            w.set_status("A pedál MIDI portját egy másik program használja (pl. OpenTone)", "warn")
        elif st == T.STATE_ERROR:
            w.set_status("A pedál MIDI portja nem nyitható meg", "err")
            w.status.set_tooltip_text(self.follower.detail)
        else:
            w.set_status("Nincs pedál", "")
        w.set_bpm(self.pedal_bpm)
        playing = self.player.playing
        if playing and self.pedal_bpm is None:
            w.set_hint(f"Nincs tempóadat a pedáltól – {self.last_bpm or FALLBACK_BPM} BPM-mel kattog")
        else:
            w.set_hint("")
        if audio_error:
            w.set_audio_status(f"A lejátszás leállt: {audio_error}")
        elif not self.sink:
            w.set_audio_status("Nem található a pedál hangkimenete (PipeWire)")
        else:
            w.set_audio_status("")
        w.banner.set_revealed(bool(self.sink and self.sink.mute))
        w.set_playing(playing, self.sink is not None)
