"""Pure display helpers: unit scales, value formatting, knob math, chain reorder."""

import math
import unittest

from mg300edit.layout import (Layout, ParamSpec, format_param_value, format_unit, parse_scale,
                              pretty_param_name)
from mg300edit.ui import knobmath as KM
from mg300edit.ui.blockstyle import BLOCK_STYLE, block_css_class, block_description


class ScaleParseTest(unittest.TestCase):
    def test_linear_db(self):
        sc = parse_scale("dB = -12 + 0.24*v (MG-400 notes, unverified)")
        self.assertEqual((sc.unit, sc.kind), ("dB", "linear"))
        self.assertAlmostEqual(sc.convert(0), -12.0)
        self.assertAlmostEqual(sc.convert(50), 0.0)
        self.assertAlmostEqual(sc.convert(100), 12.0)
        self.assertAlmostEqual(sc.zero_raw(0, 100), 50.0)
        self.assertTrue(sc.bipolar)

    def test_linear_minus(self):
        sc = parse_scale("dB = 6 - 0.12*v")
        self.assertAlmostEqual(sc.convert(100), -6.0)

    def test_exp_hz(self):
        sc = parse_scale("Hz = 20*50**(v/100) (MG-400 notes, unverified)")
        self.assertEqual((sc.unit, sc.kind), ("Hz", "exp"))
        self.assertAlmostEqual(sc.convert(0), 20.0)
        self.assertAlmostEqual(sc.convert(100), 1000.0)
        hc = parse_scale("Hz = 5000*4**(v/100)")
        self.assertAlmostEqual(hc.convert(100), 20000.0)
        self.assertIsNone(hc.zero_raw(0, 100))

    def test_rejects_garbage_and_implausible(self):
        for hint in ("", "unknown", "dB = ???", "Hz = 0*5**(v/100)", "Hz = 20*50**(v/0)",
                     "dB = -500 + 1*v",              # -500 dB: not plausible
                     "Hz = 20*1e9**(v/100)",          # not a plain number
                     "ms = 5 + 0*v"):                 # constant
            self.assertIsNone(parse_scale(hint), hint)

    def test_ms(self):
        sc = parse_scale("ms = 20 + 9.8*v")
        self.assertEqual(format_unit(sc.convert(100), sc.unit), "1.00 s")
        self.assertEqual(format_unit(sc.convert(10), sc.unit), "118 ms")


class FormatTest(unittest.TestCase):
    def test_format_unit(self):
        self.assertEqual(format_unit(0.0, "dB"), "0 dB")
        self.assertEqual(format_unit(2.4, "dB"), "+2.4 dB")
        self.assertEqual(format_unit(-12, "dB"), "-12.0 dB")
        self.assertEqual(format_unit(141.4, "Hz"), "141 Hz")
        self.assertEqual(format_unit(1200, "Hz"), "1.2 kHz")
        self.assertEqual(format_unit(20000, "Hz"), "20 kHz")

    def test_param_value(self):
        raw = ParamSpec("GAIN", 28)
        self.assertEqual(format_param_value(raw, 42), "42")
        self.assertEqual(raw.display(42), "42")
        sel = ParamSpec("MIC", 76, 0, 3, choices=["C414", "E906", "U87", "SM57"], kind="selector")
        self.assertEqual(sel.display(3), "SM57")
        self.assertEqual(sel.display(9), "9")          # out of range: raw
        tog = ParamSpec("BRIGHT", 29, 0, 1, kind="toggle01")
        self.assertEqual((tog.display(0), tog.display(1)), ("Ki", "Be"))
        lvl = ParamSpec("LEVEL", 78, scale="dB = -12 + 0.24*v")
        self.assertEqual(lvl.display(50), "0 dB")
        self.assertEqual(lvl.display(100), "+12.0 dB")
        self.assertEqual(lvl.label(50), "50")          # old label() unchanged

    def test_layout_carries_scale(self):
        cab = Layout.load().block_by_id("CAB")
        p = cab.models[0].param("LOW CUT")
        self.assertTrue(p.scale.startswith("Hz"))
        self.assertEqual(p.display(0), "20 Hz")
        self.assertEqual(p.display(100), "1.0 kHz")
        self.assertEqual(cab.models[0].param("HIGH CUT").display(100), "20 kHz")

    def test_pretty_names(self):
        self.assertEqual(pretty_param_name("1.2KHZ"), "1.2 kHz")
        self.assertEqual(pretty_param_name("100HZ"), "100 Hz")
        self.assertEqual(pretty_param_name("16KHZ"), "16 kHz")
        self.assertEqual(pretty_param_name("GAIN"), "GAIN")
        self.assertEqual(pretty_param_name("MIC POSITION"), "MIC POSITION")


