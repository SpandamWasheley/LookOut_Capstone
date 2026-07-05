import { useState, useEffect } from "react";
import { X, Home, Users, Shield, FileText, Plus, CheckCircle, ChevronRight, Info, ChevronDown } from "lucide-react";
import { ZONES } from "../data/mockData";
import { getResidents } from "./api";

const formatPhone = (raw) => {
  const d = raw.replace(/\D/g, "").slice(0, 11);
  if (d.length <= 4) return d;
  if (d.length <= 7) return `${d.slice(0, 4)}-${d.slice(4)}`;
  return `${d.slice(0, 4)}-${d.slice(4, 7)}-${d.slice(7)}`;
};
const blockNumbers = (v) => v.replace(/[0-9]/g, "");
const maxToday = () => new Date().toISOString().slice(0, 10);

function computeAge(birthdate) {
  if (!birthdate) return 0;
  const today = new Date();
  const dob = new Date(birthdate);
  let age = today.getFullYear() - dob.getFullYear();
  const m = today.getMonth() - dob.getMonth();
  if (m < 0 || (m === 0 && today.getDate() < dob.getDate())) age--;
  return age;
}

function isMinor(birthdate) { return computeAge(birthdate) < 18; }

function initials(first, last) {
  return `${first[0] ?? ""}${last[0] ?? ""}`.toUpperCase();
}

