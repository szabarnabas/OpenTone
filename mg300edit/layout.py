"""Scene layout spec: where things live inside the 126-byte decoded scene.

Loaded from JSON. Shape (all fields optional except where noted):
{
  "scene_size": 126,
  "preset_count": 72,
  "name": {"offset": 105, "length": 16},
  "tempo": {"offset": 92},
  "block_order": {"offset": 93, "length": 10},
  "blocks": [
    {"id": "AMP", "chain_id": 3, "label": "Amp", "model_offset": 3, "bypass_bit": 64,
     "bypass_when_set": true, "model_mask": 63, "cc_switch": 3, "cc_params": [24, 25],
     "models": [{"index": 1, "name": "...",
                 "params": [{"name": "Gain", "slot": 0, "offset": 28, "min": 0, "max": 100,
                             "default": 50, "kind": "knob"}]}],
     "alt_type": {"hosts": "EFX", "flag_bit": 32, "models": [...]}}
  ]
}
"tempo" may also carry "hi_offset" (bpm = hi << 7 | lo).
Model "values" are the scene byte without the bypass bit, i.e. index | alt bit;
alternate-type models (alt_type) are stored with index | flag_bit.
The loader is tolerant: unknown keys are ignored, missing ones get defaults,
unknown model indices show as "Model #n", unknown chain ids as "#n".
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

DEFAULT_LAYOUT = Path(__file__).resolve().parent / "data" / "layout.json"


def _int(v: Any, default: Optional[int]) -> Optional[int]:
    try:
        return int(v, 0) if isinstance(v, str) else int(v)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- display helpers
# Pure functions (no GTK): turn raw 0-100 param values into user-facing text.

# plausible output ranges per unit; a hint whose mapped range leaves these is ignored
_UNIT_BOUNDS = {"dB": (-96.0, 48.0), "Hz": (5.0, 40000.0), "ms": (0.0, 20000.0)}
_NUM = r"[-+]?\d+(?:\.\d+)?"
_LINEAR_RE = re.compile(rf"^\s*(dB|Hz|ms)\s*=\s*({_NUM})\s*([-+])\s*({_NUM})\s*\*\s*v\b")
_EXP_RE = re.compile(rf"^\s*(dB|Hz|ms)\s*=\s*({_NUM})\s*\*\s*({_NUM})\s*\*\*\s*"
                     rf"\(\s*v\s*/\s*({_NUM})\s*\)")


@dataclass(frozen=True)
class UnitScale:
    """value = a + b*v (linear) or a * b**(v/d) (exp)."""
    unit: str
    kind: str          # "linear" | "exp"
    a: float
    b: float
    d: float = 100.0

    def convert(self, v: float) -> float:
        if self.kind == "linear":
            return self.a + self.b * v
        return self.a * self.b ** (v / self.d)

    @property
    def bipolar(self) -> bool:
        """dB scales that cross 0 inside the range (e.g. -12..+12 dB)."""
        return self.unit == "dB"

    def zero_raw(self, lo: int, hi: int) -> Optional[float]:
        """Raw value where the converted value is 0 (linear scales only)."""
        if self.kind != "linear" or self.b == 0:
            return None
        z = -self.a / self.b
        return z if lo < z < hi else None


def parse_scale(hint: str, lo: int = 0, hi: int = 100) -> Optional[UnitScale]:
    """Parse a layout `scale` hint like "dB = -12 + 0.24*v (notes)" or
    "Hz = 20*50**(v/100)". Returns None for missing/unparseable/implausible hints."""
    if not hint:
        return None
    m = _LINEAR_RE.match(hint)
    if m:
        b = float(m.group(4)) * (-1 if m.group(3) == "-" else 1)
        sc = UnitScale(m.group(1), "linear", float(m.group(2)), b)
    else:
        m = _EXP_RE.match(hint)
        if not m:
            return None
        d = float(m.group(4))
        if d == 0:
            return None
        sc = UnitScale(m.group(1), "exp", float(m.group(2)), float(m.group(3)), d)
        if sc.a <= 0 or sc.b <= 0:
            return None
    try:
        ends = (sc.convert(lo), sc.convert(hi))
    except (OverflowError, ZeroDivisionError):
        return None
    blo, bhi = _UNIT_BOUNDS[sc.unit]
    if not all(math.isfinite(x) and blo <= x <= bhi for x in ends) or ends[0] == ends[1]:
        return None
    return sc


def format_unit(x: float, unit: str) -> str:
    """Compact human formatting: "+2.4 dB", "0 dB", "180 Hz", "1.2 kHz", "350 ms", "1.20 s"."""
    if unit == "dB":
        if abs(x) < 0.05:
            return "0 dB"
        return f"{x:+.1f} dB"
    if unit == "Hz":
        if x >= 1000:
            k = x / 1000
            return f"{k:.1f} kHz" if k < 10 else f"{k:.0f} kHz"
        return f"{x:.0f} Hz"
    if unit == "ms":
        return f"{x / 1000:.2f} s" if x >= 1000 else f"{x:.0f} ms"
    return f"{x:g}{unit}"


def format_param_value(param: "ParamSpec", v: int) -> str:
    v = int(v)
    i = v - param.min
    if param.choices and 0 <= i < len(param.choices):
        return str(param.choices[i])
    if param.kind == "toggle01" and param.max - param.min == 1:
        return "Be" if v == param.max else "Ki"
    sc = parse_scale(param.scale, param.min, param.max)
    if sc is not None:
        return format_unit(sc.convert(v), sc.unit)
    return f"{v}{param.unit}"


_FREQ_NAME_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(K?)HZ$", re.IGNORECASE)


def pretty_param_name(name: str) -> str:
    """Display form of a layout param name: "1.2KHZ" -> "1.2 kHz", "100HZ" -> "100 Hz";
    everything else unchanged."""
    m = _FREQ_NAME_RE.match(name.strip())
    if m:
        return f"{m.group(1)} {'kHz' if m.group(2) else 'Hz'}"
    return name


@dataclass
class ParamSpec:
    name: str
    offset: Optional[int]
    min: int = 0
    max: int = 100
    default: int = 0
    unit: str = ""
    slot: Optional[int] = None
    kind: str = "knob"
    choices: list = field(default_factory=list)   # selector params: label per value (min..max)
    scale: str = ""                                # display hint, e.g. "dB = -12 + 0.24*v"

    def label(self, v: int) -> str:
        i = int(v) - self.min
        if self.choices and 0 <= i < len(self.choices):
            return str(self.choices[i])
        return f"{int(v)}{self.unit}"

    def display(self, v: int) -> str:
        """User-facing value text: choice name, unit-scaled value (when the
        layout carries a plausible `scale` hint) or the raw value."""
        return format_param_value(self, v)

    @property
    def unit_scale(self) -> Optional["UnitScale"]:
        return parse_scale(self.scale, self.min, self.max)

    def read(self, scene: bytes) -> Optional[int]:
        if self.offset is None or self.offset >= len(scene):
            return None
        return scene[self.offset]

    def write(self, scene: bytearray, value: int) -> int:
        value = max(self.min, min(self.max, int(value)))
        if self.offset is not None and self.offset < len(scene):
            scene[self.offset] = value & 0xFF
        return value


@dataclass
class ModelSpec:
    index: int
    name: str
    params: list[ParamSpec] = field(default_factory=list)
    known: bool = True

    def param(self, name: str) -> Optional[ParamSpec]:
        return next((p for p in self.params if p.name == name), None)


@dataclass
class BlockSpec:
    id: str
    chain_id: Optional[int]
    label: str = ""
    model_offset: Optional[int] = None
    bypass_bit: int = 0x40
    bypass_when_set: bool = True
    models: list[ModelSpec] = field(default_factory=list)
    known: bool = True
    model_mask: Optional[int] = None     # index bits; default = all but bypass bit
    alt_bit: int = 0                     # alternate block type flag (0x20), 0 = none
    cc_switch: Optional[int] = None
    cc_params: list = field(default_factory=list)
    notes: str = ""

    @property
    def title(self) -> str:
        return self.label or self.id

    @property
    def value_mask(self) -> int:
        """Bits of the model byte that select the model (index | alt bit)."""
        mask = self.model_mask if self.model_mask is not None else (0xFF & ~self.bypass_bit)
        return (mask | self.alt_bit) & ~self.bypass_bit & 0xFF

    def _byte(self, scene: bytes) -> Optional[int]:
        if self.model_offset is None or self.model_offset >= len(scene):
            return None
        return scene[self.model_offset]

    def model_index(self, scene: bytes) -> Optional[int]:
        b = self._byte(scene)
        return None if b is None else b & self.value_mask

    def model_byte(self, scene: bytes) -> Optional[int]:
        return self._byte(scene)

    def is_bypassed(self, scene: bytes) -> Optional[bool]:
        b = self._byte(scene)
        if b is None or not self.bypass_bit:
            return None
        is_set = bool(b & self.bypass_bit)
        return is_set if self.bypass_when_set else not is_set

    def model(self, index: Optional[int]) -> Optional[ModelSpec]:
        if index is None:
            return None
        for m in self.models:
            if m.index == index:
                return m
        if self.alt_bit and index & self.alt_bit:
            return ModelSpec(index=index, name=f"Alt model #{index & ~self.alt_bit}", known=False)
        return ModelSpec(index=index, name=f"Model #{index}", known=False)

    def current_model(self, scene: bytes) -> Optional[ModelSpec]:
        return self.model(self.model_index(scene))

    def set_bypassed(self, scene: bytearray, bypassed: bool) -> None:
        b = self._byte(scene)
        if b is None or not self.bypass_bit:
            return
        on = bypassed if self.bypass_when_set else not bypassed
        scene[self.model_offset] = (b | self.bypass_bit) if on else (b & ~self.bypass_bit & 0xFF)

    def set_model(self, scene: bytearray, index: int) -> None:
        b = self._byte(scene)
        if b is None:
            return
        mask = self.value_mask
        scene[self.model_offset] = (b & ~mask & 0xFF) | (index & mask)


@dataclass
class Layout:
    scene_size: int = 126
    preset_count: int = 128
    name_offset: Optional[int] = None
    name_length: int = 16
    tempo_offset: Optional[int] = None
    tempo_hi_offset: Optional[int] = None
    order_offset: Optional[int] = None
    order_length: int = 10
    blocks: list[BlockSpec] = field(default_factory=list)
    source: str = ""

    # -- loading --
    @classmethod
    def from_dict(cls, d: dict, source: str = "") -> "Layout":
        d = d if isinstance(d, dict) else {}
        name = d.get("name") or {}
        tempo = d.get("tempo") or {}
        order = d.get("block_order") or {}
        lay = cls(
            scene_size=_int(d.get("scene_size"), 126) or 126,
            preset_count=_int(d.get("preset_count"), 128) or 128,
            name_offset=_int(name.get("offset"), None) if isinstance(name, dict) else None,
            name_length=(_int(name.get("length"), 16) if isinstance(name, dict) else 16) or 16,
            tempo_offset=_int(tempo.get("offset"), None) if isinstance(tempo, dict) else None,
            tempo_hi_offset=_int(tempo.get("hi_offset"), None) if isinstance(tempo, dict) else None,
            order_offset=_int(order.get("offset"), None) if isinstance(order, dict) else None,
            order_length=(_int(order.get("length"), 10) if isinstance(order, dict) else 10) or 10,
            source=source,
        )
        for i, b in enumerate(d.get("blocks") or []):
            if not isinstance(b, dict):
                continue
            try:
                lay.blocks.append(cls._block(b, i))
            except Exception as e:  # noqa: BLE001 - one bad block must not kill the layout
                log.warning("layout: skipping block %r: %s", b.get("id"), e)
        return lay

    @staticmethod
    def _models(items, flag: int = 0, suffix: str = "") -> list[ModelSpec]:
        models = []
        for j, m in enumerate(items or []):
            if not isinstance(m, dict):
                continue
            params = []
            for p in m.get("params") or []:
                if not isinstance(p, dict) or "name" not in p:
                    continue
                lo, hi = _int(p.get("min"), 0), _int(p.get("max"), 100)
                if hi < lo:
                    lo, hi = hi, lo
                params.append(ParamSpec(
                    name=str(p["name"]), offset=_int(p.get("offset"), None), min=lo, max=hi,
                    default=_int(p.get("default"), lo), unit=str(p.get("unit", "")),
                    slot=_int(p.get("slot"), None), kind=str(p.get("kind", "knob")),
                    choices=[str(c) for c in p.get("choices") or []],
                    scale=str(p.get("scale") or "")))
            idx = _int(m.get("index"), j)
            name = str(m.get("name") or f"Model #{idx}") + suffix
            models.append(ModelSpec(index=idx | flag, name=name, params=params))
        return models

    @classmethod
    def _block(cls, b: dict, i: int) -> BlockSpec:
        models = cls._models(b.get("models"))
        alt = b.get("alt_type") if isinstance(b.get("alt_type"), dict) else None
        alt_bit = 0
        if alt:
            alt_bit = _int(alt.get("flag_bit"), 0x20) or 0
            hosts = alt.get("hosts")
            models += cls._models(alt.get("models"), alt_bit, f" [{hosts}]" if hosts else " [alt]")
        cc_params = b.get("cc_params")
        bypass = b.get("bypass_bit", 0x40)
        return BlockSpec(
            id=str(b.get("id") or f"BLOCK{i}"),
            chain_id=_int(b.get("chain_id"), None),
            label=str(b.get("label", "")),
            model_offset=_int(b.get("model_offset"), None),
            bypass_bit=_int(bypass, 0x40) if bypass is not None else 0,
            bypass_when_set=bool(b.get("bypass_when_set", True)),
            models=models,
            model_mask=_int(b.get("model_mask"), None),
            alt_bit=alt_bit,
            cc_switch=_int(b.get("cc_switch"), None),
            cc_params=[_int(c, None) for c in cc_params] if isinstance(cc_params, list) else [],
            notes=str(b.get("notes", "")),
        )

    @classmethod
    def load(cls, path: Optional[str | Path] = None) -> "Layout":
        path = Path(path) if path else DEFAULT_LAYOUT
        try:
            with open(path, encoding="utf-8") as f:
                return cls.from_dict(json.load(f), source=str(path))
        except (OSError, json.JSONDecodeError) as e:
            log.error("cannot load layout %s: %s - using empty layout", path, e)
            return cls(source=f"(empty: {e})")

    # -- scene access --
    def scene_name(self, scene: bytes) -> str:
        if self.name_offset is None:
            return ""
        raw = bytes(scene[self.name_offset:self.name_offset + self.name_length])
        return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()

    def scene_tempo(self, scene: bytes) -> Optional[int]:
        if self.tempo_offset is None or self.tempo_offset >= len(scene):
            return None
        hi = 0
        if self.tempo_hi_offset is not None and self.tempo_hi_offset < len(scene):
            hi = scene[self.tempo_hi_offset]
        return (hi << 7) | scene[self.tempo_offset]

    def set_scene_tempo(self, scene: bytearray, bpm: int) -> None:
        if self.tempo_offset is None or self.tempo_offset >= len(scene):
            return
        if self.tempo_hi_offset is not None and self.tempo_hi_offset < len(scene):
            scene[self.tempo_hi_offset] = (bpm >> 7) & 0x7F
            scene[self.tempo_offset] = bpm & 0x7F
        else:
            scene[self.tempo_offset] = bpm & 0xFF

    def scene_block_order(self, scene: bytes) -> list[int]:
        if self.order_offset is None:
            return [b.chain_id for b in self.blocks if b.chain_id is not None]
        return list(scene[self.order_offset:self.order_offset + self.order_length])

    def block_by_chain_id(self, chain_id: int) -> BlockSpec:
        for b in self.blocks:
            if b.chain_id == chain_id:
                return b
        return BlockSpec(id=f"#{chain_id}", chain_id=chain_id, known=False)

    def block_by_id(self, block_id: str) -> Optional[BlockSpec]:
        return next((b for b in self.blocks if b.id == block_id), None)

    def chain(self, order: list[int]) -> list[BlockSpec]:
        """Blocks in signal-chain order; 0 entries (empty slots) are skipped."""
        return [self.block_by_chain_id(c) for c in order if c]
