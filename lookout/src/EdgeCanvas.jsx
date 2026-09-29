import { useEffect, useRef, useState } from "react";
import { ChevronRight, Undo2, Trash2, FlipHorizontal, Hexagon, Spline } from "lucide-react";

// Ported from detection_sandbox/obstruction_web.py's drawing screen — same
// geometry, same interaction, so an area drawn here means exactly what
// watch_parking and the sandbox tester already agree it means. Extracted out
// of EdgeEditorModal so both it (per-camera obstruction areas, live snapshot or
// an uploaded clip) and RunDetectionPage (upload -> draw -> detect) share one
// implementation of the canvas geometry instead of forking it.
//
// TWO WAYS TO MARK THE NO-PARKING AREA, mirroring core/vision/obstruction.py:
//
//   ZONE  — trace the road itself as a closed polygon. Anything standing
//           inside it is an obstruction. Nothing to get backwards, and it says
//           nothing about the rest of the frame.
//   EDGES — mark one or both kerbs as open lines plus the side the footpath is
//           on. Right when the footpath runs off the bottom of the frame and
//           has no far boundary to trace.
//
// Zone is the default: an edge divides the WHOLE frame in two, so it also
// condemns whatever else happens to sit on the footpath side — a yard, a shop
// front, the opposite pavement. A polygon only ever means the ground inside it.
const BAND = 46; // how far the shaded protected-side band extends, in canvas px
const EDGE_COLOURS = { left: "#ffa657", right: "#56d4ff" };
const ZONE_COLOUR = "#ff5f56";
const ZONE_MIN_POINTS = 3;
const EDGE_MIN_POINTS = 2;

// Default the protected side to whichever faces the nearer frame edge: the
// road runs up the middle and footpaths sit on the outside.
function defaultSide(points, canvasWidth) {
  const a = points[0], b = points[points.length - 1];
  const mx = (a.x + b.x) / 2;
  const dx = b.x - a.x, dy = b.y - a.y;
  const length = Math.hypot(dx, dy) || 1;
  const nx = -dy / length;
  return mx < canvasWidth / 2 ? (nx < 0 ? 1 : -1) : (nx > 0 ? 1 : -1);
}

// Offsets a path along its local normal — shades the protected side of a
// path that may bend, without any polygon clipping.
function offsetPath(points, side, dist) {
  return points.map((q, i) => {
    const a = points[Math.max(i - 1, 0)];
    const b = points[Math.min(i + 1, points.length - 1)];
    const dx = b.x - a.x, dy = b.y - a.y;
    const length = Math.hypot(dx, dy) || 1;
    return { x: q.x - (dy / length) * dist * side, y: q.y + (dx / length) * dist * side };
  });
}

function drawVertices(ctx, points, colour) {
  ctx.fillStyle = colour;
  points.forEach((q) => {
    ctx.beginPath();
    ctx.arc(q.x, q.y, 5, 0, Math.PI * 2);
    ctx.fill();
  });
}

function drawPath(ctx, points, colour, side) {
  if (points.length >= 2) {
    const off = offsetPath(points, side, BAND);
    ctx.fillStyle = `${colour}33`;
    ctx.beginPath();
    ctx.moveTo(points[0].x, points[0].y);
    points.forEach((q) => ctx.lineTo(q.x, q.y));
    for (let i = off.length - 1; i >= 0; i--) ctx.lineTo(off[i].x, off[i].y);
    ctx.closePath();
    ctx.fill();

    ctx.strokeStyle = colour;
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(points[0].x, points[0].y);
    points.forEach((q) => ctx.lineTo(q.x, q.y));
    ctx.stroke();
  }
  drawVertices(ctx, points, colour);
}

// The polygon is drawn CLOSED from the third point on, so what the operator
// sees is the filled area the rule will actually test — not an open path they
// then have to imagine joined up.
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

