"""Preset numbering of the MG-300 MKII (verified on hardware 2026-11-06).

72 presets: PC 0-35 = user (custom) bank, PC 36-71 = factory bank. Each group is
shown on the pedal as 9 banks x A/B/C/D: index i in a group -> f"{i//4+1}{'ABCD'[i%4]}"
(user 3B = PC 9, factory 3B = PC 45; factory index = pc-36). PC 72-127 return empty/garbage names and
are never shown. Every preset holds 3 scenes (0B get scene 0..2).
"""

from __future__ import annotations

import unicodedata
from typing import Optional

PRESET_COUNT = 72
BANK_SIZE = 36
USER_PRESETS = range(0, BANK_SIZE)
FACTORY_PRESETS = range(BANK_SIZE, PRESET_COUNT)
SCENES_PER_PRESET = 3
SLOTS = "ABCD"
SLOTS_PER_BANK = len(SLOTS)
BANKS = BANK_SIZE // SLOTS_PER_BANK      # 9

NAME_OFFSET = 0x69
NAME_LEN = 16

BANK_USER = "user"
BANK_FACTORY = "factory"


def is_valid(preset: int) -> bool:
    return 0 <= preset < PRESET_COUNT


def is_user(preset: int) -> bool:
    """Only user presets (PC 0-35) may be written from the UI."""
    return preset in USER_PRESETS


def bank(preset: int) -> str:
    if not is_valid(preset):
        raise ValueError(f"preset {preset} out of range 0..{PRESET_COUNT - 1}")
    return BANK_USER if preset < BANK_SIZE else BANK_FACTORY


def slot_label(preset: int) -> str:
    """PC number -> label within its bank group, e.g. 9 -> '3B', 45 -> '3B'."""
    bank(preset)
    i = preset % BANK_SIZE
    return f"{i // SLOTS_PER_BANK + 1}{SLOTS[i % SLOTS_PER_BANK]}"


def full_label(preset: int, user: str = "User", factory: str = "Factory") -> str:
    """9 -> 'User 3B', 45 -> 'Factory 3B'."""
    return f"{user if bank(preset) == BANK_USER else factory} {slot_label(preset)}"


def parse_label(text: str, factory: bool = False) -> Optional[int]:
    """'3B' -> 9 (or 45 with factory=True); None if not a valid label."""
    t = text.strip().upper()
    if len(t) < 2 or t[-1] not in SLOTS or not t[:-1].isdigit():
        return None
    b = int(t[:-1])
    if not 1 <= b <= BANKS:
        return None
    return (BANK_SIZE if factory else 0) + (b - 1) * SLOTS_PER_BANK + SLOTS.index(t[-1])


# --- preset names (scene bytes 0x69..0x78, ASCII, NUL padded) ----------------

def sanitize_name(name: str) -> str:
    """Printable ASCII only, max 16 chars: accents are stripped (á -> a),
    anything else non-printable becomes '_'; trailing spaces dropped."""
    text = unicodedata.normalize("NFKD", name or "")
    out = []
    for ch in text:
        if unicodedata.combining(ch):
            continue
        out.append(ch if 0x20 <= ord(ch) <= 0x7E else "_")
    return "".join(out)[:NAME_LEN].rstrip()


def encode_name(name: str) -> bytes:
    return sanitize_name(name).encode("ascii").ljust(NAME_LEN, b"\x00")


def with_name(scene: bytes, name: str) -> bytes:
    """Copy of a decoded scene with its name bytes replaced."""
    s = bytearray(scene)
    if len(s) < NAME_OFFSET + NAME_LEN:
        raise ValueError("scene too short for a name")
    s[NAME_OFFSET:NAME_OFFSET + NAME_LEN] = encode_name(name)
    return bytes(s)
