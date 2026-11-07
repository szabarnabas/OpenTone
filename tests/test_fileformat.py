"""Preset file export/import (mg300edit/fileformat.py)."""
import json
import os
import tempfile
import unittest

from mg300edit import fileformat as F
from mg300edit.fake import DEFAULT_SCENE, FakeDevice

with open(DEFAULT_SCENE, "rb") as _f:
    DEV = FakeDevice(_f.read())
S = [DEV.stored_scene(8, i) for i in range(3)]


def qt_file(scenes=S, size=F.QT_EXPECTED_SIZE, header=F.QT_HEADER):
    body = header + b"".join(scenes)
    return body + bytes(max(0, size - len(body)))


class JsonTest(unittest.TestCase):
    def test_preset_round_trip(self):
        p = F.PresetData(name="Surf Rock Clean", preset_index=8, scenes=list(S), edit_buffer=S[1])
        text = F.dump_preset(p)
        d = json.loads(text)
        self.assertEqual((d["format"], d["version"], d["device"]), (F.FORMAT_PRESET, 1, "MG-300 MKII"))
        self.assertEqual(d["label"], "User 3A")
        self.assertEqual(len(d["scenes"]), 3)
        self.assertEqual(len(d["edit_buffer"]), 252)
        (back,) = F.load_bytes(text.encode(), "x.mg300.json")
        self.assertEqual((back.name, back.preset_index, back.scenes, back.edit_buffer),
                         ("Surf Rock Clean", 8, list(S), S[1]))
        self.assertEqual([k for k, _ in back.sources()], ["edit", "scene0", "scene1", "scene2"])

    def test_bundle_round_trip(self):
        items = [F.PresetData(name=f"P{i}", preset_index=i, scenes=[DEV.stored_scene(i, s) for s in range(3)])
                 for i in range(36)]
        back = F.load_bytes(F.dump_bundle(items).encode())
        self.assertEqual(len(back), 36)
        self.assertEqual(back[9].preset_index, 9)
        self.assertEqual(back[9].scenes[2], DEV.stored_scene(9, 2))
        self.assertTrue(all(b.source == "bundle" for b in back))

    def test_missing_scene_allowed_as_null(self):
        p = F.PresetData(name="x", scenes=[S[0], None, S[2]])
        (back,) = F.load_bytes(F.dump_preset(p).encode())
        self.assertEqual(back.scenes[1], None)
        self.assertEqual([k for k, _ in back.sources()], ["scene0", "scene2"])

    def bad(self, d):
        with self.assertRaises(F.FileFormatError):
            F.load_bytes(json.dumps(d).encode())

    def test_validation(self):
        ok = F.preset_to_dict(F.PresetData(name="x", scenes=list(S)))
        self.bad({**ok, "format": "other"})
        self.bad({**ok, "version": 99})
        self.bad({**ok, "version": "1"})
        self.bad({**ok, "device": "MG-30"})
        self.bad({**ok, "scenes": [S[0].hex()[:-2]]})              # 125 bytes
        self.bad({**ok, "scenes": [S[0].hex() + "00"]})            # 127 bytes
        self.bad({**ok, "scenes": ["zz" * 126]})
        self.bad({**ok, "scenes": [S[0].hex()] * 4})
        self.bad({**ok, "scenes": "abc"})
        self.bad({**ok, "scenes": [], "edit_buffer": None})
        self.bad({**ok, "edit_buffer": 5})
        self.bad({"format": F.FORMAT_BUNDLE, "version": 1, "presets": []})
        self.bad({"format": F.FORMAT_BUNDLE, "version": 1, "presets": [ok] * 200})
        self.bad({"format": F.FORMAT_BUNDLE, "version": 1, "presets": [1]})
        self.bad([1, 2])
        with self.assertRaises(F.FileFormatError):
            F.load_bytes(b"{not json")
        with self.assertRaises(F.FileFormatError):
            F.load_bytes(b"{" + b" " * F.MAX_JSON_SIZE)

    def test_bad_index_and_name_fallback(self):
        d = F.preset_to_dict(F.PresetData(scenes=list(S)))
        d["preset_index"], d["name"] = 500, None
        (p,) = F.load_bytes(json.dumps(d).encode())
        self.assertIsNone(p.preset_index)
        self.assertEqual(p.name, "Surf Rock Clean")


class QuickToneTest(unittest.TestCase):
    def test_parse(self):
        (p,) = F.load_bytes(qt_file(), "a.mg300MK2patch")
        self.assertEqual(p.source, "quicktone")
        self.assertEqual(p.scenes, list(S))
        self.assertEqual(p.name, "Surf Rock Clean")
        self.assertIsNone(p.edit_buffer)
        self.assertIsNone(p.preset_index)

    def test_detect_by_header_without_extension(self):
        (p,) = F.load_bytes(qt_file(), "noext")
        self.assertEqual(p.scenes[1], S[1])

    def test_validation(self):
        for data in (qt_file(size=0)[:100], qt_file(header=b"\x12\x00\x00\x00"),
                     qt_file(size=F.MAX_QT_SIZE + 1)):
            with self.assertRaises(F.FileFormatError):
                F.load_bytes(data, "a.mg300MK2patch")
        with self.assertRaises(F.FileFormatError):
            F.load_bytes(b"\x00garbage", "a.bin")

    def test_load_path_cap(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.mg300MK2patch")
            with open(path, "wb") as f:
                f.write(qt_file())
            self.assertEqual(F.load_path(path)[0].scenes[2], S[2])


if __name__ == "__main__":
    unittest.main()
