import { useState } from "react";
import { Mail, Lock, Eye, EyeOff, KeyRound, Loader2, CheckCircle, ArrowLeft } from "lucide-react";
import { sendForgotPasswordCode, resetForgotPassword } from "./api";

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

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-950 px-4">
      <div className="w-full max-w-md rounded-[32px] bg-slate-900 border border-slate-800 p-8 shadow-2xl">
        <div className="mb-8 text-center">
          <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-3xl bg-amber-400 text-slate-950">
            <KeyRound size={24} />
          </div>
          <h1 className="text-2xl font-semibold text-white">
            {step === 3 ? "Password reset" : "Forgot your password?"}
          </h1>
          <p className="mt-2 text-sm text-slate-400">
            {step === 1 && "Enter the email you registered with — we'll send a reset code to it."}
            {step === 2 && `Enter the code sent to ${email}, then choose a new password.`}
            {step === 3 && "You can now sign in with your new password."}
          </p>
        </div>

        {step === 1 && (
          <form onSubmit={handleSendCode} className="space-y-5">
            <div className="space-y-2">
              <label className="block text-sm font-medium text-slate-300">Email</label>
              <div className="relative">
                <Mail size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input
                  type="email"
                  value={email}
                  onChange={(e) => { setEmail(e.target.value); setError(""); }}
                  placeholder="you@example.com"
                  autoComplete="email"
                  className="w-full rounded-3xl border border-slate-700 bg-slate-950 px-11 py-3 text-sm text-white outline-none transition focus:border-amber-400"
                />
              </div>
            </div>

            {error && <div className="rounded-3xl bg-red-500/10 px-4 py-3 text-sm text-red-300">{error}</div>}

            <button
              type="submit"
              disabled={sending}
              className="w-full flex items-center justify-center gap-2 rounded-3xl bg-amber-500 px-4 py-3 text-sm font-semibold text-slate-950 transition hover:bg-amber-400 disabled:opacity-60"
            >
              {sending ? <Loader2 size={15} className="animate-spin" /> : null}
              {sending ? "Sending…" : "Send reset code"}
            </button>

            <button
              type="button"
              onClick={onBackToLogin}
              className="w-full flex items-center justify-center gap-1.5 text-sm text-slate-400 hover:text-slate-200"
            >
              <ArrowLeft size={14} /> Back to login
            </button>
          </form>
        )}

        {step === 2 && (
          <form onSubmit={handleReset} className="space-y-5">
            <div className="space-y-2">
              <label className="block text-sm font-medium text-slate-300">Reset code</label>
              <input
                value={code}
                onChange={(e) => { setCode(e.target.value); setError(""); }}
                placeholder="6-digit code"
                maxLength={6}
                className="w-full rounded-3xl border border-slate-700 bg-slate-950 px-5 py-3 text-sm tracking-widest text-white outline-none transition focus:border-amber-400"
                style={{ fontFamily: "'DM Mono', monospace" }}
              />
            </div>

            <div className="space-y-2">
              <label className="block text-sm font-medium text-slate-300">New password</label>
              <div className="relative">
                <Lock size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(e) => { setPassword(e.target.value); setError(""); }}
                  placeholder="At least 8 characters"
                  autoComplete="new-password"
                  className="w-full rounded-3xl border border-slate-700 bg-slate-950 px-11 py-3 pr-14 text-sm text-white outline-none transition focus:border-amber-400"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400"
                >
                  {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </div>

            <div className="space-y-2">
              <label className="block text-sm font-medium text-slate-300">Confirm new password</label>
              <div className="relative">
                <Lock size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input
                  type={showPassword ? "text" : "password"}
                  value={confirm}
                  onChange={(e) => { setConfirm(e.target.value); setError(""); }}
                  placeholder="Re-enter new password"
                  autoComplete="new-password"
                  className="w-full rounded-3xl border border-slate-700 bg-slate-950 px-11 py-3 text-sm text-white outline-none transition focus:border-amber-400"
                />
              </div>
            </div>

            {error && <div className="rounded-3xl bg-red-500/10 px-4 py-3 text-sm text-red-300">{error}</div>}

            <button
              type="submit"
              disabled={resetting}
              className="w-full flex items-center justify-center gap-2 rounded-3xl bg-amber-500 px-4 py-3 text-sm font-semibold text-slate-950 transition hover:bg-amber-400 disabled:opacity-60"
            >
              {resetting ? <Loader2 size={15} className="animate-spin" /> : null}
              {resetting ? "Resetting…" : "Reset password"}
            </button>

            <button
              type="button"
              onClick={() => setStep(1)}
              className="w-full flex items-center justify-center gap-1.5 text-sm text-slate-400 hover:text-slate-200"
            >
              <ArrowLeft size={14} /> Use a different email
            </button>
          </form>
        )}

        {step === 3 && (
          <div className="space-y-5 text-center">
            <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-emerald-500/10">
              <CheckCircle size={24} className="text-emerald-400" />
            </div>
            <button
              onClick={onBackToLogin}
              className="w-full rounded-3xl bg-amber-500 px-4 py-3 text-sm font-semibold text-slate-950 transition hover:bg-amber-400"
            >
              Back to login
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
