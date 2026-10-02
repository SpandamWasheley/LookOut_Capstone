# Interleaved A/B of pose-anchor settings inside ONE process (a fresh pose model per variant), so the
# comparison is not polluted by cross-session machine variance (same code varied ~20% between sessions).
#   python detection_sandbox/ab_pose.py "Smoking/Aug18_18 - Trim2.mp4" 516 "A: FP32, no miss-cache" "B: FP16 + miss-cache" ...
import os, sys, io, time, glob
os.environ["KMP_DUPLICATE_LIB_OK"]="TRUE"
ROOT="C:/Users/User/OneDrive/Desktop/LookOut_v2"
sys.path.insert(0,ROOT+"/lookout_backend"); os.chdir(ROOT+"/lookout_backend")
os.environ.setdefault("DJANGO_SETTINGS_MODULE","lookout_backend.settings")
import django; django.setup()
from django.core.management import call_command
import torch
from core.vision import recognition as R
real=R.find_mouth_pose; acc={"calls":0,"secs":0.0,"none":0}
def timed(frame,box,with_source=False):
    t=time.time(); out=real(frame,box); torch.cuda.synchronize()
    acc["secs"]+=time.time()-t; acc["calls"]+=1; acc["none"]+=out is None; return out
R.find_mouth_pose=timed
clip="C:/Users/User/OneDrive/Desktop/Violation testing/"+sys.argv[1]; frames=int(sys.argv[2])
variants={"A: FP32, no miss-cache":(False,0.0),"B: FP16 + miss-cache":(True,0.5)}
for v in sys.argv[3:]:
    half,miss=variants[v]; R.POSE_HALF=half; R.MOUTH_MISS_CACHE_SECONDS=miss; R._pose_model=None; R._pose_logged=False   # fresh model so precision really changes
    acc.update(calls=0,secs=0.0,none=0); buf=io.StringIO(); stamp=time.time(); t=time.time()
    call_command("watch_merged", source=clip, dry_run=True, only="smoking,drinking", camera="CAM-SMOKE-01", stdout=buf, stderr=buf)
    wall=time.time()-t
    print(f"{v:26s} wall {wall:6.1f}s fps {frames/wall:4.2f} | pose calls {acc['calls']} ({acc['calls']/frames:.2f}/frame) none {acc['none']} | pose time {acc['secs']:.1f}s = {100*acc['secs']/wall:.1f}% of wall, {1000*acc['secs']/max(acc['calls'],1):.0f} ms/call",flush=True)
    for f in glob.glob("media/violations/*"):
        if os.path.getmtime(f)>=stamp-1: os.remove(f)
