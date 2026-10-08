import {
  UPLOAD_ABORTED,
  abortUploadSession,
  completeUploadSession,
  createUploadSession,
  getUploadSessions,
  uploadChunk,
} from "./api";

// Sends a detection source clip to the server in pieces instead of as one
// request, and can pick up where it left off.
//
// Why: the clips this system is pointed at are routinely 200 MB. As a single
// multipart POST that is one request which has to survive from first byte to
// last — a dropped connection, a tunnel hiccup or a proxy read timeout at 90%
// threw away all 200 MB, and the only honest thing the page could do was start
// again. A page refresh was worse: the upload died and nothing about it
// survived, because a browser cannot re-read a File it no longer holds.
//
// Chunking fixes both. Each piece is its own small request, so a failure costs
// one chunk and is retried in place; several pieces go up at once, so a link
// that cannot saturate on one stream finishes sooner; and the server keeps the
// finished pieces, so "where was I" is answered by asking it rather than by
// trusting anything in the page.
//
// See core/views.py UploadSessionViewSet for the other half.

// How many chunk requests are in flight at once. More than one because a
// single HTTP stream rarely fills the uplink, especially through a tunnel
// where per-request latency dominates; only a few because this is somebody's
// barangay connection and the point is to finish the upload, not to starve
// the 4-second dashboard polling that shares it.
const CONCURRENCY = 3;

// Attempts per chunk before the whole upload gives up. Each retry re-sends a
// few megabytes, not the clip, which is the entire point of chunking.
const CHUNK_ATTEMPTS = 4;
const RETRY_BASE_MS = 600;

// A file's identity as far as the browser can tell. Enough to recognise the
// same clip across a refresh (name, length and mtime all matching an upload in
// progress), and the server pairs it with the declared size before it will let
// anything resume onto existing chunks — so a re-encoded clip that kept its
// name cannot be stitched on top of the old one's pieces.
export function fileFingerprint(file) {
  return `${file.name}|${file.size}|${file.lastModified}`;
}

// Retry only what a retry can actually fix. A network-level failure (status 0)
// or the server being briefly unable (5xx, 429, 408) is worth sending again;
// 400 "wrong chunk length" or 403 will say exactly the same thing next time,
// and retrying it just delays a message the user needs to see.
function isWorthRetrying(err) {
  if (err?.aborted) return false;
  const status = err?.status ?? 0;
  return status === 0 || status === 408 || status === 429 || status >= 500;
}

const sleep = (ms, signal) => new Promise((resolve, reject) => {
  const timer = setTimeout(() => {
    signal?.removeEventListener("abort", onAbort);
    resolve();
  }, ms);
  function onAbort() {
    clearTimeout(timer);
    const err = new Error(UPLOAD_ABORTED);
    err.aborted = true;
    err.status = 0;
    reject(err);
  }
  if (signal?.aborted) return onAbort();
  signal?.addEventListener("abort", onAbort, { once: true });
});

// Every unfinished upload this user has, for the "you were 62% through
// clip.mp4" banner. Returns [] rather than throwing: a banner that cannot
// load must not take the page down with it.
export async function listPendingUploads() {
  try {
    return (await getUploadSessions()) ?? [];
  } catch {
    return [];
  }
}

export async function discardUpload(id) {
  try {
    await abortUploadSession(id);
  } catch {
    /* already gone, or the sweep will get it — nothing useful to tell anyone */
  }
}

/**
 * Uploads `file` in chunks and stages it for a detection run.
 *
 * Resolves with the same payload a one-shot stageDetectionFrame does —
 * { staged_token, source_filename, image, width, height } — so callers need
 * not care which transport got the bytes there.
 *
 * @param {File} file
 * @param {object} opts
 * @param {(p: {sentBytes, totalBytes, pct, phase, resumedBytes}) => void} opts.onProgress
 *        Called as bytes land. `phase` is "uploading" while pieces are going
 *        up and "assembling" while the server stitches and decodes them —
 *        that last step reports nothing of its own, so the caller needs to
 *        know to stop showing a percentage and show a spinner instead.
 * @param {AbortSignal} opts.signal  Cancels in-flight chunks. The finished
 *        pieces are deliberately LEFT on the server, so the same file can be
 *        picked again and carry on; call discardUpload to throw them away.
 */
