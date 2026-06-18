const API_BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000/api";

const ACCESS_KEY = "lookout_access";
const REFRESH_KEY = "lookout_refresh";
const USER_KEY = "lookout_user";

export function getAccessToken() {
  return localStorage.getItem(ACCESS_KEY);
}

export function getStoredUser() {
  const raw = localStorage.getItem(USER_KEY);
  return raw ? JSON.parse(raw) : null;
}

export function clearAuth() {
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
  localStorage.removeItem(USER_KEY);
}

export async function login(username, password) {
  const response = await fetch(`${API_BASE_URL}/auth/login/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });

  if (!response.ok) {
    throw new Error("Invalid username or password.");
  }

  const data = await response.json();
  const user = {
    username: data.user.username,
    role: data.user.role,
    name: data.user.display_name || data.user.username,
  };

  localStorage.setItem(ACCESS_KEY, data.access);
  localStorage.setItem(REFRESH_KEY, data.refresh);
  localStorage.setItem(USER_KEY, JSON.stringify(user));

  return user;
}

export async function apiFetch(path, options = {}) {
  const token = getAccessToken();
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(`Request to ${path} failed with status ${response.status}: ${body}`);
  }

  if (response.status === 204) return null;
  return response.json();
}

export const getHouseholds = () => apiFetch("/households/");
export const createHousehold = (payload) =>
  apiFetch("/households/", { method: "POST", body: JSON.stringify(payload) });
export const updateHousehold = (id, payload) =>
  apiFetch(`/households/${id}/`, { method: "PATCH", body: JSON.stringify(payload) });

export const createHouseholdMember = (payload) =>
  apiFetch("/household-members/", { method: "POST", body: JSON.stringify(payload) });
export const updateHouseholdMember = (id, payload) =>
  apiFetch(`/household-members/${id}/`, { method: "PATCH", body: JSON.stringify(payload) });

export const getResidents = () => apiFetch("/residents/");
export const createResident = (payload) =>
  apiFetch("/residents/", { method: "POST", body: JSON.stringify(payload) });

export const getSettings = () => apiFetch("/settings/");
export const saveSettings = (payload) =>
  apiFetch("/settings/", { method: "PATCH", body: JSON.stringify(payload) });
