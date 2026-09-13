import { useEffect, useRef, useState } from "react";
import { ChevronRight, Undo2, Trash2, FlipHorizontal } from "lucide-react";

// Ported from detection_sandbox/obstruction_web.py's drawing screen — same
// geometry, same interaction, so an edge drawn here means exactly what
// watch_parking and the sandbox tester already agree it means. Extracted out
// of EdgeEditorModal so both it (per-camera edge zones, live snapshot or an
// uploaded clip) and RunDetectionPage (upload -> draw -> detect) share one
// implementation of the canvas geometry instead of forking it.
const BAND = 46; // how far the shaded protected-side band extends, in canvas px
const EDGE_COLOURS = { left: "#ffa657", right: "#56d4ff" };

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
  ctx.fillStyle = colour;
  points.forEach((q) => {
    ctx.beginPath();
    ctx.arc(q.x, q.y, 5, 0, Math.PI * 2);
    ctx.fill();
  });
}

// Builds the {left,right} points/side spec exactly as EdgeEditorModal.handleSave
// and DetectionJobViewSet.create's `edges` field expect it.
function toSpec(paths, sides) {
  const spec = {};
  for (const side of ["left", "right"]) {
    if (paths[side].length >= 2) {
      spec[side] = { points: paths[side].map((p) => [Math.round(p.x), Math.round(p.y)]), side: sides[side] };
    }
  }
  return spec;
}

/**
 * Controlled-ish edge-drawing canvas: owns paths/sides/active internally
 * (seeded once from initialPaths/initialSides at mount — callers must not
 * render this until `frame` and any prefill are both already known), and
 * reports the current spec + whether it's save-able via onChange whenever
 * the geometry settles.
 *
 * `frame` = { src, width, height }, width/height being the SOURCE frame's
 * real pixel dimensions, never the CSS-displayed canvas size.
 */
export function EdgeCanvas({
  frame,
  initialPaths,
  initialSides,
  pct,
  onPctChange,
  minutes,
  onMinutesChange,
  onChange,
}) {
  const [paths, setPaths] = useState(initialPaths ?? { left: [], right: [] });
  const [sides, setSides] = useState(initialSides ?? { left: 1, right: 1 });
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
    drawPath(ctx, paths.left, EDGE_COLOURS.left, sides.left);
    drawPath(ctx, paths.right, EDGE_COLOURS.right, sides.right);
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
    onChange?.(toSpec(paths, sides), paths.left.length >= 2 || paths.right.length >= 2);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paths, sides]);

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
    const nextActivePath = [...paths[active], point];
    setPaths((prev) => ({ ...prev, [active]: nextActivePath }));
    if (nextActivePath.length >= 2) {
      setSides((prev) => ({ ...prev, [active]: defaultSide(nextActivePath, canvas.width) }));
    }
  };

  const handleUndo = () => {
    const next = paths[active].slice(0, -1);
    setPaths((prev) => ({ ...prev, [active]: next }));
    if (next.length >= 2 && canvasRef.current) {
      setSides((prev) => ({ ...prev, [active]: defaultSide(next, canvasRef.current.width) }));
    }
  };

  const handleClearBoth = () => {
    setPaths({ left: [], right: [] });
    setActive("left");
  };

  const handleFlip = (side) => setSides((prev) => ({ ...prev, [side]: -prev[side] }));
  const handleNextEdge = () => setActive((a) => (a === "left" ? "right" : "left"));

  if (!frame) return null;

  const otherSide = active === "left" ? "right" : "left";
  const activeCount = paths[active].length;

  return (
    <>
      <div className="rounded-xl p-3" style={{ background: "rgba(11,84,113,0.06)", border: "1px solid rgba(11,84,113,0.2)" }}>
        <span className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>
          Drawing the{" "}
          <span className="font-semibold" style={{ color: EDGE_COLOURS[active] }}>{active.toUpperCase()}</span>
          {" "}edge — {activeCount} point{activeCount === 1 ? "" : "s"}. Click along the edge; add extra points
          where it bends. {activeCount >= 2 ? "Then Next edge, or continue." : "At least 2 points needed."}
          {" "}Either edge alone is fine if the street only has one.
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
        <button onClick={handleNextEdge}
          className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-[12px] font-medium"
          style={{ background: "var(--secondary)", color: "var(--foreground)", border: "1px solid var(--border)" }}>
          <ChevronRight size={13} /> Next edge ({otherSide})
        </button>
        <button onClick={handleUndo} disabled={!paths[active].length}
          className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-[12px] font-medium disabled:opacity-40"
          style={{ background: "var(--secondary)", color: "var(--foreground)", border: "1px solid var(--border)" }}>
          <Undo2 size={13} /> Undo point
        </button>
        <button onClick={handleClearBoth}
          className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-[12px] font-medium"
          style={{ background: "var(--secondary)", color: "var(--foreground)", border: "1px solid var(--border)" }}>
          <Trash2 size={13} /> Clear both
        </button>
        <button onClick={() => handleFlip("left")} disabled={paths.left.length < 2}
          className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-[12px] font-medium disabled:opacity-40"
          style={{ background: "var(--secondary)", color: EDGE_COLOURS.left, border: "1px solid var(--border)" }}>
          <FlipHorizontal size={13} /> Flip left side
        </button>
        <button onClick={() => handleFlip("right")} disabled={paths.right.length < 2}
          className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-[12px] font-medium disabled:opacity-40"
          style={{ background: "var(--secondary)", color: EDGE_COLOURS.right, border: "1px solid var(--border)" }}>
          <FlipHorizontal size={13} /> Flip right side
        </button>
      </div>

      <div className="flex items-center gap-5 pt-1">
        <label className="flex items-center gap-2 text-[12px]" style={{ color: "var(--muted-foreground)" }}>
          Past the line
          <input type="number" min={10} max={90} value={pct}
            onChange={(e) => onPctChange(e.target.value)}
            className="w-16 px-2 py-1.5 rounded-lg text-sm outline-none"
            style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }} />
          %
        </label>
        <label className="flex items-center gap-2 text-[12px]" style={{ color: "var(--muted-foreground)" }}>
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
