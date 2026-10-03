import React, { createContext, useContext, useEffect, useState } from "react";

import * as api from "@/lib/api";

export interface Officer {
  username: string;
  role: string;
  name: string;
  mustChangePassword: boolean;
  officerId: number | null;
}

interface AuthContextType {
  officer: Officer | null;
  isLoading: boolean;
  login: (username: string, password: string) => Promise<{ ok: boolean; error?: string }>;
  logout: () => void;
  completePasswordChange: () => void;
}

const AuthContext = createContext<AuthContextType | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [officer, setOfficer] = useState<Officer | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const toOfficer = (u: api.ApiUser): Officer => ({
      username: u.username,
      role: u.role,
      name: u.display_name || u.username,
      mustChangePassword: u.must_change_password,
      officerId: u.officer_id ?? null,
    });
    api.setUnauthorizedHandler(() => setOfficer(null));
    api.getStoredUser().then(async (stored) => {
      if (stored) {
        setOfficer(toOfficer(stored));
        // The saved copy can be stale (an old session on this phone, or a password changed since).
        // Ask the server who this really is; if it can't be reached, keep what is saved.
        try {
          const fresh = await api.apiFetch<api.ApiUser>("/auth/me/");
          setOfficer(toOfficer(fresh));
        } catch { /* offline: keep the saved session; a 401 already signed out */ }
      }
      setIsLoading(false);
    });
    return () => api.setUnauthorizedHandler(null);
  }, []);

  const login = async (username: string, password: string) => {
    try {
      const user = await api.login(username.trim().toLowerCase(), password);
      setOfficer(user);
      return { ok: true };
    } catch (err) {
      return { ok: false, error: err instanceof Error ? err.message : "Login failed." };
    }
  };

  const logout = async () => {
    await api.clearAuth();
    setOfficer(null);
  };

  const completePasswordChange = () => {
    setOfficer((prev) => (prev ? { ...prev, mustChangePassword: false } : prev));
  };

  return (
    <AuthContext.Provider value={{ officer, isLoading, login, logout, completePasswordChange }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