// Builds the spec exactly as EdgeEditorModal.handleSave, DetectionJobViewSet
// .create's `edges` field and obstruction.build_edge expect it.
//
// A zone carries "type": "zone"; an edge omits `type` entirely, which
// build_edge reads as its default — so a spec saved before zones existed still
// round-trips through here byte-identically.
function toSpec(paths, sides, mode) {
  const spec = {};
  if (mode === "zone") {
    if (paths.road.length >= ZONE_MIN_POINTS) {
      spec.road = {
        type: "zone",
        points: paths.road.map((p) => [Math.round(p.x), Math.round(p.y)]),
      };
    }
    return spec;
  }
  for (const side of ["left", "right"]) {
    if (paths[side].length >= EDGE_MIN_POINTS) {
      spec[side] = { points: paths[side].map((p) => [Math.round(p.x), Math.round(p.y)]), side: sides[side] };
    }
  }
  return spec;
}

function isSaveable(paths, mode) {
  return mode === "zone"
    ? paths.road.length >= ZONE_MIN_POINTS
    : paths.left.length >= EDGE_MIN_POINTS || paths.right.length >= EDGE_MIN_POINTS;
}

/**
 * Controlled-ish drawing canvas for the parking-obstruction area: owns
 * paths/sides/mode/active internally (seeded once from initialPaths/
 * initialSides/initialMode at mount — callers must not render this until
 * `frame` and any prefill are both already known), and reports the current
 * spec + whether it's save-able via onChange whenever the geometry settles.
 *
 * `frame` = { src, width, height }, width/height being the SOURCE frame's
 * real pixel dimensions, never the CSS-displayed canvas size.
 */
