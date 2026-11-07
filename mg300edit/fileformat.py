"""Preset files: our JSON export/import (`.mg300.json`) and read-only import of
QuickTone's `.mg300MK2patch`.

All input is treated as untrusted: sizes, types and lengths are validated and
anything unexpected raises FileFormatError (never a crash deeper in the app).

JSON single preset:
  {"format": "mg300edit.preset", "version": 1, "device": "MG-300 MKII",
   "preset_index": 8, "label": "User 3C", "name": "Surf Rock Clean",
   "scenes": ["<252 hex chars>", x3], "edit_buffer": "<252 hex chars>" | null}
JSON bundle:
  {"format": "mg300edit.bundle", "version": 1, "device": "MG-300 MKII",
   "presets": [<single preset objects>]}

QuickTone .mg300MK2patch (per hemanthsuryya/mg300-forge-II app/preset_format.py
and its template file): 4-byte header 11 00 00 00, then three 126-byte decoded
scenes at offsets 4, 130, 256 (same layout as the 0C/0B decoded scene: name at
0x69), then ~8 KB of zero/IR data (file normally 8654 bytes). Only the scenes
are imported.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from . import presets as PR
from .protocol import SCENE_SIZE

DEVICE = "MG-300 MKII"
FORMAT_PRESET = "mg300edit.preset"
FORMAT_BUNDLE = "mg300edit.bundle"
VERSION = 1
EXTENSION = ".mg300.json"
QT_EXTENSION = ".mg300MK2patch"

MAX_JSON_SIZE = 4 * 1024 * 1024
MAX_QT_SIZE = 1024 * 1024
MAX_BUNDLE_PRESETS = 128
NAME_OFFSET, NAME_LEN = 0x69, 16

QT_HEADER = bytes([0x11, 0x00, 0x00, 0x00])
QT_SCENES_OFFSET = 4
QT_EXPECTED_SIZE = 8654
QT_MIN_SIZE = QT_SCENES_OFFSET + PR.SCENES_PER_PRESET * SCENE_SIZE


class FileFormatError(ValueError):
    pass


@dataclass
class PresetData:
    name: str = ""
    preset_index: Optional[int] = None
    scenes: list[Optional[bytes]] = field(default_factory=list)   # up to 3, None = not read
    edit_buffer: Optional[bytes] = None
    source: str = "json"          # json | bundle | quicktone

    def sources(self) -> list[tuple[str, bytes]]:
        """Applicable scene data, edit buffer first: [(key, data)], key 'edit' or 'scene0'.."""
        out = []
        if self.edit_buffer is not None:
            out.append(("edit", self.edit_buffer))
        for i, s in enumerate(self.scenes):
            if s is not None:
                out.append((f"scene{i}", s))
        return out


def scene_name(scene: bytes) -> str:
    raw = bytes(scene[NAME_OFFSET:NAME_OFFSET + NAME_LEN]).split(b"\x00", 1)[0]
    return raw.decode("ascii", errors="replace").strip()


# ---------------------------------------------------------------- writing ----

def preset_to_dict(p: PresetData) -> dict:
    d = {
        "format": FORMAT_PRESET,
        "version": VERSION,
        "device": DEVICE,
        "preset_index": p.preset_index,
        "label": PR.full_label(p.preset_index) if p.preset_index is not None
        and PR.is_valid(p.preset_index) else None,
        "name": p.name,
        "scenes": [s.hex() if s is not None else None for s in p.scenes],
        "edit_buffer": p.edit_buffer.hex() if p.edit_buffer is not None else None,
    }
    return d


def dump_preset(p: PresetData) -> str:
    return json.dumps(preset_to_dict(p), indent=1) + "\n"


def dump_bundle(items: list[PresetData]) -> str:
    d = {"format": FORMAT_BUNDLE, "version": VERSION, "device": DEVICE,
         "presets": [preset_to_dict(p) for p in items]}
    return json.dumps(d, indent=1) + "\n"


# ---------------------------------------------------------------- reading ----

def _scene_hex(v, what: str) -> Optional[bytes]:
    if v is None:
        return None
    if not isinstance(v, str) or len(v) > 4 * SCENE_SIZE:
        raise FileFormatError(f"{what}: not a hex string")
    try:
        data = bytes.fromhex(v)
    except ValueError as e:
        raise FileFormatError(f"{what}: bad hex ({e})") from None
    if len(data) != SCENE_SIZE:
        raise FileFormatError(f"{what}: {len(data)} bytes, expected {SCENE_SIZE}")
    return data


def preset_from_dict(d, source: str = "json") -> PresetData:
    if not isinstance(d, dict):
        raise FileFormatError("preset entry is not an object")
    if d.get("format", FORMAT_PRESET) != FORMAT_PRESET:
        raise FileFormatError(f"unknown format {d.get('format')!r}")
    _check_version_device(d)
    scenes_raw = d.get("scenes", [])
    if not isinstance(scenes_raw, list) or len(scenes_raw) > PR.SCENES_PER_PRESET:
        raise FileFormatError(f"scenes: expected a list of at most {PR.SCENES_PER_PRESET}")
    scenes = [_scene_hex(s, f"scene {i + 1}") for i, s in enumerate(scenes_raw)]
    eb = _scene_hex(d.get("edit_buffer"), "edit_buffer")
    if eb is None and not any(s is not None for s in scenes):
        raise FileFormatError("no scene data in preset")
    idx = d.get("preset_index")
    if isinstance(idx, bool) or not isinstance(idx, int) or not PR.is_valid(idx):
        idx = None
    name = d.get("name")
    if not isinstance(name, str):
        first = eb if eb is not None else next(s for s in scenes if s is not None)
        name = scene_name(first)
    return PresetData(name=name[:64], preset_index=idx, scenes=scenes, edit_buffer=eb, source=source)


def _check_version_device(d: dict) -> None:
    v = d.get("version", VERSION)
    if isinstance(v, bool) or not isinstance(v, int):
        raise FileFormatError("version is not a number")
    if v > VERSION:
        raise FileFormatError(f"file version {v} is newer than supported ({VERSION})")
    dev = d.get("device", DEVICE)
    if dev != DEVICE:
        raise FileFormatError(f"file is for {dev!r}, not {DEVICE}")


def load_json(data: bytes) -> list[PresetData]:
    if len(data) > MAX_JSON_SIZE:
        raise FileFormatError("file too large")
    try:
        d = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as e:
        raise FileFormatError(f"not valid JSON: {e}") from None
    if not isinstance(d, dict):
        raise FileFormatError("top level is not an object")
    fmt = d.get("format")
    if fmt == FORMAT_PRESET:
        return [preset_from_dict(d)]
    if fmt == FORMAT_BUNDLE:
        _check_version_device(d)
        items = d.get("presets")
        if not isinstance(items, list) or not items:
            raise FileFormatError("bundle has no presets")
        if len(items) > MAX_BUNDLE_PRESETS:
            raise FileFormatError("bundle has too many presets")
        return [preset_from_dict(x, source="bundle") for x in items]
    raise FileFormatError(f"unknown format {fmt!r}")


def load_quicktone(data: bytes) -> PresetData:
    if len(data) > MAX_QT_SIZE:
        raise FileFormatError("file too large for a QuickTone patch")
    if len(data) < QT_MIN_SIZE:
        raise FileFormatError(f"file too small for a QuickTone patch ({len(data)} bytes)")
    if data[:4] != QT_HEADER:
        raise FileFormatError(f"unknown QuickTone header {data[:4].hex(' ')}")
    scenes = [bytes(data[QT_SCENES_OFFSET + i * SCENE_SIZE:QT_SCENES_OFFSET + (i + 1) * SCENE_SIZE])
              for i in range(PR.SCENES_PER_PRESET)]
    return PresetData(name=scene_name(scenes[0]), scenes=scenes, source="quicktone")


def load_bytes(data: bytes, filename: str = "") -> list[PresetData]:
    """Detect the format by content (JSON starts with '{') or extension."""
    head = data[:64].lstrip()
    if head[:1] == b"{" or filename.lower().endswith(".json"):
        return load_json(data)
    if data[:4] == QT_HEADER or filename.lower().endswith(QT_EXTENSION.lower()):
        return [load_quicktone(data)]
    raise FileFormatError("unrecognized file (expected .mg300.json or .mg300MK2patch)")


def load_path(path: str) -> list[PresetData]:
    """Read a file with a hard size cap (never reads more than the cap + 1 bytes)."""
    cap = max(MAX_JSON_SIZE, MAX_QT_SIZE)
    with open(path, "rb") as f:
        data = f.read(cap + 1)
    if len(data) > cap:
        raise FileFormatError("file too large")
    return load_bytes(data, path)
