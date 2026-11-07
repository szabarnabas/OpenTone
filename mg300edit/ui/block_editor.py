"""Editor for the selected block.

Header: colour accent, block abbreviation, model dropdown (searchable for long
lists), "Panel / Csúszkák" view toggle, on/off switch. Body ("panel" view): a
pedal-face card with vector knobs, selector buttons / dropdowns for `choices`
params, switches for `toggle01` params and vertical faders for fader blocks
(EQ). The "Csúszkák" view keeps the precise horizontal slider rows.

Every value control reports user changes through `_param_changed`, which drops
pedal-originated updates (guard) and repeats of the same integer value."""

from __future__ import annotations

from typing import Callable, Optional

from gi.repository import Adw, Gdk, Gtk

from ..layout import BlockSpec, ParamSpec, pretty_param_name
from .blockstyle import apply_block_class, block_description
from .knob import KnobControl

SELECTOR_BUTTONS_MAX = 4        # up to this many short choices: linked toggle buttons


def _dev_offset(p: ParamSpec) -> str:
    return f"offset {p.offset} (0x{p.offset:02X})" if p.offset is not None else "offset ?"


def _knob_origin(p: ParamSpec) -> Optional[float]:
    sc = p.unit_scale
    return sc.zero_raw(p.min, p.max) if sc is not None and sc.bipolar else None


def is_fader_block(params: list[ParamSpec]) -> bool:
    plain = [p for p in params if not p.choices and p.kind != "toggle01"]
    return bool(plain) and all(p.kind == "fader" for p in plain)


# ---------------------------------------------------------------- value controls
class ParamRow(Adw.ActionRow):
    """Slider view: one horizontal slider row (precise editing)."""

    def __init__(self, param: ParamSpec, value: Optional[int], guard,
                 on_change: Callable[[ParamSpec, int], None], dev: bool = False):
        super().__init__(title=pretty_param_name(param.name),
                         subtitle=_dev_offset(param) if dev else "")
        self.param = param
        self.guard = guard
        adj = Gtk.Adjustment(lower=param.min, upper=param.max, step_increment=1,
                             page_increment=max(1, (param.max - param.min) // 10))
        self.scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj,
                               draw_value=False, hexpand=True, width_request=260,
                               valign=Gtk.Align.CENTER)
        self.scale.set_round_digits(0)
        self.scale.update_property([Gtk.AccessibleProperty.LABEL], [param.name])
        if param.choices:
            for i, _c in enumerate(param.choices):
                self.scale.add_mark(param.min + i, Gtk.PositionType.BOTTOM, None)
        self.value_label = Gtk.Label(css_classes=["param-value"], xalign=1, width_chars=8)
        self.add_suffix(self.scale)
        self.add_suffix(self.value_label)
        self.set_value(param.default if value is None else value)
        self.scale.connect("value-changed", self._changed, on_change)

    def _changed(self, scale, on_change):
        v = int(round(scale.get_value()))
        self.value_label.set_label(self.param.display(v))
        if not self.guard:
            on_change(self.param, v)

    def set_value(self, v: int):
        """Programmatic update (from the pedal) - never echoes."""
        with self.guard:
            self.scale.set_value(v)
            self.value_label.set_label(self.param.display(v))


class _ComboParamRow(Adw.ComboRow):
    """Slider view: selector param."""

    def __init__(self, param: ParamSpec, value: Optional[int], guard, on_change, dev: bool):
        super().__init__(title=pretty_param_name(param.name),
                         subtitle=_dev_offset(param) if dev else "")
        self.param, self.guard = param, guard
        self.set_model(Gtk.StringList.new([str(c) for c in param.choices]))
        self.set_value(param.default if value is None else value)
        self.connect("notify::selected", lambda *_: None if self.guard else
                     on_change(param, param.min + self.get_selected()))

    def set_value(self, v: int):
        with self.guard:
            i = int(v) - self.param.min
            if 0 <= i < len(self.param.choices):
                self.set_selected(i)


class _SwitchParamRow(Adw.ActionRow):
    """Slider view: toggle01 param (Adw.SwitchRow is final, so a row + switch)."""

    def __init__(self, param: ParamSpec, value: Optional[int], guard, on_change, dev: bool):
        super().__init__(title=pretty_param_name(param.name),
                         subtitle=_dev_offset(param) if dev else "")
        self.param, self.guard = param, guard
        self.switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.switch.update_property([Gtk.AccessibleProperty.LABEL], [param.name])
        self.add_suffix(self.switch)
        self.set_activatable_widget(self.switch)
        self.set_value(param.min if value is None else value)
        self.switch.connect("notify::active", lambda sw, _p: None if self.guard else
                            on_change(param, param.max if sw.get_active() else param.min))

    def set_value(self, v: int):
        with self.guard:
            self.switch.set_active(int(v) == self.param.max)


class _PanelItem(Gtk.Box):
    """Panel view wrapper for non-knob controls: control centred in the knob
    area, name below."""

    def __init__(self, param: ParamSpec, control: Gtk.Widget, dev: bool):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                         css_classes=["panel-item"], valign=Gtk.Align.START)
        # same skeleton as KnobControl (readout line + 66 px body) so names line up
        self.append(Gtk.Label(label=" ", css_classes=["param-readout"]))
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER,
                      css_classes=["panel-item-body"], vexpand=False, height_request=66)
        control.set_valign(Gtk.Align.CENTER)
        control.set_vexpand(True)
        top.append(control)
        self.append(top)
        self.append(Gtk.Label(label=pretty_param_name(param.name), css_classes=["param-name"]))
        self.append(Gtk.Label(label=_dev_offset(param), css_classes=["dev-info"], visible=dev))


