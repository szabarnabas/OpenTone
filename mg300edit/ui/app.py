"""Adw.Application wiring: transport + layout + controller + window."""

from __future__ import annotations

import json
import logging

from gi.repository import Adw, Gio

from .. import APP_ID, __version__
from .. import protocol as P
from ..controller import Controller
from ..layout import Layout
from .util import glib_dispatch, install_css
from .window import MainWindow

log = logging.getLogger(__name__)


def make_param_writer(args, layout: Layout) -> P.ParamWriter:
    """Live-edit strategy. Default: CC writes from the layout's cc map (verified
    on hardware 2026-11-06: CC value = raw 0-100 param value, applied to the
    edit buffer instantly). --no-live gives a read-only editor."""
    ch = args.midi_channel - 1
    if args.no_live:
        return P.ParamWriter()
    if args.cc_map:
        with open(args.cc_map, encoding="utf-8") as f:
            cc_map = {k: int(v) for k, v in json.load(f).items() if not k.startswith("_")}
        return P.CCParamWriter(cc_map, channel=ch, scale=args.cc_scale)
    return P.CCParamWriter.from_layout(layout, channel=ch, scale=args.cc_scale)


def make_transport(args):
    kw = dict(sysex_gap=args.sysex_gap / 1000.0, channel_gap=args.cc_gap / 1000.0,
              dispatch=glib_dispatch)
    if args.fake:
        from ..fake import FakeTransport
        return FakeTransport(scene_path=args.fake_scene, **kw)
    from ..transport import RawMidiTransport
    return RawMidiTransport(device_path=args.device, **kw)


class MG300App(Adw.Application):
    def __init__(self, args):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.args = args
        self.ctl = None
        self.win = None
        for name, cb, accels in (("quit", lambda *_: self.quit(), ["<Control>q"]),
                                 ("about", self._about, [])):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", cb)
            self.add_action(a)
            if accels:
                self.set_accels_for_action(f"app.{name}", accels)

    def do_startup(self):
        Adw.Application.do_startup(self)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.PREFER_DARK)
        install_css()

    def do_activate(self):
        if self.win:
            self.win.present()
            return
        layout = Layout.load(self.args.layout)
        self.ctl = Controller(make_transport(self.args), layout, make_param_writer(self.args, layout))
        self.win = MainWindow(self, self.ctl, show_monitor=self.args.monitor)
        self.win.monitor.note(f"layout: {layout.source} ({len(layout.blocks)} blocks); "
                              f"live-edit writer: {self.ctl.param_writer.name}")
        self.win.present()
        self.ctl.start()

    def do_shutdown(self):
        if self.ctl:
            self.ctl.stop()
        Adw.Application.do_shutdown(self)

    def _about(self, *_):
        Adw.AboutDialog(application_name="OpenTone", version=__version__,
                        comments="Nem hivatalos szerkesztő a NUX MG-300 MKII multieffekthez",
                        application_icon="audio-card-symbolic").present(self.win)
