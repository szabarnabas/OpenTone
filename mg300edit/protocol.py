"""MG-300 MKII wire protocol: 7-bit codec, message builders and parsers.

Everything here is pure (bytes in, bytes/values out) and has no I/O.
See research/FACTS.md for the verified facts this is based on.

SysEx frame:  F0 43 58 70 <cmd> <op> <payload...> F7
  op 00 = get, 01 = set, 02 = get-response, 03 = set-ack
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

# --- constants --------------------------------------------------------------

SYSEX_START = 0xF0
SYSEX_END = 0xF7
MANUFACTURER = bytes([0x43, 0x58])          # NUX / Cherub
HEADER = bytes([SYSEX_START, 0x43, 0x58, 0x70])

OP_GET = 0x00
OP_SET = 0x01
OP_GET_RESP = 0x02
OP_SET_ACK = 0x03

CMD_TEMPO = 0x03
CMD_COMMIT = 0x07          # commit a written preset (after 3x 0B set)
CMD_STORED = 0x0B          # stored preset scene: get = read, set = write one part (save)
CMD_EDIT_BUFFER = 0x0C
CMD_BLOCK_ORDER = 0x0D
CMD_MIDI_TABLE = 0x0F
CMD_USB = 0x14
CMD_CURRENT = 0x15
CMD_NOTIFY = 0x7E          # pedal -> host notifications (7E 02 14 = edit buffer modified)
CMD_UNSUPPORTED = 0x7F

NOTIFY_MODIFIED = 0x14

# incoming-only CCs (the pedal sends them; sending them does nothing)
CC_CURRENT_BLOCK = 78      # B0 4E <block> - block selected on the pedal UI
CC_SCENE = 80              # B0 50 <scene> - active scene 0..2: switches it (to the pedal)
                           # and reports a change (from the pedal), verified 2026-11-07

TEMPO_MIN = 40
TEMPO_MAX = 250

SCENE_SIZE = 126
ENCODED_SCENE_SIZE = 189

CMD_NAMES = {
    CMD_TEMPO: "tempo",
    CMD_COMMIT: "commit",
    0x09: "?09",
    CMD_STORED: "stored-scene",
    CMD_EDIT_BUFFER: "edit-buffer",
    CMD_BLOCK_ORDER: "block-order",
    CMD_MIDI_TABLE: "midi-table",
    CMD_USB: "usb",
    CMD_CURRENT: "current",
    0x18: "looper",
    0x19: "drum",
    CMD_NOTIFY: "notify",
    CMD_UNSUPPORTED: "unsupported",
}
OP_NAMES = {OP_GET: "get", OP_SET: "set", OP_GET_RESP: "resp", OP_SET_ACK: "ack"}


# --- 2 -> 3 seven-bit codec -------------------------------------------------
#
# Two 8-bit bytes d0 d1 are carried as three 7-bit bytes c0 c1 c2:
#   c0 = d0 >> 6                      (top 2 bits of d0)
#   c1 = ((d0 & 0x3F) << 1) | (d1 >> 7)
#   c2 = d1 & 0x7F
# Decode (verified on hardware, research/decode0c.py):
#   d0 = ((c0 << 6) | ((c1 & 0x7E) >> 1)) & 0xFF
#   d1 = (c2 & 0x7F) | ((c1 & 1) << 7)

def encode7(data: bytes) -> bytes:
    """Pack 8-bit data into 7-bit-safe bytes (2 -> 3). Odd length is zero padded."""
    data = bytes(data)
    if len(data) % 2:
        data += b"\x00"
    out = bytearray()
    for i in range(0, len(data), 2):
        d0, d1 = data[i], data[i + 1]
        out.append(d0 >> 6)
        out.append(((d0 & 0x3F) << 1) | (d1 >> 7))
        out.append(d1 & 0x7F)
    return bytes(out)


def decode7(enc: bytes) -> bytes:
    """Unpack 7-bit bytes into 8-bit data (3 -> 2). A trailing partial group is ignored."""
    out = bytearray()
    for i in range(0, len(enc) - 2, 3):
        c0, c1, c2 = enc[i], enc[i + 1], enc[i + 2]
        out.append(((c0 << 6) | ((c1 & 0x7E) >> 1)) & 0xFF)
        out.append((c2 & 0x7F) | ((c1 & 1) << 7))
    return bytes(out)


# --- generic message helpers -------------------------------------------------

def is_sysex(msg: bytes) -> bool:
    return len(msg) >= 2 and msg[0] == SYSEX_START and msg[-1] == SYSEX_END


def is_nux(msg: bytes) -> bool:
    return is_sysex(msg) and msg[1:3] == MANUFACTURER


def is_realtime(msg: bytes) -> bool:
    return len(msg) == 1 and msg[0] >= 0xF8


def sysex(cmd: int, op: int, payload: Iterable[int] = b"") -> bytes:
    payload = bytes(payload)
    if any(b > 0x7F for b in payload):
        raise ValueError("SysEx payload must be 7-bit")
    return HEADER + bytes([cmd & 0x7F, op & 0x7F]) + payload + bytes([SYSEX_END])


def get_request(cmd: int) -> bytes:
    return sysex(cmd, OP_GET)


@dataclass
class NuxMessage:
    cmd: int
    op: int
    payload: bytes          # bytes between <op> and F7


def parse_nux(msg: bytes) -> Optional[NuxMessage]:
    """Parse an F0 43 58 70 ... F7 frame; None for anything else."""
    if not is_sysex(msg) or len(msg) < 7 or msg[:4] != HEADER:
        return None
    return NuxMessage(cmd=msg[4], op=msg[5], payload=bytes(msg[6:-1]))


def hexs(msg: bytes) -> str:
    return " ".join(f"{b:02X}" for b in msg)


def parse_hex(text: str) -> bytes:
    """'f0 43 58 00 f7' / 'F0435800F7' / '0xF0,0x43' -> bytes."""
    t = re.sub(r"0x", "", text, flags=re.I)
    t = re.sub(r"[^0-9a-fA-F]", "", t)
    if len(t) % 2:
        raise ValueError("odd number of hex digits")
    return bytes.fromhex(t)


# --- identity ----------------------------------------------------------------

IDENTITY_REQUEST = bytes([0xF0, 0x43, 0x58, 0x00, 0xF7])


def identity_request() -> bytes:
    return IDENTITY_REQUEST


@dataclass
class Identity:
    firmware: str
    model: str
    raw: bytes


def parse_identity(msg: bytes) -> Optional[Identity]:
    """F0 43 58 10 "20260708110000" ... "MG300V2" 00 F7 (exact framing between strings unknown)."""
    if not is_sysex(msg) or msg[1:4] != bytes([0x43, 0x58, 0x10]):
        return None
    runs = re.findall(rb"[\x20-\x7E]{3,}", bytes(msg[4:-1]))
    strings = [r.decode("ascii") for r in runs]
    firmware = next((s for s in strings if s.isdigit()), "")
    model = next((s for s in strings if s.upper().startswith("MG")), "")
    return Identity(firmware=firmware, model=model, raw=bytes(msg))


# --- tempo (03) --------------------------------------------------------------

def get_tempo() -> bytes:
    return get_request(CMD_TEMPO)


def parse_tempo(msg: bytes) -> Optional[int]:
    """F0 43 58 70 03 02 <hi> <lo> ... F7 -> bpm = hi<<7 | lo."""
    m = parse_nux(msg)
    if not m or m.cmd != CMD_TEMPO or m.op != OP_GET_RESP or len(m.payload) < 2:
        return None
    return (m.payload[0] << 7) | m.payload[1]


def parse_tempo_extra(msg: bytes) -> Optional[int]:
    """Byte 8 of a 03 reply (stored + echoed by the pedal, meaning unknown)."""
    m = parse_nux(msg)
    if not m or m.cmd != CMD_TEMPO or m.op != OP_GET_RESP or len(m.payload) < 3:
        return None
    return m.payload[2]


def set_tempo(bpm: int, extra: int = 0) -> bytes:
    """F0 43 58 70 03 01 <bpm>>7> <bpm&7F> <extra> 00 00 00 00 00 F7 (verified 2026-11-06).

    Sets the live tempo and the tempo bytes 0x5B/0x5C of the ACTIVE scene's part
    (CORRECTION 2026-11-07). `extra` = byte 8 of the last 03 GET reply (send it
    back unchanged). Do not follow it with a 15 set (that reverts the tempo)."""
    if not 0 <= bpm <= 0x3FFF:
        raise ValueError("bpm out of range")
    return sysex(CMD_TEMPO, OP_SET, bytes([(bpm >> 7) & 0x7F, bpm & 0x7F, extra & 0x7F]) + bytes(5))


def tempo_response(bpm: int, extra: int = 0) -> bytes:
    """Device-side reply (used by the fake device)."""
    return sysex(CMD_TEMPO, OP_GET_RESP, bytes([(bpm >> 7) & 0x7F, bpm & 0x7F, extra & 0x7F]) + bytes(7))


# --- current (15) ------------------------------------------------------------

def get_current() -> bytes:
    return get_request(CMD_CURRENT)


@dataclass
class Current:
    preset: int          # 0-based
    extra: bytes         # b7.. meaning TBD (seen 03 58)


def parse_current(msg: bytes) -> Optional[Current]:
    m = parse_nux(msg)
    if not m or m.cmd != CMD_CURRENT or m.op != OP_GET_RESP or not m.payload:
        return None
    return Current(preset=m.payload[0], extra=m.payload[1:])


def current_response(preset: int) -> bytes:
    return sysex(CMD_CURRENT, OP_GET_RESP, bytes([preset & 0x7F, 0x03, 0x58]) + bytes(4))


def set_current(preset: int, scene: int) -> bytes:
    """F0 43 58 70 15 01 <p> <s> 00 00 00 00 00 00 F7 -> reply 15 02 <p> <s> ..

    NOT a scene switch (CORRECTION 2026-11-07): it only selects which part a
    plain `0C 00` returns (a "view" index). Switch the active scene with CC80.
    Unused by the editor; kept for the monitor / reverse engineering."""
    if not 0 <= preset <= 0x7F:
        raise ValueError("preset out of range")
    if not 0 <= scene <= 2:
        raise ValueError("scene out of range")
    return sysex(CMD_CURRENT, OP_SET, bytes([preset, scene]) + bytes(6))


def set_current_response(preset: int, scene: int) -> bytes:
    """Device-side reply to set_current (fake device)."""
    return sysex(CMD_CURRENT, OP_GET_RESP, bytes([preset & 0x7F, scene & 0x7F]) + bytes(5))


# --- block order (0D) --------------------------------------------------------

BLOCK_ORDER_LEN = 10


def get_block_order() -> bytes:
    return get_request(CMD_BLOCK_ORDER)


def parse_block_order(msg: bytes) -> Optional[list[int]]:
    """F0 43 58 70 0D 02 05 01 02 03 09 04 06 07 08 0A 00 00 F7 -> [5,1,2,3,9,4,6,7,8,10]."""
    m = parse_nux(msg)
    if not m or m.cmd != CMD_BLOCK_ORDER or m.op != OP_GET_RESP:
        return None
    return list(m.payload[:BLOCK_ORDER_LEN])


def block_order_response(order: list[int]) -> bytes:
    return sysex(CMD_BLOCK_ORDER, OP_GET_RESP, bytes(order) + bytes(2))


def set_block_order(order: list[int]) -> bytes:
    """F0 43 58 70 0D 01 <10 block ids> 00 00 F7 -> reply 0D 02 <order> (verified).
    Edit-buffer only; persistent after a save."""
    order = list(order)
    if len(order) != BLOCK_ORDER_LEN or any(not 0 <= b <= 0x7F for b in order):
        raise ValueError(f"block order must be {BLOCK_ORDER_LEN} ids 0..127")
    return sysex(CMD_BLOCK_ORDER, OP_SET, bytes(order) + bytes(2))


# --- edit buffer (0C) --------------------------------------------------------

def get_edit_buffer(preset: Optional[int] = None, scene: Optional[int] = None) -> bytes:
    """Plain `0C 00` = current scene; `0C 00 <preset> <scene>` = that scene of the edit
    buffer (verified 2026-11-06 for the current preset, scenes 0..2)."""
    if preset is None:
        return get_request(CMD_EDIT_BUFFER)
    if not 0 <= preset <= 0x7F or scene is None or not 0 <= scene <= 0x7F:
        raise ValueError("preset/scene out of range")
    return sysex(CMD_EDIT_BUFFER, OP_GET, bytes([preset, scene]))


@dataclass
class EditBuffer:
    preset: int
    scene: int
    data: bytes          # decoded, normally SCENE_SIZE bytes


def parse_edit_buffer(msg: bytes) -> Optional[EditBuffer]:
    """F0 43 58 70 0C 02 <preset> <scene> <189 encoded bytes> F7 -> EditBuffer."""
    m = parse_nux(msg)
    if not m or m.cmd != CMD_EDIT_BUFFER or m.op != OP_GET_RESP or len(m.payload) < 2:
        return None
    return EditBuffer(preset=m.payload[0], scene=m.payload[1], data=decode7(m.payload[2:]))


def edit_buffer_response(preset: int, scene: int, data: bytes) -> bytes:
    """Device-side 0C reply (used by the fake device and tests)."""
    return sysex(CMD_EDIT_BUFFER, OP_GET_RESP, bytes([preset & 0x7F, scene & 0x7F]) + encode7(data))


# --- stored preset scene (0B) + save --------------------------------------------
#
# F0 43 58 70 0B 00 <preset> <scene> F7
#   -> F0 43 58 70 0B 02 <preset> <scene> <189 encoded bytes> F7   (198 bytes)
# Same encoding/shape as the 0C edit buffer; a preset holds scenes 0..2.
# Verified on hardware 2026-11-06 (device answers in ~2 ms).
# SAVE (verified 2026-11-06, QuickTone sequence):
#   for part 0..2: F0 43 58 70 0B 01 <p> <part> <189 enc> F7 -> ack 0B 03 <p> <part> 01 00.. F7
#   commit:        F0 43 58 70 07 01 <p> <p> 00 00 00 00 00 00 F7 -> ack 07 03 <p> <p> 00.. F7

def get_stored_scene(preset: int, scene: int) -> bytes:
    if not 0 <= preset <= 0x7F:
        raise ValueError("preset out of range")
    if not 0 <= scene <= 0x7F:
        raise ValueError("scene out of range")
    return sysex(CMD_STORED, OP_GET, bytes([preset, scene]))


def parse_stored_scene(msg: bytes) -> Optional[EditBuffer]:
    """F0 43 58 70 0B 02 <preset> <scene> <189 encoded bytes> F7 -> EditBuffer shape."""
    m = parse_nux(msg)
    if not m or m.cmd != CMD_STORED or m.op != OP_GET_RESP or len(m.payload) < 2:
        return None
    return EditBuffer(preset=m.payload[0], scene=m.payload[1], data=decode7(m.payload[2:]))


def stored_scene_response(preset: int, scene: int, data: bytes) -> bytes:
    """Device-side 0B reply (fake device and tests)."""
    return sysex(CMD_STORED, OP_GET_RESP, bytes([preset & 0x7F, scene & 0x7F]) + encode7(data))


def set_stored_scene(preset: int, scene: int, data: bytes) -> bytes:
    """Write one part (scene) of a stored preset: F0 43 58 70 0B 01 <p> <s> <189 enc> F7.
    Not persistent until commit(preset)."""
    if not 0 <= preset <= 0x7F:
        raise ValueError("preset out of range")
    if not 0 <= scene <= 2:
        raise ValueError("scene out of range")
    data = bytes(data)
    if len(data) != SCENE_SIZE:
        raise ValueError(f"scene data must be {SCENE_SIZE} bytes, got {len(data)}")
    return sysex(CMD_STORED, OP_SET, bytes([preset, scene]) + encode7(data))


@dataclass
class StoredAck:
    preset: int
    scene: int
    ok: bool


def parse_stored_ack(msg: bytes) -> Optional[StoredAck]:
    """F0 43 58 70 0B 03 <p> <s> 01 00.. F7 (01 = ok)."""
    m = parse_nux(msg)
    if not m or m.cmd != CMD_STORED or m.op != OP_SET_ACK or len(m.payload) < 3:
        return None
    return StoredAck(preset=m.payload[0], scene=m.payload[1], ok=m.payload[2] == 0x01)


def stored_ack_response(preset: int, scene: int, ok: bool = True) -> bytes:
    """Device-side 0B set ack (fake device)."""
    return sysex(CMD_STORED, OP_SET_ACK, bytes([preset & 0x7F, scene & 0x7F, 1 if ok else 0]) + bytes(5))


def commit(preset: int) -> bytes:
    """F0 43 58 70 07 01 <p> <p> 00 00 00 00 00 00 F7 - make the 3 written parts permanent."""
    if not 0 <= preset <= 0x7F:
        raise ValueError("preset out of range")
    return sysex(CMD_COMMIT, OP_SET, bytes([preset, preset]) + bytes(6))


def parse_commit_ack(msg: bytes) -> Optional[int]:
    """F0 43 58 70 07 03 <p> <p> 00.. F7 -> p (None if not a commit ack)."""
    m = parse_nux(msg)
    if not m or m.cmd != CMD_COMMIT or m.op != OP_SET_ACK or len(m.payload) < 2:
        return None
    if m.payload[0] != m.payload[1]:
        return None
    return m.payload[0]


def commit_ack_response(preset: int) -> bytes:
    return sysex(CMD_COMMIT, OP_SET_ACK, bytes([preset & 0x7F, preset & 0x7F]) + bytes(6))


# --- notifications (7E) --------------------------------------------------------

def is_modified_notification(msg: bytes) -> bool:
    """F0 43 58 70 7E 02 14 .. F7 = the pedal's edit buffer was modified."""
    m = parse_nux(msg)
    return bool(m and m.cmd == CMD_NOTIFY and m.op == OP_GET_RESP and m.payload[:1] == bytes([NOTIFY_MODIFIED]))


