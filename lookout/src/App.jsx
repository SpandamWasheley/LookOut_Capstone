import { useState } from "react";
import { BrowserRouter, Routes, Route, Navigate, useNavigate } from "react-router-dom";
import Login from "./LoginPage.jsx";
import AdminDashboard from "./admin_dashboard.jsx";
import ChangePasswordPage from "./ChangePasswordPage.jsx";
import ForgotPasswordPage from "./ForgotPasswordPage.jsx";
import { clearAuth } from "./api.js";

// Every fresh page load (or hard refresh) requires logging in again —
// any session left over from a previous load is discarded immediately.
clearAuth();

function AppRoutes() {
  const [user, setUser] = useState(null);
  const navigate = useNavigate();

  const handleLogin = (loggedInUser) => {
    setUser(loggedInUser);
    navigate("/dashboard");
  };

  const handleLogout = () => {
    clearAuth();
    setUser(null);
    navigate("/");
  };

  const handlePasswordChanged = () => {
    setUser((prev) => ({ ...prev, mustChangePassword: false }));
  };

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