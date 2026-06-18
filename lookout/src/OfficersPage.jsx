import { useEffect, useState } from "react";
import {
  UserPlus, Radio, CheckCircle, Clock, X, Shield, MapPin, Edit2, Trash2,
  Mail, Lock, Shuffle, Loader2, ChevronRight, ChevronLeft, AlertTriangle,
} from "lucide-react";
import { getOfficers, updateOfficer, deleteOfficer, sendOfficerCode, verifyOfficerCode, registerOfficer } from "./api";

const statusConfig = {
  "on-duty":    { label: "On duty",    color: "#10b981", bg: "rgba(16,185,129,0.1)",  icon: CheckCircle },
  "off-duty":   { label: "Off duty",   color: "#64748b", bg: "rgba(100,116,139,0.1)", icon: Clock },
  "responding": { label: "Responding", color: "#f59e0b", bg: "rgba(245,158,11,0.1)",  icon: Radio },
};

function mapOfficer(raw) {
  return {
    id: raw.code,
    dbId: raw.id,
    name: raw.name,
    badge: raw.badge,
    status: raw.status,
    location: raw.location,
    phone: raw.phone,
    email: raw.email,
    username: raw.username,
    joinedDate: raw.joined_date,
  };
}

function generateUsername(firstName, lastName) {
  const base = `${firstName}.${lastName}`.toLowerCase().replace(/[^a-z.]/g, "");
  const suffix = Math.floor(100 + Math.random() * 900);
  return `${base}${suffix}`;
}

function generatePassword() {
  const chars = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789!@#$%";
  const arr = new Uint32Array(12);
  crypto.getRandomValues(arr);
  return Array.from(arr, (n) => chars[n % chars.length]).join("");
}

const emptyForm = {
  firstName: "", lastName: "", username: "", phone: "",
  email: "", code: "", emailVerified: false,
  password: "",
};

const steps = [
  { id: 1, label: "Details" },
  { id: 2, label: "Verify Email" },
  { id: 3, label: "Password" },
];

