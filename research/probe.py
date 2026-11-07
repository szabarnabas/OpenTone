#!/usr/bin/env python3
"""Read-only probe for NUX MG-300 MKII over raw ALSA MIDI. Only sends GET (op=00) requests."""
import os, sys, time, select

DEV = sys.argv[1] if len(sys.argv) > 1 else "/dev/snd/midiC2D0"
fd = os.open(DEV, os.O_RDWR | os.O_NONBLOCK)

def drain(wait=0.4):
    buf = b""
    end = time.time() + wait
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            try:
                buf += os.read(fd, 4096)
                end = max(end, time.time() + 0.15)
            except BlockingIOError:
                pass
    return buf

def split(buf):
    out, cur = [], b""
    for b in buf:
        if b == 0xF0:
            cur = bytes([b])
        elif cur:
            cur += bytes([b])
            if b == 0xF7:
                out.append(cur); cur = b""
        else:
            out.append(bytes([b]))
    return out

def hexs(b): return " ".join(f"{x:02X}" for x in b)

drain(0.2)
cmds = [int(c, 16) for c in sys.argv[2:]] if len(sys.argv) > 2 else \
       [0x03, 0x09, 0x0B, 0x0C, 0x0D, 0x0F, 0x12, 0x13, 0x14, 0x15, 0x17, 0x18, 0x19, 0x62, 0x7E]
for cmd in cmds:
    msg = bytes([0xF0, 0x43, 0x58, 0x70, cmd, 0x00, 0xF7])
    os.write(fd, msg)
    resp = drain(0.5)
    parts = split(resp)
    print(f"== GET {cmd:02X}: {len(parts)} msg(s), {len(resp)} bytes")
    for p in parts:
        print("  ", len(p), hexs(p[:80]) + (" ..." if len(p) > 80 else ""))
os.close(fd)
