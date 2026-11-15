import sys, numpy as np
sys.path.insert(0,"src"); sys.path.insert(0,"tests")
from qctrack.native.mpg123 import Mpg123Decoder as D
from helpers import ffmpeg_reference
for f in sys.argv[1:]:
    mp = np.concatenate(list(D(f).blocks())).reshape(-1,2)
    ff = ffmpeg_reference(f, 2, 44100)
    a, b = mp[:,0].astype(float), ff[:,0].astype(float)
    best = max(range(0,3000), key=lambda k: np.dot(a[k:k+20000], b[:20000]))
    n = min(len(a)-best, len(b))
    d = a[best:best+n]-b[:n]
    print("%-14s mpg123 peak %.4f ffmpeg peak %.4f lag %d  aligned maxdiff %.5f  rms %.2e"%(f.split('/')[-1],abs(mp).max(),abs(ff).max(),best,abs(d).max(),np.sqrt((d**2).mean())))
