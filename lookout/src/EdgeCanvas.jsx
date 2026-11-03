import { useEffect, useRef, useState } from "react";
import { Undo2, Trash2 } from "lucide-react";

// The drawing canvas for an area a detector judges against. Extracted out of
// EdgeEditorModal so both it (per-camera areas, live snapshot or an uploaded
// clip) and RunDetectionPage (upload -> draw -> detect) share one
// implementation of the geometry instead of forking it.
//
// ONE SHAPE: a closed polygon around the road. Any vehicle whose ground point
// (bottom-centre of its box — where the wheels meet the road) sits inside it
// is in the no-parking area.
//
// This used to offer a second mode, "kerb lines": one or two open paths plus
// the side the footpath was on, defining a half-plane. It was dropped with the
// move to core/vision/obstruction_zone.py, which tests a point against a
// closed polygon (cv2.pointPolygonTest) and has no way to express a half-plane
// running off to infinity. The polygon was the better of the two anyway — an
// open kerb line divides the WHOLE frame in two, so it also condemns whatever
// sits on the footpath side of it: a yard, a shop front, the opposite
// pavement. A polygon only ever means the ground inside it.
const ZONE_COLOUR = "#ff5f56";
const ZONE_MIN_POINTS = 3;

// What the operator is told they are drawing. It changes with `kind`, because
// the same canvas serves two rules and the wrong sentence is worse than none:
// somebody marking where rubbish gets left should not read that vehicles
// standing inside count as an obstruction.
const KIND_COPY = {
  zone: {
    name: "ROAD ZONE",
    what: "Click around the edge of the road to enclose it; any vehicle standing inside counts as an obstruction.",
    hint: "Follow the road as it narrows into the distance rather than drawing a rectangle — a camera sees the carriageway as a trapezium, not a box.",
  },
  disposal: {
    name: "RUBBISH AREA",
    what: "Click around the ground to watch for dumped rubbish; anything left inside it is checked.",
    // The opposite advice to the road zone's, and deliberately so: rubbish is
    // left where vehicles do not drive, so an area traced tightly around the
    // carriageway is the one shape that misses it.
    hint: "Include the kerb, verge and wall line, not just the carriageway — rubbish is left off the road, not on it.",
  },
};

function drawVertices(ctx, points, colour) {
  ctx.fillStyle = colour;
  points.forEach((q) => {
    ctx.beginPath();
    ctx.arc(q.x, q.y, 5, 0, Math.PI * 2);
    ctx.fill();
  });
}

// Drawn CLOSED from the third point on, so what the operator sees is the
// filled area the rule will actually test — not an open path they then have to
// imagine joined up.
function drawZone(ctx, points, colour) {
  if (points.length >= 2) {
    ctx.beginPath();
    ctx.moveTo(points[0].x, points[0].y);
    points.forEach((q) => ctx.lineTo(q.x, q.y));
    if (points.length >= ZONE_MIN_POINTS) {
      ctx.closePath();
      ctx.fillStyle = `${colour}2e`;
      ctx.fill();
    }
    ctx.strokeStyle = colour;
    ctx.lineWidth = 3;
    ctx.stroke();
  }
  drawVertices(ctx, points, colour);
}

// The spec EdgeEditorModal.handleSave and DetectionJobViewSet.create's `edges`
// field store, and obstruction.build_edge / ObstructionZone read.
//
// "type": "zone" is still written explicitly even though it is now the only
// shape: core/vision/obstruction.py's build_edge defaults a MISSING type to
// EDGE, so dropping it would silently turn every polygon into an open path
// along its own outline. watch_merged_all still runs that code.
// `kind` decides WHICH rule the polygon belongs to, and the two must stay
// distinguishable in storage. Both detectors used to read any entry typed
// "zone", so one drawn shape meant two different things at once: a tight
// no-parking area also became disposal's search area, confining the hunt for
// rubbish to the carriageway — where rubbish is least likely to be left.
//
// "zone"     the no-parking area. Parking (and Merged All 4/5) judge vehicles
//            against it; watch_parking traces one from traffic when absent.
// "disposal" where to watch for dumped rubbish. Only disposal reads it, so
//            drawing one leaves parking's own area alone.
function toSpec(points, kind = "zone") {
  if (points.length < ZONE_MIN_POINTS) return {};
  const key = kind === "disposal" ? "disposal" : "road";
  return {
    [key]: {
      type: kind,
      points: points.map((p) => [Math.round(p.x), Math.round(p.y)]),
    },
  };
}

