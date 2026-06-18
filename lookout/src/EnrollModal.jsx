import { useState, useRef, useEffect } from "react";
import {
  X, Upload, Camera, Cpu, ChevronRight, CheckCircle, UserPlus,
} from "lucide-react";

function computeAge(birthdate) {
  if (!birthdate) return 0;
  const today = new Date();
  const dob = new Date(birthdate);
  let age = today.getFullYear() - dob.getFullYear();
  const m = today.getMonth() - dob.getMonth();
  if (m < 0 || (m === 0 && today.getDate() < dob.getDate())) age--;
  return age;
}

const emptyForm = {
  firstName: "", middleName: "", lastName: "", birthdate: "", phone: "",
  imageFile: null, imagePreview: null,
};

function ProcessingStep({ label, delayMs }) {
  const [done, setDone] = useState(false);
  const [active, setActive] = useState(false);
  useEffect(() => {
    const t1 = window.setTimeout(() => setActive(true), delayMs);
    const t2 = window.setTimeout(() => setDone(true), delayMs + 500);
    return () => { window.clearTimeout(t1); window.clearTimeout(t2); };
  }, [delayMs]);
  return (
    <div className="flex items-center gap-3 px-3.5 py-2.5 rounded-lg transition-all"
      style={{
        background: done ? "rgba(16,185,129,0.06)" : active ? "rgba(245,158,11,0.06)" : "var(--secondary)",
        border: `1px solid ${done ? "rgba(16,185,129,0.15)" : active ? "rgba(245,158,11,0.15)" : "var(--border)"}`,
        opacity: active || done ? 1 : 0.4,
      }}>
      {done
        ? <CheckCircle size={13} style={{ color: "#10b981", flexShrink: 0 }} />
        : active
          ? <div className="w-3 h-3 rounded-full border-2 border-t-transparent animate-spin flex-shrink-0"
              style={{ borderColor: "#f59e0b", borderTopColor: "transparent" }} />
          : <div className="w-3 h-3 rounded-full flex-shrink-0" style={{ background: "var(--border)", opacity: 0.5 }} />
      }
      <span className="text-[12px]" style={{ color: done ? "#10b981" : active ? "#f1f5f9" : "var(--muted-foreground)" }}>{label}</span>
    </div>
  );
}

