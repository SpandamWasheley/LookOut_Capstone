import { useEffect, useState } from "react";
import {
  UserPlus, UserCog, Users, Radio, CheckCircle, Clock, X, Shield, MapPin, Edit2, Trash2,
  Mail, Lock, Shuffle, Loader2, ChevronRight, ChevronLeft, ChevronDown, AlertTriangle,
  Smartphone, MessageSquare, Info,
} from "lucide-react";
import {
  getOfficers, updateOfficer, deleteOfficer, sendOfficerCode, verifyOfficerCode, registerPersonnel,
  getDispatchers, updateDispatcher, deleteDispatcher,
} from "./api";
import { formatPhone, isValidPhone } from "./phone";
import { NoticeDialog } from "./ConfirmDialog";
import { NotificationBell } from "./NotificationBell";

const statusConfig = {
  "on-duty":    { label: "On duty",    color: "#10b981", bg: "rgba(16,185,129,0.1)",  icon: CheckCircle },
  "off-duty":   { label: "Off duty",   color: "#64748b", bg: "rgba(100,116,139,0.1)", icon: Clock },
  "responding": { label: "Responding", color: "#f59e0b", bg: "rgba(245,158,11,0.1)",  icon: Radio },
};

// The Personnel roster is filtered by what an account IS, not by duty status:
// a "both" account appears in the officer list and the dispatcher list, so the
// first three tabs each show one kind on its own.
const ROLE_FILTERS = [
  { key: "all",        label: "All",        color: "var(--primary)", bg: "var(--primary)" },
  { key: "officer",    label: "Officers",   color: "#10b981",        bg: "rgba(16,185,129,0.1)" },
  { key: "dispatcher", label: "Dispatcher", color: "#f59e0b",        bg: "rgba(245,158,11,0.1)" },
  { key: "both",       label: "Both",       color: "#3b82f6",        bg: "rgba(59,130,246,0.1)" },
];

function mapOfficer(raw) {
  return {
    id: raw.code,
    dbId: raw.id,
    name: raw.name,
    badge: raw.badge,
    status: raw.status,
    location: raw.location,
    phone: raw.phone,
    smsAlerts: raw.sms_alerts,
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
    phone: raw.phone,
    smsAlerts: raw.sms_alerts,
    username: raw.username,
    role: raw.role,
  };
}

const blockNumbers = (v) => v.replace(/[0-9]/g, "");

const SMS_HINT = "Turn on/off SMS Message Notification";
const SMS_HINT_NO_PHONE = "Add a mobile number first, then SMS Message Notification can be turned on";
const SMS_HINT_NO_ACCOUNT = "This officer has no login account, so there is nothing to notify";

