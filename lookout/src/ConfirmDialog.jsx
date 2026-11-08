import { useEffect, useRef } from "react";
import { AlertTriangle } from "lucide-react";

// A confirmation the app draws itself, in place of window.confirm.
//
// The browser's own dialog was wrong here for three reasons, and all three
// matter in a control room: it is styled by the OS, so it arrives as a grey
// box with the page's URL in it on top of a dark dashboard; it blocks the
// whole tab while it is up, which stops the 4-second alert poll dead until
// somebody answers it; and its two buttons are "OK" and "Cancel", which say
// nothing about what is being replaced.
//
// Deliberately NOT a general-purpose modal: it takes a question, an
// explanation and the word that goes on the confirm button, and nothing else.
//
// z-[90] sits above the editors (z-[70]) that raise it, below the alert toast
// (z-[100]) — a violation arriving must never be hidden behind a question.
export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel = "Confirm",
  // null for a one-button NOTICE — something that already happened and only
  // needs reading. See NoticeDialog.
  cancelLabel = "Cancel",
  // Amber by default, matching Save in the editors. Pass a red for anything
  // that destroys data.
  tone = "#f59e0b",
  onConfirm,
  onCancel,
}) {
  const confirmRef = useRef(null);

  // Escape cancels, and the confirm button takes focus so Enter answers the
  // question that is actually on screen rather than re-triggering whatever
  // button was focused behind it.
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); onCancel?.(); } };
    document.addEventListener("keydown", onKey, true);
    confirmRef.current?.focus();
    return () => document.removeEventListener("keydown", onKey, true);
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[90] flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.6)", backdropFilter: "blur(3px)" }}
      onClick={(e) => { if (e.target === e.currentTarget) onCancel?.(); }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-[420px] rounded-2xl overflow-hidden shadow-2xl"
        style={{ background: "var(--card)", border: "1px solid var(--border)" }}
      >
        <div className="flex items-start gap-3 px-5 pt-5 pb-4">
          <span className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
            style={{ background: `${tone}1f` }}>
            <AlertTriangle size={15} style={{ color: tone }} />
          </span>
          <div className="min-w-0">
            <div className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>
              {title}
            </div>
            {body && (
              <div className="text-[13px] mt-1 leading-relaxed" style={{ color: "var(--muted-foreground)" }}>
                {body}
              </div>
            )}
          </div>
        </div>

        <div className="px-5 py-4 flex items-center justify-end gap-2"
          style={{ borderTop: "1px solid var(--border)" }}>
          {cancelLabel && (
            <button onClick={onCancel} className="px-4 py-2 rounded-xl text-sm font-medium"
              style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
              {cancelLabel}
            </button>
          )}
          <button ref={confirmRef} onClick={onConfirm}
            className="px-4 py-2 rounded-xl text-sm font-medium"
            style={{ background: tone, color: "#0c0f16" }}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * The one-button version: a failure the operator has to be told about, in
 * place of window.alert.
 *
 * Same objections as window.confirm — OS styling on top of a dark dashboard,
 * the page's URL in the title, and it freezes the tab, which holds up the
 * 4-second alert poll until somebody clicks OK. A dialog that stops violations
 * arriving is the wrong way to report that a toggle did not save.
 */
export function NoticeDialog({ open, title, body, closeLabel = "OK", tone = "#ef4444", onClose }) {
  return (
    <ConfirmDialog
      open={open}
      title={title}
      body={body}
      confirmLabel={closeLabel}
      cancelLabel={null}
      tone={tone}
      onConfirm={onClose}
      onCancel={onClose}
    />
  );
}
