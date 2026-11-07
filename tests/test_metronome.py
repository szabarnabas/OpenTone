"""OpenTone's built-in click (mg300edit/metronome.py) + its settings, without GTK.
pw-cat is replaced by a harmless stand-in command, the PipeWire sink watcher by a
fake, and pactl by a recorder: no audio device, PipeWire or MIDI is touched."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest

from mg300edit.metronome import Metronome
from mg300edit.settings import Settings
from mg300edit import clickaudio as audio
from mg300edit.click import FALLBACK_BPM, OUTPUTS

NUX = "alsa_output.usb-NUX_NUX_MG-300V2_2607081100-00.analog-surround-40"


def sink(mute=False, name=NUX):
    return audio.Sink(name, "NUX MG-300V2", mute=mute, channel_map=("FL", "FR", "RL", "RR"))


class FakeWatcher:
    """Stands in for clickaudio.SinkWatcher (no pactl)."""
    instances: list = []

    def __init__(self, on_sinks, dispatch):
        self.on_sinks = on_sinks
        self.started = self.stopped = False
        self.kicks = 0
        FakeWatcher.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def kick(self):
        self.kicks += 1


class StandIn:
    """Records the pw-cat command lines; runs `cat > /dev/null` instead."""

    def __init__(self):
        self.calls = []

    def __call__(self, target, channel_map):
        self.calls.append((target, tuple(channel_map)))
        return ["sh", "-c", "cat > /dev/null"]


def wait(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


class MetronomeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "settings.json")
        self.settings = Settings(self.path)
        self.cmd = StandIn()
        self.pactl_calls = []
        self.unmuted = threading.Event()

        def pactl(*args):
            self.pactl_calls.append(args)
            self.unmuted.set()
            return ""
        FakeWatcher.instances = []
        self.m = Metronome(self.settings, command=self.cmd, watcher_factory=FakeWatcher, pactl=pactl)
        self.addCleanup(self.m.shutdown)
        self.notices = []
        self.states = 0
        self.m.connect("notice", lambda k, t: self.notices.append(k))
        self.m.connect("state", lambda: setattr(self, "states", self.states + 1))

    @property
    def watcher(self):
        return FakeWatcher.instances[-1]

    def test_defaults_from_settings(self):
        self.assertEqual((self.m.volume, self.m.accent, self.m.output), (100, True, "usb12"))
        self.assertEqual(self.m.gen.route, OUTPUTS["usb12"])
        self.assertEqual(self.m.gen.volume, 1.0)

    def test_nothing_runs_until_enabled(self):
        self.assertFalse(self.m.playing)
        self.assertEqual(FakeWatcher.instances, [])
        self.assertEqual(self.cmd.calls, [])

    def test_enable_starts_player_once_sink_known(self):
        self.m.enable()
        self.assertTrue(self.watcher.started)
        self.assertFalse(self.m.playing)                  # not before the sink is known
        self.watcher.on_sinks([audio.Sink("laptop"), sink()])
        self.assertTrue(self.m.playing)
        self.assertEqual(self.cmd.calls, [(NUX, ("FL", "FR", "RL", "RR"))])
        self.watcher.on_sinks([audio.Sink("laptop"), sink()])   # same again: no restart
        self.assertEqual(len(self.cmd.calls), 1)
        self.m.disable()
        self.assertFalse(self.m.playing)
        self.assertTrue(self.watcher.stopped)
        self.assertFalse(self.m.enabled)

    def test_no_sink_turns_back_off(self):
        self.m.enable()
        self.watcher.on_sinks([audio.Sink("laptop")])
        self.assertEqual(self.notices, ["no-sink"])
        self.assertFalse(self.m.enabled)
        self.assertEqual(self.cmd.calls, [])

    def test_sink_disappearing_stops(self):
        self.m.enable()
        self.watcher.on_sinks([sink()])
        self.assertTrue(self.m.playing)
        self.watcher.on_sinks([audio.Sink("laptop")])
        self.assertEqual(self.notices, ["sink-lost"])
        self.assertFalse(self.m.playing)
        self.assertFalse(self.m.enabled)
        self.assertTrue(self.watcher.stopped)

    def test_muted_sink_notice_and_unmute_only_on_request(self):
        self.m.enable()
        self.watcher.on_sinks([sink(mute=True)])
        self.assertEqual(self.notices, ["muted"])
        self.assertTrue(self.m.playing)                   # plays anyway (user may unmute)
        self.watcher.on_sinks([sink(mute=True), audio.Sink("x")])
        self.assertEqual(self.notices, ["muted"])         # not repeated while still muted
        self.assertEqual(self.pactl_calls, [])            # never unmutes by itself
        self.m.unmute()                                   # = the toast's "Feloldás" button
        self.assertTrue(self.unmuted.wait(2))
        self.assertEqual(self.pactl_calls, [("set-sink-mute", NUX, "0")])
        self.assertTrue(wait(lambda: self.watcher.kicks == 1))
        self.watcher.on_sinks([sink(mute=False)])
        self.assertEqual(self.notices, ["muted", "unmuted"])

    def test_tempo_follow_and_fallback(self):
        self.assertIsNone(self.m.tempo)
        self.assertEqual(self.m.bpm, FALLBACK_BPM)
        self.assertEqual(self.m.gen.bpm, FALLBACK_BPM)
        self.m.set_tempo(96)
        self.assertEqual(self.m.gen.bpm, 96)
        self.m.enable()
        self.watcher.on_sinks([sink()])
        self.m.set_tempo(150)                             # live, while playing
        self.assertEqual(self.m.gen.bpm, 150)
        self.m.set_tempo(None)
        self.assertEqual(self.m.gen.bpm, FALLBACK_BPM)

    def test_beats_are_reported(self):
        beats = []
        self.m.connect("beat", lambda i, a, d: beats.append((i, a)))
        self.m.set_tempo(240)
        self.m.enable()
        self.watcher.on_sinks([sink()])
        self.assertTrue(wait(lambda: len(beats) >= 2))
        self.assertEqual(beats[:2], [(0, True), (1, False)])
        self.m.disable()
        n = len(beats)
        time.sleep(0.1)
        self.assertEqual(len(beats), n)                   # nothing after stop

    def test_pwcat_dying_turns_off_with_notice(self):
        m = Metronome(self.settings, command=lambda t, c: ["sh", "-c", "exit 3"],
                      watcher_factory=FakeWatcher)
        self.addCleanup(m.shutdown)
        kinds = []
        m.connect("notice", lambda k, t: kinds.append(k))
        m.enable()
        FakeWatcher.instances[-1].on_sinks([sink()])
        self.assertTrue(wait(lambda: kinds == ["error"]))
        self.assertFalse(m.enabled)

    def test_settings_persist(self):
        self.m.set_volume(35)
        self.m.set_accent(False)
        self.m.set_output("all")
        self.assertEqual(self.m.gen.route, (0, 1, 2, 3))
        self.assertFalse(self.m.gen.accent)
        self.assertAlmostEqual(self.m.gen.volume, 0.35)
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual((data["click_volume"], data["click_accent"], data["click_output"]),
                         (35, False, "all"))
        m2 = Metronome(Settings(self.path), command=self.cmd, watcher_factory=FakeWatcher)
        self.assertEqual((m2.volume, m2.accent, m2.output), (35, False, "all"))
        with self.assertRaises(ValueError):
            self.m.set_output("bogus")

    def test_volume_drag_saves_later(self):
        self.m.set_volume(50, save=False)
        self.assertFalse(os.path.exists(self.path))
        self.settings.save()
        self.assertEqual(Settings(self.path).get("click_volume"), 50)

    def test_bad_settings_fall_back(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"click_volume": 900, "click_output": "usb99", "click_accent": "yes"}, f)
        m = Metronome(Settings(self.path), command=self.cmd, watcher_factory=FakeWatcher)
        self.assertEqual((m.volume, m.output, m.accent), (100, "usb12", True))

    def test_opentone_pwcat_labels(self):
        from mg300edit.metronome import PWCAT_COMMAND
        cmd = PWCAT_COMMAND("sink.x", ("FL", "FR", "RL", "RR"))
        props = cmd[cmd.index("-P") + 1]
        self.assertIn("application.name = OpenTone", props)
        self.assertIn("node.dont-fallback = true", props)
        self.assertEqual(cmd[cmd.index("--target") + 1], "sink.x")
        # the engine's defaults are OpenTone's too (standalone Takk was removed in 1.0.1)
        self.assertIn("application.name = OpenTone", audio.pwcat_command("x")[audio.pwcat_command("x").index("-P") + 1])


if __name__ == "__main__":
    unittest.main()