// ── Step indicator ─────────────────────────────────────────────────────────────
function StepBar({ step }) {
  const steps = [
    { n: 1, label: "Household" },
    { n: 2, label: "Members" },
    { n: 3, label: "Guardians" },
    { n: 4, label: "Review" },
  ];
  return (
    <div className="flex items-center justify-center gap-0 px-6 py-4 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
      {steps.map((s, i) => {
        const done   = step > s.n;
        const active = step === s.n;
        return (
          <div key={s.n} className="flex items-center">
            <div className="flex items-center gap-2">
              <div
                className="w-6 h-6 rounded-full flex items-center justify-center text-[11px] font-bold transition-all flex-shrink-0"
                style={{
                  background: done ? "#10b981" : active ? "var(--primary)" : "var(--secondary)",
                  color: done || active ? (done ? "#fff" : "var(--primary-foreground)") : "var(--muted-foreground)",
                  border: done || active ? "none" : "1px solid var(--border)",
                }}
              >
                {done ? <CheckCircle size={13} /> : s.n}
              </div>
              <span
                className="text-[12px] font-medium"
                style={{ color: active ? "var(--primary)" : done ? "#10b981" : "var(--muted-foreground)" }}
              >
                {s.label}
              </span>
            </div>
            {i < steps.length - 1 && (
              <div className="mx-3 flex-1 h-px" style={{ width: 32, background: done ? "#10b981" : "var(--border)" }} />
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Step 1: Household details ─────────────────────────────────────────────────
function StepHousehold({ form, onChange }) {
  const set = (k, v) => onChange({ ...form, [k]: v });
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 mb-2">
        <Home size={15} style={{ color: "#f59e0b" }} />
        <span className="text-sm font-semibold text-white">Household details</span>
        <span className="text-xs ml-1" style={{ color: "var(--muted-foreground)" }}>Step 1 of 4</span>
      </div>

      <div>
        <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>
          Household / family name <span style={{ color: "#ef4444" }}>*</span>
        </label>
        <input
          value={form.familyName}
          onChange={(e) => set("familyName", blockNumbers(e.target.value))}
          placeholder="e.g. Angeles"
          className="w-full px-3 py-2.5 rounded-xl text-sm outline-none"
          style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }}
        />
      </div>

      <div>
        <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>
          Address <span style={{ color: "#ef4444" }}>*</span>
        </label>
        <input
          value={form.address}
          onChange={(e) => set("address", e.target.value)}
          placeholder="e.g. 142 Don Maria Drive"
          className="w-full px-3 py-2.5 rounded-xl text-sm outline-none"
          style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }}
        />
      </div>

      <div>
        <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>
          Contact number <span style={{ color: "#ef4444" }}>*</span>
        </label>
        <input
          value={form.contact}
          onChange={(e) => set("contact", formatPhone(e.target.value))}
          placeholder="0951-853-2146"
          className="w-full px-3 py-2.5 rounded-xl text-sm outline-none"
          style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }}
        />
      </div>

      <div
        className="flex items-start gap-2.5 rounded-xl p-3.5"
        style={{ background: "rgba(245,158,11,0.06)", border: "1px solid rgba(245,158,11,0.15)" }}
      >
        <Info size={13} style={{ color: "#f59e0b", flexShrink: 0, marginTop: 1 }} />
        <p className="text-[11px] leading-relaxed" style={{ color: "var(--muted-foreground)" }}>
          A household ID will be auto-generated (e.g. HH-TET-004). All members enrolled under this household will be linked together for guardian relationship detection.
        </p>
      </div>
    </div>
  );
}

// ── Step 2: Members ───────────────────────────────────────────────────────────
const emptyMember = () => ({
  tempId: `tmp-${Date.now()}-${Math.random()}`,
  firstName: "", lastName: "", birthdate: "", phone: "",
});

function StepMembers({ familyName, members, onChange }) {
  const [adding, setAdding] = useState(emptyMember());
  const [showForm, setShowForm] = useState(members.length === 0);

  const setField = (k, v) => setAdding((p) => ({ ...p, [k]: v }));

  const addMember = () => {
    if (!adding.firstName.trim() || !adding.lastName.trim() || !adding.birthdate) return;
    onChange([...members, { ...adding }]);
    setAdding(emptyMember());
    setShowForm(false);
  };

  const remove = (id) => onChange(members.filter((m) => m.tempId !== id));

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 mb-2">
        <Users size={15} style={{ color: "#f59e0b" }} />
        <span className="text-sm font-semibold text-white">Add family members</span>
        <span className="text-xs ml-1" style={{ color: "var(--muted-foreground)" }}>Step 2 of 4 · {familyName} household</span>
      </div>

      {/* Member list */}
      <div className="space-y-2">
        {members.map((m) => {
          const age = computeAge(m.birthdate);
          const minor = age < 18;
          const init = initials(m.firstName, m.lastName);
          return (
            <div
              key={m.tempId}
              className="flex items-center gap-3 px-3.5 py-3 rounded-xl"
              style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}
            >
              <div
                className="w-8 h-8 rounded-lg flex items-center justify-center text-xs font-bold flex-shrink-0"
                style={{ background: minor ? "rgba(245,158,11,0.15)" : "rgba(100,116,139,0.15)", color: minor ? "#f59e0b" : "var(--muted-foreground)" }}
              >
                {init}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-[13px] font-medium text-white">{m.firstName} {m.lastName}</span>
                  {minor && (
                    <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full" style={{ background: "rgba(245,158,11,0.15)", color: "#f59e0b" }}>
                      Minor
                    </span>
                  )}
                </div>
                <div className="text-[11px] mt-0.5" style={{ color: "var(--muted-foreground)" }}>
                  Age {age} · {minor ? "Minor" : "Adult"} · Face photo pending
                  {m.phone && <> · {m.phone} <span style={{ color: "#f59e0b" }}>(secondary contact)</span></>}
                </div>
              </div>
              <button
                onClick={() => remove(m.tempId)}
                className="px-3 py-1.5 rounded-lg text-xs font-medium flex-shrink-0 transition-all"
                style={{ background: "rgba(239,68,68,0.08)", color: "#ef4444", border: "1px solid rgba(239,68,68,0.15)" }}
              >
                Remove
              </button>
            </div>
          );
        })}
      </div>

      {/* Add member form */}
      {showForm ? (
        <div className="rounded-xl p-4 space-y-3" style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>First name *</label>
              <input value={adding.firstName} onChange={(e) => setField("firstName", blockNumbers(e.target.value))} placeholder="Peter"
                className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }} />
            </div>
            <div>
              <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>Last name *</label>
              <input value={adding.lastName} onChange={(e) => setField("lastName", blockNumbers(e.target.value))} placeholder="Angeles"
                className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }} />
            </div>
          </div>
          <div>
              <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>
                Date of birth *
                {adding.birthdate && (
                  <span className="ml-2 font-normal" style={{ color: isMinor(adding.birthdate) ? "#f59e0b" : "var(--muted-foreground)" }}>
                    — Age {computeAge(adding.birthdate)} {isMinor(adding.birthdate) ? "(minor)" : ""}
                  </span>
                )}
              </label>
              <input type="date" value={adding.birthdate} onChange={(e) => { const v = e.target.value; if (v <= maxToday()) setField("birthdate", v); }}
                max={maxToday()}
                className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }} />
            </div>

          {adding.birthdate && !isMinor(adding.birthdate) && (
            <div className="rounded-lg p-3 space-y-1.5" style={{ background: "rgba(245,158,11,0.06)", border: "1px solid rgba(245,158,11,0.2)" }}>
              <label className="block text-xs font-medium" style={{ color: "#f59e0b" }}>
                Mobile number <span className="font-normal" style={{ color: "var(--muted-foreground)" }}>(optional · adult member)</span>
              </label>
              <input value={adding.phone} onChange={(e) => setField("phone", formatPhone(e.target.value))} placeholder="0951-853-2146"
                className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }} />
              <p className="text-[10px]" style={{ color: "var(--muted-foreground)" }}>
                Saved as a secondary contact number for this household.
              </p>
            </div>
          )}

          <div className="flex gap-2 pt-1">
            <button onClick={() => setShowForm(false)} className="px-3 py-1.5 rounded-lg text-xs font-medium"
              style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
              Cancel
            </button>
            <button
              onClick={addMember}
              disabled={!adding.firstName.trim() || !adding.lastName.trim() || !adding.birthdate}
              className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-xs font-medium transition-all"
              style={{
                background: adding.firstName && adding.lastName && adding.birthdate ? "var(--primary)" : "rgba(245,158,11,0.2)",
                color: adding.firstName && adding.lastName && adding.birthdate ? "var(--primary-foreground)" : "rgba(245,158,11,0.4)",
              }}>
              <Plus size={11} /> Add member
            </button>
          </div>
        </div>
      ) : (
        <button
          onClick={() => { setAdding(emptyMember()); setShowForm(true); }}
          className="w-full flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium transition-all"
          style={{ background: "transparent", color: "var(--muted-foreground)", border: "1px dashed var(--border)" }}
        >
          <Plus size={13} /> Add another member
        </button>
      )}

      <div className="flex items-start gap-2.5 rounded-xl p-3" style={{ background: "rgba(59,130,246,0.06)", border: "1px solid rgba(59,130,246,0.12)" }}>
        <Info size={12} style={{ color: "#3b82f6", flexShrink: 0, marginTop: 1 }} />
        <p className="text-[11px] leading-relaxed" style={{ color: "var(--muted-foreground)" }}>
          Face photos will be captured for each member in a separate step after saving. Members marked as minor are auto-detected from date of birth.
        </p>
      </div>
    </div>
  );
}

