"""Entry point: python3 -m takkapp [--device PATH] [--sink NAME] [--debug]"""

from __future__ import annotations

import argparse
import logging
import sys


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="takk", description="Takk - metronome for the NUX MG-300 MKII")
    p.add_argument("--device", metavar="PATH", help="rawmidi node instead of autodetect, e.g. /dev/snd/midiC2D0")
    p.add_argument("--sink", metavar="NAME", help="PipeWire sink name instead of the auto-detected NUX sink")
    p.add_argument("--debug", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from .app import TakkApp       # imports gi only when actually starting the UI
    return TakkApp(args).run(sys.argv[:1])


if __name__ == "__main__":
    sys.exit(main())