/**
 * Controlled-ish drawing canvas for the parking no-parking area: owns the
 * points internally (seeded once from `initialPoints` at mount — callers must
 * not render this until `frame` and any prefill are both already known), and
 * reports the current spec + whether it's save-able via onChange whenever the
 * geometry settles.
 *
 * `frame` = { src, width, height }, width/height being the SOURCE frame's
 * real pixel dimensions, never the CSS-displayed canvas size.
 */
export function EdgeCanvas({
  frame,
  // Which rule the polygon is for — see toSpec. Defaults to the no-parking
  // area, which is what every caller but the All 5 run wants.
  kind = "zone",
  initialPoints,
  pct,
  onPctChange,
  minutes,
  onMinutesChange,
  onChange,
  // Parking's two thresholds. Hidden for a detector whose area is a plain
  // containment test — showing "Inside the road %" next to a garbage area
  // would offer a dial that changes nothing.
  showThresholds = true,
}) {
  const [points, setPoints] = useState(() => initialPoints ?? []);

  const canvasRef = useRef(null);
  const imgElRef = useRef(null);

  function redraw() {
    const canvas = canvasRef.current;
    const img = imgElRef.current;
    if (!canvas || !img || !frame) return;
    canvas.width = frame.width;
    canvas.height = frame.height;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    drawZone(ctx, points, ZONE_COLOUR);
  }

  // Loads the frame image once per `frame.src`, then redraws.
  useEffect(() => {
    if (!frame) return;
    const img = new Image();
    img.onload = () => { imgElRef.current = img; redraw(); };
    img.src = frame.src;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [frame?.src]);

  // Redraws whenever the geometry changes, and reports the current spec up.
  useEffect(() => {
    redraw();
    onChange?.(toSpec(points, kind), points.length >= ZONE_MIN_POINTS);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [points]);

  // Converts a click's CSS coordinates to the canvas's real pixel space —
  // canvas.width/height are the source frame's native size, so this ratio
  // lands in true frame-pixel space no matter how small the browser has
  // scaled the canvas down to fit.
  const handleCanvasClick = (e) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    setPoints((prev) => [...prev, {
      x: (e.clientX - rect.left) * (canvas.width / rect.width),
      y: (e.clientY - rect.top) * (canvas.height / rect.height),
    }]);
  };

  if (!frame) return null;

  const count = points.length;
  const copy = KIND_COPY[kind] ?? KIND_COPY.zone;

  return (
    <>
      <div className="rounded-xl p-3" style={{ background: "rgba(11,84,113,0.06)", border: "1px solid rgba(11,84,113,0.2)" }}>
        <span className="text-[13px]" style={{ color: "var(--muted-foreground)" }}>
          Tracing the{" "}
          <span className="font-semibold" style={{ color: ZONE_COLOUR }}>{copy.name}</span>
          {" "}— {count} point{count === 1 ? "" : "s"}. {copy.what}
          {" "}{count >= ZONE_MIN_POINTS
            ? "The shape closes automatically — add more points where it bends."
            : `At least ${ZONE_MIN_POINTS} points needed.`}
          {" "}{copy.hint}
        </span>
      </div>

      <div className="rounded-xl overflow-hidden" style={{ border: "1px solid var(--border)", background: "#000" }}>
        <canvas
          ref={canvasRef}
          onClick={handleCanvasClick}
          style={{ width: "100%", display: "block", cursor: "crosshair" }}
        />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button onClick={() => setPoints((prev) => prev.slice(0, -1))} disabled={!count}
          className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-[14px] font-medium disabled:opacity-40"
          style={{ background: "var(--secondary)", color: "var(--foreground)", border: "1px solid var(--border)" }}>
          <Undo2 size={13} /> Undo point
        </button>
        <button onClick={() => setPoints([])}
          className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-[14px] font-medium"
          style={{ background: "var(--secondary)", color: "var(--foreground)", border: "1px solid var(--border)" }}>
          <Trash2 size={13} /> Clear area
        </button>
      </div>

      {showThresholds && (
      <div className="flex items-center gap-5 pt-1">
        <label className="flex items-center gap-2 text-[14px]" style={{ color: "var(--muted-foreground)" }}>
          Inside the road
          <input type="number" min={10} max={90} value={pct}
            onChange={(e) => onPctChange(e.target.value)}
            className="w-16 px-2 py-1.5 rounded-lg text-sm outline-none"
            style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }} />
          %
        </label>
        <label className="flex items-center gap-2 text-[14px]" style={{ color: "var(--muted-foreground)" }}>
          for
          <input type="number" min={0.1} step={0.1} value={minutes}
            onChange={(e) => onMinutesChange(e.target.value)}
            className="w-16 px-2 py-1.5 rounded-lg text-sm outline-none"
            style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }} />
          minutes
        </label>
      </div>
      )}
    </>
  );
}