// ── Custom scrollable dropdown ────────────────────────────────────────────────
function CustomSelect({ options, placeholder, onSelect, multi = false }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="w-full">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-3 py-2 rounded-lg text-[12px]"
        style={{
          background: "var(--secondary)",
          border: "1px solid var(--border)",
          color: "var(--muted-foreground)",
          borderRadius: open ? "8px 8px 0 0" : "8px",
        }}
      >
        <span>{placeholder}</span>
        <ChevronDown
          size={12}
          style={{ color: "var(--muted-foreground)", flexShrink: 0, transform: open ? "rotate(180deg)" : "none", transition: "transform 0.15s" }}
        />
      </button>
      {open && (
        <div
          style={{
            maxHeight: "150px",
            overflowY: "scroll",
            border: "1px solid var(--border)",
            borderTop: "none",
            borderRadius: "0 0 8px 8px",
            background: "var(--card)",
          }}
        >
          {options.length === 0 ? (
            <div className="px-3 py-2 text-[11px]" style={{ color: "var(--muted-foreground)" }}>No options available</div>
          ) : (
            options.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => { onSelect(opt.value, opt.label); if (!multi || options.length <= 1) setOpen(false); }}
                className="w-full text-left px-3 py-2 text-[12px] transition-colors"
                style={{ color: "var(--foreground)", borderBottom: "1px solid var(--border)" }}
                onMouseEnter={(e) => (e.currentTarget.style.background = "var(--secondary)")}
                onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
              >
                {opt.label}
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}

// ── Step 3: Guardian relationships ────────────────────────────────────────────
function StepGuardians({ members, links, onChange, extLinks, onExtChange }) {
  const minors = members.filter((m) => isMinor(m.birthdate));
  const adults = members.filter((m) => !isMinor(m.birthdate));
  const [allResidents, setAllResidents] = useState([]);
  const [expandedGuardians, setExpandedGuardians] = useState(new Set());

  useEffect(() => {
    getResidents()
      .then((data) => {
        const list = data.results ?? data;
        setAllResidents(list.filter((r) => (r.age ?? 0) >= 18));
      })
      .catch(() => {});
  }, []);

  const toggle = (minorId, guardianId) => {
    const current = links[minorId] ?? [];
    const updated = current.includes(guardianId)
      ? current.filter((id) => id !== guardianId)
      : [...current, guardianId];
    onChange({ ...links, [minorId]: updated });
  };

  const addExternal = (minorTempId, residentId, residentName) => {
    if (!residentId) return;
    const current = extLinks[minorTempId] ?? [];
    if (current.some((g) => g.id === Number(residentId))) return;
    onExtChange({ ...extLinks, [minorTempId]: [...current, { id: Number(residentId), name: residentName }] });
  };

  const removeExternal = (minorTempId, residentId) => {
    const current = extLinks[minorTempId] ?? [];
    onExtChange({ ...extLinks, [minorTempId]: current.filter((g) => g.id !== residentId) });
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 mb-2">
        <Shield size={15} style={{ color: "#f59e0b" }} />
        <span className="text-sm font-semibold text-white">Guardian relationships</span>
        <span className="text-xs ml-1" style={{ color: "var(--muted-foreground)" }}>Step 3 of 4 · Link minors to their guardians</span>
      </div>

      <div className="flex items-start gap-2.5 rounded-xl p-3.5" style={{ background: "rgba(59,130,246,0.06)", border: "1px solid rgba(59,130,246,0.12)" }}>
        <Info size={12} style={{ color: "#3b82f6", flexShrink: 0, marginTop: 1 }} />
        <p className="text-[11px] leading-relaxed" style={{ color: "var(--muted-foreground)" }}>
          When the system detects a minor with their registered guardian past curfew hours, the violation is automatically suppressed. Select all adults who are permitted to accompany each minor.
        </p>
      </div>

      {minors.length === 0 && (
        <div className="text-center py-8" style={{ color: "var(--muted-foreground)" }}>
          <Shield size={24} className="mx-auto mb-2 opacity-40" />
          <div className="text-sm">No minors in this household</div>
          <div className="text-[11px] mt-0.5">Guardian linking is only required for members under 18.</div>
        </div>
      )}

      {minors.map((minor) => {
        const age = computeAge(minor.birthdate);
        const selected = links[minor.tempId] ?? [];
        const extSelected = extLinks[minor.tempId] ?? [];
        return (
          <div key={minor.tempId} className="rounded-xl overflow-hidden" style={{ border: "1px solid var(--border)" }}>
            <div className="px-4 py-2.5" style={{ background: "rgba(245,158,11,0.06)", borderBottom: "1px solid var(--border)" }}>
              <span className="text-[12px] font-medium text-white">{minor.firstName} {minor.lastName}</span>
              <span className="text-[11px] ml-2" style={{ color: "var(--muted-foreground)" }}>(minor, age {age})</span>
            </div>
            <div className="p-4 flex items-start gap-4">
              <div className="flex items-center gap-2 flex-shrink-0 pt-1">
                <div className="w-8 h-8 rounded-lg flex items-center justify-center text-xs font-bold" style={{ background: "rgba(245,158,11,0.15)", color: "#f59e0b" }}>
                  {initials(minor.firstName, minor.lastName)}
                </div>
                <span className="text-[12px] font-medium text-white">{minor.firstName}</span>
              </div>
              <span className="text-[11px] pt-2.5 flex-shrink-0" style={{ color: "var(--muted-foreground)" }}>is accompanied by</span>
              <div className="flex-1 space-y-2">
                {/* In-household adults */}
                <div>
                  <div className="text-[10px] font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>
                    Guardian from this household
                  </div>
                  {adults.length === 0 ? (
                    <div className="px-3 py-2 rounded-lg text-[11px]" style={{ color: "#4a5568", border: "1px solid var(--border)" }}>
                      No adult members to link
                    </div>
                  ) : (
                    <>
                      <CustomSelect
                        placeholder="Choose a household member…"
                        multi
                        options={adults
                          .filter((a) => !selected.includes(a.tempId))
                          .map((a) => ({ value: a.tempId, label: `${a.firstName} ${a.lastName}` }))}
                        onSelect={(val) => toggle(minor.tempId, val)}
                      />
                      {selected.length > 0 && (() => {
                        const isExpanded = expandedGuardians.has(minor.tempId);
                        const visible = selected.length > 1 && !isExpanded ? selected.slice(0, 1) : selected;
                        const hiddenCount = selected.length - 1;
                        return (
                          <div className="flex flex-wrap gap-1.5 mt-2">
                            {visible.map((tid) => {
                              const adult = adults.find((a) => a.tempId === tid);
                              if (!adult) return null;
                              return (
                                <span
                                  key={tid}
                                  className="flex items-center gap-1 px-2 py-1 rounded-full text-[11px] font-medium"
                                  style={{ background: "rgba(245,158,11,0.12)", color: "#f59e0b", border: "1px solid rgba(245,158,11,0.2)" }}
                                >
                                  {adult.firstName} {adult.lastName}
                                  <button type="button" onClick={() => toggle(minor.tempId, tid)} className="ml-0.5 hover:opacity-70">×</button>
                                </span>
                              );
                            })}
                            {selected.length > 1 && !isExpanded && (
                              <button
                                type="button"
                                onClick={() => setExpandedGuardians((prev) => { const next = new Set(prev); next.add(minor.tempId); return next; })}
                                className="flex items-center px-2 py-1 rounded-full text-[11px] font-medium"
                                style={{ background: "rgba(245,158,11,0.08)", color: "#f59e0b", border: "1px dashed rgba(245,158,11,0.3)" }}
                              >
                                +{hiddenCount} more
                              </button>
                            )}
                            {selected.length > 1 && isExpanded && (
                              <button
                                type="button"
                                onClick={() => setExpandedGuardians((prev) => { const next = new Set(prev); next.delete(minor.tempId); return next; })}
                                className="flex items-center px-2 py-1 rounded-full text-[11px] font-medium"
                                style={{ background: "rgba(245,158,11,0.08)", color: "#f59e0b", border: "1px dashed rgba(245,158,11,0.3)" }}
                              >
                                show less
                              </button>
                            )}
                          </div>
                        );
                      })()}
                    </>
                  )}
                </div>

                {/* External guardian dropdown — temporarily disabled
                <div>
                  <div className="text-[10px] font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>
                    Add guardian from outside this household
                  </div>
                  <CustomSelect
                    placeholder="Choose a registered guardian…"
                    options={allResidents
                      .filter((r) => !extSelected.some((g) => g.id === r.id))
                      .map((r) => ({ value: String(r.id), label: r.name }))}
                    onSelect={(val, label) => addExternal(minor.tempId, val, label)}
                  />
                  {extSelected.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 mt-2">
                      {extSelected.map((g) => (
                        <span
                          key={g.id}
                          className="flex items-center gap-1 px-2 py-1 rounded-full text-[11px] font-medium"
                          style={{ background: "rgba(59,130,246,0.12)", color: "#3b82f6", border: "1px solid rgba(59,130,246,0.2)" }}
                        >
                          {g.name}
                          <button
                            type="button"
                            onClick={() => removeExternal(minor.tempId, g.id)}
                            className="ml-0.5 hover:opacity-70"
                          >×</button>
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                */}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Step 4: Review ────────────────────────────────────────────────────────────
function StepReview({ form, members, links, nextId }) {
  const minors = members.filter((m) => isMinor(m.birthdate));
  const secondaryContacts = members.filter((m) => !isMinor(m.birthdate) && m.phone);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 mb-2">
        <FileText size={15} style={{ color: "#f59e0b" }} />
        <span className="text-sm font-semibold text-white">Review and save</span>
        <span className="text-xs ml-1" style={{ color: "var(--muted-foreground)" }}>Step 4 of 4 · Confirm before enrolling</span>
      </div>

      {/* Household */}
      <div className="rounded-xl overflow-hidden" style={{ border: "1px solid var(--border)" }}>
        <div className="px-4 py-2.5" style={{ background: "var(--secondary)", borderBottom: "1px solid var(--border)" }}>
          <span className="text-[10px] font-bold tracking-widest" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>HOUSEHOLD</span>
        </div>
        <div className="divide-y" style={{ borderColor: "var(--border)" }}>
          {[
            ["Family name", form.familyName],
            ["Address", form.address],
            ["Contact (primary)", form.contact],
            ["ID", nextId],
          ].map(([k, v]) => (
            <div key={k} className="flex items-center justify-between px-4 py-2.5">
              <span className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>{k}</span>
              <span className="text-[12px] font-medium text-white" style={{ fontFamily: k === "ID" ? "'DM Mono', monospace" : undefined }}>{v}</span>
            </div>
          ))}
          {secondaryContacts.length > 0 && (
            <div className="flex items-center justify-between px-4 py-2.5">
              <span className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>Contact (secondary)</span>
              <span className="text-[12px] font-medium" style={{ color: "#f59e0b" }}>
                {secondaryContacts.map((m) => m.phone).join(", ")}
              </span>
            </div>
          )}
        </div>
      </div>

      {/* Members */}
      <div className="rounded-xl overflow-hidden" style={{ border: "1px solid var(--border)" }}>
        <div className="px-4 py-2.5" style={{ background: "var(--secondary)", borderBottom: "1px solid var(--border)" }}>
          <span className="text-[10px] font-bold tracking-widest" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>MEMBERS ({members.length})</span>
        </div>
        <div className="divide-y" style={{ borderColor: "var(--border)" }}>
          {members.map((m) => {
            const age = computeAge(m.birthdate);
            const minor = age < 18;
            return (
              <div key={m.tempId} className="flex items-center justify-between px-4 py-2.5">
                <span className="text-[12px] text-white">{m.firstName} {m.lastName}</span>
                <span className="text-[12px] font-medium" style={{ color: minor ? "#f59e0b" : "var(--muted-foreground)" }}>
                  {minor ? `Minor · Age ${age}` : `Adult · Age ${age}`}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* Guardian relationships */}
      {minors.length > 0 && (
        <div className="rounded-xl overflow-hidden" style={{ border: "1px solid var(--border)" }}>
          <div className="px-4 py-2.5" style={{ background: "var(--secondary)", borderBottom: "1px solid var(--border)" }}>
            <span className="text-[10px] font-bold tracking-widest" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>GUARDIAN RELATIONSHIPS</span>
          </div>
          <div className="divide-y" style={{ borderColor: "var(--border)" }}>
            {minors.map((minor) => {
              const guardianIds = links[minor.tempId] ?? [];
              const guardianNames = guardianIds
                .map((id) => members.find((m) => m.tempId === id))
                .filter(Boolean)
                .map((g) => g.firstName)
                .join(", ");
              return (
                <div key={minor.tempId} className="flex items-center justify-between px-4 py-2.5">
                  <span className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>{minor.firstName}'s guardians</span>
                  <span className="text-[12px] font-medium text-white">{guardianNames || "—"}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className="flex items-start gap-2.5 rounded-xl p-3.5" style={{ background: "rgba(59,130,246,0.06)", border: "1px solid rgba(59,130,246,0.12)" }}>
        <Info size={12} style={{ color: "#3b82f6", flexShrink: 0, marginTop: 1 }} />
        <p className="text-[11px] leading-relaxed" style={{ color: "var(--muted-foreground)" }}>
          After saving, you will be taken to each member's profile to capture their face photos for ArcFace biometric enrollment. The household will remain inactive until all face photos are captured and processed.
        </p>
      </div>
    </div>
  );
}

// ── Main modal ────────────────────────────────────────────────────────────────
export function AddHouseholdModal({ nextHouseholdId, onSave, onClose }) {
  const [step, setStep] = useState(1);
  const [form, setForm] = useState({ familyName: "", address: "", zone: "", contact: "" });
  const [members, setMembers] = useState([]);
  const [guardianLinks, setGuardianLinks] = useState({});
  const [extLinks, setExtLinks] = useState({});

  const step1Valid = form.familyName.trim() && form.address.trim() && form.contact.trim();
  const step2Valid = members.length > 0;

  const handleSave = () => {
    const builtMembers = members.map((m, i) => ({
      id: `MBR-NEW-${i}`,
      firstName: m.firstName,
      lastName: m.lastName,
      relation: "Other",
      birthdate: m.birthdate,
      barangayId: `BRG-TET-${String(9000 + i).padStart(4, "0")}`,
      status: "pending",
      phone: m.phone || "",
    }));

    const allMinorTempIds = [...new Set([
      ...Object.keys(guardianLinks),
      ...Object.keys(extLinks),
    ])];
    const builtLinks = allMinorTempIds
      .map((minorTempId) => {
        const minorIdx = members.findIndex((m) => m.tempId === minorTempId);
        const guardianTempIds = guardianLinks[minorTempId] ?? [];
        const guardianIdxs = guardianTempIds.map((gid) => members.findIndex((m) => m.tempId === gid));
        const internalIds = guardianIdxs.map((idx) => builtMembers[idx]?.id ?? "").filter(Boolean);
        const externalIds = (extLinks[minorTempId] ?? []).map((g) => g.id);
        return {
          minorId: builtMembers[minorIdx]?.id ?? "",
          guardianIds: [...internalIds, ...externalIds],
        };
      })
      .filter((l) => l.minorId && l.guardianIds.length > 0);

    onSave({
      familyName: form.familyName,
      address: form.address,
      zone: form.zone,
      contact: form.contact,
      members: builtMembers,
      guardianLinks: builtLinks,
    });
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.82)", backdropFilter: "blur(8px)" }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        className="w-full max-w-2xl rounded-2xl overflow-hidden shadow-2xl flex flex-col"
        style={{ background: "var(--card)", border: "1px solid var(--border)", maxHeight: "92vh" }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
          <div>
            <div className="text-sm font-semibold text-white">Add Household</div>
            <div className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>Register a new household and enroll all members</div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg" style={{ color: "var(--muted-foreground)", background: "var(--secondary)" }}>
            <X size={14} />
          </button>
        </div>

        {/* Step bar */}
        <StepBar step={step} />

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-5">
          {step === 1 && <StepHousehold form={form} onChange={setForm} />}
          {step === 2 && <StepMembers familyName={form.familyName} members={members} onChange={setMembers} />}
          {step === 3 && <StepGuardians members={members} links={guardianLinks} onChange={setGuardianLinks} extLinks={extLinks} onExtChange={setExtLinks} />}
          {step === 4 && <StepReview form={form} members={members} links={guardianLinks} nextId={nextHouseholdId} />}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-6 py-4 flex-shrink-0" style={{ borderTop: "1px solid var(--border)" }}>
          <div className="flex items-center gap-3">
            {step > 1 && (
              <button
                onClick={() => setStep((s) => s - 1)}
                className="px-4 py-2 rounded-xl text-sm font-medium"
                style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}
              >
                Back
              </button>
            )}
            {step === 1 && (
              <button onClick={onClose} className="px-4 py-2 rounded-xl text-sm font-medium"
                style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
                Cancel
              </button>
            )}
          </div>

          <div className="flex items-center gap-4">
            <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>Step {step} of 4</span>
            {step < 4 ? (
              <button
                disabled={step === 1 ? !step1Valid : step === 2 ? !step2Valid : false}
                onClick={() => setStep((s) => s + 1)}
                className="flex items-center gap-2 px-5 py-2 rounded-xl text-sm font-medium transition-all"
                style={{
                  background: (step === 1 ? step1Valid : step === 2 ? step2Valid : true) ? "var(--primary)" : "rgba(11,84,113,0.2)",
                  color: (step === 1 ? step1Valid : step === 2 ? step2Valid : true) ? "var(--primary-foreground)" : "rgba(133,183,214,0.4)",
                  cursor: (step === 1 ? step1Valid : step === 2 ? step2Valid : true) ? "pointer" : "not-allowed",
                }}
              >
                Next <ChevronRight size={14} />
              </button>
            ) : (
              <button
                onClick={handleSave}
                className="flex items-center gap-2 px-5 py-2 rounded-xl text-sm font-medium"
                style={{ background: "var(--primary)", color: "var(--primary-foreground)" }}
              >
                <Home size={13} /> Save household
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}