export function EdgeCanvas({
  frame,
  initialPaths,
  initialSides,
  initialMode,
  pct,
  onPctChange,
  minutes,
  onMinutesChange,
  onChange,
}) {
  const [paths, setPaths] = useState(() => ({
    left: [], right: [], road: [], ...(initialPaths ?? {}),
  }));
  const [sides, setSides] = useState(initialSides ?? { left: 1, right: 1 });
  const [mode, setMode] = useState(initialMode ?? "zone");
  const [active, setActive] = useState("left");

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
    if (mode === "zone") {
      drawZone(ctx, paths.road, ZONE_COLOUR);
    } else {
      drawPath(ctx, paths.left, EDGE_COLOURS.left, sides.left);
      drawPath(ctx, paths.right, EDGE_COLOURS.right, sides.right);
    }
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
    onChange?.(toSpec(paths, sides, mode), isSaveable(paths, mode));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paths, sides, mode]);

  // Converts a click's CSS coordinates to the canvas's real pixel space —
  // canvas.width/height are the source frame's native size, so this ratio
  // lands in true frame-pixel space no matter how small the browser has
  // scaled the canvas down to fit.
  const handleCanvasClick = (e) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const point = {
      x: (e.clientX - rect.left) * (canvas.width / rect.width),
      y: (e.clientY - rect.top) * (canvas.height / rect.height),
    };
    if (mode === "zone") {
      setPaths((prev) => ({ ...prev, road: [...prev.road, point] }));
      return;
    }
    const nextActivePath = [...paths[active], point];
    setPaths((prev) => ({ ...prev, [active]: nextActivePath }));
    if (nextActivePath.length >= EDGE_MIN_POINTS) {
      setSides((prev) => ({ ...prev, [active]: defaultSide(nextActivePath, canvas.width) }));
    }
  };

  const currentKey = mode === "zone" ? "road" : active;

  const handleUndo = () => {
    const next = paths[currentKey].slice(0, -1);
    setPaths((prev) => ({ ...prev, [currentKey]: next }));
    if (mode !== "zone" && next.length >= EDGE_MIN_POINTS && canvasRef.current) {
      setSides((prev) => ({ ...prev, [currentKey]: defaultSide(next, canvasRef.current.width) }));
    }
  };

  const handleClear = () => {
    if (mode === "zone") {
      setPaths((prev) => ({ ...prev, road: [] }));
      return;
    }
    setPaths((prev) => ({ ...prev, left: [], right: [] }));
    setActive("left");
  };

  const handleFlip = (side) => setSides((prev) => ({ ...prev, [side]: -prev[side] }));
  const handleNextEdge = () => setActive((a) => (a === "left" ? "right" : "left"));

  if (!frame) return null;

  const otherSide = active === "left" ? "right" : "left";
  const count = paths[currentKey].length;
  const zoneMode = mode === "zone";

  const modeButton = (value, Icon, label) => (
    <button
      key={value}
      onClick={() => setMode(value)}
      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[14px] font-medium"
      style={{
        background: mode === value ? "var(--primary, #0b5471)" : "transparent",
        color: mode === value ? "#fff" : "var(--muted-foreground)",
        border: "1px solid transparent",
      }}
    >
      <Icon size={13} /> {label}
    </button>
  );

  return (
    <>
      <div className="flex items-center gap-1 p-1 rounded-xl w-fit"
        style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
        {modeButton("zone", Hexagon, "Road zone")}
        {modeButton("edges", Spline, "Kerb lines")}
      </div>

      <div className="rounded-xl p-3" style={{ background: "rgba(11,84,113,0.06)", border: "1px solid rgba(11,84,113,0.2)" }}>
        <span className="text-[13px]" style={{ color: "var(--muted-foreground)" }}>
          {zoneMode ? (
            <>
              Tracing the{" "}
              <span className="font-semibold" style={{ color: ZONE_COLOUR }}>ROAD ZONE</span>
              {" "}— {count} point{count === 1 ? "" : "s"}. Click around the edge of the road to enclose it;
              any vehicle standing inside counts as an obstruction.
              {" "}{count >= ZONE_MIN_POINTS
                ? "The shape closes automatically — add more points where the road bends."
                : `At least ${ZONE_MIN_POINTS} points needed.`}
              {" "}Follow the road as it narrows into the distance rather than drawing a rectangle — a camera
              sees the carriageway as a trapezium, not a box.
            </>
          ) : (
            <>
              Drawing the{" "}
              <span className="font-semibold" style={{ color: EDGE_COLOURS[active] }}>{active.toUpperCase()}</span>
              {" "}kerb — {count} point{count === 1 ? "" : "s"}. Click along the kerb; add extra points
              where it bends. {count >= EDGE_MIN_POINTS ? "Then Next edge, or continue." : "At least 2 points needed."}
              {" "}Either kerb alone is fine if the street only has one. The shaded band shows which side
              is protected — flip it if it landed on the road.
            </>
          )}
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
        {!zoneMode && (
          <button onClick={handleNextEdge}
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-[14px] font-medium"
            style={{ background: "var(--secondary)", color: "var(--foreground)", border: "1px solid var(--border)" }}>
            <ChevronRight size={13} /> Next edge ({otherSide})
          </button>
        )}
        <button onClick={handleUndo} disabled={!count}
          className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-[14px] font-medium disabled:opacity-40"
          style={{ background: "var(--secondary)", color: "var(--foreground)", border: "1px solid var(--border)" }}>
          <Undo2 size={13} /> Undo point
        </button>
        <button onClick={handleClear}
          className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-[14px] font-medium"
          style={{ background: "var(--secondary)", color: "var(--foreground)", border: "1px solid var(--border)" }}>
          <Trash2 size={13} /> {zoneMode ? "Clear zone" : "Clear both"}
        </button>
        {!zoneMode && (
          <>
            <button onClick={() => handleFlip("left")} disabled={paths.left.length < EDGE_MIN_POINTS}
              className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-[14px] font-medium disabled:opacity-40"
              style={{ background: "var(--secondary)", color: EDGE_COLOURS.left, border: "1px solid var(--border)" }}>
              <FlipHorizontal size={13} /> Flip left side
            </button>
            <button onClick={() => handleFlip("right")} disabled={paths.right.length < EDGE_MIN_POINTS}
              className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-[14px] font-medium disabled:opacity-40"
              style={{ background: "var(--secondary)", color: EDGE_COLOURS.right, border: "1px solid var(--border)" }}>
              <FlipHorizontal size={13} /> Flip right side
            </button>
          </>
        )}
      </div>

      <div className="flex items-center gap-5 pt-1">
        <label className="flex items-center gap-2 text-[14px]" style={{ color: "var(--muted-foreground)" }}>
          {zoneMode ? "Inside the road" : "Past the line"}
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
    </>
  );
}
