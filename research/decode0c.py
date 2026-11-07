import os, time, select, sys
fd = os.open("/dev/snd/midiC2D0", os.O_RDWR | os.O_NONBLOCK)
def drain(wait=0.6):
    buf=b""; end=time.time()+wait
    while time.time()<end:
        r,_,_=select.select([fd],[],[],0.05)
        if r:
            try: buf+=os.read(fd,4096); end=max(end,time.time()+0.15)
            except BlockingIOError: pass
    return buf
def dec(d):
    out=bytearray()
    for i in range(0,len(d)-2,3):
        out.append(((d[i]<<6)|((d[i+1]&0x7E)>>1))&0xFF); out.append((d[i+2]&0x7F)|((d[i+1]&1)<<7))
    return bytes(out)
drain(0.2)
os.write(fd, bytes.fromhex("F0435870 0C00 F7".replace(" ","")))
r=drain()
print("preset",r[6],"scene",r[7],"enc len",len(r)-9)
d=dec(r[8:-1]); print("dec len",len(d))
for i in range(0,len(d),16): print(f"  {i:02x}: "+" ".join(f"{x:02X}" for x in d[i:i+16])+"  "+"".join(chr(x) if 32<=x<127 else "." for x in d[i:i+16]))
open(sys.argv[1] if len(sys.argv)>1 else "scene.bin","wb").write(d)
