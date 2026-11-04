// Philippine mobile numbers, normalized to the 10 national digits (917 123 4567).
// The three ways an operator writes the same number — 0917…, +63917…, 63917… —
// all reduce to that, so a field accepts a pasted number in any of them and
// still displays one format. The trunk 0 is dropped before the 63 so a pasted
// "0063…" normalizes too.
//
// Shared, not per-page: the Personnel page and the My Account page both take a
// number, and the backend stores whatever either sends in one canonical form
// (core.models.normalize_ph_mobile). Two copies of this would be two chances
// for a client to disagree with that.
export const toNationalPhone = (raw) => {
  let d = String(raw ?? "").replace(/\D/g, "");
  d = d.replace(/^0+/, "");                 // the trunk 0, or the 00 of an IDD prefix
  if (d.startsWith("63")) d = d.slice(2);   // country code
  return d.slice(0, 10);
};

// "+63 917 123 4567". Grows as it is typed: the +63 appears with the first
// digit, so the shape of what's wanted is visible before the number is whole.
export const formatPhone = (raw) => {
  const d = toNationalPhone(raw);
  if (!d) return "";
  return `+63 ${[d.slice(0, 3), d.slice(3, 6), d.slice(6, 10)].filter(Boolean).join(" ")}`;
};

// Every PH mobile prefix is 9xx, and the national number is exactly 10 digits.
export const isValidPhone = (raw) => /^9\d{9}$/.test(toNationalPhone(raw));
