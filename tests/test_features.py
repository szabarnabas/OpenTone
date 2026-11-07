"""Hardware-verified write features (FACTS.md 2026-11-06 + CORRECTION 2026-11-07): save
(0B set + 07 commit), scene select (CC80), tempo set (03 set, active part), block order (0D set), the modified
flag - protocol bytes, the save state machine and the fake device. No hardware."""
import json
import os
import tempfile
import unittest

from mg300edit import presets as PR
from mg300edit import protocol as P
from mg300edit import saving as S
from mg300edit.controller import Controller
from mg300edit.fake import NAME_OFFSET, ORDER_OFFSET, FakeDevice, FakeTransport
from mg300edit.layout import Layout
from tests.test_controller import Pump

SCENE = bytes(range(126))
H = bytes.fromhex


class WriteProtocol(unittest.TestCase):
    def test_set_stored_scene(self):
        msg = P.set_stored_scene(9, 1, SCENE)
        self.assertEqual(msg[:8], H("F0 43 58 70 0B 01 09 01"))
        self.assertEqual(len(msg), 198)
        self.assertEqual(msg[8:-1], P.encode7(SCENE))
        self.assertEqual(msg[-1], 0xF7)
        self.assertEqual(P.decode7(msg[8:-1]), SCENE)
        with self.assertRaises(ValueError):
            P.set_stored_scene(9, 3, SCENE)
        with self.assertRaises(ValueError):
            P.set_stored_scene(9, 0, SCENE[:-1])
        with self.assertRaises(ValueError):
            P.set_stored_scene(128, 0, SCENE)

    def test_stored_ack(self):
        ack = P.parse_stored_ack(H("F0 43 58 70 0B 03 09 01 01 00 00 00 00 00 F7"))
        self.assertEqual((ack.preset, ack.scene, ack.ok), (9, 1, True))
        self.assertFalse(P.parse_stored_ack(H("F0 43 58 70 0B 03 09 01 00 00 F7")).ok)
        self.assertIsNone(P.parse_stored_ack(P.stored_scene_response(9, 1, SCENE)))
        self.assertEqual(P.parse_stored_ack(P.stored_ack_response(4, 2)), P.StoredAck(4, 2, True))

    def test_commit(self):
        self.assertEqual(P.commit(9), H("F0 43 58 70 07 01 09 09 00 00 00 00 00 00 F7"))
        self.assertEqual(P.parse_commit_ack(H("F0 43 58 70 07 03 09 09 00 00 00 00 00 00 F7")), 9)
        self.assertIsNone(P.parse_commit_ack(H("F0 43 58 70 07 03 09 08 00 F7")))
        self.assertIsNone(P.parse_commit_ack(P.commit(9)))          # the request, not an ack
        self.assertEqual(P.parse_commit_ack(P.commit_ack_response(35)), 35)

    def test_set_current(self):
        self.assertEqual(P.set_current(9, 2), H("F0 43 58 70 15 01 09 02 00 00 00 00 00 00 F7"))
        with self.assertRaises(ValueError):
            P.set_current(9, 3)

    def test_set_tempo(self):
        self.assertEqual(P.set_tempo(120), H("F0 43 58 70 03 01 00 78 00 00 00 00 00 00 F7"))
        self.assertEqual(P.set_tempo(200, 5), H("F0 43 58 70 03 01 01 48 05 00 00 00 00 00 F7"))
        reply = H("F0 43 58 70 03 02 00 78 05 00 00 00 00 00 00 00 F7")
        self.assertEqual(P.parse_tempo(reply), 120)
        self.assertEqual(P.parse_tempo_extra(reply), 5)
        self.assertEqual(P.parse_tempo_extra(P.tempo_response(99, 3)), 3)

    def test_set_block_order(self):
        self.assertEqual(P.set_block_order([5, 1, 2, 3, 9, 4, 7, 6, 8, 10]),
                         H("F0 43 58 70 0D 01 05 01 02 03 09 04 07 06 08 0A 00 00 F7"))
        with self.assertRaises(ValueError):
            P.set_block_order([1, 2, 3])

    def test_edit_buffer_scene_get(self):
        self.assertEqual(P.get_edit_buffer(), H("F0 43 58 70 0C 00 F7"))
        self.assertEqual(P.get_edit_buffer(9, 2), H("F0 43 58 70 0C 00 09 02 F7"))

    def test_modified_notification(self):
        self.assertTrue(P.is_modified_notification(H("F0 43 58 70 7E 02 14 00 00 F7")))
        self.assertTrue(P.is_modified_notification(P.modified_notification()))
        self.assertFalse(P.is_modified_notification(H("F0 43 58 70 7E 02 13 F7")))
        self.assertFalse(P.is_modified_notification(H("F0 43 58 70 7F 02 00 F7")))

    def test_describe(self):
        self.assertIn("SAVE preset=9 part=1", P.describe(P.set_stored_scene(9, 1, SCENE)))
        self.assertIn("scene 3", P.describe(bytes([0xB0, 80, 2])))
        self.assertIn("modified", P.describe(P.modified_notification()))

    def test_tempo_cc_removed(self):
        w = P.CCParamWriter({"tempo": 90})
        self.assertFalse(hasattr(P.ParamWriter, "set_tempo"))
        self.assertIsNone(w.lookup_cc(0, 90))


