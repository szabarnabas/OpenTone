"""Scene -> CC list (mg300edit/apply.py), pure."""
import unittest

from mg300edit import apply as A
from mg300edit.fake import DEFAULT_SCENE
from mg300edit.layout import Layout

# tiny layout: AMP at offset 3 (switch CC 3, slot CCs 24/25), CAB at 9, a
# block without CCs (WAH), name/tempo/order like the real device
LAY = Layout.from_dict({
    "scene_size": 126,
    "name": {"offset": 105, "length": 16},
    "tempo": {"offset": 92, "hi_offset": 91},
    "block_order": {"offset": 93, "length": 10},
    "blocks": [
        {"id": "WAH", "chain_id": 0, "model_offset": 0, "models": [{"index": 1, "name": "W"}]},
        {"id": "AMP", "chain_id": 3, "model_offset": 3, "model_mask": 63, "cc_switch": 3,
         "cc_params": [24, 25, None],
         "models": [{"index": 1, "name": "A1", "params": [
                         {"name": "GAIN", "slot": 0, "offset": 28},
                         {"name": "LEVEL", "slot": 1, "offset": 29},
                         {"name": "NOCC", "slot": 2, "offset": 30}]},
                    {"index": 2, "name": "A2", "params": [
                         {"name": "DRIVE", "slot": 0, "offset": 28}]}]},
        {"id": "CAB", "chain_id": 9, "model_offset": 9, "model_mask": 63, "cc_switch": 9,
         "cc_params": [66], "models": [{"index": 1, "name": "C1", "params": [
             {"name": "LVL", "slot": 0, "offset": 76}]},
             {"index": 2, "name": "C2", "params": [{"name": "LVL", "slot": 0, "offset": 76}]}]},
    ]})


def scene(**kw):
    s = bytearray(126)
    s[0], s[3], s[9] = 1, 1, 1
    s[28], s[29], s[30], s[76] = 50, 60, 70, 80
    s[92] = 120
    s[93:103] = bytes([5, 1, 2, 3, 9, 4, 6, 7, 8, 10])
    s[105:110] = b"Clean"
    for k, v in kw.items():
        s[int(k[1:])] = v
    return s


