const API_BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000/api";

// Access token lives only in memory for the life of the tab — never written to
// localStorage/sessionStorage, so it can't be read from DevTools storage panels
// or exfiltrated by a stored-XSS payload scanning storage. This matches the
// app's existing design (see App.jsx) where every fresh page load requires
// logging in again, so there's nothing to persist across reloads anyway.
let accessToken = null;

export function getAccessToken() {
  return accessToken;
}

export function clearAuth() {
  accessToken = null;
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

  if (data.user.role === "officer") {
    throw new Error("Officer accounts can only sign in through the mobile app.");
  }

  const user = {
    username: data.user.username,
    role: data.user.role,
    name: data.user.display_name || data.user.username,
    mustChangePassword: data.user.must_change_password,
  };

  accessToken = data.access;

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
    const text = await response.text();
    let parsed = null;
    try { parsed = JSON.parse(text); } catch { /* not JSON */ }
    const message = parsed
      ? Object.values(parsed).flat().join(" ") || text
      : text || `Request failed with status ${response.status}`;
    const err = new Error(message);
    err.data = parsed;
    err.status = response.status;
    throw err;
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
export const deleteHouseholdMember = (id) =>
  apiFetch(`/household-members/${id}/`, { method: "DELETE" });

export const getResidents = () => apiFetch("/residents/");
export const createResident = (payload) =>
  apiFetch("/residents/", { method: "POST", body: JSON.stringify(payload) });

export const getSettings = () => apiFetch("/settings/");
export const saveSettings = (payload) =>
  apiFetch("/settings/", { method: "PATCH", body: JSON.stringify(payload) });

export const getOfficers = () => apiFetch("/officers/");
export const updateOfficer = (id, payload) =>
  apiFetch(`/officers/${id}/`, { method: "PATCH", body: JSON.stringify(payload) });
export const deleteOfficer = (id) =>
  apiFetch(`/officers/${id}/`, { method: "DELETE" });
export const sendOfficerCode = (email) =>
  apiFetch("/officers/send-code/", { method: "POST", body: JSON.stringify({ email }) });
export const verifyOfficerCode = (email, code) =>
  apiFetch("/officers/verify-code/", { method: "POST", body: JSON.stringify({ email, code }) });
export const registerPersonnel = (payload) =>
  apiFetch("/personnel/register/", { method: "POST", body: JSON.stringify(payload) });

export const getDispatchers = () => apiFetch("/dispatchers/");
export const updateDispatcher = (id, payload) =>
  apiFetch(`/dispatchers/${id}/`, { method: "PATCH", body: JSON.stringify(payload) });
export const deleteDispatcher = (id) =>
  apiFetch(`/dispatchers/${id}/`, { method: "DELETE" });

export const getAlerts = () => apiFetch("/alerts/");
export const updateAlert = (id, payload) =>
  apiFetch(`/alerts/${id}/`, { method: "PATCH", body: JSON.stringify(payload) });

export const getCameras = () => apiFetch("/cameras/");

export const changePassword = (newPassword) =>
  apiFetch("/auth/change-password/", { method: "POST", body: JSON.stringify({ new_password: newPassword }) });

export const sendForgotPasswordCode = (email) =>
  apiFetch("/auth/forgot-password/send-code/", { method: "POST", body: JSON.stringify({ email }) });
export const resetForgotPassword = (email, code, newPassword) =>
  apiFetch("/auth/forgot-password/reset/", {
    method: "POST",
    body: JSON.stringify({ email, code, new_password: newPassword }),
  });
export const sendSms = (payload) =>
  apiFetch("/sms/send/", { method: "POST", body: JSON.stringify(payload) });