class Names(unittest.TestCase):
    def test_sanitize(self):
        self.assertEqual(PR.sanitize_name("Árvíztűrő tükör"), "Arvizturo tukor")
        self.assertEqual(PR.sanitize_name("A" * 20), "A" * 16)
        self.assertEqual(PR.sanitize_name("x\ty€"), "x_y_")
        self.assertEqual(PR.encode_name("Hi"), b"Hi" + bytes(14))

    def test_with_name(self):
        s = PR.with_name(SCENE, "LeoGib CleanPush")
        self.assertEqual(s[NAME_OFFSET:NAME_OFFSET + 16], b"LeoGib CleanPush")
        self.assertEqual(s[:NAME_OFFSET], SCENE[:NAME_OFFSET])
        self.assertEqual(s[NAME_OFFSET + 16:], SCENE[NAME_OFFSET + 16:])

    def test_is_user(self):
        self.assertTrue(PR.is_user(0) and PR.is_user(35))
        self.assertFalse(PR.is_user(36) or PR.is_user(-1) or PR.is_user(72))


class Backups(unittest.TestCase):
    def test_write_and_prune(self):
        with tempfile.TemporaryDirectory() as d:
            scenes = [PR.with_name(SCENE, "LeoGib CleanPush")] * 3
            p = S.write_backup(d, 9, scenes, now=1_800_000_000.0)
            self.assertIn("_User_3B_LeoGib_CleanPush.mg300.json", p.name)
            data = json.loads(p.read_text())
            self.assertEqual(data["preset_index"], 9)
            self.assertEqual(data["scenes"][2], scenes[2].hex())
            for i in range(204):
                S.write_backup(d, 1, [SCENE] * 3, now=1_800_000_001.0 + i, keep=200)
            files = S.list_backups(d)
            self.assertEqual(len(files), 200)
            self.assertNotIn(p, files)                   # the oldest went first


class FeatureBase(unittest.TestCase):
    def setUp(self):
        self.pump = Pump()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tr = FakeTransport(dispatch=self.pump.dispatch, sysex_gap=0.004, channel_gap=0.001,
                                latency=0.003)
        lay = Layout.load()
        self.ctl = Controller(self.tr, lay, P.CCParamWriter.from_layout(lay),
                              backup_dir=self.tmp.name)
        self.dev: FakeDevice = self.tr.device
        self.events = []
        for ev in ("save", "modified", "scene_index", "info"):
            self.ctl.connect(ev, lambda *a, ev=ev: self.events.append((ev, a)))
        self.traffic = []
        self.tr.on_traffic = lambda d, m, t: self.traffic.append((d, m))
        self.ctl.start()
        self.addCleanup(self.ctl.stop)
        self.assertTrue(self.pump.until(lambda: self.ctl.scene is not None and self.ctl.tempo))
        self.amp = lay.block_by_id("AMP")
        self.gain = self.amp.current_model(self.ctl.scene).params[0]

    def out(self, since=0):
        return [m for d, m in self.traffic[since:] if d == "out"]

    def settle(self, t=0.15):
        self.pump.until(lambda: False, timeout=t)

    def run_job(self, job, timeout=5):
        self.assertTrue(self.pump.until(lambda: job.finished, timeout=timeout), job.state)
        self.settle()
        return job


