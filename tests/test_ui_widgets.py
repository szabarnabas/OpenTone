"""Smoke tests for the chain row, block editor and knob widgets (needs a display;
skipped otherwise). Never touches MIDI hardware: widgets get plain callbacks."""

import os
import unittest
from pathlib import Path

HERE = os.path.dirname(__file__)
SCENE = Path(HERE, "..", "research", "scene_a.bin")

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gtk
    HAVE_DISPLAY = bool(Gtk.init_check()) and Gdk.Display.get_default() is not None
except (ImportError, ValueError):     # pragma: no cover - no gi / no GTK4
    HAVE_DISPLAY = False


class FakeCtl:
    def __init__(self, order):
        self.block_order = list(order)
        self.orders = []

    def set_block_order(self, order):
        self.orders.append(list(order))
        self.block_order = list(order)


@unittest.skipUnless(HAVE_DISPLAY, "no display for GTK")
class WidgetSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        from mg300edit.layout import Layout
        from mg300edit.ui.util import Guard
        cls.Guard = Guard
        cls.layout = Layout.load()
        cls.scene_bytes = SCENE.read_bytes()

    def setUp(self):
        from mg300edit.ui.block_editor import BlockEditor
        self.scene = bytearray(self.scene_bytes)
        self.guard = self.Guard()
        self.params, self.models, self.toggles = [], [], []
        self.editor = BlockEditor(self.guard, lambda b, i: self.models.append((b.id, i)),
                                  lambda b, p, v: self.params.append((b.id, p.name, v)),
                                  on_toggle=lambda b, by: self.toggles.append((b.id, by)))

    def show(self, block_id):
        block = self.layout.block_by_id(block_id)
        with self.guard:
            self.editor.show_block(block, self.scene)
        return block

    def test_every_block_builds(self):
        order = self.layout.scene_block_order(self.scene)
        for block in self.layout.chain(order):
            for view in ("panel", "sliders"):
                self.editor.set_view(view)
                self.show(block.id)
                model = block.current_model(self.scene)
                self.assertEqual(set(self.editor.rows), {p.name for p in model.params}, block.id)
        self.assertEqual(self.params, [])             # building never writes

    def test_knob_emits_once_per_value_and_never_echoes(self):
        self.show("AMP")
        gain = self.editor.rows["GAIN"]
        start = gain.get_value()
        gain.knob._user_set(start + 5)
        gain.knob._user_set(start + 5)                # same integer: no second write
        gain.knob._user_set(start + 6)
        self.assertEqual(self.params, [("AMP", "GAIN", start + 5), ("AMP", "GAIN", start + 6)])
        self.editor.update_param("AMP", "GAIN", 70)    # from the pedal
        self.assertEqual(gain.get_value(), 70)
        self.assertEqual(gain.readout.get_label(), "70")
        with self.guard:
            gain.knob._user_set(71)
        self.assertEqual(len(self.params), 2)
        gain.knob.reset()
        self.assertEqual(self.params[-1], ("AMP", "GAIN", gain.knob.default))

    def test_knob_accessible_and_keys(self):
        from mg300edit.ui.knob import Knob
        k = Knob(0, 100, 50, 20, label="GAIN")
        self.assertEqual(k.get_accessible_role(), Gtk.AccessibleRole.SLIDER)
        got = []
        k.connect("changed", lambda _k, v: got.append(v))
        k._key(None, Gdk.KEY_Up, 0, 0)
        k._key(None, Gdk.KEY_Page_Down, 0, 0)
        k._key(None, Gdk.KEY_End, 0, 0)
        k._key(None, Gdk.KEY_End, 0, 0)               # already at max: no emit
        k.set_value(10)                               # programmatic: no emit
        self.assertEqual(got, [51, 41, 100])
        k.do_snapshot(Gtk.Snapshot())                 # unallocated: must not raise

    def test_selector_switch_and_units(self):
        self.show("CAB")
        mic = self.editor.rows["MIC"]
        mic.buttons[0].set_active(True)
        self.assertEqual(self.params[-1], ("CAB", "MIC", 0))
        self.assertEqual(self.editor.rows["LEVEL"].readout.get_label(), "0 dB")
        self.show("EFX")                             # alt models etc. still build
        amp = self.layout.block_by_id("AMP")
        jazz = next(m for m in amp.models if m.param("BRIGHT"))
        amp.set_model(self.scene, jazz.index)
        self.show("AMP")
        self.editor.rows["BRIGHT"].switch.set_active(True)
        self.assertEqual(self.params[-1], ("AMP", "BRIGHT", 1))

    def test_eq_faders(self):
        from mg300edit.ui.block_editor import FaderControl
        self.editor.set_view("panel")
        self.show("EQ")
        band = next(iter(self.editor.rows.values()))
        self.assertIsInstance(band, FaderControl)
        band.scale.set_value(77)
        self.assertEqual(self.params[-1][2], 77)

    def test_header_switch_and_dev_mode(self):
        self.show("AMP")
        self.editor.switch.set_active(False)
        self.assertEqual(self.toggles, [("AMP", True)])
        self.editor.set_developer_mode(True)
        dd = self.editor.header.get_first_child().get_next_sibling().get_last_child()
        self.assertTrue(dd.get_selected_item().get_string().startswith("9: "))
        self.editor.set_developer_mode(False)
        dd = self.editor.header.get_first_child().get_next_sibling().get_last_child()
        self.assertEqual(dd.get_selected_item().get_string(), "CLASS A30")

    def test_reread_updates_in_place(self):
        block = self.show("AMP")
        gain = self.editor.rows["GAIN"]
        off = block.current_model(self.scene).param("GAIN").offset
        new = bytearray(self.scene)
        new[off] = 77
        with self.guard:
            self.editor.show_block(block, new)            # same block/model: no rebuild
        self.assertIs(self.editor.rows["GAIN"], gain)
        self.assertEqual(gain.get_value(), 77)
        self.editor.set_view("sliders")                   # view change: rebuilt
        self.assertIsNot(self.editor.rows["GAIN"], gain)
        self.assertEqual(self.params, [])

    def test_empty_states(self):
        self.editor.show_no_data(connected=False)
        self.assertEqual(self.editor.status.get_title(), "Nincs csatlakoztatva pedál")
        self.assertIsNone(self.editor.status.get_paintable())
        self.editor.show_no_data(connected=True)
        self.assertIsNotNone(self.editor.status.get_paintable())
        self.editor.show_empty("Nincs kiválasztott blokk", "")
        self.assertEqual(self.editor.status.get_title(), "Nincs kiválasztott blokk")
        self.assertIsNone(self.editor.block)

    def test_chain_row(self):
        from mg300edit.ui.chain import ChainRow
        order = self.layout.scene_block_order(self.scene)
        ctl = FakeCtl(order)
        selected, toggled = [], []

        def toggle(block, bypassed):
            toggled.append((block.id, bypassed))
            block.set_bypassed(self.scene, bypassed)
        row = ChainRow(self.guard, lambda b: selected.append(b.id), toggle, ctl=ctl)
        blocks = self.layout.chain(order)
        row.set_chain(blocks, self.scene, "AMP")
        self.assertEqual([c.block.id for c in row.cards], [b.id for b in blocks])
        self.assertTrue(row.cards[3].has_css_class("selected"))
        self.assertFalse(row.cards[0].dev_label.get_visible())
        row.set_developer_mode(True)
        self.assertTrue(row.cards[0].dev_label.get_visible())

        efx = next(c for c in row.cards if c.block.id == "EFX")
        self.assertTrue(efx.has_css_class("bypassed"))
        efx.toggle()
        self.assertEqual(toggled, [("EFX", False)])
        self.assertFalse(efx.has_css_class("bypassed"))
        self.assertTrue(efx.led.has_css_class("on"))
        with self.guard:
            efx.toggle()                              # pedal-originated: ignored
        self.assertEqual(len(toggled), 1)

        row.select_card(row.cards[0])
        self.assertEqual(selected, ["NR"])
        # move AMP (chain id 3) to the front
        self.assertTrue(row._reorder(3, 0))
        self.assertEqual(ctl.orders, [[3, 5, 1, 2, 9, 4, 6, 7, 8, 10]])
        self.assertEqual(row.cards[0].block.id, "AMP")
        row.move_card(row.cards[0], +1)               # Ctrl+Right
        self.assertEqual(ctl.orders[-1], [5, 3, 1, 2, 9, 4, 6, 7, 8, 10])

    def test_chain_without_reorder_support(self):
        from mg300edit.ui.chain import ChainRow
        order = self.layout.scene_block_order(self.scene)
        row = ChainRow(self.guard, lambda b: None, lambda b, v: None, ctl=object())
        row.set_chain(self.layout.chain(order), self.scene, None)
        self.assertFalse(row._reorder(3, 0))
        self.assertEqual(row.cards[3].block.id, "AMP")


class ReorderTest(unittest.TestCase):
    def test_reorder_chain(self):
        try:
            from mg300edit.ui.chain import reorder_chain
        except (ImportError, ValueError):            # pragma: no cover
            self.skipTest("gi not available")
        order = [5, 1, 2, 3, 9, 4, 6, 7, 8, 10]
        self.assertEqual(reorder_chain(order, 3, 0), [3, 5, 1, 2, 9, 4, 6, 7, 8, 10])
        self.assertEqual(reorder_chain(order, 5, 10), [1, 2, 3, 9, 4, 6, 7, 8, 10, 5])
        self.assertEqual(reorder_chain(order, 3, 3), order)      # before itself: no-op
        self.assertEqual(reorder_chain(order, 3, 4), order)      # after itself: no-op
        self.assertEqual(reorder_chain(order, 1, 4), [5, 2, 3, 1, 9, 4, 6, 7, 8, 10])
        self.assertEqual(reorder_chain(order, 99, 0), order)     # unknown id
        # empty slots keep their place
        self.assertEqual(reorder_chain([5, 0, 1, 2, 0], 2, 0), [2, 0, 5, 1, 0])


if __name__ == "__main__":
    unittest.main()
