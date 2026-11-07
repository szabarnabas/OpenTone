import json
import os
import tempfile
import unittest
from pathlib import Path

from mg300edit.layout import Layout

HERE = os.path.dirname(__file__)
SCENE = os.path.join(HERE, "..", "research", "scene_a.bin")
DRAFT = os.path.join(HERE, "..", "research", "layout_draft.json")


class LayoutSceneATest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene = Path(SCENE).read_bytes()
        cls.layout = Layout.load()       # bundled placeholder

    def test_verified_fields(self):
        lay, s = self.layout, self.scene
        self.assertEqual(lay.scene_size, len(s))
        self.assertEqual(lay.scene_name(s), "Surf Rock Clean")
        self.assertEqual(lay.scene_tempo(s), 120)
        self.assertEqual(lay.scene_block_order(s), [5, 1, 2, 3, 9, 4, 6, 7, 8, 10])

    def test_chain_and_blocks(self):
        chain = self.layout.chain(self.layout.scene_block_order(self.scene))
        self.assertEqual([b.id for b in chain],
                         ["NR", "CMP", "EFX", "AMP", "CAB", "EQ", "MOD", "DLY", "RVB", "PL"])
        amp = self.layout.block_by_id("AMP")
        self.assertEqual(amp.model_index(self.scene), 9)       # byte 0x03 = 0x09
        self.assertFalse(amp.is_bypassed(self.scene))
        model = amp.current_model(self.scene)
        self.assertEqual(model.name, "CLASS A30")
        self.assertEqual(model.param("GAIN").read(self.scene), 0x19)
        efx = self.layout.block_by_id("EFX")                   # 0x45 = model 5, bypassed
        self.assertEqual(efx.model_index(self.scene), 5)
        self.assertTrue(efx.is_bypassed(self.scene))

    def test_edit_helpers(self):
        s = bytearray(self.scene)
        efx = self.layout.block_by_id("EFX")
        efx.set_bypassed(s, False)
        self.assertEqual(s[2], 0x05)
        efx.set_model(s, 1)
        self.assertEqual(s[2], 0x01)
        efx.set_bypassed(s, True)
        self.assertEqual(s[2], 0x41)
        amp = self.layout.block_by_id("AMP")
        p = amp.current_model(s).param("GAIN")
        self.assertEqual(p.write(s, 250), 100)                 # clamped
        self.assertEqual(s[28], 100)
        self.layout.set_scene_tempo(s, 200)                    # 14-bit: hi=1, lo=72
        self.assertEqual((s[91], s[92]), (1, 72))
        self.assertEqual(self.layout.scene_tempo(s), 200)

    def test_unknown_model_and_block(self):
        amp = self.layout.block_by_id("AMP")
        m = amp.model(42)
        self.assertEqual(m.name, "Model #42")
        self.assertFalse(m.known)
        b = self.layout.block_by_chain_id(77)
        self.assertEqual(b.id, "#77")
        self.assertIsNone(b.model_index(self.scene))
        self.assertIsNone(b.is_bypassed(self.scene))


class TolerantLoaderTest(unittest.TestCase):
    def test_garbage_and_missing_fields(self):
        lay = Layout.from_dict({
            "name": {"offset": "0x69"},
            "blocks": [
                "junk",
                {"id": "X"},
                {"chain_id": 4, "model_offset": 200, "models": [
                    {"name": "no index"}, {"index": 3, "params": [{"offset": 1}, {"name": "P", "min": 9, "max": 1}]}]},
            ]})
        self.assertEqual(lay.name_offset, 0x69)
        self.assertIsNone(lay.tempo_offset)
        self.assertEqual(len(lay.blocks), 2)
        b = lay.blocks[1]
        self.assertEqual(b.id, "BLOCK2")
        self.assertIsNone(b.model_index(bytes(126)))           # offset beyond scene
        p = b.model(3).params[0]
        self.assertEqual((p.name, p.min, p.max, p.offset), ("P", 1, 9, None))
        self.assertIsNone(p.read(bytes(126)))
        self.assertEqual(lay.scene_tempo(bytes(126)), None)

    def test_missing_file(self):
        lay = Layout.load("/nonexistent/layout.json")
        self.assertEqual(lay.blocks, [])
        self.assertEqual(lay.scene_name(bytes(126)), "")

    def test_spec_example_shape(self):
        d = {"scene_size": 126, "name": {"offset": 105, "length": 16}, "tempo": {"offset": 92},
             "block_order": {"offset": 93, "length": 10},
             "blocks": [{"id": "AMP", "chain_id": 3, "model_offset": 3, "bypass_bit": 64,
                         "models": [{"index": 1, "name": "A",
                                     "params": [{"name": "Gain", "offset": 32, "min": 0, "max": 100,
                                                 "default": 50}]}]}]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(d, f)
        self.addCleanup(os.unlink, f.name)
        lay = Layout.load(f.name)
        self.assertEqual(lay.blocks[0].models[0].params[0].default, 50)
        self.assertEqual(lay.scene_name(Path(SCENE).read_bytes()), "Surf Rock Clean")


@unittest.skipUnless(os.path.exists(DRAFT), "research/layout_draft.json not present yet")
class DraftLayoutTest(unittest.TestCase):
    def setUp(self):
        self.lay = Layout.load(DRAFT)
        self.scene = Path(SCENE).read_bytes()

    def test_draft_decodes_scene_a(self):
        lay, scene = self.lay, self.scene
        self.assertEqual(lay.scene_name(scene), "Surf Rock Clean")
        self.assertEqual(lay.scene_tempo(scene), 120)
        names = {}
        for b in lay.chain(lay.scene_block_order(scene)):
            m = b.current_model(scene)
            names[b.id] = m.name if m else None
            b.is_bypassed(scene)
        self.assertEqual(names["AMP"], "CLASS A30")
        self.assertEqual(names["RVB"], "SPRING")

    def test_alt_models(self):
        cmp_ = self.lay.block_by_id("CMP")
        self.assertEqual(cmp_.alt_bit, 0x20)
        s = bytearray(self.scene)
        cmp_.set_model(s, 0x20 | 1)                # alt list entry 1, keep bypass bit
        self.assertEqual(s[1] & 0x3F, 0x21)
        self.assertTrue(cmp_.current_model(s).known)
        self.assertIn("[", cmp_.current_model(s).name)

    def test_cc_writer_from_layout(self):
        from mg300edit import protocol as P
        w = P.CCParamWriter.from_layout(self.lay)
        amp = self.lay.block_by_id("AMP")
        gain = amp.current_model(self.scene).param("GAIN")
        out = w.set_param("AMP", gain, 60)
        self.assertEqual(out[0][1], bytes([0xB0, amp.cc_params[gain.slot], 60]))
        out = w.set_bypass("AMP", True, 0x49)
        self.assertEqual(out[0][1], bytes([0xB0, amp.cc_switch, 0x49]))
        self.assertEqual(w.lookup_cc(0, amp.cc_params[0]), ("AMP", "#0"))


if __name__ == "__main__":
    unittest.main()
