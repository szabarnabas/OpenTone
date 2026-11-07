"""Preset library: 0B stored-scene protocol, preset labels, writer priority,
fake device 0B answers and the controller's name loading / fetch / save refusal."""
import time
import unittest

from mg300edit import presets as PR
from mg300edit import protocol as P
from mg300edit.controller import Controller
from mg300edit.fake import FakeDevice, FakeTransport, cc_offset
from mg300edit.layout import Layout
from mg300edit.writer import PRIO_LOW, CoalescingWriter
from tests.test_controller import Pump, is_stored_get
from tests.test_writer import SX, Recorder, cc

SCENE = bytes(range(126))


class StoredSceneProtocol(unittest.TestCase):
    def test_builder(self):
        self.assertEqual(P.get_stored_scene(7, 2), bytes.fromhex("F0 43 58 70 0B 00 07 02 F7"))
        self.assertEqual(P.get_stored_scene(71, 0), bytes.fromhex("F0 43 58 70 0B 00 47 00 F7"))
        with self.assertRaises(ValueError):
            P.get_stored_scene(128, 0)
        with self.assertRaises(ValueError):
            P.get_stored_scene(0, -1)

    def test_parse_round_trip(self):
        msg = P.stored_scene_response(43, 1, SCENE)
        self.assertEqual(len(msg), 198)
        self.assertEqual(msg[:8], bytes.fromhex("F0 43 58 70 0B 02 2B 01"))
        st = P.parse_stored_scene(msg)
        self.assertEqual((st.preset, st.scene, st.data), (43, 1, SCENE))

    def test_parse_rejects_others(self):
        self.assertIsNone(P.parse_stored_scene(P.edit_buffer_response(1, 0, SCENE)))
        self.assertIsNone(P.parse_stored_scene(P.get_stored_scene(1, 0)))      # a GET, not a reply
        self.assertIsNone(P.parse_edit_buffer(P.stored_scene_response(1, 0, SCENE)))
        self.assertIsNone(P.parse_stored_scene(bytes.fromhex("F0 43 58 70 0B 02 01 F7")))

    def test_describe(self):
        self.assertIn("preset=7 scene=2", P.describe(P.get_stored_scene(7, 2)))
        self.assertIn("stored-scene resp preset=43 scene=1", P.describe(P.stored_scene_response(43, 1, SCENE)))


