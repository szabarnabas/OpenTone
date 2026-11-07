"""Follow the pedal's tempo over MIDI (UI-agnostic, unit-testable).

Primary path: on every TAP the pedal sends `F0 43 58 70 7E 02 03 00.. F7`
("tempo changed"); we answer it at once with `F0 43 58 70 03 00 F7` (03 GET)
and use the reply's bpm as-is. A Program Change (C0 xx, preset change) or CC80
(B0 50 xx, scene change - tempo is stored per scene) re-queries too. A slow
fallback poll (poll(), every POLL_MS) catches anything else, e.g. tempo knob
edits that send no notification. All requests share one writer key, so while
one is still queued a newer one just replaces it: requests never pile up.
"""

from __future__ import annotations

import errno
from typing import Callable, Optional

from mg300edit import protocol as P

POLL_MS = 1000           # fallback poll; the 7E 02 03 notification is the fast path
TEMPO_KEY = "takk-tempo"

# pedal/port states shown in the UI
STATE_CONNECTED = "connected"
STATE_NO_PEDAL = "no-pedal"
STATE_BUSY = "busy"            # rawmidi is exclusive: another program (e.g. OpenTone) holds it
STATE_ERROR = "error"          # e.g. no permission


def is_tempo_trigger(msg: bytes) -> bool:
    """Tempo-changed notification (TAP), Program Change on any channel, or CC80
    (scene) on channel 1."""
    if P.is_tempo_notification(msg):
        return True
    if len(msg) == 2 and (msg[0] & 0xF0) == 0xC0:
        return True
    return len(msg) == 3 and msg[0] == 0xB0 and msg[1] == P.CC_SCENE


class TempoFollower:
    """Wires itself to a transport (mg300edit BaseTransport subclass).

    Callbacks (run in the transport's dispatch context, i.e. the GTK loop):
      on_tempo(bpm: int | None)        new pedal tempo (None = unknown / disconnected)
      on_state(state: str, detail: str)
    """

    def __init__(self, transport, on_tempo: Optional[Callable] = None,
                 on_state: Optional[Callable] = None):
        self.transport = transport
        self.on_tempo = on_tempo
        self.on_state = on_state
        self.bpm: Optional[int] = None
        self.state = STATE_NO_PEDAL
        self.detail = ""
        transport.on_messages = self._on_messages
        transport.on_status = self._on_status

    def start(self) -> None:
        self.transport.start()

    def stop(self) -> None:
        self.transport.stop()

    def poll(self) -> None:
        """Ask for the tempo (no-op while disconnected; coalesces while queued)."""
        if self.transport.connected:
            self.transport.send(P.get_tempo(), key=TEMPO_KEY)

    # -- transport callbacks --
    def _on_status(self, connected: bool, text: str) -> None:
        if connected:
            state = STATE_CONNECTED
        else:
            err = getattr(self.transport, "last_errno", 0)
            state = (STATE_BUSY if err == errno.EBUSY
                     else STATE_ERROR if err else STATE_NO_PEDAL)
        self.state, self.detail = state, text
        if connected:
            self.poll()
        else:
            self._set_bpm(None)
        if self.on_state:
            self.on_state(state, text)

    def _on_messages(self, msgs: list[bytes]) -> None:
        requery = False
        for m in msgs:
            bpm = P.parse_tempo(m)
            if bpm is not None:
                if P.TEMPO_MIN <= bpm <= P.TEMPO_MAX:
                    self._set_bpm(bpm)
            elif is_tempo_trigger(m):
                requery = True
        if requery:
            # move_to_end: a poll queued before the change must not answer for it
            self.transport.send(P.get_tempo(), key=TEMPO_KEY, move_to_end=True)

    def _set_bpm(self, bpm: Optional[int]) -> None:
        if bpm == self.bpm:
            return
        self.bpm = bpm
        if self.on_tempo:
            self.on_tempo(bpm)
