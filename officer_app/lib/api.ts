import AsyncStorage from "@react-native-async-storage/async-storage";
import Constants from "expo-constants";
import { Platform } from "react-native";

function resolveApiBaseUrl(): string {
  const fromEnv = process.env.EXPO_PUBLIC_API_URL;
  if (fromEnv) return fromEnv;

  // On native, "localhost" means the phone itself, not the dev machine —
  // derive the dev machine's LAN IP from the Expo dev server's host URI instead.
  const hostUri = Constants.expoConfig?.hostUri ?? Constants.expoGoConfig?.debuggerHost;
  if (hostUri && Platform.OS !== "web") {
    const host = hostUri.split(":")[0];
    return `http://${host}:8000/api`;
  }

  return "http://localhost:8000/api";
}

export const API_BASE_URL = resolveApiBaseUrl();

const ACCESS_KEY = "lookout_officer_access";
const REFRESH_KEY = "lookout_officer_refresh";
const USER_KEY = "lookout_officer_user";

export interface ApiUser {
  id: number;
  username: string;
  display_name: string;
  role: string;
  email: string;
  must_change_password: boolean;
}

export async function getAccessToken() {
  return AsyncStorage.getItem(ACCESS_KEY);
}

export async function getStoredUser(): Promise<ApiUser | null> {
  const raw = await AsyncStorage.getItem(USER_KEY);
  return raw ? JSON.parse(raw) : null;
}

export async function clearAuth() {
  await AsyncStorage.multiRemove([ACCESS_KEY, REFRESH_KEY, USER_KEY]);
}

export class ApiError extends Error {
  data: unknown;
  status: number;
  constructor(message: string, status: number, data: unknown) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

export async function apiFetch<T = unknown>(path: string, options: RequestInit = {}): Promise<T> {
  const token = await getAccessToken();
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });

  if (!response.ok) {
    const text = await response.text();
    let parsed: Record<string, unknown> | null = null;
    try {
      parsed = JSON.parse(text);
    } catch {
      // not JSON
    }
    const message = parsed
      ? Object.values(parsed).flat().join(" ") || text
      : text || `Request failed with status ${response.status}`;
    throw new ApiError(message, response.status, parsed);
  }

  if (response.status === 204) return null as T;
  return response.json();
}

export interface LoginResult {
  user: {
    username: string;
    role: string;
    name: string;
    mustChangePassword: boolean;
  };
}

export async function login(username: string, password: string): Promise<LoginResult["user"]> {
  const response = await fetch(`${API_BASE_URL}/auth/login/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });

  if (!response.ok) {
    throw new Error("Invalid username or password.");
  }

  const data = await response.json();

  if (data.user.role !== "officer") {
    throw new Error("Only officer accounts can sign in to this app.");
  }

  const user = {
    username: data.user.username,
    role: data.user.role,
    name: data.user.display_name || data.user.username,
    mustChangePassword: data.user.must_change_password,
  };

  await AsyncStorage.setItem(ACCESS_KEY, data.access);
  await AsyncStorage.setItem(REFRESH_KEY, data.refresh);
  await AsyncStorage.setItem(USER_KEY, JSON.stringify(user));

  return user;
}

export const changePassword = (newPassword: string) =>
  apiFetch("/auth/change-password/", { method: "POST", body: JSON.stringify({ new_password: newPassword }) });

export const sendForgotPasswordCode = (email: string) =>
  apiFetch("/auth/forgot-password/send-code/", { method: "POST", body: JSON.stringify({ email }) });

export const resetForgotPassword = (email: string, code: string, newPassword: string) =>
  apiFetch("/auth/forgot-password/reset/", {
    method: "POST",
    body: JSON.stringify({ email, code, new_password: newPassword }),
  });

export interface ApiOfficer {
  id: number;
  code: string;
  name: string;
  badge: string;
  status: "on-duty" | "off-duty" | "responding";
  location: string;
  phone: string;
  shift: string;
  joined_date: string | null;
  email: string;
  username: string;
}

export const getOfficers = () => apiFetch<{ results: ApiOfficer[] } | ApiOfficer[]>("/officers/");
export const updateOfficer = (id: number, payload: Partial<ApiOfficer>) =>
  apiFetch<ApiOfficer>(`/officers/${id}/`, { method: "PATCH", body: JSON.stringify(payload) });

export interface ApiAlert {
  id: number;
  code: string;
  type: string;
  status: "active" | "dispatched" | "acknowledged" | "resolved";
  camera: string | null;
  camera_zone: string;
  timestamp: string;
  confidence: number;
  description: string;
  image_url: string;
  officer_assigned: string | null;
  suspect: string;
  notes: string;
}

export const getAlerts = () => apiFetch<{ results: ApiAlert[] } | ApiAlert[]>("/alerts/");
export const updateAlert = (id: number, payload: Partial<ApiAlert>) =>
  apiFetch<ApiAlert>(`/alerts/${id}/`, { method: "PATCH", body: JSON.stringify(payload) });

export interface ApiViolationType {
  id: number;
  code: string;
  label: string;
  color: string;
  icon: string;
}

export const getViolationTypes = () =>
  apiFetch<{ results: ApiViolationType[] } | ApiViolationType[]>("/violation-types/");
