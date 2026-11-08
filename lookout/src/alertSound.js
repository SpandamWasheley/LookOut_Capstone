import { useEffect, useState } from "react";

// The sound a new violation makes.
//
// Why there is one at all: the dashboard announces arrivals with a toast in
// the bottom-right corner, which only works for somebody looking at the
// screen. A barangay desk is not a screen somebody watches — it is a desk
// with a radio, a logbook and people at it — so an alert that only ever
// appeared silently was an alert nobody saw until the next time they glanced
// over. A sound is the one channel that reaches the room rather than the
// monitor.
//
// SYNTHESISED, not an mp3. A tone built here cannot 404 after a deploy, adds
// nothing to the bundle, is not blocked as "media" by any content policy, and
// can be tuned by changing two numbers rather than sourcing another file. The
// sound wanted is a short two-note chime, not a siren: the operator is in the
// room and the screen already says what happened, so this only has to turn a
// head.
//
// Per-browser preference, like "Show testing tools" — this computer's speaker
// is this computer's business, and a dispatcher muting the machine at a shared
// desk must not silence anybody else's.
const KEY = "lookout:alert-sound";
const EVENT = "lookout:alert-sound-changed";

// Default ON. A control-room feature nobody knows to switch on is a feature
// that does not exist; it is one click away in the bell.
export function readAlertSound() {
  try { return localStorage.getItem(KEY) !== "0"; } catch { return true; }
}

export function writeAlertSound(on) {
  try { localStorage.setItem(KEY, on ? "1" : "0"); } catch { /* storage blocked: stays on */ }
  window.dispatchEvent(new Event(EVENT));
}

/** The current setting, kept in step with every other copy of this toggle on
 *  the page and with the same setting changed in another tab. */
export function useAlertSoundPref() {
  const [on, setOn] = useState(readAlertSound);
  useEffect(() => {
    const read = () => setOn(readAlertSound());
    window.addEventListener(EVENT, read);
    window.addEventListener("storage", read);
    return () => {
      window.removeEventListener(EVENT, read);
      window.removeEventListener("storage", read);
    };
  }, []);
  return on;
}

// The two notes, in Hz: B5 then E6. A rising pair reads as "something has
// arrived" where a falling one reads as "something has finished", and a fifth
// apart is far enough that a small laptop speaker renders both.
const NOTES = [988, 1319];
const NOTE_LENGTH = 0.3;      // seconds of decay per note
const NOTE_GAP = 0.14;        // the second note starts before the first has died
const PEAK = 0.11;            // quiet on purpose: a notification, not an alarm

// A burst of alerts is ONE chime. The toaster already counts a burst into a
// single toast, and the same reasoning applies louder here: five alerts from
// one incident landing in the same poll must not produce five overlapping
// chimes, which is a noise nobody can tell from a fault.
const MIN_GAP_MS = 2000;

let audio = null;
let lastPlayedAt = 0;

// Created lazily and kept: a browser allows a handful of AudioContexts per
// page and refuses the rest, so one per chime would stop making a sound after
// a busy hour.
function context() {
  if (audio) return audio;
  const Ctor = window.AudioContext || window.webkitAudioContext;
  if (!Ctor) return null;                       // no Web Audio: silent, never throws
  try { audio = new Ctor(); } catch { return null; }
  return audio;
}

/**
 * Open the audio device on the first click or keypress.
 *
 * Browsers start an AudioContext "suspended" until the user has interacted
 * with the page, and resuming takes a moment — long enough that the FIRST
 * chime after a page load would be swallowed, which is the one that matters
 * on a desk that has just been sat down at. Signing in is a click, so by the
 * time any alert can arrive the device is already open.
 *
 * Returns its own teardown so a caller can mount it in an effect.
 */
export function armAlertSound() {
  let done = false;
  const arm = () => {
    if (done) return;
    done = true;
    const ctx = context();
    if (ctx && ctx.state === "suspended") ctx.resume().catch(() => {});
    off();
  };
  const off = () => {
    document.removeEventListener("pointerdown", arm);
    document.removeEventListener("keydown", arm);
  };
  document.addEventListener("pointerdown", arm);
  document.addEventListener("keydown", arm);
  return off;
}

/**
 * Chime, unless the machine is muted or one just played.
 *
 * Never throws: it is called from inside the alert poll's response handler,
 * and a browser that will not make a sound must not take the alert list down
 * with it.
 */
export function playAlertChime() {
  if (!readAlertSound()) return;
  const now = Date.now();
  if (now - lastPlayedAt < MIN_GAP_MS) return;

  const ctx = context();
  if (!ctx) return;
  lastPlayedAt = now;
  // Still suspended (no interaction yet, or the tab was restored): ask, and
  // let this chime go if the answer comes too late. The next one will play.
  if (ctx.state === "suspended") ctx.resume().catch(() => {});

  try {
    const start = ctx.currentTime + 0.02;
    NOTES.forEach((hz, i) => {
      const at = start + i * NOTE_GAP;
      const osc = ctx.createOscillator();
      // Triangle: a sine is too soft to carry across a room, a square too
      // harsh to hear forty times a shift.
      osc.type = "triangle";
      osc.frequency.value = hz;
      const gain = ctx.createGain();
      // An envelope, not a step. A tone that starts and stops at full volume
      // clicks at both ends, and the click is the part that grates.
      gain.gain.setValueAtTime(0.0001, at);
      gain.gain.exponentialRampToValueAtTime(PEAK, at + 0.015);
      gain.gain.exponentialRampToValueAtTime(0.0001, at + NOTE_LENGTH);
      osc.connect(gain).connect(ctx.destination);
      osc.start(at);
      // Stopped explicitly: an oscillator left running is a node the browser
      // keeps alive for the life of the page.
      osc.stop(at + NOTE_LENGTH + 0.02);
    });
  } catch { /* the device went away mid-chime; the screen still has the toast */ }
}
