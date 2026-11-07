"""Signal chain row: one compact "pedal" card per block in block-order order.

Card: colour accent per block type, abbreviation, model name, LED on/off.
Click / Enter selects, the LED / Space toggles bypass, drag & drop (or
Ctrl+←/→ on a focused card) reorders via `ctl.set_block_order(new_order)`
when the controller offers it."""

from __future__ import annotations

import logging
from typing import Callable, Optional

from gi.repository import Gdk, GObject, Graphene, Gtk

from ..layout import BlockSpec
from .blockstyle import apply_block_class, block_description

log = logging.getLogger(__name__)


def reorder_chain(order: list[int], moving: int, insert_at: int) -> list[int]:
    """Move chain id `moving` so it lands before the block that is at position
    `insert_at` (0..n, counted among the non-zero entries of `order`, before the
    move). Empty slots (0) keep their positions. Returns a new list."""
    ids = [c for c in order if c]
    if moving not in ids:
        return list(order)
    src = ids.index(moving)
    insert_at = max(0, min(len(ids), insert_at))
    ids.pop(src)
    if insert_at > src:
        insert_at -= 1
    ids.insert(insert_at, moving)
    it = iter(ids)
    return [next(it) if c else 0 for c in order]


class Connector(Gtk.Widget):
    """Signal line + arrowhead between cards; doubles as the drop indicator."""

    __gtype_name__ = "Mg300ChainConnector"

    def __init__(self, end: bool = False):
        super().__init__(css_classes=["chain-connector"] + (["end"] if end else []),
                         valign=Gtk.Align.FILL)
        self.end = end
        self.drop = False

    def set_drop(self, on: bool) -> None:
        if on != self.drop:
            self.drop = on
            (self.add_css_class if on else self.remove_css_class)("drop")
            self.queue_draw()

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            n = 6 if self.end else 14
            return n, n, -1, -1
        return 8, 8, -1, -1

    def do_snapshot(self, snapshot):
        w, h = self.get_width(), self.get_height()
        c = self.get_color()
        if self.drop:
            bar = Graphene.Rect().init(w / 2 - 2, 4, 4, max(0, h - 8))
            snapshot.push_rounded_clip(_rounded(bar, 2))
            snapshot.append_color(c, bar)
            snapshot.pop()
            return
        if self.end:
            return
        cr = snapshot.append_cairo(Graphene.Rect().init(0, 0, w, h))
        cr.set_source_rgba(c.red, c.green, c.blue, c.alpha)
        cy = round(h / 2) + 0.5
        cr.set_line_width(1.5)
        cr.set_line_cap(1)
        cr.set_line_join(1)
        cr.move_to(1.5, cy)
        cr.line_to(w - 4, cy)
        cr.stroke()
        cr.move_to(w - 8, cy - 4)
        cr.line_to(w - 3.5, cy)
        cr.line_to(w - 8, cy + 4)
        cr.stroke()


def _rounded(rect: Graphene.Rect, radius: float):
    from gi.repository import Gsk
    size = Graphene.Size().init(radius, radius)
    return Gsk.RoundedRect().init(rect, size, size, size, size)


