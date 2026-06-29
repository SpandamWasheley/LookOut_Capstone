import { useState, useRef, useEffect } from "react";
import {
  X, Upload, Cpu, ChevronRight, ChevronDown, CheckCircle, UserPlus,
  Search, Home, User, RefreshCw, AlertTriangle, Loader2,
} from "lucide-react";
import { getHouseholds } from "./api";

function computeAge(birthdate) {
  if (!birthdate) return 0;
  const today = new Date();
  const dob = new Date(birthdate);
  let age = today.getFullYear() - dob.getFullYear();
  const m = today.getMonth() - dob.getMonth();
  if (m < 0 || (m === 0 && today.getDate() < dob.getDate())) age--;
  return age;
}

function mapHousehold(raw) {
  const idToCode = {};
  raw.members.forEach((m) => { idToCode[m.id] = m.code; });
  return {
    id: raw.code,
    familyName: raw.family_name,
    address: raw.address,
    members: raw.members.map((m) => ({
      id: m.code,
      firstName: m.first_name,
      lastName: m.last_name,
      birthdate: m.birthdate,
      barangayId: m.barangay_id,
      imageUrl: m.image_url,
      age: computeAge(m.birthdate),
    })),
  };
}

// ── Processing step ───────────────────────────────────────────────────────────
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
        opacity: !active && !done ? 0.4 : 1,
      }}>
      {done
        ? <CheckCircle size={13} style={{ color: "#10b981", flexShrink: 0 }} />
        : active
          ? <div className="w-3 h-3 rounded-full border-2 border-t-transparent animate-spin flex-shrink-0"
              style={{ borderColor: "var(--primary)", borderTopColor: "transparent" }} />
          : <div className="w-3 h-3 rounded-full flex-shrink-0" style={{ background: "var(--muted-foreground)", opacity: 0.3 }} />
      }
      <span className="text-[12px]"
        style={{ color: done ? "#10b981" : active ? "var(--primary)" : "var(--muted-foreground)" }}>
        {label}
      </span>
    </div>
  );
}

