"""Small persistent UI settings: ~/.config/mg300edit/settings.json (XDG aware).

Unknown keys are kept, bad values fall back to the defaults, and a broken
file never stops the app."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

DEFAULTS: dict[str, Any] = {
    "developer_mode": False,
    "confirm_unsaved": True,      # ask Mentés / Elvetés / Mégse when leaving a modified preset
}


def default_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "mg300edit" / "settings.json"


class Settings:
    def __init__(self, path: Optional[os.PathLike] = None):
        self.path = Path(path) if path is not None else default_path()
        self.data: dict[str, Any] = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as e:
            log.warning("settings %s unreadable (%s), using defaults", self.path, e)
            return
        if not isinstance(raw, dict):
            return
        for k, v in raw.items():
            if k in DEFAULTS and type(v) is not type(DEFAULTS[k]):
                continue
            self.data[k] = v

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as e:
            log.warning("cannot write settings %s: %s", self.path, e)

    def get(self, key: str) -> Any:
        return self.data.get(key, DEFAULTS.get(key))

    def set(self, key: str, value: Any) -> None:
        if self.data.get(key) != value:
            self.data[key] = value
            self.save()
