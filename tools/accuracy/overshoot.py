import sys, subprocess, numpy as np
sys.path.insert(0,"src"); sys.path.insert(0,"tests")
from pathlib import Path
from qctrack.native.mpg123 import Mpg123Decoder
from qctrack.native.vorbisfile import VorbisDecoder
from qctrack.core.loudness import LoudnessAnalyzer
from helpers import ffmpeg_reference
S = Path(sys.argv[1]); rate = 44100
def measure(blocks):
    a = LoudnessAnalyzer(rate)
    for b in blocks: a.feed(b)
    return a.results()[0]
def pcm_tp(x): 
    a = LoudnessAnalyzer(rate); a.feed(x.astype(np.float32)); return a.results()[0]
rng = np.random.default_rng(3)
n = rate*20; t = np.arange(n)/rate
def source(kind):
    base = sum(rng.uniform(.02,.1)*np.sin(2*np.pi*rng.uniform(80,15000)*t+rng.uniform(0,6)) for _ in range(60))
    base = base*(1+.5*np.sin(2*np.pi*2*t)) + rng.standard_normal(n)*.03
    base /= abs(base).max()
    if kind=="loud":   # brickwall-limited master: hard clip at -0.1 dBFS after +9 dB gain
        ceil = 10**(-0.1/20); return np.clip(base*2.8, -ceil, ceil)
    if kind=="clipped":  return np.clip(base*4, -1, 1)
    if kind.startswith("mast"):   # soft-limited (tanh) master, then scaled so the TRUE peak is exactly the target
        tgt = float(kind.split("_")[1]); x = np.tanh(base*3.0)
        tp = pcm_tp(np.stack([x,x*0.9],1)).true_peak_db
        return x*10**((tgt-tp)/20)
    return base*0.5      # quiet: peak -6 dBFS
print("%-8s %-10s %8s %8s %8s %8s"%("source","codec","src TP","dec TP","dec SP","overshoot"))
for kind in ("mast_-1.0","mast_-0.3","mast_0.0"):
    m = source(kind); st = np.stack([m, m*0.9],1).astype(np.float32)
    src = pcm_tp(st); raw = S/f"{kind}.f32"; raw.write_bytes(st.tobytes())
    for name,args,ext in [("mp3 128",["-c:a","libmp3lame","-b:a","128k"],"mp3"),("mp3 192",["-c:a","libmp3lame","-b:a","192k"],"mp3"),
        ("mp3 320",["-c:a","libmp3lame","-b:a","320k"],"mp3"),("mp3 V0",["-c:a","libmp3lame","-q:a","0"],"mp3"),
        ("vorbis q3",["-c:a","libvorbis","-q:a","3"],"ogg"),("vorbis q6",["-c:a","libvorbis","-q:a","6"],"ogg")]:
        out = S/f"{kind}_{name.replace(' ','_')}.{ext}"
        subprocess.run(["ffmpeg","-v","error","-y","-f","f32le","-ar","44100","-ac","2","-i",str(raw),*args,str(out)],check=True)
        dec = (Mpg123Decoder if ext=="mp3" else VorbisDecoder)(out)
        r = measure(dec.blocks()); 
        print("%-8s %-10s %8.2f %8.2f %8.2f %8.2f"%(kind,name,src.true_peak_db,r.true_peak_db,r.sample_peak_db,r.true_peak_db-src.true_peak_db))
