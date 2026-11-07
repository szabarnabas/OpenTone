"""Integration smoke test of the main window against the in-process fake pedal
(needs a display; skipped otherwise). Never touches MIDI hardware."""

import os
import tempfile
import time
import unittest

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gio, GLib, Gtk
    HAVE_DISPLAY = bool(Gtk.init_check()) and Gdk.Display.get_default() is not None
except (ImportError, ValueError):     # pragma: no cover
    HAVE_DISPLAY = False


def spin(cond=lambda: False, timeout=2.0):
    ctx = GLib.MainContext.default()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        ctx.iteration(False)
        time.sleep(0.002)
    return cond()


@unittest.skipUnless(HAVE_DISPLAY, "no display for GTK")
class WindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        cls.app = Adw.Application(application_id="io.github.barni.MG300EditTest",
                                  flags=Gio.ApplicationFlags.NON_UNIQUE)
        cls.app.register(None)

    def setUp(self):
        from mg300edit import protocol as P
        from mg300edit.controller import Controller
        from mg300edit.fake import FakeTransport
        from mg300edit.layout import Layout
        from mg300edit.settings import Settings
        from mg300edit.ui.util import glib_dispatch
        from mg300edit.ui.window import MainWindow
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        lay = Layout.load()
        self.tr = FakeTransport(dispatch=glib_dispatch, sysex_gap=0.002, channel_gap=0.001,
                                latency=0.002)
        self.ctl = Controller(self.tr, lay, P.CCParamWriter.from_layout(lay),
                              backup_dir=os.path.join(self.tmp.name, "backups"))
        self.settings = Settings(os.path.join(self.tmp.name, "settings.json"))
        # built-in click: stand-in for pw-cat, fake sink watcher, recorded pactl
        from mg300edit.metronome import Metronome
        from tests.test_metronome import FakeWatcher, StandIn
        FakeWatcher.instances = []
        self.pwcat = StandIn()
        self.pactl_calls = []
        self.metronome = Metronome(self.settings, glib_dispatch, command=self.pwcat,
                                   watcher_factory=FakeWatcher,
                                   pactl=lambda *a: self.pactl_calls.append(a))
        self.addCleanup(self.metronome.shutdown)
        self.win = MainWindow(self.app, self.ctl, settings=self.settings, metronome=self.metronome)
        self.addCleanup(self.win.destroy)
        self.addCleanup(self.ctl.stop)

    def enabled(self, name):
        return self.win.lookup_action(name).get_enabled()

    def connect_pedal(self):
        self.ctl.start()
        self.assertTrue(spin(lambda: self.ctl.scene is not None and self.ctl.tempo, 3))
        spin(timeout=0.1)

    def test_disconnected_state(self):
        ed = self.win.editor
        self.assertEqual(ed.stack.get_visible_child_name(), "status")
        self.assertEqual(ed.status.get_title(), "Nincs csatlakoztatva pedál")
        for name in ("save", "save-as", "rename", "copy-scene", "revert", "export-preset",
                     "export-all", "import-preset", "import-to-slot"):
            self.assertFalse(self.enabled(name), name)
        self.assertFalse(self.win.chain.get_visible())
        self.assertNotEqual(self.win.tempo_spin.get_text(), str(40))       # no fake value

    def test_connected_actions_and_editor(self):
        self.connect_pedal()
        self.assertEqual(self.win.editor.stack.get_visible_child_name(), "block")
        self.assertTrue(self.win.chain.get_visible())
        self.assertEqual(self.win.tempo_spin.get_text(), str(self.ctl.tempo))
        self.assertEqual(self.win.preset_slot.get_label(), "Saját 3A")
        # fake starts on user 3A (PC 8): everything is allowed
        for name in ("save", "save-as", "rename", "copy-scene", "revert", "export-preset",
                     "export-all", "import-preset", "import-to-slot", "refresh"):
            self.assertTrue(self.enabled(name), name)
        # the active scene is unknown until chosen / reported (0C byte 7 is not it)
        self.assertEqual(self.win.scene_group.get_active(), Gtk.INVALID_LIST_POSITION)
        self.win.scene_group.set_active(2)                # user clicks "3"
        self.assertEqual(self.ctl.scene_index, 2)
        self.assertTrue(spin(lambda: self.tr.device.scene_index == 2))
        # a factory preset: no rename / scene copy (save opens Save As instead)
        self.ctl.select_preset(40)
        self.assertTrue(spin(lambda: self.ctl.scene is not None and self.ctl.preset == 40))
        self.assertFalse(self.enabled("rename"))
        self.assertFalse(self.enabled("copy-scene"))
        self.assertTrue(self.enabled("save-as"))

    def test_modified_marks_save_button(self):
        self.connect_pedal()
        self.assertFalse(self.win.save_btn.has_css_class("suggested-action"))
        amp = self.ctl.layout.block_by_id("AMP")
        gain = amp.current_model(self.ctl.scene).params[0]
        self.ctl.set_param(amp, gain.name, 33)
        self.assertTrue(self.win.modified_label.get_visible())
        self.assertTrue(self.win.save_btn.has_css_class("suggested-action"))
        self.ctl.revert()
        self.assertFalse(self.win.save_btn.has_css_class("suggested-action"))

    def test_bypass_keeps_chain_and_editor_in_step(self):
        self.connect_pedal()
        card = next(c for c in self.win.chain.cards if c.block.id == "AMP")
        self.win.chain.select_card(card)
        self.assertEqual(self.win.editor.block.id, "AMP")
        self.win.editor.switch.set_active(False)          # header switch -> bypass
        self.assertTrue(card.has_css_class("bypassed"))
        card.toggle()                                     # LED -> on again
        self.assertTrue(self.win.editor.switch.get_active())

    def test_developer_mode_persists(self):
        self.win.lookup_action("developer-mode").activate(None)      # menu toggle
        self.assertTrue(self.settings.get("developer_mode"))
        self.assertTrue(self.win.mon_btn.get_visible())
        self.assertTrue(self.win.editor.dev)

    def test_scene_keys_and_dialog_blocks_keys(self):
        self.connect_pedal()
        self.win.present()
        spin(timeout=0.2)
        self.assertTrue(self.win._on_key(None, Gdk.KEY_2, 0, 0))
        self.assertEqual(self.ctl.scene_index, 1)
        self.assertTrue(spin(lambda: self.tr.device.scene_index == 1))
        self.win.saves.save_as()
        spin(timeout=0.3)
        self.assertIsNotNone(self.win.get_visible_dialog())
        cur = self.ctl.preset
        self.assertFalse(self.win._on_key(None, Gdk.KEY_Right, 0, 0))   # dialog open
        self.assertEqual(self.ctl.preset, cur)

    # -- built-in click (Takk metronome) --
    def click_on(self, sinks=None):
        from tests.test_metronome import FakeWatcher, sink
        self.win.lookup_action("click").activate(None)          # = toggle button / Ctrl+K
        self.assertTrue(self.metronome.enabled)
        FakeWatcher.instances[-1].on_sinks(sinks if sinks is not None else [sink()])
        return FakeWatcher.instances[-1]

    def test_click_toggle_runs_stand_in_player(self):
        from tests.test_metronome import NUX
        self.assertEqual(self.app.get_accels_for_action("win.click"), ["<Control>k"])
        self.assertFalse(self.win.click.toggle.get_active())
        self.assertEqual(self.pwcat.calls, [])                  # nothing runs before the toggle
        watcher = self.click_on()
        self.assertTrue(self.metronome.playing)
        self.assertEqual([c[0] for c in self.pwcat.calls], [NUX])
        spin(timeout=0.05)
        self.assertTrue(self.win.click.toggle.get_active())
        self.assertTrue(self.win.click.led.has_css_class("armed"))
        self.assertIn("120 BPM", self.win.click.toggle.get_tooltip_text())     # no tempo yet
        self.win.lookup_action("click").activate(None)
        self.assertFalse(self.metronome.playing)
        self.assertTrue(watcher.stopped)
        self.assertFalse(self.win.click.toggle.get_active())
        self.assertFalse(self.win.click.led.has_css_class("armed"))

    def test_click_follows_tempo(self):
        self.connect_pedal()
        self.click_on()
        self.assertEqual(self.metronome.gen.bpm, 120)
        self.tr.simulate_tap(90)                                 # pedal TAP (7E 02 03)
        self.assertTrue(spin(lambda: self.metronome.gen.bpm == 90))
        self.assertIn("90 BPM", self.win.click.toggle.get_tooltip_text())
        self.win.tempo_spin.set_value(140)                       # app tempo spin / TAP
        self.assertTrue(spin(lambda: self.metronome.gen.bpm == 140))
        self.assertTrue(spin(lambda: self.tr.device.tempo == 140))
        self.tr.device.tempo = 77
        self.tr.simulate_preset_change(3)                        # preset load re-reads it
        self.assertTrue(spin(lambda: self.metronome.gen.bpm == 77))
        self.assertTrue(self.metronome.playing)

    def test_click_sink_lost_and_no_sink(self):
        from mg300edit import clickaudio as audio
        toasts = []
        self.win.toast = lambda text, timeout=3: toasts.append(text)
        watcher = self.click_on()
        watcher.on_sinks([audio.Sink("laptop")])                 # pedal unplugged
        self.assertFalse(self.metronome.playing)
        self.assertFalse(self.win.lookup_action("click").get_state().get_boolean())
        self.assertTrue(any("eltűnt" in t for t in toasts))
        self.click_on([audio.Sink("laptop")])                    # no pedal sink at all
        self.assertFalse(self.metronome.enabled)
        self.assertTrue(any("Nem található" in t for t in toasts))
        self.assertEqual(len(self.pwcat.calls), 1)

    def test_click_mute_toast_unmutes_only_on_click(self):
        from tests.test_metronome import NUX, sink
        self.click_on([sink(mute=True)])
        t = self.win._mute_toast
        self.assertIsNotNone(t)
        self.assertEqual(t.get_button_label(), "Feloldás")
        spin(timeout=0.1)
        self.assertEqual(self.pactl_calls, [])                   # never automatically
        t.emit("button-clicked")
        self.assertTrue(spin(lambda: self.pactl_calls == [("set-sink-mute", NUX, "0")]))
        self.win.lookup_action("click").activate(None)           # off: toast goes away
        self.assertIsNone(self.win._mute_toast)

    def test_click_beat_led_flashes(self):
        led = self.win.click.led
        self.win.click.flash(True)
        self.assertTrue(led.has_css_class("beat") and led.has_css_class("accent"))
        self.assertTrue(spin(lambda: not led.has_css_class("beat"), 1))
        self.click_on()                                          # 120 BPM: first beat at once
        self.assertTrue(spin(lambda: led.has_css_class("beat"), 2))

    def test_click_stops_on_close_and_popover_settings(self):
        c = self.win.click
        c.volume.set_value(40)
        c.accent_row.set_active(False)
        c.output_row.set_selected(2)
        self.assertEqual((self.metronome.volume, self.metronome.accent, self.metronome.output),
                         (40, False, "all"))
        self.click_on()
        self.assertTrue(self.metronome.playing)
        self.win.present()                      # close() only acts on a realized window
        spin(timeout=0.2)
        self.win.close()
        spin(timeout=0.05)
        self.assertFalse(self.metronome.playing)
        from mg300edit.settings import Settings
        s2 = Settings(self.settings.path)
        self.assertEqual((s2.get("click_volume"), s2.get("click_accent"), s2.get("click_output")),
                         (40, False, "all"))