class KnobMathTest(unittest.TestCase):
    def test_clamp_fraction_angle(self):
        self.assertEqual(KM.clamp(-5, 0, 100), 0)
        self.assertEqual(KM.clamp(105, 0, 100), 100)
        self.assertEqual(KM.fraction(50, 0, 100), 0.5)
        self.assertEqual(KM.fraction(3, 3, 3), 0.0)
        self.assertAlmostEqual(KM.value_to_angle(0, 0, 100), math.radians(135))
        self.assertAlmostEqual(KM.value_to_angle(100, 0, 100), math.radians(405))
        self.assertAlmostEqual(KM.value_to_angle(50, 0, 100), math.radians(270))  # straight up

    def test_drag(self):
        # full range over DRAG_PIXELS, up (negative dy) increases
        self.assertAlmostEqual(KM.drag_value(50, -KM.DRAG_PIXELS / 2, 0, 100), 100)
        self.assertAlmostEqual(KM.drag_value(50, 20, 0, 100), 40)
        self.assertAlmostEqual(KM.drag_value(50, 20, 0, 100, fine=True), 48)
        self.assertEqual(KM.drag_value(90, -1000, 0, 100), 100)      # clamped
        self.assertEqual(KM.drag_value(10, 1000, 0, 100), 0)
        # small ranges still need a sensible distance
        self.assertAlmostEqual(KM.drag_value(0, -KM.DRAG_PIXELS, 0, 3), 3)

    def test_to_int(self):
        self.assertEqual(KM.to_int(49.5, 0, 100), 50)
        self.assertEqual(KM.to_int(49.49, 0, 100), 49)
        self.assertEqual(KM.to_int(-3, 0, 100), 0)
        self.assertEqual(KM.to_int(130.2, 0, 100), 100)

    def test_steps(self):
        self.assertEqual(KM.key_value(50, 0, 100, "up"), 51)
        self.assertEqual(KM.key_value(0, 0, 100, "down"), 0)
        self.assertEqual(KM.key_value(50, 0, 100, "pgup"), 60)
        self.assertEqual(KM.key_value(95, 0, 100, "pgup"), 100)
        self.assertEqual(KM.key_value(50, 0, 100, "home"), 0)
        self.assertEqual(KM.key_value(50, 0, 100, "end"), 100)
        self.assertEqual(KM.key_value(1, 0, 3, "pgdn"), 0)
        self.assertEqual(KM.key_value(7, 0, 100, "other"), 7)
        self.assertEqual(KM.scroll_step(0, 100), 2)
        self.assertEqual(KM.scroll_step(0, 100, fine=True), 1)
        self.assertEqual(KM.scroll_step(0, 3), 1)

    def test_drag_emits_once_per_integer(self):
        """Simulated drag: the knob emits only when the rounded value changes."""
        emitted, cur = [], 50
        for px in [0, -0.5, -1, -1.2, -2, -2, -3.9, -4, -6, -6]:
            v = KM.to_int(KM.drag_value(50, px, 0, 100), 0, 100)
            if v != cur:
                emitted.append(v)
                cur = v
        self.assertEqual(emitted, sorted(set(emitted)))
        self.assertEqual(len(emitted), len(set(emitted)))
        self.assertEqual(emitted[-1], 53)


class BlockStyleTest(unittest.TestCase):
    def test_palette(self):
        self.assertEqual(block_css_class("AMP"), "blk-amp")
        self.assertEqual(block_css_class("???"), "blk-unknown")
        self.assertEqual(block_description("RVB"), "Zengető")
        ids = {b.id for b in Layout.load().blocks}
        self.assertTrue(ids <= set(BLOCK_STYLE), ids - set(BLOCK_STYLE))


if __name__ == "__main__":
    unittest.main()
