import threading
import time
import unittest

from mg300edit.writer import CoalescingWriter

SX = bytes.fromhex("F04358701500F7")


def cc(v, n=20):
    return bytes([0xB0, n, v])


class Recorder:
    def __init__(self):
        self.items = []
        self.lock = threading.Lock()

    def __call__(self, msg):
        with self.lock:
            self.items.append((time.monotonic(), bytes(msg)))

    @property
    def msgs(self):
        with self.lock:
            return [m for _, m in self.items]

    @property
    def times(self):
        with self.lock:
            return [t for t, _ in self.items]


class WriterTest(unittest.TestCase):
    def make(self, **kw):
        rec = Recorder()
        w = CoalescingWriter(rec, **kw)
        w.start()
        self.addCleanup(w.stop)
        return w, rec

    def test_sysex_gap_enforced(self):
        w, rec = self.make(sysex_gap=0.030, channel_gap=0.001)
        for _ in range(5):
            w.send(SX)
        self.assertTrue(w.flush(2))
        t = rec.times
        self.assertEqual(len(t), 5)
        for a, b in zip(t, t[1:]):
            self.assertGreaterEqual(b - a, 0.028)

    def test_channel_gap_smaller(self):
        w, rec = self.make(sysex_gap=0.040, channel_gap=0.005)
        for i in range(6):
            w.send(cc(i))
        self.assertTrue(w.flush(2))
        t = rec.times
        self.assertEqual(rec.msgs, [cc(i) for i in range(6)])
        gaps = [b - a for a, b in zip(t, t[1:])]
        self.assertTrue(all(g >= 0.0045 for g in gaps), gaps)
        self.assertLess(t[-1] - t[0], 5 * 0.040)     # clearly faster than SysEx pacing

    def test_channel_after_sysex_waits_sysex_gap(self):
        w, rec = self.make(sysex_gap=0.040, channel_gap=0.001)
        w.send(SX)
        w.send(cc(1))
        self.assertTrue(w.flush(2))
        t = rec.times
        self.assertGreaterEqual(t[1] - t[0], 0.038)

    def test_coalesce_keeps_only_latest(self):
        w, rec = self.make(sysex_gap=0.060, channel_gap=0.005)
        w.send(SX)                       # goes out now; following CCs must wait 60 ms
        time.sleep(0.005)
        for v in range(100):
            w.send(cc(v), key="AMP.Gain")
        self.assertTrue(w.flush(2))
        self.assertEqual(rec.msgs, [SX, cc(99)])
        self.assertEqual(w.coalesced_count, 99)

    def test_slider_drag_no_backlog(self):
        gap = 0.020
        w, rec = self.make(sysex_gap=0.040, channel_gap=gap)
        t0 = time.monotonic()
        v = 0
        while time.monotonic() - t0 < 0.300:       # ~1 kHz slider updates for 300 ms
            w.send(cc(v % 128), key="AMP.Gain")
            v += 1
            time.sleep(0.001)
        last = cc((v - 1) % 128)
        t_end = time.monotonic()
        self.assertTrue(w.flush(1))
        done = time.monotonic()
        # nothing left to drain beyond one gap, and far fewer writes than updates
        self.assertLess(done - t_end, gap + 0.015)
        self.assertLessEqual(len(rec.msgs), int(0.300 / gap) + 3)
        self.assertGreater(v, 50)
        self.assertEqual(rec.msgs[-1], last)

    def test_coalesced_slot_keeps_queue_position(self):
        w, rec = self.make(sysex_gap=0.050, channel_gap=0.001)
        w.send(SX)                     # blocks the queue for 50 ms
        time.sleep(0.005)
        w.send(cc(1, 1))
        w.send(cc(10), key="k")
        w.send(cc(2, 2))
        w.send(cc(11), key="k")
        self.assertTrue(w.flush(2))
        self.assertEqual(rec.msgs, [SX, cc(1, 1), cc(11), cc(2, 2)])

    def test_move_to_end(self):
        w, rec = self.make(sysex_gap=0.050, channel_gap=0.001)
        w.send(SX)
        time.sleep(0.005)
        w.send(bytes([0xC0, 1]), key="pc")
        w.send(SX, key="get", move_to_end=True)
        w.send(bytes([0xC0, 2]), key="pc")
        w.send(SX, key="get", move_to_end=True)
        self.assertTrue(w.flush(2))
        self.assertEqual(rec.msgs, [SX, bytes([0xC0, 2]), SX])

    def test_write_error_does_not_kill_thread(self):
        calls = []

        def bad(msg):
            calls.append(msg)
            if len(calls) == 1:
                raise OSError("boom")

        errors = []
        w = CoalescingWriter(bad, sysex_gap=0.001, channel_gap=0.001, on_error=errors.append)
        w.start()
        self.addCleanup(w.stop)
        w.send(cc(1))
        w.send(cc(2))
        self.assertTrue(w.flush(1))
        time.sleep(0.01)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(errors), 1)


if __name__ == "__main__":
    unittest.main()
