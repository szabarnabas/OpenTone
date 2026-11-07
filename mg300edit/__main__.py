"""Entry point: python3 -m mg300edit [--fake] ..."""

from __future__ import annotations

import argparse
import logging
import sys


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="opentone", description="OpenTone - editor for the NUX MG-300 MKII")
    p.add_argument("--fake", action="store_true", help="use a built-in fake pedal (no hardware access)")
    p.add_argument("--fake-scene", metavar="FILE", help="decoded 126-byte scene for --fake "
                   "(default research/scene_a.bin)")
    p.add_argument("--device", metavar="PATH", help="rawmidi node to use instead of autodetect, "
                   "e.g. /dev/snd/midiC2D0")
    p.add_argument("--layout", metavar="JSON", help="scene layout spec (default: bundled placeholder)")
    p.add_argument("--sysex-gap", type=float, default=40.0, metavar="MS",
                   help="minimum gap around SysEx messages (default 40)")
    p.add_argument("--cc-gap", type=float, default=5.0, metavar="MS",
                   help="minimum gap between channel messages (default 5)")
    p.add_argument("--cc-map", metavar="JSON",
                   help='enable CC live edits from a map: {"AMP.GAIN": 28, "AMP.#0": 24, "AMP.switch": 3}')
    p.add_argument("--cc-from-layout", action="store_true",
                   help="(default, kept for compatibility) CC live edits from the layout's cc map")
    p.add_argument("--no-live", action="store_true",
                   help="read-only: never send parameter/model/bypass changes to the pedal")
    p.add_argument("--cc-scale", action="store_true",
                   help="scale param values to 0-127 for CCs (default: send raw values)")
    p.add_argument("--midi-channel", type=int, default=1, choices=range(1, 17), metavar="1-16")
    p.add_argument("--monitor", action="store_true", help="show the MIDI monitor at start")
    p.add_argument("--debug", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from .ui.app import MG300App   # imports gi only when actually starting the UI
    return MG300App(args).run(sys.argv[:1])


if __name__ == "__main__":
    sys.exit(main())
