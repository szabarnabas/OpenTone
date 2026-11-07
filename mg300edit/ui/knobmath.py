"""Pure value math for the knob / fader widgets (no GTK, unit-tested)."""

from __future__ import annotations

import math

# geometry: 270 degree sweep, 0 at 7:30 o'clock, max at 4:30 o'clock
START_ANGLE = math.radians(135.0)      # cairo angle (0 = +x, clockwise)
SWEEP = math.radians(270.0)

DRAG_PIXELS = 200.0     # vertical pixels for the full range
FINE_FACTOR = 0.2       # Shift: 5x finer


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def fraction(v: float, lo: float, hi: float) -> float:
    """0..1 position of v in [lo, hi]."""
    if hi <= lo:
        return 0.0
    return clamp((v - lo) / (hi - lo), 0.0, 1.0)


def value_to_angle(v: float, lo: float, hi: float) -> float:
    return START_ANGLE + SWEEP * fraction(v, lo, hi)


def drag_value(start: float, dy: float, lo: int, hi: int, fine: bool = False) -> float:
    """Float value after dragging dy pixels (screen coords: negative = up = more)
    from `start`. Callers round and clamp; the float lets the fine mode accumulate."""
    span = max(1, hi - lo)
    scale = span / DRAG_PIXELS * (FINE_FACTOR if fine else 1.0)
    return clamp(start - dy * scale, lo, hi)


def to_int(v: float, lo: int, hi: int) -> int:
    return int(clamp(int(math.floor(v + 0.5)), lo, hi))


def page_step(lo: int, hi: int) -> int:
    return max(1, (hi - lo) // 10)


def scroll_step(lo: int, hi: int, fine: bool = False) -> int:
    """Value change per wheel notch: ~50 notches for the full range, 1 when fine."""
    if fine:
        return 1
    return max(1, (hi - lo) // 50)


def key_value(v: int, lo: int, hi: int, key: str) -> int:
    """New value for a navigation key: up/down/pgup/pgdn/home/end (others: v)."""
    step = {"up": 1, "down": -1, "pgup": page_step(lo, hi), "pgdn": -page_step(lo, hi)}
    if key == "home":
        return lo
    if key == "end":
        return hi
    return int(clamp(v + step.get(key, 0), lo, hi))
