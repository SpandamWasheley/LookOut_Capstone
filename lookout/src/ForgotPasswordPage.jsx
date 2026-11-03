import { useState } from "react";
import { Mail, Lock, Eye, EyeOff, KeyRound, Loader2, CheckCircle, ArrowLeft, AlertTriangle } from "lucide-react";
import { sendForgotPasswordCode, resetForgotPassword } from "./api";

// Colors come from the theme tokens in index.css (--background/--card/--primary/…),
// the same ones LoginPage reads, so this screen is the system's light theme by
// default and follows the sidebar's toggle instead of staying hardcoded dark.
const INPUT_CLASS =
  "w-full rounded-xl text-sm outline-none transition focus:border-[var(--primary)] focus:ring-2 focus:ring-[var(--ring)]";
const INPUT_STYLE = {
  background: "var(--secondary)",
  border: "1px solid var(--border)",
  color: "var(--foreground)",
};
const PRIMARY_STYLE = {
  background: "var(--primary)",
  color: "var(--primary-foreground)",
};
const PRIMARY_BUTTON_CLASS =
  "w-full flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-medium transition hover:brightness-110 disabled:opacity-60";
const LINK_BUTTON_CLASS =
  "w-full flex items-center justify-center gap-1.5 text-xs font-medium transition hover:underline";
const LABEL_CLASS = "block text-xs font-medium";

