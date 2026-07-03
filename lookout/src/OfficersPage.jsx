import { useEffect, useState } from "react";
import {
  UserPlus, UserCog, Users, Radio, CheckCircle, Clock, X, Shield, MapPin, Edit2, Trash2,
  Mail, Lock, Shuffle, Loader2, ChevronRight, ChevronLeft, ChevronDown, AlertTriangle,
} from "lucide-react";
import {
  getOfficers, updateOfficer, deleteOfficer, sendOfficerCode, verifyOfficerCode, registerPersonnel,
  getDispatchers, updateDispatcher, deleteDispatcher,
} from "./api";

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

function mapDispatcher(raw) {
  return {
    id: `dispatcher-${raw.id}`,
    dbId: raw.id,
    name: raw.display_name || raw.username,
    email: raw.email,
    username: raw.username,
    role: raw.role,
  };
}

const formatPhone = (raw) => {
  const d = raw.replace(/\D/g, "").slice(0, 11);
  if (d.length <= 4) return d;
  if (d.length <= 7) return `${d.slice(0, 4)}-${d.slice(4)}`;
  return `${d.slice(0, 4)}-${d.slice(4, 7)}-${d.slice(7)}`;
};
const blockNumbers = (v) => v.replace(/[0-9]/g, "");

function generateUsername(firstName, lastName) {
  const base = `${firstName}.${lastName}`.replace(/[^a-zA-Z.]/g, "");
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

const ROLE_CONFIG = {
  officer: {
    title: "Add Officer",
    subtitle: "Register a new field officer",
    submitLabel: "Add officer",
    submittingLabel: "Creating…",
    requirePhone: true,
  },
  dispatcher: {
    title: "Add Dispatcher",
    subtitle: "Register a new dispatcher account",
    submitLabel: "Add dispatcher",
    submittingLabel: "Creating…",
    requirePhone: false,
  },
  both: {
    title: "Add Personnel",
    subtitle: "Register one account with both officer and dispatcher access",
    submitLabel: "Add personnel",
    submittingLabel: "Creating…",
    requirePhone: true,
  },
};

const ADD_PERSONNEL_OPTIONS = [
  { role: "officer", label: "Officer", desc: "Field officer · mobile app access", icon: Shield },
  { role: "dispatcher", label: "Dispatcher", desc: "Web dispatch board access", icon: UserCog },
  { role: "both", label: "Both", desc: "Officer + dispatcher in one account", icon: Users },
];

function AddAccountModal({ role, onAdd, onClose }) {
  const cfg = ROLE_CONFIG[role];
  const [step, setStep] = useState(1);
  const [form, setForm] = useState(emptyForm);
  const [sendingCode, setSendingCode] = useState(false);
  const [codeSent, setCodeSent] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const set = (k, v) => setForm((p) => ({ ...p, [k]: v }));

  const step1Valid = form.firstName.trim() && form.lastName.trim() && form.username.trim() && (!cfg.requirePhone || form.phone.trim());
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
      const created = await registerPersonnel({
        role,
        first_name: form.firstName.trim(),
        last_name: form.lastName.trim(),
        username: form.username.trim(),
        email: form.email.trim(),
        password: form.password,
        code: form.code.trim(),
        ...(cfg.requirePhone ? { phone: form.phone.trim() } : {}),
      });
      onAdd(created);
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
            <div className="text-sm font-semibold text-white">{cfg.title}</div>
            <div className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>{cfg.subtitle}</div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg"
            style={{ color: "var(--muted-foreground)", background: "var(--secondary)" }}>
            <X size={14} />
          </button>
        </div>

        {/* Step bar */}
        <div className="flex items-center justify-center gap-0 px-5 py-3 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
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
                  <input value={form.firstName} onChange={(e) => set("firstName", blockNumbers(e.target.value))} placeholder="Juan"
                    className="w-full px-3 py-2.5 rounded-xl text-sm outline-none"
                    style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }} />
                </div>
                <div>
                  <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>Last name *</label>
                  <input value={form.lastName} onChange={(e) => set("lastName", blockNumbers(e.target.value))} placeholder="Dela Cruz"
                    className="w-full px-3 py-2.5 rounded-xl text-sm outline-none"
                    style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }} />
                </div>
              </div>
              <div>
                <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>Username *</label>
                <div className="flex gap-2">
                  <input value={form.username} onChange={(e) => set("username", e.target.value)} placeholder="juan.delacruz123"
                    className="flex-1 px-3 py-2.5 rounded-xl text-sm outline-none"
                    style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }} />
                  <button onClick={handleGenerateUsername} type="button"
                    className="flex items-center gap-1.5 px-3 py-2.5 rounded-xl text-xs font-medium flex-shrink-0"
                    style={{ background: "rgba(245,158,11,0.1)", color: "#f59e0b", border: "1px solid rgba(245,158,11,0.2)" }}>
                    <Shuffle size={12} /> Generate
                  </button>
                </div>
              </div>
              {cfg.requirePhone && (
                <div>
                  <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>Phone *</label>
                  <input value={form.phone} onChange={(e) => set("phone", formatPhone(e.target.value))} placeholder="0951-853-2146"
                    className="w-full px-3 py-2.5 rounded-xl text-sm outline-none"
                    style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }} />
                </div>
              )}
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
                    style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }} />
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
                      style={{ background: "var(--card)", border: "1px solid var(--border)", color: "var(--foreground)", fontFamily: "'DM Mono', monospace" }} />
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
                    style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)", fontFamily: "'DM Mono', monospace" }} />
                  <button onClick={() => set("password", generatePassword())} type="button"
                    className="flex items-center gap-1.5 px-3 py-2.5 rounded-xl text-xs font-medium flex-shrink-0"
                    style={{ background: "rgba(245,158,11,0.1)", color: "#f59e0b", border: "1px solid rgba(245,158,11,0.2)" }}>
                    <Shuffle size={12} /> Generate
                  </button>
                </div>
              </div>
              <div className="rounded-xl p-3.5 flex items-start gap-2.5" style={{ background: "rgba(59,130,246,0.06)", border: "1px solid rgba(59,130,246,0.12)" }}>
                <Lock size={13} style={{ color: "#3b82f6", flexShrink: 0, marginTop: 1 }} />
                <p className="text-[11px] leading-relaxed" style={{ color: "var(--muted-foreground)" }}>
                  This is a <span style={{ color: "var(--foreground)" }}>temporary password</span>. The officer will be required to set a new password the first time they log in.
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
              {submitting ? cfg.submittingLabel : cfg.submitLabel}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function EditCredentialsModal({ target, onSaved, onClose }) {
  // target: { type: "officer" | "dispatcher", dbId, name, email }
  const [email, setEmail] = useState(target.email || "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const handleSave = async () => {
    if (!email.trim()) return;
    setError("");
    setSaving(true);
    try {
      if (target.type === "officer") {
        await updateOfficer(target.dbId, { email: email.trim() });
      } else {
        await updateDispatcher(target.dbId, { email: email.trim() });
      }
      onSaved();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.75)", backdropFilter: "blur(6px)" }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="w-full max-w-sm rounded-2xl overflow-hidden shadow-2xl"
        style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
        <div className="flex items-center justify-between px-5 py-4" style={{ borderBottom: "1px solid var(--border)" }}>
          <div>
            <div className="text-sm font-semibold text-white">Edit Credentials</div>
            <div className="text-[11px] mt-0.5" style={{ color: "var(--muted-foreground)" }}>{target.name}</div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg"
            style={{ color: "var(--muted-foreground)", background: "var(--secondary)" }}>
            <X size={14} />
          </button>
        </div>

        <div className="px-5 py-4 space-y-3">
          <div>
            <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>
              Email address
            </label>
            <div className="flex gap-2 items-center px-3 py-2.5 rounded-xl"
              style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
              <Mail size={13} style={{ color: "var(--muted-foreground)", flexShrink: 0 }} />
              <input
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="email@example.com"
                className="flex-1 bg-transparent text-sm outline-none"
                style={{ color: "var(--foreground)" }}
              />
            </div>
          </div>

          {error && (
            <div className="text-[11px] px-3 py-2 rounded-lg" style={{ background: "rgba(239,68,68,0.08)", color: "#ef4444" }}>
              {error}
            </div>
          )}
        </div>

        <div className="flex gap-2 px-5 pb-5">
          <button onClick={onClose}
            className="flex-1 py-2 rounded-xl text-sm font-medium"
            style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
            Cancel
          </button>
          <button onClick={handleSave} disabled={!email.trim() || saving}
            className="flex-1 py-2 rounded-xl text-sm font-medium flex items-center justify-center gap-2 disabled:opacity-50"
            style={{ background: "rgba(245,158,11,0.15)", color: "#f59e0b", border: "1px solid rgba(245,158,11,0.25)" }}>
            {saving ? <Loader2 size={13} className="animate-spin" /> : <Mail size={13} />}
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function OfficersPage() {
  const [officers, setOfficers] = useState([]);
  const [dispatchers, setDispatchers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [showAdd, setShowAdd] = useState(null); // "officer" | "dispatcher" | "both" | null
  const [addMenuOpen, setAddMenuOpen] = useState(false);
  const [filterStatus, setFilterStatus] = useState("all");
  const [deleteTarget, setDeleteTarget] = useState(null); // { type, id }
  const [editTarget, setEditTarget] = useState(null); // { type, dbId, name, email }
  const [statusConfirm, setStatusConfirm] = useState(null); // { id, name, next }

  const refreshOfficers = async () => {
    const data = await getOfficers();
    setOfficers((data.results ?? data).map(mapOfficer));
  };

  const refreshDispatchers = async () => {
    const data = await getDispatchers();
    setDispatchers((data.results ?? data).map(mapDispatcher));
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setLoadError("");
      try {
        await Promise.all([refreshOfficers(), refreshDispatchers()]);
      } catch (err) {
        if (!cancelled) setLoadError(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const interval = setInterval(() => {
      refreshOfficers().catch(() => {});
      refreshDispatchers().catch(() => {});
    }, 4000);
    return () => clearInterval(interval);
  }, []);

  const filtered = officers.filter((o) => filterStatus === "all" || o.status === filterStatus);

  const handleAdd = async () => {
    try {
      if (showAdd === "both") await Promise.all([refreshOfficers(), refreshDispatchers()]);
      else if (showAdd === "dispatcher") await refreshDispatchers();
      else await refreshOfficers();
    } finally {
      setShowAdd(null);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      if (deleteTarget.type === "dispatcher") {
        await deleteDispatcher(deleteTarget.dbId);
      } else {
        await deleteOfficer(deleteTarget.dbId);
      }
      // A "both"-role account can appear in either list, so refresh both
      // to avoid leaving a stale entry behind in the other one.
      await Promise.all([refreshOfficers(), refreshDispatchers()]);
    } catch (err) {
      alert(`Failed to remove ${deleteTarget.type}: ${err.message}`);
    } finally {
      setDeleteTarget(null);
    }
  };

  const toggleStatus = (id) => {
    const officer = officers.find((o) => o.id === id);
    const next = officer.status === "off-duty" ? "on-duty" : "off-duty";
    setStatusConfirm({ id: officer.dbId, name: officer.name, next });
  };

  const confirmToggle = async () => {
    if (!statusConfirm) return;
    try {
      await updateOfficer(statusConfirm.id, { status: statusConfirm.next });
      await refreshOfficers();
    } catch (err) {
      alert(`Failed to update status: ${err.message}`);
    } finally {
      setStatusConfirm(null);
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
        <div className="relative">
          <button
            onClick={() => setAddMenuOpen((v) => !v)}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium transition-all"
            style={{ background: "rgba(245,158,11,0.12)", color: "#f59e0b", border: "1px solid rgba(245,158,11,0.2)" }}
          >
            <UserPlus size={12} /> Add Personnel
            <ChevronDown size={12} style={{ transform: addMenuOpen ? "rotate(180deg)" : "none", transition: "transform 0.15s" }} />
          </button>

          {addMenuOpen && (
            <>
              <div className="fixed inset-0 z-40" onClick={() => setAddMenuOpen(false)} />
              <div className="absolute right-0 top-full mt-2 w-64 rounded-xl overflow-hidden shadow-2xl z-50"
                style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
                {ADD_PERSONNEL_OPTIONS.map((opt) => (
                  <button
                    key={opt.role}
                    onClick={() => { setShowAdd(opt.role); setAddMenuOpen(false); }}
                    className="w-full flex items-center gap-3 px-3.5 py-3 text-left transition-colors"
                    style={{ borderBottom: "1px solid var(--border)" }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "var(--secondary)")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                  >
                    <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
                      style={{ background: "rgba(245,158,11,0.12)", color: "#f59e0b" }}>
                      <opt.icon size={15} />
                    </div>
                    <div className="min-w-0">
                      <div className="text-[13px] font-medium text-white">{opt.label}</div>
                      <div className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>{opt.desc}</div>
                    </div>
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto flex flex-col px-6 py-5 gap-4">
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
        <div className="rounded-xl flex-shrink-0" style={{ border: "1px solid var(--border)", maxHeight: 420, overflowY: "auto" }}>
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
            <span>Username</span>
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
                        {officer.badge} · {formatPhone(officer.phone ?? "")}
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

                  {/* Username */}
                  <div className="text-[12px] flex items-center gap-1" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
                    {officer.username || "—"}
                  </div>

                  {/* Email */}
                  <div className="text-[11px] truncate" style={{ color: "var(--muted-foreground)" }}>
                    {officer.email || "—"}
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-1 justify-end">
                    <button
                      onClick={() => setEditTarget({ type: "officer", dbId: officer.dbId, name: officer.name, email: officer.email })}
                      className="p-1.5 rounded-lg transition-all" style={{ color: "var(--muted-foreground)" }}
                      onMouseEnter={(e) => { e.currentTarget.style.color = "var(--foreground)"; e.currentTarget.style.background = "var(--secondary)"; }}
                      onMouseLeave={(e) => { e.currentTarget.style.color = "var(--muted-foreground)"; e.currentTarget.style.background = "transparent"; }}
                      title="Edit credentials">
                      <Edit2 size={13} />
                    </button>
                    <button onClick={() => setDeleteTarget({ type: "officer", dbId: officer.dbId, name: officer.name })}
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

        {/* Dispatchers */}
        <div className="flex items-center gap-2 flex-shrink-0">
          <h2 className="text-sm font-semibold text-white">Dispatchers</h2>
          <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>{dispatchers.length} total</span>
        </div>
        <div className="rounded-xl flex-shrink-0" style={{ border: "1px solid var(--border)" }}>
          <div className="grid px-4 py-2.5 text-[11px] font-semibold"
            style={{ gridTemplateColumns: "2fr 2fr 1fr", color: "var(--muted-foreground)", background: "var(--card)", borderBottom: "1px solid var(--border)" }}>
            <span>Name</span>
            <span>Email</span>
            <span />
          </div>
          {!loading && dispatchers.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-10 gap-2" style={{ background: "var(--card)" }}>
              <UserCog size={22} style={{ color: "var(--muted-foreground)" }} />
              <div className="text-sm font-medium text-white">No dispatchers found</div>
            </div>
          ) : (
            dispatchers.map((d, idx) => (
              <div key={d.id} className="grid px-4 py-3 items-center transition-colors hover:bg-white/[0.02] group"
                style={{ gridTemplateColumns: "2fr 2fr 1fr", borderBottom: idx < dispatchers.length - 1 ? "1px solid var(--border)" : "none", background: "var(--card)" }}>
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-lg flex items-center justify-center text-xs font-bold flex-shrink-0"
                    style={{ background: "rgba(245,158,11,0.15)", color: "#f59e0b" }}>
                    {d.name[0]?.toUpperCase() ?? "D"}
                  </div>
                  <div>
                    <div className="flex items-center gap-1.5">
                      <span className="text-[13px] font-medium text-white">{d.name}</span>
                      {d.role === "both" && (
                        <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full" style={{ background: "rgba(59,130,246,0.15)", color: "#3b82f6" }}>
                          OFFICER & DISPATCHER
                        </span>
                      )}
                    </div>
                    <div className="text-[10px]" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>{d.username}</div>
                  </div>
                </div>
                <div className="text-[11px] truncate" style={{ color: "var(--muted-foreground)" }}>{d.email || "—"}</div>
                <div className="flex items-center gap-1 justify-end">
                  <button
                    onClick={() => setEditTarget({ type: "dispatcher", dbId: d.dbId, name: d.name, email: d.email })}
                    className="p-1.5 rounded-lg transition-all" style={{ color: "var(--muted-foreground)" }}
                    onMouseEnter={(e) => { e.currentTarget.style.color = "var(--foreground)"; e.currentTarget.style.background = "var(--secondary)"; }}
                    onMouseLeave={(e) => { e.currentTarget.style.color = "var(--muted-foreground)"; e.currentTarget.style.background = "transparent"; }}
                    title="Edit credentials">
                    <Edit2 size={13} />
                  </button>
                  <button onClick={() => setDeleteTarget({ type: "dispatcher", dbId: d.dbId, name: d.name })}
                    className="p-1.5 rounded-lg transition-all" style={{ color: "var(--muted-foreground)" }}
                    onMouseEnter={(e) => { e.currentTarget.style.color = "#ef4444"; e.currentTarget.style.background = "rgba(239,68,68,0.08)"; }}
                    onMouseLeave={(e) => { e.currentTarget.style.color = "var(--muted-foreground)"; e.currentTarget.style.background = "transparent"; }}>
                    <Trash2 size={13} />
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Confirm status change */}
      {statusConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ background: "rgba(0,0,0,0.75)", backdropFilter: "blur(6px)" }}>
          <div className="w-full max-w-xs rounded-2xl overflow-hidden"
            style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
            <div className="px-5 py-5 text-center">
              <div className="w-10 h-10 rounded-full flex items-center justify-center mx-auto mb-3"
                style={{ background: statusConfirm.next === "on-duty" ? "rgba(16,185,129,0.1)" : "rgba(100,116,139,0.1)" }}>
                {statusConfirm.next === "on-duty"
                  ? <CheckCircle size={18} style={{ color: "#10b981" }} />
                  : <Clock size={18} style={{ color: "#64748b" }} />}
              </div>
              <div className="text-sm font-semibold text-white">Change duty status?</div>
              <p className="text-[12px] mt-1" style={{ color: "var(--muted-foreground)" }}>
                Set <span className="text-white">{statusConfirm.name}</span> to{" "}
                <span style={{ color: statusConfirm.next === "on-duty" ? "#10b981" : "#64748b", fontWeight: 600 }}>
                  {statusConfirm.next === "on-duty" ? "On Duty" : "Off Duty"}
                </span>?
              </p>
            </div>
            <div className="flex gap-2 px-5 pb-5">
              <button onClick={() => setStatusConfirm(null)}
                className="flex-1 py-2 rounded-xl text-sm font-medium"
                style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
                Cancel
              </button>
              <button onClick={confirmToggle}
                className="flex-1 py-2 rounded-xl text-sm font-medium"
                style={{
                  background: statusConfirm.next === "on-duty" ? "rgba(16,185,129,0.15)" : "rgba(100,116,139,0.15)",
                  color: statusConfirm.next === "on-duty" ? "#10b981" : "#94a3b8",
                  border: `1px solid ${statusConfirm.next === "on-duty" ? "rgba(16,185,129,0.25)" : "rgba(100,116,139,0.25)"}`,
                }}>
                Confirm
              </button>
            </div>
          </div>
        </div>
      )}

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
              <div className="text-sm font-semibold text-white">
                {deleteTarget.type === "dispatcher" ? "Remove dispatcher?" : "Remove officer?"}
              </div>
              <p className="text-[12px] mt-1" style={{ color: "var(--muted-foreground)" }}>
                {deleteTarget.name} will lose access and be removed from the {deleteTarget.type === "dispatcher" ? "dispatcher list" : "roster"}.
              </p>
            </div>
            <div className="flex gap-2 px-5 pb-5">
              <button onClick={() => setDeleteTarget(null)}
                className="flex-1 py-2 rounded-xl text-sm font-medium"
                style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
                Cancel
              </button>
              <button onClick={handleDelete}
                className="flex-1 py-2 rounded-xl text-sm font-medium"
                style={{ background: "rgba(239,68,68,0.15)", color: "#ef4444", border: "1px solid rgba(239,68,68,0.25)" }}>
                Remove
              </button>
            </div>
          </div>
        </div>
      )}

      {showAdd && <AddAccountModal role={showAdd} onAdd={handleAdd} onClose={() => setShowAdd(null)} />}

      {editTarget && (
        <EditCredentialsModal
          target={editTarget}
          onSaved={async () => {
            await Promise.all([refreshOfficers(), refreshDispatchers()]);
            setEditTarget(null);
          }}
          onClose={() => setEditTarget(null)}
        />
      )}
    </div>
  );
}
