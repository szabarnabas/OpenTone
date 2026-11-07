"""Metronome click generator (pure, no I/O).

Produces a continuous interleaved multi-channel s16 stream (4 channels = the
pedal's USB playback 1-4; the click is routed to the selected pair(s)). Beat positions are
computed from a running sample counter, so they are sample-accurate and never
drift; the audio clock (PipeWire pulling from pw-cat) drives everything.

Tempo changes take effect from the next beat: the beat after the last one that
already sounded is re-placed one *new* beat period after it (or right away if
that moment has already passed). Clicks therefore never double up and the gap
between two clicks is never shorter than the new period.
"""

from __future__ import annotations

import math
from array import array
from typing import NamedTuple

RATE = 48000
CHANNELS = 4                 # NUX sink: USB playback channels 1-4
CLICK_MS = 25
BEATS_PER_BAR = 4
FREQ_NORMAL = 1000.0         # Hz
FREQ_ACCENT = 1600.0         # beat 1 of the bar: higher pitch
PEAK_NORMAL = 0.80           # of full scale at 100 % volume
PEAK_ACCENT = 0.95
DEFAULT_VOLUME = 1.00          # user test 2026-11-07: 100 % needed through the pedal
BPM_MIN, BPM_MAX = 40, 250
FULL_SCALE = 32767

# output choice -> 0-based channels of the 4-channel stream
OUTPUTS = {
    "usb12": (0, 1),
    "usb34": (2, 3),
    "all": (0, 1, 2, 3),
}
DEFAULT_OUTPUT = "usb12"


class Beat(NamedTuple):
    offset: int              # frame offset inside the rendered chunk
    position: int            # absolute frame position since start()
    index: int               # beat in bar, 0..BEATS_PER_BAR-1 (0 = beat 1)
    accent: bool


def clamp_bpm(bpm: float) -> float:
    return float(min(BPM_MAX, max(BPM_MIN, bpm)))


def click_shape(freq: float, rate: int = RATE, ms: float = CLICK_MS) -> list[float]:
    """One click, peak 1.0: 1 ms attack, exponential decay, short fade to exact 0."""
    n = int(rate * ms / 1000)
    attack = max(1, rate // 1000)
    fade = max(1, rate // 500)
    tau = n / 4.5
    out = []
    for i in range(n):
        env = math.exp(-i / tau)
        if i < attack:
            env *= i / attack
        if i >= n - fade:
            env *= (n - 1 - i) / fade
        out.append(env * math.sin(2 * math.pi * freq * i / rate))
    peak = max(abs(x) for x in out) or 1.0
    return [x / peak for x in out]


class ClickGenerator:
    """`channels`-channel interleaved s16 stream; the click goes only into the
    channels of the current `route` (others stay silent). A route change takes
    effect with the next click, so a click that is sounding is never cut."""

    def __init__(self, bpm: float = 120, volume: float = DEFAULT_VOLUME, accent: bool = True,
                 rate: int = RATE, beats_per_bar: int = BEATS_PER_BAR,
                 channels: int = CHANNELS, route: tuple[int, ...] = (0, 1)):
        self.rate = rate
        self.channels = channels
        self.beats_per_bar = beats_per_bar
        self._shapes = {False: click_shape(FREQ_NORMAL, rate), True: click_shape(FREQ_ACCENT, rate)}
        self._tables: dict[bool, array] = {}
        self._bpm = clamp_bpm(bpm)
        self._volume = 0.0
        self.accent = accent
        self.route: tuple[int, ...] = ()
        self.set_route(route)
        self.set_volume(volume)
        self.reset()

    # -- parameters (not thread-safe against render(); the player serialises them) --
    @property
    def bpm(self) -> float:
        return self._bpm

    def set_bpm(self, bpm: float) -> None:
        bpm = clamp_bpm(bpm)
        if bpm == self._bpm:
            return
        self._bpm = bpm
        if self._last_beat is not None:
            # re-place the upcoming beat from the last one that sounded
            self._next_beat = max(self._last_beat + self.period, float(self.position))

    @property
    def period(self) -> float:
        """Frames per beat (fractional; positions are rounded per beat, no drift)."""
        return self.rate * 60.0 / self._bpm

    @property
    def volume(self) -> float:
        return self._volume

    def set_volume(self, volume: float) -> None:
        """0.0-1.0, linear amplitude."""
        volume = min(1.0, max(0.0, float(volume)))
        self._volume = volume
        self._tables = {     # swapped atomically
            acc: array("h", (max(-FULL_SCALE, min(FULL_SCALE, round(x * peak * volume * FULL_SCALE)))
                             for x in self._shapes[acc]))
            for acc, peak in ((False, PEAK_NORMAL), (True, PEAK_ACCENT))}

    def set_route(self, route) -> None:
        """Output channels (0-based) that carry the click."""
        route = tuple(sorted({int(c) for c in route}))
        if not route or route[0] < 0 or route[-1] >= self.channels:
            raise ValueError(f"route {route} outside 0..{self.channels - 1}")
        self.route = route

    # -- stream --
    def reset(self) -> None:
        """Start over: the next rendered frame is beat 1."""
        self.position = 0
        self._next_beat = 0.0
        self._last_beat: float | None = None
        self._beat_count = 0
        # rest of a click that crossed a chunk border: (mono samples, its route)
        self._tail: tuple[array, tuple[int, ...]] | None = None

    def _put(self, buf: array, off: int, mono: array, route: tuple[int, ...], frames: int):
        """Write mono samples from frame `off` into the routed channels; returns the
        part that did not fit into this chunk (or None)."""
        n = min(len(mono), frames - off)
        c = self.channels
        seg = mono[0:n]
        for ch in route:
            buf[off * c + ch:(off + n) * c + ch - c + 1:c] = seg
        return (mono[n:], route) if n < len(mono) else None

    def render(self, frames: int) -> tuple[bytes, list[Beat]]:
        """Next `frames` frames of interleaved s16 (native endian, = what pw-cat's
        s16 expects) and the beats that start inside them."""
        buf = array("h", bytes(2 * self.channels * frames))
        beats: list[Beat] = []
        start, end = self.position, self.position + frames
        if self._tail is not None:
            mono, route = self._tail
            self._tail = self._put(buf, 0, mono, route, frames)
        while True:
            beat_at = round(self._next_beat)
            if beat_at >= end:
                break
            beat_at = max(beat_at, start)
            index = self._beat_count % self.beats_per_bar
            accent = self.accent and index == 0
            off = beat_at - start
            self._tail = self._put(buf, off, self._tables[accent], self.route, frames)
            beats.append(Beat(off, beat_at, index, accent))
            self._beat_count += 1
            self._last_beat = max(self._next_beat, float(start))   # exact (fractional) beat time
            self._next_beat = self._last_beat + self.period
        self.position = end
        return buf.tobytes(), beats
