// Philippine mobile numbers, reduced to the 10 national digits (917 123 4567).
//
// Deliberately the same rules as the dashboard's src/phone.js and the
// backend's core.models.normalize_ph_mobile: an officer typing 0917… on a
// phone and an admin pasting +63 917… into the web form must end up with the
// identical stored number, or the roster and the account disagree about who is
// reachable.
const toNationalPhone = (raw: string) => {
  let d = String(raw ?? "").replace(/\D/g, "");
  d = d.replace(/^0+/, "");                 // the trunk 0, or the 00 of an IDD prefix
  if (d.startsWith("63")) d = d.slice(2);   // country code
  return d.slice(0, 10);
};

/** "+63 917 123 4567", growing as it is typed. */
export const formatPhone = (raw: string) => {
  const d = toNationalPhone(raw);
  if (!d) return "";
  return `+63 ${[d.slice(0, 3), d.slice(3, 6), d.slice(6, 10)].filter(Boolean).join(" ")}`;
};

/** Every PH mobile prefix is 9xx, and the national number is exactly 10 digits. */
export const isValidPhone = (raw: string) => /^9\d{9}$/.test(toNationalPhone(raw));