@unittest.skipUnless(HAVE_DISPLAY, "no display for GTK")
class StylesheetTest(unittest.TestCase):
    def test_style_css_parses_cleanly(self):
        from mg300edit.ui.util import STYLESHEET
        errors = []
        provider = Gtk.CssProvider()
        provider.connect("parsing-error", lambda _p, sec, err: errors.append(
            f"{sec.to_string()}: {err.message}"))
        provider.load_from_path(str(STYLESHEET))
        self.assertEqual(errors, [])


@unittest.skipUnless(HAVE_DISPLAY, "no display for GTK")
class SaveDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def test_user_slots_only_and_name_sanitizing(self):
        from mg300edit import presets as PR
        from mg300edit.ui.save_dialog import SaveAsDialog, overwrite_question
        got = []
        names = {9: "LeoGib CleanPush"}
        dlg = SaveAsDialog(names, target=9, name="Árvíztűrő", on_confirm=lambda *a: got.append(a))
        self.assertEqual(dlg.slots, list(PR.USER_PRESETS))
        self.assertEqual(dlg.target(), 9)
        self.assertEqual(dlg.name_row.get_text(), "Arvizturo")
        dlg.name_row.set_text("   ")
        self.assertFalse(dlg.get_response_enabled("save"))
        dlg.name_row.set_text("Új név")
        self.assertTrue(dlg.hint.get_visible())
        self.assertTrue(dlg.get_response_enabled("save"))
        self.assertEqual(overwrite_question(9, names), "Felülírod: 3B LeoGib CleanPush?")
        # a factory target is never offered: falls back to the first user slot
        dlg2 = SaveAsDialog(names, target=45, name="x", on_confirm=lambda *a: None)
        self.assertEqual(dlg2.target(), 0)


if __name__ == "__main__":
    unittest.main()
