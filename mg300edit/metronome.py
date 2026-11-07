"""OpenTone's built-in click: the Takk metronome inside the editor (no GTK here).

The pedal's MIDI port is exclusive, so Takk and OpenTone cannot run together;
this puts the click engine (click.ClickGenerator + clickaudio.ClickPlayer /
SinkWatcher) behind a small state machine the window drives:

  enable()   user turned the click on: start watching PipeWire sinks; as soon
             as the pedal's sink is known, start pw-cat (never before)
  disable()  stop pw-cat and the sink watcher (also on window close / quit)
  set_tempo  follows Controller "tempo" events; applies from the next beat
             (None = not known yet -> click.FALLBACK_BPM)

If the sink disappears while playing, the click stops and a "sink-lost" notice
is emitted. A muted sink gives a "muted" notice; unmute() runs
`pactl set-sink-mute <sink> 0` and is only ever called from a user click.

Events (metronome.connect(name, fn), all on the main loop):
  state()                          enabled / playing / sink changed
  beat(index, accent, delay_s)     a click was queued; it sounds in ~delay_s
  notice(kind, text)               kind: "no-sink" | "sink-lost" | "muted" |
                                   "unmuted" | "error"
"""

from __future__ import annotations

import functools
import logging
import threading
from collections import defaultdict
from typing import Callable, Optional

from . import clickaudio as audio
from .click import (DEFAULT_OUTPUT, FALLBACK_BPM, OUTPUT_CHOICES, OUTPUTS,
                           ClickGenerator)

from . import APP_ID

log = logging.getLogger(__name__)

OUTPUT_KEYS = [k for k, _ in OUTPUT_CHOICES]

# label the stream as OpenTone's in the system mixer (default names otherwise)
PWCAT_COMMAND = functools.partial(audio.pwcat_command, node_name="opentone-click",
                                  app_name="OpenTone", app_id=APP_ID)


def _volume(v) -> int:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(min(100, max(0, round(v))))
    return 100


