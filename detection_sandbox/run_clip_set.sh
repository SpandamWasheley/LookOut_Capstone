#!/usr/bin/env bash
# Runs watch_merged (dry run: no Alert rows) over the 5 mouth-test clips and logs
# alerts + cues + mouth distances for before/after comparison.
#   bash detection_sandbox/run_clip_set.sh <label>      # e.g. baseline | pose
# Output: detection_sandbox/output/<label>/<clip>.log, .jsonl and times.txt
set -u
LABEL="${1:?usage: run_clip_set.sh <label>}"
export KMP_DUPLICATE_LIB_OK=TRUE
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/detection_sandbox/output/$LABEL"; mkdir -p "$OUT"; : > "$OUT/times.txt"
V="/c/Users/User/OneDrive/Desktop/Violation testing"
CLIPS=("Smoking/Aug14_3 - MorningMediumBldg - Trim.mp4" "Smoking/Aug18_18 - TrimCigaretteNightFar1.mp4" "Smoking/Aug18_18 - Trim2.mp4" "Drinking/Aug18_4 - TrimDrinkingEveningFar3.mp4" "Drinking/Aug18_5 - DrinkingKabilangRoad1.mp4" "Holdup/Aug24_16 - TrimHoldupBldg.mp4")
# Footage start times (drive the holdup time block and the drinking evening band).
# Hour/minute partly read off obscured overlays -- confirm. Kabilang unknown -> wall clock.
declare -A CLOCKS=(
  ["Aug14_3"]="2026-08-14 15:59"
  ["NightFar1"]="2026-08-18 19:06"
  ["Trim2"]="2026-08-18 19:04"
  ["EveningFar3"]="2026-08-18 18:54"
  ["TrimHoldupBldg"]="2026-08-24 16:17"
  # ["Kabilang"]="YYYY-MM-DD HH:MM"
)
# EXTRA="--no-cascade" etc. is passed straight to watch_merged; MOMENTUM="0.95,1.5,0.4,3.0" tunes momentum.
cd "$ROOT/lookout_backend"
touch "$OUT/.stamp"
for c in "${CLIPS[@]}"; do
  # optional filter: ONLY=Kabilang bash run_clip_set.sh <label>
  if [ -n "${ONLY:-}" ] && [[ "$c" != *"$ONLY"* ]]; then continue; fi
  n="$(basename "$c" .mp4 | tr ' ' '_')"; rm -f "$OUT/$n.jsonl"
  clk=(); for k in "${!CLOCKS[@]}"; do [[ "$c" == *"$k"* ]] && clk=(--clock "${CLOCKS[$k]}"); done
  s=$(date +%s)
  LOOKOUT_MOMENTUM="${MOMENTUM:-}" LOOKOUT_MOUTH_LOG="$OUT/$n.jsonl" python manage.py watch_merged --source "$V/$c" --dry-run --stats --camera CAM-SMOKE-01 "${clk[@]}" ${EXTRA:-} > "$OUT/$n.log" 2>&1
  e=$(date +%s)
  f=$(python -c "import cv2,sys;c=cv2.VideoCapture(sys.argv[1]);print(int(c.get(7)))" "$V/$c")
  echo "$n frames=$f wall_s=$((e-s))" | tee -a "$OUT/times.txt"
done
# evidence files the dry runs wrote into media/violations
find media/violations -newer "$OUT/.stamp" -type f -delete
echo "done -> $OUT"
