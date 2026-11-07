"""Save-to-pedal UI: Mentés (Ctrl+S), Mentés másként (Ctrl+Shift+S), Átnevezés,
Scene másolása, Importálás slotba - plus progress / result toasts.

Window actions registered here:
  win.save          save the edit buffer to the current preset; on a factory
                    preset it opens Save As instead (factory is never written)
  win.save-as       target user slot + name, overwrite confirmation
  win.rename        Save As to the current slot with a new name
  win.copy-scene    copy one scene of the edit buffer over another and save
All writes go through Controller.save_preset() (backup + readback verify)."""

from __future__ import annotations

import logging
from typing import Callable, Optional

from gi.repository import Adw, Gio, GLib, Gtk

from .. import fileformat as F
from .. import presets as PR
from ..controller import Controller
from ..saving import SaveJob
from .save_dialog import SaveAsDialog

log = logging.getLogger(__name__)

KIND_TEXT = {"save": "Mentés", "save-as": "Mentés", "rename": "Átnevezés",
             "copy-scene": "Scene másolása", "import": "Importálás"}


class SaveActions:
    def __init__(self, win: Gtk.Window, ctl: Controller, toast: Callable[..., Adw.Toast],
                 note: Callable[[str], None]):
        self.win = win
        self.ctl = ctl
        self.toast = toast
        self.note = note
        self._progress: Optional[Adw.Toast] = None
        self._then: Optional[Callable[[], None]] = None

        for name, cb in (("save", self.save), ("save-as", self.save_as), ("rename", self.rename),
                         ("copy-scene", self.copy_scene)):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda *_a, cb=cb: cb())
            win.add_action(a)
        ctl.connect("save", self._ev_save)
        for ev in ("preset", "status", "scene"):
            ctl.connect(ev, lambda *_: self.update_sensitivity())
        self.update_sensitivity()

    # ------------------------------------------------------------ state --
    def _can_write(self) -> bool:
        return self.ctl.connected and self.ctl.live and not self.ctl.busy

    def _current_user(self) -> bool:
        return self.ctl.preset is not None and PR.is_user(self.ctl.preset)

    def update_sensitivity(self) -> None:
        loaded = self.ctl.scene is not None and self.ctl.preset is not None
        ok = self._can_write() and loaded
        for name, en in (("save", ok), ("save-as", ok), ("rename", ok and self._current_user()),
                         ("copy-scene", ok and self._current_user())):
            a = self.win.lookup_action(name)
            if a is not None:
                a.set_enabled(en)

    def _current_name(self) -> str:
        if self.ctl.scene is not None:
            return self.ctl.layout.scene_name(self.ctl.scene)
        return self.ctl.preset_names.get(self.ctl.preset, "") if self.ctl.preset is not None else ""

    def _start(self, fn: Callable[[], SaveJob], then: Optional[Callable[[], None]] = None) -> bool:
        try:
            job = fn()
        except (ValueError, RuntimeError) as e:
            self.toast(f"Mentés nem indítható: {e}")
            return False
        self._then = then
        self.update_sensitivity()
        self.note(f"save ({job.kind}) -> {PR.full_label(job.target)} started")
        return True

    # ---------------------------------------------------------- actions --
    def save(self, then: Optional[Callable[[], None]] = None) -> None:
        """Ctrl+S: save over the current preset - only user presets."""
        if self.ctl.preset is None:
            return
        if not self._current_user():
            self.toast("Gyári presetet nem lehet felülírni – válassz saját helyet")
            self.save_as(then=then)
            return
        self._start(lambda: self.ctl.save_preset(self.ctl.preset, kind="save"), then)

    def save_as(self, name: Optional[str] = None,
                then: Optional[Callable[[], None]] = None) -> None:
        if self.ctl.preset is None or self.ctl.scene is None:
            self.toast("Még nincs beolvasott preset")
            return
        target = self.ctl.preset if self._current_user() else self._first_free_guess()
        SaveAsDialog(self.ctl.preset_names, target=target,
                     name=name if name is not None else self._current_name(),
                     on_confirm=lambda t, n, _i: self._start(
                         lambda: self.ctl.save_preset(t, name=n, kind="save-as"), then),
                     body="A szerkesztett állapot mindhárom jelenete a kiválasztott "
                          "saját presetbe kerül.").present(self.win)

    def rename(self) -> None:
        if not self._current_user():
            self.toast("Csak saját preset nevezhető át")
            return
        SaveAsDialog(self.ctl.preset_names, target=self.ctl.preset, name=self._current_name(),
                     heading="Átnevezés", fixed_target=True,
                     body="Az átnevezés a szerkesztett állapotot is elmenti.",
                     on_confirm=lambda t, n, _i: self._start(
                         lambda: self.ctl.save_preset(t, name=n, kind="rename"))).present(self.win)

    def copy_scene(self) -> None:
        if not self._current_user():
            self.toast("Gyári presetben nem lehet jelenetet másolni (mentés kellene)")
            return
        cur = self.ctl.scene_index or 0
        dlg = Adw.AlertDialog(
            heading="Scene másolása",
            body=f"A(z) {PR.slot_label(self.ctl.preset)} {self._current_name()} preset "
                 "kiválasztott jelenetét egy másikra másolja, majd a presetet a pedálra MENTI "
                 "(mindhárom jelenet a jelenlegi szerkesztett állapottal).")
        group = Adw.PreferencesGroup()
        labels = Gtk.StringList.new([f"{i + 1}. jelenet" for i in range(3)])
        src = Adw.ComboRow(title="Forrás", model=labels, selected=cur)
        dst = Adw.ComboRow(title="Cél", model=Gtk.StringList.new([f"{i + 1}. jelenet" for i in range(3)]),
                           selected=(cur + 1) % 3)
        group.add(src)
        group.add(dst)
        dlg.set_extra_child(group)
        dlg.add_response("cancel", "Mégse")
        dlg.add_response("copy", "Másolás és mentés")
        dlg.set_response_appearance("copy", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_close_response("cancel")

        def check(*_):
            dlg.set_response_enabled("copy", src.get_selected() != dst.get_selected())
        src.connect("notify::selected", check)
        dst.connect("notify::selected", check)
        check()

        def resp(_d, r):
            if r == "copy":
                s, d = src.get_selected(), dst.get_selected()
                self._start(lambda: self.ctl.copy_scene(s, d))
        dlg.connect("response", resp)
        dlg.present(self.win)

    def import_to_slot(self, items: list[F.PresetData], filename: str) -> None:
        """Write all 3 scenes of a file preset directly to a user slot."""
        usable = [it for it in items if it.sources()]
        if not usable:
            self.toast("A fájlban nincs jelenet")
            return

        def confirm(target: int, name: str, item: Optional[F.PresetData]):
            item = item or usable[0]
            scenes, filled = _three_scenes(item)
            if filled:
                self.note(f"import to slot: scenes {filled} missing in file, filled from scene 1/edit")
            self._start(lambda: self.ctl.import_to_slot(target, scenes, name=name))

        first = usable[0]
        target = first.preset_index if first.preset_index is not None and PR.is_user(first.preset_index) \
            else (self.ctl.preset if self._current_user() else 0)
        SaveAsDialog(self.ctl.preset_names, target=target, name=first.name, items=usable,
                     heading="Importálás slotba", on_confirm=confirm,
                     body=f"{filename}\nA fájl mindhárom jelenete közvetlenül a kiválasztott "
                          "saját presetbe íródik (a szerkesztett állapot nem változik)."
                     ).present(self.win)

    def save_then(self, then: Callable[[], None]) -> None:
        """Unsaved-changes dialog: save (factory preset: Save As), then continue."""
        self.save(then=then)

    def _first_free_guess(self) -> int:
        for p in PR.USER_PRESETS:
            if not self.ctl.preset_names.get(p):
                return p
        return 0

    # ---------------------------------------------------------- progress --
    def _ev_save(self, job: SaveJob) -> None:
        what = KIND_TEXT.get(job.kind, "Mentés")
        label = f"{PR.slot_label(job.target)}"
        if not job.finished:
            title = f"{what} → {label}: {job.text}…"
            if self._progress is None:
                self._progress = self.toast(title, timeout=0)
            else:
                self._progress.set_title(GLib.markup_escape_text(title))
            return
        if self._progress is not None:
            self._progress.dismiss()
            self._progress = None
        self.update_sensitivity()
        then, self._then = self._then, None
        if job.ok:
            self.toast(f"{what} kész: {label} {job.result_name()}", timeout=4)
            self.note(f"save ok -> {PR.full_label(job.target)} '{job.result_name()}'"
                      f" (backup {job.backup_path})")
            if then is not None:
                then()
        else:
            extra = f" – a régi tartalom: {job.backup_path.name}" if job.backup_path else ""
            self.toast(f"{what} sikertelen: {job.error}{extra}", timeout=10)
            self.note(f"save FAILED -> {PR.full_label(job.target)}: {job.error} (backup {job.backup_path})")


def _three_scenes(item: F.PresetData) -> tuple[list[bytes], list[int]]:
    """The file's 3 scenes; missing ones are filled from the first available
    scene (or the edit buffer). Returns (scenes, 1-based indices that were filled)."""
    fallback = next((s for s in item.scenes if s is not None), item.edit_buffer)
    scenes, filled = [], []
    for i in range(PR.SCENES_PER_PRESET):
        s = item.scenes[i] if i < len(item.scenes) else None
        if s is None:
            filled.append(i + 1)
            s = fallback
        scenes.append(bytes(s))
    return scenes, filled
