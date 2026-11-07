# OpenTone

A native Linux editor for the **NUX MG-300 MKII** guitar multi-effects pedal — an open alternative to the
vendor's Windows/macOS-only QuickTone. Python + GTK4/libadwaita, talks to the pedal over class-compliant
USB MIDI (raw ALSA), no drivers or extra Python packages needed.

> Not affiliated with or endorsed by NUX / Cherub Technology. "NUX", "MG-300" and "QuickTone" are
> trademarks of their respective owners. Use at your own risk — saving writes to your pedal's presets.

## Features
- Live editing over MIDI CC (low latency, two-way: knob changes on the pedal show up in the app)
- Colour-coded signal chain with on/off LEDs and drag & drop block order
- Model selection, vector knobs / sliders, graphic-EQ faders, cab mic & position
- Preset list (user + factory, 1A–9D), scenes 1/2/3, tempo + tap
- Built-in metronome click (see below): a "Takk" toggle next to TAP (Ctrl+K) plays a
  click into the pedal's USB audio that follows the pedal tempo live (TAP on the pedal, preset/scene
  change, the app's tempo field and TAP); beat LED in the header, volume / accent / output in its popover
- Save / Save As / Rename to user presets with automatic backups and read-back verification
- Export / import presets (`.mg300.json`, reads QuickTone `.mg300MK2patch`)
- MIDI monitor with raw SysEx send, and a `--fake` pedal for development without hardware

## Requirements
Linux with Python 3.11+, GTK 4 and libadwaita with PyGObject (preinstalled on Fedora Workstation;
on Debian/Ubuntu: `python3-gi gir1.2-gtk-4.0 gir1.2-adw-1`). Your user needs access to `/dev/snd/midiC*D0`
(normally granted to the logged-in user).

## Run
```sh
./opentone            # connect to the pedal (auto-detected via USB MIDI "NUX MG-300V2")
./opentone --fake     # demo mode, no hardware
./opentone --monitor  # show the MIDI monitor
python3 -m unittest   # tests
```
Optional desktop entry: copy `io.github.barni.OpenTone.desktop` to `~/.local/share/applications/`,
`data/io.github.barni.OpenTone.svg` to `~/.local/share/icons/hicolor/scalable/apps/`, and link
`opentone` into a directory on your `PATH`.

## Built-in click (Takk)
The **Takk** toggle in the header (Ctrl+K) plays a metronome click into the pedal's USB audio, so you hear it
in the pedal's headphone/output mix. It follows the pedal tempo live: TAP on the pedal (the pedal announces
every tap), preset and scene changes, and the app's own tempo field / TAP button; a new tempo applies from
the next beat. The LED next to the toggle flashes on each beat (beat 1 in amber).
- Settings (small arrow, right-click or main menu): volume (default 100 %), accent on beat 1, output.
  Output "USB 1/2" reaches the headphones and the main output; "USB 3/4" is only used in the pedal's Reamp
  USB mode. Settings are saved in `~/.config/mg300edit/settings.json`.
- Needs `pw-cat` and `pactl` (PipeWire); `pw-cat` only runs while the click is on. If the pedal's sink is
  muted in the system, a toast offers a "Feloldás" (unmute) button — the mute state is never changed
  automatically. `./opentone --click-sink NAME` plays into another sink.

## Protocol
Reverse-engineered on real hardware; see [`research/FACTS.md`](research/FACTS.md). Builds on public
MG-30/MG-400 protocol work by naikel (NUXFootSwitch), athedsx (open-mg30), voronkovd (nux-mg30-mcp)
and willianszwy (mg-400).

## License
MIT