class SelectorControl(_PanelItem):
    """Panel view: `choices` param as linked toggle buttons or a dropdown."""

    def __init__(self, param: ParamSpec, value: Optional[int], guard, on_change, dev: bool):
        self.param, self.guard = param, guard
        self.buttons: list[Gtk.ToggleButton] = []
        self.dropdown: Optional[Gtk.DropDown] = None
        short = all(len(str(c)) <= 8 for c in param.choices)
        if len(param.choices) <= SELECTOR_BUTTONS_MAX and short:
            control = Gtk.Box(css_classes=["linked", "selector-buttons"], halign=Gtk.Align.CENTER)
            group = None
            for i, c in enumerate(param.choices):
                b = Gtk.ToggleButton(label=str(c))
                if group is not None:
                    b.set_group(group)
                group = group or b
                b.connect("toggled", self._toggled, i, on_change)
                self.buttons.append(b)
                control.append(b)
            control.update_property([Gtk.AccessibleProperty.LABEL], [param.name])
        else:
            control = Gtk.DropDown(model=Gtk.StringList.new([str(c) for c in param.choices]),
                                   halign=Gtk.Align.CENTER)
            control.update_property([Gtk.AccessibleProperty.LABEL], [param.name])
            control.connect("notify::selected", lambda dd, _p: None if self.guard else
                            on_change(param, param.min + dd.get_selected()))
            self.dropdown = control
        super().__init__(param, control, dev)
        self.set_value(param.default if value is None else value)

    def _toggled(self, b, i, on_change):
        if b.get_active() and not self.guard:
            on_change(self.param, self.param.min + i)

    def set_value(self, v: int):
        i = int(v) - self.param.min
        with self.guard:
            if self.dropdown is not None and 0 <= i < len(self.param.choices):
                self.dropdown.set_selected(i)
            for j, b in enumerate(self.buttons):
                b.set_active(j == i)


