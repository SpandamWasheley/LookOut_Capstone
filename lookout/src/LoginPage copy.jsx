import { useState } from "react";
import { Eye, Lock, User, AlertCircle } from "lucide-react";
import { mockUsers } from "./data/mockData";

export default function Login({ onLogin }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  const handleSubmit = () => {
    const match = mockUsers.find(
      (u) => u.username === username.trim() && u.password === password
    );
    if (!match) {
      setError("Invalid username or password");
      return;
    }
    setError("");
    onLogin({ username: match.username, role: match.role, name: match.name });
  };

  return (
    <div className="flex items-center justify-center min-h-screen p-4" style={{ background: "var(--background)" }}>
      <div className="w-full max-w-sm rounded-2xl p-6 shadow-2xl"
        style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
        <div className="flex flex-col items-center mb-6">
          <div className="w-12 h-12 rounded-xl flex items-center justify-center mb-3" style={{ background: "#f59e0b" }}>
            <Eye size={22} color="#0c0f16" strokeWidth={2.5} />
          </div>
          <div className="text-lg font-semibold text-white tracking-tight">LookOut</div>
          <div className="text-[11px] mt-0.5" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
            Barangay Tetuan · Sign in
          </div>
        </div>

        {error && (
          <div className="flex items-center gap-2 rounded-lg px-3 py-2 mb-4 text-[12px]"
            style={{ background: "rgba(239,68,68,0.1)", color: "#ef4444", border: "1px solid rgba(239,68,68,0.2)" }}>
            <AlertCircle size={13} /> {error}
          </div>
        )}

        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>Username</label>
            <div className="relative">
              <User size={14} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "var(--muted-foreground)" }} />
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
                placeholder="admin"
                className="w-full pl-9 pr-3 py-2.5 rounded-xl text-sm outline-none"
                style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "#f1f5f9" }}
              />
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>Password</label>
            <div className="relative">
              <Lock size={14} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "var(--muted-foreground)" }} />
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
                placeholder="••••••••"
                className="w-full pl-9 pr-3 py-2.5 rounded-xl text-sm outline-none"
                style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "#f1f5f9" }}
              />
            </div>
          </div>

          <button
            onClick={handleSubmit}
            className="w-full py-2.5 rounded-xl text-sm font-semibold transition-all mt-2"
            style={{ background: "#f59e0b", color: "#0c0f16" }}
          >
            Sign in
          </button>
        </div>

        <div className="mt-5 pt-4 text-[10px] leading-relaxed" style={{ borderTop: "1px solid var(--border)", color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
          admin / admin123 · dispatcher / dispatch123 · officer / officer123
        </div>
      </div>
    </div>
  );
}