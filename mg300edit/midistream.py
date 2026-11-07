"""Split a raw MIDI byte stream into complete messages.

Handles SysEx split across reads, running status for channel messages and
realtime bytes (F8..FF) interleaved anywhere, including inside SysEx.
"""

from __future__ import annotations

MAX_SYSEX = 64 * 1024


def _data_len(status: int) -> int:
    """Number of data bytes following a (non-SysEx, non-realtime) status byte."""
    if status < 0xF0:
        return 1 if (status & 0xF0) in (0xC0, 0xD0) else 2
    return {0xF1: 1, 0xF2: 2, 0xF3: 1}.get(status, 0)


class StreamSplitter:
    def __init__(self, keep_realtime: bool = True):
        self.keep_realtime = keep_realtime
        self._sysex: bytearray | None = None
        self._running = 0          # running status (channel messages only)
        self._data = bytearray()
        self.dropped = 0           # stray / truncated bytes (diagnostics)

    def reset(self) -> None:
        self._sysex = None
        self._running = 0
        self._data.clear()

    def feed(self, chunk: bytes) -> list[bytes]:
        out: list[bytes] = []
        for b in chunk:
            if b >= 0xF8:                          # realtime: never disturbs state
                if self.keep_realtime:
                    out.append(bytes([b]))
                continue
            if b == 0xF0:
                if self._sysex is not None:
                    self.dropped += len(self._sysex)
                self._sysex = bytearray([b])
                self._running = 0
                self._data.clear()
                continue
            if b == 0xF7:
                if self._sysex is not None:
                    self._sysex.append(b)
                    out.append(bytes(self._sysex))
                    self._sysex = None
                else:
                    self.dropped += 1
                continue
            if b & 0x80:                           # any other status byte
                if self._sysex is not None:        # terminates an unfinished SysEx
                    self.dropped += len(self._sysex)
                    self._sysex = None
                self._data.clear()
                n = _data_len(b)
                if b < 0xF0:
                    self._running = b
                else:                              # system common clears running status
                    self._running = 0
                    if n == 0:
                        out.append(bytes([b]))
                    else:
                        self._running = b          # collect its data bytes once
                continue
            # data byte
            if self._sysex is not None:
                self._sysex.append(b)
                if len(self._sysex) > MAX_SYSEX:
                    self.dropped += len(self._sysex)
                    self._sysex = None
                continue
            if not self._running:
                self.dropped += 1
                continue
            self._data.append(b)
            if len(self._data) == _data_len(self._running):
                out.append(bytes([self._running]) + bytes(self._data))
                self._data.clear()
                if self._running >= 0xF0:          # system common has no running status
                    self._running = 0
        return out
