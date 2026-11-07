"""File export/import.

Window actions registered here:
  win.export-preset   current edit buffer + the 3 stored scenes (0B) -> .mg300.json
  win.export-all      user bank (PC 0-35) x 3 scenes -> one JSON bundle
  win.import-preset   .mg300.json / bundle / QuickTone .mg300MK2patch -> applied LIVE
                      to the current scene of the edit buffer by CCs only; the toast
                      offers "Mentés…" (Save As) afterwards
  win.import-to-slot  same files -> all 3 scenes written straight into a chosen user
                      slot via the save path (0B x3 + commit, see save_actions.py)
"""

from __future__ import annotations

import logging
import os
from typing import Callable, Optional

from gi.repository import Adw, Gio, GLib, Gtk

from .. import apply as A
from .. import fileformat as F
from .. import presets as PR
from ..controller import Controller, StoredFetch
from .import_dialog import ImportDialog, source_label

log = logging.getLogger(__name__)

# the pedal answers a 0B in ~2 ms but the writer paces SysEx at >= 40 ms
FETCH_BASE_MS = 2500
FETCH_PER_SCENE_MS = 80


def _filters(*specs: tuple[str, list[str]]) -> Gio.ListStore:
    store = Gio.ListStore.new(Gtk.FileFilter)
    for name, patterns in specs:
        f = Gtk.FileFilter(name=name)
        for p in patterns:
            f.add_pattern(p)
        store.append(f)
    return store


def _safe_filename(text: str) -> str:
    keep = "".join(c if c.isalnum() or c in " -_" else "_" for c in text).strip()
    return keep.replace(" ", "_") or "preset"


