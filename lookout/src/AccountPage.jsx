import { useEffect, useState } from "react";
import { Smartphone, MessageSquare, AlertTriangle, Loader2, Check, User as UserIcon } from "lucide-react";

import { getMe, updateMe } from "./api";
import { formatPhone, isValidPhone } from "./phone";

// My Account. Deliberately small: the only things a user may change about
// their own account are their mobile number and whether they want to be
// texted. Who they are and what they may do stays an admin's to set, which is
// enforced on the server (UserSerializer.read_only_fields), not just here.
//
// It exists because the number has to be reachable without an admin. An admin
// can fill one in from Personnel, but a dispatcher whose SIM changed should not
// have to find one to keep getting alerts — and admins have no Personnel row of
// their own to be edited through at all.

const ROLE_LABEL = {
  admin: "Administrator",
  dispatcher: "Dispatcher",
  officer: "Officer",
  both: "Officer & Dispatcher",
};

function Field({ label, children }) {
  return (
    <div>
      <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>
        {label}
      </label>
      {children}
    </div>
  );
}

function ReadOnlyRow({ label, value }) {
  return (
    <div className="flex items-center justify-between py-2.5" style={{ borderBottom: "1px solid var(--border)" }}>
      <span className="text-[13px]" style={{ color: "var(--muted-foreground)" }}>{label}</span>
      <span className="text-[13px]" style={{ color: "var(--foreground)" }}>{value || "—"}</span>
    </div>
  );
}

export function AccountPage() {
  const [me, setMe] = useState(null);
  const [phone, setPhone] = useState("");
  const [smsAlerts, setSmsAlerts] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await getMe();
        if (cancelled) return;
        setMe(data);
        setPhone(formatPhone(data.phone || ""));
        setSmsAlerts(Boolean(data.sms_alerts));
      } catch (err) {
        if (!cancelled) setError(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const phoneInvalid = phone.trim() !== "" && !isValidPhone(phone);
  // Opting in with no number to send to is not a saveable state — it would
  // store a preference that can never do anything.
  const smsWithoutNumber = smsAlerts && phone.trim() === "";
  const dirty = me !== null
    && (phone.trim() !== formatPhone(me.phone || "") || smsAlerts !== Boolean(me.sms_alerts));
  const blocked = !dirty || phoneInvalid || smsWithoutNumber || saving;

  const handleSave = async () => {
    if (blocked) return;
    setError("");
    setSaved(false);
    setSaving(true);
    try {
      const data = await updateMe({ phone: phone.trim(), sms_alerts: smsAlerts });
      // Re-seed from the response, not from what was typed: the server stores
      // the number in one canonical form, and showing that back is what tells
      // the user the save actually landed.
      setMe(data);
      setPhone(formatPhone(data.phone || ""));
      setSmsAlerts(Boolean(data.sms_alerts));
      setSaved(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex items-center px-6 h-14 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
        <h1 className="text-xl font-bold" style={{ color: "var(--foreground)" }}>My Account</h1>
      </div>

      <div className="flex-1 overflow-y-auto p-6">
        <div className="max-w-xl space-y-4">
          {loading ? (
            <div className="flex items-center gap-2 text-sm" style={{ color: "var(--muted-foreground)" }}>
              <Loader2 size={14} className="animate-spin" /> Loading your account…
            </div>
          ) : !me ? (
            <div className="text-[13px] px-3 py-2 rounded-lg"
              style={{ background: "rgba(239,68,68,0.08)", color: "#ef4444" }}>
              {error || "Could not load your account."}
            </div>
          ) : (
            <>
              <section className="rounded-xl p-5" style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
                <div className="flex items-center gap-2 mb-3">
                  <UserIcon size={16} style={{ color: "var(--muted-foreground)" }} />
                  <h2 className="text-[16px] font-semibold" style={{ color: "var(--foreground)" }}>
                    {me.display_name || me.username}
                  </h2>
                </div>
                <ReadOnlyRow label="Username" value={me.username} />
                <ReadOnlyRow label="Email" value={me.email} />
                <div className="flex items-center justify-between pt-2.5">
                  <span className="text-[13px]" style={{ color: "var(--muted-foreground)" }}>Role</span>
                  <span className="text-[13px]" style={{ color: "var(--foreground)" }}>
                    {ROLE_LABEL[me.role] ?? me.role}
                  </span>
                </div>
                <p className="text-[12px] mt-3" style={{ color: "var(--muted-foreground)" }}>
                  An administrator sets these. Ask one to change them for you.
                </p>
              </section>

              <section className="rounded-xl p-5 space-y-3" style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
                <div className="flex items-center gap-2">
                  <Smartphone size={16} style={{ color: "var(--muted-foreground)" }} />
                  <h2 className="text-[16px] font-semibold" style={{ color: "var(--foreground)" }}>Mobile number</h2>
                </div>

                <Field label="Mobile number">
                  <div className="flex gap-2 items-center px-3 py-2.5 rounded-xl"
                    style={{
                      background: "var(--secondary)",
                      border: `1px solid ${phoneInvalid ? "rgba(239,68,68,0.4)" : "var(--border)"}`,
                    }}>
                    <Smartphone size={13} style={{ color: "var(--muted-foreground)", flexShrink: 0 }} />
                    <input
                      value={phone}
                      onChange={(e) => { setPhone(formatPhone(e.target.value)); setSaved(false); }}
                      placeholder="+63 917 123 4567"
                      inputMode="tel"
                      autoComplete="tel"
                      aria-invalid={phoneInvalid}
                      aria-describedby={phoneInvalid ? "account-phone-error" : undefined}
                      className="flex-1 bg-transparent text-sm outline-none"
                      style={{ color: "var(--foreground)" }}
                    />
                  </div>
                  {phoneInvalid && (
                    <p id="account-phone-error" className="flex items-center gap-1.5 text-[13px] mt-1.5" style={{ color: "#ef4444" }}>
                      <AlertTriangle size={11} className="flex-shrink-0" />
                      Enter a mobile number as +63 917 123 4567.
                    </p>
                  )}
                </Field>

                <button
                  type="button"
                  onClick={() => { setSmsAlerts((v) => !v); setSaved(false); }}
                  aria-pressed={smsAlerts}
                  className="w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-left"
                  style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}
                >
                  <MessageSquare size={13} style={{ color: "var(--muted-foreground)", flexShrink: 0 }} />
                  <span className="flex-1">
                    <span className="block text-sm" style={{ color: "var(--foreground)" }}>SMS alerts</span>
                    <span className="block text-[12px]" style={{ color: "var(--muted-foreground)" }}>
                      Text me when an alert comes in
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
                  <div className="text-[13px] px-3 py-2 rounded-lg"
                    style={{ background: "rgba(239,68,68,0.08)", color: "#ef4444" }}>
                    {error}
                  </div>
                )}

                <div className="flex items-center gap-3">
                  <button onClick={handleSave} disabled={blocked}
                    className="py-2 px-4 rounded-xl text-sm font-medium flex items-center justify-center gap-2 disabled:opacity-50"
                    style={{ background: "var(--primary)", color: "#fff" }}>
                    {saving ? <Loader2 size={13} className="animate-spin" /> : null}
                    {saving ? "Saving…" : "Save changes"}
                  </button>
                  {saved && !dirty && (
                    <span className="flex items-center gap-1.5 text-[13px]" style={{ color: "#10b981" }}>
                      <Check size={13} /> Saved
                    </span>
                  )}
                </div>
              </section>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