class SwitchControl(_PanelItem):
    """Panel view: toggle01 param as a switch."""

    def __init__(self, param: ParamSpec, value: Optional[int], guard, on_change, dev: bool):
        self.param, self.guard = param, guard
        self.switch = Gtk.Switch(halign=Gtk.Align.CENTER)
        self.switch.update_property([Gtk.AccessibleProperty.LABEL], [param.name])
        self.switch.connect("notify::active", lambda sw, _p: None if self.guard else
                            on_change(param, param.max if sw.get_active() else param.min))
        super().__init__(param, self.switch, dev)
        self.set_value(param.min if value is None else value)

    def set_value(self, v: int):
        with self.guard:
            self.switch.set_active(int(v) == self.param.max)


class FaderControl(Gtk.Box):
    """Panel view for fader blocks (graphic EQ): vertical slider with readout."""

    def __init__(self, param: ParamSpec, value: Optional[int], guard, on_change, dev: bool):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                         css_classes=["fader-control"])
        self.param, self.guard = param, guard
        self.readout = Gtk.Label(css_classes=["param-readout"], width_chars=5)
        adj = Gtk.Adjustment(lower=param.min, upper=param.max, step_increment=1,
                             page_increment=max(1, (param.max - param.min) // 10))
        self.scale = Gtk.Scale(orientation=Gtk.Orientation.VERTICAL, adjustment=adj,
                               inverted=True, draw_value=False, height_request=168,
                               halign=Gtk.Align.CENTER, css_classes=["eq-fader"])
        self.scale.set_round_digits(0)
        self.scale.update_property([Gtk.AccessibleProperty.LABEL], [param.name])
        mid = (param.min + param.max) / 2
        self.scale.add_mark(mid, Gtk.PositionType.RIGHT, None)
        name = Gtk.Label(label=pretty_param_name(param.name), css_classes=["param-name"])
        self.append(self.readout)
        self.append(self.scale)
        self.append(name)
        self.append(Gtk.Label(label=f"@{param.offset}", css_classes=["dev-info"], visible=dev))
        reset = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        reset.connect("pressed", self._pressed)
        self.scale.add_controller(reset)
        self.set_value(param.default if value is None else value)
        self.scale.connect("value-changed", self._changed, on_change)

    def _pressed(self, gesture, n_press, _x, _y):
        if n_press == 2:
            self.scale.set_value(self.param.default)

    def _changed(self, scale, on_change):
        v = int(round(scale.get_value()))
        self._label(v)
        if not self.guard:
            on_change(self.param, v)

    def _label(self, v: int):
        text = self.param.display(v)
        self.readout.set_label(text)
        self.set_tooltip_text(f"{self.param.name}: {text}\nDupla kattintás: alapérték "
                              f"({self.param.display(self.param.default)})")

    def set_value(self, v: int):
        with self.guard:
            self.scale.set_value(v)
            self._label(int(v))


class _KnobItem(KnobControl):
    def __init__(self, param: ParamSpec, value: Optional[int], guard, on_change, dev: bool):
        self.param = param
        v = param.default if value is None else value
        super().__init__(pretty_param_name(param.name), param.min, param.max, v, param.default,
                         param.display, origin=_knob_origin(param),
                         on_change=lambda x: on_change(param, x),
                         dev_text=_dev_offset(param) if dev else "")
        self.guard = guard

    def set_value(self, v: int):
        with self.guard:
            super().set_value(v)


def make_panel_control(param: ParamSpec, value, guard, on_change, dev: bool, faders: bool):
    if param.choices:
        return SelectorControl(param, value, guard, on_change, dev)
    if param.kind == "toggle01" and param.max - param.min == 1:
        return SwitchControl(param, value, guard, on_change, dev)
    if faders:
        return FaderControl(param, value, guard, on_change, dev)
    return _KnobItem(param, value, guard, on_change, dev)


def make_slider_row(param: ParamSpec, value, guard, on_change, dev: bool):
    if param.choices:
        return _ComboParamRow(param, value, guard, on_change, dev)
    if param.kind == "toggle01" and param.max - param.min == 1:
        return _SwitchParamRow(param, value, guard, on_change, dev)
    return ParamRow(param, value, guard, on_change, dev)


# ---------------------------------------------------------------- the editor
class BlockEditor(Gtk.Box):
    VIEW_PANEL, VIEW_SLIDERS = "panel", "sliders"

    def __init__(self, guard, on_model: Callable[[BlockSpec, int], None],
                 on_param: Callable[[BlockSpec, ParamSpec, int], None],
                 on_toggle: Optional[Callable[[BlockSpec, bool], None]] = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["block-editor"])
        self.guard = guard
        self.on_model = on_model
        self.on_param = on_param
        self.on_toggle = on_toggle
        self.block: Optional[BlockSpec] = None
        self._shown: Optional[tuple] = None         # (block id, model, view, dev) on screen
        self.rows: dict[str, Gtk.Widget] = {}       # param name -> control with set_value()
        self._last: dict[str, int] = {}
        self._model_indices: list[int] = []
        self._scene: Optional[bytes] = None
        self.dev = False
        self.view = self.VIEW_PANEL                 # remembered in-memory across blocks
        self.switch: Optional[Gtk.Switch] = None
        self.face: Optional[Gtk.Widget] = None
        self.header: Optional[Gtk.Widget] = None

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                               transition_duration=120, vexpand=True)
        self.status = Adw.StatusPage(vexpand=True)
        self.stack.add_named(self.status, "status")
        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        clamp = Adw.Clamp(maximum_size=980, tightening_threshold=720, margin_top=14,
                          margin_bottom=18, margin_start=16, margin_end=16)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        clamp.set_child(self.content)
        scroller.set_child(clamp)
        self.stack.add_named(scroller, "block")
        self.append(self.stack)
        self.show_disconnected()

    # ---------- public API ----------
    def set_developer_mode(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self.dev:
            return
        self.dev = enabled
        self._rerender()

    def set_view(self, view: str) -> None:
        if view in (self.VIEW_PANEL, self.VIEW_SLIDERS) and view != self.view:
            self.view = view
            self._rerender()

    def show_empty(self, title: str, desc: str, icon: str = "audio-card-symbolic"):
        """Generic empty state (e.g. no block selected)."""
        self._clear()
        self.block = None
        self._status_page(title, desc, icon)

    def show_no_data(self, connected: bool):
        """Nothing to edit: spinner while reading from a connected pedal,
        otherwise the "no pedal" page."""
        if connected:
            self.show_loading()
        else:
            self.show_disconnected()

    def show_disconnected(self):
        self._clear()
        self.block = None
        self._status_page("Nincs csatlakoztatva pedál",
                          "Csatlakoztasd a NUX MG-300 MKII-t USB-kábellel – az editor "
                          "magától felismeri és beolvassa.",
                          "network-offline-symbolic")

    def show_loading(self, text: str = "Beolvasás a pedálról…"):
        self._clear()
        self.block = None
        self.status.set_icon_name(None)
        self.status.set_paintable(Adw.SpinnerPaintable.new(self.status))
        self.status.set_title(text)
        self.status.set_description("A szerkesztőpuffer és a blokklánc betöltése.")
        self.stack.set_visible_child_name("status")

    def show_block(self, block: BlockSpec, scene: bytes):
        """Show `block` of `scene`. When the same block/model is already on
        screen only the values are refreshed, so a re-read from the pedal does
        not steal keyboard focus or interrupt a knob drag."""
        current = block.current_model(scene)
        key = (block.id, current.index if current else None, self.view, self.dev)
        if key == self._shown and self.block is block and not self.dev \
                and self.stack.get_visible_child_name() == "block":
            self._scene = scene
            self._refresh_values(current)
            return
        self._clear()
        self._shown = key
        self.block = block
        self._scene = scene
        self.stack.set_visible_child_name("block")

        params = current.params if current else []
        self.content.append(self._build_header(block, scene, current))
        if self.dev:
            self.content.append(self._dev_line(block, scene))

        values = {p.name: p.read(scene) for p in params}
        self._last = {k: v for k, v in values.items() if v is not None}
        cb = lambda param, v, b=block: self._param_changed(b, param, v)   # noqa: E731
        if not params:
            body = Adw.StatusPage(title="Ehhez a modellhez nincs ismert paraméter",
                                  description="A layout JSON bővítésével szerkeszthetővé tehető.",
                                  icon_name="dialog-question-symbolic", css_classes=["compact"])
            self.content.append(body)
        elif self.view == self.VIEW_SLIDERS:
            group = Adw.PreferencesGroup()
            for p in params:
                row = make_slider_row(p, values[p.name], self.guard, cb, self.dev)
                self.rows[p.name] = row
                group.add(row)
            self.content.append(group)
        else:
            self.content.append(self._build_face(block, params, values, cb))
            hint = ("Fader: húzás · görgő · nyilak · dupla kattintás = alapérték"
                    if is_fader_block(params) else
                    "Gomb: húzás fel/le (Shift = finom) · görgő · nyilak, PgUp/PgDn, "
                    "Home/End · dupla kattintás = alapérték")
            self.content.append(Gtk.Label(label=hint, css_classes=["editor-hint"], wrap=True,
                                          justify=Gtk.Justification.CENTER))
        self._sync_bypass()

    def update_param(self, block_id: str, name: str, value: int):
        if self.block and self.block.id == block_id and name in self.rows:
            self._last[name] = int(value)
            self.rows[name].set_value(value)

    def refresh_bypass(self, block_id: str) -> None:
        if self.block is not None and self.block.id == block_id:
            self._sync_bypass()

    # ---------- building ----------
    def _build_header(self, block: BlockSpec, scene: bytes, current) -> Gtk.Widget:
        header = Gtk.Box(spacing=12, css_classes=["block-header"])
        apply_block_class(header, block.id if block.known else "")
        chip = Gtk.Label(label=block.title, css_classes=["blk-chip"], valign=Gtk.Align.CENTER)
        header.append(chip)

        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, spacing=2)
        desc = block_description(block.id)
        titles.append(Gtk.Label(label=desc or block.id, xalign=0, css_classes=["block-desc"]))
        titles.append(self._build_model_dropdown(block, current))
        header.append(titles)
        header.append(Gtk.Box(hexpand=True))

        toggle = Adw.ToggleGroup(valign=Gtk.Align.CENTER, css_classes=["view-toggle"])
        toggle.add(Adw.Toggle(name=self.VIEW_PANEL, label="Panel",
                              tooltip="Gombok és faderek – pedálszerű nézet"))
        toggle.add(Adw.Toggle(name=self.VIEW_SLIDERS, label="Csúszkák",
                              tooltip="Vízszintes csúszkák – pontos szerkesztés"))
        toggle.set_active_name(self.view)
        toggle.connect("notify::active-name",
                       lambda tg, _p: tg.get_active_name() and self.set_view(tg.get_active_name()))
        header.append(toggle)

        self.switch = Gtk.Switch(valign=Gtk.Align.CENTER, tooltip_text="Blokk be/ki")
        self.switch.update_property([Gtk.AccessibleProperty.LABEL], [f"{block.title} be/ki"])
        self.switch.connect("notify::active", self._switch_toggled)
        header.append(self.switch)
        self.header = header
        return header

    def _build_model_dropdown(self, block: BlockSpec, current) -> Gtk.Widget:
        models = list(block.models)
        if current and not current.known:
            models.append(current)
        models.sort(key=lambda m: m.index)
        self._model_indices = [m.index for m in models]

        def label(m) -> str:
            if not self.dev:
                return m.name
            n = m.index & ~block.alt_bit if block.alt_bit else m.index
            return f"{n}: {m.name}"
        dd = Gtk.DropDown(model=Gtk.StringList.new([label(m) for m in models]),
                          halign=Gtk.Align.START, css_classes=["model-dropdown"],
                          tooltip_text="Modell")
        dd.update_property([Gtk.AccessibleProperty.LABEL], [f"{block.title} modell"])
        if len(models) > 10:
            dd.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
            dd.set_enable_search(True)
            dd.set_search_match_mode(Gtk.StringFilterMatchMode.SUBSTRING)
        if current and current.index in self._model_indices:
            dd.set_selected(self._model_indices.index(current.index))
        dd.set_sensitive(bool(models))
        dd.connect("notify::selected", self._model_changed)
        return dd

    def _dev_line(self, block: BlockSpec, scene: bytes) -> Gtk.Widget:
        off = f"model byte @ {block.model_offset}" if block.model_offset is not None else "model offset unknown"
        mb = block.model_byte(scene)
        byte = f" = 0x{mb:02X}" if mb is not None else ""
        return Gtk.Label(label=f"{block.id} · chain id {block.chain_id} · {off}{byte}",
                         xalign=0, css_classes=["dev-info", "dev-line"], selectable=True)

    def _build_face(self, block: BlockSpec, params, values, cb) -> Gtk.Widget:
        face = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, css_classes=["pedal-face"])
        apply_block_class(face, block.id if block.known else "")
        faders = is_fader_block(params)
        if faders:
            face.add_css_class("faders")
        wrap = Adw.WrapBox(child_spacing=22 if not faders else 4, line_spacing=16,
                           align=0.5, halign=Gtk.Align.FILL)
        for p in params:
            ctl = make_panel_control(p, values[p.name], self.guard, cb, self.dev, faders)
            self.rows[p.name] = ctl
            wrap.append(ctl)
        face.append(wrap)
        self.face = face
        return face

    # ---------- state ----------
    def _sync_bypass(self, requested: Optional[bool] = None):
        """Show the block's on/off state. `requested` = the user just flipped the
        header switch: style for that state and leave the switch alone (no
        re-entrant set_active from inside its own handler)."""
        if self.block is None or self._scene is None or self.switch is None:
            return
        if requested is None:
            bypassed = self.block.is_bypassed(self._scene)
            with self.guard:
                self.switch.set_sensitive(bypassed is not None)
                self.switch.set_active(bypassed is False)
        else:
            bypassed = requested
        for w in (self.face, self.header):
            if w is not None:
                (w.add_css_class if bypassed else w.remove_css_class)("bypassed")

    def _refresh_values(self, current) -> None:
        for p in (current.params if current else []):
            v = p.read(self._scene)
            row = self.rows.get(p.name)
            if v is not None and row is not None and self._last.get(p.name) != v:
                self._last[p.name] = v
                row.set_value(v)
        self._sync_bypass()

    def _clear(self):
        while (child := self.content.get_first_child()) is not None:
            self.content.remove(child)
        self.rows = {}
        self._shown = None
        self.switch = self.face = self.header = None

    def _status_page(self, title: str, desc: str, icon: str):
        self.status.set_paintable(None)
        self.status.set_icon_name(icon)
        self.status.set_title(title)
        self.status.set_description(desc)
        self.stack.set_visible_child_name("status")

    def _rerender(self):
        if self.block is not None and self._scene is not None:
            with self.guard:
                self.show_block(self.block, self._scene)

    # ---------- user -> controller ----------
    def _param_changed(self, block: BlockSpec, param: ParamSpec, v: int):
        if self.guard:
            return
        v = int(v)
        if self._last.get(param.name) == v:
            return
        self._last[param.name] = v
        self.on_param(block, param, v)

    def _model_changed(self, dd, _pspec):
        if self.guard or self.block is None:
            return
        i = dd.get_selected()
        if 0 <= i < len(self._model_indices):
            self.on_model(self.block, self._model_indices[i])

    def _switch_toggled(self, sw, _pspec):
        if self.guard or self.block is None:
            return
        bypassed = not sw.get_active()
        if self.on_toggle is not None:
            self.on_toggle(self.block, bypassed)
        self._sync_bypass(requested=bypassed)