class Metronome:
    def __init__(self, settings, dispatch: Callable = lambda fn, *a: fn(*a), *,
                 command: Callable = PWCAT_COMMAND,
                 watcher_factory: Optional[Callable] = None,
                 pactl: Callable = audio.pactl,
                 sink_override: Optional[str] = None):
        self.settings = settings
        self.dispatch = dispatch
        self.pactl = pactl
        self.sink_override = sink_override
        self._watcher_factory = watcher_factory or (
            lambda on_sinks, dispatch: audio.SinkWatcher(on_sinks, dispatch=dispatch))
        self._listeners: dict[str, list[Callable]] = defaultdict(list)

        self.volume = _volume(settings.get("click_volume"))
        self.accent = bool(settings.get("click_accent"))
        out = settings.get("click_output")
        self.output = out if out in OUTPUTS else DEFAULT_OUTPUT
        self.tempo: Optional[int] = None
        self.enabled = False
        self.sink: Optional[audio.Sink] = None
        self._watcher = None
        self._gen_id = 0                # bumps on start/stop: stale beats are dropped

        self.gen = ClickGenerator(bpm=FALLBACK_BPM, volume=self.volume / 100,
                                  accent=self.accent, route=OUTPUTS[self.output])
        self.player = audio.ClickPlayer(self.gen, on_beat=self._beat_from_thread,
                                        on_stopped=lambda err: dispatch(self._on_stopped, err),
                                        command=command)

    # -- events --
    def connect(self, name: str, fn: Callable) -> None:
        self._listeners[name].append(fn)

    def emit(self, name: str, *args) -> None:
        for fn in list(self._listeners[name]):
            try:
                fn(*args)
            except Exception:  # noqa: BLE001 - a UI bug must not stop the click logic
                log.exception("metronome listener for %s failed", name)

    # -- state --
    @property
    def playing(self) -> bool:
        return self.player.playing

    @property
    def bpm(self) -> int:
        """What the click plays: the pedal's tempo, or the fallback while unknown."""
        return self.tempo if self.tempo is not None else FALLBACK_BPM

    @property
    def muted(self) -> bool:
        return bool(self.sink and self.sink.mute)

    # -- user actions --
    def enable(self) -> None:
        if self.enabled:
            return
        self.enabled = True
        self.sink = None
        self._watcher = self._watcher_factory(self._on_sinks, self.dispatch)
        self._watcher.start()            # first sink list arrives in a few ms -> _on_sinks
        self.emit("state")

    def disable(self) -> None:
        if not self.enabled and not self.player.playing and self._watcher is None:
            return
        self.enabled = False
        self._gen_id += 1
        self.player.stop()
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None
        self.emit("state")

    shutdown = disable

    def toggle(self) -> bool:
        (self.disable if self.enabled else self.enable)()
        return self.enabled

    def set_tempo(self, bpm: Optional[int]) -> None:
        self.tempo = int(bpm) if bpm else None
        self.player.set_bpm(self.bpm)        # takes effect from the next beat
        self.emit("state")

    def set_volume(self, percent, save: bool = True) -> None:
        self.volume = _volume(percent)
        self.player.set_volume(self.volume / 100)
        self.settings.set("click_volume", self.volume, save=save)

    def set_accent(self, on: bool) -> None:
        self.accent = bool(on)
        self.player.set_accent(self.accent)
        self.settings.set("click_accent", self.accent)

    def set_output(self, key: str) -> None:
        if key not in OUTPUTS:
            raise ValueError(f"unknown output {key!r}")
        self.output = key
        self.player.set_route(OUTPUTS[key])      # next click goes there, no restart
        self.settings.set("click_output", key)

    def unmute(self) -> None:
        """`pactl set-sink-mute <sink> 0` - only call this from a user's click."""
        sink, watcher = self.sink, self._watcher
        if not sink:
            return

        def work():
            self.pactl("set-sink-mute", sink.name, "0")
            if watcher is not None:
                watcher.kick()
        threading.Thread(target=work, name="opentone-unmute", daemon=True).start()

    # -- internals --
    def _start_player(self) -> None:
        self._gen_id += 1
        self.player.set_bpm(self.bpm)
        try:
            self.player.start(self.sink.name, audio.stream_channel_map(self.sink))
        except OSError as e:
            self.emit("notice", "error", f"A pw-cat nem indítható: {e.strerror or e}")
            self.disable()

    def _on_sinks(self, sinks) -> None:
        if not self.enabled:
            return
        old = self.sink
        sink = audio.find_pedal_sink(sinks, self.sink_override)
        self.sink = sink
        if sink is None:
            if self.player.playing:
                self.emit("notice", "sink-lost",
                          "A pedál hangkimenete eltűnt – a metronóm leállt")
            else:
                self.emit("notice", "no-sink",
                          "Nem található a pedál hangkimenete (PipeWire) – a metronóm nem indult el")
            self.disable()
            return
        was_muted = bool(old and old.mute and old.name == sink.name)
        if sink.mute and not was_muted:
            self.emit("notice", "muted", "A pedál hangkimenete némítva van a rendszerben")
        elif was_muted and not sink.mute:
            self.emit("notice", "unmuted", "")
        if not self.player.playing:
            self._start_player()
        self.emit("state")

    def _beat_from_thread(self, index: int, accent: bool, delay: float) -> None:
        self.dispatch(self._beat, self._gen_id, index, accent, delay)

    def _beat(self, gen_id: int, index: int, accent: bool, delay: float) -> None:
        if gen_id == self._gen_id and self.enabled and self.player.playing:
            self.emit("beat", index, accent, max(0.0, delay))

    def _on_stopped(self, error: Optional[str]) -> None:
        # our own stop() reports error=None; a restart in between means it's stale
        if error is None or not self.enabled or self.player.playing:
            return
        log.warning("click stopped: %s", error)
        self.emit("notice", "error", f"A metronóm leállt: {error}")
        self.disable()
