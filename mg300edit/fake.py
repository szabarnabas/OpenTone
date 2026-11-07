"""FakeTransport: an in-process MG-300 MKII emulation for running without hardware.

Answers GETs (identity, 03 tempo, 15 current, 0D block order, 0C edit buffer,
0B stored preset scene) from a decoded scene (research/scene_a.bin by default),
re-encoded into proper device responses. Replies arrive asynchronously and split
into chunks so the same stream-splitting path as the real transport is exercised.

Model: 72 stored presets x 3 scenes (PC 0-35 user "User 1A".., 36-71 factory
"Factory 1A"..; preset 8 = user 3A keeps scene_a.bin's real name), and an edit
buffer of 3 scenes that a PC reloads from the stored preset and incoming CCs
modify (CC -> offset map verified on hardware, research/FACTS.md).

Write commands (as verified on hardware, FACTS.md incl. CORRECTION 2026-11-07):
  CC80     B0 50 <s> switches the ACTIVE scene (no echo); CC param edits land in
           the active scene's part
  0B set   store one part of a preset (in RAM of this fake) -> ack 0B 03 p s 01
  07 set   commit -> ack 07 03 p p; committing the current preset reloads the
           edit buffer and resets the active scene to 0
  15 set   only changes the "view" part that a plain `0C 00` returns (and loads
           another preset if p differs) -> 15 02 p s; NOT a scene switch
  03 set   live tempo + tempo bytes 0x5B/0x5C of the active part, echo 03 02
  0D set   block order of the edit buffer -> 0D 02 <order>
  PC       reloads the preset, active scene and view -> 0
  CC edits -> 7E 02 14 "modified" notification (once until reload/save)
Test hooks: drop_acks / nak_writes / corrupt_store / drop_commit_ack.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

from . import presets as PR
from . import protocol as P
from .midistream import StreamSplitter
from .transport import BaseTransport

log = logging.getLogger(__name__)

DEFAULT_SCENE = Path(__file__).resolve().parents[1] / "research" / "scene_a.bin"
NAME_OFFSET, NAME_LEN, TEMPO_OFFSET, ORDER_OFFSET = 0x69, 16, 0x5C, 0x5D
TEMPO_HI_OFFSET = 0x5B
AMP_GAIN_OFFSET, RVB_LEVEL_OFFSET, DLY_SWITCH_OFFSET = 0x1C, 0x49, 0x07
REAL_PRESET = 8          # scene_a.bin was read from preset 8, scene 0

# CC number ranges -> decoded scene offset (FACTS.md "Live control", verified)
CC_RANGES = [(1, 11, 0x01), (12, 13, 0x0D), (14, 17, 0x10), (18, 23, 0x15), (24, 31, 0x1C),
             (32, 43, 0x25), (44, 47, 0x32), (48, 53, 0x37), (54, 61, 0x3E), (62, 65, 0x47),
             (66, 71, 0x4C), (72, 73, 0x53), (74, 76, 0x56)]
CC_PL_POSITION, PL_POSITION_OFFSET = 77, 0x59


def cc_offset(cc: int) -> Optional[int]:
    for lo, hi, off in CC_RANGES:
        if lo <= cc <= hi:
            return off + cc - lo
    return PL_POSITION_OFFSET if cc == CC_PL_POSITION else None


def _blank_scene() -> bytes:
    s = bytearray(P.SCENE_SIZE)
    s[TEMPO_OFFSET] = 120
    s[ORDER_OFFSET:ORDER_OFFSET + 10] = bytes([5, 1, 2, 3, 9, 4, 6, 7, 8, 10])
    s[NAME_OFFSET:NAME_OFFSET + 4] = b"Fake"
    return bytes(s)


def _set_name(s: bytearray, name: str) -> None:
    s[NAME_OFFSET:NAME_OFFSET + NAME_LEN] = name.encode("ascii")[:NAME_LEN].ljust(NAME_LEN, b"\x00")


class FakeDevice:
    """Pure request -> reply logic (no threads), easy to unit test."""

    FIRMWARE = "20260708110000"
    MODEL = "MG300V2"

    def __init__(self, scene: Optional[bytes] = None, preset: int = REAL_PRESET, scene_index: int = 0):
        self.base_scene = bytes(scene) if scene else _blank_scene()
        self.preset = preset
        self.stored: dict[tuple[int, int], bytes] = {}
        self.pending: dict[tuple[int, int], bytes] = {}   # written, not yet committed
        self.edits: list[bytearray] = []
        self._load(preset)
        self.active = scene_index        # active scene (CC80 / footswitch)
        self.view = scene_index          # part returned by a plain 0C (15 set)
        self.tempo = self.base_scene[TEMPO_OFFSET] or 120
        self.tempo_extra = 0
        self.modified = False
        self.received: list[bytes] = []
        self.commits: list[int] = []
        # test hooks
        self.drop_acks = False           # never ack 0B set
        self.nak_writes = False          # ack 0B set with 00 (failure)
        self.corrupt_store = False       # store a modified copy (readback mismatch)
        self.drop_commit_ack = False
        self.drop_writes = 0             # silently ignore the next N 0B set writes (real pedal does this)

    def _load(self, preset: int) -> None:
        self.preset = preset
        self.active = self.view = 0
        self.edits = [bytearray(self.stored_scene(preset, s)) for s in range(PR.SCENES_PER_PRESET)]
        self.modified = False

    @property
    def scene_index(self) -> int:
        """The active scene (alias kept for tests)."""
        return self.active

    @property
    def edit(self) -> bytearray:
        """The active scene's part (where CC edits land)."""
        return self.edits[self.active]

    @edit.setter
    def edit(self, value) -> None:
        self.edits[self.active] = bytearray(value)

    def preset_name(self, preset: int) -> str:
        if preset == REAL_PRESET and self.base_scene is not None:
            raw = self.base_scene[NAME_OFFSET:NAME_OFFSET + NAME_LEN].split(b"\x00", 1)[0]
            if raw:
                return raw.decode("ascii", "replace")
        return PR.full_label(preset)

    def stored_scene(self, preset: int, scene: int) -> bytes:
        """Stored preset data (deterministic, distinct per preset and scene)."""
        key = (preset, scene)
        if key not in self.stored:
            s = bytearray(self.base_scene)
            if PR.is_valid(preset):
                if preset != REAL_PRESET:
                    _set_name(s, self.preset_name(preset))
                    s[AMP_GAIN_OFFSET] = (preset * 7) % 101
                if scene:
                    s[AMP_GAIN_OFFSET] = (s[AMP_GAIN_OFFSET] + 20 * scene) % 101
                    s[RVB_LEVEL_OFFSET] = (s[RVB_LEVEL_OFFSET] + 15 * scene) % 101
                    s[DLY_SWITCH_OFFSET] ^= 0x40 if scene == 1 else 0
            else:
                s[NAME_OFFSET:NAME_OFFSET + NAME_LEN] = bytes([0xFF] * 3) + bytes(NAME_LEN - 3)
            self.stored[key] = bytes(s)
        return self.stored[key]

    def identity_reply(self) -> bytes:
        body = self.FIRMWARE.encode() + b"\x00" + self.MODEL.encode() + b"\x00"
        return bytes([0xF0, 0x43, 0x58, 0x10]) + body + b"\xF7"

    def _apply_cc(self, cc: int, value: int) -> list[bytes]:
        off = cc_offset(cc)
        if off is None or off >= len(self.edit):
            return []
        self.edit[off] = (0x80 | value) if cc == CC_PL_POSITION else value
        if not self.modified:
            self.modified = True
            return [P.modified_notification()]
        return []

    def _switch_scene(self, scene: int) -> list[bytes]:
        """Footswitch scene change: the pedal reports B0 4E <blk>, B0 50 <s>."""
        self.active = scene
        return [P.control_change(P.CC_CURRENT_BLOCK, 3), P.control_change(P.CC_SCENE, scene)]

    def _handle_set(self, m: P.NuxMessage) -> list[bytes]:
        pl = m.payload
        if m.cmd == P.CMD_STORED and len(pl) >= 2:
            preset, sc = pl[0], pl[1]
            data = P.decode7(pl[2:])
            if len(data) != P.SCENE_SIZE or sc >= PR.SCENES_PER_PRESET or not PR.is_valid(preset):
                return [P.stored_ack_response(preset, sc, ok=False)]
            if self.drop_acks:
                return []
            if self.drop_writes > 0:
                self.drop_writes -= 1
                return []
            if self.nak_writes:
                return [P.stored_ack_response(preset, sc, ok=False)]
            if self.corrupt_store:
                data = bytes([data[0] ^ 1]) + data[1:]
            self.pending[(preset, sc)] = data
            return [P.stored_ack_response(preset, sc)]
        if m.cmd == P.CMD_COMMIT and len(pl) >= 2 and pl[0] == pl[1]:
            preset = pl[0]
            for (p, sc), data in list(self.pending.items()):
                if p == preset:
                    self.stored[(p, sc)] = data
                    del self.pending[(p, sc)]
            self.commits.append(preset)
            if preset == self.preset:
                self._load(preset)               # reloads the edit buffer, scene -> 0
            return [] if self.drop_commit_ack else [P.commit_ack_response(preset)]
        if m.cmd == P.CMD_CURRENT and len(pl) >= 2:
            preset, sc = pl[0], pl[1]
            if sc >= PR.SCENES_PER_PRESET:
                return [P.unsupported_response()]
            if preset != self.preset:
                self._load(preset)
            self.view = sc                       # NOT the active scene (verified)
            return [P.set_current_response(preset, sc)]
        if m.cmd == P.CMD_TEMPO and len(pl) >= 3:
            self.tempo = (pl[0] << 7) | pl[1]
            self.tempo_extra = pl[2]
            self.edit[TEMPO_HI_OFFSET] = (self.tempo >> 7) & 0x7F
            self.edit[TEMPO_OFFSET] = self.tempo & 0x7F
            return [P.tempo_response(self.tempo, self.tempo_extra)]
        if m.cmd == P.CMD_BLOCK_ORDER and len(pl) >= 10:
            order = bytes(pl[:10])
            for e in self.edits:
                e[ORDER_OFFSET:ORDER_OFFSET + 10] = order
            return [P.block_order_response(list(order))]
        return [P.unsupported_response()]

    def handle(self, msg: bytes) -> list[bytes]:
        self.received.append(bytes(msg))
        if msg == P.IDENTITY_REQUEST:
            return [self.identity_reply(), P.program_change(self.preset)]
        if msg and (msg[0] & 0xF0) == 0xC0 and len(msg) == 2:
            self._load(msg[1])                   # PC discards edits, scene -> 0
            return []
        if msg and (msg[0] & 0xF0) == 0xB0:
            if len(msg) == 3 and (msg[0] & 0x0F) == 0:
                if msg[1] == P.CC_SCENE:
                    if msg[2] < PR.SCENES_PER_PRESET:
                        self.active = msg[2]         # scene switch, no echo
                    return []
                return self._apply_cc(msg[1], msg[2])   # no echo, only 7E "modified"
            return []
        m = P.parse_nux(msg)
        if not m:
            return []
        scene = self.edit
        if m.op == P.OP_GET:
            if m.cmd == P.CMD_TEMPO:
                return [P.tempo_response(self.tempo, self.tempo_extra)]
            if m.cmd == P.CMD_CURRENT:
                return [P.current_response(self.preset)]
            if m.cmd == P.CMD_BLOCK_ORDER:
                return [P.block_order_response(list(scene[ORDER_OFFSET:ORDER_OFFSET + 10]))]
            if m.cmd == P.CMD_EDIT_BUFFER:
                if len(m.payload) >= 2:
                    preset, sc = m.payload[0], m.payload[1]
                    if sc >= PR.SCENES_PER_PRESET:
                        return [P.unsupported_response()]
                    data = self.edits[sc] if preset == self.preset else self.stored_scene(preset, sc)
                    return [P.edit_buffer_response(preset, sc, bytes(data))]
                return [P.edit_buffer_response(self.preset, self.view, bytes(self.edits[self.view]))]
            if m.cmd == P.CMD_STORED and len(m.payload) >= 2:
                preset, sc = m.payload[0], m.payload[1]
                if sc >= PR.SCENES_PER_PRESET:
                    return [P.unsupported_response()]
                return [P.stored_scene_response(preset, sc, self.stored_scene(preset, sc))]
            return [P.unsupported_response()]
        if m.op == P.OP_SET:
            return self._handle_set(m)
        return []


