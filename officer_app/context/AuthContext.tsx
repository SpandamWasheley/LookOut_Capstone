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
    api.getStoredUser().then((stored) => {
      if (stored) {
        setOfficer({
          username: stored.username,
          role: stored.role,
          name: stored.display_name || stored.username,
          mustChangePassword: stored.must_change_password,
          officerId: stored.officer_id ?? null,
        });
      }
      setIsLoading(false);
    });
    api.setUnauthorizedHandler(() => setOfficer(null));
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
