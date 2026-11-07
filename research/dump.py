import os, sys, time, select
fd = os.open("/dev/snd/midiC2D0", os.O_RDWR | os.O_NONBLOCK)
def drain(wait=0.6):
    buf=b""; end=time.time()+wait
    while time.time()<end:
        r,_,_=select.select([fd],[],[],0.05)
        if r:
            try: buf+=os.read(fd,4096); end=max(end,time.time()+0.15)
            except BlockingIOError: pass
    return buf
drain(0.2)
for args in sys.argv[1:]:
    b=bytes.fromhex(args)
    os.write(fd, bytes([0xF0,0x43,0x58,0x70])+b+bytes([0xF7]))
    r=drain()
    print("REQ",args.upper(),"->",len(r))
    for i in range(0,len(r),16): print(f"  {i:04x}: "+" ".join(f"{x:02X}" for x in r[i:i+16]))