// ── Main modal ────────────────────────────────────────────────────────────────
export function EnrollModal({ onClose, onEnroll }) {
  const [step, setStep] = useState("select");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState(null);
  const [photos, setPhotos] = useState({
    front: { file: null, preview: null },
    right: { file: null, preview: null },
    left:  { file: null, preview: null },
  });
  const [dragOver, setDragOver] = useState(null);
  const [expandedHouseholds, setExpandedHouseholds] = useState(new Set());

  // API households
  const [households, setHouseholds] = useState([]);
  const [loadingHH, setLoadingHH] = useState(true);
  const [loadError, setLoadError] = useState("");

  const frontRef = useRef(null);
  const rightRef = useRef(null);
  const leftRef  = useRef(null);
  const fileRefs = { front: frontRef, right: rightRef, left: leftRef };

  const allPhotosReady = photos.front.file && photos.right.file && photos.left.file;

  useEffect(() => {
    getHouseholds()
      .then((data) => setHouseholds((data.results ?? data).map(mapHousehold)))
      .catch((err) => setLoadError(err.message))
      .finally(() => setLoadingHH(false));
  }, []);

  const toggleHousehold = (id) =>
    setExpandedHouseholds((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  // Flat list of all members for search
  const allMembers = households.flatMap((hh) =>
    hh.members.map((m) => ({ ...m, isMinor: m.age < 18, householdName: `${hh.familyName} household`, householdId: hh.id }))
  );

  const filtered = allMembers.filter((m) => {
    const q = search.toLowerCase();
    return !q ||
      `${m.firstName} ${m.lastName}`.toLowerCase().includes(q) ||
      m.householdName.toLowerCase().includes(q) ||
      m.barangayId.toLowerCase().includes(q);
  });

  // Group by household
  const grouped = {};
  for (const m of filtered) {
    if (!grouped[m.householdId]) {
      const hh = households.find((h) => h.id === m.householdId);
      grouped[m.householdId] = { householdName: m.householdName, address: hh?.address ?? "", members: [] };
    }
    grouped[m.householdId].members.push(m);
  }

  const handleFile = (angle, file) => {
    if (!file.type.startsWith("image/")) return;
    setPhotos((prev) => ({ ...prev, [angle]: { file, preview: URL.createObjectURL(file) } }));
  };

  const clearPhoto = (angle) =>
    setPhotos((prev) => ({ ...prev, [angle]: { file: null, preview: null } }));

  const handleProcess = () => {
    if (!selected || !allPhotosReady) return;
    setStep("processing");
    window.setTimeout(() => {
      setStep("done");
      onEnroll({
        name: `${selected.lastName}, ${selected.firstName}`,
        age: selected.age,
        status: "pending",
        enrolledDate: new Date().toISOString().slice(0, 10),
        imageUrl: photos.front.preview ?? selected.imageUrl ?? "",
      });
    }, 3000);
  };

  const steps = [
    { id: "select",     label: "Select member" },
    { id: "photo",      label: "Face photo" },
    { id: "processing", label: "Processing" },
    { id: "done",       label: "Done" },
  ];
  const stepOrder = ["select", "photo", "processing", "done"];

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
              style={{ background: "rgba(245,158,11,0.12)" }}>
              <UserPlus size={15} style={{ color: "#f59e0b" }} />
            </div>
            <div>
              <div className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>Enroll Resident</div>
              <div className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>Select from a registered household</div>
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
        <div className="flex items-center px-5 py-3 flex-shrink-0"
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

          {/* ── Step 1: Select member ── */}
          {step === "select" && (
            <div className="space-y-3">
              <p className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>
                Choose a household member to enroll for ArcFace biometric identification.
              </p>

              <div className="relative">
                <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2"
                  style={{ color: "var(--muted-foreground)" }} />
                <input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search by name, household, or BRG ID…"
                  className="w-full pl-9 pr-3 py-2.5 rounded-xl text-sm outline-none"
                  style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }}
                />
              </div>

              <div className="space-y-2 max-h-80 overflow-y-auto">
                {loadingHH ? (
                  <div className="flex items-center justify-center py-10 gap-2">
                    <Loader2 size={18} className="animate-spin" style={{ color: "var(--muted-foreground)" }} />
                    <span className="text-sm" style={{ color: "var(--muted-foreground)" }}>Loading households…</span>
                  </div>
                ) : loadError ? (
                  <div className="flex flex-col items-center justify-center py-10 gap-2">
                    <AlertTriangle size={20} style={{ color: "#ef4444" }} />
                    <span className="text-sm" style={{ color: "var(--muted-foreground)" }}>{loadError}</span>
                  </div>
                ) : Object.keys(grouped).length === 0 ? (
                  <div className="flex flex-col items-center justify-center py-10 gap-2">
                    <User size={24} style={{ color: "var(--muted-foreground)" }} />
                    <div className="text-sm font-medium" style={{ color: "var(--foreground)" }}>No members found</div>
                  </div>
                ) : (
                  Object.entries(grouped).map(([hhId, { householdName, address, members }]) => {
                    const isOpen = expandedHouseholds.has(hhId);
                    const minorCount = members.filter((m) => m.isMinor).length;
                    return (
                      <div key={hhId} className="rounded-xl overflow-hidden"
                        style={{ border: "1px solid var(--border)", background: "var(--secondary)" }}>
                        <button
                          onClick={() => toggleHousehold(hhId)}
                          className="w-full flex items-center gap-2.5 px-3.5 py-3 text-left transition-all"
                        >
                          <div style={{ color: "var(--muted-foreground)", flexShrink: 0 }}>
                            {isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                          </div>
                          <Home size={13} style={{ color: "var(--muted-foreground)", flexShrink: 0 }} />
                          <div className="flex-1 min-w-0 flex items-center gap-2 flex-wrap">
                            <span className="text-[13px] font-semibold" style={{ color: "var(--foreground)" }}>
                              {householdName}
                            </span>
                            {address && (
                              <span className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>
                                · {address}, Brgy. Tetuan
                              </span>
                            )}
                          </div>
                          <div className="flex items-center gap-1.5 flex-shrink-0">
                            {minorCount > 0 && (
                              <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full"
                                style={{ background: "rgba(245,158,11,0.15)", color: "#f59e0b" }}>
                                {minorCount} {minorCount === 1 ? "minor" : "minors"}
                              </span>
                            )}
                            <span className="text-[10px] px-2 py-0.5 rounded-full"
                              style={{ background: "var(--muted)", color: "var(--muted-foreground)" }}>
                              {members.length} member{members.length !== 1 ? "s" : ""}
                            </span>
                          </div>
                        </button>

                        {isOpen && (
                          <div className="px-3 pb-3" style={{ borderTop: "1px solid var(--border)" }}>
                            <div className="pt-2 space-y-1.5">
                              {members.map((m) => {
                                const isSelected = selected?.id === m.id;
                                return (
                                  <button
                                    key={m.id}
                                    onClick={() => setSelected(isSelected ? null : m)}
                                    className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-left transition-all"
                                    style={{
                                      background: isSelected ? "rgba(11,84,113,0.08)" : "var(--card)",
                                      border: `1px solid ${isSelected ? "rgba(11,84,113,0.3)" : "var(--border)"}`,
                                    }}
                                  >
                                    <div className="flex-shrink-0">
                                      {m.imageUrl
                                        ? <img src={m.imageUrl} alt={m.firstName} className="w-8 h-8 rounded-lg object-cover"
                                            style={{ border: `1.5px solid ${isSelected ? "rgba(11,84,113,0.4)" : "var(--border)"}` }} />
                                        : <div className="w-8 h-8 rounded-lg flex items-center justify-center text-xs font-bold"
                                            style={{ background: m.isMinor ? "rgba(245,158,11,0.12)" : "var(--secondary)", color: m.isMinor ? "#f59e0b" : "var(--muted-foreground)" }}>
                                            {m.firstName[0]}{m.lastName[0]}
                                          </div>
                                      }
                                    </div>
                                    <div className="flex-1 min-w-0">
                                      <div className="flex items-center gap-2">
                                        <span className="text-[12px] font-medium" style={{ color: "var(--foreground)" }}>
                                          {m.firstName} {m.lastName}
                                        </span>
                                        {m.isMinor && (
                                          <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full"
                                            style={{ background: "rgba(245,158,11,0.12)", color: "#f59e0b" }}>
                                            Minor
                                          </span>
                                        )}
                                      </div>
                                      <div className="text-[10px] mt-0.5"
                                        style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
                                        {m.barangayId} · Age {m.age}
                                      </div>
                                    </div>
                                    {isSelected && <CheckCircle size={14} style={{ color: "var(--primary)", flexShrink: 0 }} />}
                                  </button>
                                );
                              })}
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })
                )}
              </div>

              {selected && (
                <div className="rounded-xl p-3 flex items-center gap-3"
                  style={{ background: "rgba(245,158,11,0.06)", border: "1px solid rgba(245,158,11,0.2)" }}>
                  {selected.imageUrl
                    ? <img src={selected.imageUrl} alt={selected.firstName} className="w-8 h-8 rounded-lg object-cover flex-shrink-0" />
                    : <div className="w-8 h-8 rounded-lg flex items-center justify-center text-xs font-bold flex-shrink-0"
                        style={{ background: "rgba(245,158,11,0.2)", color: "#f59e0b" }}>
                        {selected.firstName[0]}
                      </div>
                  }
                  <div className="flex-1 min-w-0">
                    <div className="text-[12px] font-medium" style={{ color: "var(--foreground)" }}>
                      {selected.firstName} {selected.lastName}
                    </div>
                    <div className="text-[10px]" style={{ color: "#f59e0b" }}>{selected.householdName}</div>
                  </div>
                  <CheckCircle size={14} style={{ color: "#f59e0b", flexShrink: 0 }} />
                </div>
              )}
            </div>
          )}

          {/* ── Step 2: Face photos ── */}
          {step === "photo" && selected && (
            <div className="space-y-4">
              <div>
                <div className="text-sm font-medium mb-0.5" style={{ color: "var(--foreground)" }}>
                  {selected.firstName} {selected.lastName}
                </div>
                <p className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>
                  Upload all three angles for accurate ArcFace embedding.
                </p>
              </div>

              {/* Progress strip */}
              <div className="flex items-center gap-2">
                {["front", "right", "left"].map((angle) => (
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
                {[
                  { angle: "front", label: "Front",      icon: "⬤", hint: "Face the camera directly" },
                  { angle: "right", label: "Right side", icon: "◐", hint: "Turn your head to the right" },
                  { angle: "left",  label: "Left side",  icon: "◑", hint: "Turn your head to the left" },
                ].map(({ angle, label, icon, hint }) => {
                  const slot = photos[angle];
                  const isOver = dragOver === angle;
                  return (
                    <div key={angle} className="flex flex-col gap-1.5">
                      <div className="flex items-center gap-1">
                        <span className="text-[11px] font-semibold"
                          style={{ color: slot.file ? "#10b981" : "var(--muted-foreground)" }}>
                          {label}
                        </span>
                        {slot.file && <CheckCircle size={10} style={{ color: "#10b981" }} />}
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
                            border: `2px dashed ${isOver ? "var(--primary)" : "var(--border)"}`,
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
                        <div className="relative rounded-xl overflow-hidden" style={{ aspectRatio: "3/4" }}>
                          <img src={slot.preview} alt={`${label} angle`} className="w-full h-full object-cover" />
                          <div className="absolute inset-0 flex items-center justify-center">
                            <div className="w-10 h-14 rounded relative" style={{ border: "1.5px solid #10b981" }}>
                              {["top-0 left-0 border-t-2 border-l-2","top-0 right-0 border-t-2 border-r-2","bottom-0 left-0 border-b-2 border-l-2","bottom-0 right-0 border-b-2 border-r-2"].map((cls, i) => (
                                <div key={i} className={`absolute w-2 h-2 ${cls}`} style={{ borderColor: "#10b981" }} />
                              ))}
                            </div>
                          </div>
                          <button
                            onClick={() => clearPhoto(angle)}
                            className="absolute top-1.5 right-1.5 p-1 rounded-md transition-all"
                            style={{ background: "rgba(0,0,0,0.65)", color: "#fff", backdropFilter: "blur(4px)" }}
                          >
                            <RefreshCw size={10} />
                          </button>
                          <div className="absolute bottom-1.5 left-0 right-0 flex justify-center">
                            <div className="flex items-center gap-1 px-2 py-0.5 rounded-full text-[9px] font-medium"
                              style={{ background: "rgba(16,185,129,0.9)", color: "#fff" }}>
                              <CheckCircle size={9} /> OK
                            </div>
                          </div>
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
              {(photos.front.preview ?? selected?.imageUrl) && (
                <div className="relative">
                  <img src={photos.front.preview ?? selected?.imageUrl} alt="Processing"
                    className="w-24 h-24 rounded-xl object-cover"
                    style={{ border: "2px solid rgba(245,158,11,0.3)" }} />
                  <div className="absolute -inset-1.5 rounded-xl border-2 border-transparent border-t-amber-400 animate-spin"
                    style={{ animationDuration: "1.2s" }} />
                </div>
              )}
              <div className="text-center space-y-1.5">
                <div className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>Generating embeddings…</div>
                <div className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>DeepFace · ArcFace backend</div>
              </div>
              <div className="w-full space-y-2">
                {[
                  { label: "Detecting landmarks on 3 angles",    delay: 0    },
                  { label: "Normalizing face regions",           delay: 500  },
                  { label: "Generating 512-d ArcFace embedding", delay: 1000 },
                  { label: "Fusing multi-angle embeddings",      delay: 1500 },
                  { label: "Saving to local database",           delay: 2000 },
                ].map((item, i) => (
                  <ProcessingStep key={i} label={item.label} delayMs={item.delay} />
                ))}
              </div>
            </div>
          )}

          {/* ── Step 4: Done ── */}
          {step === "done" && selected && (
            <div className="flex flex-col items-center justify-center py-6 gap-4 text-center">
              <div className="w-14 h-14 rounded-full flex items-center justify-center"
                style={{ background: "rgba(16,185,129,0.12)", border: "2px solid rgba(16,185,129,0.3)" }}>
                <CheckCircle size={26} style={{ color: "#10b981" }} />
              </div>
              <div>
                <div className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>Enrollment complete</div>
                <div className="text-[12px] mt-1" style={{ color: "var(--muted-foreground)" }}>
                  {selected.firstName} {selected.lastName} has been enrolled.
                </div>
              </div>
              <div className="w-full rounded-xl p-3.5 space-y-1.5 text-left"
                style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
                {[
                  ["Name",      `${selected.lastName}, ${selected.firstName}`],
                  ["Household", selected.householdName],
                  ["BRG ID",    selected.barangayId],
                  ["Age",       `${selected.age} years old`],
                  ["Status",    "Pending verification"],
                  ["Photos",    "Front · Right · Left (3 angles)"],
                  ["Embedding", "ArcFace · 512-d fused · stored locally"],
                ].map(([k, v]) => (
                  <div key={k} className="flex justify-between text-[11px]">
                    <span style={{ color: "var(--muted-foreground)" }}>{k}</span>
                    <span style={{ color: "var(--muted-foreground)", fontFamily: k === "BRG ID" || k === "Embedding" ? "'DM Mono', monospace" : undefined }}>{v}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-3 px-5 py-4 flex-shrink-0"
          style={{ borderTop: "1px solid var(--border)" }}>
          {step === "select" && (
            <>
              <button onClick={onClose} className="px-4 py-2 rounded-xl text-sm font-medium"
                style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
                Cancel
              </button>
              <button
                disabled={!selected}
                onClick={() => selected && setStep("photo")}
                className="flex items-center gap-2 px-5 py-2 rounded-xl text-sm font-medium transition-all"
                style={{
                  background: selected ? "var(--primary)" : "rgba(245,158,11,0.2)",
                  color: selected ? "var(--primary-foreground)" : "rgba(245,158,11,0.4)",
                  cursor: selected ? "pointer" : "not-allowed",
                }}>
                Next <ChevronRight size={14} />
              </button>
            </>
          )}
          {step === "photo" && (
            <>
              <button onClick={() => setStep("select")} className="px-4 py-2 rounded-xl text-sm font-medium"
                style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
                Back
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
          {step === "done" && (
            <button onClick={onClose} className="w-full py-2.5 rounded-xl text-sm font-medium"
              style={{ background: "#10b981", color: "#fff" }}>
              Done
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