export function EnrollModal({ onClose, onEnroll }) {
  const [form, setForm] = useState(emptyForm);
  const [step, setStep] = useState("details");
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef(null);

  const set = (k, v) => setForm((p) => ({ ...p, [k]: v }));
  const detailsValid = form.firstName.trim() && form.lastName.trim() && form.birthdate;

  const handleFile = (file) => {
    if (!file.type.startsWith("image/")) return;
    setForm((p) => ({ ...p, imageFile: file, imagePreview: URL.createObjectURL(file) }));
  };

  const handleProcess = () => {
    setStep("processing");
    window.setTimeout(() => {
      setStep("done");
      const age = computeAge(form.birthdate);
      const fullName = [form.lastName + ",", form.firstName, form.middleName].filter(Boolean).join(" ");
      onEnroll({
        name: fullName,
        age,
        status: "pending",
        enrolledDate: new Date().toISOString().slice(0, 10),
        imageUrl: form.imagePreview ?? `https://ui-avatars.com/api/?name=${form.firstName}+${form.lastName}&size=80&background=random`,
        phone: form.phone || "",
      });
    }, 2200);
  };

  const steps = [
    { id: "details",    label: "Details" },
    { id: "photo",      label: "Photo" },
    { id: "processing", label: "Processing" },
    { id: "done",       label: "Done" },
  ];
  const stepOrder = ["details", "photo", "processing", "done"];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.78)", backdropFilter: "blur(6px)" }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="w-full max-w-md rounded-2xl overflow-hidden shadow-2xl flex flex-col"
        style={{ background: "var(--card)", border: "1px solid var(--border)", maxHeight: "92vh" }}>

        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 flex-shrink-0"
          style={{ borderBottom: "1px solid var(--border)" }}>
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center"
              style={{ background: "rgba(245,158,11,0.12)" }}>
              <UserPlus size={15} style={{ color: "#f59e0b" }} />
            </div>
            <div>
              <div className="text-sm font-semibold text-white">Enroll Resident</div>
              <div className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>DeepFace · ArcFace backend · RA 10173</div>
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
        <div className="flex items-center gap-0 px-5 py-3 flex-shrink-0"
          style={{ borderBottom: "1px solid var(--border)" }}>
          {steps.map((s, i) => {
            const current = stepOrder.indexOf(step);
            const thisIdx = stepOrder.indexOf(s.id);
            const done = thisIdx < current;
            const active = thisIdx === current;
            return (
              <div key={s.id} className="flex items-center">
                <div className="flex items-center gap-1.5">
                  <div className="w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold transition-all"
                    style={{
                      background: done ? "#10b981" : active ? "#f59e0b" : "var(--secondary)",
                      color: done ? "#fff" : active ? "#0c0f16" : "var(--muted-foreground)",
                    }}>
                    {done ? <CheckCircle size={11} /> : i + 1}
                  </div>
                  <span className="text-[11px] font-medium"
                    style={{ color: done ? "#10b981" : active ? "#f59e0b" : "var(--muted-foreground)" }}>
                    {s.label}
                  </span>
                </div>
                {i < steps.length - 1 && (
                  <div className="w-6 h-px mx-2"
                    style={{ background: done ? "#10b981" : "var(--border)" }} />
                )}
              </div>
            );
          })}
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-5">

          {step === "details" && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <label className="block text-xs font-medium" style={{ color: "var(--muted-foreground)" }}>
                    First name <span style={{ color: "#ef4444" }}>*</span>
                  </label>
                  <input value={form.firstName} onChange={(e) => set("firstName", e.target.value)}
                    placeholder="Juan"
                    className="w-full px-3 py-2.5 rounded-xl text-sm outline-none"
                    style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "#f1f5f9" }} />
                </div>
                <div className="space-y-1.5">
                  <label className="block text-xs font-medium" style={{ color: "var(--muted-foreground)" }}>
                    Last name <span style={{ color: "#ef4444" }}>*</span>
                  </label>
                  <input value={form.lastName} onChange={(e) => set("lastName", e.target.value)}
                    placeholder="dela Cruz"
                    className="w-full px-3 py-2.5 rounded-xl text-sm outline-none"
                    style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "#f1f5f9" }} />
                </div>
              </div>
              <div className="space-y-1.5">
                <label className="block text-xs font-medium" style={{ color: "var(--muted-foreground)" }}>
                  Middle name <span style={{ color: "var(--muted-foreground)", fontWeight: 400 }}>(optional)</span>
                </label>
                <input value={form.middleName} onChange={(e) => set("middleName", e.target.value)}
                  placeholder="Santos"
                  className="w-full px-3 py-2.5 rounded-xl text-sm outline-none"
                  style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "#f1f5f9" }} />
              </div>
              <div className="space-y-1.5">
                <label className="block text-xs font-medium" style={{ color: "var(--muted-foreground)" }}>
                  Date of birth <span style={{ color: "#ef4444" }}>*</span>
                </label>
                <input type="date" value={form.birthdate} onChange={(e) => set("birthdate", e.target.value)}
                  max={new Date().toISOString().slice(0, 10)}
                  className="w-full px-3 py-2.5 rounded-xl text-sm outline-none"
                  style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "#f1f5f9", colorScheme: "dark" }} />
                {form.birthdate && (
                  <p className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>
                    <span style={{ color: "#f1f5f9" }}>{computeAge(form.birthdate)} years old</span>
                    {computeAge(form.birthdate) < 18 && (
                      <span className="ml-2 px-1.5 py-0.5 rounded text-[10px] font-medium"
                        style={{ background: "rgba(245,158,11,0.1)", color: "#f59e0b" }}>
                        Minor — guardian required
                      </span>
                    )}
                  </p>
                )}
              </div>

              {form.birthdate && computeAge(form.birthdate) >= 18 && (
                <div className="rounded-xl p-3.5 space-y-1.5"
                  style={{ background: "rgba(245,158,11,0.06)", border: "1px solid rgba(245,158,11,0.2)" }}>
                  <label className="block text-xs font-medium" style={{ color: "#f59e0b" }}>
                    Mobile number <span className="font-normal" style={{ color: "var(--muted-foreground)" }}>(optional · adult resident)</span>
                  </label>
                  <input value={form.phone} onChange={(e) => set("phone", e.target.value)} placeholder="09XXXXXXXXX"
                    className="w-full px-3 py-2.5 rounded-xl text-sm outline-none"
                    style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "#f1f5f9" }} />
                </div>
              )}

              <div className="rounded-xl p-3.5 flex items-start gap-2.5"
                style={{ background: "rgba(245,158,11,0.06)", border: "1px solid rgba(245,158,11,0.15)" }}>
                <Cpu size={13} style={{ color: "#f59e0b", flexShrink: 0, marginTop: 1 }} />
                <p className="text-[11px] leading-relaxed" style={{ color: "#94a3b8" }}>
                  A face photo will be required in the next step. The image will be processed by{" "}
                  <span style={{ color: "#f59e0b" }}>ArcFace</span> to generate a 512-dimensional embedding stored locally.
                </p>
              </div>
            </div>
          )}

          {step === "photo" && (
            <div className="space-y-4">
              <div className="text-sm font-medium text-white">{form.lastName}, {form.firstName} {form.middleName}</div>
              <p className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>
                Upload a clear, front-facing photo. The face must be well-lit and unobstructed.
              </p>
              {!form.imagePreview ? (
                <div
                  onDrop={(e) => { e.preventDefault(); setDragOver(false); const f = e.dataTransfer.files[0]; if (f) handleFile(f); }}
                  onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                  onDragLeave={() => setDragOver(false)}
                  onClick={() => fileRef.current?.click()}
                  className="rounded-xl cursor-pointer transition-all flex flex-col items-center justify-center gap-3"
                  style={{
                    height: 180,
                    border: `2px dashed ${dragOver ? "rgba(245,158,11,0.5)" : "rgba(255,255,255,0.1)"}`,
                    background: dragOver ? "rgba(245,158,11,0.05)" : "var(--secondary)",
                  }}>
                  <div className="w-12 h-12 rounded-full flex items-center justify-center"
                    style={{ background: "rgba(245,158,11,0.1)" }}>
                    <Upload size={20} style={{ color: "#f59e0b" }} />
                  </div>
                  <div className="text-center">
                    <div className="text-sm font-medium text-white">Drop photo here</div>
                    <div className="text-[11px] mt-0.5" style={{ color: "var(--muted-foreground)" }}>or click to browse — JPG, PNG, WEBP</div>
                  </div>
                </div>
              ) : (
                <div className="relative rounded-xl overflow-hidden" style={{ height: 220 }}>
                  <img src={form.imagePreview} alt="Face preview" className="w-full h-full object-cover" />
                  <div className="absolute inset-0 flex items-center justify-center"
                    style={{ background: "rgba(0,0,0,0.15)" }}>
                    <div className="w-28 h-36 rounded relative"
                      style={{ border: "1px solid rgba(245,158,11,0.4)" }}>
                      {["top-0 left-0 border-t-2 border-l-2","top-0 right-0 border-t-2 border-r-2",
                        "bottom-0 left-0 border-b-2 border-l-2","bottom-0 right-0 border-b-2 border-r-2"]
                        .map((cls, i) => (
                          <div key={i} className={`absolute w-3 h-3 ${cls}`}
                            style={{ borderColor: "#f59e0b" }} />
                        ))}
                    </div>
                  </div>
                  <div className="absolute bottom-3 left-0 right-0 flex justify-center">
                    <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[11px] font-medium"
                      style={{ background: "rgba(16,185,129,0.9)", color: "#fff" }}>
                      <CheckCircle size={11} /> Face detected
                    </div>
                  </div>
                  <button onClick={() => fileRef.current?.click()}
                    className="absolute top-3 right-3 flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] font-medium"
                    style={{ background: "rgba(0,0,0,0.65)", color: "#f1f5f9", backdropFilter: "blur(4px)" }}>
                    <Camera size={11} /> Retake
                  </button>
                </div>
              )}
              <input ref={fileRef} type="file" accept="image/jpeg,image/png,image/webp" className="hidden"
                onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f); }} />
              <div className="rounded-xl p-3.5 space-y-1.5" style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
                <div className="text-[11px] font-semibold text-white mb-2">Photo requirements</div>
                {[
                  "Clear, front-facing view of the face",
                  "Neutral expression, eyes open",
                  "Adequate lighting — no harsh shadows",
                  "No sunglasses, heavy makeup, or face coverings",
                  "Minimum 200×200 px resolution",
                ].map((req) => (
                  <div key={req} className="flex items-center gap-2 text-[11px]" style={{ color: "#94a3b8" }}>
                    <div className="w-1 h-1 rounded-full flex-shrink-0" style={{ background: "#64748b" }} />
                    {req}
                  </div>
                ))}
              </div>
            </div>
          )}

          {step === "processing" && (
            <div className="flex flex-col items-center justify-center py-8 gap-5">
              {form.imagePreview && (
                <div className="relative">
                  <img src={form.imagePreview} alt="Processing"
                    className="w-24 h-24 rounded-xl object-cover"
                    style={{ border: "1px solid rgba(245,158,11,0.3)" }} />
                  <div className="absolute -inset-1.5 rounded-xl border-2 border-transparent border-t-amber-400 animate-spin"
                    style={{ animationDuration: "1s" }} />
                </div>
              )}
              <div className="text-center space-y-1.5">
                <div className="text-sm font-semibold text-white">Generating embeddings…</div>
                <div className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>DeepFace · ArcFace backend</div>
              </div>
              <div className="w-full space-y-2">
                {[
                  { label: "Detecting face landmarks", delay: 0 },
                  { label: "Aligning facial geometry", delay: 400 },
                  { label: "Generating ArcFace embedding", delay: 900 },
                  { label: "Storing embedding locally", delay: 1500 },
                ].map((item, i) => (
                  <ProcessingStep key={i} label={item.label} delayMs={item.delay} />
                ))}
              </div>
            </div>
          )}

          {step === "done" && (
            <div className="flex flex-col items-center justify-center py-6 gap-4 text-center">
              <div className="w-14 h-14 rounded-full flex items-center justify-center"
                style={{ background: "rgba(16,185,129,0.12)", border: "1px solid rgba(16,185,129,0.3)" }}>
                <CheckCircle size={26} style={{ color: "#10b981" }} />
              </div>
              <div>
                <div className="text-sm font-semibold text-white">Enrollment complete</div>
                <div className="text-[12px] mt-1" style={{ color: "var(--muted-foreground)" }}>
                  {form.lastName}, {form.firstName} has been enrolled and their face embedding stored locally.
                </div>
              </div>
              <div className="w-full rounded-xl p-3.5 space-y-1.5 text-left"
                style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
                {[
                  ["Name", `${form.lastName}, ${form.firstName} ${form.middleName}`.trim()],
                  ["Date of birth", form.birthdate],
                  ["Age", `${computeAge(form.birthdate)} years old`],
                  ...(form.phone ? [["Mobile number", form.phone]] : []),
                  ["Status", "Pending verification"],
                  ["Embedding", "ArcFace · 512-d · stored locally"],
                ].map(([k, v]) => (
                  <div key={k} className="flex justify-between text-[11px]">
                    <span style={{ color: "var(--muted-foreground)" }}>{k}</span>
                    <span style={{ color: "#f1f5f9" }}>{v}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-3 px-5 py-4 flex-shrink-0"
          style={{ borderTop: "1px solid var(--border)" }}>
          {step === "details" && (<>
            <button onClick={onClose} className="px-4 py-2 rounded-xl text-sm font-medium"
              style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
              Cancel
            </button>
            <button disabled={!detailsValid} onClick={() => detailsValid && setStep("photo")}
              className="flex items-center gap-2 px-5 py-2 rounded-xl text-sm font-medium transition-all"
              style={{
                background: detailsValid ? "#f59e0b" : "rgba(245,158,11,0.2)",
                color: detailsValid ? "#0c0f16" : "rgba(245,158,11,0.4)",
                cursor: detailsValid ? "pointer" : "not-allowed",
              }}>
              Next <ChevronRight size={14} />
            </button>
          </>)}
          {step === "photo" && (<>
            <button onClick={() => setStep("details")} className="px-4 py-2 rounded-xl text-sm font-medium"
              style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
              Back
            </button>
            <button disabled={!form.imageFile} onClick={() => form.imageFile && handleProcess()}
              className="flex items-center gap-2 px-5 py-2 rounded-xl text-sm font-medium transition-all"
              style={{
                background: form.imageFile ? "#f59e0b" : "rgba(245,158,11,0.2)",
                color: form.imageFile ? "#0c0f16" : "rgba(245,158,11,0.4)",
                cursor: form.imageFile ? "pointer" : "not-allowed",
              }}>
              <Cpu size={13} /> Generate embedding
            </button>
          </>)}
          {step === "done" && (
            <button onClick={onClose} className="w-full py-2.5 rounded-xl text-sm font-medium"
              style={{ background: "#10b981", color: "#fff" }}>
              Close
            </button>
          )}
        </div>
      </div>
    </div>
  );
}