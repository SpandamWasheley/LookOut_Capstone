import { useState } from "react";
import { X, Loader2, Save, CheckCircle2, XCircle, AlertTriangle } from "lucide-react";
import { updateCamera } from "./api";

// Sets a camera's stream_url (RTSP URL, credentials included) — the field the
// dashboard has no other UI for today (it's admin-DB-only otherwise). The
// current value can never be shown back (stream_url is write_only on the
// serializer specifically so credentials never round-trip to the browser —
// see CameraSerializer), so this only ever *sets a new value*: a blank
// submit is a no-op rather than clearing a working URL, and "configured"
// status comes from the camera's own already-exposed isLive flag, never from
// reading the URL back.
export function CameraStreamModal({ camera, onClose, onSaved }) {
  const [value, setValue] = useState("");
  // Unlike stream_url, the address is NOT write-only -- it carries no
  // credentials -- so it round-trips and can be shown and corrected rather than
  // only ever set blind.
  const [address, setAddress] = useState(camera.address ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const handleSave = async () => {
    const url = value.trim();
    const addr = address.trim();
    const addressChanged = addr !== (camera.address ?? "").trim();
    // Either field alone is a valid save. Requiring the URL would mean an
    // address could never be corrected without also re-typing credentials.
    if ((!url && !addressChanged) || saving) return;
    setSaving(true);
    setError("");
    try {
      const payload = {};
      if (url) payload.stream_url = url;       // blank leaves the existing one
      if (addressChanged) payload.address = addr;
      const updated = await updateCamera(camera.dbId, payload);
      onSaved?.(updated);
      onClose();
    } catch (err) {
      setError(err.message || "Could not save.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.72)", backdropFilter: "blur(6px)" }}
      onClick={(e) => { if (e.target === e.currentTarget && !saving) onClose(); }}
    >
      <div
        className="w-full max-w-md rounded-2xl overflow-hidden shadow-2xl flex flex-col"
        style={{ background: "var(--card)", border: "1px solid var(--border)" }}
      >
        <div className="flex items-center justify-between px-5 py-4"
          style={{ borderBottom: "1px solid var(--border)" }}>
          <div>
            <div className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>
              Stream URL
            </div>
            <div className="text-[13px]" style={{ color: "var(--muted-foreground)" }}>
              {camera.name} ({camera.id})
            </div>
          </div>
          {!saving && (
            <button onClick={onClose} className="p-1.5 rounded-lg"
              style={{ color: "var(--muted-foreground)", background: "var(--secondary)" }}>
              <X size={14} />
            </button>
          )}
        </div>

        <div className="px-5 py-4 space-y-3.5">
          <div className="flex items-center gap-2 text-[14px]"
            style={{ color: camera.isLive ? "#22c55e" : "var(--muted-foreground)" }}>
            {camera.isLive ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
            {camera.isLive ? "Stream configured" : "Not configured"}
          </div>

          <div>
            <label className="text-[13px] font-medium block mb-1.5" style={{ color: "var(--muted-foreground)" }}>
              RTSP URL {camera.isLive && "(enter a new one to replace it)"}
            </label>
            <input
              type="text"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="rtsp://user:pass@192.168.1.64:554/Streaming/Channels/101"
              disabled={saving}
              className="w-full px-3 py-2.5 rounded-xl text-[14px]"
              style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }}
            />
            <div className="text-[12px] mt-1.5" style={{ color: "var(--muted-foreground)" }}>
              Never shown back once saved — credentials stay server-side only. Leaving this blank changes nothing.
            </div>
          </div>

        {/* Where this camera is. An alert inherits it, so whatever is typed
            here becomes the location shown on every violation this camera
            detects -- which is the only location the system can honestly
            claim: it knows which camera saw the event, not where the person
            stood in the frame. */}
        <div className="px-5 pb-4">
          <label className="block text-[13px] mb-1.5" style={{ color: "var(--muted-foreground)" }}>
            Address / location
          </label>
          <input
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            placeholder="e.g. Purok 3, Talon-Talon Rd, near the covered court"
            className="w-full px-3 py-2 rounded-lg text-[14px] outline-none"
            style={{ background: "var(--secondary)", border: "1px solid var(--border)",
                     color: "var(--foreground)" }}
          />
          <div className="mt-1.5 text-[12px]" style={{ color: "var(--muted-foreground)" }}>
            Shown as the location on every violation this camera detects.
          </div>
        </div>

          {error && (
            <div className="flex items-start gap-2 text-[14px] px-3 py-2.5 rounded-xl"
              style={{ background: "rgba(239,68,68,0.08)", border: "1px solid rgba(239,68,68,0.2)", color: "#ef4444" }}>
              <AlertTriangle size={13} className="flex-shrink-0 mt-0.5" />
              <span>{error}</span>
            </div>
          )}
        </div>

        <div className="px-5 py-4 flex items-center justify-end gap-2"
          style={{ borderTop: "1px solid var(--border)" }}>
          {!saving && (
            <button onClick={onClose} className="px-4 py-2 rounded-xl text-sm font-medium"
              style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
              Cancel
            </button>
          )}
          <button
            disabled={!value.trim() || saving}
            onClick={handleSave}
            className="flex items-center gap-2 px-5 py-2 rounded-xl text-sm font-medium"
            style={{
              background: "#f59e0b",
              color: "#0c0f16",
              cursor: (!value.trim() || saving) ? "not-allowed" : "pointer",
              opacity: (!value.trim() || saving) ? 0.5 : 1,
            }}
          >
            {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
