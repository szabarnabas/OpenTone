"""Save As / Rename / Import-to-slot dialog: target user slot + name, then an
explicit overwrite confirmation ("Felülírod: 3B LeoGib CleanPush?").

Only user presets (PC 0-35, 1A..9D) are offered - factory presets can never
be chosen as a target from the UI."""

from __future__ import annotations

from typing import Callable, Optional

from gi.repository import Adw, GLib, Gtk

from .. import presets as PR
from ..fileformat import PresetData
from .import_dialog import preset_label


def slot_text(preset: int, names: dict) -> str:
    name = names.get(preset)
    return f"{PR.slot_label(preset)}  {name}" if name else PR.slot_label(preset)


def overwrite_question(preset: int, names: dict) -> str:
    old = names.get(preset) or "(ismeretlen név)"
    return f"Felülírod: {PR.slot_label(preset)} {old}?"


def confirm_overwrite(parent: Gtk.Widget, preset: int, names: dict, new_name: Optional[str],
                      on_yes: Callable[[], None], extra: str = "") -> None:
    body = "A pedálon tárolt preset mindhárom jelenete felülíródik."
    if new_name is not None:
        body += f"\nÚj név: {new_name or '(üres)'}"
    if extra:
        body += f"\n{extra}"
    body += "\nA régi tartalomról előtte biztonsági mentés készül."
    dlg = Adw.AlertDialog(heading=overwrite_question(preset, names), body=body)
    dlg.add_response("cancel", "Mégse")
    dlg.add_response("save", "Felülírás")
    dlg.set_response_appearance("save", Adw.ResponseAppearance.DESTRUCTIVE)
    dlg.set_default_response("cancel")
    dlg.set_close_response("cancel")
    dlg.connect("response", lambda _d, r: r == "save" and on_yes())
    dlg.present(parent)


class SaveAsDialog(Adw.AlertDialog):
    """on_confirm(target, name, item) runs after the overwrite confirmation.
    `items` (import to slot): presets from a file to choose from; the name
    entry follows the chosen one."""

    def __init__(self, names: dict, *, target: Optional[int], name: str,
                 on_confirm: Callable[[int, str, Optional[PresetData]], None],
                 heading: str = "Mentés másként", fixed_target: bool = False,
                 items: Optional[list[PresetData]] = None, body: str = ""):
        super().__init__(heading=heading, body=body)
        self.names = dict(names)
        self.on_confirm = on_confirm
        self.items = items or []
        self.slots = list(PR.USER_PRESETS)

        group = Adw.PreferencesGroup()
        self.item_row = None
        if len(self.items) > 1:
            self.item_row = Adw.ComboRow(title="Preset a fájlból")
            self.item_row.set_model(Gtk.StringList.new([preset_label(p) for p in self.items]))
            self.item_row.connect("notify::selected", self._item_changed)
            group.add(self.item_row)
        self.target_row = Adw.ComboRow(title="Cél (saját preset)")
        self.target_row.set_model(Gtk.StringList.new([slot_text(p, self.names) for p in self.slots]))
        self.target_row.set_enable_search(True)
        if target is not None and target in self.slots:
            self.target_row.set_selected(self.slots.index(target))
        self.target_row.set_sensitive(not fixed_target)
        group.add(self.target_row)
        self.name_row = Adw.EntryRow(title=f"Név (max. {PR.NAME_LEN} karakter)")
        self.name_row.set_max_length(PR.NAME_LEN)
        self.name_row.set_text(PR.sanitize_name(name))
        self.name_row.connect("changed", self._name_changed)
        group.add(self.name_row)
        self.hint = Gtk.Label(xalign=0, wrap=True, css_classes=["dim-label", "caption"],
                              margin_top=6, visible=False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(group)
        box.append(self.hint)
        self.set_extra_child(box)

        self.add_response("cancel", "Mégse")
        self.add_response("save", "Mentés…")
        self.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        self.set_default_response("save")
        self.set_close_response("cancel")
        self.connect("response", self._response)
        self._name_changed(self.name_row)

    # ---- state ----
    def target(self) -> int:
        i = self.target_row.get_selected()
        return self.slots[i if 0 <= i < len(self.slots) else 0]

    def item(self) -> Optional[PresetData]:
        if not self.items:
            return None
        i = self.item_row.get_selected() if self.item_row else 0
        return self.items[i if 0 <= i < len(self.items) else 0]

    def _item_changed(self, *_):
        it = self.item()
        if it is not None:
            self.name_row.set_text(PR.sanitize_name(it.name))

    def _name_changed(self, row):
        text = row.get_text()
        clean = PR.sanitize_name(text)
        if clean != text:
            self.hint.set_label(f"A pedálon így jelenik meg: „{clean}” (csak ASCII karakterek)")
        self.hint.set_visible(clean != text)
        self.set_response_enabled("save", bool(clean.strip()))

    def _response(self, _dlg, response: str):
        if response != "save":
            return
        target, name, item = self.target(), PR.sanitize_name(self.name_row.get_text()), self.item()

        def ask():
            # this dialog is closing: confirm on the window it was presented on
            confirm_overwrite(self._parent, target, self.names, name,
                              lambda: self.on_confirm(target, name, item))
            return GLib.SOURCE_REMOVE
        GLib.idle_add(ask)

    _parent = None

    def present(self, parent):        # remember the parent for the follow-up confirmation
        self._parent = parent
        super().present(parent)

