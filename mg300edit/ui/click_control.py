"""Header control for the built-in click (Takk metronome): a toggle with a beat
LED (bound to the window action win.click, Ctrl+K) and a settings popover
(volume, accent on beat 1, output channels) behind a small arrow button, a
right-click on the toggle or the main menu (show_settings(); the arrow is hidden
in the narrow layout). Logic lives in ../metronome.py."""

from __future__ import annotations

from pathlib import Path

from gi.repository import Adw, Gdk, GLib, Gtk

from takkapp.click import FALLBACK_BPM, OUTPUT_CHOICES

from ..metronome import OUTPUT_KEYS, Metronome

ICON_DIR = Path(__file__).resolve().parents[2] / "data" / "icons"
ICON_NAME = "opentone-metronome-symbolic"
FLASH_MS = 110           # LED on-time per beat (CSS fades it out afterwards)
SAVE_DEBOUNCE_MS = 600   # volume slider: write settings.json once the drag settles


def install_icons(display: Gdk.Display | None = None) -> None:
    """Make data/icons (own symbolic icons) visible to the icon theme (idempotent)."""
    display = display or Gdk.Display.get_default()
    if display is None:
        return
    theme = Gtk.IconTheme.get_for_display(display)
    if str(ICON_DIR) not in (theme.get_search_path() or []):
        theme.add_search_path(str(ICON_DIR))


