"""Paced, coalescing MIDI writer.

- Messages go out in FIFO order from a single thread.
- Minimum gap before a message: `sysex_gap` if it or the previous message is
  SysEx (the pedal drops SysEx sent < ~40 ms apart), else `channel_gap`.
- Messages sent with a `key` coalesce: while a message with that key is still
  queued, a newer one replaces it in place, so dragging a slider never builds
  a backlog - the pedal gets the latest value as soon as the gap allows.
- `move_to_end=True` re-queues a pending keyed message at the tail instead
  (used for GET requests that must follow e.g. a program change).
- `priority=PRIO_LOW` puts a message in a background queue that is only
  served while the normal queue is empty, so live CC edits overtake e.g. a
  batch of 72 preset-name GETs. A key lives in one queue at a time; re-sending
  it with another priority moves it.
"""

from __future__ import annotations

import itertools
import logging
import threading
import time
from collections import OrderedDict
from typing import Callable, Hashable, Optional

log = logging.getLogger(__name__)

PRIO_NORMAL = 0
PRIO_LOW = 1


class CoalescingWriter:
    def __init__(self, write_fn: Callable[[bytes], None], *,
                 sysex_gap: float = 0.040, channel_gap: float = 0.005,
                 clock: Callable[[], float] = time.monotonic,
                 on_sent: Optional[Callable[[bytes, float], None]] = None,
                 on_error: Optional[Callable[[BaseException], None]] = None):
        self.write_fn = write_fn
        self.sysex_gap = sysex_gap
        self.channel_gap = channel_gap
        self.clock = clock
        self.on_sent = on_sent
        self.on_error = on_error
        # one FIFO per priority level; index = priority
        self._queues: "list[OrderedDict[Hashable, bytes]]" = [OrderedDict(), OrderedDict()]
        self._seq = itertools.count()
        self._cond = threading.Condition()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_time = float("-inf")
        self._last_sysex = False
        self.sent_count = 0
        self.coalesced_count = 0

    # -- lifecycle --
    def start(self) -> None:
        with self._cond:
            if self._running:
                return
            self._running = True
        self._thread = threading.Thread(target=self._run, name="midi-writer", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 1.0) -> None:
        with self._cond:
            self._running = False
            self._cond.notify_all()
        if self._thread:
            self._thread.join(timeout)
            self._thread = None

    # -- API --
    def send(self, msg: bytes, key: Optional[Hashable] = None, move_to_end: bool = False,
             priority: int = PRIO_NORMAL) -> None:
        msg = bytes(msg)
        prio = PRIO_LOW if priority >= PRIO_LOW else PRIO_NORMAL
        q = self._queues[prio]
        with self._cond:
            if key is None:
                q[("_seq", next(self._seq))] = msg
            else:
                other = self._queues[1 - prio]
                if key in other:                # priority changed: move it
                    del other[key]
                    self.coalesced_count += 1
                elif key in q:
                    self.coalesced_count += 1
                    if move_to_end:
                        q.move_to_end(key)
                q[key] = msg                    # existing key keeps its position
            self._cond.notify_all()

    def clear(self) -> None:
        with self._cond:
            for q in self._queues:
                q.clear()

    def pending(self, priority: Optional[int] = None) -> int:
        with self._cond:
            if priority is not None:
                return len(self._queues[priority])
            return sum(len(q) for q in self._queues)

    def _empty(self) -> bool:
        return not any(self._queues)

    def flush(self, timeout: float = 2.0) -> bool:
        """Block until the queue is empty (tests / shutdown)."""
        end = time.monotonic() + timeout
        with self._cond:
            while not self._empty() and time.monotonic() < end:
                self._cond.wait(0.01)
            return self._empty()

    # -- internals --
    def _gap_for(self, msg: bytes) -> float:
        is_sx = msg[:1] == b"\xF0"
        return self.sysex_gap if (is_sx or self._last_sysex) else self.channel_gap

    def _run(self) -> None:
        while True:
            with self._cond:
                while self._running and self._empty():
                    self._cond.wait()
                if not self._running:
                    return
                q = next(q for q in self._queues if q)     # highest priority first
                key, msg = next(iter(q.items()))
                due = self._last_time + self._gap_for(msg)
                now = self.clock()
                if now < due:
                    # wait; a newer value for this key (or a higher-priority
                    # message) may arrive meanwhile and is picked on re-loop
                    self._cond.wait(due - now)
                    continue
                q.popitem(last=False)
                self._cond.notify_all()
            try:
                self.write_fn(msg)
            except BaseException as e:  # noqa: BLE001 - keep the thread alive
                log.warning("MIDI write failed: %s", e)
                if self.on_error:
                    self.on_error(e)
                continue
            t = self.clock()
            self._last_time = t
            self._last_sysex = msg[:1] == b"\xF0"
            self.sent_count += 1
            if self.on_sent:
                self.on_sent(msg, t)
