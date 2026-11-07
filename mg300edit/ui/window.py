"""Main window: header (status, preset selector, scene 1/2/3, tempo + TAP, save),
preset sidebar, chain row, block editor, monitor. File export/import lives in
file_actions.py, saving in save_actions.py, the preset list in preset_sidebar.py.

Settings (~/.config/mg300edit/settings.json): developer mode (debug details in
the chain/editor + the MIDI-monitor header button; the monitor itself is always
reachable via Ctrl+M), the unsaved-changes question on preset switch and the
built-in click's volume/accent/output.

Built-in click (Takk metronome, ../metronome.py): header toggle next to TAP
(win.click, Ctrl+K) + settings popover; follows the controller's "tempo" event
(pedal TAP / preset / scene, our tempo spin and TAP). pw-cat only runs while
the toggle is on; it stops on toggle off, window close and if the pedal's
PipeWire sink disappears."""

from __future__ import annotations

from typing import Callable, Optional

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from .. import presets as PR
from .. import protocol as P
from ..controller import Controller
from ..layout import BlockSpec, ParamSpec
from ..metronome import Metronome
from ..settings import Settings
from ..taptempo import TapTempo
from .block_editor import BlockEditor
from .chain import ChainRow
from .click_control import ClickControl
from .file_actions import FileActions
from .monitor import MonitorPanel
from .preset_sidebar import PresetSidebar
from .save_actions import SaveActions
from .util import Guard, glib_dispatch

TICK_MS = 100            # Controller.tick() period (save timeouts, scene restore)
TEMPO_DEBOUNCE_MS = 150  # a dragged/held tempo spin sends at most one 03 set per 150 ms
CLICK_POLL_TICKS = 20    # while the click plays, re-read the tempo every 2 s (catches
                         # tempo changes without a 7E 02 03 notification, like Takk)
DIGIT_SCENES = {Gdk.KEY_1: 0, Gdk.KEY_2: 1, Gdk.KEY_3: 2,
                Gdk.KEY_KP_1: 0, Gdk.KEY_KP_2: 1, Gdk.KEY_KP_3: 2}