def modified_notification() -> bytes:
    return sysex(CMD_NOTIFY, OP_GET_RESP, bytes([NOTIFY_MODIFIED]) + bytes(4))


def unsupported_response() -> bytes:
    return sysex(CMD_UNSUPPORTED, OP_GET_RESP, bytes(4))


# --- channel messages ----------------------------------------------------------

def program_change(preset: int, channel: int = 0) -> bytes:
    """Select a preset (0-based). The pedal itself emits C0 <preset> on preset change."""
    if not 0 <= preset <= 127:
        raise ValueError("preset out of range")
    return bytes([0xC0 | (channel & 0x0F), preset])


def control_change(cc: int, value: int, channel: int = 0) -> bytes:
    return bytes([0xB0 | (channel & 0x0F), cc & 0x7F, max(0, min(127, int(value)))])


# --- human-readable description (MIDI monitor) --------------------------------

def describe(msg: bytes) -> str:
    if not msg:
        return ""
    s = msg[0]
    if len(msg) == 1 and s >= 0xF8:
        return {0xF8: "clock", 0xFA: "start", 0xFB: "continue", 0xFC: "stop",
                0xFE: "active-sense", 0xFF: "reset"}.get(s, "realtime")
    if s == SYSEX_START:
        if msg[:4] == bytes([0xF0, 0x43, 0x58, 0x00]):
            return "identity request"
        if msg[:4] == bytes([0xF0, 0x43, 0x58, 0x10]):
            ident = parse_identity(msg)
            return f"identity fw={ident.firmware} model={ident.model}" if ident else "identity"
        m = parse_nux(msg)
        if m:
            name = CMD_NAMES.get(m.cmd, f"cmd {m.cmd:02X}")
            op = OP_NAMES.get(m.op, f"op {m.op:02X}")
            extra = ""
            if m.cmd in (CMD_EDIT_BUFFER, CMD_STORED) and m.op == OP_GET_RESP and len(m.payload) >= 2:
                extra = f" preset={m.payload[0]} scene={m.payload[1]} ({len(m.payload) - 2} enc bytes)"
            elif m.cmd in (CMD_STORED, CMD_EDIT_BUFFER) and m.op == OP_GET and len(m.payload) >= 2:
                extra = f" preset={m.payload[0]} scene={m.payload[1]}"
            elif m.cmd == CMD_STORED and m.op == OP_SET and len(m.payload) >= 2:
                extra = f" SAVE preset={m.payload[0]} part={m.payload[1]} ({len(m.payload) - 2} enc bytes)"
            elif m.cmd == CMD_STORED and m.op == OP_SET_ACK and len(m.payload) >= 3:
                extra = f" preset={m.payload[0]} part={m.payload[1]} {'ok' if m.payload[2] == 1 else 'FAIL'}"
            elif m.cmd == CMD_COMMIT and len(m.payload) >= 1:
                extra = f" preset={m.payload[0]}"
            elif m.cmd == CMD_CURRENT and m.op == OP_SET and len(m.payload) >= 2:
                extra = f" preset={m.payload[0]} scene={m.payload[1]}"
            elif m.cmd == CMD_TEMPO and m.op == OP_SET and len(m.payload) >= 2:
                extra = f" bpm={(m.payload[0] << 7) | m.payload[1]}"
            elif m.cmd == CMD_BLOCK_ORDER and m.op in (OP_SET, OP_GET_RESP):
                extra = " " + ",".join(str(b) for b in m.payload[:BLOCK_ORDER_LEN])
            elif m.cmd == CMD_NOTIFY and m.payload[:1] == bytes([NOTIFY_MODIFIED]):
                extra = " (edit buffer modified)"
            elif m.cmd == CMD_TEMPO and m.op == OP_GET_RESP:
                extra = f" bpm={parse_tempo(msg)}"
            elif m.cmd == CMD_CURRENT and m.op == OP_GET_RESP and m.payload:
                extra = f" preset={m.payload[0]}"
            return f"{name} {op}{extra}"
        return f"sysex ({len(msg)} bytes)"
    kind = s & 0xF0
    ch = (s & 0x0F) + 1
    if kind == 0xC0 and len(msg) >= 2:
        return f"PC ch{ch} {msg[1]}"
    if kind == 0xB0 and len(msg) >= 3:
        if msg[1] == CC_SCENE:
            return f"CC ch{ch} #{msg[1]}={msg[2]} (scene {msg[2] + 1})"
        if msg[1] == CC_CURRENT_BLOCK:
            return f"CC ch{ch} #{msg[1]}={msg[2]} (selected block)"
        return f"CC ch{ch} #{msg[1]}={msg[2]}"
    if kind == 0x90 and len(msg) >= 3:
        return f"note-on ch{ch} {msg[1]} v{msg[2]}"
    if kind == 0x80 and len(msg) >= 3:
        return f"note-off ch{ch} {msg[1]}"
    return ""