class SaveTests(FeatureBase):
    def test_save_current_happy_path(self):
        self.ctl.select_scene(1)
        self.assertTrue(self.pump.until(lambda: self.dev.scene_index == 1 and self.ctl.scene_index == 1))
        self.settle()
        self.ctl.set_param(self.amp, self.gain.name, 77)
        self.assertTrue(self.pump.until(lambda: self.dev.edits[1][self.gain.offset] == 77))
        self.assertTrue(self.ctl.modified)
        old = [self.dev.stored_scene(8, s) for s in range(3)]
        expected = [S.with_tempo(PR.with_name(bytes(e), "My Tone"), self.ctl.tempo)
                    for e in self.dev.edits]
        n = len(self.traffic)
        job = self.run_job(self.ctl.save_preset(8, name="My Tone"))
        self.assertTrue(job.ok, job.error)
        self.assertEqual([self.dev.stored[(8, s)] for s in range(3)], expected)
        self.assertEqual(self.dev.commits, [8])
        # the exact verified sequence: 3x 0C part read, 3x 0B backup read, 3x 0B set, commit, 3x 0B verify
        sx = [m[4:8] for m in self.out(n) if m[:1] == b"\xF0" and m[4] in (0x0B, 0x07, 0x0C)
              and m[5] in (0, 1) and len(m) > 7 and m[6] == 8]      # (background name reads skipped)
        self.assertEqual(sx[:3], [H("0C 00 08 00"), H("0C 00 08 01"), H("0C 00 08 02")])
        self.assertEqual(sx[3:6], [H("0B 00 08 00"), H("0B 00 08 01"), H("0B 00 08 02")])
        self.assertEqual(sx[6:10], [H("0B 01 08 00"), H("0B 01 08 01"), H("0B 01 08 02"), H("07 01 08 08")])
        self.assertEqual([x for x in sx[10:] if x[0] == 0x0B][:3],
                         [H("0B 00 08 00"), H("0B 00 08 01"), H("0B 00 08 02")])
        self.assertIn(H("0C 00 08 01"), sx[10:])        # re-read of the restored scene's part
        self.assertIn(P.control_change(P.CC_SCENE, 1), self.out(n))     # CC80 restore
        # backup of what was overwritten
        (bk,) = S.list_backups(self.tmp.name)
        self.assertEqual(json.loads(bk.read_text())["scenes"], [o.hex() for o in old])
        self.assertEqual(job.backup_path, bk)
        # pedal reloaded + reset the scene -> restored to scene 1, modified cleared, name updated
        self.assertTrue(self.pump.until(lambda: self.dev.scene_index == 1))
        self.assertEqual(self.ctl.scene_index, 1)
        self.assertFalse(self.ctl.modified)
        self.assertEqual(self.ctl.preset_names[8], "My Tone")
        self.assertTrue(self.pump.until(lambda: self.ctl.layout.scene_name(self.ctl.scene) == "My Tone"))

    def test_save_as_other_slot(self):
        self.ctl.set_param(self.amp, self.gain.name, 55)
        self.assertTrue(self.pump.until(lambda: self.dev.edit[self.gain.offset] == 55))
        edits = [bytes(e) for e in self.dev.edits]
        job = self.run_job(self.ctl.save_preset(9))
        self.assertTrue(job.ok, job.error)
        self.assertEqual([self.dev.stored[(9, s)] for s in range(3)], edits)
        self.assertEqual(self.dev.preset, 8)                 # still on the source preset
        self.assertTrue(self.ctl.modified)                   # the current preset is still unsaved
        self.assertEqual(self.ctl.preset_names[9], "Surf Rock Clean")
        (bk,) = S.list_backups(self.tmp.name)
        self.assertIn("User_3B", bk.name)
        self.assertNotIn(bytes([0xF0, 0x43, 0x58, 0x70, 0x15, 0x01]), [m[:6] for m in self.out()])

    def test_factory_refused_unless_allowed(self):
        with self.assertRaises(ValueError):
            self.ctl.save_preset(40)
        job = self.run_job(self.ctl.save_preset(40, allow_factory=True))
        self.assertTrue(job.ok, job.error)
        self.assertEqual(self.dev.commits, [40])

    def test_ack_timeout(self):
        self.dev.drop_acks = True
        job = self.ctl.save_preset(9)
        self.assertTrue(self.pump.until(lambda: job.state == job.WRITE))
        self.ctl.tick(self.ctl.clock() + 0.5)
        self.assertEqual(job.state, job.WRITE)               # not yet
        self.ctl.tick(self.ctl.clock() + S.STEP_TIMEOUT + 0.1)
        self.assertEqual(job.state, job.ERROR)
        self.assertIn("nem jött válasz", job.error)
        self.settle()
        writes = [m for m in self.out() if m[4:6] == H("0B 01")]
        self.assertTrue(1 <= len(writes) <= 1 + S.MAX_RETRIES)   # part 0 (+ retries), never part 1
        self.assertTrue(all(m[7] == 0 for m in writes))
        self.assertFalse(any(m[4] == 0x07 for m in self.out()))
        self.assertEqual(self.dev.commits, [])
        self.assertIn(("save", (job,)), self.events)
        self.assertFalse(self.ctl.busy)

    def test_dropped_write_is_retried(self):
        """The real pedal sometimes ignores a 0B write (no ack): the job re-sends it."""
        self.dev.drop_writes = 1
        job = self.ctl.save_preset(9)
        self.assertTrue(self.pump.until(lambda: job.state == job.WRITE))
        self.settle()
        self.assertEqual(job.state, job.WRITE)               # first write swallowed
        self.ctl.tick(self.ctl.clock() + S.RETRY_AFTER + 0.05)
        self.run_job(job)
        self.assertTrue(job.ok, job.error)
        writes = [m for m in self.out() if m[4:6] == H("0B 01")]
        self.assertEqual([m[7] for m in writes], [0, 0, 1, 2])
        self.assertEqual(self.dev.commits, [9])

    def test_nak_stops(self):
        self.dev.nak_writes = True
        job = self.run_job(self.ctl.save_preset(9))
        self.assertEqual(job.state, job.ERROR)
        self.assertIn("elutasította", job.error)
        self.assertEqual(self.dev.commits, [])

    def test_readback_mismatch(self):
        self.dev.corrupt_store = True
        job = self.run_job(self.ctl.save_preset(9, name="X"))
        self.assertEqual(job.state, job.ERROR)
        self.assertIn("eltér", job.error)
        self.assertTrue(job.backup_path and job.backup_path.exists())   # restorable

    def test_busy_and_read_only(self):
        job = self.ctl.save_preset(9)
        with self.assertRaises(RuntimeError):
            self.ctl.save_preset(10)
        self.run_job(job)
        self.ctl.param_writer = P.ParamWriter()
        with self.assertRaises(RuntimeError):
            self.ctl.save_preset(9)

    def test_backup_failure_aborts_before_writing(self):
        blocker = os.path.join(self.tmp.name, "file")
        open(blocker, "w").close()
        self.ctl.backup_dir = os.path.join(blocker, "sub")        # cannot mkdir under a file
        job = self.run_job(self.ctl.save_preset(9))
        self.assertEqual(job.state, job.ERROR)
        self.assertIn("biztonsági mentés", job.error)
        self.assertFalse(any(m[4:6] == H("0B 01") for m in self.out()))

    def test_copy_scene(self):
        self.ctl.set_param(self.amp, self.gain.name, 66)          # scene 0 edit
        self.assertTrue(self.pump.until(lambda: self.dev.edits[0][self.gain.offset] == 66))
        e0, e1 = bytes(self.dev.edits[0]), bytes(self.dev.edits[1])
        job = self.run_job(self.ctl.copy_scene(0, 2))
        self.assertTrue(job.ok, job.error)
        self.assertEqual(self.dev.stored[(8, 0)], e0)
        self.assertEqual(self.dev.stored[(8, 1)], e1)
        self.assertEqual(self.dev.stored[(8, 2)], e0)
        with self.assertRaises(ValueError):
            self.ctl.copy_scene(1, 1)

    def test_import_to_slot(self):
        scenes = [PR.with_name(self.dev.stored_scene(20, s), "x") for s in range(3)]
        job = self.run_job(self.ctl.import_to_slot(11, scenes, name="Imported Tone"))
        self.assertTrue(job.ok, job.error)
        self.assertEqual([self.dev.stored[(11, s)] for s in range(3)],
                         [PR.with_name(sc, "Imported Tone") for sc in scenes])
        self.assertEqual(self.ctl.preset_names[11], "Imported Tone")

    def test_rename(self):
        job = self.run_job(self.ctl.rename_preset("Új név"))
        self.assertTrue(job.ok, job.error)
        self.assertEqual(self.ctl.preset_names[8], "Uj nev")
        self.assertTrue(all(self.dev.stored[(8, s)][NAME_OFFSET:NAME_OFFSET + 6] == b"Uj nev"
                            for s in range(3)))


