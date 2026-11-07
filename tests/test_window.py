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
        self.win = MainWindow(self.app, self.ctl, settings=self.settings)
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
