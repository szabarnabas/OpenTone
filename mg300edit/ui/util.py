"""Small UI helpers: main-loop dispatch, feedback-loop guard, stylesheet."""

from __future__ import annotations

from pathlib import Path

from gi.repository import Gdk, GLib, Gtk


def glib_dispatch(fn, *args) -> None:
    """Thread-safe: run fn(*args) on the GTK main loop."""
    def _call():
        fn(*args)
        return GLib.SOURCE_REMOVE
    GLib.idle_add(_call)


class Guard:
    """`with guard:` marks UI updates that come from the pedal; widget signal
    handlers check `if guard: return` so nothing is echoed back."""

    def __init__(self):
        self._depth = 0

    def __enter__(self):
        self._depth += 1
        return self

    def __exit__(self, *exc):
        self._depth -= 1

    def __bool__(self):
        return self._depth > 0


STYLESHEET = Path(__file__).with_name("style.css")


def install_css() -> None:
    """Load ui/style.css for the whole display."""
    provider = Gtk.CssProvider()
    provider.load_from_path(str(STYLESHEET))
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