export async function uploadClipInChunks(file, { onProgress, signal } = {}) {
  const report = (phase, sentBytes, totalBytes, resumedBytes) => {
    onProgress?.({
      phase,
      sentBytes,
      totalBytes,
      resumedBytes,
      pct: totalBytes ? Math.min(100, Math.round((sentBytes / totalBytes) * 100)) : 0,
    });
  };

  const session = await createUploadSession({
    filename: file.name,
    size: file.size,
    fingerprint: fileFingerprint(file),
  });

  const chunkSize = session.chunk_size;
  const totalChunks = session.total_chunks;
  const already = new Set(session.received_indices ?? []);
  const pending = [];
  for (let i = 0; i < totalChunks; i += 1) if (!already.has(i)) pending.push(i);

  // Bytes the server already had before this run started. Reported separately
  // so the page can say "resuming at 62%" rather than silently jumping the bar
  // and leaving the user wondering what it skipped.
  const resumedBytes = session.received_bytes ?? 0;

  // Per-chunk bytes-sent, so the bar can move DURING a chunk and not just in
  // 5 MB steps. A chunk being retried resets its own entry to 0, which is the
  // truth: those bytes are going up again.
  const inFlight = new Map();
  // Three concurrent XHRs fire progress events every few tens of milliseconds.
  // Reporting each one would re-render the page a hundred times a second for a
  // bar that can only show whole percent, so only a changed percentage is
  // passed on.
  let lastPct = -1;
  const emit = () => {
    let sent = resumedBytes;
    for (const n of inFlight.values()) sent += n;
    sent = Math.min(sent, file.size);
    const pct = file.size ? Math.round((sent / file.size) * 100) : 0;
    if (pct === lastPct) return;
    lastPct = pct;
    report("uploading", sent, file.size, resumedBytes);
  };
  emit();

  const next = (() => {
    let cursor = 0;
    return () => (cursor < pending.length ? pending[cursor++] : null);
  })();

  async function sendOne(index) {
    const start = index * chunkSize;
    const blob = file.slice(start, Math.min(start + chunkSize, file.size));
    const length = blob.size;

    for (let attempt = 1; ; attempt += 1) {
      try {
        await uploadChunk(session.id, index, blob, {
          signal,
          onProgress: (pct) => {
            // null means the browser would not give a total for this request;
            // leave the chunk at 0 and let its completion below book the
            // bytes, rather than guessing.
            inFlight.set(index, pct == null ? 0 : Math.round((pct / 100) * length));
            emit();
          },
        });
        inFlight.set(index, length);
        emit();
        return;
      } catch (err) {
        inFlight.set(index, 0);
        emit();
        if (attempt >= CHUNK_ATTEMPTS || !isWorthRetrying(err)) throw err;
        // Backs off a little between attempts so a server that is briefly
        // overloaded is not hit by three chunk workers retrying in lockstep.
        await sleep(RETRY_BASE_MS * attempt, signal);
      }
    }
  }

  async function worker() {
    for (let index = next(); index !== null; index = next()) {
      await sendOne(index);
    }
  }

  await Promise.all(
    Array.from({ length: Math.min(CONCURRENCY, pending.length || 1) }, worker),
  );

  // The bytes are away; what follows is the server stitching the pieces and
  // decoding the first frame, which reports no progress of its own.
  report("assembling", file.size, file.size, resumedBytes);
  const staged = await completeUploadSession(session.id);
  return { ...staged, session_id: session.id, resumed_bytes: resumedBytes };
}
