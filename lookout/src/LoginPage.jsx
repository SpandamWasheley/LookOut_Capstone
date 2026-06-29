import { useState } from "react";
import { Eye, EyeOff, Lock, User, AlertTriangle } from "lucide-react";
import { login } from "./api";

export default function LoginPage({ onLogin, onForgotPassword }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    if (!username.trim() || !password) {
      setError("Please enter your username and password.");
      return;
    }
    setLoading(true);
    try {
      const user = await login(username.trim().toLowerCase(), password);
      onLogin(user);
    } catch (err) {
      setError(err.message || "Invalid username or password.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      className="flex h-screen w-screen overflow-hidden"
      style={{ background: "var(--background)" }}
    >
      {/* Left — branding panel */}
      <div
        className="hidden lg:flex flex-col items-center justify-center p-12 w-[420px] flex-shrink-0"
        style={{ background: "var(--sidebar)", borderRight: "1px solid var(--sidebar-border)" }}
      >
        <div className="flex flex-col items-center gap-6 text-center">
          <div
            className="w-20 h-20 rounded-2xl flex items-center justify-center"
            style={{
              background: "var(--sidebar-primary)",
              boxShadow: "0 8px 32px rgba(234,243,242,0.15)",
            }}
          >
            <Eye size={40} color="var(--sidebar-primary-foreground)" strokeWidth={2} />
          </div>

          <div>
            <div
              className="text-4xl font-bold tracking-tight leading-none"
              style={{ color: "var(--sidebar-primary)", letterSpacing: "-0.03em" }}
            >
              LookOut
            </div>
            <div className="text-sm mt-2" style={{ color: "var(--sidebar-foreground)" }}>
              Barangay Tetuan, Zamboanga City
            </div>
          </div>

          <div
            className="text-[10px] tracking-widest"
            style={{ color: "var(--sidebar-foreground)", fontFamily: "'DM Mono', monospace", opacity: 0.7 }}
          >
            v2.4.1 · YOLOv8 + ArcFace
          </div>
        </div>
      </div>

      {/* Right — login form */}
      <div className="flex-1 flex items-center justify-center p-6">
        <div className="w-full max-w-sm">
          {/* Mobile logo */}
          <div className="flex items-center gap-3 mb-10 lg:hidden">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ background: "#85B7D6" }}>
              <Eye size={16} color="#0c0f16" strokeWidth={2.5} />
            </div>
            <div>
              <div className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>LookOut</div>
              <div className="text-[11px]" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>Barangay Tetuan</div>
            </div>
          </div>

          <div className="mb-8">
            <h1 className="text-xl font-semibold" style={{ color: "var(--foreground)" }}>Sign in</h1>
            <p className="text-sm mt-1" style={{ color: "var(--muted-foreground)" }}>
              Authorized personnel only
            </p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Username */}
            <div className="space-y-1.5">
              <label className="text-xs font-medium" style={{ color: "var(--muted-foreground)" }}>
                Username
              </label>
              <div className="relative">
                <User
                  size={14}
                  className="absolute left-3 top-1/2 -translate-y-1/2"
                  style={{ color: "var(--muted-foreground)" }}
                />
                <input
                  type="text"
                  value={username}
                  onChange={(e) => { setUsername(e.target.value); setError(""); }}
                  placeholder="Enter your username"
                  autoComplete="username"
                  className="w-full pl-9 pr-3 py-2.5 rounded-xl text-sm outline-none transition-all"
                  style={{
                    background: "var(--secondary)",
                    border: `1px solid ${error ? "rgba(239,68,68,0.4)" : "var(--border)"}`,
                    color: "var(--foreground)",
                  }}
                />
              </div>
            </div>

            {/* Password */}
            <div className="space-y-1.5">
              <label className="text-xs font-medium" style={{ color: "var(--muted-foreground)" }}>
                Password
              </label>
              <div className="relative">
                <Lock
                  size={14}
                  className="absolute left-3 top-1/2 -translate-y-1/2"
                  style={{ color: "var(--muted-foreground)" }}
                />
                <input
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(e) => { setPassword(e.target.value); setError(""); }}
                  placeholder="Enter your password"
                  autoComplete="current-password"
                  className="w-full pl-9 pr-10 py-2.5 rounded-xl text-sm outline-none transition-all"
                  style={{
                    background: "var(--secondary)",
                    border: `1px solid ${error ? "rgba(239,68,68,0.4)" : "var(--border)"}`,
                    color: "var(--foreground)",
                  }}
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 transition-colors"
                  style={{ color: "var(--muted-foreground)" }}
                >
                  {showPassword ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </div>
              {onForgotPassword && (
                <div className="text-right">
                  <button
                    type="button"
                    onClick={onForgotPassword}
                    className="text-xs font-medium transition-colors"
                    style={{ color: "var(--primary)" }}
                  >
                    Forgot password?
                  </button>
                </div>
              )}
            </div>

            {/* Error */}
            {error && (
              <div
                className="flex items-center gap-2 px-3 py-2.5 rounded-xl text-sm"
                style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)", color: "#ef4444" }}
              >
                <AlertTriangle size={13} className="flex-shrink-0" />
                {error}
              </div>
            )}

            {/* Submit */}
            <button
              type="submit"
              disabled={loading}
              className="w-full py-2.5 rounded-xl text-sm font-medium transition-all duration-150 mt-2 outline-none"
              style={{
                background: loading ? "var(--muted)" : "var(--primary)",
                color: "var(--primary-foreground)",
                cursor: loading ? "not-allowed" : "pointer",
              }}
            >
              {loading ? (
                <span className="flex items-center justify-center gap-2">
                  <span className="w-3.5 h-3.5 rounded-full border-2 border-current border-t-transparent animate-spin" />
                  Authenticating…
                </span>
              ) : "Sign in"}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