class FileActions:
    def __init__(self, win: Gtk.Window, ctl: Controller, toast: Callable[..., Adw.Toast],
                 note: Callable[[str], None], saves=None):
        self.win = win
        self.saves = saves                      # SaveActions (Save As / import to slot)
        self.ctl = ctl
        self.toast = toast
        self.note = note
        self._fetch: Optional[StoredFetch] = None
        self._timeout_id: Optional[int] = None
        self._progress_toast: Optional[Adw.Toast] = None

        for name, cb in (("export-preset", self.export_preset), ("export-all", self.export_all),
                         ("import-preset", self.import_preset),
                         ("import-to-slot", self.import_to_slot)):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda *_a, cb=cb: cb())
            win.add_action(a)
        for ev in ("status", "scene", "save"):
            ctl.connect(ev, lambda *_: self.update_sensitivity())
        self.update_sensitivity()

    def update_sensitivity(self) -> None:
        c = self.ctl
        loaded = c.connected and c.scene is not None and c.preset is not None
        writable = c.connected and c.live and not c.busy
        for name, en in (("export-preset", loaded), ("export-all", c.connected),
                         ("import-preset", loaded and c.live),
                         ("import-to-slot", writable and self.saves is not None)):
            a = self.win.lookup_action(name)
            if a is not None:
                a.set_enabled(en)

    # -------------------------------------------------------- stored fetch --
    def _start_fetch(self, keys: list[tuple[int, int]], label: str,
                     done: Callable[[StoredFetch], None]) -> None:
        if self._fetch is not None:
            self.toast("Egy beolvasás már folyamatban van")
            return
        if not self.ctl.connected:
            self.toast("Nincs kapcsolat a pedállal")
            return
        total = len(keys)
        self._progress_toast = self.toast(f"{label}: beolvasás 0/{total}…", timeout=0)

        def progress(n, tot):
            if self._progress_toast is not None:
                self._progress_toast.set_title(f"{label}: beolvasás {n}/{tot}…")

        def finished(f: StoredFetch):
            self._fetch = None
            if self._timeout_id is not None:
                GLib.source_remove(self._timeout_id)
                self._timeout_id = None
            if self._progress_toast is not None:
                self._progress_toast.dismiss()
                self._progress_toast = None
            if f.missing:
                self.toast(f"{label}: {len(f.missing)} jelenetre nem jött válasz – megszakítva")
                self.note(f"export aborted, missing 0B replies: {f.missing}")
                return
            done(f)

        def timed_out():
            self._timeout_id = None
            if self._fetch is not None:
                self._fetch.finish()
            return GLib.SOURCE_REMOVE

        self._fetch = self.ctl.fetch_stored(keys, on_done=finished, on_progress=progress)
        if self._fetch is not None and not self._fetch.done:
            self._timeout_id = GLib.timeout_add(FETCH_BASE_MS + FETCH_PER_SCENE_MS * total, timed_out)

    # -------------------------------------------------------------- export --
    def export_preset(self) -> None:
        if self.ctl.scene is None or self.ctl.preset is None:
            self.toast("Nincs beolvasott preset")
            return
        preset = self.ctl.preset
        edit = bytes(self.ctl.scene)
        name = self.ctl.layout.scene_name(edit)
        keys = [(preset, s) for s in range(PR.SCENES_PER_PRESET)]

        def done(f: StoredFetch):
            data = F.PresetData(name=name, preset_index=preset,
                                scenes=[f.results[k] for k in keys], edit_buffer=edit)
            fname = f"{PR.full_label(preset)}-{name}" if PR.is_valid(preset) else name
            self._save_dialog("Preset exportálása", _safe_filename(fname) + F.EXTENSION,
                              F.dump_preset(data), f"Exportálva: {name or preset}")

        self._start_fetch(keys, "Export", done)

    def export_all(self) -> None:
        keys = [(p, s) for p in PR.USER_PRESETS for s in range(PR.SCENES_PER_PRESET)]

        def done(f: StoredFetch):
            items = []
            for p in PR.USER_PRESETS:
                scenes = [f.results[(p, s)] for s in range(PR.SCENES_PER_PRESET)]
                items.append(F.PresetData(name=F.scene_name(scenes[0]), preset_index=p, scenes=scenes))
            self._save_dialog("Összes saját preset exportálása", "mg300-user-presets" + F.EXTENSION,
                              F.dump_bundle(items), f"Exportálva: {len(items)} preset")

        self._start_fetch(keys, "Összes export", done)

    def _save_dialog(self, title: str, initial: str, text: str, ok_msg: str) -> None:
        dlg = Gtk.FileDialog(title=title, initial_name=initial, modal=True)
        dlg.set_filters(_filters(("MG-300 preset (*.mg300.json)", ["*.mg300.json"]),
                                 ("JSON", ["*.json"])))

        def cb(d, res):
            try:
                gfile = d.save_finish(res)
            except GLib.Error:
                return                                  # cancelled
            path = gfile.get_path() if gfile else None
            if not path:
                self.toast("Ide nem lehet menteni")
                return
            try:
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    fh.write(text)
                os.replace(tmp, path)
            except OSError as e:
                self.toast(f"Mentési hiba: {e.strerror or e}")
                return
            self.toast(ok_msg)
            self.note(f"exported {path}")

        dlg.save(self.win, None, cb)

    # -------------------------------------------------------------- import --
    def _open_dialog(self, title: str, then: Callable[[str], None]) -> None:
        dlg = Gtk.FileDialog(title=title, modal=True)
        dlg.set_filters(_filters(
            ("MG-300 presetek", ["*.mg300.json", "*.json", "*.mg300MK2patch", "*.MG300MK2PATCH"]),
            ("Minden fájl", ["*"])))

        def cb(d, res):
            try:
                gfile = d.open_finish(res)
            except GLib.Error:
                return
            path = gfile.get_path() if gfile else None
            if path:
                then(path)

        dlg.open(self.win, None, cb)

    def import_preset(self) -> None:
        self._open_dialog("Preset importálása", self.import_path)

    def import_to_slot(self) -> None:
        if self.saves is None:
            return
        self._open_dialog("Importálás slotba", self.import_to_slot_path)

    def _load(self, path: str) -> Optional[list[F.PresetData]]:
        try:
            return F.load_path(path)
        except (F.FileFormatError, OSError) as e:
            self.toast(f"Nem olvasható fájl: {e}")
            return None

    def import_to_slot_path(self, path: str) -> None:
        items = self._load(path)
        if items is not None:
            self.saves.import_to_slot(items, os.path.basename(path))

    def import_path(self, path: str) -> None:
        items = self._load(path)
        if items is None:
            return
        fname = os.path.basename(path)
        if len(items) == 1 and len(items[0].sources()) == 1:
            key, data = items[0].sources()[0]
            self._apply(items[0], key, data)
            return
        ImportDialog(fname, items, self._apply, preferred_scene=self.ctl.scene_index).present(self.win)

    def _apply(self, item: F.PresetData, key: str, data: bytes) -> None:
        if self.ctl.scene is None:
            self.toast("Előbb olvasd be a pedál állapotát")
            return
        try:
            plan = self.ctl.apply_scene(data)
        except RuntimeError:
            self.toast("Csak olvasható mód (--no-live): az import nem küld semmit")
            return
        except ValueError as e:
            self.toast(f"Hibás jelenet: {e}")
            return
        summary = A.summarize(plan)
        t = self.toast(f"{item.name or 'Preset'} ({source_label(key)}) – {summary}", timeout=8)
        if self.saves is not None and self.ctl.live:
            t.set_button_label("Mentés…")
            t.connect("button-clicked", lambda *_: self.saves.save_as(name=item.name))
        self.note(f"import {item.source} '{item.name}' {key}: {summary}")
        for s in plan.skipped:
            self.note(f"  skipped {s.kind} {s.block} {s.detail}".rstrip())
