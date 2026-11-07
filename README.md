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
- Built-in metronome click (the Takk engine, see below): a "Takk" toggle next to TAP (Ctrl+K) plays a
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

## Takk (metronome)
A tiny companion app: reads the tempo from the pedal and plays a metronome click into the pedal's
USB audio, so you hear it in the pedal's headphone/output mix. When the tempo changes on the pedal
(TAP, preset or scene change) the click follows from the next beat.

```sh
./takk                # auto-detects the pedal (MIDI) and its PipeWire sink
./takk --sink NAME    # play into another PipeWire sink (e.g. a null sink for testing)
```
- Beat 1 of 4 is accented (higher pitch; can be turned off in the menu), volume 0–100 %, Space starts/stops.
- Output: pedal USB playback channels 1/2, 3/4 or all four. Volume, output and accent are saved in
  `~/.config/takk/settings.json`.
- Needs `pw-cat` and `pactl` (PipeWire). If the pedal's sink is muted in the system, Takk shows a banner
  with a "Feloldás" (unmute) button; it never changes the mute state by itself.
- **Takk and OpenTone can't run at the same time**: the pedal's MIDI port (ALSA rawmidi) is exclusive,
  so whichever starts second shows that the port is busy. That's why OpenTone has the same click built
  in: the **Takk** toggle in its header (Ctrl+K; settings via the small arrow, a right-click or the main
  menu). It only starts `pw-cat` while switched on, stops when the pedal's sink disappears or the window
  closes, and offers the same "Feloldás" button (in a toast) for a muted sink. Its volume, accent and
  output are saved in OpenTone's `~/.config/mg300edit/settings.json`; `./opentone --click-sink NAME`
  plays into another sink.
- Desktop entry: `io.github.barni.Takk.desktop` + `data/io.github.barni.Takk.svg` (install like OpenTone's).

## Protocol
Reverse-engineered on real hardware; see [`research/FACTS.md`](research/FACTS.md). Builds on public
MG-30/MG-400 protocol work by naikel (NUXFootSwitch), athedsx (open-mg30), voronkovd (nux-mg30-mcp)
and willianszwy (mg-400).

## License
MIT
