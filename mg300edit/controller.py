"""Editor state + message handling, independent of GTK.

Runs on the main loop (transport callbacks are dispatched there). The UI
listens to events and calls the action methods; incoming pedal data never
causes anything to be sent back except read-only GET refreshes.

Events (controller.connect(name, fn)):
  status(connected, text)  identity(Identity)  preset(int)  tempo(int)
  scene()  - edit buffer (or block order) changed, redraw everything
  param(block_id, param_name, value)  - single value changed from the pedal side
  info(text)  - something worth showing (unsupported cmd, ack, ...)
  name(preset, name)  - a preset's (scene 0 / edit buffer) name became known or changed
  names()             - a load_preset_names() batch completed
  stored(preset, scene, data)  - a stored scene (0B) was read
  scene_index(int|None) - the pedal's ACTIVE scene (0..2) changed (None = unknown)
  modified(bool)      - edit buffer has unsaved changes / was saved or reloaded
  save(SaveJob)       - a save made progress or finished (job.finished / job.ok)

Preset library: 0B GETs are queued at LOW priority so live CC edits overtake
them; repeated requests for the same (preset, scene) collapse (keyed).

Scenes (FACTS.md "CORRECTION 2026-11-07"): the ACTIVE scene is switched with
CC80 (B0 50 <s>) and live CC edits land in the active scene's part. It is
tracked from our own CC80 sends, the pedal's B0 50 notifications and PC
reloads (-> scene 0) - never from 0C byte 7 (that is only the "view" part a
plain 0C returns). The editor shows the active part, read with 0C <p> <s>.

Writes to stored presets happen ONLY through save_preset() (and its wrappers
copy_scene / import_to_slot), which refuses factory presets unless
allow_factory=True, backs up the target first and verifies by readback
(see saving.py). Timeouts are driven by tick(), which the UI calls every
~100 ms (tests call it with an injected clock).
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Callable, Optional

from . import apply as A
from . import presets as PR
from . import protocol as P
from . import saving as S
from .layout import BlockSpec, Layout
from .writer import PRIO_LOW, PRIO_NORMAL

log = logging.getLogger(__name__)


class StoredFetch:
    """Collects 0B replies for a set of (preset, scene) keys.

    on_done(fetch) fires once all keys arrived, or when finish() is called
    (e.g. by a UI timeout) - check fetch.missing then. cancel() drops it silently.
    """

    def __init__(self, ctl: "Controller", keys, on_done: Optional[Callable] = None,
                 on_progress: Optional[Callable] = None):
        self.ctl = ctl
        self.keys: list[tuple[int, int]] = list(dict.fromkeys(keys))
        self.results: dict[tuple[int, int], bytes] = {}
        self.on_done = on_done
        self.on_progress = on_progress
        self.done = False

    @property
    def missing(self) -> list[tuple[int, int]]:
        return [k for k in self.keys if k not in self.results]

    def _feed(self, preset: int, scene: int, data: bytes) -> None:
        key = (preset, scene)
        if self.done or key not in self.keys:
            return
        self.results[key] = bytes(data)
        if self.on_progress:
            self.on_progress(len(self.results), len(self.keys))
        if len(self.results) == len(self.keys):
            self.finish()

    def finish(self) -> None:
        if self.done:
            return
        self.done = True
        if self in self.ctl._fetches:
            self.ctl._fetches.remove(self)
        if self.on_done:
            self.on_done(self)

    def cancel(self) -> None:
        self.done = True
        if self in self.ctl._fetches:
            self.ctl._fetches.remove(self)


class Controller:
    TOUCH_HOLD = 0.6   # s: local edits win over a racing edit-buffer reply
    RESTORE_HOLD = 1.5  # s: ignore other scene reports while a scene restore is in flight

    def __init__(self, transport, layout: Layout, param_writer: Optional[P.ParamWriter] = None,
                 *, clock: Callable[[], float] = time.monotonic, backup_dir=None,
                 save_timeout: float = S.STEP_TIMEOUT):
        self._touched: dict[int, float] = {}
        self.clock = clock
        self.backup_dir = backup_dir            # None -> saving.default_backup_dir()
        self.save_timeout = save_timeout
        self.transport = transport
        self.layout = layout
        self.param_writer = param_writer or P.ParamWriter()
        self._listeners: dict[str, list[Callable]] = defaultdict(list)

        self.connected = False
        self.identity: Optional[P.Identity] = None
        self.preset: Optional[int] = None
        self.scene_index: Optional[int] = None
        self.tempo: Optional[int] = None
        self.tempo_extra = 0                    # byte 8 of the last 03 reply, sent back on set
        self.scene: Optional[bytearray] = None
        self.block_order: list[int] = []
        self.modified = False
        self.save_job: Optional[S.SaveJob] = None
        self._restore: Optional[tuple[int, float]] = None    # (scene, until) after a reload
        self._part_reads: set[tuple[int, int]] = set()       # 0C <p> <s> reads in flight

        # preset library
        self.preset_names: dict[int, str] = {}
        self._names_pending: set[int] = set()
        self._fetches: list[StoredFetch] = []

        transport.on_messages = self.handle_messages
        transport.on_status = self._on_status

    # -- events --
    def connect(self, name: str, fn: Callable) -> None:
        self._listeners[name].append(fn)

    def emit(self, name: str, *args) -> None:
        for fn in list(self._listeners[name]):
            try:
                fn(*args)
            except Exception:  # noqa: BLE001 - a UI bug must not kill message handling
                log.exception("listener for %s failed", name)

    # -- lifecycle --
    def start(self) -> None:
        self.transport.start()

    def stop(self) -> None:
        self.transport.stop()

    def _on_status(self, connected: bool, text: str) -> None:
        was = self.connected
        self.connected = connected
        self.emit("status", connected, text)
        if connected and not was:
            self.transport.send(P.identity_request(), key="get:identity")
            self.refresh()
            self.load_preset_names()        # low priority: after the refresh

    # -- requests (read-only GETs) --
    def _get(self, msg: bytes, name: str) -> None:
        # keyed + move_to_end: repeated refreshes collapse, and a GET always
        # goes out after any program change queued before it
        self.transport.send(msg, key=f"get:{name}", move_to_end=True)

    def refresh(self) -> None:
        self._get(P.get_current(), "15")
        self._get(P.get_tempo(), "03")
        self._get(P.get_block_order(), "0D")
        self._get(self._edit_buffer_request(), "0C")

    def refresh_edit_buffer(self) -> None:
        """Re-read the ACTIVE scene's part (0C <p> <s>); while the active scene
        is unknown (just connected) the pedal's default 0C part is shown."""
        self._get(self._edit_buffer_request(), "0C")
        self._get(P.get_tempo(), "03")

    def _edit_buffer_request(self) -> bytes:
        if self.preset is not None and self.scene_index is not None:
            return P.get_edit_buffer(self.preset, self.scene_index)
        return P.get_edit_buffer()

    # -- preset library (read-only 0B GETs) --
    def request_stored(self, preset: int, scene: int, priority: int = PRIO_LOW) -> None:
        self.transport.send(P.get_stored_scene(preset, scene), key=f"get:0B:{preset}:{scene}",
                            priority=priority)

    def load_preset_names(self, presets=None) -> None:
        """Queue scene-0 reads for all 72 presets (low priority, keyed so that
        repeated calls collapse). Emits name(...) per preset and names() at the end."""
        presets = [p for p in (presets if presets is not None else range(PR.PRESET_COUNT))
                   if PR.is_valid(p)]
        self._names_pending.update(presets)
        for p in presets:
            self.request_stored(p, 0)

    def fetch_stored(self, keys, on_done: Optional[Callable] = None,
                     on_progress: Optional[Callable] = None,
                     priority: int = PRIO_LOW) -> StoredFetch:
        """Read stored scenes [(preset, scene), ...]; see StoredFetch."""
        f = StoredFetch(self, keys, on_done, on_progress)
        self._fetches.append(f)
        for p, sc in f.keys:
            self.request_stored(p, sc, priority=priority)
        if not f.keys:
            f.finish()
        return f

    def _set_name(self, preset: int, name: str) -> None:
        if not PR.is_valid(preset):
            return
        if self.preset_names.get(preset) != name:
            self.preset_names[preset] = name
            self.emit("name", preset, name)

    # -- state helpers --
    @property
    def live(self) -> bool:
        """False in read-only mode (--no-live): no edits/tempo/order/save are sent."""
        return type(self.param_writer) is not P.ParamWriter

    @property
    def busy(self) -> bool:
        return self.save_job is not None and self.save_job.active

    def _set_modified(self, value: bool) -> None:
        if value != self.modified:
            self.modified = value
            self.emit("modified", value)

    def _set_scene_index(self, scene: Optional[int]) -> None:
        if scene != self.scene_index:
            self.scene_index = scene
            self.emit("scene_index", scene)

    def tick(self, now: Optional[float] = None) -> None:
        """Timeouts (save job, scene restore). Call every ~100 ms."""
        now = self.clock() if now is None else now
        if self.save_job is not None and self.save_job.active:
            self.save_job.tick(now)
        if self._restore is not None and now > self._restore[1]:
            self._restore = None
            self.refresh_edit_buffer()          # whatever the pedal ended up on

    # -- scenes --
    def _send_scene_cc(self, scene: int) -> None:
        ch = getattr(self.param_writer, "channel", 0)
        self.transport.send(P.control_change(P.CC_SCENE, scene, ch), key="set:scene",
                            move_to_end=True)

    def select_scene(self, scene: int) -> None:
        """Switch the pedal's ACTIVE scene (CC80, verified), then read that part."""
        if not 0 <= scene < PR.SCENES_PER_PRESET:
            raise ValueError("scene must be 0..2")
        if self.preset is None:
            return
        self._restore = None
        self._send_scene_cc(scene)
        if scene != self.scene_index:
            self._touched.clear()
        self._set_scene_index(scene)        # the pedal does not echo our CC80
        self.refresh_edit_buffer()

    def _restore_scene(self, scene: Optional[int]) -> None:
        """After a reload (PC revert, save to the current preset) the pedal is on
        scene 0: switch back to `scene` with CC80 and re-read that part."""
        if self.preset is None:
            return
        if scene:                                   # None / 0: stays on scene 0
            self._send_scene_cc(scene)
            self._restore = (scene, self.clock() + self.RESTORE_HOLD)
        else:
            self._restore = None
        self._set_scene_index(scene or 0)
        self.refresh_edit_buffer()

    def _request_edit_part(self, preset: int, scene: int) -> None:
        self._part_reads.add((preset, scene))
        self.transport.send(P.get_edit_buffer(preset, scene), key=f"get:0C:{preset}:{scene}",
                            move_to_end=True)

    # -- save --
    def save_preset(self, target: int, name: Optional[str] = None, *,
                    allow_factory: bool = False, parts: Optional[list[bytes]] = None,
                    transform: Optional[Callable] = None, kind: str = "save") -> S.SaveJob:
        """Save to stored preset `target` (see saving.py for the sequence).

        Default source = the edit buffer of all 3 scenes of the CURRENT preset
        (read with 0C <cur> <s>); `parts` = 3 explicit decoded scenes instead
        (import); `transform(parts) -> parts` edits them before writing (scene
        copy); `name` replaces the name in all 3 parts (printable ASCII, <=16).
        Progress/result via the "save" event. Raises ValueError (refused target)
        or RuntimeError (busy / offline / read-only / nothing loaded)."""
        if not PR.is_valid(target):
            raise ValueError(f"érvénytelen preset: {target}")
        if not PR.is_user(target) and not allow_factory:
            raise ValueError("gyári presetet nem lehet felülírni")
        if not self.live:
            raise RuntimeError("csak olvasható mód (--no-live): mentés letiltva")
        if not self.connected:
            raise RuntimeError("nincs kapcsolat a pedállal")
        if self.busy:
            raise RuntimeError("egy mentés már folyamatban van")
        if parts is None and (self.preset is None or self.scene is None):
            raise RuntimeError("még nincs beolvasott preset")
        if parts is not None and len(parts) != PR.SCENES_PER_PRESET:
            raise ValueError("3 jelenet kell")
        job = S.SaveJob(self, target, name=name, parts=parts, transform=transform, kind=kind,
                        timeout=self.save_timeout,
                        tempo=self.tempo if parts is None else None)
        self.save_job = job
        job.start()
        return job

    def rename_preset(self, name: str) -> S.SaveJob:
        """Save the current preset (edit buffer) under a new name."""
        if self.preset is None:
            raise RuntimeError("még nincs beolvasott preset")
        return self.save_preset(self.preset, name=name, kind="rename")

    def copy_scene(self, src: int, dst: int) -> S.SaveJob:
        """Copy edit-buffer scene `src` over scene `dst` of the current preset
        and SAVE the preset (the only verified way to write another scene)."""
        if not (0 <= src < 3 and 0 <= dst < 3) or src == dst:
            raise ValueError("érvénytelen jelenet")
        if self.preset is None:
            raise RuntimeError("még nincs beolvasott preset")

        def tf(parts):
            parts[dst] = parts[src]
            return parts
        return self.save_preset(self.preset, transform=tf, kind="copy-scene")

    def import_to_slot(self, target: int, scenes: list[bytes], name: Optional[str] = None) -> S.SaveJob:
        """Write a file's 3 scenes directly to user preset `target`."""
        return self.save_preset(target, name=name, parts=list(scenes), kind="import")

    def _write_backup(self, preset: int, scenes: list[bytes]):
        d = self.backup_dir if self.backup_dir is not None else S.default_backup_dir()
        return S.write_backup(d, preset, scenes)

    def _after_commit(self, job: S.SaveJob) -> None:
        if job.target == self.preset:
            # the pedal reloads the edit buffer of the saved preset and resets the scene
            self._touched.clear()
            self._restore_scene(job.user_scene)

    def _save_finished(self, job: S.SaveJob) -> None:
        if job.ok:
            name = job.result_name()
            self._set_name(job.target, name)
            if job.target == self.preset:
                self._set_modified(False)
                if self.scene is not None and job.user_scene is not None:
                    # local copy = what is now stored (until the 0C re-read lands)
                    self.scene = bytearray(job.parts[job.user_scene])
                    self.emit("scene")
        self.emit("save", job)

    # -- apply a scene (import) via CCs only --
    def apply_scene(self, data: bytes) -> A.ApplyPlan:
        """Apply a decoded scene LIVE to the edit buffer using only CCs from the
        layout map (model/switch CCs first, then param slots). Never sends SysEx
        writes. Returns the plan (what was sent / skipped). Raises ValueError for
        a wrong-sized scene and RuntimeError when live edits are disabled."""
        if type(self.param_writer) is P.ParamWriter:
            raise RuntimeError("csak olvasható mód (--no-live): semmi sem lett elküldve")
        plan = A.plan_scene(self.layout, bytes(data),
                            bytes(self.scene) if self.scene is not None else None)
        ch = getattr(self.param_writer, "channel", 0)
        for st in plan.steps:
            # unkeyed: strict FIFO so model CCs always precede their params
            self.transport.send(P.control_change(st.cc, st.value, ch), priority=PRIO_NORMAL)
        if self.scene is not None and plan.steps:
            for st in plan.steps:
                if st.offset < len(self.scene):
                    self.scene[st.offset] = st.value
            self._touched.clear()
            self.emit("scene")
            self._set_modified(True)
        self.refresh_edit_buffer()          # read back the real result
        return plan

    # -- incoming --
    def handle_messages(self, msgs: list[bytes]) -> None:
        for m in msgs:
            try:
                self._handle(m)
            except Exception:  # noqa: BLE001
                log.exception("error handling %s", P.hexs(m))

    def _handle(self, msg: bytes) -> None:
        if not msg or P.is_realtime(msg):
            return
        s = msg[0]
        if (s & 0xF0) == 0xC0 and len(msg) == 2:         # preset changed on the pedal
            if self.preset is None:
                self._set_preset(msg[1])        # first report (e.g. after identity): not a load
            elif msg[1] != self.preset:
                self._restore = None
                self._touched.clear()
                self._set_preset(msg[1])
                self._set_scene_index(0)        # a preset load starts on scene 0
                self._set_modified(False)
            self.refresh_edit_buffer()
            return
        if (s & 0xF0) == 0xB0 and len(msg) == 3:
            self._handle_cc(s & 0x0F, msg[1], msg[2])
            return
        if s != P.SYSEX_START:
            return
        ident = P.parse_identity(msg)
        if ident:
            self.identity = ident
            self.emit("identity", ident)
            return
        m = P.parse_nux(msg)
        if not m:
            return
        if m.cmd == P.CMD_UNSUPPORTED:
            if self.save_job is not None and self.save_job.on_unsupported():
                return
            self.emit("info", "A pedál nem támogatja ezt a parancsot")
            return
        if P.is_modified_notification(msg):
            self._set_modified(True)
            return
        if m.op == P.OP_GET_RESP:
            self._handle_response(msg, m)
        elif m.op == P.OP_SET_ACK:
            job = self.save_job
            ack = P.parse_stored_ack(msg)
            if ack is not None and job is not None and job.on_stored_ack(ack):
                return
            cp = P.parse_commit_ack(msg)
            if cp is not None and job is not None and job.on_commit_ack(cp):
                return
            self.emit("info", f"A pedál nyugtázta: {m.cmd:02X} set")
        elif m.op == P.OP_SET:
            # the pedal pushed a change (e.g. knob turned on the unit): re-read
            self.emit("info", f"Változás a pedálon (parancs {m.cmd:02X})")
            self.refresh_edit_buffer()

    def _handle_response(self, msg: bytes, m: P.NuxMessage) -> None:
        if m.cmd == P.CMD_EDIT_BUFFER:
            eb = P.parse_edit_buffer(msg)
            if eb is None:
                return
            if len(eb.data) < self.layout.scene_size:
                log.warning("short edit buffer: %d bytes", len(eb.data))
            key = (eb.preset, eb.scene)
            if key in self._part_reads:
                # reply to a part read for a save
                self._part_reads.discard(key)
                job = self.save_job
                if job is not None and job.active:
                    job.on_edit_part(eb.preset, eb.scene, eb.data)
            # byte 7 is the pedal's "view" part, not the active scene: show the
            # reply only when it is the active part (or the active one is unknown)
            if self.scene_index is not None and eb.scene != self.scene_index:
                return
            if self.preset is not None and eb.preset != self.preset:
                return                           # stale reply from before a preset change
            new = bytearray(eb.data)
            # don't yank a slider the user is dragging right now: keep local
            # values for offsets touched within TOUCH_HOLD seconds (same preset)
            if self.scene is not None and eb.preset == self.preset:
                now = time.monotonic()
                for off, t in list(self._touched.items()):
                    if now - t < self.TOUCH_HOLD and off < len(new) and off < len(self.scene):
                        new[off] = self.scene[off]
                    elif now - t >= self.TOUCH_HOLD:
                        del self._touched[off]
            else:
                self._touched.clear()
            self.scene = new
            self._set_preset(eb.preset)
            self._set_name(eb.preset, self.layout.scene_name(new))
            t = self.layout.scene_tempo(self.scene)
            if t is not None and self.tempo is None:
                self._set_tempo(t)
            if self.layout.order_offset is not None:
                self.block_order = self.layout.scene_block_order(self.scene)
            self.emit("scene")
        elif m.cmd == P.CMD_STORED:
            st = P.parse_stored_scene(msg)
            if st is None:
                return
            if len(st.data) < self.layout.scene_size:
                log.warning("short stored scene %d/%d: %d bytes", st.preset, st.scene, len(st.data))
                return
            data = bytes(st.data[:self.layout.scene_size])
            job = self.save_job
            if job is not None and job.active:
                job.on_stored(st.preset, st.scene, data)
            if st.scene == 0:
                self._set_name(st.preset, self.layout.scene_name(data))
                if st.preset in self._names_pending:
                    self._names_pending.discard(st.preset)
                    if not self._names_pending:
                        self.emit("names")
            for f in list(self._fetches):
                f._feed(st.preset, st.scene, data)
            self.emit("stored", st.preset, st.scene, data)
        elif m.cmd == P.CMD_TEMPO:
            bpm = P.parse_tempo(msg)
            if bpm is not None:
                self._set_tempo(bpm)
            extra = P.parse_tempo_extra(msg)
            if extra is not None:
                self.tempo_extra = extra
        elif m.cmd == P.CMD_CURRENT:
            cur = P.parse_current(msg)
            if cur:
                self._set_preset(cur.preset)
        elif m.cmd == P.CMD_BLOCK_ORDER:
            order = P.parse_block_order(msg)
            if order is not None and order != self.block_order:
                self.block_order = order
                if self.scene is not None and self.layout.order_offset is not None \
                        and len(order) == self.layout.order_length:
                    o = self.layout.order_offset
                    self.scene[o:o + len(order)] = bytes(order)
                self.emit("scene")

    def _handle_cc(self, channel: int, cc: int, value: int) -> None:
        """A CC from the pedal (or another controller) that maps to a known
        parameter updates local state and the UI - it is never echoed back."""
        if cc == P.CC_SCENE and channel == getattr(self.param_writer, "channel", 0):
            self._on_scene_cc(value)
            return
        if cc == P.CC_CURRENT_BLOCK:
            return                              # block selected on the pedal UI: ignore
        hit = self.param_writer.lookup_cc(channel, cc)
        if not hit or self.scene is None:
            return
        block_id, what = hit
        block = self.layout.block_by_id(block_id)
        if block is None:
            return
        self._set_modified(True)
        if what == "switch" and block.model_offset is not None:
            old = self.scene[block.model_offset]
            self.scene[block.model_offset] = value      # full model byte
            self.emit("scene")
            if (old & ~block.bypass_bit) != (value & ~block.bypass_bit):
                # model changed on the pedal: params/defaults (and maybe the
                # auto-picked cab) changed too -> re-read the edit buffer
                self.refresh_edit_buffer()
            return
        if what == "bypass":
            block.set_bypassed(self.scene, value < 64)
            self.emit("scene")
            return
        model = block.current_model(self.scene)
        if model is None:
            return
        if what.startswith("#"):
            slot = int(what[1:]) if what[1:].isdigit() else None
            p = next((x for x in model.params if x.slot == slot), None)
        else:
            p = model.param(what)
        if p is None:
            return
        conv = getattr(self.param_writer, "from_cc_value", None)
        v = p.write(self.scene, conv(value, p.min, p.max) if conv else value)
        self.emit("param", block_id, p.name, v)

    def _on_scene_cc(self, value: int) -> None:
        """B0 50 <scene>: the pedal switched its active scene (footswitch / reload)."""
        if not 0 <= value < PR.SCENES_PER_PRESET:
            return
        if self._restore is not None:
            if value != self._restore[0]:
                return                          # the jump we are about to undo
            self._restore = None
        if value != self.scene_index:
            self._touched.clear()
            self._set_scene_index(value)
            self.refresh_edit_buffer()

    def _set_preset(self, preset: int) -> None:
        if preset != self.preset:
            self.preset = preset
            self.emit("preset", preset)

    def _set_tempo(self, bpm: int) -> None:
        if bpm != self.tempo:
            self.tempo = bpm
            self.emit("tempo", bpm)

    # -- user actions (outgoing) --
    def select_preset(self, preset: int) -> None:
        preset = max(0, min(self.layout.preset_count - 1, PR.PRESET_COUNT - 1, preset))
        self.transport.send(P.program_change(preset), key="pc")
        self._restore = None
        self._touched.clear()
        self.preset = preset            # optimistic; confirmed by the 0C reply
        self.emit("preset", preset)
        self._set_scene_index(0)        # a PC load starts on scene 0 (verified)
        self._set_modified(False)
        self.refresh_edit_buffer()

    def step_preset(self, delta: int) -> None:
        cur = self.preset if self.preset is not None else 0
        self.select_preset((cur + delta) % min(self.layout.preset_count, PR.PRESET_COUNT))

    def _send_all(self, out: P.Outgoing) -> None:
        for key, msg in out:
            self.transport.send(msg, key=key)

    def _call_writer(self, fn: Callable[[], P.Outgoing]) -> None:
        try:
            self._send_all(fn())
        except NotImplementedError as e:
            self.emit("info", str(e))

    def set_param(self, block: BlockSpec, param_name: str, value: int) -> None:
        if self.scene is None:
            return
        model = block.current_model(self.scene)
        p = model.param(param_name) if model else None
        if p is None:
            return
        v = p.write(self.scene, value)
        if p.offset is not None:
            self._touched[p.offset] = time.monotonic()
        if self.live:
            self._set_modified(True)
        self._call_writer(lambda: self.param_writer.set_param(block.id, p, v))

    def revert(self) -> None:
        """Discard edit-buffer changes: a PC to the current preset reloads the
        stored preset (verified on hardware); the pedal lands on scene 0, so the
        user's scene is re-selected with CC80."""
        if self.preset is None:
            return
        self._touched.clear()
        scene = self.scene_index
        self.transport.send(P.program_change(self.preset), key="pc")
        self._set_modified(False)
        self._restore_scene(scene)

    def set_bypass(self, block: BlockSpec, bypassed: bool) -> None:
        if self.scene is None:
            return
        if block.model_offset is not None:
            self._touched[block.model_offset] = time.monotonic()
        block.set_bypassed(self.scene, bypassed)
        if self.live:
            self._set_modified(True)
        mb = block.model_byte(self.scene) or 0
        self._call_writer(lambda: self.param_writer.set_bypass(block.id, bypassed, mb))

    def set_model(self, block: BlockSpec, model_index: int) -> None:
        if self.scene is None:
            return
        block.set_model(self.scene, model_index)
        if self.live:
            self._set_modified(True)
        mb = block.model_byte(self.scene) or 0
        self._call_writer(lambda: self.param_writer.set_model(block.id, model_index, mb))
        # the pedal loads the model's defaults (and may auto-pick a cab):
        # read back the real state; the GET is queued after the CC
        if type(self.param_writer) is not P.ParamWriter:   # no-op writer = nothing changed
            self.refresh_edit_buffer()

    def set_tempo(self, bpm: int) -> None:
        """Tempo via SysEx 03 set: the live tempo and the tempo bytes (0x5B/0x5C)
        of the ACTIVE scene's part (verified 2026-11-07). Nothing else is sent
        (a 15 set would revert it). Saving writes the tempo into all 3 parts.
        Read-only mode: local only."""
        bpm = max(P.TEMPO_MIN, min(P.TEMPO_MAX, int(bpm)))
        self.tempo = bpm
        if not self.live:
            return
        self.transport.send(P.set_tempo(bpm, self.tempo_extra), key="set:03", move_to_end=True)
        if self.scene is not None and self.layout.scene_tempo(self.scene) != bpm:
            self.layout.set_scene_tempo(self.scene, bpm)
            self._set_modified(True)

    def set_block_order(self, order: list[int]) -> bool:
        """Reorder the chain (0D set). `order` must be a permutation of the
        current block order. Edit buffer only (save to keep). Returns False
        (and emits info) when refused."""
        order = [int(b) for b in order]
        cur = list(self.block_order)
        if len(order) != P.BLOCK_ORDER_LEN or sorted(order) != sorted(cur):
            log.warning("set_block_order: %s is not a permutation of %s", order, cur)
            self.emit("info", "Érvénytelen blokksorrend")
            return False
        if order == cur:
            return True
        if not self.live:
            self.emit("info", "Csak olvasható mód: a blokksorrend nem változtatható")
            return False
        self.transport.send(P.set_block_order(order), key="set:0D", move_to_end=True)
        self.block_order = order
        if self.scene is not None and self.layout.order_offset is not None:
            o = self.layout.order_offset
            self.scene[o:o + len(order)] = bytes(order)
            self._touched[o] = time.monotonic()
        self._set_modified(True)
        self.emit("scene")
        return True

    def send_raw(self, msg: bytes) -> None:
        """Monitor panel: send arbitrary bytes (user-initiated, for reverse engineering)."""
        self.transport.send(msg)
