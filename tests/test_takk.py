"""Takk (metronome) tests: click generator, routing, tempo following, pactl parsing.
No audio device, no PipeWire and no MIDI hardware is touched."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from array import array
from pathlib import Path

from mg300edit import protocol as P
from mg300edit.fake import FakeTransport
from takkapp import audio, settings
from takkapp import tempo as T
from takkapp.click import (CLICK_MS, FULL_SCALE, OUTPUTS, RATE, ClickGenerator, clamp_bpm)
from tests.test_controller import Pump

CLICK_LEN = RATE * CLICK_MS // 1000


def render_all(gen: ClickGenerator, frames: int, chunk: int = 480):
    """Render `frames` in chunks; returns (samples array, absolute beat list)."""
    out = array("h")
    beats = []
    while frames > 0:
        n = min(chunk, frames)
        data, bs = gen.render(n)
        out.frombytes(data)
        beats += bs
        frames -= n
    return out, beats


def channel(samples: array, ch: int, channels: int = 4) -> array:
    return samples[ch::channels]


class ClickGeneratorTest(unittest.TestCase):
    def test_beats_every_24000_samples_at_120(self):
        g = ClickGenerator(bpm=120)
        _, beats = render_all(g, 5 * RATE)
        self.assertEqual([b.position for b in beats], [i * 24000 for i in range(10)])

    def test_chunk_size_does_not_change_output(self):
        a, ba = render_all(ClickGenerator(bpm=133), 3 * RATE, chunk=480)
        b, bb = render_all(ClickGenerator(bpm=133), 3 * RATE, chunk=1001)
        self.assertEqual(a, b)
        self.assertEqual([x.position for x in ba], [x.position for x in bb])

    def test_click_sits_at_beat_position(self):
        g = ClickGenerator(bpm=120)
        s, _ = render_all(g, RATE)
        left = channel(s, 0)
        self.assertTrue(any(left[0:CLICK_LEN]))
        self.assertTrue(any(left[24000:24000 + CLICK_LEN]))
        self.assertFalse(any(left[CLICK_LEN:24000]))           # silence between clicks
        self.assertFalse(any(left[24000 + CLICK_LEN:]))

    def test_fractional_period_does_not_drift(self):
        g = ClickGenerator(bpm=137)                             # 21021.9.. frames per beat
        _, beats = render_all(g, 60 * RATE)
        self.assertEqual(len(beats), 137)
        for i, b in enumerate(beats):
            self.assertEqual(b.position, round(i * RATE * 60 / 137))

    def test_bpm_change_applies_at_next_beat(self):
        g = ClickGenerator(bpm=120)
        render_all(g, 30000)                                    # beats at 0 and 24000 sounded
        g.set_bpm(60)                                           # next beat: 24000 + 48000
        _, beats = render_all(g, 100000)
        self.assertEqual([b.position for b in beats], [72000, 120000])

    def test_bpm_change_faster_no_doubled_click(self):
        g = ClickGenerator(bpm=60)                              # beats at 0, 48000
        render_all(g, 10000)
        g.set_bpm(240)                                          # 12000 frames: 0+12000 is still ahead
        _, beats = render_all(g, 30000)
        self.assertEqual([b.position for b in beats], [12000, 24000, 36000])

    def test_bpm_change_when_next_beat_already_due_plays_now(self):
        g = ClickGenerator(bpm=40)                              # 72000 frames per beat
        render_all(g, 30000)
        g.set_bpm(250)                                          # 0 + 11520 is in the past -> right now
        _, beats = render_all(g, 20000)
        self.assertEqual(beats[0].position, 30000)
        self.assertEqual(beats[1].position, 30000 + 11520)
        # gap is never shorter than the new period
        gaps = [b.position - a.position for a, b in zip(beats, beats[1:])]
        self.assertTrue(all(gp >= 11520 for gp in gaps))

    def test_bpm_change_mid_click_keeps_click_whole(self):
        g = ClickGenerator(bpm=120)
        ref, _ = render_all(ClickGenerator(bpm=120), CLICK_LEN)
        a, _ = render_all(g, 300)
        g.set_bpm(200)
        b, _ = render_all(g, CLICK_LEN - 300)
        self.assertEqual(a + b, ref)

    def test_same_bpm_is_a_noop(self):
        g = ClickGenerator(bpm=120)
        render_all(g, 30000)
        g.set_bpm(120)
        _, beats = render_all(g, 30000)
        self.assertEqual([b.position for b in beats], [48000])

    def test_bpm_clamped(self):
        self.assertEqual(clamp_bpm(10), 40)
        self.assertEqual(clamp_bpm(999), 250)
        self.assertEqual(ClickGenerator(bpm=300).bpm, 250)

    def test_accent_pattern(self):
        g = ClickGenerator(bpm=120)
        _, beats = render_all(g, 8 * 24000)
        self.assertEqual([b.index for b in beats], [0, 1, 2, 3, 0, 1, 2, 3])
        self.assertEqual([b.accent for b in beats], [True, False, False, False] * 2)

    def test_accent_has_higher_pitch(self):
        def zero_crossings(seg):
            return sum(1 for x, y in zip(seg, seg[1:]) if (x < 0) != (y < 0))
        s, _ = render_all(ClickGenerator(bpm=120), 48000)
        left = channel(s, 0)
        self.assertGreater(zero_crossings(left[0:CLICK_LEN]),
                           zero_crossings(left[24000:24000 + CLICK_LEN]) * 1.3)

    def test_accent_off(self):
        g = ClickGenerator(bpm=120, accent=False)
        s, beats = render_all(g, 48000)
        self.assertFalse(any(b.accent for b in beats))
        left = channel(s, 0)
        self.assertEqual(left[0:CLICK_LEN], left[24000:24000 + CLICK_LEN])

    def test_volume_scaling(self):
        peaks = {}
        for v in (0.0, 0.3, 0.6, 1.0):
            s, _ = render_all(ClickGenerator(bpm=120, volume=v), 48000)
            peaks[v] = max(abs(x) for x in channel(s, 0)[24000:24000 + CLICK_LEN])
        self.assertEqual(peaks[0.0], 0)
        self.assertAlmostEqual(peaks[0.6] / peaks[1.0], 0.6, delta=0.01)
        self.assertAlmostEqual(peaks[0.3] / peaks[1.0], 0.3, delta=0.01)

    def test_volume_change_takes_effect(self):
        g = ClickGenerator(bpm=120, volume=1.0)
        render_all(g, 24000)
        g.set_volume(0.5)
        b, _ = render_all(g, 24000)
        ref = ClickGenerator(bpm=120, volume=0.5)
        render_all(ref, 24000)
        expect, _ = render_all(ref, 24000)
        self.assertEqual(b, expect)

    def test_no_clipping_and_ends_at_zero(self):
        g = ClickGenerator(bpm=250, volume=1.0)
        s, _ = render_all(g, 4 * RATE)
        self.assertLessEqual(max(s), FULL_SCALE)
        self.assertGreaterEqual(min(s), -FULL_SCALE)
        left = channel(s, 0)
        self.assertEqual(left[CLICK_LEN - 1], 0)               # faded to exactly 0: no pop
        self.assertEqual(left[0], 0)                            # starts from 0 too
        self.assertLess(max(map(abs, left[0:4])), FULL_SCALE * 0.1)    # soft 1 ms attack

    def test_reset_restarts_at_beat_one(self):
        g = ClickGenerator(bpm=120)
        render_all(g, 50000)
        g.reset()
        _, beats = render_all(g, 1000)
        self.assertEqual((beats[0].position, beats[0].index), (0, 0))


class RoutingTest(unittest.TestCase):
    def active_channels(self, route):
        s, _ = render_all(ClickGenerator(bpm=120, route=route), 24000)
        return [ch for ch in range(4) if any(channel(s, ch))]

    def test_outputs(self):
        self.assertEqual(self.active_channels(OUTPUTS["usb12"]), [0, 1])
        self.assertEqual(self.active_channels(OUTPUTS["usb34"]), [2, 3])
        self.assertEqual(self.active_channels(OUTPUTS["all"]), [0, 1, 2, 3])

    def test_frame_layout_is_interleaved_4ch(self):
        data, _ = ClickGenerator(bpm=120).render(100)
        self.assertEqual(len(data), 100 * 4 * 2)

    def test_routed_channels_identical(self):
        s, _ = render_all(ClickGenerator(bpm=120, route=OUTPUTS["all"]), 24000)
        self.assertEqual(channel(s, 0), channel(s, 3))

    def test_route_change_applies_with_next_click_without_cutting(self):
        g = ClickGenerator(bpm=120, route=OUTPUTS["usb12"])
        a, _ = render_all(g, 400)
        g.set_route(OUTPUTS["usb34"])                           # mid-click
        b, _ = render_all(g, 48000 - 400)
        s = a + b
        ref, _ = render_all(ClickGenerator(bpm=120), CLICK_LEN)
        self.assertEqual(channel(s, 0)[:CLICK_LEN], channel(ref, 0))   # first click finished on 1/2
        self.assertFalse(any(channel(s, 2)[:CLICK_LEN]))
        self.assertTrue(any(channel(s, 2)[24000:24000 + CLICK_LEN]))   # next one on 3/4
        self.assertFalse(any(channel(s, 0)[CLICK_LEN:]))
        # beat timing unaffected by the switch
        self.assertEqual(len(s), 48000 * 4)

    def test_bad_route(self):
        with self.assertRaises(ValueError):
            ClickGenerator(route=(4,))
        with self.assertRaises(ValueError):
            ClickGenerator(route=())


class FakePedal(FakeTransport):
    """FakeTransport whose writes are recorded (and which can stay 'disconnected')."""

    def __init__(self, **kw):
        super().__init__(latency=0.002, sysex_gap=0.0, channel_gap=0.0, **kw)
        self.sent = []
        self.last_errno = 0

    def _write(self, msg):
        self.sent.append(bytes(msg))
        super()._write(msg)

    def tempo_gets(self):
        return sum(1 for m in self.sent if m == P.get_tempo())


class TempoFollowerTest(unittest.TestCase):
    def setUp(self):
        self.pump = Pump()
        self.tr = FakePedal(dispatch=self.pump.dispatch)
        self.tempi = []
        self.states = []
        self.f = T.TempoFollower(self.tr, on_tempo=self.tempi.append,
                                 on_state=lambda s, d: self.states.append(s))
        self.f.start()
        self.addCleanup(self.f.stop)

    def test_reads_tempo_on_connect(self):
        self.assertTrue(self.pump.until(lambda: self.f.bpm == 120))
        self.assertEqual(self.states[0], T.STATE_CONNECTED)
        self.assertIn(P.get_tempo(), self.tr.sent)
        self.assertEqual(P.get_tempo(), bytes.fromhex("F04358700300F7"))

    def test_poll_follows_tempo_change(self):
        self.pump.until(lambda: self.f.bpm == 120)
        self.tr.device.tempo = 97                       # e.g. tempo knob: no notification
        self.f.poll()
        self.assertTrue(self.pump.until(lambda: self.f.bpm == 97))
        self.assertEqual(self.tempi, [120, 97])

    def test_tap_notification_requeries_immediately(self):
        self.pump.until(lambda: self.f.bpm == 120)
        n = self.tr.tempo_gets()
        self.tr.simulate_tap(88)                        # 7E 02 03 notification, no poll()
        self.assertTrue(self.pump.until(lambda: self.f.bpm == 88))
        self.assertEqual(self.tr.tempo_gets(), n + 1)

    def test_program_change_requeries(self):
        self.pump.until(lambda: self.f.bpm == 120)
        self.tr.device.tempo = 150
        self.tr.simulate_preset_change(3)               # C0 03
        self.assertTrue(self.pump.until(lambda: self.f.bpm == 150))

    def test_scene_change_requeries(self):
        self.pump.until(lambda: self.f.bpm == 120)
        self.tr.device.tempo = 66
        self.tr.simulate_scene_change(2)                # B0 4E .., B0 50 02
        self.assertTrue(self.pump.until(lambda: self.f.bpm == 66))

    def test_polls_never_queue_up(self):
        self.pump.until(lambda: self.f.bpm == 120)
        self.tr.writer.sysex_gap = 0.2                  # slow link: requests would pile up
        n = self.tr.tempo_gets()
        for _ in range(20):
            self.f.poll()
        time.sleep(0.5)
        self.assertLessEqual(self.tr.tempo_gets() - n, 2)

    def test_out_of_range_reply_ignored(self):
        self.pump.until(lambda: self.f.bpm == 120)
        self.f._on_messages([P.tempo_response(300), P.tempo_response(20)])
        self.assertEqual(self.f.bpm, 120)
        self.f._on_messages([P.tempo_response(250)])
        self.assertEqual(self.f.bpm, 250)

    def test_disconnect_clears_tempo(self):
        self.pump.until(lambda: self.f.bpm == 120)
        self.tr._set_status(False, "Kapcsolat bontva")
        self.assertTrue(self.pump.until(lambda: self.f.bpm is None))
        self.assertEqual(self.f.state, T.STATE_NO_PEDAL)
        n = len(self.tr.sent)
        self.f.poll()                                   # nothing is sent while disconnected
        time.sleep(0.05)
        self.assertEqual(len(self.tr.sent), n)

    def test_busy_port_state(self):
        import errno
        self.tr.last_errno = errno.EBUSY
        self.tr._set_status(False, "Nem nyitható meg: /dev/snd/midiC2D0 (Device or resource busy)")
        self.assertTrue(self.pump.until(lambda: self.f.state == T.STATE_BUSY))
        self.tr.last_errno = errno.EACCES
        self.tr._set_status(False, "Nem nyitható meg (Permission denied)")
        self.assertTrue(self.pump.until(lambda: self.f.state == T.STATE_ERROR))

    def test_triggers(self):
        self.assertTrue(T.is_tempo_trigger(bytes.fromhex("F04358707E020300000000000000F7")))
        self.assertTrue(T.is_tempo_trigger(bytes([0xC0, 5])))
        self.assertTrue(T.is_tempo_trigger(bytes([0xB0, 0x50, 1])))
        self.assertFalse(T.is_tempo_trigger(bytes([0xB0, 0x4E, 1])))
        self.assertFalse(T.is_tempo_trigger(P.modified_notification()))


PACTL_JSON = json.dumps([
    {"index": 54, "state": "RUNNING", "name": "alsa_output.pci-0000_00_1f.3.analog-stereo",
     "description": "Built-in Audio Analog Stereo", "channel_map": "front-left,front-right",
     "mute": False, "volume": {"front-left": {"value": 65536, "value_percent": "100%"},
                               "front-right": {"value": 65536, "value_percent": "100%"}},
     "properties": {}},
    {"index": 431, "state": "SUSPENDED",
     "name": "alsa_output.usb-NUX_NUX_MG-300V2_2607081100-00.analog-surround-40",
     "description": "NUX MG-300V2 Analog Surround 4.0",
     "sample_specification": "s32le 4ch 48000Hz",
     "channel_map": "front-left,front-right,rear-left,rear-right", "mute": True,
     "volume": {c: {"value": 22937, "value_percent": "35%", "db": "-27.36 dB"}
                for c in ("front-left", "front-right", "rear-left", "rear-right")},
     "properties": {"device.description": "NUX MG-300V2", "media.class": "Audio/Sink"}},
])

PACTL_SHORT = (
    "54\talsa_output.pci-0000_00_1f.3.analog-stereo\tPipeWire\ts32le 2ch 44100Hz\tRUNNING\n"
    "420\talsa_output.usb-DisplayLink_Dock-02.analog-stereo\tPipeWire\ts16le 2ch 48000Hz\tSUSPENDED\n"
    "431\talsa_output.usb-NUX_NUX_MG-300V2_2607081100-00.analog-surround-40\tPipeWire\t"
    "s32le 4ch 48000Hz\tSUSPENDED\n")


class SinkParsingTest(unittest.TestCase):
    def test_json(self):
        sinks = audio.parse_sinks(PACTL_JSON)
        s = audio.find_pedal_sink(sinks)
        self.assertEqual(s.name, "alsa_output.usb-NUX_NUX_MG-300V2_2607081100-00.analog-surround-40")
        self.assertIs(s.mute, True)
        self.assertEqual(s.volume_percent, 35)
        self.assertEqual(s.channel_map, ("FL", "FR", "RL", "RR"))
        self.assertIs(sinks[0].mute, False)

    def test_short(self):
        s = audio.find_pedal_sink(audio.parse_short_sinks(PACTL_SHORT))
        self.assertEqual(s.name, "alsa_output.usb-NUX_NUX_MG-300V2_2607081100-00.analog-surround-40")
        self.assertIsNone(s.mute)

    def test_not_found_and_garbage(self):
        self.assertIsNone(audio.find_pedal_sink(audio.parse_sinks(json.dumps([json.loads(PACTL_JSON)[0]]))))
        self.assertEqual(audio.parse_sinks("not json"), [])
        self.assertEqual(audio.parse_sinks("{}"), [])
        self.assertEqual(audio.parse_short_sinks(""), [])

    def test_description_match_and_override(self):
        sinks = [audio.Sink("alsa_output.usb-0001.analog-stereo", "NUX MG-300V2"),
                 audio.Sink("null_test", "Null")]
        self.assertEqual(audio.find_pedal_sink(sinks).name, "alsa_output.usb-0001.analog-stereo")
        self.assertEqual(audio.find_pedal_sink(sinks, override="null_test").name, "null_test")
        self.assertIsNone(audio.find_pedal_sink(sinks, override="missing"))

    def test_stream_map_follows_sink(self):
        s = audio.find_pedal_sink(audio.parse_sinks(PACTL_JSON))
        self.assertEqual(audio.stream_channel_map(s), ("FL", "FR", "RL", "RR"))
        self.assertEqual(audio.stream_channel_map(audio.Sink("x", channel_map=("FL", "FR"))),
                         ("FL", "FR", "FC", "LFE"))

    def test_pwcat_command(self):
        cmd = audio.pwcat_command("sink.x", ("FL", "FR", "RL", "RR"))
        self.assertEqual(cmd[:3], ["pw-cat", "--playback", "--raw"])
        for flag, val in (("--rate", "48000"), ("--channels", "4"), ("--format", "s16"),
                          ("--latency", "256"), ("--target", "sink.x"),
                          ("--channel-map", "FL,FR,RL,RR")):
            self.assertEqual(cmd[cmd.index(flag) + 1], val)
        props = cmd[cmd.index("-P") + 1]
        self.assertIn("node.dont-fallback = true", props)      # never fall back to the laptop speakers
        self.assertIn("channelmix.upmix = false", props)
        self.assertEqual(cmd[-1], "-")


class PlayerTest(unittest.TestCase):
    """ClickPlayer against a stand-in for pw-cat that writes stdin to a file."""

    def test_streams_pcm_and_stops(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "pcm.raw"

            def cmd(target, cmap):
                # read ~0.5 s of audio then exit, like a pw-cat that went away
                return ["sh", "-c", f"head -c {RATE * 8 // 2} > {out}"]
            stopped = []
            beats = []
            p = audio.ClickPlayer(ClickGenerator(bpm=240, route=OUTPUTS["usb34"]),
                                  on_beat=lambda i, a, dl: beats.append(i),
                                  on_stopped=stopped.append, command=cmd)
            p.start("x")
            end = time.monotonic() + 5
            while not stopped and time.monotonic() < end:
                time.sleep(0.02)
            self.assertTrue(stopped)
            self.assertFalse(p.playing)
            s = array("h", out.read_bytes())
            self.assertEqual(len(s), RATE * 2)
            self.assertEqual([ch for ch in range(4) if any(channel(s, ch))], [2, 3])
            self.assertEqual(beats[:2], [0, 1])

    def test_stop_terminates(self):
        p = audio.ClickPlayer(ClickGenerator(), command=lambda t, m: ["sh", "-c", "cat > /dev/null"])
        p.start("x")
        time.sleep(0.1)
        proc = p._proc
        p.stop()
        self.assertFalse(p.playing)
        self.assertIsNotNone(proc.poll())


class SettingsTest(unittest.TestCase):
    def test_roundtrip_and_validation(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "takk" / "settings.json"
            self.assertEqual(settings.load(f), settings.DEFAULTS)
            settings.save({"volume": 35, "output": "usb34", "accent": False}, f)
            self.assertEqual(settings.load(f), {"volume": 35, "output": "usb34", "accent": False})
            f.write_text('{"volume": 900, "output": "bogus", "accent": "yes"}')
            self.assertEqual(settings.load(f), {**settings.DEFAULTS, "volume": 100})

    def test_xdg_path(self):
        old = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = "/tmp/xdgtest"
        try:
            self.assertEqual(settings.path(), Path("/tmp/xdgtest/takk/settings.json"))
        finally:
            if old is None:
                del os.environ["XDG_CONFIG_HOME"]
            else:
                os.environ["XDG_CONFIG_HOME"] = old


if __name__ == "__main__":
    unittest.main()
