import { useEffect, useState } from "react";
import { Smartphone, MessageSquare, AlertTriangle, Loader2, Check, Lock, Mail, User as UserIcon } from "lucide-react";

import { confirmEmailChange, getMe, sendEmailChangeCode, updateMe } from "./api";
import { formatPhone, isValidPhone } from "./phone";

// My Account. A user may change their username, their email, their mobile
// number and whether they want to be texted. What they may DO -- their role --
// stays an admin's to set, and is read-only on the server
// (UserSerializer.read_only_fields), not merely here: an account that could
// grant itself a role would be a privilege escalation, not a preference.
//
// Email is the odd one out and does NOT go through the Save button. It is the
// address a password reset is sent to, so a session being able to repoint it
// freely would turn a borrowed session into permanent ownership of the
// account. Two separate things gate it:
//
//   - the CURRENT PASSWORD, which proves this is the owner and not merely
//     whoever holds the session. This is the gate that stops a takeover.
//   - a CODE sent to the new address, which proves the address is reachable
//     so the account cannot strand itself on a typo. On its own this stops
//     mistakes, not attackers -- whoever is asking owns the inbox it goes to.
//
// The old address is notified afterwards, which is the only warning that
// reaches the real owner if the password itself has been taken.
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

export function AccountPage({ embedded = false }) {
  const [me, setMe] = useState(null);
  const [username, setUsername] = useState("");
  const [phone, setPhone] = useState("");
  const [smsAlerts, setSmsAlerts] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  // The email flow keeps its own state and its own busy/error, because it is
  // a separate two-call exchange rather than part of the form's one save.
  const [emailOpen, setEmailOpen] = useState(false);
  const [newEmail, setNewEmail] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [emailCode, setEmailCode] = useState("");
  const [codeSent, setCodeSent] = useState(false);
  const [emailBusy, setEmailBusy] = useState(false);
  const [emailError, setEmailError] = useState("");
  const [emailNote, setEmailNote] = useState("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await getMe();
        if (cancelled) return;
        setMe(data);
        setUsername(data.username || "");
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
  // Blank is the only username the client rejects outright. Uniqueness is the
  // server's to judge -- it is the only place that can -- and comes back as a
  // field error on save.
  const usernameInvalid = username.trim() === "";
  const dirty = me !== null
    && (phone.trim() !== formatPhone(me.phone || "")
      || smsAlerts !== Boolean(me.sms_alerts)
      || username.trim() !== (me.username || ""));
  const blocked = !dirty || phoneInvalid || smsWithoutNumber || usernameInvalid || saving;

  const handleSave = async () => {
    if (blocked) return;
    setError("");
    setSaved(false);
    setSaving(true);
    try {
      const data = await updateMe({
        username: username.trim(),
        phone: phone.trim(),
        sms_alerts: smsAlerts,
      });
      // Re-seed from the response, not from what was typed: the server stores
      // the number in one canonical form, and showing that back is what tells
      // the user the save actually landed.
      setMe(data);
      setUsername(data.username || "");
      setPhone(formatPhone(data.phone || ""));
      setSmsAlerts(Boolean(data.sms_alerts));
      setSaved(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const resetEmailFlow = () => {
    setEmailOpen(false);
    setCodeSent(false);
    setNewEmail("");
    setCurrentPassword("");
    setEmailCode("");
    setEmailError("");
    setEmailNote("");
  };

  const handleSendCode = async () => {
    setEmailError("");
    setEmailNote("");
    setEmailBusy(true);
    try {
      const res = await sendEmailChangeCode(newEmail.trim().toLowerCase(), currentPassword);
      setCodeSent(true);
      setEmailNote(res?.detail || "Code sent.");
    } catch (err) {
      setEmailError(err.message);
    } finally {
      setEmailBusy(false);
    }
  };

  const handleConfirmEmail = async () => {
    setEmailError("");
    setEmailBusy(true);
    try {
      // Returns the updated account, so the row above re-renders with the new
      // address without a second fetch.
      const data = await confirmEmailChange(newEmail.trim().toLowerCase(), emailCode.trim());
      setMe(data);
      resetEmailFlow();
    } catch (err) {
      setEmailError(err.message);
    } finally {
      setEmailBusy(false);
    }
  };

  const body = (
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
                <Field label="Username">
                  <div className="flex gap-2 items-center px-3 py-2.5 rounded-xl"
                    style={{
                      background: "var(--secondary)",
                      border: `1px solid ${usernameInvalid ? "rgba(239,68,68,0.4)" : "var(--border)"}`,
                    }}>
                    <UserIcon size={13} style={{ color: "var(--muted-foreground)", flexShrink: 0 }} />
                    <input
                      value={username}
                      onChange={(e) => { setUsername(e.target.value); setSaved(false); }}
                      placeholder="username"
                      autoComplete="username"
                      aria-invalid={usernameInvalid}
                      className="flex-1 bg-transparent text-sm outline-none"
                      style={{ color: "var(--foreground)" }}
                    />
                  </div>
                  <p className="text-[12px] mt-1.5" style={{ color: "var(--muted-foreground)" }}>
                    What you sign in with. Saved with the button at the bottom.
                  </p>
                </Field>

                {/* Email deliberately does NOT move with that Save button --
                    see the note at the top of this file. */}
                <div className="flex items-center justify-between gap-2 py-2.5 mt-3"
                  style={{ borderTop: "1px solid var(--border)" }}>
                  <span className="text-[13px] flex-shrink-0" style={{ color: "var(--muted-foreground)" }}>Email</span>
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-[13px] truncate" style={{ color: "var(--foreground)" }}>{me.email || "—"}</span>
                    <button
                      onClick={() => (emailOpen ? resetEmailFlow() : setEmailOpen(true))}
                      className="text-[12px] font-medium px-2 py-1 rounded-md flex-shrink-0"
                      style={{ color: "var(--primary)", background: "var(--secondary)", border: "1px solid var(--border)" }}>
                      {emailOpen ? "Cancel" : "Change"}
                    </button>
                  </div>
                </div>

                {emailOpen && (
                  <div className="rounded-xl p-3 mb-3 space-y-2"
                    style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
                    <div className="flex gap-2 items-center px-3 py-2 rounded-lg"
                      style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
                      <Mail size={13} style={{ color: "var(--muted-foreground)", flexShrink: 0 }} />
                      <input
                        value={newEmail}
                        onChange={(e) => setNewEmail(e.target.value)}
                        placeholder="new.address@example.com"
                        inputMode="email"
                        autoComplete="email"
                        disabled={codeSent}
                        className="flex-1 bg-transparent text-sm outline-none disabled:opacity-60"
                        style={{ color: "var(--foreground)" }}
                      />
                    </div>

                    {!codeSent && (
                      <div className="flex gap-2 items-center px-3 py-2 rounded-lg"
                        style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
                        <Lock size={13} style={{ color: "var(--muted-foreground)", flexShrink: 0 }} />
                        <input
                          value={currentPassword}
                          onChange={(e) => setCurrentPassword(e.target.value)}
                          placeholder="Your current password"
                          type="password"
                          autoComplete="current-password"
                          className="flex-1 bg-transparent text-sm outline-none"
                          style={{ color: "var(--foreground)" }}
                        />
                      </div>
                    )}

                    {codeSent && (
                      <div className="flex gap-2 items-center px-3 py-2 rounded-lg"
                        style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
                        <input
                          value={emailCode}
                          onChange={(e) => setEmailCode(e.target.value)}
                          placeholder="6-digit code"
                          inputMode="numeric"
                          maxLength={6}
                          className="flex-1 bg-transparent text-sm outline-none tracking-[0.3em]"
                          style={{ color: "var(--foreground)" }}
                        />
                      </div>
                    )}

                    {emailNote && (
                      <p className="text-[12px]" style={{ color: "var(--muted-foreground)" }}>{emailNote}</p>
                    )}
                    {emailError && (
                      <p className="flex items-center gap-1.5 text-[13px]" style={{ color: "#ef4444" }}>
                        <AlertTriangle size={11} className="flex-shrink-0" /> {emailError}
                      </p>
                    )}

                    <button
                      onClick={codeSent ? handleConfirmEmail : handleSendCode}
                      disabled={emailBusy || (codeSent
                        ? emailCode.trim() === ""
                        : newEmail.trim() === "" || currentPassword === "")}
                      className="py-1.5 px-3 rounded-lg text-[13px] font-medium flex items-center gap-2 disabled:opacity-50"
                      style={{ background: "var(--primary)", color: "#fff" }}>
                      {emailBusy ? <Loader2 size={12} className="animate-spin" /> : null}
                      {codeSent ? "Confirm new email" : "Send code"}
                    </button>
                  </div>
                )}

                <ReadOnlyRow label="Role" value={ROLE_LABEL[me.role] ?? me.role} />
                <p className="text-[12px] mt-3" style={{ color: "var(--muted-foreground)" }}>
                  An administrator sets your role. Ask one to change it for you.
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
  );

  // Rendered as a Settings section, the page chrome is already there: Settings
  // draws the header and owns the scrolling container, so a second "My
  // Account" title and a nested h-full/overflow pair would fight it.
  if (embedded) return body;

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex items-center px-6 h-14 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
        <h1 className="text-xl font-bold" style={{ color: "var(--foreground)" }}>My Account</h1>
      </div>
      <div className="flex-1 overflow-y-auto p-6">{body}</div>
    </div>
  );
}