# --- live-edit write strategy ------------------------------------------------
#
# >>> STRATEGY HOOK: set_param() <<<
# How the pedal expects live edits is NOT yet determined (CC vs. a SysEx "set"
# message). The controller only ever talks to a ParamWriter; swap the
# implementation once the write path has been reverse engineered.
# Every method returns a list of (coalesce_key, message) tuples that the
# controller hands to the coalescing writer: same key -> only the latest value
# is sent, so a dragged slider never builds a backlog.
#
# `param` is duck-typed (layout.ParamSpec): .name .slot .min .max
# `model_byte` is the complete new scene byte (index | alt bit | bypass bit).

Outgoing = list[tuple[Optional[str], bytes]]


class ParamWriter:
    """Interface + no-op default: nothing is sent to the pedal."""

    name = "none (read-only)"

    def set_param(self, block_id: str, param, value: int) -> Outgoing:
        return []

    def set_bypass(self, block_id: str, bypassed: bool, model_byte: int) -> Outgoing:
        return []

    def set_model(self, block_id: str, model_value: int, model_byte: int) -> Outgoing:
        return []

    def lookup_cc(self, channel: int, cc: int) -> Optional[tuple[str, str]]:
        """Reverse-map an incoming CC to (block_id, what); what is a param
        name, "#<slot>", "switch" or "bypass". None if unknown."""
        return None


