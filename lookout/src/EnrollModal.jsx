import { useState, useRef, useEffect } from "react";
import {
  X, Upload, Cpu, ChevronRight, CheckCircle, UserPlus,
  RefreshCw, AlertTriangle, Loader2,
} from "lucide-react";
import { createPerson, enrollFace } from "./api";

const ANGLES = [
  { angle: "front", label: "Front",      icon: "⬤", hint: "Face the camera directly" },
  { angle: "right", label: "Right side", icon: "◐", hint: "Turn your head to the right" },
  { angle: "left",  label: "Left side",  icon: "◑", hint: "Turn your head to the left" },
];

// ── Processing step (cosmetic — mirrors the real request's rough timeline) ────
function ProcessingStep({ label, delayMs, failed }) {
  const [done, setDone] = useState(false);
  const [active, setActive] = useState(false);
  useEffect(() => {
    const t1 = window.setTimeout(() => setActive(true), delayMs);
    const t2 = window.setTimeout(() => setDone(true), delayMs + 500);
    return () => { window.clearTimeout(t1); window.clearTimeout(t2); };
  }, [delayMs]);
  const state = failed ? "failed" : done ? "done" : active ? "active" : "idle";
  return (
    <div className="flex items-center gap-3 px-3.5 py-2.5 rounded-lg transition-all"
      style={{
        background: state === "failed" ? "rgba(239,68,68,0.06)" : state === "done" ? "rgba(16,185,129,0.06)" : state === "active" ? "rgba(245,158,11,0.06)" : "var(--secondary)",
        border: `1px solid ${state === "failed" ? "rgba(239,68,68,0.2)" : state === "done" ? "rgba(16,185,129,0.15)" : state === "active" ? "rgba(245,158,11,0.15)" : "var(--border)"}`,
        opacity: state === "idle" ? 0.4 : 1,
      }}>
      {state === "failed"
        ? <AlertTriangle size={13} style={{ color: "#ef4444", flexShrink: 0 }} />
        : state === "done"
          ? <CheckCircle size={13} style={{ color: "#10b981", flexShrink: 0 }} />
          : state === "active"
            ? <div className="w-3 h-3 rounded-full border-2 border-t-transparent animate-spin flex-shrink-0"
                style={{ borderColor: "var(--primary)", borderTopColor: "transparent" }} />
            : <div className="w-3 h-3 rounded-full flex-shrink-0" style={{ background: "var(--muted-foreground)", opacity: 0.3 }} />
      }
      <span className="text-[12px]"
        style={{ color: state === "failed" ? "#ef4444" : state === "done" ? "#10b981" : state === "active" ? "var(--primary)" : "var(--muted-foreground)" }}>
        {label}
      </span>
    </div>
  );
}

