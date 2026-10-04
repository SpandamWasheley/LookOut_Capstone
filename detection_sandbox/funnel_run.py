# Diagnostic: in-process watch_merged / watch_smoking dry run with a spy around the model. Usage:
#   RUN_CMD=watch_smoking|watch_merged python detection_sandbox/funnel_run.py <label> "Smoking/<clip>.mp4" ...
# Writes per-run logs under $TEMP/work/funnel_<label>/. Does not modify the repo.
"""In-process watch_merged (smoking engine only, dry run) with a spy around the merged model.
Records every raw detection at conf>=0.01 per inference pass type. No repo files are modified."""
import os, sys, io, json, time, collections
os.environ["KMP_DUPLICATE_LIB_OK"]="TRUE"
ROOT="C:/Users/User/OneDrive/Desktop/LookOut_v2"
sys.path.insert(0,ROOT+"/lookout_backend"); os.chdir(ROOT+"/lookout_backend")
os.environ.setdefault("DJANGO_SETTINGS_MODULE","lookout_backend.settings")
import django; django.setup()
from django.core.management import call_command
from core.vision import recognition as R
CMD=os.environ.get('RUN_CMD','watch_merged'); label=sys.argv[1]; clips=sys.argv[2:]
OUT=os.environ["TEMP"]+"/work/funnel_"+label; os.makedirs(OUT,exist_ok=True)
state={"H":0,"W":0,"raw":[], "calls":collections.Counter(), "crop_dims":[]}
real_load=R.load_merged_model
class Spy:
    def __init__(self,m): self._m=m
    def __getattr__(self,n): return getattr(self._m,n)
    def __call__(self,img,*a,**k):
        k["conf"]=0.01
        res=self._m(img,*a,**k)
        h,w=img.shape[:2]
        if h*w>state["H"]*state["W"]: state["H"],state["W"]=h,w
        if (h,w)==(state["H"],state["W"]): kind="whole"
        elif h>=0.5*state["H"] and w>=0.5*state["W"]: kind="tile"
        else: kind="crop"; state["crop_dims"].append((h,w))
        state["calls"][kind]+=1
        r=res[0]
        for b in r.boxes:
            state["raw"].append((kind,r.names[int(b.cls[0])],float(b.conf[0])))
        return res
_spy=None
def load():
    global _spy
    if _spy is None: _spy=Spy(real_load())
    return _spy
R.load_merged_model=load
from core.vision import tracking
_assign=tracking.PersonTracker.assign
asg=collections.Counter()
def assign(self,dets,now,*a,**k):
    out=_assign(self,dets,now,*a,**k)
    for t,d in out.items():
        asg['to_scene' if getattr(t,'is_scene',False) else 'to_person']+=len(d)
    asg['in']+=len(dets)
    return out
tracking.PersonTracker.assign=assign
B="C:/Users/User/OneDrive/Desktop/Violation testing/"
bins=[(0.01,0.10),(0.10,0.25),(0.25,0.30),(0.30,0.50),(0.50,1.01)]
import cv2
for c in clips:
    asg.clear(); state["raw"].clear(); state["calls"].clear(); state["crop_dims"].clear(); state["H"]=state["W"]=0
    os.environ["LOOKOUT_MOUTH_LOG"]=OUT+"/"+os.path.basename(c)[:20].replace(" ","_")+".jsonl"
    if os.path.exists(os.environ["LOOKOUT_MOUTH_LOG"]): os.remove(os.environ["LOOKOUT_MOUTH_LOG"])
    buf=io.StringIO(); t=time.time(); stamp=time.time()
    call_command(CMD, source=B+c, dry_run=True, stats=True, camera="CAM-SMOKE-01", stdout=buf, stderr=buf, **({"only":"smoking"} if CMD=="watch_merged" else {}))
    el=time.time()-t
    name=os.path.basename(c)[:30]
    open(OUT+"/"+os.path.basename(c)[:20].replace(" ","_")+".log","w",encoding="utf-8").write(buf.getvalue())
    print(f"\n##### {name}  wall {el:.0f}s  model calls {dict(state['calls'])}  frame {state['W']}x{state['H']}")
    for cls in ("Cigarette","Bottle","knife"):
        rows=[r for r in state["raw"] if r[1]==cls]
        by={k:sum(1 for r in rows if r[0]==k) for k in ("whole","tile","crop")}
        hist=[sum(1 for r in rows if lo<=r[2]<hi) for lo,hi in bins]
        print(f"  raw {cls:9s} total {len(rows):6d}  by pass {by}  conf hist [0.01-.1|.1-.25|.25-.3|.3-.5|.5+] {hist}")
    cig=[r for r in state["raw"] if r[1]=="Cigarette"]
    for k in ("whole","tile","crop"):
        v=[r[2] for r in cig if r[0]==k]
        print(f"     Cigarette via {k:5s}: n={len(v):5d} max={max(v) if v else 0:.2f} n>=0.30={sum(x>=0.30 for x in v)}")
    print(f"  tracker.assign: detections in {asg['in']}  -> attached to a person {asg['to_person']}  -> scene (no person) {asg['to_scene']}")
    cd=state["crop_dims"]
    if cd: print(f"  person-crop input median {sorted(h for h,w in cd)[len(cd)//2]}x{sorted(w for h,w in cd)[len(cd)//2]} px (before the x2 upscale)")
    # engine stats block for smoking
    txt=buf.getvalue(); i=txt.find("Detection stats")
    print("  ENGINE STATS:\n"+"\n".join("   "+l for l in txt[i:i+1500].splitlines()[:14]))
    # clean evidence
    import glob
    for f in glob.glob("media/violations/*"):
        if os.path.getmtime(f)>=stamp-1: os.remove(f)