class FakeTransport(BaseTransport):
    def __init__(self, scene_path: Optional[str] = None, latency: float = 0.010, **kw):
        super().__init__(**kw)
        path = Path(scene_path) if scene_path else DEFAULT_SCENE
        try:
            scene = path.read_bytes()
        except OSError:
            log.warning("fake scene %s not found, using a blank scene", path)
            scene = None
        self.device = FakeDevice(scene)
        self.latency = latency
        self._splitter = StreamSplitter()
        self._lock = threading.Lock()

    def start(self) -> None:
        super().start()
        self._set_status(True, "Szimulált MG-300V2 (hardver nélkül)")

    def stop(self) -> None:
        self._set_status(False, "Kapcsolat bontva (leállítva)")
        super().stop()

    def _write(self, msg: bytes) -> None:
        with self._lock:
            replies = self.device.handle(msg)
        if replies:
            self._emit_later(b"".join(replies))

    def _emit_later(self, raw: bytes) -> None:
        # deliver in two chunks to exercise SysEx reassembly
        mid = len(raw) // 2
        chunks = [raw[:mid], raw[mid:]] if len(raw) > 8 else [raw]

        def fire():
            with self._lock:
                msgs = []
                for c in chunks:
                    msgs += self._splitter.feed(c)
            self._deliver(msgs)

        t = threading.Timer(self.latency, fire)
        t.daemon = True
        t.start()

    # test / debug helper: pretend the user stomped a footswitch
    def simulate_preset_change(self, preset: int) -> None:
        with self._lock:
            self.device.handle(P.program_change(preset))
        self._emit_later(P.program_change(preset))

    def simulate_scene_change(self, scene: int) -> None:
        """Footswitch scene change: the pedal emits B0 4E <blk>, B0 50 <scene>."""
        with self._lock:
            out = self.device._switch_scene(scene)
        self._emit_later(b"".join(out))

    def simulate_knob(self, cc: int, value: int) -> None:
        """Knob turned on the pedal: B0 <cc> <v> (+ 7E 02 14 the first time)."""
        msg = P.control_change(cc, value)
        with self._lock:
            out = self.device._apply_cc(cc, value)
        self._emit_later(msg + b"".join(out))
