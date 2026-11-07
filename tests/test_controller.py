"""Controller + FakeTransport end to end (no GTK, no hardware)."""
import os
import queue
import tempfile
import time
import unittest

from mg300edit import protocol as P
from mg300edit.controller import Controller
from mg300edit.fake import FakeTransport
from mg300edit.layout import Layout
from mg300edit.transport import find_device


class Pump:
    """Stands in for GLib.idle_add: callbacks queue up and run on the test thread."""

    def __init__(self):
        self.q = queue.Queue()

    def dispatch(self, fn, *args):
        self.q.put((fn, args))

    def until(self, cond, timeout=3.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return True
            try:
                fn, args = self.q.get(timeout=0.02)
                fn(*args)
            except queue.Empty:
                pass
        return cond()


def is_stored_get(m):
    """Background preset-name reads (0B GET) run on connect; ignore them when
    counting what the editor sends in response to an action."""
    return m[:6] == bytes([0xF0, 0x43, 0x58, 0x70, 0x0B, 0x00])


class FakeEndToEnd(unittest.TestCase):
    def setUp(self):
        self.pump = Pump()
        self.tr = FakeTransport(dispatch=self.pump.dispatch, sysex_gap=0.005, channel_gap=0.001)
        self.ctl = Controller(self.tr, Layout.load(), P.CCParamWriter({"AMP.GAIN": 20}))
        self.events = []
        for ev in ("scene", "preset", "tempo", "identity", "param", "info"):
            self.ctl.connect(ev, lambda *a, ev=ev: self.events.append((ev, a)))
        self.traffic = []
        self.tr.on_traffic = lambda d, m, t: self.traffic.append((d, m))
        self.ctl.start()
        self.addCleanup(self.ctl.stop)

    def test_initial_read(self):
        self.assertTrue(self.pump.until(lambda: self.ctl.scene is not None))
        lay = self.ctl.layout
        self.assertEqual(lay.scene_name(self.ctl.scene), "Surf Rock Clean")
        self.assertEqual(self.ctl.preset, 8)
        self.assertTrue(self.pump.until(lambda: self.ctl.identity is not None and self.ctl.tempo == 120))
        self.assertEqual(self.ctl.identity.model, "MG300V2")
        self.assertEqual(self.ctl.block_order, [5, 1, 2, 3, 9, 4, 6, 7, 8, 10])
        sent = [m for d, m in self.traffic if d == "out"]
        self.assertTrue(all(m[5] == P.OP_GET for m in sent if P.parse_nux(m)), "only GETs on connect")

    def test_preset_select_and_footswitch(self):
        self.assertTrue(self.pump.until(lambda: self.ctl.scene is not None))
        self.ctl.select_preset(3)
        self.assertTrue(self.pump.until(
            lambda: self.ctl.layout.scene_name(self.ctl.scene) == "User 1D"))
        self.assertEqual(self.tr.device.preset, 3)
        # pedal-side preset change: UI follows, no PC echoed back
        n_pc_before = sum(1 for d, m in self.traffic if d == "out" and m[0] & 0xF0 == 0xC0)
        self.tr.simulate_preset_change(8)
        self.assertTrue(self.pump.until(
            lambda: self.ctl.layout.scene_name(self.ctl.scene) == "Surf Rock Clean"))
        n_pc_after = sum(1 for d, m in self.traffic if d == "out" and m[0] & 0xF0 == 0xC0)
        self.assertEqual(n_pc_before, n_pc_after)

    def test_set_param_via_cc_and_incoming_cc(self):
        self.assertTrue(self.pump.until(lambda: self.ctl.scene is not None))
        amp = self.ctl.layout.block_by_id("AMP")
        self.ctl.set_param(amp, "GAIN", 100)
        self.assertTrue(self.pump.until(lambda: bytes([0xB0, 20, 127]) in [m for _, m in self.traffic]))
        self.assertEqual(self.ctl.scene[28], 100)
        n_out = sum(1 for d, m in self.traffic if d == "out" and not is_stored_get(m))
        self.ctl.handle_messages([bytes([0xB0, 20, 0])])     # pedal-side CC
        self.assertEqual(self.ctl.scene[28], 0)
        self.assertIn(("param", ("AMP", "GAIN", 0)), self.events)
        self.pump.until(lambda: False, timeout=0.1)
        self.assertEqual(n_out, sum(1 for d, m in self.traffic if d == "out" and not is_stored_get(m)),
                         "no echo")

    def test_switch_cc_both_directions(self):
        self.ctl.param_writer = P.CCParamWriter({"EFX.switch": 2})
        self.assertTrue(self.pump.until(lambda: self.ctl.scene is not None))
        efx = self.ctl.layout.block_by_id("EFX")
        self.ctl.set_bypass(efx, False)                       # 0x45 -> 0x05
        self.assertTrue(self.pump.until(lambda: bytes([0xB0, 2, 0x05]) in [m for _, m in self.traffic]))
        self.events.clear()
        self.ctl.handle_messages([bytes([0xB0, 2, 0x43])])    # pedal: model 3, bypassed
        self.assertEqual(self.ctl.scene[2], 0x43)
        self.assertIn(("scene", ()), self.events)

    def test_noop_writer_sends_nothing(self):
        self.ctl.param_writer = P.ParamWriter()
        self.assertTrue(self.pump.until(lambda: self.ctl.scene is not None))
        self.pump.until(lambda: False, timeout=0.2)
        n_out = len([1 for d, m in self.traffic if d == "out" and not is_stored_get(m)])
        amp = self.ctl.layout.block_by_id("AMP")
        self.ctl.set_param(amp, "GAIN", 10)
        self.ctl.set_bypass(amp, False)
        self.ctl.set_tempo(99)
        self.pump.until(lambda: False, timeout=0.1)
        self.assertEqual(n_out, len([1 for d, m in self.traffic if d == "out" and not is_stored_get(m)]))

    # -- tempo following (TAP notification 7E 02 03, preset / scene loads) --
    def tempo_gets(self):
        return sum(1 for d, m in self.traffic if d == "out" and m == P.get_tempo())

    def loaded(self):
        self.assertTrue(self.pump.until(lambda: self.ctl.scene is not None and self.ctl.tempo == 120))
        self.pump.until(lambda: False, timeout=0.1)

    def test_pedal_tap_notification_requeries_tempo(self):
        self.loaded()
        n = self.tempo_gets()
        self.events.clear()
        self.tr.simulate_tap(88)                      # 7E 02 03, carries no value
        self.assertTrue(self.pump.until(lambda: self.ctl.tempo == 88))
        self.assertEqual(self.tempo_gets(), n + 1)    # exactly one 03 GET in answer
        self.assertIn(("tempo", (88,)), self.events)
        self.assertEqual(self.ctl.tempo_extra, 1)     # byte 8 follows the pedal
        self.assertFalse(self.ctl.modified)           # a TAP is not an edit-buffer change
        self.assertFalse(any(e == "info" for e, _ in self.events))

    def test_repeated_taps_follow_last_tempo(self):
        self.loaded()
        for bpm in (100, 104, 97):
            self.tr.simulate_tap(bpm)
        self.assertTrue(self.pump.until(lambda: self.ctl.tempo == 97))
        tempi = [a[0] for e, a in self.events if e == "tempo"]
        self.assertEqual(tempi[-1], 97)

    def test_scene_change_on_pedal_rereads_tempo(self):
        self.loaded()
        self.tr.device.tempo = 77                     # e.g. per-scene tempo, no notification
        self.tr.simulate_scene_change(1)              # B0 50 01
        self.assertTrue(self.pump.until(lambda: self.ctl.tempo == 77))
        self.assertIn(("tempo", (77,)), self.events)

    def test_preset_change_on_pedal_rereads_tempo(self):
        self.loaded()
        self.tr.device.tempo = 133
        self.tr.simulate_preset_change(3)             # C0 03
        self.assertTrue(self.pump.until(lambda: self.ctl.tempo == 133))

    def test_own_preset_and_scene_select_reread_tempo(self):
        self.loaded()
        n = self.tempo_gets()
        self.ctl.select_preset(3)
        self.assertTrue(self.pump.until(lambda: self.tempo_gets() > n))
        n = self.tempo_gets()
        self.ctl.select_scene(2)
        self.assertTrue(self.pump.until(lambda: self.tempo_gets() > n))

    def test_set_tempo_emits_tempo_event(self):
        self.loaded()
        self.events.clear()
        self.ctl.set_tempo(141)
        self.assertEqual(self.events, [("tempo", (141,))])     # at once, before any reply
        self.ctl.set_tempo(141)                                 # unchanged: no repeat
        self.assertEqual(self.events, [("tempo", (141,))])
        self.assertTrue(self.pump.until(lambda: self.tr.device.tempo == 141))

    def test_unsupported_reply(self):
        self.assertTrue(self.pump.until(lambda: self.ctl.scene is not None))
        self.ctl.send_raw(P.get_request(0x62))
        self.assertTrue(self.pump.until(lambda: any(e == "info" for e, _ in self.events)))


class FindDeviceTest(unittest.TestCase):
    def test_proc_cards(self):
        with tempfile.TemporaryDirectory() as d:
            cards = os.path.join(d, "cards")
            with open(cards, "w") as f:
                f.write(" 0 [PCH            ]: HDA-Intel - HDA Intel PCH\n"
                        "                      HDA Intel PCH at 0xf1230000 irq 140\n"
                        " 2 [V2             ]: USB-Audio - NUX MG-300V2\n"
                        "                      NUX MG-300V2 at usb-0000:00:14.0-1, high speed\n")
            dev = os.path.join(d, "snd")
            os.mkdir(dev)
            self.assertIsNone(find_device(cards, os.path.join(d, "nosys"), dev))
            open(os.path.join(dev, "midiC2D0"), "w").close()
            info = find_device(cards, os.path.join(d, "nosys"), dev)
            self.assertEqual(info.card, 2)
            self.assertTrue(info.path.endswith("midiC2D0"))

    def test_sysfs_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            card = os.path.join(d, "sys", "card3")
            os.makedirs(card)
            with open(os.path.join(card, "id"), "w") as f:
                f.write("MG300V2\n")
            dev = os.path.join(d, "snd")
            os.mkdir(dev)
            open(os.path.join(dev, "midiC3D0"), "w").close()
            info = find_device(os.path.join(d, "nocards"), os.path.join(d, "sys"), dev)
            self.assertEqual(info.card, 3)


if __name__ == "__main__":
    unittest.main()


class TouchHoldAndRevert(unittest.TestCase):
    def setUp(self):
        self.pump = Pump()
        self.tr = FakeTransport(dispatch=self.pump.dispatch, sysex_gap=0.005, channel_gap=0.001)
        lay = Layout.load()
        self.ctl = Controller(self.tr, lay, P.CCParamWriter.from_layout(lay))
        self.traffic = []
        self.tr.on_traffic = lambda d, m, t: self.traffic.append((d, m))
        self.ctl.start()
        self.addCleanup(self.ctl.stop)
        self.assertTrue(self.pump.until(lambda: self.ctl.scene is not None))
        self.amp = self.ctl.layout.block_by_id("AMP")
        self.gain = self.amp.current_model(self.ctl.scene).params[0]

    def test_racing_edit_buffer_does_not_yank_slider(self):
        self.ctl.set_param(self.amp, self.gain.name, 99)
        self.assertTrue(self.pump.until(lambda: any(m[:2] == bytes([0xB0, 24]) for d, m in self.traffic if d == "out")))
        # a stale 0C reply (fake still holds the old value) arrives right after the drag
        n = len(self.traffic)
        self.ctl.refresh_edit_buffer()
        self.assertTrue(self.pump.until(lambda: any(d == "in" and m[4:5] == b"\x0C" for d, m in self.traffic[n:])))
        self.pump.until(lambda: False, timeout=0.1)
        self.assertEqual(self.ctl.scene[self.gain.offset], 99)

    def test_revert_sends_pc_to_current_preset(self):
        self.ctl.revert()
        self.assertTrue(self.pump.until(lambda: any(m == bytes([0xC0, 8]) for d, m in self.traffic if d == "out")))