class SceneTempoOrderTests(FeatureBase):
    def test_select_scene(self):
        n = len(self.traffic)
        self.ctl.select_scene(2)
        self.assertTrue(self.pump.until(lambda: self.dev.scene_index == 2))
        self.assertTrue(self.pump.until(lambda: self.ctl.scene == self.dev.edits[2]))
        self.assertEqual(self.ctl.scene_index, 2)
        out = self.out(n)
        self.assertIn(P.control_change(P.CC_SCENE, 2), out)            # CC80 = active scene
        self.assertIn(P.get_edit_buffer(8, 2), out)                    # explicit part read
        self.assertFalse(any(m[4:6] == H("15 01") for m in out))       # never 15 set
        self.assertIn(("scene_index", (2,)), self.events)
        # live edits now land in part 2
        self.ctl.set_param(self.amp, self.gain.name, 61)
        self.assertTrue(self.pump.until(lambda: self.dev.edits[2][self.gain.offset] == 61))
        self.assertNotEqual(self.dev.edits[0][self.gain.offset], 61)
        with self.assertRaises(ValueError):
            self.ctl.select_scene(3)

    def test_active_scene_unknown_until_known(self):
        self.assertIsNone(self.ctl.scene_index)         # identity C0 is not a preset load
        self.assertEqual(self.ctl.scene, self.dev.edits[0])
        self.ctl.select_preset(9)                       # our PC: the pedal starts on scene 0
        self.assertEqual(self.ctl.scene_index, 0)
        self.assertTrue(self.pump.until(lambda: self.ctl.scene == self.dev.edits[0]
                                        and self.dev.preset == 9))
        self.tr.simulate_preset_change(12)              # footswitch: C0 12 -> scene 0
        self.assertTrue(self.pump.until(lambda: self.ctl.preset == 12))
        self.assertEqual(self.ctl.scene_index, 0)

    def test_view_part_is_not_the_active_scene(self):
        """0C byte 7 is only the pedal's view index (15 set) - it must not move
        the active scene or replace the shown part."""
        self.ctl.select_scene(1)
        self.assertTrue(self.pump.until(lambda: self.ctl.scene == self.dev.edits[1]))
        self.tr.send(P.set_current(8, 2))          # someone changes the view part
        self.tr.send(P.get_edit_buffer())          # plain 0C -> reply for part 2
        self.settle(0.2)
        self.assertEqual(self.ctl.scene_index, 1)
        self.assertEqual(self.ctl.scene, self.dev.edits[1])
        self.assertEqual(self.dev.scene_index, 1)

    def test_footswitch_scene_change(self):
        self.tr.simulate_scene_change(1)
        self.assertTrue(self.pump.until(lambda: self.ctl.scene_index == 1))
        self.assertTrue(self.pump.until(lambda: self.ctl.scene == self.dev.edits[1]))
        self.assertNotIn(P.set_current(8, 1), self.out())          # nothing echoed

    def test_tempo_set_active_part_and_save_all_parts(self):
        self.dev.tempo_extra = 5
        self.ctl.refresh()
        self.assertTrue(self.pump.until(lambda: self.ctl.tempo_extra == 5))
        self.ctl.select_scene(2)
        self.assertTrue(self.pump.until(lambda: self.dev.scene_index == 2))
        self.settle()
        self.events.clear()
        n = len(self.traffic)
        self.ctl.set_tempo(140)
        self.assertTrue(self.pump.until(lambda: self.dev.tempo == 140))
        self.settle(0.2)
        out = self.out(n)
        sets = [m for m in out if m[:1] == b"\xF0" and m[5] == P.OP_SET]
        self.assertEqual(sets, [P.set_tempo(140, 5)])                   # no 15 set after it
        self.assertNotIn(P.control_change(P.CC_SCENE, 2), out)          # no scene restore
        self.assertEqual(self.ctl.layout.scene_tempo(self.dev.edits[2]), 140)
        self.assertEqual(self.ctl.layout.scene_tempo(self.ctl.scene), 140)
        self.assertEqual(self.ctl.scene_index, 2)
        self.assertNotIn(("scene_index", (0,)), self.events)
        self.assertEqual(self.ctl.tempo, 140)
        self.assertTrue(self.ctl.modified)                  # per-scene tempo: unsaved
        job = self.run_job(self.ctl.save_preset(8))
        self.assertTrue(job.ok, job.error)
        self.assertEqual([self.ctl.layout.scene_tempo(self.dev.stored[(8, s)]) for s in range(3)],
                         [140, 140, 140])

    def test_tempo_on_scene0_and_clamp(self):
        n = len(self.traffic)
        self.ctl.set_tempo(999)
        self.assertTrue(self.pump.until(lambda: self.dev.tempo == P.TEMPO_MAX))
        self.settle()
        self.assertFalse(any(m[4:6] == H("15 01") for m in self.out(n)))

    def test_tempo_read_only(self):
        self.ctl.param_writer = P.ParamWriter()
        n = len(self.traffic)
        self.ctl.set_tempo(100)
        self.settle()
        self.assertFalse(any(m[4:6] == H("03 01") for m in self.out(n)))

    def test_revert_restores_scene(self):
        self.ctl.select_scene(2)
        self.assertTrue(self.pump.until(lambda: self.dev.scene_index == 2))
        self.ctl.set_param(self.amp, self.gain.name, 3)
        self.assertTrue(self.pump.until(lambda: self.dev.edits[2][self.gain.offset] == 3))
        self.settle()
        self.ctl.revert()
        self.assertFalse(self.ctl.modified)
        self.assertTrue(self.pump.until(lambda: self.dev.scene_index == 2
                                        and self.dev.edits[2] == self.dev.stored_scene(8, 2)))
        self.assertTrue(self.pump.until(lambda: self.ctl.scene == self.dev.stored_scene(8, 2)))
        self.assertEqual(self.ctl.scene_index, 2)

    def test_block_order(self):
        cur = list(self.ctl.block_order)
        n = len(self.traffic)
        self.assertFalse(self.ctl.set_block_order(cur[:-1] + [99]))
        self.assertFalse(self.ctl.set_block_order(cur[:5]))
        self.assertFalse(self.ctl.set_block_order(cur[:-1] + [cur[0]]))
        self.settle(0.05)
        self.assertFalse(any(m[4:6] == H("0D 01") for m in self.out(n)))
        self.assertFalse(self.ctl.modified)
        new = cur[:]
        i, j = new.index(6), new.index(7)                     # swap MOD / DLY
        new[i], new[j] = new[j], new[i]
        self.assertTrue(self.ctl.set_block_order(new))
        self.assertEqual(self.ctl.block_order, new)
        self.assertEqual(list(self.ctl.scene[ORDER_OFFSET:ORDER_OFFSET + 10]), new)
        self.assertTrue(self.ctl.modified)
        self.assertTrue(self.pump.until(lambda: list(self.dev.edit[ORDER_OFFSET:ORDER_OFFSET + 10]) == new))
        self.assertIn(P.set_block_order(new), self.out(n))

    def test_block_order_read_only(self):
        self.ctl.param_writer = P.ParamWriter()
        new = list(self.ctl.block_order)
        new[0], new[1] = new[1], new[0]
        self.assertFalse(self.ctl.set_block_order(new))


