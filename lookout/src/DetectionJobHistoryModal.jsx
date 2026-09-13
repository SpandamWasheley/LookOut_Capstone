import { X, History } from "lucide-react";

const STATUS_COLOR = {
  running: "#f59e0b",
  done: "#10b981",
  failed: "#ef4444",
  cancelled: "#6b7280",
};

function formatDateTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

// Full history of admin-launched test-detection jobs (see UploadDetectionModal
// and DetectionJobsPanel, which only ever shows a running/recently-finished
// subset). Jobs themselves are never deleted — this just reads the same list
// the floating panel does, unfiltered.
export function DetectionJobHistoryModal({ jobs, onClose, onCancel }) {
  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.72)", backdropFilter: "blur(6px)" }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        className="w-full max-w-3xl rounded-2xl overflow-hidden shadow-2xl flex flex-col"
        style={{ background: "var(--card)", border: "1px solid var(--border)", maxHeight: "85vh" }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 flex-shrink-0"
          style={{ borderBottom: "1px solid var(--border)" }}>
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ background: "rgba(245,158,11,0.12)" }}>
              <History size={14} style={{ color: "#f59e0b" }} />
            </div>
            <div>
              <div className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>Detection Job History</div>
              <div className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>
                {jobs.length} job{jobs.length === 1 ? "" : "s"}
              </div>
            </div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg"
            style={{ color: "var(--muted-foreground)", background: "var(--secondary)" }}>
            <X size={14} />
          </button>
        </div>

        {/* Table */}
        <div className="flex-1 overflow-y-auto">
          {jobs.length === 0 ? (
            <div className="px-5 py-10 text-center text-[12px]" style={{ color: "var(--muted-foreground)" }}>
              No detection jobs yet.
            </div>
          ) : (
            <table className="w-full text-[12px]" style={{ borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--border)" }}>
                  {["File", "Type", "Status", "Started", "Finished", ""].map((h, i) => (
                    <th key={i} className="text-left px-5 py-2 font-semibold uppercase tracking-wide text-[10px]"
                      style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
                  <tr key={job.id} style={{ borderBottom: "1px solid var(--border)" }}>
                    <td className="px-5 py-2.5 max-w-[220px] truncate" style={{ color: "var(--foreground)" }} title={job.sourceFilename}>
                      {job.sourceFilename}
                    </td>
                    <td className="px-5 py-2.5 capitalize" style={{ color: "var(--foreground)" }}>
                      {job.violationType}
                    </td>
                    <td className="px-5 py-2.5">
                      <span className="capitalize font-medium" style={{ color: STATUS_COLOR[job.status] ?? "var(--foreground)" }}>
                        {job.status}
                      </span>
                    </td>
                    <td className="px-5 py-2.5" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
                      {formatDateTime(job.startedAt)}
                    </td>
                    <td className="px-5 py-2.5" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
                      {formatDateTime(job.finishedAt)}
                    </td>
                    <td className="px-5 py-2.5 text-right">
                      {job.status === "running" && onCancel && (
                        <button
                          onClick={() => onCancel(job.id)}
                          className="text-[11px] font-medium px-2.5 py-1 rounded-lg"
                          style={{ color: "#ef4444", background: "rgba(239,68,68,0.1)" }}
                        >
                          Cancel
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
