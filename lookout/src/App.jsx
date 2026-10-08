import { useEffect, useState } from "react";
import { BrowserRouter, Routes, Route, Navigate, useNavigate } from "react-router-dom";
import Login from "./LoginPage.jsx";
import AdminDashboard from "./admin_dashboard.jsx";
import ChangePasswordPage from "./ChangePasswordPage.jsx";
import ForgotPasswordPage from "./ForgotPasswordPage.jsx";
import { logout, restoreSession } from "./api.js";

function AppRoutes() {
  const [user, setUser] = useState(null);
  // A page load no longer means "logged out": the refresh token in
  // sessionStorage is redeemed for a new session first. Until that answers we
  // know neither way, so routing has to wait — otherwise every reload bounces
  // a signed-in user through the login screen for a moment.
  const [booting, setBooting] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const restored = await restoreSession();
      if (cancelled) return;
      setUser(restored);
      setBooting(false);
    })();
    return () => { cancelled = true; };
  }, []);

  const handleLogin = (loggedInUser) => {
    setUser(loggedInUser);
    navigate("/dashboard");
  };

  const handleLogout = () => {
    // Not awaited: logout() clears the local tokens synchronously before its
    // network call, so the UI can leave immediately while the server-side
    // blacklisting completes in the background. Nothing here depends on the
    // result, and making the user watch a spinner to sign out would be worse.
    logout();
    setUser(null);
    navigate("/");
  };

  const handlePasswordChanged = () => {
    setUser((prev) => ({ ...prev, mustChangePassword: false }));
  };

  // Deliberately blank rather than a spinner: restoring is one or two fast
  // calls, and a flash of chrome replaced a moment later reads worse than a
  // single beat of nothing.
  if (booting) return <div style={{ minHeight: "100vh", background: "var(--background)" }} />;

  return (
    <Routes>
      <Route
        path="/"
        element={
          user
            ? <Navigate to="/dashboard" replace />
            : <Login onLogin={handleLogin} onForgotPassword={() => navigate("/forgot-password")} />
        }
      />
      <Route
        path="/forgot-password"
        element={<ForgotPasswordPage onBackToLogin={() => navigate("/")} />}
      />
      <Route
        path="/dashboard"
        element={
          !user
            ? <Navigate to="/" replace />
            : user.mustChangePassword
              ? <ChangePasswordPage onDone={handlePasswordChanged} />
              : <AdminDashboard user={user} onLogout={handleLogout} />
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AppRoutes />
    </BrowserRouter>
  );
}