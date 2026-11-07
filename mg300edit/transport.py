"""Transports: device autodetection + raw ALSA MIDI I/O with hotplug.

A transport is UI-agnostic. Callbacks are delivered through `dispatch(fn, *args)`
which the app sets to a GLib.idle_add wrapper so they run on the GTK main loop.

Callbacks (all optional, set as attributes):
  on_messages(list[bytes])          complete incoming messages (one read batch)
  on_status(connected: bool, text)  connection state changes
  on_traffic(direction, msg, t)     every message in/out ('in'/'out'), for the monitor
"""

from __future__ import annotations

import errno
import glob
import logging
import os
import re
import select
import threading
import time
from dataclasses import dataclass
from typing import Callable, Hashable, Optional

from .midistream import StreamSplitter
from .writer import PRIO_LOW, PRIO_NORMAL, CoalescingWriter  # noqa: F401 - re-exported

log = logging.getLogger(__name__)

DEVICE_PATTERNS = ("MG-300V2", "MG300V2", "MG-300", "MG300")
HOTPLUG_INTERVAL = 2.0
NOT_FOUND = "Nem található pedál"


def _direct_dispatch(fn, *args):
    fn(*args)


# --- device detection --------------------------------------------------------

@dataclass
class DeviceInfo:
    card: int
    name: str
    path: str          # /dev/snd/midiC<card>D0


def _matches(text: str) -> bool:
    t = text.upper()
    return any(p.upper() in t for p in DEVICE_PATTERNS)


