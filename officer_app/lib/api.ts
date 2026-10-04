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
  // A wrong address makes fetch hang for minutes, which looks like the app doing nothing.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 12000);
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/auth/login/`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "ngrok-skip-browser-warning": "1" },
      body: JSON.stringify({ username, password }),
      signal: controller.signal,
    });
  } catch {
    throw new Error(`Can't reach the server at ${API_BASE_URL.replace(/\/api\/?$/, "")}. `
      + "Check that you are on the same Wi-Fi as the computer running LookOut and that the server is on.");
  } finally {
    clearTimeout(timer);
  }

  if (!response.ok) {
    if (response.status === 429) {
      throw new Error("Too many attempts. Please wait a moment and try again.");
    }
    if (response.status >= 500) {
      throw new Error("The server ran into a problem. Please try again shortly.");
    }
    if (response.status === 400 || response.status === 404) {
      // Not a login answer: a wrong address, or Django refusing the host name.
      throw new Error(`The server did not accept the request (error ${response.status}). `
        + "The app may be pointing at the wrong address.");
    }
    // 401 / 403: wrong username or password. Kept generic on purpose so the message
    // does not reveal whether an account exists.
    throw new Error("Wrong username or password.");
  }

  let data: any;
  try {
    data = await response.json();
  } catch {
    // A 200 that is not JSON: a tunnel or proxy page answered instead of LookOut.
    throw new Error("The address answered, but not from the LookOut server. Check the server address.");
  }

  if (data.user.role !== "officer" && data.user.role !== "both") {
    throw new Error("This account is not an officer account, so it can't sign in to this app. Use the web dashboard instead.");
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

export interface ApiAiContext {
  state: "pending" | "done" | "unavailable";
  badge: { code: "supports" | "ordinary" | "unclear" | "unavailable"; text: string };
  suggestion: { text: string; suggested: string | null; changed: boolean; direction: "up" | "down" | "none" };
  observations: string;
  checklist: { field: string; label: string; value: boolean | string }[];
  confidence: string | null;
}

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

  // --- scoring (core/vision/scoring.py) --------------------------------------
  // `confidence` above is the VIOLATION SCORE, not the detector's certainty --
  // the two were conflated until the scoring layer separated them. What the
  // officer is shown is `level_label`; the number stays for the record.
  level: "" | "none" | "monitoring" | "warning" | "violation";
  level_label: string;
  // What the object detector itself was sure of. Null on alerts filed before
  // the two were separated, so every read of it is guarded.
  object_confidence: number | null;
  // The full cue vector. `checklist` inside it is the plain-language evidence
  // the alert card shows -- built server-side so this app and the web
  // dashboard cannot drift apart on wording.
  cues: {
    checklist?: { found: string[]; adjusted_by?: string[]; tag?: string };
    [key: string]: unknown;
  } | null;
  // Who closed the alert (dismissed / resolved) and when, and whether a citation was filed.
  reviewed_by_name: string;
  reviewed_at: string | null;
  citation_issued: boolean;
  // true when the alert was worth attending, false for a false alarm. Recorded silently from
  // Assign / Dismiss for evaluating the system; there is no UI for it.
  reviewed_valid: boolean | null;

  // --- AI checker (core/vision/ai_checker.py) --------------------------------
  // Display only: computed by the server on every read from the stored AI reply and the
  // alert's CURRENT status. It never changes the official status.
  ai_context: ApiAiContext | null;
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

// Correcting a citation already filed. Only the fields an officer can get
// wrong on the form: the names, the home barangay, which violations, and the
// notes. Everything else is deliberately absent — `alert` and `officer` are
// what the citation IS, and `violator` is resolved server-side FROM the names
// (see CitationViewSet.perform_update), so sending it from here would pin the
// citation to a person record that no longer matches what it says.
//
// The server allows this only to the officer who filed it and only while the
// alert is still open; a 403 means one of those is no longer true.
export interface UpdateCitationPayload {
  first_name_entered: string;
  middle_name_entered?: string;
  last_name_entered: string;
  suffix_entered?: string;
  violator_barangay: string;
  violations: number[];
  notes?: string;
}

export const updateCitation = (id: number, payload: UpdateCitationPayload) =>
  apiFetch<ApiCitation>(`/citations/${id}/`, { method: "PATCH", body: JSON.stringify(payload) });