// ── Main modal ────────────────────────────────────────────────────────────────
// `person` — pass an existing Person to skip straight to the face-photo step
// ("Enroll faces" on a registry card). Omit it to start at the name-only
// "add person" form ("Add person" button) — both are step 1, "Select or add
// person," just rendered differently depending on how the modal was opened.
export function EnrollModal({ person = null, onClose, onEnrolled }) {
  const [step, setStep] = useState(person ? "photo" : "add");
  const [selected, setSelected] = useState(person);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");

  const [photos, setPhotos] = useState({
    front: { file: null, preview: null },
    right: { file: null, preview: null },
    left:  { file: null, preview: null },
  });
  const [angleErrors, setAngleErrors] = useState({ front: "", right: "", left: "" });
  const [submitError, setSubmitError] = useState("");
  const [dragOver, setDragOver] = useState(null);
  const [enrolledPerson, setEnrolledPerson] = useState(null);

  const frontRef = useRef(null);
  const rightRef = useRef(null);
  const leftRef  = useRef(null);
  const fileRefs = { front: frontRef, right: rightRef, left: leftRef };

  const allPhotosReady = photos.front.file && photos.right.file && photos.left.file;

  const handleCreate = async () => {
    if (!newName.trim()) return;
    setCreating(true);
    setCreateError("");
    try {
      const created = await createPerson({ full_name: newName.trim() });
      setSelected(created);
      setStep("photo");
    } catch (err) {
      setCreateError(err.message || "Failed to create person.");
    } finally {
      setCreating(false);
    }
  };

  const handleFile = (angle, file) => {
    if (!file.type.startsWith("image/")) return;
    setPhotos((prev) => ({ ...prev, [angle]: { file, preview: URL.createObjectURL(file) } }));
    setAngleErrors((prev) => ({ ...prev, [angle]: "" }));
  };

  const clearPhoto = (angle) => {
    setPhotos((prev) => ({ ...prev, [angle]: { file: null, preview: null } }));
    setAngleErrors((prev) => ({ ...prev, [angle]: "" }));
  };

  const handleProcess = async () => {
    if (!selected || !allPhotosReady) return;
    setStep("processing");
    setSubmitError("");
    setAngleErrors({ front: "", right: "", left: "" });

    const formData = new FormData();
    formData.append("front", photos.front.file);
    formData.append("right", photos.right.file);
    formData.append("left", photos.left.file);

    const started = Date.now();
    try {
      const updated = await enrollFace(selected.dbId ?? selected.id, formData);
      // Keep the processing checklist on screen for a beat even on a fast
      // response, so it doesn't flash past before the user can read it.
      const elapsed = Date.now() - started;
      if (elapsed < 1800) await new Promise((r) => setTimeout(r, 1800 - elapsed));
      setEnrolledPerson(updated);
      setStep("done");
    } catch (err) {
      const angle = err.data?.angle;
      if (angle && angleErrors[angle] !== undefined) {
        setAngleErrors((prev) => ({ ...prev, [angle]: err.data.detail || err.message }));
      } else {
        setSubmitError(err.data?.detail || err.message || "Enrollment failed.");
      }
      setStep("photo");
    }
  };

  const steps = [
    { id: "add",        label: "Select or add person" },
    { id: "photo",      label: "Face photo" },
    { id: "processing", label: "Processing" },
    { id: "done",       label: "Done" },
  ];
  const stepOrder = ["add", "photo", "processing", "done"];
  const currentIdx = stepOrder.indexOf(step === "select" ? "add" : step);

  // Once enrollment finishes, pop out a dedicated success modal.
  if (step === "done" && enrolledPerson) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
        style={{ background: "rgba(0,0,0,0.78)", backdropFilter: "blur(8px)" }}
        onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
        <div className="w-full max-w-sm rounded-2xl overflow-hidden shadow-2xl"
          style={{ background: "var(--card)", border: "1px solid var(--border)" }}
          onClick={(e) => e.stopPropagation()}>
          <div className="flex flex-col items-center text-center px-6 pt-7 pb-5 gap-4">
            <div className="w-14 h-14 rounded-full flex items-center justify-center"
              style={{ background: "rgba(16,185,129,0.12)", border: "2px solid rgba(16,185,129,0.3)" }}>
              <CheckCircle size={26} style={{ color: "#10b981" }} />
            </div>
            <div>
              <div className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>Enrollment complete</div>
              <div className="text-[12px] mt-1" style={{ color: "var(--muted-foreground)" }}>
                {enrolledPerson.full_name} has been enrolled.
              </div>
            </div>
            <div className="w-full rounded-xl p-3.5 space-y-1.5 text-left"
              style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
              {[
                ["Name",      enrolledPerson.full_name],
                ["Person ID", enrolledPerson.person_code],
                ["Status",    "Enrolled"],
                ["Photos",    "Front · Right · Left (3 angles)"],
                ["Embedding", "insightface · 512-d · buffalo_l"],
              ].map(([k, v]) => (
                <div key={k} className="flex justify-between text-[11px]">
                  <span style={{ color: "var(--muted-foreground)" }}>{k}</span>
                  <span style={{ color: "var(--muted-foreground)", fontFamily: k === "Person ID" ? "'DM Mono', monospace" : undefined }}>{v}</span>
                </div>
              ))}
            </div>
          </div>
          <div className="px-6 pb-6">
            <button onClick={() => { onEnrolled?.(enrolledPerson); onClose(); }} className="w-full py-2.5 rounded-xl text-sm font-medium"
              style={{ background: "#10b981", color: "#fff" }}>
              Done
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.78)", backdropFilter: "blur(8px)" }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="w-full max-w-xl rounded-2xl overflow-hidden shadow-2xl flex flex-col"
        style={{ background: "var(--card)", border: "1px solid var(--border)", maxHeight: "92vh" }}>

        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 flex-shrink-0"
          style={{ borderBottom: "1px solid var(--border)" }}>
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center"
              style={{ background: "rgba(11,84,113,0.12)" }}>
              <UserPlus size={15} style={{ color: "var(--primary)" }} />
            </div>
            <div>
              <div className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>
                {selected ? "Enroll Faces" : "Add Person"}
              </div>
              <div className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>
                {selected ? selected.full_name : "Register a new face-registry entry"}
              </div>
            </div>
          </div>
          {step !== "processing" && (
            <button onClick={onClose} className="p-1.5 rounded-lg"
              style={{ color: "var(--muted-foreground)", background: "var(--secondary)" }}>
              <X size={14} />
            </button>
          )}
        </div>

        {/* Step indicator */}
        <div className="flex items-center justify-center px-5 py-3 flex-shrink-0"
          style={{ borderBottom: "1px solid var(--border)" }}>
          {steps.map((s, i) => {
            const thisIdx = stepOrder.indexOf(s.id);
            const done = thisIdx < currentIdx;
            const active = thisIdx === currentIdx;
            return (
              <div key={s.id} className="flex items-center">
                <div className="flex items-center gap-1.5">
                  <div className="w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold transition-all"
                    style={{
                      background: done ? "#10b981" : active ? "var(--primary)" : "var(--secondary)",
                      color: done ? "#fff" : active ? "var(--primary-foreground)" : "var(--muted-foreground)",
                    }}>
                    {done ? <CheckCircle size={11} /> : i + 1}
                  </div>
                  <span className="text-[11px] font-medium hidden sm:block"
                    style={{ color: active ? "var(--primary)" : done ? "#10b981" : "var(--muted-foreground)" }}>
                    {s.label}
                  </span>
                </div>
                {i < steps.length - 1 && (
                  <div className="w-5 h-px mx-2"
                    style={{ background: done ? "#10b981" : "var(--border)" }} />
                )}
              </div>
            );
          })}
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-5">

          {/* ── Step 1: Select or add person (only reached without a preselected person) ── */}
          {step === "add" && (
            <div className="space-y-3">
              <p className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>
                Enter the person's name to register them in the face registry. You'll capture
                their three enrollment photos next.
              </p>
              <div>
                <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>
                  Full name <span style={{ color: "#ef4444" }}>*</span>
                </label>
                <input
                  autoFocus
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && newName.trim() && !creating) handleCreate(); }}
                  placeholder="e.g. Matthew Angeles"
                  className="w-full px-3 py-2.5 rounded-xl text-sm outline-none"
                  style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }}
                />
              </div>
              {createError && (
                <div className="flex items-center gap-2 text-[11px]" style={{ color: "#ef4444" }}>
                  <AlertTriangle size={12} /> {createError}
                </div>
              )}
            </div>
          )}

          {/* ── Step 2: Face photos ── */}
          {step === "photo" && selected && (
            <div className="space-y-4">
              <div>
                <div className="text-sm font-medium mb-0.5" style={{ color: "var(--foreground)" }}>
                  {selected.full_name}
                </div>
                <p className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>
                  Upload all three angles for accurate insightface embedding.
                </p>
              </div>

              {submitError && (
                <div className="flex items-center gap-2 text-[11px] px-3 py-2 rounded-lg"
                  style={{ background: "rgba(239,68,68,0.08)", color: "#ef4444", border: "1px solid rgba(239,68,68,0.2)" }}>
                  <AlertTriangle size={12} /> {submitError}
                </div>
              )}

              {/* Progress strip */}
              <div className="flex items-center gap-2">
                {ANGLES.map(({ angle }) => (
                  <div key={angle} className="flex-1 flex items-center gap-1.5">
                    <div className="h-1 rounded-full flex-1 transition-all"
                      style={{ background: photos[angle].file ? "#10b981" : "var(--secondary)" }} />
                  </div>
                ))}
                <span className="text-[11px] font-medium ml-1"
                  style={{ color: allPhotosReady ? "#10b981" : "var(--muted-foreground)" }}>
                  {[photos.front, photos.right, photos.left].filter((p) => p.file).length} / 3
                </span>
              </div>

              {/* Three angle cards */}
              <div className="grid grid-cols-3 gap-2.5">
                {ANGLES.map(({ angle, label, icon, hint }) => {
                  const slot = photos[angle];
                  const isOver = dragOver === angle;
                  const error = angleErrors[angle];
                  return (
                    <div key={angle} className="flex flex-col gap-1.5">
                      <div className="flex items-center gap-1">
                        <span className="text-[11px] font-semibold"
                          style={{ color: error ? "#ef4444" : slot.file ? "#10b981" : "var(--muted-foreground)" }}>
                          {label}
                        </span>
                        {slot.file && !error && <CheckCircle size={10} style={{ color: "#10b981" }} />}
                        {error && <AlertTriangle size={10} style={{ color: "#ef4444" }} />}
                      </div>

                      {!slot.preview ? (
                        <div
                          onDrop={(e) => { e.preventDefault(); setDragOver(null); const f = e.dataTransfer.files[0]; if (f) handleFile(angle, f); }}
                          onDragOver={(e) => { e.preventDefault(); setDragOver(angle); }}
                          onDragLeave={() => setDragOver(null)}
                          onClick={() => fileRefs[angle].current?.click()}
                          className="rounded-xl cursor-pointer transition-all flex flex-col items-center justify-center gap-2"
                          style={{
                            aspectRatio: "3/4",
                            border: `2px dashed ${error ? "#ef4444" : isOver ? "var(--primary)" : "var(--border)"}`,
                            background: isOver ? "rgba(245,158,11,0.05)" : "var(--secondary)",
                          }}
                        >
                          <div className="w-8 h-8 rounded-full flex items-center justify-center text-xs"
                            style={{ background: "rgba(245,158,11,0.1)", color: "#f59e0b" }}>
                            {icon}
                          </div>
                          <div className="text-center px-1">
                            <div className="text-[10px] font-medium leading-tight" style={{ color: "var(--foreground)" }}>{hint}</div>
                          </div>
                          <div className="flex items-center gap-1 text-[10px]" style={{ color: "var(--muted-foreground)" }}>
                            <Upload size={10} /> Upload
                          </div>
                        </div>
                      ) : (
                        <div className="relative rounded-xl overflow-hidden" style={{ aspectRatio: "3/4", border: error ? "2px solid #ef4444" : undefined }}>
                          <img src={slot.preview} alt={`${label} angle`} className="w-full h-full object-cover" />
                          {!error && (
                            <div className="absolute inset-0 flex items-center justify-center">
                              <div className="w-10 h-14 rounded relative" style={{ border: "1.5px solid #10b981" }}>
                                {["top-0 left-0 border-t-2 border-l-2","top-0 right-0 border-t-2 border-r-2","bottom-0 left-0 border-b-2 border-l-2","bottom-0 right-0 border-b-2 border-r-2"].map((cls, i) => (
                                  <div key={i} className={`absolute w-2 h-2 ${cls}`} style={{ borderColor: "#10b981" }} />
                                ))}
                              </div>
                            </div>
                          )}
                          <button
                            onClick={() => clearPhoto(angle)}
                            className="absolute top-1.5 right-1.5 p-1 rounded-md transition-all"
                            style={{ background: "rgba(0,0,0,0.65)", color: "#fff", backdropFilter: "blur(4px)" }}
                          >
                            <RefreshCw size={10} />
                          </button>
                          {!error && (
                            <div className="absolute bottom-1.5 left-0 right-0 flex justify-center">
                              <div className="flex items-center gap-1 px-2 py-0.5 rounded-full text-[9px] font-medium"
                                style={{ background: "rgba(16,185,129,0.9)", color: "#fff" }}>
                                <CheckCircle size={9} /> OK
                              </div>
                            </div>
                          )}
                        </div>
                      )}

                      {error && (
                        <div className="text-[10px] leading-snug" style={{ color: "#ef4444" }}>
                          {error}
                        </div>
                      )}

                      <input
                        ref={fileRefs[angle]}
                        type="file"
                        accept="image/jpeg,image/png,image/webp"
                        className="hidden"
                        onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(angle, f); }}
                      />
                    </div>
                  );
                })}
              </div>

              <div className="rounded-xl p-3 space-y-1" style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
                <div className="text-[11px] font-semibold mb-1.5" style={{ color: "var(--foreground)" }}>Photo requirements</div>
                {[
                  "Neutral expression, eyes open for all angles",
                  "Well-lit — no harsh shadows on the face",
                  "No sunglasses or face coverings",
                  "Minimum 200×200 px per photo",
                ].map((req) => (
                  <div key={req} className="flex items-center gap-2 text-[11px]" style={{ color: "var(--muted-foreground)" }}>
                    <div className="w-1 h-1 rounded-full flex-shrink-0" style={{ background: "var(--muted-foreground)" }} />
                    {req}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ── Step 3: Processing ── */}
          {step === "processing" && (
            <div className="flex flex-col items-center justify-center py-8 gap-5">
              {photos.front.preview && (
                <div className="relative">
                  <img src={photos.front.preview} alt="Processing"
                    className="w-24 h-24 rounded-xl object-cover"
                    style={{ border: "2px solid rgba(245,158,11,0.3)" }} />
                  <div className="absolute -inset-1.5 rounded-xl border-2 border-transparent border-t-amber-400 animate-spin"
                    style={{ animationDuration: "1.2s" }} />
                </div>
              )}
              <div className="text-center space-y-1.5">
                <div className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>Generating embeddings…</div>
                <div className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>insightface · buffalo_l backend</div>
              </div>
              <div className="w-full space-y-2">
                {[
                  { label: "Detecting faces — SCRFD (buffalo_l)",        delay: 0    },
                  { label: "Aligning to 112×112 face template",         delay: 500  },
                  { label: "Extracting 512-d insightface embeddings ×3", delay: 1000 },
                  { label: "Validating quality per angle",               delay: 1400 },
                  { label: "Writing entries to face_db.json",            delay: 1800 },
                ].map((item, i) => (
                  <ProcessingStep key={i} label={item.label} delayMs={item.delay} />
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-3 px-5 py-4 flex-shrink-0"
          style={{ borderTop: "1px solid var(--border)" }}>
          {step === "add" && (
            <>
              <button onClick={onClose} className="px-4 py-2 rounded-xl text-sm font-medium"
                style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
                Cancel
              </button>
              <button
                disabled={!newName.trim() || creating}
                onClick={handleCreate}
                className="flex items-center gap-2 px-5 py-2 rounded-xl text-sm font-medium transition-all"
                style={{
                  background: newName.trim() ? "var(--primary)" : "rgba(11,84,113,0.2)",
                  color: newName.trim() ? "var(--primary-foreground)" : "rgba(133,183,214,0.4)",
                  cursor: newName.trim() && !creating ? "pointer" : "not-allowed",
                }}>
                {creating ? <Loader2 size={13} className="animate-spin" /> : <ChevronRight size={14} />}
                {creating ? "Creating…" : "Continue"}
              </button>
            </>
          )}
          {step === "photo" && (
            <>
              <button onClick={onClose} className="px-4 py-2 rounded-xl text-sm font-medium"
                style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
                Cancel
              </button>
              <button
                disabled={!allPhotosReady}
                onClick={() => allPhotosReady && handleProcess()}
                className="flex items-center gap-2 px-5 py-2 rounded-xl text-sm font-medium transition-all"
                style={{
                  background: allPhotosReady ? "var(--primary)" : "rgba(245,158,11,0.2)",
                  color: allPhotosReady ? "var(--primary-foreground)" : "rgba(245,158,11,0.4)",
                  cursor: allPhotosReady ? "pointer" : "not-allowed",
                }}>
                <Cpu size={13} />
                {allPhotosReady ? "Generate embedding" : "Upload all 3 photos"}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