class ClickControl(Gtk.Box):
    def __init__(self, metronome: Metronome, action_name: str = "win.click"):
        super().__init__(css_classes=["linked", "click-control"], valign=Gtk.Align.CENTER)
        self.m = metronome
        self._flash_id = 0
        self._save_id = 0
        install_icons()

        content = Gtk.Box(spacing=5)
        content.append(Gtk.Image(icon_name=ICON_NAME))
        self.label = Gtk.Label(label="Takk", css_classes=["click-label"])
        content.append(self.label)
        self.led = Gtk.Box(css_classes=["click-led"], valign=Gtk.Align.CENTER)
        content.append(self.led)
        self.toggle = Gtk.ToggleButton(child=content, action_name=action_name,
                                       css_classes=["click-toggle"])
        self.toggle.update_property([Gtk.AccessibleProperty.LABEL], ["Metronóm (Takk)"])
        self.append(self.toggle)

        # right-click on the toggle also opens the settings
        rc = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        rc.connect("pressed", lambda *_: self.show_settings())
        self.toggle.add_controller(rc)

        self.menu_btn = Gtk.Button(icon_name="pan-down-symbolic", css_classes=["click-menu"],
                                   tooltip_text="Metronóm beállításai")
        self.menu_btn.update_property([Gtk.AccessibleProperty.LABEL], ["Metronóm beállításai"])
        self.menu_btn.connect("clicked", lambda *_: self.show_settings())
        self.append(self.menu_btn)

        # the popover belongs to the whole control (not to the arrow button), so it
        # still opens from the menu / right-click when the arrow is hidden
        self.popover = Gtk.Popover(css_classes=["click-popover"], position=Gtk.PositionType.BOTTOM)
        self.popover.set_child(self._build_popover())
        self.popover.set_parent(self)
        self.popover.connect("closed", lambda *_: self._save_now())

        metronome.connect("state", self.update)
        metronome.connect("beat", self._on_beat)
        self.connect("destroy", self._on_destroy)
        self.update()

    def show_settings(self) -> None:
        """Open the settings popover, pointing at the arrow (or the toggle in
        the narrow layout, where the arrow is hidden)."""
        target = self.menu_btn if self.menu_btn.get_visible() else self.toggle
        ok, b = target.compute_bounds(self)
        if ok:
            r = Gdk.Rectangle()
            r.x, r.y = int(b.get_x()), int(b.get_y())
            r.width, r.height = max(1, int(b.get_width())), max(1, int(b.get_height()))
            self.popover.set_pointing_to(r)
        self.popover.popup()

    def _on_destroy(self, *_):
        self._cancel_timers()
        if self.popover.get_parent() is not None:
            self.popover.unparent()

    # -- popover --
    def _build_popover(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=10,
                      margin_bottom=10, margin_start=10, margin_end=10)
        head = Gtk.Box(spacing=8)
        head.append(Gtk.Label(label="Metronóm", css_classes=["heading"], xalign=0, hexpand=True))
        self.tempo_label = Gtk.Label(css_classes=["click-tempo", "numeric"], xalign=1)
        head.append(self.tempo_label)
        box.append(head)

        lb = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        vol_row = Adw.ActionRow(title="Hangerő")
        self.volume = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.volume.set_hexpand(True)
        self.volume.set_size_request(130, -1)
        self.volume.set_valign(Gtk.Align.CENTER)
        self.volume.set_draw_value(False)
        self.volume.set_value(self.m.volume)
        self.volume.update_property([Gtk.AccessibleProperty.LABEL], ["Metronóm hangereje"])
        self.vol_label = Gtk.Label(label=f"{self.m.volume} %", width_chars=5, xalign=1,
                                   css_classes=["numeric", "dim-label"])
        self.volume.connect("value-changed", self._on_volume)
        vol_row.add_suffix(self.volume)
        vol_row.add_suffix(self.vol_label)
        lb.append(vol_row)

        self.accent_row = Adw.SwitchRow(title="Hangsúly az 1. ütésen", active=self.m.accent)
        self.accent_row.connect("notify::active",
                                lambda row, _p: self.m.set_accent(row.get_active()))
        lb.append(self.accent_row)

        self.output_row = Adw.ComboRow(title="Kimenet", use_subtitle=True,
                                       model=Gtk.StringList.new([t for _, t in OUTPUT_CHOICES]))
        self.output_row.set_selected(OUTPUT_KEYS.index(self.m.output))
        self.output_row.set_tooltip_text("A pedál USB lejátszási csatornái, amelyekre a kattogás megy")
        self.output_row.connect("notify::selected", self._on_output)
        lb.append(self.output_row)
        box.append(lb)

        box.append(Gtk.Label(
            label="A pedál tempóját követi (TAP, preset, jelenet), a pedál USB hangjába szól. "
                  "Be/ki: Ctrl+K.",
            css_classes=["caption", "dim-label"], wrap=True, xalign=0, max_width_chars=40))
        return box

    def _on_volume(self, scale) -> None:
        v = int(round(scale.get_value()))
        self.vol_label.set_label(f"{v} %")
        self.m.set_volume(v, save=False)
        if self._save_id:
            GLib.source_remove(self._save_id)
        self._save_id = GLib.timeout_add(SAVE_DEBOUNCE_MS, self._save_now)

    def _save_now(self) -> bool:
        if self._save_id:
            GLib.source_remove(self._save_id)
            self._save_id = 0
            self.m.settings.save()
        return GLib.SOURCE_REMOVE

    def _on_output(self, row, _pspec) -> None:
        i = row.get_selected()
        if 0 <= i < len(OUTPUT_KEYS):
            self.m.set_output(OUTPUT_KEYS[i])

    # -- view --
    def update(self) -> None:
        m = self.m
        if m.tempo is None:
            tempo = f"Még nincs tempó a pedáltól – {FALLBACK_BPM} BPM-mel kattog"
            self.tempo_label.set_label(f"{FALLBACK_BPM} BPM?")
            self.tempo_label.set_tooltip_text(tempo)
        else:
            tempo = f"A pedál tempóját követi: {m.tempo} BPM"
            self.tempo_label.set_label(f"{m.tempo} BPM")
            self.tempo_label.set_tooltip_text(None)
        if m.playing:
            state = "Szól"
        elif m.enabled:
            state = "Indul… (a pedál hangkimenetét keresem)"
        else:
            state = "Ki"
        self.toggle.set_tooltip_text(f"Metronóm a pedál USB hangjába (Ctrl+K) – {state}\n{tempo}")
        (self.led.add_css_class if m.playing else self.led.remove_css_class)("armed")
        if not m.playing:
            self._clear_flash()

    def _on_beat(self, index: int, accent: bool, delay: float) -> None:
        GLib.timeout_add(int(delay * 1000), self._flash, accent)

    def _flash(self, accent: bool) -> bool:
        if not self.m.playing:
            return GLib.SOURCE_REMOVE
        self.flash(accent)
        return GLib.SOURCE_REMOVE

    def flash(self, accent: bool) -> None:
        """Light the LED for one beat (accent = beat 1, other colour)."""
        self._clear_flash()
        self.led.add_css_class("beat")
        if accent:
            self.led.add_css_class("accent")
        self._flash_id = GLib.timeout_add(FLASH_MS, self._unflash)

    def _unflash(self) -> bool:
        self._flash_id = 0
        self.led.remove_css_class("beat")
        self.led.remove_css_class("accent")
        return GLib.SOURCE_REMOVE

    def _clear_flash(self) -> None:
        if self._flash_id:
            GLib.source_remove(self._flash_id)
            self._flash_id = 0
        self.led.remove_css_class("beat")
        self.led.remove_css_class("accent")

    def _cancel_timers(self) -> None:
        self._clear_flash()
        self._save_now()