PRESET_KEYS = {Gdk.KEY_Left: -1, Gdk.KEY_Right: 1, Gdk.KEY_Page_Up: -1, Gdk.KEY_Page_Down: 1}


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, ctl: Controller, show_monitor: bool = False,
                 settings: Optional[Settings] = None, metronome: Optional[Metronome] = None):
        super().__init__(application=app, title="OpenTone", default_width=1200, default_height=780)
        self.ctl = ctl
        self.settings = settings if settings is not None else Settings()
        self.guard = Guard()
        self.selected_id: Optional[str] = None
        self.tap = TapTempo()
        self._tempo_timer: Optional[int] = None
        self.metronome = metronome if metronome is not None else Metronome(self.settings, glib_dispatch)
        self._mute_toast: Optional[Adw.Toast] = None
        self._ticks = 0

        # ---- header ----
        header = Adw.HeaderBar()
        self.sidebar_btn = Gtk.ToggleButton(icon_name="sidebar-show-symbolic", active=True,
                                            tooltip_text="Presetlista (F9)")
        header.pack_start(self.sidebar_btn)
        # connection: a dot (tooltip = full status); text only while there is no pedal
        status_box = Gtk.Box(spacing=6, margin_start=4)
        self.status_dot = Gtk.Label(label="●", css_classes=["status-dot", "off"])
        self.status_label = Gtk.Label(label="Nincs pedál", css_classes=["dim-label", "caption"])
        status_box.append(self.status_dot)
        status_box.append(self.status_label)
        header.pack_start(status_box)

        preset_box = Gtk.Box(spacing=6)
        self.prev_btn = Gtk.Button(icon_name="go-previous-symbolic", css_classes=["flat"],
                                   tooltip_text="Előző preset (← / PgUp)")
        self.prev_btn.connect("clicked", lambda *_: self._step_preset(-1))
        self.next_btn = Gtk.Button(icon_name="go-next-symbolic", css_classes=["flat"],
                                   tooltip_text="Következő preset (→ / PgDn)")
        self.next_btn.connect("clicked", lambda *_: self._step_preset(+1))
        self.preset_slot = Gtk.Label(label="", css_classes=["preset-chip"], valign=Gtk.Align.CENTER,
                                     visible=False)
        self.preset_name = Gtk.Label(label="—", css_classes=["preset-name"], width_chars=12,
                                     ellipsize=3, max_width_chars=20)
        self.modified_label = Gtk.Label(label="módosítva", visible=False, valign=Gtk.Align.CENTER,
                                        css_classes=["preset-modified"],
                                        tooltip_text="A szerkesztett állapot nincs elmentve (Ctrl+S)")
        for w in (self.prev_btn, self.preset_slot, self.preset_name, self.modified_label,
                  self.next_btn):
            preset_box.append(w)

        # scene selector 1/2/3 + copy
        self.scene_group = Adw.ToggleGroup(css_classes=["scene-group"], valign=Gtk.Align.CENTER,
                                           tooltip_text="Jelenet (1 / 2 / 3)")
        for i in range(PR.SCENES_PER_PRESET):
            self.scene_group.add(Adw.Toggle(name=str(i), label=str(i + 1),
                                            tooltip=f"{i + 1}. jelenet ({i + 1})"))
        self.scene_group.set_active(Gtk.INVALID_LIST_POSITION)
        self.scene_group.connect("notify::active", self._on_scene_toggle)
        scene_box = Gtk.Box(spacing=4, margin_start=10, css_classes=["scene-box"])
        self.scene_caption = Gtk.Label(label="Jelenet", css_classes=["dim-label", "caption"])
        scene_box.append(self.scene_caption)
        scene_box.append(self.scene_group)
        self.copy_scene_btn = Gtk.Button(icon_name="edit-copy-symbolic", action_name="win.copy-scene",
                                         css_classes=["flat"], valign=Gtk.Align.CENTER,
                                         tooltip_text="Scene másolása… (mentéssel)")
        scene_box.append(self.copy_scene_btn)
        preset_box.append(scene_box)
        header.set_title_widget(preset_box)

        menu = Gio.Menu()
        sec = Gio.Menu()
        sec.append("Frissítés a pedálról", "win.refresh")
        sec.append("Változások elvetése", "win.revert")
        menu.append_section(None, sec)
        sec = Gio.Menu()
        sec.append("Mentés", "win.save")
        sec.append("Mentés másként…", "win.save-as")
        sec.append("Átnevezés…", "win.rename")
        sec.append("Scene másolása…", "win.copy-scene")
        menu.append_section(None, sec)
        sec = Gio.Menu()
        sec.append("Preset importálása…", "win.import-preset")
        sec.append("Importálás slotba…", "win.import-to-slot")
        sec.append("Preset exportálása…", "win.export-preset")
        sec.append("Összes saját exportálása…", "win.export-all")
        menu.append_section(None, sec)
        sec = Gio.Menu()
        sec.append("Metronóm (Takk)", "win.click")
        sec.append("Metronóm beállításai…", "win.click-settings")
        menu.append_section(None, sec)
        sec = Gio.Menu()
        sec.append("Figyelmeztetés mentetlen változásokra", "win.confirm-unsaved")
        sec.append("Fejlesztői mód", "win.developer-mode")
        sec.append("MIDI monitor", "win.monitor")
        menu.append_section(None, sec)
        sec = Gio.Menu()
        sec.append("Névjegy", "app.about")
        sec.append("Kilépés", "app.quit")
        menu.append_section(None, sec)
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       tooltip_text="Menü"))
        save_tip = "Mentés a pedálra, az aktuális presetbe (Ctrl+S)"
        self.save_btn = Gtk.Button(label="Mentés", action_name="win.save", tooltip_text=save_tip)
        header.pack_end(self.save_btn)
        self.save_icon_btn = Gtk.Button(icon_name="document-save-symbolic", action_name="win.save",
                                        tooltip_text=save_tip, visible=False)
        header.pack_end(self.save_icon_btn)
        self.mon_btn = Gtk.ToggleButton(icon_name="utilities-terminal-symbolic",
                                        tooltip_text="MIDI monitor (Ctrl+M)", action_name="win.monitor")
        header.pack_end(self.mon_btn)
        self.refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic",
                                      tooltip_text="Frissítés a pedálról (F5)", action_name="win.refresh")
        header.pack_end(self.refresh_btn)
        tempo_box = Gtk.Box(spacing=4, margin_end=6)
        self.tempo_spin = Gtk.SpinButton.new_with_range(P.TEMPO_MIN, P.TEMPO_MAX, 1)
        self.tempo_spin.update_property([Gtk.AccessibleProperty.LABEL], ["Tempó (BPM)"])
        self.tempo_spin.set_tooltip_text(
            f"Tempó (BPM, {P.TEMPO_MIN}–{P.TEMPO_MAX}) – az aktív jelenetre hat;\n"
            "mentéskor mindhárom jelenetbe ez kerül")
        self.tempo_spin.connect("value-changed", self._on_tempo_spin)
        self.tempo_spin.connect("output", self._tempo_output)
        self.tempo_spin.set_text("")            # until the pedal reports its tempo
        tempo_box.append(self.tempo_spin)
        self.bpm_label = Gtk.Label(label="BPM", css_classes=["dim-label"])
        tempo_box.append(self.bpm_label)
        self.tap_btn = Gtk.Button(label="TAP", css_classes=["tap-button"],
                                  tooltip_text="Tap tempó: koppints ütemre (az utolsó 4 koppintás átlaga)")
        self.tap_btn.connect("clicked", self._on_tap)
        tempo_box.append(self.tap_btn)
        self.click = ClickControl(self.metronome)
        tempo_box.append(self.click)
        header.pack_end(tempo_box)

        # ---- body ----
        self.chain = ChainRow(self.guard, self._on_select_block, self._on_toggle_block, ctl=ctl)
        self.editor = BlockEditor(self.guard, self._on_model, self._on_param,
                                  on_toggle=self._on_toggle_block)
        self.chain_sep = Gtk.Separator()
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(self.chain)
        body.append(self.chain_sep)
        body.append(self.editor)

        self.monitor = MonitorPanel(ctl.send_raw)
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, start_child=body,
                               end_child=self.monitor, shrink_end_child=False,
                               resize_end_child=False, position=480)
        self.monitor.set_visible(show_monitor)

        self.sidebar = PresetSidebar(self._on_sidebar_select)
        self.split = Adw.OverlaySplitView(sidebar=self.sidebar, content=self.paned,
                                          min_sidebar_width=210, max_sidebar_width=290,
                                          sidebar_width_fraction=0.22)
        self.split.bind_property("show-sidebar", self.sidebar_btn, "active",
                                 GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        # narrow window: sidebar becomes an overlay, header sheds optional labels
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 1180sp"))
        bp.add_setter(self.split, "collapsed", True)
        bp.add_setter(self.split, "show-sidebar", False)
        # (copy-scene and the click settings stay reachable from the main menu)
        for w in (self.save_btn, self.bpm_label, self.scene_caption, self.refresh_btn,
                  self.copy_scene_btn, self.click.label, self.click.menu_btn):
            bp.add_setter(w, "visible", False)
        bp.add_setter(self.save_icon_btn, "visible", True)
        bp.add_setter(self.preset_name, "width-chars", 8)
        bp.add_setter(self.preset_name, "max-width-chars", 14)
        self.add_breakpoint(bp)
        self.set_size_request(820, 480)

        self.toasts = Adw.ToastOverlay(child=self.split)
        tv = Adw.ToolbarView()
        tv.add_top_bar(header)
        tv.set_content(self.toasts)
        self.set_content(tv)

        # ---- actions / keys ----
        a = Gio.SimpleAction.new("refresh", None)
        a.connect("activate", lambda *_: self.ctl.refresh())
        self.add_action(a)
        mon = Gio.SimpleAction.new_stateful("monitor", None, GLib.Variant.new_boolean(show_monitor))
        mon.connect("change-state", self._on_monitor_toggle)
        self.add_action(mon)
        rv = Gio.SimpleAction.new("revert", None)
        rv.connect("activate", lambda *_: self.ctl.revert())
        self.add_action(rv)
        dev = Gio.SimpleAction.new_stateful(
            "developer-mode", None, GLib.Variant.new_boolean(bool(self.settings.get("developer_mode"))))
        dev.connect("change-state", self._on_dev_toggle)
        self.add_action(dev)
        cu = Gio.SimpleAction.new_stateful(
            "confirm-unsaved", None, GLib.Variant.new_boolean(bool(self.settings.get("confirm_unsaved"))))
        cu.connect("change-state", self._on_confirm_unsaved_toggle)
        self.add_action(cu)
        ck = Gio.SimpleAction.new_stateful("click", None, GLib.Variant.new_boolean(False))
        ck.connect("change-state", self._on_click_toggle)
        self.add_action(ck)
        cs = Gio.SimpleAction.new("click-settings", None)
        cs.connect("activate", lambda *_: self.click.show_settings())
        self.add_action(cs)
        sb = Gio.SimpleAction.new("toggle-sidebar", None)
        sb.connect("activate", lambda *_: self.split.set_show_sidebar(not self.split.get_show_sidebar()))
        self.add_action(sb)
        for action, accels in (("win.revert", ["<Control>z"]), ("win.refresh", ["F5", "<Control>r"]),
                               ("win.monitor", ["<Control>m"]), ("win.toggle-sidebar", ["F9"]),
                               ("win.import-preset", ["<Control>o"]),
                               ("win.export-preset", ["<Control>e"]),
                               ("win.save", ["<Control>s"]), ("win.save-as", ["<Control><Shift>s"]),
                               ("win.rename", ["F2"]), ("win.click", ["<Control>k"])):
            app.set_accels_for_action(action, accels)
        self.saves = SaveActions(self, ctl, self.toast, self.monitor.note)
        self.files = FileActions(self, ctl, self.toast, self.monitor.note, saves=self.saves)

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        # ---- controller events ----
        ctl.connect("status", self._ev_status)
        ctl.connect("preset", self._ev_preset)
        ctl.connect("tempo", self._ev_tempo)
        ctl.connect("tempo", self.metronome.set_tempo)          # click follows from the next beat
        self.metronome.set_tempo(ctl.tempo)
        self.metronome.connect("state", self._ev_click_state)
        self.metronome.connect("notice", self._ev_click_notice)
        ctl.connect("scene", self._ev_scene)
        ctl.connect("scene_index", self._ev_scene_index)
        ctl.connect("modified", self._ev_modified)
        ctl.connect("param", self.editor.update_param)
        ctl.connect("identity", lambda ident: self.toast(
            f"Csatlakozva: {ident.model} · firmware {ident.firmware}"))
        ctl.connect("info", self._ev_info)
        ctl.connect("name", self._ev_name)
        ctl.connect("names", lambda: self.sidebar.set_names(self.ctl.preset_names))
        ctl.transport.on_traffic = self.monitor.add
        ctl.connect("save", lambda _job: self._update_actions())
        self._apply_developer_mode(bool(self.settings.get("developer_mode")))
        self._update_actions()
        self._ev_scene()

        self._tick_id = GLib.timeout_add(TICK_MS, self._tick)
        self.connect("close-request", self._on_close)
        self.connect("destroy", lambda *_: self.metronome.shutdown())

    # ---------- helpers ----------
    def toast(self, text: str, timeout: int = 3) -> Adw.Toast:
        t = Adw.Toast(title=GLib.markup_escape_text(text), timeout=timeout)
        self.toasts.add_toast(t)
        return t

    def _blocks(self) -> list[BlockSpec]:
        return self.ctl.layout.chain(self.ctl.block_order)

    def _tick(self) -> bool:
        self.ctl.tick()
        self._ticks += 1
        if (self._ticks % CLICK_POLL_TICKS == 0 and self.metronome.playing
                and self.ctl.connected and not self.ctl.busy):
            self.ctl.refresh_tempo()
        return GLib.SOURCE_CONTINUE

    def _on_close(self, *_):
        if self._tick_id:
            GLib.source_remove(self._tick_id)
            self._tick_id = 0
        self.metronome.shutdown()           # never leave pw-cat running
        self.settings.save()
        return False

    # ---------- built-in click (Takk) ----------
    def _on_click_toggle(self, action, value):
        if value.get_boolean():
            self.metronome.enable()
        else:
            self.metronome.disable()
        action.set_state(GLib.Variant.new_boolean(self.metronome.enabled))

    def _ev_click_state(self):
        action = self.lookup_action("click")
        if action is not None and action.get_state().get_boolean() != self.metronome.enabled:
            action.set_state(GLib.Variant.new_boolean(self.metronome.enabled))
        if not self.metronome.enabled:
            self._dismiss_mute_toast()

    def _ev_click_notice(self, kind: str, text: str):
        if kind == "muted":
            self._dismiss_mute_toast()
            t = Adw.Toast(title=GLib.markup_escape_text(text), timeout=0, button_label="Feloldás")
            t.connect("button-clicked", lambda *_: self.metronome.unmute())   # user's click only
            t.connect("dismissed", lambda tt: setattr(self, "_mute_toast", None)
                      if self._mute_toast is tt else None)
            self._mute_toast = t
            self.toasts.add_toast(t)
        elif kind == "unmuted":
            self._dismiss_mute_toast()
        elif text:
            self.monitor.note(f"metronóm: {text}")
            self.toast(text, timeout=5)

    def _dismiss_mute_toast(self):
        t, self._mute_toast = self._mute_toast, None
        if t is not None:
            t.dismiss()

    # ---------- developer mode / settings ----------
    def _apply_developer_mode(self, on: bool) -> None:
        for w in (self.chain, self.editor):
            fn = getattr(w, "set_developer_mode", None)
            if fn is not None:
                fn(on)
        self.mon_btn.set_visible(on)

    def _on_dev_toggle(self, action, value):
        action.set_state(value)
        on = value.get_boolean()
        self.settings.set("developer_mode", on)
        self._apply_developer_mode(on)

    def _on_confirm_unsaved_toggle(self, action, value):
        action.set_state(value)
        self.settings.set("confirm_unsaved", value.get_boolean())

    # ---------- controller events (pedal -> UI, never echo) ----------
    def _ev_status(self, connected: bool, text: str):
        self.status_label.set_visible(not connected)
        for w in (self.status_dot, self.status_label):
            w.set_tooltip_text(text)
        self.status_dot.remove_css_class("on" if not connected else "off")
        self.status_dot.add_css_class("on" if connected else "off")
        self.monitor.note(f"status: {text}")
        self._update_actions()
        self._show_selected()

    def _ev_preset(self, preset: int):
        valid = PR.is_valid(preset)
        self._update_actions()
        self.preset_slot.set_label(PR.full_label(preset, user="Saját", factory="Gyári") if valid else "")
        self.preset_slot.set_visible(valid)
        self.preset_slot.set_tooltip_text(f"Program Change {preset}" if valid else None)
        factory = valid and not PR.is_user(preset)
        (self.preset_slot.add_css_class if factory else self.preset_slot.remove_css_class)("factory")
        self.sidebar.set_current(preset if valid else None)

    def _ev_name(self, preset: int, name: str):
        self.sidebar.set_name(preset, name)

    def _ev_tempo(self, bpm: int):
        if self._tempo_timer is not None:
            return                      # the user is changing it right now
        with self.guard:
            self.tempo_spin.set_value(bpm)
            self.tempo_spin.update()          # re-format (drops the empty placeholder)

    def _ev_scene_index(self, scene: Optional[int]):
        known = scene is not None and 0 <= scene < PR.SCENES_PER_PRESET
        self.scene_group.set_tooltip_text(
            "Aktív jelenet (1 / 2 / 3)" if known else
            "Az aktív jelenet még nem ismert – válassz egyet (1 / 2 / 3)")
        with self.guard:
            self.scene_group.set_active(scene if scene is not None and 0 <= scene < 3
                                        else Gtk.INVALID_LIST_POSITION)

    def _ev_modified(self, modified: bool):
        self._update_title()
        self.sidebar.set_modified(modified)
        self._update_actions()

    def _update_actions(self) -> None:
        """Window-level action state (save/file actions manage their own)."""
        loaded = self.ctl.connected and self.ctl.preset is not None
        self.lookup_action("revert").set_enabled(loaded and self.ctl.live and not self.ctl.busy)
        self.lookup_action("refresh").set_enabled(self.ctl.connected)
        for w in (self.scene_group, self.tempo_spin, self.tap_btn, self.prev_btn, self.next_btn):
            w.set_sensitive(loaded)
        # no pedal: only the status page (no empty chain strip)
        shown = self.ctl.connected and self.ctl.scene is not None
        self.chain.set_visible(shown)
        self.chain_sep.set_visible(shown)
        self.sidebar.set_sensitive(self.ctl.connected)
        # a modified edit buffer makes "Mentés" the suggested action
        if self.ctl.modified and self.ctl.live:
            self.save_btn.add_css_class("suggested-action")
        else:
            self.save_btn.remove_css_class("suggested-action")

    def _ev_info(self, text: str):
        self.monitor.note(text)
        self.toast(text)

    def _update_title(self):
        scene = self.ctl.scene
        name = self.ctl.layout.scene_name(scene) if scene is not None else ""
        mod = self.ctl.modified
        if scene is None:
            self.preset_name.set_label("MG-300 Editor")
        else:
            self.preset_name.set_label(name or "(névtelen)")
        self.preset_name.set_tooltip_text("Módosítva – nincs elmentve" if mod else None)
        self.modified_label.set_visible(mod)
        self.set_title(f"• {name} – MG-300 Editor" if mod and name else "MG-300 Editor")

    def _ev_scene(self):
        scene = self.ctl.scene
        blocks = self._blocks()
        self._update_title()
        self._update_actions()
        self._ev_scene_index(self.ctl.scene_index)
        ids = [b.id for b in blocks]
        if self.selected_id not in ids:
            editable = [b for b in blocks if b.known and b.model_offset is not None]
            self.selected_id = editable[0].id if editable else (ids[0] if ids else None)
        self.chain.set_chain(blocks, scene, self.selected_id)
        self._show_selected()

    def _show_selected(self):
        scene = self.ctl.scene
        block = next((b for b in self._blocks() if b.id == self.selected_id), None)
        if not self.ctl.connected or scene is None:
            self.editor.show_no_data(self.ctl.connected)
        elif block is None:
            self.editor.show_empty("Nincs kiválasztott blokk", "Válassz egy blokkot a fenti láncból.")
        else:
            with self.guard:
                self.editor.show_block(block, scene)

    # ---------- user -> controller ----------
    def _on_select_block(self, block: BlockSpec):
        self.selected_id = block.id
        self._show_selected()

    def _on_toggle_block(self, block: BlockSpec, bypassed: bool):
        self.ctl.set_bypass(block, bypassed)
        # keep the chain card and the editor header in step
        self.chain.refresh_bypass(block.id)
        self.editor.refresh_bypass(block.id)

    def _on_model(self, block: BlockSpec, index: int):
        self.ctl.set_model(block, index)
        # model change swaps the parameter set; rebuild after the combo signal returns
        GLib.idle_add(lambda: (self._ev_scene(), GLib.SOURCE_REMOVE)[1])

    def _on_param(self, block: BlockSpec, param: ParamSpec, value: int):
        self.ctl.set_param(block, param.name, value)

    def _on_scene_toggle(self, group, _pspec):
        if self.guard:
            return
        i = group.get_active()
        if 0 <= i < PR.SCENES_PER_PRESET and i != self.ctl.scene_index:
            self.ctl.select_scene(i)

    # -- preset switching (asks about unsaved changes) --
    def _request_preset(self, preset: int) -> None:
        if preset == self.ctl.preset:
            return
        if self.ctl.busy:
            self.toast("Mentés folyamatban – várj, amíg befejeződik")
            self._ev_preset(self.ctl.preset)
            return
        if self.ctl.modified and self.ctl.live and self.settings.get("confirm_unsaved"):
            self._ev_preset(self.ctl.preset)          # keep spin/sidebar on the current one
            self._ask_unsaved(lambda: self.ctl.select_preset(preset))
            return
        self.ctl.select_preset(preset)

    def _ask_unsaved(self, proceed: Callable[[], None]) -> None:
        cur = self.ctl.preset
        name = self.ctl.layout.scene_name(self.ctl.scene) if self.ctl.scene is not None else ""
        label = f"{PR.slot_label(cur)} {name}" if cur is not None and PR.is_valid(cur) else name
        factory = cur is not None and not PR.is_user(cur)
        dlg = Adw.AlertDialog(
            heading="Mentetlen változások",
            body=f"A(z) {label} preset módosításai elvesznek, ha most másik presetre váltasz."
                 + ("\nEz gyári preset: a Mentés másik (saját) helyre ment." if factory else ""))
        dlg.add_response("cancel", "Mégse")
        dlg.add_response("discard", "Elvetés")
        dlg.add_response("save", "Mentés…" if factory else "Mentés")
        dlg.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("save")
        dlg.set_close_response("cancel")

        def resp(_d, r):
            if r == "discard":
                proceed()
            elif r == "save":
                self.saves.save_then(proceed)
        dlg.connect("response", resp)
        dlg.present(self)

    def _step_preset(self, delta: int) -> None:
        n = min(self.ctl.layout.preset_count, PR.PRESET_COUNT)
        cur = self.ctl.preset if self.ctl.preset is not None else 0
        self._request_preset((cur + delta) % n)

    def _on_sidebar_select(self, preset: int):
        self._request_preset(preset)
        if self.split.get_collapsed():
            self.split.set_show_sidebar(False)

    # -- tempo --
    def _on_tempo_spin(self, spin):
        if self.guard:
            return
        if self._tempo_timer is not None:
            GLib.source_remove(self._tempo_timer)
        self._tempo_timer = GLib.timeout_add(TEMPO_DEBOUNCE_MS, self._send_tempo)

    def _tempo_output(self, spin) -> bool:
        if self.ctl.tempo is None:          # not read yet: no fake "40"
            spin.set_text("")
            return True
        return False

    def _send_tempo(self) -> bool:
        self._tempo_timer = None
        bpm = int(self.tempo_spin.get_value())
        if bpm != self.ctl.tempo:
            self.ctl.set_tempo(bpm)
        return GLib.SOURCE_REMOVE

    def _on_tap(self, _btn):
        bpm = self.tap.tap()
        if bpm is not None:
            self.tempo_spin.set_value(bpm)          # -> debounced set_tempo

    def _on_monitor_toggle(self, action, value):
        action.set_state(value)
        self.monitor.set_visible(value.get_boolean())

    def _on_key(self, _ctrl, keyval, _code, state):
        """Window-wide keys (capture phase): 1/2/3 scene, ←/→ and PgUp/PgDn preset.
        Text fields, knobs/sliders and open dialogs keep their own keys."""
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            return False
        if keyval not in DIGIT_SCENES and keyval not in PRESET_KEYS:
            return False
        if self.get_visible_dialog() is not None:
            return False
        focus = self.get_focus()
        if isinstance(focus, (Gtk.Editable, Gtk.TextView)):
            return False
        if keyval in DIGIT_SCENES:
            if self.ctl.preset is not None and self.ctl.connected:
                self.ctl.select_scene(DIGIT_SCENES[keyval])
            return True
        if isinstance(focus, (Gtk.Range, Gtk.AccessibleRange)):
            return False            # sliders / knobs use arrows and PgUp/PgDn themselves
        self._step_preset(PRESET_KEYS[keyval])
        return True
