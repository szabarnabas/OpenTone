"""PipeWire side: find the pedal's sink (pactl) and stream the click (pw-cat).

Pure helpers (parse_sinks, parse_short_sinks, find_pedal_sink, pwcat_command)
are unit-tested; ClickPlayer owns the pw-cat process and a writer thread.

The click is written as raw PCM into pw-cat's stdin in ~10 ms chunks. The pipe
is shrunk to 8 KiB (~40 ms of 4-channel s16) and the writes block when it is
full, so the generator runs exactly as fast as PipeWire consumes audio (the
pedal's USB clock) and only a few tens of ms are ever buffered.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable, Optional

from .click import CHANNELS, RATE, ClickGenerator

log = logging.getLogger(__name__)

SINK_PATTERNS = ("MG-300", "MG300", "NUX")
CHUNK_FRAMES = RATE // 100            # 10 ms
PIPE_BYTES = 8192                     # ~43 ms at 4 ch s16
# rough audio-out delay after a chunk was written (pipe + pw-cat's stdio buffer
# + quantum + device), used to light the beat dots in time with the sound
OUTPUT_DELAY_S = 0.07
PACTL_ENV = {**os.environ, "LC_ALL": "C"}

# pactl channel names -> pw-cat/SPA short names
_POS = {"front-left": "FL", "front-right": "FR", "front-center": "FC", "lfe": "LFE",
        "rear-left": "RL", "rear-right": "RR", "side-left": "SL", "side-right": "SR",
        "mono": "MONO", "aux0": "AUX0", "aux1": "AUX1", "aux2": "AUX2", "aux3": "AUX3"}
DEFAULT_MAP = ("FL", "FR", "FC", "LFE")


@dataclass
class Sink:
    name: str
    description: str = ""
    mute: Optional[bool] = None        # None = unknown
    channel_map: tuple[str, ...] = ()  # SPA short names, sink order
    volume_percent: Optional[int] = None


def _matches(text: str) -> bool:
    t = text.upper()
    return any(p in t for p in SINK_PATTERNS)


def parse_sinks(text: str) -> list[Sink]:
    """`pactl -f json list sinks` -> sinks."""
    try:
        data = json.loads(text)
    except ValueError:
        return []
    out = []
    for s in data if isinstance(data, list) else []:
        if not isinstance(s, dict) or not s.get("name"):
            continue
        cmap = tuple(_POS.get(c.strip(), c.strip().upper())
                     for c in str(s.get("channel_map", "")).split(",") if c.strip())
        vol = None
        vols = s.get("volume")
        if isinstance(vols, dict):
            pcts = [int(m.group(1)) for v in vols.values() if isinstance(v, dict)
                    for m in [re.match(r"\s*(\d+)%", str(v.get("value_percent", "")))] if m]
            vol = max(pcts) if pcts else None
        mute = s.get("mute")
        props = s.get("properties") if isinstance(s.get("properties"), dict) else {}
        out.append(Sink(name=s["name"],
                        description=str(s.get("description") or props.get("device.description", "")),
                        mute=mute if isinstance(mute, bool) else None,
                        channel_map=cmap, volume_percent=vol))
    return out


def parse_short_sinks(text: str) -> list[Sink]:
    """`pactl list short sinks` fallback: index, name, driver, spec, state."""
    out = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1].strip():
            m = re.search(r"(\d+)ch", parts[3]) if len(parts) > 3 else None
            n = int(m.group(1)) if m else 0
            out.append(Sink(name=parts[1].strip(), channel_map=DEFAULT_MAP[:n] if n == 4 else ()))
    return out


def find_pedal_sink(sinks: list[Sink], override: Optional[str] = None) -> Optional[Sink]:
    """The NUX MG-300 sink (by node name first, then description)."""
    if override:
        return next((s for s in sinks if s.name == override), None)
    for s in sinks:
        if _matches(s.name):
            return s
    return next((s for s in sinks if _matches(s.description)), None)


def stream_channel_map(sink: Optional[Sink]) -> tuple[str, ...]:
    """Positions for our 4-channel stream. Use the sink's own map when it has 4
    channels, so stream channel n lands 1:1 on sink channel n (= pedal USB
    playback n) without PipeWire channel mixing; else FL,FR,FC,LFE."""
    if sink and len(sink.channel_map) == CHANNELS:
        return sink.channel_map
    return DEFAULT_MAP


def pwcat_command(target: str, channel_map: tuple[str, ...] = DEFAULT_MAP, *,
                  node_name: str = "opentone-click", app_name: str = "OpenTone",
                  app_id: str = "io.github.barni.OpenTone") -> list[str]:
    """pw-cat playback command; node/app names label the stream in the mixer
    (OpenTone's built-in click passes its own)."""
    props = ("{ channelmix.upmix = false channelmix.mix-lfe = false "
             "node.dont-fallback = true node.dont-reconnect = true "
             f"node.name = {node_name} media.name = \"{app_name} metronóm\" "
             f"application.name = {app_name} application.id = {app_id} }}")
    return ["pw-cat", "--playback", "--raw", "--rate", str(RATE), "--channels", str(CHANNELS),
            "--channel-map", ",".join(channel_map), "--format", "s16", "--latency", "256",
            "--media-role", "Music", "-P", props, "--target", target, "-"]


def pactl(*args: str, timeout: float = 3.0) -> Optional[str]:
    try:
        r = subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=timeout,
                           env=PACTL_ENV)
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("pactl %s failed: %s", args, e)
        return None
    return r.stdout if r.returncode == 0 else None


