# Diagnostic: smoking_v5 vs merged_v2 Cigarette detections across near / crops / production / cascade modes.
#   python detection_sandbox/model_compare.py [stride]   -> top-5 crop sheets under $TEMP/work/compare/
import os, sys, time, json
os.environ["KMP_DUPLICATE_LIB_OK"]="TRUE"
ROOT="C:/Users/User/OneDrive/Desktop/LookOut_v2"
sys.path.insert(0,ROOT+"/lookout_backend"); os.environ.setdefault("DJANGO_SETTINGS_MODULE","lookout_backend.settings")
import django; django.setup()
import cv2, numpy as np
from ultralytics import YOLO
from core.vision import recognition as R
STRIDE=int(sys.argv[1]) if len(sys.argv)>1 else 6
V=ROOT+"/lookout_backend/core/vision/"
models={"smoking_v5":YOLO(V+"smoking_v5.pt"),"merged_v2":YOLO(V+"merged_v2.pt")}
B="C:/Users/User/OneDrive/Desktop/Violation testing/Smoking/"
clips=["Aug14_3 - MorningMediumBldg - Trim.mp4","Aug18_18 - TrimCigaretteNightFar1.mp4","Aug18_18 - Trim2.mp4"]
OUT=os.environ["TEMP"]+"/work/compare"; os.makedirs(OUT,exist_ok=True)
def near(m,fr,persons): return R._smoking_boxes_from_result(m(fr,verbose=False,imgsz=R.NEAR_IMGSZ)[0],0.25)
def far_notile(m,fr,persons): return R._detect_far(m,fr,0.25,(1,1),0.2,persons,2.0)      # whole frame + person crops, no tiling
def far_tile(m,fr,persons): return R._detect_far(m,fr,0.25,(2,2),0.2,persons,2.0)        # production: whole + 2x2 tiles + person crops
def cascade(m,fr,persons): return R.detect_on_person_crops(m,fr,persons,0.25,0.35,640)
modes={"near(960, whole only)":near,"whole+crops (no tiles)":far_notile,"PRODUCTION whole+tiles+crops":far_tile,"cascade (native person crops)":cascade}
for c in clips:
    cap=cv2.VideoCapture(B+c); n=int(cap.get(7)); frames=[]
    for fi in range(0,n,STRIDE):
        cap.set(1,fi); ok,fr=cap.read()
        if not ok: break
        frames.append((fi,fr,R.detect_persons(fr)))
    print(f"\n##### {c[:34]}  ({len(frames)} frames, stride {STRIDE})",flush=True)
    top={}
    for mn,m in models.items():
        for md,fn in modes.items():
            t=time.time(); dets=[]
            for fi,fr,pb in frames:
                for d in fn(m,fr,pb): dets.append((fi,)+tuple(d))
            cig=[d for d in dets if d[6].lower()=="cigarette"]; vape=[d for d in dets if d[6].lower()=="vape"]
            cf=[d[5] for d in cig]
            fr_with=len({d[0] for d in cig if d[5]>=0.30})
            print(f"  {mn:10s} | {md:30s} | cig {len(cig):4d} (>=.30: {sum(x>=.30 for x in cf):4d}, >=.50: {sum(x>=.5 for x in cf):4d}) max {max(cf) if cf else 0:.2f} | frames w/ cig>=.30: {fr_with:3d}/{len(frames)} | vape {len(vape)} | {time.time()-t:.0f}s",flush=True)
            if md.startswith("PRODUCTION"): top[mn]=sorted(cig,key=lambda d:-d[5])[:5]
    # contact sheet of top-5 per model (production mode)
    rows=[]
    cache={fi:fr for fi,fr,_ in frames}
    for mn in models:
        tiles=[]
        for (fi,x1,y1,x2,y2,sc,lab) in top[mn]:
            fr=cache[fi]; cx,cy=(x1+x2)//2,(y1+y2)//2; r=110
            crop=fr[max(cy-r,0):cy+r,max(cx-r,0):cx+r].copy()
            cv2.rectangle(crop,(min(x1-max(cx-r,0),2*r),min(y1-max(cy-r,0),2*r)),(min(x2-max(cx-r,0),2*r),min(y2-max(cy-r,0),2*r)),(0,255,255),1)
            crop=cv2.resize(crop,(300,300),interpolation=cv2.INTER_CUBIC)
            cv2.putText(crop,f"{mn} {sc:.2f} f{fi}",(4,16),cv2.FONT_HERSHEY_SIMPLEX,0.5,(0,255,0),1); tiles.append(crop)
        while len(tiles)<5: tiles.append(np.zeros((300,300,3),np.uint8))
        rows.append(cv2.hconcat(tiles))
    cv2.imwrite(f"{OUT}/top5_{c[:12].replace(' ','_')}_{c[-14:-4].replace(' ','_')}.jpg",cv2.vconcat(rows))
