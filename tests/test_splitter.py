import unittest

from mg300edit.midistream import StreamSplitter
from mg300edit import protocol as P


class SplitterTest(unittest.TestCase):
    def test_sysex_split_across_reads(self):
        msg = P.edit_buffer_response(8, 0, bytes(range(126)))
        s = StreamSplitter()
        out = []
        for i in range(0, len(msg), 7):
            out += s.feed(msg[i:i + 7])
        self.assertEqual(out, [msg])

    def test_byte_by_byte(self):
        stream = bytes.fromhex("F04358700302007800F7 C008 B01440 F0435800F7")
        s = StreamSplitter()
        out = []
        for b in stream:
            out += s.feed(bytes([b]))
        self.assertEqual(out, [bytes.fromhex("F04358700302007800F7"), bytes([0xC0, 8]),
                               bytes([0xB0, 0x14, 0x40]), bytes.fromhex("F0435800F7")])

    def test_realtime_interleaved_in_sysex(self):
        s = StreamSplitter()
        out = s.feed(bytes.fromhex("F04358F8700C"))
        out += s.feed(bytes.fromhex("00FEF7"))
        self.assertEqual(out, [b"\xF8", b"\xFE", bytes.fromhex("F04358700C00F7")])

    def test_realtime_dropped_when_disabled(self):
        s = StreamSplitter(keep_realtime=False)
        self.assertEqual(s.feed(bytes.fromhex("F8B0F80140")), [bytes([0xB0, 1, 0x40])])

    def test_realtime_inside_running_status(self):
        s = StreamSplitter()
        out = s.feed(bytes.fromhex("B0 01 F8 40 02 41".replace(" ", "")))
        self.assertEqual(out, [b"\xF8", bytes([0xB0, 1, 0x40]), bytes([0xB0, 2, 0x41])])

    def test_running_status(self):
        s = StreamSplitter()
        self.assertEqual(s.feed(bytes.fromhex("C00102")), [bytes([0xC0, 1]), bytes([0xC0, 2])])
        self.assertEqual(s.feed(bytes.fromhex("B00A")), [])
        self.assertEqual(s.feed(bytes.fromhex("7F0B00")), [bytes([0xB0, 0x0A, 0x7F]), bytes([0xB0, 0x0B, 0x00])])

    def test_sysex_cancels_running_status(self):
        s = StreamSplitter()
        out = s.feed(bytes.fromhex("C001F07EF705"))
        self.assertEqual(out, [bytes([0xC0, 1]), bytes.fromhex("F07EF7")])
        self.assertEqual(s.dropped, 1)

    def test_truncated_sysex_discarded(self):
        s = StreamSplitter()
        out = s.feed(bytes.fromhex("F0435870C005F0435800F7"))
        self.assertEqual(out, [bytes([0xC0, 5]), bytes.fromhex("F0435800F7")])
        self.assertGreater(s.dropped, 0)

    def test_stray_data_and_f7(self):
        s = StreamSplitter()
        self.assertEqual(s.feed(bytes.fromhex("0102F7")), [])
        self.assertEqual(s.dropped, 3)

    def test_system_common(self):
        s = StreamSplitter()
        self.assertEqual(s.feed(bytes.fromhex("F10102F6")), [bytes([0xF1, 1]), bytes([0xF6])])
        self.assertEqual(s.dropped, 1)


if __name__ == "__main__":
    unittest.main()