export default function ForgotPasswordPage({ onBackToLogin }) {
  const [step, setStep] = useState(1); // 1: email, 2: code + new password, 3: done
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [sending, setSending] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [error, setError] = useState("");

  const handleSendCode = async (event) => {
    event.preventDefault();
    if (!email.trim()) return;
    setError("");
    setSending(true);
    try {
      await sendForgotPasswordCode(email.trim());
      setStep(2);
    } catch (err) {
      setError(err.message);
    } finally {
      setSending(false);
    }
  };

  const handleReset = async (event) => {
    event.preventDefault();
    setError("");

    if (!code.trim()) {
      setError("Enter the code sent to your email.");
      return;
    }
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    if (password !== confirm) {
      setError("Passwords do not match.");
      return;
    }

    setResetting(true);
    try {
      await resetForgotPassword(email.trim(), code.trim(), password);
      setStep(3);
    } catch (err) {
      setError(err.message);
    } finally {
      setResetting(false);
    }
  };

  // Same red as the login screen's error strip — a status color, so it stays
  // literal rather than following the theme.
  const errorBox = error ? (
    <div
      className="flex items-center gap-2 px-3 py-2.5 rounded-xl text-sm"
      style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)", color: "#ef4444" }}
    >
      <AlertTriangle size={13} className="flex-shrink-0" />
      {error}
    </div>
  ) : null;

  return (
    <div
      className="min-h-screen flex items-center justify-center px-4"
      style={{ background: "var(--background)" }}
    >
      <div
        className="w-full max-w-md rounded-2xl p-8"
        style={{
          background: "var(--card)",
          border: "1px solid var(--border)",
          boxShadow: "0 12px 40px rgba(11, 30, 44, 0.10)",
        }}
      >
        <div className="mb-8 text-center">
          <div
            className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl"
            style={PRIMARY_STYLE}
          >
            <KeyRound size={24} />
          </div>
          <h1 className="text-2xl font-semibold" style={{ color: "var(--foreground)" }}>
            {step === 3 ? "Password reset" : "Forgot your password?"}
          </h1>
          <p className="mt-2 text-sm" style={{ color: "var(--muted-foreground)" }}>
            {step === 1 && "Enter the email you registered with — we'll send a reset code to it."}
            {step === 2 && `Enter the code sent to ${email}, then choose a new password.`}
            {step === 3 && "You can now sign in with your new password."}
          </p>
        </div>

        {step === 1 && (
          <form onSubmit={handleSendCode} className="space-y-5">
            <div className="space-y-1.5">
              <label className={LABEL_CLASS} style={{ color: "var(--muted-foreground)" }}>Email</label>
              <div className="relative">
                <Mail
                  size={14}
                  className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2"
                  style={{ color: "var(--muted-foreground)" }}
                />
                <input
                  type="email"
                  value={email}
                  onChange={(e) => { setEmail(e.target.value); setError(""); }}
                  placeholder="you@example.com"
                  autoComplete="email"
                  className={`${INPUT_CLASS} pl-9 pr-3 py-2.5`}
                  style={INPUT_STYLE}
                />
              </div>
            </div>

            {errorBox}

            <button type="submit" disabled={sending} className={PRIMARY_BUTTON_CLASS} style={PRIMARY_STYLE}>
              {sending ? <Loader2 size={15} className="animate-spin" /> : null}
              {sending ? "Sending…" : "Send reset code"}
            </button>

            <button
              type="button"
              onClick={onBackToLogin}
              className={LINK_BUTTON_CLASS}
              style={{ color: "var(--primary)" }}
            >
              <ArrowLeft size={14} /> Back to login
            </button>
          </form>
        )}

        {step === 2 && (
          <form onSubmit={handleReset} className="space-y-5">
            <div className="space-y-1.5">
              <label className={LABEL_CLASS} style={{ color: "var(--muted-foreground)" }}>Reset code</label>
              <input
                value={code}
                onChange={(e) => { setCode(e.target.value); setError(""); }}
                placeholder="6-digit code"
                maxLength={6}
                className={`${INPUT_CLASS} px-4 py-2.5 tracking-widest`}
                style={{ ...INPUT_STYLE, fontFamily: "'DM Mono', monospace" }}
              />
            </div>

            <div className="space-y-1.5">
              <label className={LABEL_CLASS} style={{ color: "var(--muted-foreground)" }}>New password</label>
              <div className="relative">
                <Lock
                  size={14}
                  className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2"
                  style={{ color: "var(--muted-foreground)" }}
                />
                <input
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(e) => { setPassword(e.target.value); setError(""); }}
                  placeholder="At least 8 characters"
                  autoComplete="new-password"
                  className={`${INPUT_CLASS} pl-9 pr-10 py-2.5`}
                  style={INPUT_STYLE}
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 transition-colors"
                  style={{ color: "var(--muted-foreground)" }}
                >
                  {showPassword ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </div>
            </div>

            <div className="space-y-1.5">
              <label className={LABEL_CLASS} style={{ color: "var(--muted-foreground)" }}>Confirm new password</label>
              <div className="relative">
                <Lock
                  size={14}
                  className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2"
                  style={{ color: "var(--muted-foreground)" }}
                />
                <input
                  type={showPassword ? "text" : "password"}
                  value={confirm}
                  onChange={(e) => { setConfirm(e.target.value); setError(""); }}
                  placeholder="Re-enter new password"
                  autoComplete="new-password"
                  className={`${INPUT_CLASS} pl-9 pr-3 py-2.5`}
                  style={INPUT_STYLE}
                />
              </div>
            </div>

            {errorBox}

            <button type="submit" disabled={resetting} className={PRIMARY_BUTTON_CLASS} style={PRIMARY_STYLE}>
              {resetting ? <Loader2 size={15} className="animate-spin" /> : null}
              {resetting ? "Resetting…" : "Reset password"}
            </button>

            <button
              type="button"
              onClick={() => setStep(1)}
              className={LINK_BUTTON_CLASS}
              style={{ color: "var(--primary)" }}
            >
              <ArrowLeft size={14} /> Use a different email
            </button>
          </form>
        )}

        {step === 3 && (
          <div className="space-y-5 text-center">
            {/* --chart-4 is the theme's green (deep in light, bright in dark), so the
                success mark reads on both grounds without a hardcoded emerald. */}
            <div
              className="mx-auto flex h-12 w-12 items-center justify-center rounded-full"
              style={{ background: "color-mix(in srgb, var(--chart-4) 14%, transparent)" }}
            >
              <CheckCircle size={24} style={{ color: "var(--chart-4)" }} />
            </div>
            <button onClick={onBackToLogin} className={PRIMARY_BUTTON_CLASS} style={PRIMARY_STYLE}>
              Back to login
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
