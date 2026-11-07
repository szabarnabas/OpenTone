"""Scene -> live CC list: apply a stored/imported scene to the edit buffer
without any SysEx write (the preset write protocol is still unknown).

Pure functions only (no I/O). Uses the layout's verified CC map:
  block.cc_switch       full model byte (index | alt bit | bypass bit)
  block.cc_params[slot] knob slot `slot` of whatever model is loaded
Order: every model/switch CC first (the pedal loads the model's defaults on a
model change), then the parameter slots of each block's *target* model.

What can NOT be applied by CC is reported, not sent: name, block order, tempo,
blocks without a switch CC (WAH), unknown models, values > 127 (e.g. the
non-linear hidden DLY slot), and bytes no known parameter covers ("hidden").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .layout import BlockSpec, Layout

# Changing the amp makes the pedal auto-pick a matching cab: always (re)send
# the cab after an amp model change so the imported cab wins.
LINKED_BLOCKS = {"AMP": ("CAB",)}

SKIP_NO_CC = "no_cc"                 # block / slot has no CC
SKIP_UNKNOWN_MODEL = "unknown_model"
SKIP_RANGE = "range"                 # value > 127, not representable as CC
SKIP_NAME = "name"
SKIP_ORDER = "block_order"
SKIP_TEMPO = "tempo"
SKIP_HIDDEN = "hidden"               # differing bytes not covered by a known param


@dataclass
class CCStep:
    cc: int
    value: int
    offset: int          # scene byte this CC writes
    block: str
    what: str            # "model" or the parameter name


@dataclass
class Skip:
    kind: str
    block: str = ""
    detail: str = ""


@dataclass
class ApplyPlan:
    steps: list[CCStep] = field(default_factory=list)
    skipped: list[Skip] = field(default_factory=list)

    @property
    def ccs(self) -> list[tuple[int, int]]:
        return [(s.cc, s.value) for s in self.steps]

    @property
    def model_steps(self) -> list[CCStep]:
        return [s for s in self.steps if s.what == "model"]

    @property
    def param_steps(self) -> list[CCStep]:
        return [s for s in self.steps if s.what != "model"]

    def skipped_kinds(self) -> set[str]:
        return {s.kind for s in self.skipped}


def _cc_for_slot(block: BlockSpec, slot: Optional[int]) -> Optional[int]:
    if slot is None or not block.cc_params or not 0 <= slot < len(block.cc_params):
        return None
    return block.cc_params[slot]


def plan_scene(layout: Layout, target: bytes, current: Optional[bytes] = None) -> ApplyPlan:
    """Build the CC list that turns `current` (the live edit buffer, or None =
    unknown -> send everything) into `target` as far as CCs can."""
    size = layout.scene_size
    if len(target) != size:
        raise ValueError(f"scene must be {size} bytes, got {len(target)}")
    if current is not None and len(current) != size:
        current = None
    plan = ApplyPlan()
    covered: set[int] = set()

    # ---- phase 1: model / switch bytes ----
    model_changed: set[str] = set()
    for b in layout.blocks:
        if b.model_offset is None or b.model_offset >= size:
            continue
        covered.add(b.model_offset)
        tb = target[b.model_offset]
        if current is None:
            byte_diff, model_diff = True, True
        else:
            cb = current[b.model_offset]
            byte_diff = cb != tb
            model_diff = (cb & b.value_mask) != (tb & b.value_mask)
        if model_diff:
            model_changed.add(b.id)
        if not byte_diff:
            continue
        if b.cc_switch is None:
            plan.skipped.append(Skip(SKIP_NO_CC, b.id, "model"))
        elif tb > 127:
            plan.skipped.append(Skip(SKIP_RANGE, b.id, "model"))
        else:
            plan.steps.append(CCStep(b.cc_switch, tb, b.model_offset, b.id, "model"))
    # linked blocks (amp -> cab): force-send the linked block's switch + params
    if current is not None:
        for src, linked in LINKED_BLOCKS.items():
            if src not in model_changed:
                continue
            for lid in linked:
                lb = layout.block_by_id(lid)
                if lb is None or lb.model_offset is None or lb.cc_switch is None:
                    continue
                model_changed.add(lid)
                tb = target[lb.model_offset]
                if tb <= 127 and not any(s.block == lid and s.what == "model" for s in plan.steps):
                    plan.steps.append(CCStep(lb.cc_switch, tb, lb.model_offset, lid, "model"))

    # ---- phase 2: parameter slots of each block's target model ----
    for b in layout.blocks:
        if b.model_offset is None or b.model_offset >= size:
            continue
        model = b.model(b.model_index(target))
        if model is None or not model.known:
            plan.skipped.append(Skip(SKIP_UNKNOWN_MODEL, b.id, model.name if model else ""))
            continue
        for p in model.params:
            if p.offset is None or p.offset >= size:
                continue
            covered.add(p.offset)
            v = target[p.offset]
            if current is not None and b.id not in model_changed and current[p.offset] == v:
                continue
            cc = _cc_for_slot(b, p.slot)
            if cc is None:
                plan.skipped.append(Skip(SKIP_NO_CC, b.id, p.name))
            elif v > 127:
                plan.skipped.append(Skip(SKIP_RANGE, b.id, p.name))
            else:
                plan.steps.append(CCStep(cc, v, p.offset, b.id, p.name))

    # ---- not representable by CC ----
    meta: set[int] = set()
    if layout.name_offset is not None:
        rng = range(layout.name_offset, min(size, layout.name_offset + layout.name_length))
        meta.update(rng)
        if current is None or any(current[i] != target[i] for i in rng):
            if current is not None or layout.scene_name(target):
                plan.skipped.append(Skip(SKIP_NAME, detail=layout.scene_name(target)))
    if layout.order_offset is not None:
        rng = range(layout.order_offset, min(size, layout.order_offset + layout.order_length))
        meta.update(rng)
        if current is not None and any(current[i] != target[i] for i in rng):
            plan.skipped.append(Skip(SKIP_ORDER))
    for off in (layout.tempo_offset, layout.tempo_hi_offset):
        if off is not None and off < size:
            meta.add(off)
    if current is not None and layout.scene_tempo(current) != layout.scene_tempo(target):
        plan.skipped.append(Skip(SKIP_TEMPO, detail=str(layout.scene_tempo(target))))
    if current is not None:
        hidden = [i for i in range(size)
                  if i not in covered and i not in meta and current[i] != target[i]]
        if hidden:
            plan.skipped.append(Skip(SKIP_HIDDEN, detail=str(len(hidden))))
    return plan


def summarize(plan: ApplyPlan) -> str:
    """Short Hungarian summary for a toast."""
    n_models, n_params = len(plan.model_steps), len(plan.param_steps)
    parts = [f"Alkalmazva: {n_models} modell/kapcsoló, {n_params} paraméter"]
    sk = []
    kinds = [s.kind for s in plan.skipped]
    if SKIP_NAME in kinds:
        sk.append("név")
    if SKIP_ORDER in kinds:
        sk.append("blokksorrend")
    if SKIP_TEMPO in kinds:
        sk.append("tempó")
    no_cc = sorted({s.block for s in plan.skipped if s.kind == SKIP_NO_CC})
    if no_cc:
        sk.append("nincs CC: " + ", ".join(no_cc))
    unk = sorted({s.block for s in plan.skipped if s.kind == SKIP_UNKNOWN_MODEL})
    if unk:
        sk.append("ismeretlen modell: " + ", ".join(unk))
    rng = sum(1 for k in kinds if k == SKIP_RANGE)
    if rng:
        sk.append(f"{rng} érték >127")
    hidden = next((s for s in plan.skipped if s.kind == SKIP_HIDDEN), None)
    if hidden:
        sk.append(f"{hidden.detail} rejtett bájt")
    if sk:
        parts.append("Kihagyva: " + "; ".join(sk))
    return " · ".join(parts)
