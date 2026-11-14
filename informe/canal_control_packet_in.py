import struct, sys, collections
data=open(sys.argv[1],'rb').read()
off=24; c=collections.Counter(); n=0; ports=collections.Counter()
while off+16<=len(data) and n<10**9:
    sec,us,incl,orig=struct.unpack('<IIII',data[off:off+16]); p=data[off+16:off+16+incl]; off+=16+incl
    if len(p)<34 or p[12:14]!=b'\x08\x00': continue
    ihl=(p[14]&15)*4; ip=p[14:]; tot=struct.unpack('>H',ip[2:4])[0]
    tcp=ip[ihl:tot]; sp,dp=struct.unpack('>HH',tcp[:4]); doff=(tcp[12]>>4)*4; pl=tcp[doff:]
    i=0
    while i+8<=len(pl):
        v,ty,ln=struct.unpack('>BBH',pl[i:i+4])
        if ln<8 or v!=4: break
        if ty==10 and i+ln<=len(pl):
            m=pl[i+8:i+ln]; reason=m[6]; mlen=struct.unpack('>H',m[18:20])[0]
            ml=(mlen+7)//8*8; pkt=m[16+ml+2:]
            et=struct.unpack('>H',pkt[12:14])[0] if len(pkt)>=14 else None
            extra=''
            if et==0x0800 and len(pkt)>=34:
                proto=pkt[23]; dst='.'.join(map(str,pkt[30:34])); src='.'.join(map(str,pkt[26:30])); extra=f'proto{proto} {src}->{dst}'
            c[(hex(et) if et else None, reason, extra)]+=1; ports[sp]+=1; n+=1
        i+=ln
    pass
for k,v in c.most_common(10): print(v,k)
print(ports.most_common(5))