function AddOfficerModal({ onAdd, onClose }) {
  const [step, setStep] = useState(1);
  const [form, setForm] = useState(emptyForm);
  const [sendingCode, setSendingCode] = useState(false);
  const [codeSent, setCodeSent] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const set = (k, v) => setForm((p) => ({ ...p, [k]: v }));

  const step1Valid = form.firstName.trim() && form.lastName.trim() && form.username.trim() && form.phone.trim();
  const step2Valid = form.emailVerified;
  const step3Valid = form.password.trim().length >= 8;

  const handleGenerateUsername = () => {
    if (!form.firstName.trim() || !form.lastName.trim()) return;
    set("username", generateUsername(form.firstName, form.lastName));
  };

  const handleSendCode = async () => {
    if (!form.email.trim()) return;
    setError("");
    setSendingCode(true);
    try {
      await sendOfficerCode(form.email.trim());
      setCodeSent(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setSendingCode(false);
    }
  };

  const handleVerifyCode = async () => {
    if (!form.code.trim()) return;
    setError("");
    setVerifying(true);
    try {
      await verifyOfficerCode(form.email.trim(), form.code.trim());
      set("emailVerified", true);
    } catch (err) {
      setError(err.message);
    } finally {
      setVerifying(false);
    }
  };

  const handleSubmit = async () => {
    setError("");
    setSubmitting(true);
    try {
      const officer = await registerOfficer({
        first_name: form.firstName.trim(),
        last_name: form.lastName.trim(),
        username: form.username.trim(),
        email: form.email.trim(),
        phone: form.phone.trim(),
        password: form.password,
        code: form.code.trim(),
      });
      onAdd(officer);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.75)", backdropFilter: "blur(6px)" }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="w-full max-w-md rounded-2xl overflow-hidden shadow-2xl flex flex-col"
        style={{ background: "var(--card)", border: "1px solid var(--border)", maxHeight: "92vh" }}>
        <div className="flex items-center justify-between px-5 py-4 flex-shrink-0"
          style={{ borderBottom: "1px solid var(--border)" }}>
          <div>
            <div className="text-sm font-semibold text-white">Add Officer</div>
            <div className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>Register a new field officer</div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg"
            style={{ color: "var(--muted-foreground)", background: "var(--secondary)" }}>
            <X size={14} />
          </button>
        </div>

        {/* Step bar */}
        <div className="flex items-center gap-0 px-5 py-3 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
          {steps.map((s, i) => {
            const done = step > s.id;
            const active = step === s.id;
            return (
              <div key={s.id} className="flex items-center">
                <div className="flex items-center gap-1.5">
                  <div className="w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold"
                    style={{
                      background: done ? "#10b981" : active ? "#f59e0b" : "var(--secondary)",
                      color: done ? "#fff" : active ? "#0c0f16" : "var(--muted-foreground)",
                    }}>
                    {done ? <CheckCircle size={11} /> : s.id}
                  </div>
                  <span className="text-[11px] font-medium"
                    style={{ color: done ? "#10b981" : active ? "#f59e0b" : "var(--muted-foreground)" }}>
                    {s.label}
                  </span>
                </div>
                {i < steps.length - 1 && <div className="w-6 h-px mx-2" style={{ background: done ? "#10b981" : "var(--border)" }} />}
              </div>
            );
          })}
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-3">
          {step === 1 && (
            <>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>First name *</label>
                  <input value={form.firstName} onChange={(e) => set("firstName", e.target.value)} placeholder="Juan"
                    className="w-full px-3 py-2.5 rounded-xl text-sm outline-none"
                    style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "#f1f5f9" }} />
                </div>
                <div>
                  <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>Last name *</label>
                  <input value={form.lastName} onChange={(e) => set("lastName", e.target.value)} placeholder="Dela Cruz"
                    className="w-full px-3 py-2.5 rounded-xl text-sm outline-none"
                    style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "#f1f5f9" }} />
                </div>
              </div>
              <div>
                <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>Username *</label>
                <div className="flex gap-2">
                  <input value={form.username} onChange={(e) => set("username", e.target.value)} placeholder="juan.delacruz123"
                    className="flex-1 px-3 py-2.5 rounded-xl text-sm outline-none"
                    style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "#f1f5f9" }} />
                  <button onClick={handleGenerateUsername} type="button"
                    className="flex items-center gap-1.5 px-3 py-2.5 rounded-xl text-xs font-medium flex-shrink-0"
                    style={{ background: "rgba(245,158,11,0.1)", color: "#f59e0b", border: "1px solid rgba(245,158,11,0.2)" }}>
                    <Shuffle size={12} /> Generate
                  </button>
                </div>
              </div>
              <div>
                <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>Phone *</label>
                <input value={form.phone} onChange={(e) => set("phone", e.target.value)} placeholder="+63 9XX XXX XXXX"
                  className="w-full px-3 py-2.5 rounded-xl text-sm outline-none"
                  style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "#f1f5f9" }} />
              </div>
            </>
          )}

          {step === 2 && (
            <>
              <div>
                <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>Email *</label>
                <div className="flex gap-2">
                  <input value={form.email} disabled={form.emailVerified}
                    onChange={(e) => { set("email", e.target.value); set("emailVerified", false); setCodeSent(false); }}
                    placeholder="officer@example.com"
                    className="flex-1 px-3 py-2.5 rounded-xl text-sm outline-none disabled:opacity-60"
                    style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "#f1f5f9" }} />
                  <button onClick={handleSendCode} type="button" disabled={!form.email.trim() || sendingCode || form.emailVerified}
                    className="flex items-center gap-1.5 px-3 py-2.5 rounded-xl text-xs font-medium flex-shrink-0 disabled:opacity-50"
                    style={{ background: "rgba(245,158,11,0.1)", color: "#f59e0b", border: "1px solid rgba(245,158,11,0.2)" }}>
                    {sendingCode ? <Loader2 size={12} className="animate-spin" /> : <Mail size={12} />}
                    {codeSent ? "Resend" : "Send code"}
                  </button>
                </div>
              </div>

              {codeSent && !form.emailVerified && (
                <div className="rounded-xl p-3.5 space-y-2" style={{ background: "rgba(245,158,11,0.06)", border: "1px solid rgba(245,158,11,0.2)" }}>
                  <label className="block text-xs font-medium" style={{ color: "#f59e0b" }}>
                    Verification code <span className="font-normal" style={{ color: "var(--muted-foreground)" }}>(check the inbox for {form.email})</span>
                  </label>
                  <div className="flex gap-2">
                    <input value={form.code} onChange={(e) => set("code", e.target.value)} placeholder="6-digit code" maxLength={6}
                      className="flex-1 px-3 py-2.5 rounded-xl text-sm outline-none tracking-widest"
                      style={{ background: "var(--card)", border: "1px solid var(--border)", color: "#f1f5f9", fontFamily: "'DM Mono', monospace" }} />
                    <button onClick={handleVerifyCode} type="button" disabled={!form.code.trim() || verifying}
                      className="flex items-center gap-1.5 px-4 py-2.5 rounded-xl text-xs font-medium flex-shrink-0 disabled:opacity-50"
                      style={{ background: "#f59e0b", color: "#0c0f16" }}>
                      {verifying ? <Loader2 size={12} className="animate-spin" /> : "Verify"}
                    </button>
                  </div>
                </div>
              )}

              {form.emailVerified && (
                <div className="flex items-center gap-2 rounded-xl p-3" style={{ background: "rgba(16,185,129,0.1)", border: "1px solid rgba(16,185,129,0.25)" }}>
                  <CheckCircle size={14} style={{ color: "#10b981" }} />
                  <span className="text-[12px] font-medium" style={{ color: "#10b981" }}>Email verified</span>
                </div>
              )}
            </>
          )}

          {step === 3 && (
            <>
              <div>
                <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>Temporary password *</label>
                <div className="flex gap-2">
                  <input value={form.password} onChange={(e) => set("password", e.target.value)} placeholder="At least 8 characters"
                    className="flex-1 px-3 py-2.5 rounded-xl text-sm outline-none"
                    style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "#f1f5f9", fontFamily: "'DM Mono', monospace" }} />
                  <button onClick={() => set("password", generatePassword())} type="button"
                    className="flex items-center gap-1.5 px-3 py-2.5 rounded-xl text-xs font-medium flex-shrink-0"
                    style={{ background: "rgba(245,158,11,0.1)", color: "#f59e0b", border: "1px solid rgba(245,158,11,0.2)" }}>
                    <Shuffle size={12} /> Generate
                  </button>
                </div>
              </div>
              <div className="rounded-xl p-3.5 flex items-start gap-2.5" style={{ background: "rgba(59,130,246,0.06)", border: "1px solid rgba(59,130,246,0.12)" }}>
                <Lock size={13} style={{ color: "#3b82f6", flexShrink: 0, marginTop: 1 }} />
                <p className="text-[11px] leading-relaxed" style={{ color: "#94a3b8" }}>
                  This is a <span style={{ color: "#f1f5f9" }}>temporary password</span>. The officer will be required to set a new password the first time they log in.
                </p>
              </div>
            </>
          )}

          {error && (
            <div className="flex items-start gap-2 rounded-xl p-3 text-[12px]" style={{ background: "rgba(239,68,68,0.1)", color: "#ef4444" }}>
              <AlertTriangle size={13} className="flex-shrink-0 mt-0.5" /> {error}
            </div>
          )}
        </div>

        <div className="flex items-center justify-between gap-3 px-5 py-4 flex-shrink-0"
          style={{ borderTop: "1px solid var(--border)" }}>
          {step === 1 ? (
            <button onClick={onClose} className="px-4 py-2 rounded-xl text-sm font-medium"
              style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
              Cancel
            </button>
          ) : (
            <button onClick={() => { setError(""); setStep((s) => s - 1); }}
              className="flex items-center gap-1 px-4 py-2 rounded-xl text-sm font-medium"
              style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
              <ChevronLeft size={14} /> Back
            </button>
          )}

          {step < 3 ? (
            <button
              disabled={step === 1 ? !step1Valid : !step2Valid}
              onClick={() => { setError(""); setStep((s) => s + 1); }}
              className="flex items-center gap-2 px-5 py-2 rounded-xl text-sm font-medium transition-all"
              style={{
                background: (step === 1 ? step1Valid : step2Valid) ? "#f59e0b" : "rgba(245,158,11,0.2)",
                color: (step === 1 ? step1Valid : step2Valid) ? "#0c0f16" : "rgba(245,158,11,0.4)",
                cursor: (step === 1 ? step1Valid : step2Valid) ? "pointer" : "not-allowed",
              }}>
              Next <ChevronRight size={14} />
            </button>
          ) : (
            <button
              disabled={!step3Valid || submitting}
              onClick={handleSubmit}
              className="flex items-center gap-2 px-5 py-2 rounded-xl text-sm font-medium transition-all"
              style={{
                background: step3Valid ? "rgba(245,158,11,0.2)" : "rgba(245,158,11,0.05)",
                color: step3Valid ? "#f59e0b" : "rgba(245,158,11,0.4)",
                cursor: step3Valid && !submitting ? "pointer" : "not-allowed",
              }}>
              {submitting ? <Loader2 size={13} className="animate-spin" /> : <UserPlus size={13} />}
              {submitting ? "Creating…" : "Add officer"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export function OfficersPage() {
  const [officers, setOfficers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [showAdd, setShowAdd] = useState(false);
  const [filterStatus, setFilterStatus] = useState("all");
  const [deleteTarget, setDeleteTarget] = useState(null);

  const refreshOfficers = async () => {
    const data = await getOfficers();
    setOfficers((data.results ?? data).map(mapOfficer));
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setLoadError("");
      try {
        await refreshOfficers();
      } catch (err) {
        if (!cancelled) setLoadError(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const filtered = officers.filter((o) => filterStatus === "all" || o.status === filterStatus);

  const handleAdd = async () => {
    try {
      await refreshOfficers();
    } finally {
      setShowAdd(false);
    }
  };

  const handleDelete = async (id) => {
    const officer = officers.find((o) => o.id === id);
    try {
      await deleteOfficer(officer.dbId);
      await refreshOfficers();
    } catch (err) {
      alert(`Failed to remove officer: ${err.message}`);
    } finally {
      setDeleteTarget(null);
    }
  };

  const toggleStatus = async (id) => {
    const officer = officers.find((o) => o.id === id);
    const next = officer.status === "off-duty" ? "on-duty" : "off-duty";
    try {
      await updateOfficer(officer.dbId, { status: next });
      await refreshOfficers();
    } catch (err) {
      alert(`Failed to update status: ${err.message}`);
    }
  };

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-6 h-14 flex-shrink-0"
        style={{ borderBottom: "1px solid var(--border)" }}>
        <div className="flex items-center gap-3">
          <h1 className="text-[15px] font-semibold text-white">Officers</h1>
          <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>
            {officers.filter((o) => o.status !== "off-duty").length} on duty · {officers.length} total
          </span>
        </div>
        <button
          onClick={() => setShowAdd(true)}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium transition-all"
          style={{ background: "rgba(245,158,11,0.12)", color: "#f59e0b", border: "1px solid rgba(245,158,11,0.2)" }}
        >
          <UserPlus size={12} /> Add officer
        </button>
      </div>

      <div className="flex-1 overflow-hidden flex flex-col px-6 py-5 gap-4">
        {/* KPIs */}
        <div className="grid grid-cols-4 gap-3 flex-shrink-0">
          {[
            { label: "Total",      value: officers.length,                                        color: "#f59e0b" },
            { label: "On Duty",    value: officers.filter((o) => o.status === "on-duty").length,    color: "#10b981" },
            { label: "Responding", value: officers.filter((o) => o.status === "responding").length, color: "#f59e0b" },
            { label: "Off Duty",   value: officers.filter((o) => o.status === "off-duty").length,   color: "#64748b" },
          ].map((s) => (
            <div key={s.label} className="rounded-xl p-4 text-center"
              style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
              <div className="text-2xl font-semibold leading-none" style={{ color: s.color }}>{s.value}</div>
              <div className="text-[11px] mt-1.5" style={{ color: "var(--muted-foreground)" }}>{s.label}</div>
            </div>
          ))}
        </div>

        {/* Filter tabs */}
        <div className="flex items-center gap-1.5 flex-shrink-0">
          {["all", "on-duty", "responding", "off-duty"].map((s) => (
            <button
              key={s}
              onClick={() => setFilterStatus(s)}
              className="px-3 py-1 text-xs font-medium rounded-full transition-all capitalize"
              style={{
                background: filterStatus === s ? (s === "all" ? "#f59e0b" : statusConfig[s]?.bg ?? "rgba(245,158,11,0.1)") : "var(--secondary)",
                color:      filterStatus === s ? (s === "all" ? "#0c0f16" : statusConfig[s]?.color ?? "#f59e0b") : "var(--muted-foreground)",
                border:     `1px solid ${filterStatus === s ? (s === "all" ? "#f59e0b" : (statusConfig[s]?.color ?? "#f59e0b") + "40") : "var(--border)"}`,
              }}
            >
              {s === "all" ? "All" : s.replace("-", " ")}
            </button>
          ))}
        </div>

        {/* Table */}
        <div className="flex-1 overflow-y-auto rounded-xl" style={{ border: "1px solid var(--border)" }}>
          {/* Header */}
          <div
            className="grid px-4 py-2.5 text-[11px] font-semibold sticky top-0"
            style={{
              gridTemplateColumns: "2fr 1fr 1fr 1fr 0.5fr",
              color: "var(--muted-foreground)",
              background: "var(--card)",
              borderBottom: "1px solid var(--border)",
              zIndex: 1,
            }}
          >
            <span>Officer</span>
            <span>Status</span>
            <span>Location</span>
            <span>Email</span>
            <span />
          </div>

          {loading ? (
            <div className="flex flex-col items-center justify-center py-16 gap-2" style={{ background: "var(--card)" }}>
              <Loader2 size={24} className="animate-spin" style={{ color: "var(--muted-foreground)" }} />
              <div className="text-sm font-medium text-white">Loading officers…</div>
            </div>
          ) : loadError ? (
            <div className="flex flex-col items-center justify-center py-16 gap-2" style={{ background: "var(--card)" }}>
              <AlertTriangle size={24} style={{ color: "#ef4444" }} />
              <div className="text-sm font-medium text-white">Failed to load officers</div>
              <div className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>{loadError}</div>
            </div>
          ) : (
            filtered.map((officer, idx) => {
              const scfg = statusConfig[officer.status];
              const StatusIcon = scfg.icon;
              return (
                <div
                  key={officer.id}
                  className="grid px-4 py-3 items-center transition-colors hover:bg-white/[0.02] group"
                  style={{
                    gridTemplateColumns: "2fr 1fr 1fr 1fr 0.5fr",
                    borderBottom: idx < filtered.length - 1 ? "1px solid var(--border)" : "none",
                    background: "var(--card)",
                  }}
                >
                  {/* Name + badge */}
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-lg flex items-center justify-center text-xs font-bold flex-shrink-0"
                      style={{ background: `${scfg.color}22`, color: scfg.color }}>
                      {officer.name.split(" ")[1]?.[0] ?? officer.name[0] ?? "O"}
                    </div>
                    <div>
                      <div className="text-[13px] font-medium text-white">{officer.name}</div>
                      <div className="text-[10px]" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
                        {officer.badge} · {officer.phone}
                      </div>
                    </div>
                  </div>

                  {/* Status */}
                  <button
                    onClick={() => toggleStatus(officer.id)}
                    className="flex items-center gap-1.5 px-2 py-1 rounded-md w-fit text-[11px] font-medium transition-all"
                    style={{ background: scfg.bg, color: scfg.color }}
                    title="Click to toggle on-duty / off-duty"
                  >
                    <StatusIcon size={10} /> {scfg.label}
                  </button>

                  {/* Location */}
                  <div className="text-[12px] flex items-center gap-1" style={{ color: "#cbd5e1" }}>
                    <MapPin size={10} style={{ color: "var(--muted-foreground)", flexShrink: 0 }} />
                    {officer.location || "—"}
                  </div>

                  {/* Email */}
                  <div className="text-[11px] truncate" style={{ color: "var(--muted-foreground)" }}>
                    {officer.email || "—"}
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-1 justify-end">
                    <button className="p-1.5 rounded-lg transition-all" style={{ color: "var(--muted-foreground)" }}
                      onMouseEnter={(e) => { e.currentTarget.style.color = "#f1f5f9"; e.currentTarget.style.background = "var(--secondary)"; }}
                      onMouseLeave={(e) => { e.currentTarget.style.color = "var(--muted-foreground)"; e.currentTarget.style.background = "transparent"; }}>
                      <Edit2 size={13} />
                    </button>
                    <button onClick={() => setDeleteTarget(officer.id)}
                      className="p-1.5 rounded-lg transition-all" style={{ color: "var(--muted-foreground)" }}
                      onMouseEnter={(e) => { e.currentTarget.style.color = "#ef4444"; e.currentTarget.style.background = "rgba(239,68,68,0.08)"; }}
                      onMouseLeave={(e) => { e.currentTarget.style.color = "var(--muted-foreground)"; e.currentTarget.style.background = "transparent"; }}>
                      <Trash2 size={13} />
                    </button>
                  </div>
                </div>
              );
            })
          )}

          {!loading && !loadError && filtered.length === 0 && (
            <div className="flex flex-col items-center justify-center py-16 gap-2"
              style={{ background: "var(--card)" }}>
              <Shield size={28} style={{ color: "var(--muted-foreground)" }} />
              <div className="text-sm font-medium text-white">No officers found</div>
            </div>
          )}
        </div>
      </div>

      {/* Confirm delete */}
      {deleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ background: "rgba(0,0,0,0.75)", backdropFilter: "blur(6px)" }}>
          <div className="w-full max-w-xs rounded-2xl overflow-hidden"
            style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
            <div className="px-5 py-5 text-center">
              <div className="w-10 h-10 rounded-full flex items-center justify-center mx-auto mb-3"
                style={{ background: "rgba(239,68,68,0.1)" }}>
                <Trash2 size={18} style={{ color: "#ef4444" }} />
              </div>
              <div className="text-sm font-semibold text-white">Remove officer?</div>
              <p className="text-[12px] mt-1" style={{ color: "var(--muted-foreground)" }}>
                {officers.find((o) => o.id === deleteTarget)?.name} will be removed from the roster.
              </p>
            </div>
            <div className="flex gap-2 px-5 pb-5">
              <button onClick={() => setDeleteTarget(null)}
                className="flex-1 py-2 rounded-xl text-sm font-medium"
                style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
                Cancel
              </button>
              <button onClick={() => handleDelete(deleteTarget)}
                className="flex-1 py-2 rounded-xl text-sm font-medium"
                style={{ background: "rgba(239,68,68,0.15)", color: "#ef4444", border: "1px solid rgba(239,68,68,0.25)" }}>
                Remove
              </button>
            </div>
          </div>
        </div>
      )}

      {showAdd && <AddOfficerModal onAdd={handleAdd} onClose={() => setShowAdd(false)} />}
    </div>
  );
}