class ChainCard(Gtk.Box):
    """One block in the chain. Focusable; Space toggles, Enter selects."""

    __gtype_name__ = "Mg300ChainCard"

    def __init__(self, row: "ChainRow", block: BlockSpec, movable: bool):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=1, focusable=True,
                         accessible_role=Gtk.AccessibleRole.BUTTON,
                         css_classes=["chain-card"], valign=Gtk.Align.CENTER)
        self.row = row
        self.block = block
        self.movable = movable
        self.bypassed: Optional[bool] = None
        self.model_name = ""
        apply_block_class(self, block.id if block.known else "")
        if not block.known:
            self.add_css_class("unknown")

        top = Gtk.Box(spacing=4)
        self.abbr = Gtk.Label(label=block.title, css_classes=["chain-abbr"], xalign=0,
                              hexpand=True)
        top.append(self.abbr)
        self.led = Gtk.Button(css_classes=["led-btn", "flat"], focusable=False,
                              focus_on_click=False, valign=Gtk.Align.CENTER)
        self.led.set_child(Gtk.Box(css_classes=["led"]))
        self.led.update_property([Gtk.AccessibleProperty.LABEL], [f"{block.title} ki/be"])
        self.led.connect("clicked", lambda *_: self.toggle())
        top.append(self.led)
        self.append(top)
        self.model_label = Gtk.Label(label="", css_classes=["chain-model"], xalign=0,
                                     ellipsize=3, max_width_chars=1, hexpand=True)
        self.append(self.model_label)
        self.dev_label = Gtk.Label(label=f"#{block.chain_id}", css_classes=["dev-info"],
                                   xalign=0, visible=False)
        self.append(self.dev_label)

        click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        click.connect("released", self._clicked)
        self.add_controller(click)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        if movable:
            src = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
            src.connect("prepare", self._drag_prepare)
            src.connect("drag-begin", self._drag_begin)
            src.connect("drag-end", self._drag_end)
            self.add_controller(src)

    # ---- state ----
    def update(self, scene: Optional[bytes], dev: bool) -> None:
        model = self.block.current_model(scene) if scene is not None else None
        self.model_name = model.name if model else "–"
        self.model_label.set_label(self.model_name)
        self.bypassed = self.block.is_bypassed(scene) if scene is not None else None
        self.led.set_sensitive(self.bypassed is not None)
        on = self.bypassed is False
        (self.led.add_css_class if on else self.led.remove_css_class)("on")
        (self.add_css_class if self.bypassed else self.remove_css_class)("bypassed")
        self.dev_label.set_visible(dev)
        state = "nincs adat" if self.bypassed is None else ("bekapcsolva" if on else "kikapcsolva")
        desc = block_description(self.block.id)
        tip = [f"{self.block.title} – {desc}" if desc else self.block.title,
               f"{self.model_name} · {state}",
               "Kattintás/Enter: szerkesztés · LED/Szóköz: ki/be"]
        if self.movable:
            tip.append("Húzás vagy Ctrl+←/→: áthelyezés a láncban")
        if dev:
            tip.append(f"chain id {self.block.chain_id} · model byte @ {self.block.model_offset}")
        self.set_tooltip_text("\n".join(tip))
        self.led.set_tooltip_text("Kikapcsolás" if on else "Bekapcsolás")
        self.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
                             [f"{self.block.title} {self.model_name}", state])

    def set_selected(self, selected: bool) -> None:
        (self.add_css_class if selected else self.remove_css_class)("selected")

    def toggle(self) -> None:
        if self.bypassed is None or self.row.guard:
            return
        self.row.toggle_block(self, not self.bypassed)

    # ---- input ----
    def _clicked(self, gesture, n_press, _x, _y):
        self.grab_focus()
        self.row.select_card(self)

    def _key(self, _ctrl, keyval, _code, state):
        if keyval in (Gdk.KEY_space, Gdk.KEY_KP_Space):
            self.toggle()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter):
            self.row.select_card(self)
            return True
        if state & Gdk.ModifierType.CONTROL_MASK and keyval in (Gdk.KEY_Left, Gdk.KEY_Right):
            if self.movable:
                self.row.move_card(self, -1 if keyval == Gdk.KEY_Left else 1)
            return True
        return False

    def _drag_prepare(self, _src, _x, _y):
        if self.row.guard:
            return None
        return Gdk.ContentProvider.new_for_value(str(self.block.chain_id))

    def _drag_begin(self, src, _drag):
        src.set_icon(Gtk.WidgetPaintable.new(self), self.get_width() // 2, self.get_height() // 2)
        self.add_css_class("dragging")

    def _drag_end(self, *_):
        self.remove_css_class("dragging")
        self.row.clear_drop()


class ChainRow(Gtk.ScrolledWindow):
    def __init__(self, guard, on_select: Callable[[BlockSpec], None],
                 on_toggle: Callable[[BlockSpec, bool], None], ctl=None):
        super().__init__(vscrollbar_policy=Gtk.PolicyType.NEVER,
                         hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                         css_classes=["chain-row"])
        self.guard = guard
        self.on_select = on_select
        self.on_toggle = on_toggle
        self._ctl = ctl
        self.dev = False
        self.box = Gtk.Box(spacing=0, halign=Gtk.Align.CENTER, css_classes=["chain-box"])
        self.set_child(self.box)
        self.cards: list[ChainCard] = []
        self.gaps: list[Connector] = []      # len(cards)+1 drop positions
        self._blocks: list[BlockSpec] = []
        self._chain_ids: Optional[list] = None
        self._scene: Optional[bytes] = None
        self.selected_id: Optional[str] = None
        self._drop_at: Optional[int] = None

        drop = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop.connect("motion", self._drop_motion)
        drop.connect("leave", lambda *_: self.clear_drop())
        drop.connect("drop", self._drop)
        self.box.add_controller(drop)
        self._rebuild([])

    # ---- API used by the window ----
    @property
    def ctl(self):
        return self._ctl if self._ctl is not None else getattr(self.get_root(), "ctl", None)

    def set_developer_mode(self, enabled: bool) -> None:
        self.dev = bool(enabled)
        with self.guard:
            for c in self.cards:
                c.update(self._scene, self.dev)

    def set_chain(self, blocks: list[BlockSpec], scene: Optional[bytes], selected_id: Optional[str]):
        ids = [b.chain_id for b in blocks]
        self._scene = scene
        if ids != self._chain_ids:
            self._rebuild(blocks)
            self._chain_ids = ids
        self.selected_id = selected_id
        with self.guard:
            for c in self.cards:
                c.update(scene, self.dev)
                c.set_selected(c.block.id == selected_id)

    def refresh_bypass(self, block_id: str) -> None:
        """Re-read one card's on/off state from the scene (after a toggle elsewhere)."""
        with self.guard:
            for c in self.cards:
                if c.block.id == block_id:
                    c.update(self._scene, self.dev)

    # ---- card callbacks ----
    def select_card(self, card: ChainCard) -> None:
        if self.guard:
            return
        self.selected_id = card.block.id
        for c in self.cards:
            c.set_selected(c is card)
        self.on_select(card.block)

    def toggle_block(self, card: ChainCard, bypassed: bool) -> None:
        self.on_toggle(card.block, bypassed)
        if self._scene is not None and card.block.is_bypassed(self._scene) is not None:
            card.update(self._scene, self.dev)       # controller updated the shared scene
        else:                                         # no scene feedback: show the request
            card.bypassed = bypassed
            card.update(None, self.dev)

    def move_card(self, card: ChainCard, delta: int) -> None:
        i = self.cards.index(card)
        j = i + delta
        if 0 <= j < len(self.cards):
            self._reorder(card.block.chain_id, j if delta < 0 else j + 1, refocus=True)

    # ---- drag & drop ----
    def _insert_index(self, x: float) -> int:
        for i, c in enumerate(self.cards):
            ok, rect = c.compute_bounds(self.box)
            if ok and x < rect.get_x() + rect.get_width() / 2:
                return i
        return len(self.cards)

    def _drop_motion(self, target, x, _y):
        moving = self._dragged_index(target)
        at = self._insert_index(x)
        if moving is not None and at in (moving, moving + 1):
            at = None                   # dropping here would not change anything
        self._show_drop(at)
        return Gdk.DragAction.MOVE

    def _dragged_index(self, target) -> Optional[int]:
        drag = target.get_current_drop()
        src = drag.get_drag() if drag is not None else None
        if src is None:
            return None
        for i, c in enumerate(self.cards):
            if c.has_css_class("dragging"):
                return i
        return None

    def _show_drop(self, at: Optional[int]) -> None:
        self._drop_at = at
        for i, g in enumerate(self.gaps):
            g.set_drop(i == at)

    def clear_drop(self) -> None:
        self._show_drop(None)

    def _drop(self, _target, value, x, _y) -> bool:
        at = self._insert_index(x)
        self.clear_drop()
        try:
            chain_id = int(value)
        except (TypeError, ValueError):
            return False
        return self._reorder(chain_id, at)

    def _reorder(self, chain_id: int, insert_at: int, refocus: bool = False) -> bool:
        if self.guard:
            return False
        ctl = self.ctl
        order = list(getattr(ctl, "block_order", None) or [b.chain_id for b in self._blocks])
        new_order = reorder_chain(order, chain_id, insert_at)
        if new_order == order:
            return False
        setter = getattr(ctl, "set_block_order", None)
        if setter is None:
            log.info("block reorder requested %s, but the controller has no set_block_order", new_order)
            return False
        setter(new_order)
        by_id = {b.chain_id: b for b in self._blocks}
        blocks = [by_id[c] for c in new_order if c in by_id]
        if [b.chain_id for b in blocks] != self._chain_ids:
            self._rebuild(blocks)
            self._chain_ids = [b.chain_id for b in blocks]
            with self.guard:
                for c in self.cards:
                    c.update(self._scene, self.dev)
                    c.set_selected(c.block.id == self.selected_id)
        if refocus:
            for c in self.cards:
                if c.block.chain_id == chain_id:
                    c.grab_focus()
        return True

    # ---- building ----
    def _rebuild(self, blocks: list[BlockSpec]):
        while (child := self.box.get_first_child()) is not None:
            self.box.remove(child)
        self._blocks = list(blocks)
        self.cards, self.gaps = [], []
        if not blocks:
            self.box.append(Gtk.Label(label="Nincs beolvasott blokklánc", css_classes=["dim-label", "chain-empty"]))
            return
        ctl = self.ctl
        order = getattr(ctl, "block_order", None) or [b.chain_id for b in blocks]
        for i, b in enumerate(blocks):
            gap = Connector(end=(i == 0))
            self.gaps.append(gap)
            self.box.append(gap)
            card = ChainCard(self, b, movable=b.chain_id in order and b.chain_id != 0)
            self.cards.append(card)
            self.box.append(card)
        tail = Connector(end=True)
        self.gaps.append(tail)
        self.box.append(tail)
