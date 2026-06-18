import { useState } from "react";
import { Eye, EyeOff, Lock, User } from "lucide-react";
import { useNavigate } from "react-router-dom";

const credentials = [
  { username: "admin", password: "admin123", label: "Admin" },
  { username: "dispatcher", password: "dispatch123", label: "Dispatcher" },
];

function Login() {
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");

  const handleLogin = (event) => {
    event.preventDefault();
    const match = credentials.find(
      (item) => item.username === username.trim().toLowerCase() && item.password === password,
    );
    if (match) {
      navigate("/admin-dashboard");
    } else {
      setError("Invalid username or password.");
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-950 px-4">
      <div className="w-full max-w-md rounded-3xl border border-slate-800 bg-slate-900 p-8 shadow-2xl">
        <div className="mb-8 text-center">
          <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-amber-400 text-slate-950">
            <Eye size={24} />
          </div>
          <h1 className="text-3xl font-semibold text-white">LookOut Login</h1>
          <p className="mt-2 text-sm text-slate-400">Sign in to access the admin dashboard.</p>
        </div>

        <form onSubmit={handleLogin} className="space-y-5">
          <div className="space-y-2">
            <label className="block text-sm font-medium text-slate-300">Username</label>
            <div className="relative">
              <User size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <input
                value={username}
                onChange={(e) => { setUsername(e.target.value); setError(""); }}
                placeholder="Enter your username"
                autoComplete="username"
                className="w-full rounded-3xl border border-slate-700 bg-slate-950 px-11 py-3 text-sm text-white outline-none transition focus:border-amber-400"
              />
            </div>
          </div>

          <div className="space-y-2">
            <label className="block text-sm font-medium text-slate-300">Password</label>
            <div className="relative">
              <Lock size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <input
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={(e) => { setPassword(e.target.value); setError(""); }}
                placeholder="Enter your password"
                autoComplete="current-password"
                className="w-full rounded-3xl border border-slate-700 bg-slate-950 px-11 py-3 pr-14 text-sm text-white outline-none transition focus:border-amber-400"
              />
              <button
                type="button"
                onClick={() => setShowPassword((value) => !value)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400"
              >
                {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </div>

          {error && <div className="rounded-3xl bg-red-500/10 px-4 py-3 text-sm text-red-300">{error}</div>}

          <button
            type="submit"
            className="w-full rounded-3xl bg-amber-500 px-4 py-3 text-sm font-semibold text-slate-950 transition hover:bg-amber-400"
          >
            Sign In
          </button>
        </form>

        <div className="mt-8 rounded-3xl border border-slate-800 bg-slate-950/40 p-4 text-sm text-slate-400">
          <div className="mb-2 font-medium text-slate-200">Demo credentials</div>
          <div className="space-y-2">
            {credentials.map((item) => (
              <div key={item.username} className="flex items-center justify-between rounded-2xl bg-slate-900 px-3 py-2 text-slate-300">
                <div>{item.label}</div>
                <div className="text-slate-400">{item.username} / {item.password}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

export default Login;