def find_device(proc_cards: str = "/proc/asound/cards",
                sys_sound: str = "/sys/class/sound",
                dev_snd: str = "/dev/snd") -> Optional[DeviceInfo]:
    """Locate the pedal's rawmidi node. Only reads text files and stats paths;
    never opens anything under /dev/snd."""
    candidates: list[tuple[int, str]] = []
    try:
        with open(proc_cards, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        for i, line in enumerate(lines):
            m = re.match(r"^\s*(\d+)\s+\[([^\]]*)\]\s*:\s*(.*)$", line)
            if not m:
                continue
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if _matches(" ".join((m.group(2), m.group(3), nxt))):
                candidates.append((int(m.group(1)), m.group(3).strip() or m.group(2).strip()))
    except OSError:
        pass
    if not candidates:  # fallback: sysfs
        for p in sorted(glob.glob(os.path.join(sys_sound, "card*"))):
            mm = re.search(r"card(\d+)$", p)
            if not mm:
                continue
            texts = []
            for fn in ("id", "device/../product", "device/product"):
                try:
                    with open(os.path.join(p, fn), encoding="utf-8", errors="replace") as f:
                        texts.append(f.read().strip())
                except OSError:
                    pass
            if _matches(" ".join(texts)):
                candidates.append((int(mm.group(1)), " ".join(t for t in texts if t)))
    for card, name in candidates:
        path = os.path.join(dev_snd, f"midiC{card}D0")
        if os.path.exists(path):
            return DeviceInfo(card=card, name=name, path=path)
    return None


# --- base transport ----------------------------------------------------------

class BaseTransport:
    def __init__(self, *, sysex_gap: float = 0.040, channel_gap: float = 0.005,
                 dispatch: Callable = _direct_dispatch):
        self.dispatch = dispatch
        self.on_messages: Optional[Callable[[list[bytes]], None]] = None
        self.on_status: Optional[Callable[[bool, str], None]] = None
        self.on_traffic: Optional[Callable[[str, bytes, float], None]] = None
        self.connected = False
        self.status_text = "Nincs kapcsolat"
        self.writer = CoalescingWriter(self._write, sysex_gap=sysex_gap, channel_gap=channel_gap,
                                       on_sent=self._on_sent)

    def _write(self, msg: bytes) -> None:      # subclasses implement
        raise NotImplementedError

    def start(self) -> None:
        self.writer.start()

    def stop(self) -> None:
        self.writer.stop()

    def send(self, msg: bytes, key: Optional[Hashable] = None, move_to_end: bool = False,
             priority: int = PRIO_NORMAL) -> None:
        if not self.connected:
            log.debug("not connected, dropping %s", bytes(msg).hex())
            return
        self.writer.send(msg, key=key, move_to_end=move_to_end, priority=priority)

    # helpers for subclasses (may be called from any thread)
    def _on_sent(self, msg: bytes, t: float) -> None:
        if self.on_traffic:
            self.dispatch(self.on_traffic, "out", msg, time.time())

    def _deliver(self, msgs: list[bytes]) -> None:
        if not msgs:
            return
        now = time.time()
        if self.on_traffic:
            for m in msgs:
                self.dispatch(self.on_traffic, "in", m, now)
        if self.on_messages:
            self.dispatch(self.on_messages, msgs)

    def _set_status(self, connected: bool, text: str) -> None:
        self.connected = connected
        self.status_text = text
        if not connected:
            self.writer.clear()
        if self.on_status:
            self.dispatch(self.on_status, connected, text)


# --- raw ALSA rawmidi --------------------------------------------------------

class _Conn:
    def __init__(self, fd: int, info: DeviceInfo):
        self.fd = fd
        self.info = info
        self.alive = True


class RawMidiTransport(BaseTransport):
    """os.open(/dev/snd/midiC<n>D0, O_RDWR|O_NONBLOCK) with reader + hotplug threads."""

    def __init__(self, device_path: Optional[str] = None, **kw):
        super().__init__(**kw)
        self.device_path = device_path      # fixed path overrides autodetect
        self._lock = threading.Lock()
        self._conn: Optional[_Conn] = None
        self._stop = threading.Event()
        self._hotplug: Optional[threading.Thread] = None
        self._last_error = ""

    def start(self) -> None:
        super().start()
        self._stop.clear()
        self._hotplug = threading.Thread(target=self._hotplug_loop, name="midi-hotplug", daemon=True)
        self._hotplug.start()

    def stop(self) -> None:
        self._stop.set()
        self._close("leállítva")
        super().stop()

    # -- connection management --
    def _locate(self) -> Optional[DeviceInfo]:
        if self.device_path:
            if os.path.exists(self.device_path):
                return DeviceInfo(card=-1, name=self.device_path, path=self.device_path)
            return None
        return find_device()

    def _open(self, info: DeviceInfo) -> None:
        try:
            fd = os.open(info.path, os.O_RDWR | os.O_NONBLOCK)
        except OSError as e:
            msg = f"Nem nyitható meg: {info.path} ({e.strerror})"
            if msg != self._last_error:     # e.g. EBUSY / EACCES: report once, retry later
                log.warning(msg)
                self._last_error = msg
                self._set_status(False, msg)
            return
        self._last_error = ""
        conn = _Conn(fd, info)
        with self._lock:
            self._conn = conn
        threading.Thread(target=self._reader, args=(conn,), name="midi-reader", daemon=True).start()
        self._set_status(True, f"{info.name} ({os.path.basename(info.path)})")

    def _close(self, reason: str) -> None:
        with self._lock:
            conn, self._conn = self._conn, None
            if conn:
                conn.alive = False          # the reader thread closes the fd
        if conn:
            log.info("disconnected: %s", reason)
            self._set_status(False, f"Kapcsolat bontva ({reason})")

    def _hotplug_loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                conn = self._conn
            if conn is None:
                info = self._locate()
                if info:
                    self._open(info)
                else:
                    self._last_error = ""
                    if self.status_text != NOT_FOUND:
                        self._set_status(False, NOT_FOUND)
            elif not os.path.exists(conn.info.path):
                self._close("eszköz eltávolítva")
            self._stop.wait(HOTPLUG_INTERVAL)

    def _reader(self, conn: _Conn) -> None:
        splitter = StreamSplitter()
        try:
            while conn.alive:
                try:
                    r, _, _ = select.select([conn.fd], [], [], 0.25)
                except (OSError, ValueError):
                    break
                if not r:
                    continue
                try:
                    data = os.read(conn.fd, 4096)
                except BlockingIOError:
                    continue
                except OSError as e:
                    log.warning("read error: %s", e)
                    break
                if not data:                # EOF: device gone
                    break
                self._deliver(splitter.feed(data))
        finally:
            conn.alive = False
            try:
                os.close(conn.fd)
            except OSError:
                pass
            with self._lock:
                mine = self._conn is conn
                if mine:
                    self._conn = None
            if mine:
                self._set_status(False, "Kapcsolat bontva (I/O hiba)")

    def _write(self, msg: bytes) -> None:
        with self._lock:
            conn = self._conn
            if conn is None or not conn.alive:
                return
            fd = conn.fd
            view = memoryview(msg)
            deadline = time.monotonic() + 0.5
            while view:
                try:
                    n = os.write(fd, view)
                    view = view[n:]
                except BlockingIOError:
                    if time.monotonic() > deadline:
                        raise TimeoutError("rawmidi write timed out")
                    select.select([], [fd], [], 0.05)
                except OSError as e:
                    if e.errno in (errno.ENODEV, errno.EIO, errno.EBADF):
                        conn.alive = False      # reader notices and reports disconnect
                    raise
