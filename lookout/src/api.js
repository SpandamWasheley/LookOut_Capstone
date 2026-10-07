const API_BASE_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000/api";

// ngrok's free tier answers browser-looking requests with an HTML "you are
// about to visit..." interstitial (ERR_NGROK_6024) instead of proxying them.
// That page carries no Access-Control-Allow-Origin, so every call through a
// tunnel fails as a CORS error even though Django's own CORS config is right.
// This header opts out of the interstitial; harmless on any other host.
// It is a custom header, so it must also be in the API's CORS_ALLOW_HEADERS.
const TUNNEL_HEADERS = { "ngrok-skip-browser-warning": "true" };

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
    headers: { "Content-Type": "application/json", ...TUNNEL_HEADERS },
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

  // Layout used to be remembered in localStorage; drop the stale value.
  try { localStorage.removeItem("lookout.cameraLayout"); } catch { /* storage unavailable */ }

  return user;
}

export async function apiFetch(path, options = {}) {
  const token = getAccessToken();
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...TUNNEL_HEADERS,
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
    headers: { ...TUNNEL_HEADERS, ...(token ? { Authorization: `Bearer ${token}` } : {}) },
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

export const getSettings = () => apiFetch("/settings/");
export const resetSpecDefaults = (violation) =>
  apiFetch("/settings/reset/", { method: "POST", body: JSON.stringify({ violation }) });
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

// params: {include_monitoring: 1} adds the quiet Monitoring watchlist to the list;
// {level: "monitoring"} returns only that watchlist. The default list is Possible / Likely.
// Both split on the event's PEAK status, not its current one, so an event that
// reached Possible and then faded stays in the default list (still badged with
// whatever it is now) instead of dropping back onto the watchlist.
export const getAlerts = (params) =>
  apiFetch("/alerts/" + (params ? `?${new URLSearchParams(params)}` : ""));
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

// Live processing view (what the detector is tracking, plus its latest clean frame). `since` is
// the sequence number the page already has, so an unchanged frame is not sent again.
const sinceQuery = (since) => (since != null ? `?since=${since}` : "");
export const getJobState = (id, since) => apiFetch(`/detection-jobs/${id}/state/${sinceQuery(since)}`);

// Live monitoring of the camera (admin only): start / stop / status / processing view.
export const getMonitor = () => apiFetch("/monitor/");
export const startMonitor = () => apiFetch("/monitor/start/", { method: "POST", body: "{}" });
export const stopMonitor = () => apiFetch("/monitor/stop/", { method: "POST", body: "{}" });
export const getMonitorState = (since) => apiFetch(`/monitor/state/${sinceQuery(since)}`);
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
  { stagedToken, sourceFilename, violationType, recordedAt, edges, edgesWidth, edgesHeight,
    obstructionPct, obstructionMinutes, trimStart, trimEnd },
) => {
  const formData = new FormData();
  formData.append("staged_token", stagedToken);
  formData.append("source_filename", sourceFilename);
  formData.append("violation_type", violationType);
  // When the clip was recorded (local time). Drives the holdup time block and the drinking
  // evening band; the detector is started with --clock. Optional.
  if (recordedAt) formData.append("recorded_at", recordedAt);
  // Seconds into the clip to run over. 0/0 (or omitted) means the whole thing;
  // the detector seeks rather than the server cutting a second copy of the file.
  if (trimStart) formData.append("trim_start", trimStart);
  if (trimEnd) formData.append("trim_end", trimEnd);
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
    headers: { ...TUNNEL_HEADERS, ...(token ? { Authorization: `Bearer ${token}` } : {}) },
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