def list_sinks() -> list[Sink]:
    """Blocking (~10-30 ms); call from a worker thread."""
    out = pactl("-f", "json", "list", "sinks")
    sinks = parse_sinks(out) if out else []
    if not sinks:
        out = pactl("list", "short", "sinks")
        sinks = parse_short_sinks(out) if out else []
    return sinks


class ClickPlayer:
    """Runs pw-cat + writer thread. Callbacks come from the writer thread:
      on_beat(beat_index, accent, delay_s)  a click was queued, sounds in ~delay_s
      on_stopped(error_text_or_None)        playback ended (stop() or pw-cat died)
    """

    def __init__(self, gen: ClickGenerator, on_beat: Optional[Callable] = None,
                 on_stopped: Optional[Callable] = None, command=pwcat_command):
        self.gen = gen
        self.on_beat = on_beat
        self.on_stopped = on_stopped
        self.command = command
        self.lock = threading.Lock()          # serialises generator access
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._stopping = threading.Event()

    @property
    def playing(self) -> bool:
        return self._proc is not None

    # parameter changes from the UI thread
    def set_bpm(self, bpm: float) -> None:
        with self.lock:
            self.gen.set_bpm(bpm)

    def set_volume(self, v: float) -> None:
        with self.lock:
            self.gen.set_volume(v)

    def set_accent(self, on: bool) -> None:
        with self.lock:
            self.gen.accent = on

    def set_route(self, route) -> None:
        with self.lock:
            self.gen.set_route(route)

    def start(self, target: str, channel_map: tuple[str, ...] = DEFAULT_MAP) -> None:
        if self._proc:
            return
        with self.lock:
            self.gen.reset()
        cmd = self.command(target, channel_map)
        log.info("starting %s", " ".join(cmd))
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, bufsize=0)
        try:
            fcntl.fcntl(proc.stdin.fileno(), fcntl.F_SETPIPE_SZ, PIPE_BYTES)
        except (OSError, AttributeError) as e:
            log.debug("F_SETPIPE_SZ failed: %s", e)
        self._proc = proc
        self._stopping.clear()
        self._thread = threading.Thread(target=self._run, args=(proc,), name="takk-audio", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 1.0) -> None:
        proc, thread = self._proc, self._thread
        if not proc:
            return
        self._stopping.set()
        # only kill the process here: the writer thread then gets EPIPE and closes
        # stdin itself (closing the fd under a blocked write could hit a reused fd)
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
        if thread and thread is not threading.current_thread():
            thread.join(timeout)

    def _run(self, proc: subprocess.Popen) -> None:
        fd = proc.stdin.fileno()
        error = None
        try:
            while not self._stopping.is_set():
                with self.lock:
                    data, beats = self.gen.render(CHUNK_FRAMES)
                view = memoryview(data)
                while view:
                    n = os.write(fd, view)       # blocks while the pipe is full
                    view = view[n:]
                if beats and self.on_beat:
                    for b in beats:
                        self.on_beat(b.index, b.accent,
                                     OUTPUT_DELAY_S - (CHUNK_FRAMES - b.offset) / RATE)
        except (BrokenPipeError, OSError) as e:
            if not self._stopping.is_set():
                error = _stderr_tail(proc) or str(e)
        finally:
            _terminate(proc, 1.0)
            if self._proc is proc:
                self._proc = None
            if error is None and not self._stopping.is_set():
                error = _stderr_tail(proc) or "pw-cat leállt"
            if proc.stderr:
                proc.stderr.close()
            if self.on_stopped:
                self.on_stopped(error)


def _terminate(proc: subprocess.Popen, timeout: float) -> None:
    try:
        proc.stdin.close()
    except OSError:
        pass
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout)


def _stderr_tail(proc: subprocess.Popen) -> str:
    if proc.poll() is None or proc.stderr is None:
        return ""
    try:
        os.set_blocking(proc.stderr.fileno(), False)
        text = (proc.stderr.read() or b"").decode("utf-8", "replace")
    except (OSError, ValueError):
        return ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    errors = [ln for ln in lines if "error" in ln.lower()]
    return (errors or lines or [""])[-1]



class SinkWatcher:
    """Keeps the sink list fresh: re-lists on `pactl subscribe` sink/card/server
    events (debounced), on kick(), and every FALLBACK_S anyway.
    on_sinks(list[Sink]) is called via `dispatch` whenever the list changes."""

    FALLBACK_S = 5.0
    DEBOUNCE_S = 0.15

    def __init__(self, on_sinks: Callable, dispatch: Callable = lambda fn, *a: fn(*a),
                 lister: Callable[[], list] = list_sinks):
        self.on_sinks = on_sinks
        self.dispatch = dispatch
        self.lister = lister
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._sub: Optional[subprocess.Popen] = None
        self._last: Optional[list] = None

    def start(self) -> None:
        threading.Thread(target=self._loop, name="takk-sinks", daemon=True).start()
        threading.Thread(target=self._subscribe, name="takk-pactl-sub", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        sub = self._sub
        if sub and sub.poll() is None:
            sub.terminate()

    def kick(self) -> None:
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            sinks = self.lister()
            if sinks != self._last:
                self._last = sinks
                self.dispatch(self.on_sinks, sinks)
            self._wake.wait(self.FALLBACK_S)
            if self._stop.is_set():
                break
            self._stop.wait(self.DEBOUNCE_S)
            self._wake.clear()

    def _subscribe(self) -> None:
        try:
            self._sub = subprocess.Popen(["pactl", "subscribe"], stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, text=True, env=PACTL_ENV)
        except OSError:
            return                      # fallback polling only
        for line in self._sub.stdout:
            if self._stop.is_set():
                break
            if re.search(r" on (sink|card|server) ", line):
                self._wake.set()
        self._sub.stdout.close()