// The per-person SMS switch, flipped straight from the roster.
//
// Inline rather than only inside the edit dialog because this is the one
// setting an admin changes on its own — silencing one officer should not mean
// opening a form that also holds their email and number.
//
// It cannot be switched ON for somebody with no number: the row would store a
// preference that can never be acted on. Switching OFF is always allowed, so a
// number being cleared can never strand an account in a state it cannot leave.
//
// `hasAccount` is the other half. The switch lives on the USER, and an Officer
// row may have no user at all (Officer.user is nullable — the seeded roster is
// exactly that). Without this the toggle would accept a click, send a PATCH
// the server has nowhere to apply, and flip back on the next refresh with no
// explanation.
function SmsToggle({ on, phone, hasAccount = true, onToggle }) {
  const [busy, setBusy] = useState(false);
  // Why the switch bounced back. In a dialog rather than window.alert, which
  // blocks the tab the violation poll runs in.
  const [notice, setNotice] = useState(null);
  const hasPhone = Boolean(phone);
  const disabled = busy || !hasAccount || (!on && !hasPhone);
  const hint = !hasAccount ? SMS_HINT_NO_ACCOUNT : hasPhone || on ? SMS_HINT : SMS_HINT_NO_PHONE;

  const handleClick = async (e) => {
    e.stopPropagation();
    if (disabled) return;
    setBusy(true);
    try {
      await onToggle(!on);
    } catch (err) {
      setNotice(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
    <button
      type="button"
      onClick={handleClick}
      disabled={disabled}
      role="switch"
      aria-checked={on}
      aria-label="SMS Message Notification"
      title={hint}
      className="w-8 h-[18px] rounded-full relative transition-colors flex-shrink-0 disabled:opacity-40"
      style={{ background: on ? "var(--primary)" : "var(--border)" }}
    >
      <span className="absolute top-0.5 w-3.5 h-3.5 rounded-full bg-white transition-all"
        style={{ left: on ? "1rem" : "0.125rem" }} />
    </button>
    <NoticeDialog
      open={!!notice}
      title="SMS alerts not changed"
      body={notice ? `${notice} The switch is still set the way the server has it.` : ""}
      onClose={() => setNotice(null)}
    />
    </>
  );
}

function generateUsername(firstName, lastName) {
  const base = `${firstName}.${lastName}`.replace(/[^a-zA-Z.]/g, "").toLowerCase();
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

  const isValidEmail = (v) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v.trim());

  // Shown only once something has been typed — an empty required field is
  // already reported by the disabled Next button, and flagging it as "invalid"
  // the moment the modal opens would be scolding the operator for not starting.
  // Checked for every role, not only the ones that require it: a dispatcher's
  // number is optional to GIVE, but a half-typed one is still wrong.
  const phoneInvalid = form.phone.trim() !== "" && !isValidPhone(form.phone);

  const step1Valid = form.firstName.trim() && form.lastName.trim() && form.username.trim()
    && (cfg.requirePhone ? isValidPhone(form.phone) : !phoneInvalid);
  const step2Valid = form.emailVerified;
  const step3Valid = form.password.trim().length >= 8;

  const handleGenerateUsername = () => {
    if (!form.firstName.trim() || !form.lastName.trim()) return;
    set("username", generateUsername(form.firstName, form.lastName));
  };

  const handleSendCode = async () => {
    if (!form.email.trim()) return;
    if (!isValidEmail(form.email)) {
      setError("Enter a valid email address.");
      return;
    }
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
        // Sent whenever one was typed -- required for an officer, accepted
        // for a dispatcher, omitted only when the field was left empty.
        ...(form.phone.trim() ? { phone: form.phone.trim() } : {}),
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
            <div className="text-[13px]" style={{ color: "var(--muted-foreground)" }}>{cfg.subtitle}</div>
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
                  <div className="w-5 h-5 rounded-full flex items-center justify-center text-[12px] font-bold"
                    style={{
                      background: done ? "#10b981" : active ? "var(--primary)" : "var(--secondary)",
                      color: done ? "#fff" : active ? "var(--primary-foreground)" : "var(--muted-foreground)",
                    }}>
                    {done ? <CheckCircle size={11} /> : s.id}
                  </div>
                  <span className="text-[13px] font-medium"
                    style={{ color: done ? "#10b981" : active ? "var(--primary)" : "var(--muted-foreground)" }}>
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
                    style={{ background: "rgba(11,84,113,0.1)", color: "var(--primary)", border: "1px solid rgba(11,84,113,0.2)" }}>
                    <Shuffle size={12} /> Generate
                  </button>
                </div>
              </div>
              {/* Shown for every role. A dispatcher has no officer record, so this
                  field is the only place their number can be recorded -- and they
                  are among the accounts that review an alert first. */}
              <div>
                <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>
                  {cfg.requirePhone ? "Mobile number *" : "Mobile number"}
                </label>
                <input value={form.phone} onChange={(e) => set("phone", formatPhone(e.target.value))} placeholder="+63 917 123 4567"
                  inputMode="tel" autoComplete="tel" aria-invalid={phoneInvalid}
                  aria-describedby={phoneInvalid ? "officer-phone-error" : undefined}
                  className="w-full px-3 py-2.5 rounded-xl text-sm outline-none"
                  style={{
                    background: "var(--secondary)",
                    border: `1px solid ${phoneInvalid ? "rgba(239,68,68,0.4)" : "var(--border)"}`,
                    color: "var(--foreground)",
                  }} />
                {phoneInvalid && (
                  <p id="officer-phone-error" className="flex items-center gap-1.5 text-[13px] mt-1.5" style={{ color: "#ef4444" }}>
                    <AlertTriangle size={11} className="flex-shrink-0" />
                    Enter a mobile number as +63 917 123 4567.
                  </p>
                )}
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
                    style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }} />
                  <button onClick={handleSendCode} type="button" disabled={!isValidEmail(form.email) || sendingCode || form.emailVerified}
                    className="flex items-center gap-1.5 px-3 py-2.5 rounded-xl text-xs font-medium flex-shrink-0 disabled:opacity-50"
                    style={{ background: "rgba(11,84,113,0.1)", color: "var(--primary)", border: "1px solid rgba(11,84,113,0.2)" }}>
                    {sendingCode ? <Loader2 size={12} className="animate-spin" /> : <Mail size={12} />}
                    {codeSent ? "Resend" : "Send code"}
                  </button>
                </div>
              </div>

              {codeSent && !form.emailVerified && (
                <div className="rounded-xl p-3.5 space-y-2" style={{ background: "rgba(11,84,113,0.06)", border: "1px solid rgba(11,84,113,0.2)" }}>
                  <label className="block text-xs font-medium" style={{ color: "var(--primary)" }}>
                    Verification code <span className="font-normal" style={{ color: "var(--muted-foreground)" }}>(check the inbox for {form.email})</span>
                  </label>
                  <div className="flex gap-2">
                    <input value={form.code} onChange={(e) => set("code", e.target.value)} placeholder="6-digit code" maxLength={6}
                      className="flex-1 px-3 py-2.5 rounded-xl text-sm outline-none tracking-widest"
                      style={{ background: "var(--card)", border: "1px solid var(--border)", color: "var(--foreground)", fontFamily: "'DM Mono', monospace" }} />
                    <button onClick={handleVerifyCode} type="button" disabled={!form.code.trim() || verifying}
                      className="flex items-center gap-1.5 px-4 py-2.5 rounded-xl text-xs font-medium flex-shrink-0 disabled:opacity-50"
                      style={{ background: "var(--primary)", color: "var(--primary-foreground)" }}>
                      {verifying ? <Loader2 size={12} className="animate-spin" /> : "Verify"}
                    </button>
                  </div>
                </div>
              )}

              {form.emailVerified && (
                <div className="flex items-center gap-2 rounded-xl p-3" style={{ background: "rgba(16,185,129,0.1)", border: "1px solid rgba(16,185,129,0.25)" }}>
                  <CheckCircle size={14} style={{ color: "#10b981" }} />
                  <span className="text-[14px] font-medium" style={{ color: "#10b981" }}>Email verified</span>
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
                    style={{ background: "rgba(11,84,113,0.1)", color: "var(--primary)", border: "1px solid rgba(11,84,113,0.2)" }}>
                    <Shuffle size={12} /> Generate
                  </button>
                </div>
                <p className="text-[10.5px] mt-1.5 leading-relaxed" style={{ color: "var(--muted-foreground)" }}>
                  At least 8 characters, not all numbers, not a commonly used password, and not too close to the officer's name, username, or email.
                </p>
              </div>
              <div className="rounded-xl p-3.5 flex items-start gap-2.5" style={{ background: "rgba(59,130,246,0.06)", border: "1px solid rgba(59,130,246,0.12)" }}>
                <Lock size={13} style={{ color: "#3b82f6", flexShrink: 0, marginTop: 1 }} />
                <p className="text-[13px] leading-relaxed" style={{ color: "var(--muted-foreground)" }}>
                  This is a <span style={{ color: "var(--foreground)" }}>temporary password</span>. The officer will be required to set a new password the first time they log in.
                </p>
              </div>
            </>
          )}

          {error && (
            <div className="flex items-start gap-2 rounded-xl p-3 text-[14px]" style={{ background: "rgba(239,68,68,0.1)", color: "#ef4444" }}>
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
                background: (step === 1 ? step1Valid : step2Valid) ? "var(--primary)" : "var(--secondary)",
                color: (step === 1 ? step1Valid : step2Valid) ? "var(--primary-foreground)" : "var(--muted-foreground)",
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
                background: step3Valid ? "var(--primary)" : "var(--secondary)",
                color: step3Valid ? "var(--primary-foreground)" : "var(--muted-foreground)",
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

function EditContactModal({ target, onSaved, onClose }) {
  // target: { type: "officer" | "dispatcher", dbId, name, email, phone, smsAlerts }
  const [email, setEmail] = useState(target.email || "");
  const [phone, setPhone] = useState(formatPhone(target.phone || ""));
  const [smsAlerts, setSmsAlerts] = useState(Boolean(target.smsAlerts));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const phoneInvalid = phone.trim() !== "" && !isValidPhone(phone);
  // The number is what makes the switch mean anything, so the two are checked
  // together. Opting an account in with nothing to send to is not a saveable
  // state: refusing it here is clearer than storing a setting that would
  // silently do nothing.
  const smsWithoutNumber = smsAlerts && phone.trim() === "";
  const blocked = !email.trim() || phoneInvalid || smsWithoutNumber;

  const handleSave = async () => {
    if (blocked) return;
    setError("");
    setSaving(true);
    const payload = { email: email.trim(), phone: phone.trim(), sms_alerts: smsAlerts };
    try {
      if (target.type === "officer") {
        await updateOfficer(target.dbId, payload);
      } else {
        await updateDispatcher(target.dbId, payload);
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
            <div className="text-sm font-semibold text-white">Edit Contact Details</div>
            <div className="text-[13px] mt-0.5" style={{ color: "var(--muted-foreground)" }}>{target.name}</div>
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

          <div>
            <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>
              Mobile number
            </label>
            <div className="flex gap-2 items-center px-3 py-2.5 rounded-xl"
              style={{
                background: "var(--secondary)",
                border: `1px solid ${phoneInvalid ? "rgba(239,68,68,0.4)" : "var(--border)"}`,
              }}>
              <Smartphone size={13} style={{ color: "var(--muted-foreground)", flexShrink: 0 }} />
              <input
                value={phone}
                onChange={(e) => setPhone(formatPhone(e.target.value))}
                placeholder="+63 917 123 4567"
                inputMode="tel"
                autoComplete="tel"
                aria-invalid={phoneInvalid}
                className="flex-1 bg-transparent text-sm outline-none"
                style={{ color: "var(--foreground)" }}
              />
            </div>
            {phoneInvalid && (
              <p className="flex items-center gap-1.5 text-[13px] mt-1.5" style={{ color: "#ef4444" }}>
                <AlertTriangle size={11} className="flex-shrink-0" />
                Enter a mobile number as +63 917 123 4567.
              </p>
            )}
          </div>

          <button
            type="button"
            onClick={() => setSmsAlerts((v) => !v)}
            aria-pressed={smsAlerts}
            className="w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-left"
            style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}
          >
            <MessageSquare size={13} style={{ color: "var(--muted-foreground)", flexShrink: 0 }} />
            <span className="flex-1">
              <span className="block text-sm" style={{ color: "var(--foreground)" }}>SMS alerts</span>
              <span className="block text-[12px]" style={{ color: "var(--muted-foreground)" }}>
                Text this number when an alert comes in
              </span>
            </span>
            <span className="w-9 h-5 rounded-full flex-shrink-0 transition-colors relative"
              style={{ background: smsAlerts ? "var(--primary)" : "var(--border)" }}>
              <span className="absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all"
                style={{ left: smsAlerts ? "1.125rem" : "0.125rem" }} />
            </span>
          </button>

          {smsWithoutNumber && (
            <p className="flex items-center gap-1.5 text-[13px]" style={{ color: "#f59e0b" }}>
              <AlertTriangle size={11} className="flex-shrink-0" />
              Add a mobile number to turn on SMS alerts.
            </p>
          )}

          {error && (
            <div className="text-[13px] px-3 py-2 rounded-lg" style={{ background: "rgba(239,68,68,0.08)", color: "#ef4444" }}>
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
          <button onClick={handleSave} disabled={blocked || saving}
            className="flex-1 py-2 rounded-xl text-sm font-medium flex items-center justify-center gap-2 disabled:opacity-50"
            style={{ background: "var(--primary)", color: "var(--primary-foreground)" }}>
            {saving ? <Loader2 size={13} className="animate-spin" /> : <Mail size={13} />}
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

// `alerts` and `onOpenAlerts` feed the header's notification bell. Passed in
// rather than fetched here: the dashboard already polls /alerts/ for every
// page, and this page has no other reason to know about alerts at all.
export function OfficersPage({ alerts = [], onOpenAlerts = () => {} }) {
  const [officers, setOfficers] = useState([]);
  const [dispatchers, setDispatchers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [showAdd, setShowAdd] = useState(null); // "officer" | "dispatcher" | "both" | null
  const [addMenuOpen, setAddMenuOpen] = useState(false);
  const [filterRole, setFilterRole] = useState("all"); // all | officer | dispatcher | both
  const [deleteTarget, setDeleteTarget] = useState(null); // { type, id }
  const [editTarget, setEditTarget] = useState(null); // { type, dbId, name, email }

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

  // A "both" account owns a row in each list, and only the dispatcher route
  // reports `role` -- so the officer rows learn which of them are also
  // dispatchers by matching usernames, rather than the officer serializer
  // growing a field for one filter's sake.
  const bothUsernames = new Set(
    dispatchers.filter((d) => d.role === "both" && d.username).map((d) => d.username),
  );
  const isBoth = (o) => Boolean(o.username) && bothUsernames.has(o.username);

  const showOfficers = filterRole === "all" || filterRole === "officer" || filterRole === "both";
  const showDispatchers = filterRole === "all" || filterRole === "dispatcher" || filterRole === "both";

  const filtered = officers.filter((o) =>
    filterRole === "officer" ? !isBoth(o) : filterRole === "both" ? isBoth(o) : true,
  );
  const visibleDispatchers = dispatchers.filter((d) =>
    filterRole === "dispatcher" ? d.role !== "both" : filterRole === "both" ? d.role === "both" : true,
  );

  const handleAdd = async () => {
    try {
      if (showAdd === "both") await Promise.all([refreshOfficers(), refreshDispatchers()]);
      else if (showAdd === "dispatcher") await refreshDispatchers();
      else await refreshOfficers();
    } finally {
      setShowAdd(null);
    }
  };

  // Why a removal was refused, kept after `deleteTarget` is cleared so the
  // confirmation closes and the reason stays on screen.
  const [removeError, setRemoveError] = useState(null);

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
      setRemoveError({ type: deleteTarget.type, message: err.message });
    } finally {
      setDeleteTarget(null);
    }
  };

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-6 h-14 flex-shrink-0"
        style={{ borderBottom: "1px solid var(--border)" }}>
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-bold" style={{ color: "var(--foreground)" }}>Personnel</h1>
        </div>
        <div className="flex items-center gap-3">
          <NotificationBell alerts={alerts} onOpenAlerts={onOpenAlerts} />

          <div className="relative">
            <button
              onClick={() => setAddMenuOpen((v) => !v)}
              className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium transition-all"
              style={{ background: "var(--primary)", color: "var(--primary-foreground)" }}
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
                        style={{ background: "rgba(11,84,113,0.12)", color: "var(--primary)" }}>
                        <opt.icon size={15} />
                      </div>
                      <div className="min-w-0">
                        <div className="text-[15px] font-medium text-white">{opt.label}</div>
                        <div className="text-[13px]" style={{ color: "var(--muted-foreground)" }}>{opt.desc}</div>
                      </div>
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto flex flex-col px-6 py-5 gap-4">
        {/* Filter tabs */}
        <div className="flex items-center gap-1.5 flex-shrink-0">
          {ROLE_FILTERS.map((f) => (
            <button
              key={f.key}
              onClick={() => setFilterRole(f.key)}
              className="px-3 py-1 text-xs font-medium rounded-full transition-all"
              style={{
                background: filterRole === f.key ? f.bg : "var(--secondary)",
                color:      filterRole === f.key ? (f.key === "all" ? "var(--primary-foreground)" : f.color) : "var(--muted-foreground)",
                border:     `1px solid ${filterRole === f.key ? f.color + "40" : "var(--border)"}`,
              }}
            >
              {f.label}
            </button>
          ))}
        </div>

        {showOfficers && (
          <>
          {/* Table */}
          <div className="rounded-xl flex-shrink-0" style={{ border: "1px solid var(--border)", maxHeight: 420, overflowY: "auto" }}>
            {/* Header */}
            <div
              className="grid px-4 py-2.5 text-[13px] font-semibold sticky top-0"
              style={{
                gridTemplateColumns: "2fr 2fr 1.4fr 1.2fr 0.6fr",
                color: "var(--muted-foreground)",
                background: "var(--card)",
                borderBottom: "1px solid var(--border)",
                zIndex: 1,
              }}
            >
              <span>Officer</span>
              <span className="pl-3">Email</span>
              <span className="pl-3">Mobile</span>
              <span className="flex items-center justify-center gap-2">
                SMS
                {/* The tooltip lives on a wrapping span: a `title` attribute on
                    an <svg> is not what browsers show a tooltip for. */}
                <span title={SMS_HINT} className="flex items-center flex-shrink-0 cursor-help">
                  <Info size={11} style={{ color: "var(--muted-foreground)" }} />
                </span>
              </span>
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
                <div className="text-[13px]" style={{ color: "var(--muted-foreground)" }}>{loadError}</div>
              </div>
            ) : (
              filtered.map((officer, idx) => {
                const scfg = statusConfig[officer.status];
                return (
                  <div
                    key={officer.id}
                    className="grid px-4 py-3 items-center transition-colors hover:bg-white/[0.02] group"
                    style={{
                      gridTemplateColumns: "2fr 2fr 1.4fr 1.2fr 0.6fr",
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
                        <div className="text-[15px] font-medium text-white">{officer.name}</div>
                        <div className="text-[12px]" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
                          {officer.badge} · {officer.username || "—"}
                        </div>
                      </div>
                    </div>

                    {/* Email */}
                    <div className="text-[13px] truncate pl-3" style={{ color: "var(--muted-foreground)" }}>
                      {officer.email || "—"}
                    </div>

                    {/* Mobile */}
                    <div className="text-[13px] truncate pl-3" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
                      {officer.phone ? formatPhone(officer.phone) : "—"}
                    </div>

                    {/* SMS */}
                    <div className="flex items-center justify-center">
                      <SmsToggle
                        on={Boolean(officer.smsAlerts)}
                        phone={officer.phone}
                        // An empty username is how the serializer reports an
                        // officer with no linked account.
                        hasAccount={Boolean(officer.username)}
                        onToggle={async (next) => {
                          await updateOfficer(officer.dbId, { sms_alerts: next });
                          await refreshOfficers();
                        }}
                      />
                    </div>

                    {/* Actions */}
                    <div className="flex items-center gap-1 justify-end">
                      <button
                        onClick={() => setEditTarget({
                          type: "officer", dbId: officer.dbId, name: officer.name,
                          email: officer.email, phone: officer.phone, smsAlerts: officer.smsAlerts,
                        })}
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
          </>
        )}

        {showDispatchers && (
          <>
          {/* Dispatchers */}
          <div className="flex items-center gap-2 flex-shrink-0">
            <h2 className="text-sm font-semibold text-white">Dispatchers</h2>
            <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>{visibleDispatchers.length} total</span>
          </div>
          <div className="rounded-xl overflow-hidden flex-shrink-0" style={{ border: "1px solid var(--border)" }}>
            <div className="grid px-4 py-2.5 text-[13px] font-semibold rounded-t-xl"
              style={{ gridTemplateColumns: "2fr 2fr 1.4fr 1.2fr 0.6fr", color: "var(--muted-foreground)", background: "var(--card)", borderBottom: "1px solid var(--border)" }}>
              <span>Name</span>
              <span className="pl-3">Email</span>
              <span className="pl-3">Mobile</span>
              <span className="flex items-center justify-center gap-2">
                SMS
                {/* The tooltip lives on a wrapping span: a `title` attribute on
                    an <svg> is not what browsers show a tooltip for. */}
                <span title={SMS_HINT} className="flex items-center flex-shrink-0 cursor-help">
                  <Info size={11} style={{ color: "var(--muted-foreground)" }} />
                </span>
              </span>
              <span />
            </div>
            {!loading && visibleDispatchers.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-10 gap-2" style={{ background: "var(--card)" }}>
                <UserCog size={22} style={{ color: "var(--muted-foreground)" }} />
                <div className="text-sm font-medium text-white">No dispatchers found</div>
              </div>
            ) : (
              visibleDispatchers.map((d, idx) => (
                <div key={d.id} className="grid px-4 py-3 items-center transition-colors hover:bg-white/[0.02] group"
                  style={{ gridTemplateColumns: "2fr 2fr 1.4fr 1.2fr 0.6fr", borderBottom: idx < visibleDispatchers.length - 1 ? "1px solid var(--border)" : "none", background: "var(--card)" }}>
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-lg flex items-center justify-center text-xs font-bold flex-shrink-0"
                      style={{ background: "rgba(245,158,11,0.15)", color: "#f59e0b" }}>
                      {d.name[0]?.toUpperCase() ?? "D"}
                    </div>
                    <div>
                      <div className="flex items-center gap-1.5">
                        <span className="text-[15px] font-medium text-white">{d.name}</span>
                        {d.role === "both" && (
                          <span className="text-[11px] font-bold px-1.5 py-0.5 rounded-full" style={{ background: "rgba(59,130,246,0.15)", color: "#3b82f6" }}>
                            OFFICER & DISPATCHER
                          </span>
                        )}
                      </div>
                      <div className="text-[12px]" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>{d.username}</div>
                    </div>
                  </div>
                  <div className="text-[13px] truncate pl-3" style={{ color: "var(--muted-foreground)" }}>{d.email || "—"}</div>
                  <div className="text-[13px] truncate pl-3" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
                    {d.phone ? formatPhone(d.phone) : "—"}
                  </div>
                  <div className="flex items-center justify-center">
                    <SmsToggle
                      on={Boolean(d.smsAlerts)}
                      phone={d.phone}
                      onToggle={async (next) => {
                        await updateDispatcher(d.dbId, { sms_alerts: next });
                        await refreshDispatchers();
                      }}
                    />
                  </div>
                  <div className="flex items-center gap-1 justify-end">
                    <button
                      onClick={() => setEditTarget({
                        type: "dispatcher", dbId: d.dbId, name: d.name,
                        email: d.email, phone: d.phone, smsAlerts: d.smsAlerts,
                      })}
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
          </>
        )}
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
              <div className="text-sm font-semibold text-white">
                {deleteTarget.type === "dispatcher" ? "Remove dispatcher?" : "Remove officer?"}
              </div>
              <p className="text-[14px] mt-1" style={{ color: "var(--muted-foreground)" }}>
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
        <EditContactModal
          target={editTarget}
          onSaved={async () => {
            await Promise.all([refreshOfficers(), refreshDispatchers()]);
            setEditTarget(null);
          }}
          onClose={() => setEditTarget(null)}
        />
      )}

      <NoticeDialog
        open={!!removeError}
        title={`Could not remove that ${removeError?.type ?? "account"}`}
        body={removeError ? `${removeError.message} The account is unchanged.` : ""}
        onClose={() => setRemoveError(null)}
      />
    </div>
  );
}
