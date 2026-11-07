"""Chooser shown when an imported file holds more than one applicable scene:
pick the preset (bundles) and the source (edit buffer / scene 1-3)."""

from __future__ import annotations

from typing import Callable, Optional

from gi.repository import Adw, Gtk

from .. import presets as PR
from ..fileformat import PresetData


def source_label(key: str) -> str:
    if key == "edit":
        return "Szerkesztett állapot"
    if key.startswith("scene") and key[5:].isdigit():
        return f"{int(key[5:]) + 1}. jelenet"
    return key


def preset_label(p: PresetData) -> str:
    slot = ""
    if p.preset_index is not None and PR.is_valid(p.preset_index):
        slot = PR.full_label(p.preset_index, user="Saját", factory="Gyári") + "  "
    return f"{slot}{p.name or '(névtelen)'}"


class ImportDialog(Adw.AlertDialog):
    def __init__(self, filename: str, items: list[PresetData],
                 on_apply: Callable[[PresetData, str, bytes], None],
                 preferred_scene: Optional[int] = None):
        super().__init__(heading="Preset importálása",
                         body=f"{filename}\nA kiválasztott jelenet CC-üzenetekkel kerül a "
                              "szerkesztett állapotba (a pedálra nem ment).")
        self.items = items
        self.on_apply = on_apply
        self.preferred_scene = preferred_scene
        self._keys: list[str] = []

        group = Adw.PreferencesGroup()
        self.preset_row = Adw.ComboRow(title="Preset")
        self.preset_row.set_model(Gtk.StringList.new([preset_label(p) for p in items]))
        self.preset_row.set_visible(len(items) > 1)
        self.preset_row.connect("notify::selected", lambda *_: self._fill_sources())
        self.source_row = Adw.ComboRow(title="Forrás")
        group.add(self.preset_row)
        group.add(self.source_row)
        self.set_extra_child(group)

        self.add_response("cancel", "Mégse")
        self.add_response("apply", "Alkalmazás")
        self.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)
        self.set_default_response("apply")
        self.set_close_response("cancel")
        self.connect("response", self._response)
        self._fill_sources()

    def _item(self) -> PresetData:
        i = self.preset_row.get_selected()
        return self.items[i if 0 <= i < len(self.items) else 0]

    def _fill_sources(self) -> None:
        srcs = self._item().sources()
        self._keys = [k for k, _ in srcs]
        self.source_row.set_model(Gtk.StringList.new([source_label(k) for k in self._keys]))
        want = f"scene{self.preferred_scene}" if self.preferred_scene is not None else None
        if "edit" not in self._keys and want in self._keys:
            self.source_row.set_selected(self._keys.index(want))
        self.set_response_enabled("apply", bool(self._keys))

    def _response(self, _dlg, response: str) -> None:
        if response != "apply":
            return
        item = self._item()
        i = self.source_row.get_selected()
        srcs = item.sources()
        if 0 <= i < len(srcs):
            key, data = srcs[i]
            self.on_apply(item, key, data)
