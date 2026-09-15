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
    officerId: data.user.officer_id ?? null,
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

// Like apiFetch, but for multipart/form-data bodies (file uploads) — the
// Content-Type (with its boundary) must come from the browser, not be set
// manually, so this skips the JSON header apiFetch always adds.
async function apiUpload(path, formData) {
  const token = getAccessToken();
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: formData,
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

export const getPersons = () => apiFetch("/persons/");
export const createPerson = (payload) =>
  apiFetch("/persons/", { method: "POST", body: JSON.stringify(payload) });
export const deletePerson = (id) =>
  apiFetch(`/persons/${id}/`, { method: "DELETE" });
// front/right/left File objects under those field names in `formData` —
// matches core/views.py PersonViewSet.enroll_face's request.FILES.get(angle).
export const enrollFace = (id, formData) =>
  apiUpload(`/persons/${id}/enroll-face/`, formData);

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

export const getViolationTypes = () => apiFetch("/violation-types/");
export const getBarangays = () => apiFetch("/barangays/");
// Creating a citation against an alert resolves that alert server-side, in
// the same transaction — see core/views.py CitationViewSet.perform_create.
export const createCitation = (payload) =>
  apiFetch("/citations/", { method: "POST", body: JSON.stringify(payload) });

export const getCitations = (params = {}) => {
  const qs = new URLSearchParams(params).toString();
  return apiFetch(`/citations/${qs ? `?${qs}` : ""}`);
};

export const getViolators = () => apiFetch("/violators/");
export const getViolator = (id) => apiFetch(`/violators/${id}/`);
export const searchViolators = (q) => apiFetch(`/violators/search/?q=${encodeURIComponent(q)}`);
export const mergeViolators = (winnerId, loserId) =>
  apiFetch(`/violators/${winnerId}/merge/`, { method: "POST", body: JSON.stringify({ loser_id: loserId }) });

// Continuous CCTV recording, tied to dashboard login/logout: start when the
// operator signs in, stop when they sign out. Fire-and-forget from the UI.
export const startRecording = () => apiFetch("/recording/start/", { method: "POST" });
export const stopRecording = () => apiFetch("/recording/stop/", { method: "POST" });
export const getRecordingStatus = () => apiFetch("/recording/status/");

export const getCameras = () => apiFetch("/cameras/");
export const updateCamera = (id, payload) =>
  apiFetch(`/cameras/${id}/`, { method: "PATCH", body: JSON.stringify(payload) });

// Grabs the first frame of an uploaded clip (as a data URL) plus its native
// pixel size, for drawing obstruction edges when the camera has no live feed
// to snapshot from. See CameraViewSet.edge_frame.
export const uploadCameraEdgeFrame = (id, file) => {
  const formData = new FormData();
  formData.append("file", file);
  return apiUpload(`/cameras/${id}/edge-frame/`, formData);
};

// Admin test harness: run an existing detector against an uploaded clip
// instead of the terminal. Launches a subprocess server-side; this call
// returns as soon as the job row is created, not when detection finishes.
export const getDetectionJobs = () => apiFetch("/detection-jobs/");
export const uploadDetectionJob = (file, violationType) => {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("violation_type", violationType);
  return apiUpload("/detection-jobs/", formData);
};

// Stages a clip server-side and returns its first frame (native resolution)
// plus a staged_token — for the "upload -> draw edges -> start" flow, where
// the clip should only cross the wire once even though drawing happens
// before the job itself is created. See DetectionJobViewSet.frame.
export const stageDetectionFrame = (file) => {
  const formData = new FormData();
  formData.append("file", file);
  return apiUpload("/detection-jobs/frame/", formData);
};

// Starts a job from an already-staged clip (see stageDetectionFrame) instead
// of re-uploading it. `edges` (parking only) is the same {left,right} spec
// EdgeEditorModal writes via updateCamera, plus the frame size it was drawn
// against so the backend can rescale it correctly at analysis time.
export const startStagedDetectionJob = (
  { stagedToken, sourceFilename, violationType, edges, edgesWidth, edgesHeight, obstructionPct, obstructionMinutes },
) => {
  const formData = new FormData();
  formData.append("staged_token", stagedToken);
  formData.append("source_filename", sourceFilename);
  formData.append("violation_type", violationType);
  if (edges) {
    formData.append("edges", JSON.stringify(edges));
    formData.append("edges_width", edgesWidth);
    formData.append("edges_height", edgesHeight);
    formData.append("obstruction_pct", obstructionPct);
    formData.append("obstruction_minutes", obstructionMinutes);
  }
  return apiUpload("/detection-jobs/", formData);
};
// Starts a job against a live camera's stream_url instead of a file — no
// staging, no edges payload: for parking, draw/save edges on the camera
// itself first via EdgeEditorModal (updateCamera), which already writes
// camera.edges directly and is picked up the same way any other run does.
// The resulting job never reaches EOF on its own; see cancelDetectionJob.
export const startLiveDetectionJob = ({ violationType, cameraId }) => {
  const formData = new FormData();
  formData.append("violation_type", violationType);
  formData.append("camera_id", cameraId);
  return apiUpload("/detection-jobs/", formData);
};

// Kills the job's subprocess server-side and marks it cancelled. Only valid
// while the job is still running — see DetectionJobViewSet.cancel. Doubles as
// "Stop" for a live job (isLive), which has no natural end of its own.
export const cancelDetectionJob = (id) =>
  apiFetch(`/detection-jobs/${id}/cancel/`, { method: "POST" });

// Fetches one JPEG frame from a live camera's snapshot proxy as an object URL.
// The access token lives only in memory, so an <img src> can't carry it — we
// fetch the bytes with the Authorization header and wrap them in a blob URL.
// The caller MUST URL.revokeObjectURL the returned value when replacing it, or
// object URLs leak for the life of the tab.
export async function getCameraSnapshotUrl(dbId, signal) {
  const token = getAccessToken();
  const response = await fetch(`${API_BASE_URL}/cameras/${dbId}/snapshot/`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    signal,
  });
  if (!response.ok) throw new Error(`snapshot ${response.status}`);
  const blob = await response.blob();
  return URL.createObjectURL(blob);
}

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
