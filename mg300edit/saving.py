"""Save-to-pedal state machine + automatic backups (GTK-free).

Verified sequence (research/FACTS.md "Presets, scenes, SAVE"):
  [read]    0C 00 <cur> <s>         s = 0..2   (edit buffer of the current preset;
                                               the current tempo goes into all 3 parts)
  [backup]  0B 00 <target> <s>      s = 0..2   -> backup file of what gets overwritten
  [write]   0B 01 <target> <s> <enc>  -> ack 0B 03 <target> <s> 01    (one part at a time)
  [commit]  07 01 <target> <target> ..  -> ack 07 03 <target> <target>
  [verify]  0B 00 <target> <s>      s = 0..2   -> must equal what was written

The job is driven by incoming messages (Controller routes 0C/0B replies and
acks here) and by Controller.tick() for timeouts; it never blocks and needs no
main loop of its own. Any timeout, NAK or readback mismatch stops the job with
an error - nothing further is sent.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Callable, Optional

from . import fileformat as F
from . import presets as PR
from . import protocol as P

log = logging.getLogger(__name__)

MAX_BACKUPS = 200
TEMPO_HI_OFFSET, TEMPO_LO_OFFSET = 0x5B, 0x5C   # per-scene tempo, bpm = hi << 7 | lo
STEP_TIMEOUT = 2.0          # s per awaited reply group (the pedal answers in ~2 ms)
RETRY_AFTER = 0.5           # s: re-send an unanswered write/commit/read (the pedal sometimes drops one -
MAX_RETRIES = 3             #    seen on hardware 2026-11-07: ~1 in 3 presets lost the 2nd scene write)


# ------------------------------------------------------------------ backups --

def default_backup_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(base) / "mg300edit" / "backups"


def _safe(text: str) -> str:
    keep = "".join(c if c.isalnum() or c in "-_" else "_" for c in text.replace(" ", "_")).strip("_")
    return keep or "preset"


def write_backup(directory, preset: int, scenes: list[bytes], *, now: Optional[float] = None,
                 keep: int = MAX_BACKUPS, reason: str = "") -> Path:
    """Write <dir>/<timestamp>_<label>_<name>.mg300.json and prune to `keep` files
    (oldest first by name = timestamp). Returns the path. Raises OSError."""
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    t = time.time() if now is None else now
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(t)) + f"-{int(t * 1000) % 1000:03d}"
    name = F.scene_name(scenes[0]) if scenes else ""
    label = PR.full_label(preset) if PR.is_valid(preset) else f"PC{preset}"
    path = d / f"{stamp}_{_safe(label)}_{_safe(name)}{F.EXTENSION}"
    n = 1
    while path.exists():
        n += 1
        path = d / f"{stamp}-{n}_{_safe(label)}_{_safe(name)}{F.EXTENSION}"
    data = F.PresetData(name=name, preset_index=preset if PR.is_valid(preset) else None,
                        scenes=[bytes(s) for s in scenes])
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(F.dump_preset(data), encoding="utf-8")
    os.replace(tmp, path)
    prune_backups(d, keep)
    return path


def list_backups(directory) -> list[Path]:
    d = Path(directory)
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_file() and p.name.endswith(F.EXTENSION))


def prune_backups(directory, keep: int = MAX_BACKUPS) -> int:
    files = list_backups(directory)
    extra = files[:max(0, len(files) - keep)]
    for p in extra:
        try:
            p.unlink()
        except OSError as e:
            log.warning("cannot delete old backup %s: %s", p, e)
    return len(extra)


def with_tempo(part: bytes, bpm: int) -> bytes:
    """`part` with its tempo bytes set to `bpm` (FACTS.md CORRECTION 2026-11-07:
    tempo is stored per scene at 0x5B/0x5C)."""
    b = bytearray(part)
    b[TEMPO_HI_OFFSET] = (int(bpm) >> 7) & 0x7F
    b[TEMPO_LO_OFFSET] = int(bpm) & 0x7F
    return bytes(b)


# ---------------------------------------------------------------- the job --

class SaveJob:
    """One save run. States: read -> backup -> write -> commit -> verify -> done | error."""

    READ, BACKUP, WRITE, COMMIT, VERIFY, DONE, ERROR = (
        "read", "backup", "write", "commit", "verify", "done", "error")

    STEP_TEXT = {
        READ: "Szerkesztett állapot beolvasása",
        BACKUP: "Biztonsági mentés",
        WRITE: "Írás",
        COMMIT: "Véglegesítés",
        VERIFY: "Ellenőrzés",
        DONE: "Kész",
        ERROR: "Hiba",
    }

    def __init__(self, ctl, target: int, *, name: Optional[str] = None,
                 parts: Optional[list[bytes]] = None,
                 transform: Optional[Callable[[list[bytes]], list[bytes]]] = None,
                 kind: str = "save", timeout: float = STEP_TIMEOUT,
                 tempo: Optional[int] = None):
        self.ctl = ctl
        self.target = target
        self.name = None if name is None else PR.sanitize_name(name)
        self.parts: list[Optional[bytes]] = list(parts) if parts is not None else [None] * 3
        self.from_edit = parts is None
        self.transform = transform
        self.kind = kind                     # save | save-as | rename | copy-scene | import
        self.timeout = timeout
        self.tempo = tempo                   # written into all 3 parts when set
        self.source_preset: Optional[int] = ctl.preset
        self.user_scene: Optional[int] = ctl.scene_index
        self.state = ""
        self.error: Optional[str] = None
        self.backup_path: Optional[Path] = None
        self.backup_scenes: dict[int, bytes] = {}
        self.verify_scenes: dict[int, bytes] = {}
        self.write_index = 0
        self.deadline = 0.0
        self.retry_at = 0.0
        self.retries = 0
        self.progress = (0, 6)               # (step, total) for a progress bar

    # -- status --
    @property
    def active(self) -> bool:
        return self.state not in ("", self.DONE, self.ERROR)

    @property
    def finished(self) -> bool:
        return self.state in (self.DONE, self.ERROR)

    @property
    def ok(self) -> bool:
        return self.state == self.DONE

    @property
    def to_current(self) -> bool:
        return self.target == self.source_preset

    @property
    def text(self) -> str:
        base = self.STEP_TEXT.get(self.state, self.state)
        if self.state == self.WRITE:
            return f"{base} {self.write_index + 1}/3"
        if self.state == self.ERROR:
            return f"{base}: {self.error}"
        return base

    def result_name(self) -> str:
        return F.scene_name(self.parts[0]) if self.parts and self.parts[0] else ""

    # -- helpers --
    def _send(self, msg: bytes, key=None) -> None:
        self.ctl.transport.send(msg, key=key, move_to_end=key is not None)

    def _arm(self) -> None:
        now = self.ctl.clock()
        self.deadline = now + self.timeout
        self.retry_at = now + RETRY_AFTER
        self.retries = 0

    def _enter(self, state: str, step: int) -> None:
        self.state = state
        self.progress = (step, 6)
        self._arm()
        self.ctl.emit("save", self)

    def fail(self, why: str) -> None:
        if self.finished:
            return
        self.error = why
        self.state = self.ERROR
        log.warning("save to %s failed: %s", self.target, why)
        self.ctl._save_finished(self)

    # -- driving --
    def start(self) -> None:
        if self.from_edit:
            self._enter(self.READ, 1)
            for s in range(PR.SCENES_PER_PRESET):
                self.ctl._request_edit_part(self.source_preset, s)
        else:
            self._parts_ready()

    def _parts_ready(self) -> None:
        try:
            parts = [bytes(p) for p in self.parts]
            if self.transform is not None:
                parts = [bytes(p) for p in self.transform(list(parts))]
            if len(parts) != PR.SCENES_PER_PRESET or any(len(p) != P.SCENE_SIZE for p in parts):
                raise ValueError(f"{PR.SCENES_PER_PRESET} x {P.SCENE_SIZE} byte scenes expected")
            if self.name is not None:
                parts = [PR.with_name(p, self.name) for p in parts]
            if self.tempo is not None:
                parts = [with_tempo(p, self.tempo) for p in parts]
        except (ValueError, TypeError) as e:
            self.fail(f"hibás adat ({e})")
            return
        self.parts = parts
        self._enter(self.BACKUP, 2)
        for s in range(PR.SCENES_PER_PRESET):
            self.ctl.request_stored(self.target, s, priority=0)

    def _write_next(self) -> None:
        self._enter(self.WRITE, 3)
        i = self.write_index
        self._send(P.set_stored_scene(self.target, i, self.parts[i]))

    def _resend(self) -> bool:
        """Re-send whatever the current state is still waiting for. False = nothing to resend."""
        if self.state == self.WRITE:
            self._send(P.set_stored_scene(self.target, self.write_index, self.parts[self.write_index]))
        elif self.state == self.COMMIT:
            self._send(P.commit(self.target))
        elif self.state in (self.BACKUP, self.VERIFY):
            got = self.backup_scenes if self.state == self.BACKUP else self.verify_scenes
            for sc in range(PR.SCENES_PER_PRESET):
                if sc not in got:
                    self.ctl.request_stored(self.target, sc, priority=0)
        elif self.state == self.READ:
            for sc in range(PR.SCENES_PER_PRESET):
                if self.parts[sc] is None:
                    self.ctl._request_edit_part(self.source_preset, sc)
        else:
            return False
        return True

    def tick(self, now: float) -> None:
        if not self.active:
            return
        if now > self.retry_at and self.retries < MAX_RETRIES and now <= self.deadline:
            if self._resend():
                self.retries += 1
                self.retry_at = now + RETRY_AFTER
                log.info("save to %s: no reply in %s, retry %d", self.target, self.state, self.retries)
                return
        if now > self.deadline:
            self.fail(f"nem jött válasz a pedáltól ({self.STEP_TEXT[self.state].lower()})")

    # -- incoming (return True when the message was consumed) --
    def on_edit_part(self, preset: int, scene: int, data: bytes) -> bool:
        if self.state != self.READ or preset != self.source_preset or not 0 <= scene < 3:
            return False
        self.parts[scene] = bytes(data[:P.SCENE_SIZE])
        if all(p is not None for p in self.parts):
            self._parts_ready()
        return True

    def on_stored(self, preset: int, scene: int, data: bytes) -> bool:
        if preset != self.target or not 0 <= scene < 3:
            return False
        if self.state == self.BACKUP:
            self.backup_scenes[scene] = bytes(data)
            if len(self.backup_scenes) == 3:
                try:
                    self.backup_path = self.ctl._write_backup(
                        self.target, [self.backup_scenes[s] for s in range(3)])
                except OSError as e:
                    self.fail(f"a biztonsági mentés nem írható ({e.strerror or e})")
                    return True
                self.write_index = 0
                self._write_next()
            return True
        if self.state == self.VERIFY:
            self.verify_scenes[scene] = bytes(data)
            if len(self.verify_scenes) == 3:
                bad = [s + 1 for s in range(3) if self.verify_scenes[s] != self.parts[s]]
                if bad:
                    self.fail("a visszaolvasott adat eltér (jelenet "
                              + ", ".join(map(str, bad)) + ")")
                else:
                    self.state = self.DONE
                    self.progress = (6, 6)
                    self.ctl._save_finished(self)
            return True
        return False

    def on_stored_ack(self, ack: P.StoredAck) -> bool:
        if self.state != self.WRITE or ack.preset != self.target:
            return False
        if ack.scene != self.write_index:
            return True                              # stray/duplicate ack: ignore
        if not ack.ok:
            self.fail(f"a pedál elutasította az írást (jelenet {ack.scene + 1})")
            return True
        self.write_index += 1
        if self.write_index < 3:
            self._write_next()
        else:
            self._enter(self.COMMIT, 4)
            self._send(P.commit(self.target))
        return True

    def on_commit_ack(self, preset: int) -> bool:
        if self.state != self.COMMIT or preset != self.target:
            return False
        self.ctl._after_commit(self)
        self._enter(self.VERIFY, 5)
        for s in range(PR.SCENES_PER_PRESET):
            self.ctl.request_stored(self.target, s, priority=0)
        return True

    def on_unsupported(self) -> bool:
        if self.state in (self.WRITE, self.COMMIT):
            self.fail("a pedál nem támogatja a parancsot")
            return True
        return False
