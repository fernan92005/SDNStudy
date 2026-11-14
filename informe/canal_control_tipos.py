import struct, sys, collections
data=open(sys.argv[1],'rb').read()
off=24; streams=collections.defaultdict(dict); t0=None
while off+16<=len(data):
    sec,us,incl,orig=struct.unpack('<IIII',data[off:off+16]); p=data[off+16:off+16+incl]; off+=16+incl
    t=sec+us/1e6; t0=t0 or t
    if len(p)<34 or p[12:14]!=b'\x08\x00': continue
    ihl=(p[14]&15)*4; ip=p[14:]; tot=struct.unpack('>H',ip[2:4])[0]
    tcp=ip[ihl:tot]; sp,dp,seq=struct.unpack('>HHI',tcp[:8]); doff=(tcp[12]>>4)*4; pl=tcp[doff:]
    if pl: streams[(sp,dp)].setdefault(seq,(t,pl))
names={0:'HELLO',2:'ECHO_REQ',3:'ECHO_REP',10:'PACKET_IN',13:'PACKET_OUT',14:'FLOW_MOD',18:'MULTIPART_REQ',19:'MULTIPART_REP',12:'PORT_STATUS',11:'FLOW_REMOVED',20:'BARRIER_REQ',21:'BARRIER_REP'}
cnt=collections.Counter(); byts=collections.Counter(); per_sec=collections.defaultdict(collections.Counter); per_sec_b=collections.defaultdict(collections.Counter)
dur=0
for k,segs in streams.items():
    parts=[]; times=[]; L=0
    seqs=sorted(segs); pos=None
    for s in seqs:
        t,pl=segs[s]
        if pos is not None and s<pos:
            if s+len(pl)<=pos: continue
            pl=pl[pos-s:]; s=pos
        times.append((L,t)); parts.append(pl); L+=len(pl); pos=s+len(pl)
    buf=b''.join(parts)
    import bisect; starts=[x[0] for x in times]
    i=0; ti=0
    while i+8<=len(buf):
        v,ty,ln=struct.unpack('>BBH',buf[i:i+4])
        if ln<8: break
        t=times[bisect.bisect_right(starts,i)-1][1]; n=names.get(ty,str(ty))
        cnt[n]+=1; byts[n]+=ln; per_sec[int(t-t0)][n]+=1; per_sec_b[int(t-t0)][n]+=ln
        i+=ln
tmax=max(per_sec); print('duracion s',tmax)
for n,c in cnt.most_common(): print(f'{n:14s} {c:9d} msgs {byts[n]/1e6:9.2f} MB  {c/tmax:8.1f}/s {byts[n]*8/tmax/1e3:9.1f} kbit/s')
import json; json.dump({str(k):dict(v) for k,v in per_sec_b.items()},open('/home/claude/of_per_sec_bytes.json','w'))
json.dump({str(k):dict(v) for k,v in per_sec.items()},open('/home/claude/of_per_sec.json','w'))