class ModifiedFlag(FeatureBase):
    def test_lifecycle(self):
        self.assertFalse(self.ctl.modified)
        self.ctl.set_param(self.amp, self.gain.name, 12)
        self.assertTrue(self.ctl.modified)
        self.ctl.select_preset(3)
        self.assertFalse(self.ctl.modified)
        self.assertTrue(self.pump.until(lambda: self.ctl.preset == 3 and self.dev.preset == 3))
        self.settle()
        # knob turned on the pedal: CC + 7E 02 14
        self.tr.simulate_knob(24, 40)
        self.assertTrue(self.pump.until(lambda: self.ctl.modified))
        self.ctl.revert()
        self.assertFalse(self.ctl.modified)
        self.settle()
        self.ctl.handle_messages([P.modified_notification()])
        self.assertTrue(self.ctl.modified)
        self.tr.simulate_preset_change(4)                       # footswitch: reloaded
        self.assertTrue(self.pump.until(lambda: not self.ctl.modified))
        self.ctl.handle_messages([P.modified_notification()])
        job = self.run_job(self.ctl.save_preset(4))
        self.assertTrue(job.ok, job.error)
        self.assertFalse(self.ctl.modified)
        mods = [a[0] for e, a in self.events if e == "modified"]
        self.assertEqual(mods, [True, False, True, False, True, False, True, False])

    def test_scene_cc78_ignored(self):
        self.events.clear()
        self.ctl.handle_messages([bytes([0xB0, 78, 3])])
        self.assertFalse(self.ctl.modified)
        self.assertEqual(self.events, [])


if __name__ == "__main__":
    unittest.main()
