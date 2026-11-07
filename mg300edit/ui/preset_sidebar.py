"""Preset library sidebar: search entry + list of the 72 presets in two groups
("Saját (User)" PC 0-35, "Gyári (Factory)" PC 36-71), rows "3B  Name".
Clicking a row selects that preset (Program Change via the callback)."""

from __future__ import annotations

from typing import Callable, Optional

from gi.repository import GLib, Gtk

from .. import presets as PR

GROUP_TITLES = {PR.BANK_USER: "Saját (User)", PR.BANK_FACTORY: "Gyári (Factory)"}
GROUP_WORDS = {PR.BANK_USER: "saját user", PR.BANK_FACTORY: "gyári factory"}


class PresetRow(Gtk.ListBoxRow):
    def __init__(self, preset: int):
        super().__init__()
        self.preset = preset
        self.bank = PR.bank(preset)
        self.slot = PR.slot_label(preset)
        self.name = ""
        box = Gtk.Box(spacing=10, margin_start=4, margin_end=4)
        self.slot_label = Gtk.Label(label=self.slot, xalign=0, width_chars=3,
                                    css_classes=["preset-slot", "dim-label", "monospace"])
        self.name_label = Gtk.Label(label="…", xalign=0, hexpand=True, ellipsize=3,
                                    css_classes=["preset-row-name"])
        self.dot = Gtk.Label(label="•", visible=False, css_classes=["preset-modified-dot"],
                             tooltip_text="Módosítva (nincs mentve)")
        box.append(self.slot_label)
        box.append(self.name_label)
        box.append(self.dot)
        self.set_child(box)
        self.set_tooltip_text(f"{GROUP_TITLES[self.bank]} {self.slot} · PC {preset}")

    def set_name(self, name: str) -> None:
        self.name = name
        self.name_label.set_label(name or "—")

    def set_modified(self, modified: bool) -> None:
        self.dot.set_visible(modified)

    def matches(self, query: str) -> bool:
        if not query:
            return True
        hay = f"{self.slot} {self.name} {GROUP_WORDS[self.bank]} pc{self.preset}".lower()
        return all(w in hay for w in query.lower().split())


class PresetSidebar(Gtk.Box):
    def __init__(self, on_select: Callable[[int], None]):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["preset-sidebar"])
        self.on_select = on_select
        self._guard = False
        self.current: Optional[int] = None
        self.modified = False

        self.search = Gtk.SearchEntry(placeholder_text="Keresés (név, 3B…)",
                                      margin_start=8, margin_end=8, margin_top=8, margin_bottom=6)
        self.search.connect("search-changed", lambda *_: self._refilter())
        self.search.connect("stop-search", lambda e: e.set_text(""))
        self.append(self.search)

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE,
                                css_classes=["navigation-sidebar"])
        self.list.set_header_func(self._header)
        self.list.set_filter_func(lambda row: row.matches(self.search.get_text()))
        self.list.set_placeholder(Gtk.Label(label="Nincs találat", margin_top=24,
                                            css_classes=["dim-label"]))
        self.list.connect("row-activated", self._activated)
        self.rows: list[PresetRow] = []
        for p in range(PR.PRESET_COUNT):
            row = PresetRow(p)
            self.rows.append(row)
            self.list.append(row)

        self.scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.scroller.set_child(self.list)
        self.append(self.scroller)

    # ---- data in ----
    def set_name(self, preset: int, name: str) -> None:
        if 0 <= preset < len(self.rows):
            self.rows[preset].set_name(name)
            if self.search.get_text():
                self.rows[preset].changed()

    def set_names(self, names: dict) -> None:
        for p, n in names.items():
            if 0 <= p < len(self.rows):
                self.rows[p].set_name(n)
        self._refilter()

    def set_modified(self, modified: bool) -> None:
        """Dot on the current preset's row while the edit buffer is unsaved."""
        self.modified = modified
        for r in self.rows:
            r.set_modified(modified and r.preset == self.current)

    def set_current(self, preset: Optional[int]) -> None:
        """Highlight the pedal's current preset (never triggers on_select)."""
        if self.current is not None and 0 <= self.current < len(self.rows):
            self.rows[self.current].remove_css_class("current")
            self.rows[self.current].set_modified(False)
        self.current = preset
        if preset is not None and 0 <= preset < len(self.rows):
            self.rows[preset].set_modified(self.modified)
        self._guard = True
        try:
            if preset is None or not 0 <= preset < len(self.rows):
                self.list.unselect_all()
                return
            row = self.rows[preset]
            row.add_css_class("current")
            self.list.select_row(row)
        finally:
            self._guard = False
        GLib.idle_add(self._scroll_to_current)

    # ---- internals ----
    def _refilter(self) -> None:
        self.list.invalidate_filter()
        self.list.invalidate_headers()

    def _header(self, row: PresetRow, before: Optional[PresetRow]) -> None:
        if before is None or before.bank != row.bank:
            row.set_header(Gtk.Label(label=GROUP_TITLES[row.bank], xalign=0,
                                     css_classes=["heading", "preset-group"],
                                     margin_start=12, margin_top=10 if before else 4, margin_bottom=4))
        else:
            row.set_header(None)

    def _activated(self, _list, row: PresetRow) -> None:
        if not self._guard:
            self.on_select(row.preset)

    def _scroll_to_current(self) -> bool:
        if self.current is None:
            return GLib.SOURCE_REMOVE
        row = self.rows[self.current]
        if not row.get_mapped():
            return GLib.SOURCE_REMOVE
        ok, bounds = row.compute_bounds(self.list)
        if ok:
            adj = self.scroller.get_vadjustment()
            y, h = bounds.get_y(), bounds.get_height()
            top, page = adj.get_value(), adj.get_page_size()
            if y < top or y + h > top + page:
                adj.set_value(max(0, y - page / 3))
        return GLib.SOURCE_REMOVE
