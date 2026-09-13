import AsyncStorage from "@react-native-async-storage/async-storage";
import Constants from "expo-constants";
import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";

// SecureStore backs onto the OS Keychain (iOS) / Keystore (Android), so tokens
// are encrypted at rest instead of sitting in plaintext AsyncStorage. It has no
// native implementation on web, so fall back to AsyncStorage there — the Expo
// web preview is a dev convenience, not this app's real deployment target.
const authStorage = {
  getItem: (key: string) =>
    Platform.OS === "web" ? AsyncStorage.getItem(key) : SecureStore.getItemAsync(key),
  setItem: (key: string, value: string) =>
    Platform.OS === "web" ? AsyncStorage.setItem(key, value) : SecureStore.setItemAsync(key, value),
  removeItem: (key: string) =>
    Platform.OS === "web" ? AsyncStorage.removeItem(key) : SecureStore.deleteItemAsync(key),
};

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
  officer_id: number | null;
}

export async function getAccessToken() {
  return authStorage.getItem(ACCESS_KEY);
}

export async function getStoredUser(): Promise<ApiUser | null> {
  const raw = await authStorage.getItem(USER_KEY);
  return raw ? JSON.parse(raw) : null;
}

export async function clearAuth() {
  await Promise.all([ACCESS_KEY, REFRESH_KEY, USER_KEY].map((key) => authStorage.removeItem(key)));
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

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn;
}

export async function apiFetch<T = unknown>(path: string, options: RequestInit = {}): Promise<T> {
  const token = await getAccessToken();
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "ngrok-skip-browser-warning": "true",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });

  if (!response.ok) {
    if (response.status === 401 && token) {
      await clearAuth();
      onUnauthorized?.();
      throw new ApiError("Your session has expired. Please log in again.", 401, null);
    }
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
    officerId: number | null;
  };
}

export async function login(username: string, password: string): Promise<LoginResult["user"]> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/auth/login/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
  } catch {
    throw new Error("Couldn't reach the server. Check your connection and try again.");
  }

  if (!response.ok) {
    if (response.status === 429) {
      throw new Error("Too many attempts. Please wait a moment and try again.");
    }
    if (response.status >= 500) {
      throw new Error("The server ran into a problem. Please try again shortly.");
    }
    // 400/401/403 all stay generic — surfacing more detail here would let the
    // message reveal whether an account exists.
    throw new Error("Invalid username or password.");
  }

  const data = await response.json();

  if (data.user.role !== "officer" && data.user.role !== "both") {
    throw new Error("Only officer accounts can sign in to this app.");
  }

  const user = {
    username: data.user.username,
    role: data.user.role,
    name: data.user.display_name || data.user.username,
    mustChangePassword: data.user.must_change_password,
    officerId: data.user.officer_id ?? null,
  };

  await authStorage.setItem(ACCESS_KEY, data.access);
  await authStorage.setItem(REFRESH_KEY, data.refresh);
  // Persist the raw API shape (matches ApiUser / what getStoredUser() reads
  // back on app restart) — NOT the transformed `user` shape above, which has
  // different field names (name vs display_name, mustChangePassword vs
  // must_change_password) and would silently break on next app load.
  await authStorage.setItem(USER_KEY, JSON.stringify(data.user));

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
  video_url: string;
  raw_video_url: string;
  officers_assigned: number[];
  officers_assigned_names: string[];
  suspect: string;
  notes: string;
}

export const getAlerts = () => apiFetch<{ results: ApiAlert[] } | ApiAlert[]>("/alerts/");
export const updateAlert = (id: number, payload: Partial<ApiAlert>) =>
  apiFetch<ApiAlert>(`/alerts/${id}/`, { method: "PATCH", body: JSON.stringify(payload) });
// Atomically adds the current officer to officers_assigned (and promotes
// status to "dispatched" if still "active") — safe under concurrent accepts,
// unlike a client-computed read-modify-write PATCH of the full list.
export const acceptAlert = (id: number) =>
  apiFetch<ApiAlert>(`/alerts/${id}/accept/`, { method: "POST" });

export interface ApiViolationType {
  id: number;
  code: string;
  label: string;
  color: string;
  icon: string;
}

export const getViolationTypes = () =>
  apiFetch<{ results: ApiViolationType[] } | ApiViolationType[]>("/violation-types/");

export interface ApiCitation {
  id: number;
  alert: number | null;
  violator: number | null;
  violator_name: string;
  first_name_entered: string;
  middle_name_entered: string;
  last_name_entered: string;
  suffix_entered: string;
  officer: number;
  officer_name: string;
  barangay_of_violation: string;
  violator_barangay: string;
  violations: number[];
  violation_labels: string[];
  matched_person: number | null;
  match_confidence: number | null;
  notes: string;
  created_by: number | null;
  created_at: string;
}

export const getCitations = (params: Record<string, string> = {}) => {
  const qs = new URLSearchParams(params).toString();
  return apiFetch<{ results: ApiCitation[] } | ApiCitation[]>(`/citations/${qs ? `?${qs}` : ""}`);
};

export interface CreateCitationPayload {
  alert: number;
  violator?: number;
  first_name_entered: string;
  middle_name_entered?: string;
  last_name_entered: string;
  suffix_entered?: string;
  officer: number;
  barangay_of_violation?: string;
  violator_barangay: string;
  violations: number[];
  notes?: string;
  // Stage 1 backend addition — mobile always sends false here and resolves
  // the alert as its own explicit step (see resolveAssignment) once the
  // officer says they're done with the scene, since one alert can produce
  // several citations. client_uuid isn't sent yet: that's Stage 4 (offline
  // queue), which is what actually needs a client-generated retry key.
  resolve_alert?: boolean;
  client_uuid?: string;
}

export const createCitation = (payload: CreateCitationPayload) =>
  apiFetch<ApiCitation>("/citations/", { method: "POST", body: JSON.stringify(payload) });
