"""Tiny persistent settings: ~/.config/takk/settings.json (XDG_CONFIG_HOME)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from .click import DEFAULT_OUTPUT, DEFAULT_VOLUME, OUTPUTS

log = logging.getLogger(__name__)

DEFAULTS = {"volume": round(DEFAULT_VOLUME * 100), "output": DEFAULT_OUTPUT, "accent": True}


def path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "takk" / "settings.json"


def load(p: Path | None = None) -> dict:
    p = p or path()
    out = dict(DEFAULTS)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    if isinstance(data, dict):
        v = data.get("volume")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out["volume"] = int(min(100, max(0, round(v))))
        if data.get("output") in OUTPUTS:
            out["output"] = data["output"]
        if isinstance(data.get("accent"), bool):
            out["accent"] = data["accent"]
    return out


def save(values: dict, p: Path | None = None) -> None:
    p = p or path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({k: values[k] for k in DEFAULTS if k in values}, indent=1) + "\n",
                       encoding="utf-8")
        os.replace(tmp, p)
    except OSError as e:
        log.warning("cannot save settings to %s: %s", p, e)
