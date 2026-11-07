import os
import random
import unittest
from pathlib import Path

from mg300edit import protocol as P

SCENE = os.path.join(os.path.dirname(__file__), "..", "research", "scene_a.bin")


class CodecTest(unittest.TestCase):
    def test_known_vectors(self):
        self.assertEqual(P.encode7(b"\x00\x00"), b"\x00\x00\x00")
        self.assertEqual(P.encode7(b"\xFF\xFF"), b"\x03\x7F\x7F")
        self.assertEqual(P.encode7(b"\x80\x80"), b"\x02\x01\x00")
        self.assertEqual(P.decode7(b"\x03\x7F\x7F"), b"\xFF\xFF")

    def test_round_trip_all_pairs(self):
        for d0 in range(256):
            for d1 in (0, 1, 0x7F, 0x80, 0xD5, 0xFF):
                data = bytes([d0, d1])
                enc = P.encode7(data)
                self.assertTrue(all(b < 0x80 for b in enc))
                self.assertEqual(P.decode7(enc), data)

    def test_round_trip_random(self):
        rnd = random.Random(1)
        for n in range(0, 300, 2):
            data = bytes(rnd.randrange(256) for _ in range(n))
            self.assertEqual(P.decode7(P.encode7(data)), data)

    def test_encode_decode_inverse_direction(self):
        # every valid 3-byte group (c0 in 0..3) survives decode -> encode
        for c0 in range(4):
            for c1 in range(128):
                for c2 in (0, 0x55, 0x7F):
                    g = bytes([c0, c1, c2])
                    self.assertEqual(P.encode7(P.decode7(g)), g)

    def test_odd_length_padded(self):
        self.assertEqual(P.decode7(P.encode7(b"\x12")), b"\x12\x00")

    def test_scene_a_sizes(self):
        data = Path(SCENE).read_bytes()
        self.assertEqual(len(data), 126)
        enc = P.encode7(data)
        self.assertEqual(len(enc), P.ENCODED_SCENE_SIZE)
        self.assertEqual(P.decode7(enc), data)


class MessageTest(unittest.TestCase):
    def test_edit_buffer_response_round_trip(self):
        data = Path(SCENE).read_bytes()
        msg = P.edit_buffer_response(8, 0, data)
        self.assertEqual(len(msg), 198)          # FACTS.md: 198-byte 0C reply
        self.assertEqual(msg[:8], bytes([0xF0, 0x43, 0x58, 0x70, 0x0C, 0x02, 8, 0]))
        eb = P.parse_edit_buffer(msg)
        self.assertEqual((eb.preset, eb.scene, eb.data), (8, 0, data))

    def test_requests(self):
        self.assertEqual(P.identity_request(), bytes.fromhex("F0435800F7"))
        self.assertEqual(P.get_edit_buffer(), bytes.fromhex("F04358700C00F7"))
        self.assertEqual(P.get_tempo(), bytes.fromhex("F04358700300F7"))
        self.assertEqual(P.get_current(), bytes.fromhex("F04358701500F7"))
        self.assertEqual(P.get_block_order(), bytes.fromhex("F04358700D00F7"))
        self.assertEqual(P.program_change(8), bytes([0xC0, 8]))

    def test_parse_real_responses(self):
        self.assertEqual(P.parse_tempo(bytes.fromhex("F04358700302007800000000F7")), 120)
        cur = P.parse_current(bytes.fromhex("F0435870150208035800F7"))
        self.assertEqual(cur.preset, 8)
        order = P.parse_block_order(bytes.fromhex("F04358700D02050102030904060708 0A0000F7".replace(" ", "")))
        self.assertEqual(order, [5, 1, 2, 3, 9, 4, 6, 7, 8, 10])
        self.assertIsNone(P.parse_tempo(bytes.fromhex("F04358707F020000F7")))

    def test_identity(self):
        msg = bytes([0xF0, 0x43, 0x58, 0x10]) + b"20260708110000\x00\x01\x02MG300V2\x00" + b"\xF7"
        ident = P.parse_identity(msg)
        self.assertEqual((ident.firmware, ident.model), ("20260708110000", "MG300V2"))

    def test_cc_writer(self):
        from mg300edit.layout import ParamSpec
        gain = ParamSpec("Gain", 28, 0, 100, slot=0)
        master = ParamSpec("Master", 29, 0, 100, slot=1)
        w = P.CCParamWriter({"AMP.Gain": 20, "AMP.bypass": 21, "DLY.#1": 30, "RVB.switch": 8})
        self.assertEqual(w.set_param("AMP", gain, 100), [("cc:0:20", bytes([0xB0, 20, 127]))])
        self.assertEqual(w.set_param("AMP", gain, 0)[0][1][2], 0)
        self.assertEqual(w.set_param("AMP", master, 5), [])
        self.assertEqual(w.set_param("DLY", master, 50)[0][1], bytes([0xB0, 30, 64]))
        self.assertEqual(w.set_bypass("AMP", True, 0x49)[0][1], bytes([0xB0, 21, 0]))
        self.assertEqual(w.set_bypass("RVB", True, 0x44)[0][1], bytes([0xB0, 8, 0x44]))
        self.assertEqual(w.set_model("RVB", 2, 0x02)[0][1], bytes([0xB0, 8, 0x02]))
        self.assertEqual(w.lookup_cc(0, 20), ("AMP", "Gain"))
        self.assertIsNone(w.lookup_cc(1, 20))
        raw = P.CCParamWriter({"AMP.Gain": 20}, scale=False)
        self.assertEqual(raw.set_param("AMP", gain, 100)[0][1][2], 100)
        self.assertEqual(P.ParamWriter().set_param("AMP", gain, 1), [])
        with self.assertRaises(NotImplementedError):
            P.SysExParamWriter().set_param("AMP", gain, 1)

    def test_parse_hex(self):
        self.assertEqual(P.parse_hex("f0 43,0x58 70"), bytes([0xF0, 0x43, 0x58, 0x70]))
        with self.assertRaises(ValueError):
            P.parse_hex("F0 4")


if __name__ == "__main__":
    unittest.main()