class CCParamWriter(ParamWriter):
    """Live edits as Control Change messages.

    cc_map keys -> CC number:
      "BLOCK.PARAM"  a named parameter, e.g. "AMP.GAIN"
      "BLOCK.#n"     knob slot n of whatever model is loaded (QuickTone style)
      "BLOCK.switch" block switch CC, value = full model byte (index|0x20 alt|0x40 bypass)
      "BLOCK.bypass" plain on/off CC, value 127 = on, 0 = bypassed
    (Tempo is NOT a CC: it is written with protocol.set_tempo(), SysEx 03 set.)
    With scale=True parameter values are mapped [min,max] -> 0..127, otherwise
    sent raw (clamped to 0..127).
    """

    name = "CC"

    def __init__(self, cc_map: dict[str, int], channel: int = 0, scale: bool = True):
        self.cc_map = {k: int(v) for k, v in cc_map.items()}
        self.channel = channel
        self.scale_values = scale
        self._reverse = {v: k for k, v in self.cc_map.items()}

    @classmethod
    def from_layout(cls, layout, channel: int = 0, scale: bool = False) -> "CCParamWriter":
        """Build a map from layout blocks' cc_switch / cc_params (research/layout_draft.json)."""
        m: dict[str, int] = {}
        for b in layout.blocks:
            if b.cc_switch is not None:
                m[f"{b.id}.switch"] = b.cc_switch
            for slot, cc in enumerate(b.cc_params or []):
                if cc is not None:
                    m[f"{b.id}.#{slot}"] = cc
        return cls(m, channel=channel, scale=scale)

    @staticmethod
    def scale(value: int, lo: int, hi: int) -> int:
        if hi <= lo:
            return 0
        v = min(max(value, lo), hi)
        return round((v - lo) * 127 / (hi - lo))

    @staticmethod
    def unscale(cc_value: int, lo: int, hi: int) -> int:
        if hi <= lo:
            return lo
        return lo + round(cc_value * (hi - lo) / 127)

    def to_cc_value(self, value: int, lo: int, hi: int) -> int:
        return self.scale(value, lo, hi) if self.scale_values else max(0, min(127, int(value)))

    def from_cc_value(self, cc_value: int, lo: int, hi: int) -> int:
        return self.unscale(cc_value, lo, hi) if self.scale_values else cc_value

    def _cc(self, cc: Optional[int], value: int) -> Outgoing:
        if cc is None:
            return []
        return [(f"cc:{self.channel}:{cc}", control_change(cc, value, self.channel))]

    def set_param(self, block_id, param, value):
        cc = self.cc_map.get(f"{block_id}.{param.name}")
        if cc is None and getattr(param, "slot", None) is not None:
            cc = self.cc_map.get(f"{block_id}.#{param.slot}")
        return self._cc(cc, self.to_cc_value(value, param.min, param.max))

    def set_bypass(self, block_id, bypassed, model_byte):
        if f"{block_id}.switch" in self.cc_map:
            return self._cc(self.cc_map[f"{block_id}.switch"], model_byte & 0x7F)
        return self._cc(self.cc_map.get(f"{block_id}.bypass"), 0 if bypassed else 127)

    def set_model(self, block_id, model_value, model_byte):
        return self._cc(self.cc_map.get(f"{block_id}.switch"), model_byte & 0x7F)

    def lookup_cc(self, channel, cc):
        if channel != self.channel or cc not in self._reverse:
            return None
        key = self._reverse[cc]
        if "." not in key:
            return None
        block, what = key.split(".", 1)
        return block, what


class SysExParamWriter(ParamWriter):
    """Placeholder for a SysEx (op 01 set) live-edit path. Format not known yet."""

    name = "SysEx (not implemented)"

    def _todo(self, *_a, **_k):
        raise NotImplementedError("SysEx live-edit format not reverse engineered yet")

    set_param = set_bypass = set_model = _todo
