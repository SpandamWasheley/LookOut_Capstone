import { useState } from "react";
import { BrowserRouter, Routes, Route, Navigate, useNavigate } from "react-router-dom";
import Login from "./LoginPage.jsx";
import AdminDashboard from "./admin_dashboard.jsx";
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

  return (
    <Routes>
      <Route
        path="/"
        element={
          user
            ? <Navigate to="/dashboard" replace />
            : <Login onLogin={handleLogin} />
        }
      />
      <Route
        path="/dashboard"
        element={
          user
            ? <AdminDashboard user={user} onLogout={handleLogout} />
            : <Navigate to="/" replace />
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