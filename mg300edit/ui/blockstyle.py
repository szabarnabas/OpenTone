"""Block-type look: CSS colour class + Hungarian description (pure, no GTK).

The colours themselves live in style.css (`.blk-*` set the `--blk` variable),
so light/dark tweaks stay in one place."""

from __future__ import annotations

# block id -> (css class, Hungarian description shown next to the abbreviation)
BLOCK_STYLE: dict[str, tuple[str, str]] = {
    "NR": ("blk-nr", "Zajzár"),
    "CMP": ("blk-cmp", "Kompresszor"),
    "EFX": ("blk-efx", "Effekt / torzító"),
    "AMP": ("blk-amp", "Erősítő"),
    "CAB": ("blk-cab", "Hangláda"),
    "EQ": ("blk-eq", "Hangszínszabályzó"),
    "MOD": ("blk-mod", "Moduláció"),
    "DLY": ("blk-dly", "Késleltetés"),
    "RVB": ("blk-rvb", "Zengető"),
    "PL": ("blk-pl", "Patch-hangerő"),
    "WAH": ("blk-wah", "Wah"),
}
ALL_CLASSES = tuple(sorted({c for c, _ in BLOCK_STYLE.values()} | {"blk-unknown"}))


def block_css_class(block_id: str) -> str:
    return BLOCK_STYLE.get(block_id, ("blk-unknown", ""))[0]


def block_description(block_id: str) -> str:
    return BLOCK_STYLE.get(block_id, ("", "Ismeretlen blokk"))[1]


def apply_block_class(widget, block_id: str) -> None:
    """Swap the widget's `.blk-*` class to the one for block_id."""
    for c in ALL_CLASSES:
        widget.remove_css_class(c)
    widget.add_css_class(block_css_class(block_id))