class PresetLabels(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(PR.slot_label(0), "1A")
        self.assertEqual(PR.slot_label(3), "1D")
        self.assertEqual(PR.slot_label(9), "3B")        # user 3B = PC 9 (verified)
        self.assertEqual(PR.slot_label(35), "9D")
        self.assertEqual(PR.slot_label(36), "1A")
        self.assertEqual(PR.slot_label(45), "3B")       # factory 3B
        self.assertEqual(PR.slot_label(71), "9D")
        self.assertEqual(PR.full_label(9), "User 3B")
        self.assertEqual(PR.full_label(45, factory="Gyári"), "Gyári 3B")
        self.assertEqual(PR.bank(35), PR.BANK_USER)
        self.assertEqual(PR.bank(36), PR.BANK_FACTORY)
        for bad in (-1, 72, 127):
            with self.assertRaises(ValueError):
                PR.slot_label(bad)

    def test_parse_label(self):
        self.assertEqual(PR.parse_label("3b"), 9)
        self.assertEqual(PR.parse_label("3B", factory=True), 45)
        for p in range(PR.PRESET_COUNT):
            self.assertEqual(PR.parse_label(PR.slot_label(p), factory=p >= 36), p)
        for bad in ("", "3", "3E", "10A", "0A", "AB"):
            self.assertIsNone(PR.parse_label(bad))


class WriterPriority(unittest.TestCase):
    def make(self, **kw):
        rec = Recorder()
        w = CoalescingWriter(rec, **kw)
        w.start()
        self.addCleanup(w.stop)
        return w, rec

    def test_cc_overtakes_low_priority_gets(self):
        w, rec = self.make(sysex_gap=0.030, channel_gap=0.001)
        w.send(SX)
        time.sleep(0.005)
        names = [P.get_stored_scene(p, 0) for p in range(5)]
        for p, m in enumerate(names):
            w.send(m, key=f"get:0B:{p}:0", priority=PRIO_LOW)
        w.send(cc(1), key="cc")                          # live edit, queued last
        self.assertTrue(w.flush(2))
        self.assertEqual(rec.msgs, [SX, cc(1)] + names)

    def test_low_keys_collapse_and_move_between_queues(self):
        w, rec = self.make(sysex_gap=0.040, channel_gap=0.001)
        w.send(SX)
        time.sleep(0.005)
        for _ in range(3):                               # repeated loads collapse
            for p in range(4):
                w.send(P.get_stored_scene(p, 0), key=f"n{p}", priority=PRIO_LOW)
        self.assertEqual(w.pending(PRIO_LOW), 4)
        w.send(P.get_stored_scene(3, 0), key="n3")       # promoted to normal priority
        self.assertEqual((w.pending(0), w.pending(PRIO_LOW)), (1, 3))
        self.assertTrue(w.flush(2))
        self.assertEqual(rec.msgs, [SX, P.get_stored_scene(3, 0)] +
                         [P.get_stored_scene(p, 0) for p in range(3)])

    def test_clear_empties_both(self):
        w, _ = self.make(sysex_gap=1.0)
        w.send(SX)
        time.sleep(0.01)
        w.send(SX, key="a")
        w.send(SX, key="b", priority=PRIO_LOW)
        w.clear()
        self.assertEqual(w.pending(), 0)


class FakeStored(unittest.TestCase):
    def setUp(self):
        with open(Path_scene(), "rb") as f:
            self.dev = FakeDevice(f.read())
        self.lay = Layout.load()

    def get(self, p, s):
        (r,) = self.dev.handle(P.get_stored_scene(p, s))
        return P.parse_stored_scene(r)

    def test_names_all_presets(self):
        names = [self.lay.scene_name(self.get(p, 0).data) for p in range(72)]
        self.assertEqual(len(set(names)), 72)
        self.assertEqual(names[0], "User 1A")
        self.assertEqual(names[8], "Surf Rock Clean")
        self.assertEqual(names[9], "User 3B")
        self.assertEqual(names[45], "Factory 3B")

    def test_scenes_differ(self):
        a, b, c = (self.get(10, s) for s in range(3))
        self.assertEqual((a.preset, b.scene, c.scene), (10, 1, 2))
        self.assertEqual(len({a.data, b.data, c.data}), 3)
        self.assertEqual(self.lay.scene_name(b.data), "User 3C")

    def test_out_of_range(self):
        self.assertEqual(self.dev.handle(P.get_stored_scene(1, 3))[0][4], P.CMD_UNSUPPORTED)
        junk = self.get(100, 0)
        self.assertEqual(len(junk.data), 126)

    def test_cc_edits_edit_buffer_not_store_and_pc_reverts(self):
        self.assertEqual(cc_offset(24), 0x1C)
        self.assertEqual(cc_offset(77), 0x59)
        self.assertIsNone(cc_offset(0))
        stored = bytes(self.dev.stored_scene(8, 0))
        self.dev.handle(bytes([0xB0, 24, 42]))
        self.assertEqual(self.dev.edit[0x1C], 42)
        self.assertEqual(self.dev.stored_scene(8, 0), stored)
        self.dev.handle(P.program_change(8))
        self.assertEqual(bytes(self.dev.edit), stored)


def Path_scene():
    from mg300edit.fake import DEFAULT_SCENE
    return DEFAULT_SCENE


class ControllerLibrary(unittest.TestCase):
    def setUp(self):
        self.pump = Pump()
        self.tr = FakeTransport(dispatch=self.pump.dispatch, sysex_gap=0.003, channel_gap=0.001)
        lay = Layout.load()
        self.ctl = Controller(self.tr, lay, P.CCParamWriter.from_layout(lay))
        self.events = []
        for ev in ("name", "names", "stored", "scene", "info"):
            self.ctl.connect(ev, lambda *a, ev=ev: self.events.append((ev, a)))
        self.traffic = []
        self.tr.on_traffic = lambda d, m, t: self.traffic.append((d, m))
        self.ctl.start()
        self.addCleanup(self.ctl.stop)

    def test_names_loaded_on_connect_after_refresh(self):
        self.assertTrue(self.pump.until(lambda: ("names", ()) in self.events, timeout=5))
        self.assertEqual(len(self.ctl.preset_names), 72)
        self.assertEqual(self.ctl.preset_names[45], "Factory 3B")
        out = [m for d, m in self.traffic if d == "out" and P.parse_nux(m)]
        first_0b = next(i for i, m in enumerate(out) if is_stored_get(m))
        self.assertTrue(any(m[4] == P.CMD_EDIT_BUFFER for m in out[:first_0b]), "0C before names")
        self.assertEqual(sum(1 for m in out if is_stored_get(m)), 72)
        self.assertTrue(all(p < 72 for p in self.ctl.preset_names))

    def test_repeated_load_collapses(self):
        self.assertTrue(self.pump.until(lambda: self.ctl.scene is not None))
        for _ in range(5):
            self.ctl.load_preset_names()
        self.assertLessEqual(self.tr.writer.pending(PRIO_LOW), 72)

    def test_cc_overtakes_name_loading(self):
        self.assertTrue(self.pump.until(lambda: self.ctl.scene is not None))
        self.ctl.load_preset_names()
        amp = self.ctl.layout.block_by_id("AMP")
        gain = amp.current_model(self.ctl.scene).params[0]
        self.ctl.set_param(amp, gain.name, 55)
        self.assertTrue(self.pump.until(lambda: ("names", ()) in self.events, timeout=5))
        out = [m for d, m in self.traffic if d == "out"]
        i_cc = out.index(bytes([0xB0, 24, 55]))
        n_0b_before = sum(1 for m in out[:i_cc] if is_stored_get(m))
        self.assertLess(n_0b_before, 72)
        self.assertGreater(sum(1 for m in out[i_cc:] if is_stored_get(m)), 0)

    def test_name_follows_edit_buffer(self):
        self.assertTrue(self.pump.until(lambda: self.ctl.preset_names.get(8) == "Surf Rock Clean"))
        self.ctl.select_preset(9)
        self.assertTrue(self.pump.until(lambda: ("name", (9, "User 3B")) in self.events))

    def test_fetch_stored_scenes(self):
        self.assertTrue(self.pump.until(lambda: self.ctl.scene is not None))
        done = []
        f = self.ctl.fetch_stored([(9, 0), (9, 1), (9, 2), (9, 1)], on_done=done.append)
        self.assertEqual(len(f.keys), 3)
        self.assertTrue(self.pump.until(lambda: done, timeout=5))
        self.assertEqual(f.missing, [])
        self.assertEqual(len({f.results[k] for k in f.keys}), 3)

    def test_fetch_finish_reports_missing(self):
        done = []
        f = self.ctl.fetch_stored([(1, 0), (1, 5)], on_done=done.append)   # scene 5 -> unsupported
        self.assertTrue(self.pump.until(lambda: (1, 0) in f.results, timeout=5))
        f.finish()
        self.assertEqual(done, [f])
        self.assertEqual(f.missing, [(1, 5)])
        f.finish()
        self.assertEqual(len(done), 1)

    def test_save_preset_refuses_factory(self):
        self.assertTrue(self.pump.until(lambda: self.ctl.scene is not None))
        n = len(self.traffic)
        for target in (36, 45, 71, 72, -1):
            with self.assertRaises(ValueError):
                self.ctl.save_preset(target)
        self.pump.until(lambda: False, timeout=0.05)
        self.assertFalse(any(d == "out" and m[:1] == b"\xF0" and m[5:6] == bytes([P.OP_SET])
                             for d, m in self.traffic[n:]))

    def test_apply_scene_sends_only_ccs_and_reads_back(self):
        self.assertTrue(self.pump.until(lambda: self.ctl.scene is not None))
        target = bytearray(self.tr.device.stored_scene(9, 2))    # different gain/rvb/name
        self.pump.until(lambda: False, timeout=0.05)
        n = len(self.traffic)
        plan = self.ctl.apply_scene(bytes(target))
        self.assertTrue(plan.steps)
        self.assertTrue(self.pump.until(
            lambda: self.tr.device.edit[0x1C] == target[0x1C], timeout=3))
        sent = [m for d, m in self.traffic[n:] if d == "out" and not is_stored_get(m)]
        self.assertTrue(all(m[0] == 0xB0 or (P.parse_nux(m) and m[5] == P.OP_GET) for m in sent))
        self.assertEqual(self.ctl.scene[0x1C], target[0x1C])

    def test_apply_scene_read_only(self):
        self.ctl.param_writer = P.ParamWriter()
        with self.assertRaises(RuntimeError):
            self.ctl.apply_scene(SCENE)


if __name__ == "__main__":
    unittest.main()