class PlanTest(unittest.TestCase):
    def test_full_apply_models_first_then_params(self):
        plan = A.plan_scene(LAY, bytes(scene()))
        ccs = plan.ccs
        self.assertEqual(ccs[:2], [(3, 1), (9, 1)])                 # switch CCs first
        self.assertEqual(ccs[2:], [(24, 50), (25, 60), (66, 80)])  # then params by slot CC
        self.assertTrue(all(s.what == "model" for s in plan.steps[:2]))
        kinds = plan.skipped_kinds()
        self.assertIn(A.SKIP_NO_CC, kinds)          # WAH model + AMP slot 2
        self.assertIn(A.SKIP_NAME, kinds)
        self.assertNotIn(A.SKIP_HIDDEN, kinds)      # unknown without a current scene
        self.assertIn(("WAH", "model"), {(s.block, s.detail) for s in plan.skipped})
        self.assertIn(("AMP", "NOCC"), {(s.block, s.detail) for s in plan.skipped})

    def test_diff_only_changed_params(self):
        cur = scene()
        tgt = scene(o28=99)
        plan = A.plan_scene(LAY, bytes(tgt), bytes(cur))
        self.assertEqual(plan.ccs, [(24, 99)])
        self.assertEqual(plan.skipped, [])

    def test_identical_scene_is_empty(self):
        plan = A.plan_scene(LAY, bytes(scene()), bytes(scene()))
        self.assertEqual((plan.steps, plan.skipped), ([], []))

    def test_model_change_resends_all_params_of_new_model(self):
        cur = scene()
        tgt = scene(o3=2)                            # A1 -> A2; DRIVE byte == old GAIN (50)
        plan = A.plan_scene(LAY, bytes(tgt), bytes(cur))
        self.assertEqual(plan.steps[0].cc, 3)
        self.assertEqual(plan.steps[0].value, 2)
        self.assertIn((24, 50), plan.ccs)            # sent although byte unchanged
        self.assertNotIn(25, [c for c, _ in plan.ccs])   # A2 has no slot-1 param
        # amp change -> cab re-sent (pedal auto-picks a cab)
        self.assertIn((9, 1), plan.ccs)
        self.assertIn((66, 80), plan.ccs)
        self.assertLess(plan.ccs.index((9, 1)), plan.ccs.index((24, 50)))

    def test_bypass_only_does_not_resend_params(self):
        plan = A.plan_scene(LAY, bytes(scene(o3=0x41)), bytes(scene()))
        self.assertEqual(plan.ccs, [(3, 0x41)])

    def test_values_over_127_skipped(self):
        plan = A.plan_scene(LAY, bytes(scene(o28=213)), bytes(scene()))
        self.assertEqual(plan.ccs, [])
        self.assertEqual([(s.kind, s.block, s.detail) for s in plan.skipped],
                         [(A.SKIP_RANGE, "AMP", "GAIN")])
        plan = A.plan_scene(LAY, bytes(scene(o3=0x81)), bytes(scene()))
        self.assertNotIn(3, [c for c, _ in plan.ccs])
        self.assertIn((A.SKIP_RANGE, "AMP", "model"), [(s.kind, s.block, s.detail) for s in plan.skipped])

    def test_unknown_model_params_not_sent(self):
        plan = A.plan_scene(LAY, bytes(scene(o3=7)), bytes(scene()))
        self.assertEqual(plan.ccs[0], (3, 7))
        self.assertNotIn(24, [c for c, _ in plan.ccs])
        self.assertIn((A.SKIP_UNKNOWN_MODEL, "AMP"), [(s.kind, s.block) for s in plan.skipped])

    def test_meta_and_hidden_reported(self):
        tgt = scene(o92=99, o93=1, o50=7, o51=8)
        tgt[105:110] = b"Other"
        plan = A.plan_scene(LAY, bytes(tgt), bytes(scene()))
        self.assertEqual(plan.ccs, [])
        kinds = {s.kind: s.detail for s in plan.skipped}
        self.assertEqual(kinds[A.SKIP_NAME], "Other")
        self.assertIn(A.SKIP_ORDER, kinds)
        self.assertEqual(kinds[A.SKIP_TEMPO], "99")
        self.assertEqual(kinds[A.SKIP_HIDDEN], "2")
        text = A.summarize(plan)
        for word in ("név", "blokksorrend", "tempó", "2 rejtett"):
            self.assertIn(word, text)

    def test_wrong_size(self):
        with self.assertRaises(ValueError):
            A.plan_scene(LAY, bytes(10))
        plan = A.plan_scene(LAY, bytes(scene()), bytes(5))   # bad current -> full apply
        self.assertEqual(plan.ccs[:2], [(3, 1), (9, 1)])


class RealLayoutTest(unittest.TestCase):
    def test_respects_layout_cc_map(self):
        lay = Layout.load()
        with open(DEFAULT_SCENE, "rb") as f:
            sc = f.read()
        plan = A.plan_scene(lay, sc)
        n_models = len(plan.model_steps)
        self.assertTrue(all(s.what == "model" for s in plan.steps[:n_models]))
        self.assertTrue(all(s.what != "model" for s in plan.steps[n_models:]))
        for st in plan.steps:
            b = lay.block_by_id(st.block)
            self.assertLessEqual(st.value, 127)
            if st.what == "model":
                self.assertEqual((st.cc, st.offset), (b.cc_switch, b.model_offset))
            else:
                p = b.current_model(sc).param(st.what)
                self.assertEqual(st.cc, b.cc_params[p.slot])
                self.assertEqual(st.offset, p.offset)
        # the hidden DLY slot (0x41 = 213) is never sent
        self.assertNotIn(0x41, [s.offset for s in plan.steps])
        self.assertIn("Alkalmazva", A.summarize(plan))


if __name__ == "__main__":
    unittest.main()
