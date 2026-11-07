"""Tap tempo: BPM from the average of the last 4 tap intervals (pure, testable)."""

from __future__ import annotations

import time
from typing import Callable, Optional

from .protocol import TEMPO_MAX, TEMPO_MIN


class TapTempo:
    RESET_AFTER = 2.0      # s without a tap starts a new measurement
    INTERVALS = 4

    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 lo: int = TEMPO_MIN, hi: int = TEMPO_MAX):
        self.clock = clock
        self.lo, self.hi = lo, hi
        self.taps: list[float] = []

    def tap(self, now: Optional[float] = None) -> Optional[int]:
        """Register a tap; returns the BPM once at least two taps are known."""
        t = self.clock() if now is None else now
        if self.taps and t - self.taps[-1] > self.RESET_AFTER:
            self.taps.clear()
        self.taps.append(t)
        self.taps = self.taps[-(self.INTERVALS + 1):]
        if len(self.taps) < 2:
            return None
        avg = (self.taps[-1] - self.taps[0]) / (len(self.taps) - 1)
        if avg <= 0:
            return None
        return max(self.lo, min(self.hi, round(60.0 / avg)))
