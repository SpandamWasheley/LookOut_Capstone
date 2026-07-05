import { useEffect, useState } from "react";
import {
  Search, UserPlus, CheckCircle, Clock, AlertTriangle,
  Shield, User, X, Home, LayoutList, ChevronDown, ChevronUp, Plus, Save, Loader2, Phone,
} from "lucide-react";
import { ZONES } from "../data/mockData";
import {
  getHouseholds, createHousehold, updateHousehold,
  createHouseholdMember, updateHouseholdMember,
  getResidents, createResident,
} from "./api";
import { AddHouseholdModal } from "./AddHouseholdModal";
import { EnrollModal } from "./EnrollModal";

const formatPhone = (raw) => {
  const d = raw.replace(/\D/g, "").slice(0, 11);
  if (d.length <= 4) return d;
  if (d.length <= 7) return `${d.slice(0, 4)}-${d.slice(4)}`;
  return `${d.slice(0, 4)}-${d.slice(4, 7)}-${d.slice(7)}`;
};
const blockNumbers = (v) => v.replace(/[0-9]/g, "");
const maxToday = () => new Date().toISOString().slice(0, 10);

function mapHousehold(raw) {
  const idToCode = {};
  raw.members.forEach((m) => { idToCode[m.id] = m.code; });
  return {
    id: raw.code,
    dbId: raw.id,
    familyName: raw.family_name,
    purok: raw.purok,
    address: raw.address,
    zone: raw.zone,
    contact: raw.contact,
    enrolledDate: raw.enrolled_date,
    members: raw.members.map((m) => ({
      id: m.code,
      dbId: m.id,
      firstName: m.first_name,
      lastName: m.last_name,
      birthdate: m.birthdate,
      barangayId: m.barangay_id,
      status: m.status,
      relation: m.relation,
      imageUrl: m.image_url,
      phone: m.phone,
    })),
    guardianLinks: raw.members
      .filter((m) => m.guardians.length > 0)
      .map((m) => ({ minorId: m.code, guardianIds: m.guardians.map((gid) => idToCode[gid]).filter(Boolean) })),
  };
}

function mapResident(raw) {
  return {
    id: raw.code,
    dbId: raw.id,
    name: raw.name,
    age: raw.age,
    status: raw.status,
    barangayId: raw.barangay_id,
    guardianName: raw.guardian_name,
    imageUrl: raw.image_url,
    phone: raw.phone,
  };
}

function randomBarangayId() {
  return `BRG-TET-${Math.floor(1000 + Math.random() * 8999)}`;
}

const statusConfig = {
  verified: { label: "Verified", color: "#10b981", bg: "rgba(16,185,129,0.1)", icon: CheckCircle },
  pending:  { label: "Pending",  color: "#f59e0b", bg: "rgba(245,158,11,0.1)", icon: Clock },
  flagged:  { label: "Flagged",  color: "#ef4444", bg: "rgba(239,68,68,0.1)", icon: AlertTriangle },
};

function computeAge(birthdate) {
  const today = new Date();
  const dob = new Date(birthdate);
  let age = today.getFullYear() - dob.getFullYear();
  const m = today.getMonth() - dob.getMonth();
  if (m < 0 || (m === 0 && today.getDate() < dob.getDate())) age--;
  return age;
}

function memberInitials(first, last) {
  return `${first[0] ?? ""}${last[0] ?? ""}`.toUpperCase();
}

function matchesQualityFilter(filter, age, status) {
  if (filter === "all") return true;
  if (filter === "minor") return age < 18;
  if (filter === "adult") return age >= 18;
  if (filter === "flagged") return status === "flagged";
  return true;
}

function fmtTime(ts) {
  return new Date(ts).toLocaleTimeString("en-PH", { hour: "2-digit", minute: "2-digit", hour12: false });
}

// ── Manage Household Modal ─────────────────────────────────────────────────────
function ManageHouseholdModal({ household, onSave, onClose }) {
  const [familyName, setFamilyName] = useState(household.familyName);
  const [purok,      setPurok]      = useState(household.purok);
  const [address,    setAddress]    = useState(household.address);
  const [zone,       setZone]       = useState(household.zone);
  const [contact,    setContact]    = useState(household.contact);
  const [saved, setSaved] = useState(false);

  const secondaryContacts = household.members
    .filter((m) => computeAge(m.birthdate) >= 18 && m.phone)
    .map((m) => ({ name: `${m.firstName} ${m.lastName}`, phone: m.phone }));

  const handleSave = () => {
    onSave({ ...household, familyName, purok, address, zone, contact });
    setSaved(true);
    setTimeout(() => { setSaved(false); onClose(); }, 900);
  };

  const valid = familyName.trim() && address.trim() && contact.trim();

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.78)", backdropFilter: "blur(8px)" }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        className="w-full max-w-lg rounded-2xl overflow-hidden shadow-2xl flex flex-col"
        style={{ background: "var(--card)", border: "1px solid var(--border)", maxHeight: "90vh" }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ background: "rgba(245,158,11,0.12)" }}>
              <Home size={14} style={{ color: "#f59e0b" }} />
            </div>
            <div>
              <div className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>Manage Household</div>
              <div className="text-[11px]" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
                {household.id}
              </div>
            </div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg" style={{ color: "var(--muted-foreground)", background: "var(--secondary)" }}>
            <X size={14} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-5 space-y-5">
          <div>
            <div className="text-[10px] font-semibold tracking-widest uppercase mb-3"
              style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
              Household Details
            </div>
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>
                    Family name <span style={{ color: "#ef4444" }}>*</span>
                  </label>
                  <input
                    value={familyName}
                    onChange={(e) => setFamilyName(e.target.value)}
                    className="w-full px-3 py-2.5 rounded-xl text-sm outline-none"
                    style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }}
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>Purok / Sitio</label>
                  <input
                    value={purok}
                    onChange={(e) => setPurok(e.target.value)}
                    placeholder="e.g. Purok 3"
                    className="w-full px-3 py-2.5 rounded-xl text-sm outline-none"
                    style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }}
                  />
                </div>
              </div>
              <div>
                <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>
                  Address <span style={{ color: "#ef4444" }}>*</span>
                </label>
                <input
                  value={address}
                  onChange={(e) => setAddress(e.target.value)}
                  className="w-full px-3 py-2.5 rounded-xl text-sm outline-none"
                  style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }}
                />
              </div>
              <div>
                <label className="block text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>
                  Contact (primary) <span style={{ color: "#ef4444" }}>*</span>
                </label>
                <input
                  value={contact}
                  onChange={(e) => setContact(formatPhone(e.target.value))}
                  placeholder="0951-853-2146"
                  className="w-full px-3 py-2.5 rounded-xl text-sm outline-none"
                  style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }}
                />
              </div>

              {secondaryContacts.length > 0 && (
                <div className="rounded-xl px-3.5 py-3" style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
                  <div className="text-xs font-medium mb-1.5" style={{ color: "var(--muted-foreground)" }}>
                    Secondary contacts <span className="font-normal">(from adult members)</span>
                  </div>
                  <div className="space-y-1">
                    {secondaryContacts.map((c) => (
                      <div key={c.phone} className="flex items-center justify-between text-[12px]">
                        <span style={{ color: "var(--muted-foreground)" }}>{c.name}</span>
                        <span style={{ color: "#f59e0b", fontFamily: "'DM Mono', monospace" }}>{c.phone}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>

          <div>
            <div className="text-[10px] font-semibold tracking-widest uppercase mb-3"
              style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
              Members ({household.members.length})
            </div>
            <div className="rounded-xl overflow-hidden" style={{ border: "1px solid var(--border)" }}>
              {household.members.map((m, i) => {
                const age = computeAge(m.birthdate);
                const minor = age < 18;
                const st = statusConfig[m.status];
                return (
                  <div
                    key={m.id}
                    className="flex items-center gap-3 px-4 py-2.5"
                    style={{ borderBottom: i < household.members.length - 1 ? "1px solid var(--border)" : "none" }}
                  >
                    <div
                      className="w-7 h-7 rounded-lg flex items-center justify-center text-[10px] font-bold flex-shrink-0"
                      style={{ background: minor ? "rgba(245,158,11,0.12)" : "rgba(100,116,139,0.12)", color: minor ? "#f59e0b" : "var(--muted-foreground)" }}
                    >
                      {memberInitials(m.firstName, m.lastName)}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-[12px] font-medium" style={{ color: "var(--foreground)" }}>{m.lastName}, {m.firstName}</span>
                        {minor && (
                          <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full" style={{ background: "rgba(245,158,11,0.12)", color: "#f59e0b" }}>
                            Minor
                          </span>
                        )}
                      </div>
                      <div className="text-[10px]" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
                        {m.barangayId} · Age {age}
                      </div>
                    </div>
                    <span className="text-[11px] font-medium" style={{ color: st.color }}>{st.label}</span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-3 px-5 py-4 flex-shrink-0" style={{ borderTop: "1px solid var(--border)" }}>
          <button onClick={onClose} className="px-4 py-2 rounded-xl text-sm font-medium"
            style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
            Cancel
          </button>
          <button
            disabled={!valid}
            onClick={handleSave}
            className="flex items-center gap-2 px-5 py-2 rounded-xl text-sm font-medium transition-all"
            style={{
              background: saved ? "#10b981" : valid ? "var(--primary)" : "rgba(245,158,11,0.2)",
              color: saved ? "#fff" : valid ? "var(--primary-foreground)" : "rgba(245,158,11,0.4)",
              cursor: valid ? "pointer" : "not-allowed",
            }}
          >
            <Save size={13} /> {saved ? "Saved!" : "Save changes"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Household card ─────────────────────────────────────────────────────────────
const emptyNewMember = { firstName: "", lastName: "", birthdate: "", relation: "", phone: "" };

function HouseholdCard({ household, onManage, filter, onAddMember }) {
  const [collapsed, setCollapsed] = useState(false);
  const [addingMember, setAddingMember] = useState(false);
  const [newMember, setNewMember] = useState(emptyNewMember);
  const [submittingMember, setSubmittingMember] = useState(false);

  const minorCount  = household.members.filter((m) => computeAge(m.birthdate) < 18).length;
  const memberCount = household.members.length;
  const visibleMembers = household.members.filter((m) => matchesQualityFilter(filter, computeAge(m.birthdate), m.status));
  const secondaryContacts = household.members
    .filter((m) => computeAge(m.birthdate) >= 18 && m.phone)
    .map((m) => ({ name: `${m.firstName} ${m.lastName}`, phone: m.phone }));

  const newMemberValid = newMember.firstName.trim() && newMember.lastName.trim() && newMember.birthdate;

  const submitNewMember = async () => {
    if (!newMemberValid) return;
    setSubmittingMember(true);
    try {
      await onAddMember(newMember);
      setNewMember(emptyNewMember);
      setAddingMember(false);
    } finally {
      setSubmittingMember(false);
    }
  };

  return (
    <div className="rounded-xl overflow-hidden mb-3" style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
      <div className="flex items-center gap-3 px-4 py-3" style={{ background: "var(--secondary)" }}>
        <button
          onClick={() => setCollapsed((c) => !c)}
          className="p-1 rounded-md flex-shrink-0 transition-colors"
          style={{ color: "var(--muted-foreground)" }}
        >
          {collapsed ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
        </button>

        <Home size={13} style={{ color: "var(--muted-foreground)", flexShrink: 0 }} />

        <div
          className="flex-1 min-w-0 flex items-center gap-2 flex-wrap cursor-pointer"
          onClick={() => setCollapsed((c) => !c)}
        >
          <span className="text-[13px] font-semibold" style={{ color: "var(--foreground)" }}>
            {household.familyName} household
          </span>
          <span className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>
            · {household.address}, Brgy. Tetuan
          </span>
          {minorCount > 0 && (
            <span className="text-[10px] font-bold px-2 py-0.5 rounded-full" style={{ background: "rgba(245,158,11,0.15)", color: "#f59e0b" }}>
              {minorCount} {minorCount === 1 ? "minor" : "minors"}
            </span>
          )}
          {secondaryContacts.length > 0 && (
            <span className="flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full" style={{ background: "rgba(59,130,246,0.15)", color: "#3b82f6" }}>
              <Phone size={9} /> {secondaryContacts.length} secondary {secondaryContacts.length === 1 ? "number" : "numbers"}
            </span>
          )}
          {collapsed && (
            <span className="text-[10px] px-2 py-0.5 rounded-full" style={{ background: "var(--muted)", color: "var(--muted-foreground)" }}>
              {memberCount} member{memberCount !== 1 ? "s" : ""}
            </span>
          )}
        </div>

        <span className="text-[10px] font-bold flex-shrink-0" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
          {household.id}
        </span>

        <button
          onClick={() => onManage(household)}
          className="text-[11px] font-medium px-2.5 py-1 rounded-md flex-shrink-0 transition-all"
          style={{ background: "rgba(245,158,11,0.1)", color: "#f59e0b", border: "1px solid rgba(245,158,11,0.2)" }}
        >
          Manage
        </button>
      </div>

      {(household.contact || secondaryContacts.length > 0) && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2 text-[11px]"
          style={{ borderTop: "1px solid var(--border)", color: "var(--muted-foreground)" }}>
          {household.contact && (
            <span>Primary: <span style={{ color: "var(--foreground)", fontFamily: "'DM Mono', monospace" }}>{household.contact}</span></span>
          )}
          {secondaryContacts.length > 0 && (
            <span>
              Secondary:{" "}
              {secondaryContacts.map((c, i) => (
                <span key={c.phone}>
                  <span style={{ color: "#f59e0b", fontFamily: "'DM Mono', monospace" }}>{c.phone}</span>
                  {" "}({c.name}){i < secondaryContacts.length - 1 ? ", " : ""}
                </span>
              ))}
            </span>
          )}
        </div>
      )}

      {!collapsed && (
        <>
          <div className="divide-y" style={{ borderColor: "var(--border)", borderTop: "1px solid var(--border)" }}>
            {visibleMembers.map((member) => {
              const age = computeAge(member.birthdate);
              const minor = age < 18;
              const init = memberInitials(member.firstName, member.lastName);
              const st = statusConfig[member.status];

              const guardianLink = household.guardianLinks.find((gl) => gl.minorId === member.id);
              const guardianNames = (guardianLink?.guardianIds ?? []).map((gid) => {
                const g = household.members.find((m) => m.id === gid);
                return g ? g.firstName : "";
              }).filter(Boolean);

              const guardianOf = minor ? [] : household.guardianLinks
                .filter((gl) => gl.guardianIds.includes(member.id))
                .map((gl) => household.members.find((m) => m.id === gl.minorId)?.firstName)
                .filter(Boolean);

              return (
                <div
                  key={member.id}
                  className="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-white/[0.015]   "
                >
                  <div
                    className="w-9 h-9 rounded-lg flex items-center justify-center text-xs font-bold flex-shrink-0 overflow-hidden"
                    style={{
                      background: minor ? "rgba(245,158,11,0.15)" : "rgba(100,116,139,0.12)",
                      color: minor ? "#f59e0b" : "var(--muted-foreground)",
                      border: `1.5px solid ${st.color}30`,
                    }}
                  >
                    {member.imageUrl
                      ? <img src={member.imageUrl} alt={member.firstName} className="w-full h-full object-cover" />
                      : init}
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-[13px] font-medium" style={{ color: "var(--foreground)" }}>
                        {member.lastName}, {member.firstName}
                      </span>
                      <span className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>{member.relation}</span>
                      {minor && (
                        <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full"
                          style={{ background: "rgba(245,158,11,0.15)", color: "#f59e0b" }}>
                          {["Son","Daughter"].includes(member.relation) ? `${member.relation} · Minor` : "Minor"}
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                      <span className="text-[10px]" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
                        {member.barangayId}
                      </span>
                      <span className="text-[10px]" style={{ color: "var(--muted-foreground)" }}>· Age {age} · {minor ? "Minor" : "Adult"}</span>
                    </div>
                    {guardianOf.length > 0 && (
                      <div className="text-[10px] mt-0.5" style={{ color: "#64748b" }}>
                        · Registered guardian of {guardianOf.join(", ")}
                      </div>
                    )}
                    {guardianNames.length > 0 && (
                      <div className="text-[10px] mt-0.5" style={{ color: "#64748b" }}>
                        · Guardians: {guardianNames.map((n) => {
                          const g = household.members.find((m) => m.firstName === n);
                          return g ? `${n} (${g.relation})` : n;
                        }).join(", ")}
                      </div>
                    )}
                  </div>

                  <div className="text-right flex-shrink-0 space-y-0.5">
                    <div className="text-[11px] font-medium" style={{ color: st.color }}>{st.label}</div>
                    {member.lastSeen && (
                      <div className="text-[10px]" style={{ color: "var(--muted-foreground)" }}>
                        Last seen {fmtTime(member.lastSeen)}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          <div style={{ borderTop: "1px solid var(--border)" }}>
            {addingMember ? (
              <div className="p-4 space-y-3" style={{ background: "var(--secondary)" }}>
                <div className="grid grid-cols-2 gap-2.5">
                  <input
                    value={newMember.firstName}
                    onChange={(e) => setNewMember((p) => ({ ...p, firstName: blockNumbers(e.target.value) }))}
                    placeholder="First name"
                    className="w-full px-3 py-2 rounded-lg text-xs outline-none"
                    style={{ background: "var(--card)", border: "1px solid var(--border)", color: "var(--foreground)" }}
                  />
                  <input
                    value={newMember.lastName}
                    onChange={(e) => setNewMember((p) => ({ ...p, lastName: blockNumbers(e.target.value) }))}
                    placeholder="Last name"
                    className="w-full px-3 py-2 rounded-lg text-xs outline-none"
                    style={{ background: "var(--card)", border: "1px solid var(--border)", color: "var(--foreground)" }}
                  />
                </div>
                <div className="grid grid-cols-2 gap-2.5">
                  <input
                    type="date"
                    value={newMember.birthdate}
                    onChange={(e) => { const v = e.target.value; if (v <= maxToday()) setNewMember((p) => ({ ...p, birthdate: v })); }}
                    max={maxToday()}
                    className="w-full px-3 py-2 rounded-lg text-xs outline-none"
                    style={{ background: "var(--card)", border: "1px solid var(--border)", color: "var(--foreground)" }}
                  />
                  <input
                    value={newMember.relation}
                    onChange={(e) => setNewMember((p) => ({ ...p, relation: e.target.value }))}
                    placeholder="Relation (e.g. Son)"
                    className="w-full px-3 py-2 rounded-lg text-xs outline-none"
                    style={{ background: "var(--card)", border: "1px solid var(--border)", color: "var(--foreground)" }}
                  />
                </div>

                {newMember.birthdate && computeAge(newMember.birthdate) >= 18 && (
                  <div className="rounded-lg p-3 space-y-1.5" style={{ background: "rgba(245,158,11,0.06)", border: "1px solid rgba(245,158,11,0.2)" }}>
                    <label className="block text-[11px] font-medium" style={{ color: "#f59e0b" }}>
                      Mobile number <span className="font-normal" style={{ color: "var(--muted-foreground)" }}>(optional · adult member)</span>
                    </label>
                    <input
                      value={newMember.phone}
                      onChange={(e) => setNewMember((p) => ({ ...p, phone: formatPhone(e.target.value) }))}
                      placeholder="0951-853-2146"
                      className="w-full px-3 py-2 rounded-lg text-xs outline-none"
                      style={{ background: "var(--card)", border: "1px solid var(--border)", color: "var(--foreground)" }}
                    />
                    <p className="text-[10px]" style={{ color: "var(--muted-foreground)" }}>
                      Saved as a secondary contact number for this household.
                    </p>
                  </div>
                )}

                <div className="flex items-center gap-2">
                  <button
                    onClick={() => { setAddingMember(false); setNewMember(emptyNewMember); }}
                    className="px-3 py-1.5 rounded-lg text-xs font-medium"
                    style={{ background: "var(--card)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}
                  >
                    Cancel
                  </button>
                  <button
                    onClick={submitNewMember}
                    disabled={!newMemberValid || submittingMember}
                    className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-xs font-medium transition-all"
                    style={{
                      background: newMemberValid ? "#f59e0b" : "rgba(245,158,11,0.2)",
                      color: newMemberValid ? "#0c0f16" : "rgba(245,158,11,0.4)",
                      cursor: newMemberValid && !submittingMember ? "pointer" : "not-allowed",
                    }}
                  >
                    <Plus size={11} /> {submittingMember ? "Adding…" : "Add member"}
                  </button>
                </div>
              </div>
            ) : (
              <button
                onClick={() => setAddingMember(true)}
                className="w-full flex items-center gap-2 px-4 py-2.5 text-[12px] font-medium transition-colors cursor-pointer"
                style={{ color: "var(--muted-foreground)" }}
                onMouseEnter={(e) => (e.currentTarget.style.color = "#f59e0b")}
                onMouseLeave={(e) => (e.currentTarget.style.color = "var(--muted-foreground)")}
              >
                <Plus size={12} />
                Add member to this household
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// ── Flat resident row ─────────────────────────────────────────────────────────
function ResidentRow({ r }) {
  const scfg = statusConfig[r.status];
  const StatusIcon = scfg.icon;
  const isMinor = r.age < 18;
  return (
    <div className="flex items-center gap-4 px-4 py-3 rounded-xl transition-all hover:bg-white/[0.02] cursor-pointer"
      style={{ border: "1px solid var(--border)", background: "var(--card)" }}>
      <div className="relative flex-shrink-0">
        <img src={r.imageUrl} alt={r.name} className="w-9 h-9 rounded-lg object-cover"
          style={{ border: `1.5px solid ${scfg.color}40` }} />
        {isMinor && (
          <span className="absolute -bottom-0.5 -right-0.5 w-3.5 h-3.5 rounded-full text-[7px] font-bold flex items-center justify-center"
            style={{ background: "#f59e0b", color: "#fff" }}>M</span>
        )}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-[13px] font-medium" style={{ color: "var(--foreground)" }}>{r.name}</span>
          {isMinor && <span className="text-[10px] px-1.5 py-0.5 rounded font-medium" style={{ background: "rgba(245,158,11,0.1)", color: "#f59e0b" }}>Minor</span>}
        </div>
        <div className="flex items-center gap-3 mt-0.5">
          <span className="text-[11px]" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>{r.barangayId}</span>
          <span className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>Age {r.age}</span>
          {r.guardianName && <span className="text-[11px] flex items-center gap-1" style={{ color: "var(--muted-foreground)" }}><Shield size={9} /> {r.guardianName}</span>}
          {r.phone && <span className="text-[11px] flex items-center gap-1" style={{ color: "#f59e0b" }}><Phone size={9} /> {r.phone}</span>}
        </div>
      </div>
      <div className="flex items-center gap-3 flex-shrink-0">
        {r.lastSeen && (
          <div className="text-right">
            <div className="text-[10px]" style={{ color: "var(--muted-foreground)" }}>Last seen</div>
            <div className="text-[11px] font-medium" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
              {new Date(r.lastSeen).toLocaleTimeString("en-PH", { hour: "2-digit", minute: "2-digit", hour12: false })}
            </div>
          </div>
        )}
        <span className="flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium" style={{ background: scfg.bg, color: scfg.color }}>
          <StatusIcon size={10} /> {scfg.label}
        </span>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────
export function ResidentDatabase() {
  const [households,   setHouseholds]   = useState([]);
  const [residents,    setResidents]    = useState([]);
  const [loading,      setLoading]      = useState(true);
  const [loadError,    setLoadError]    = useState("");
  const [search,       setSearch]       = useState("");
  const [filter,       setFilter]       = useState("all");
  const [viewMode,     setViewMode]     = useState("household");
  const [showAddHH,    setShowAddHH]    = useState(false);
  const [showEnroll,   setShowEnroll]   = useState(false);
  const [managingHH,   setManagingHH]   = useState(null);

  const refreshHouseholds = async () => {
    const data = await getHouseholds();
    setHouseholds((data.results ?? data).map(mapHousehold));
  };

  const refreshResidents = async () => {
    const data = await getResidents();
    setResidents((data.results ?? data).map(mapResident));
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setLoadError("");
      try {
        await Promise.all([refreshHouseholds(), refreshResidents()]);
      } catch (err) {
        if (!cancelled) setLoadError(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const allMembers   = households.flatMap((h) => h.members);
  const totalEnrolled = allMembers.length + residents.length;
  const totalMinors   = allMembers.filter((m) => computeAge(m.birthdate) < 18).length + residents.filter((r) => r.age < 18).length;
  const totalFlagged  = allMembers.filter((m) => m.status === "flagged").length + residents.filter((r) => r.status === "flagged").length;

  const filteredHouseholds = households.filter((h) => {
    const matchSearch = !search || (() => {
      const q = search.toLowerCase();
      return (
        h.familyName.toLowerCase().includes(q) ||
        h.address.toLowerCase().includes(q) ||
        h.id.toLowerCase().includes(q) ||
        h.members.some((m) => `${m.firstName} ${m.lastName}`.toLowerCase().includes(q) || m.barangayId.toLowerCase().includes(q))
      );
    })();
    const matchFilter = h.members.some((m) => matchesQualityFilter(filter, computeAge(m.birthdate), m.status));
    return matchSearch && matchFilter;
  });

  const filteredResidents = residents.filter((r) => {
    const matchSearch = !search || r.name.toLowerCase().includes(search.toLowerCase()) || r.barangayId.toLowerCase().includes(search.toLowerCase());
    const matchFilter = matchesQualityFilter(filter, r.age, r.status);
    return matchSearch && matchFilter;
  });

  const handleSaveHousehold = async (updated) => {
    try {
      await updateHousehold(updated.dbId, {
        family_name: updated.familyName,
        purok: updated.purok,
        address: updated.address,
        zone: updated.zone || null,
        contact: updated.contact,
      });
      await refreshHouseholds();
      setManagingHH(null);
    } catch (err) {
      alert(`Failed to update household: ${err.message}`);
    }
  };

  const handleAddHousehold = async (data) => {
    try {
      const createdHousehold = await createHousehold({
        family_name: data.familyName,
        address: data.address,
        contact: data.contact,
        ...(data.zone ? { zone: data.zone } : {}),
      });

      const localToPk = {};
      for (const m of data.members) {
        const created = await createHouseholdMember({
          household: createdHousehold.id,
          first_name: m.firstName,
          last_name: m.lastName,
          birthdate: m.birthdate,
          barangay_id: randomBarangayId(),
          status: "pending",
          relation: m.relation,
          phone: m.phone || "",
        });
        localToPk[m.id] = created.id;
      }

      for (const link of data.guardianLinks) {
        const minorPk = localToPk[link.minorId];
        const guardianPks = link.guardianIds.map((gid) => typeof gid === "number" ? gid : localToPk[gid]).filter(Boolean);
        if (minorPk && guardianPks.length > 0) {
          await updateHouseholdMember(minorPk, { guardians: guardianPks });
        }
      }

      await refreshHouseholds();
      setShowAddHH(false);
    } catch (err) {
      alert(`Failed to save household: ${err.message}`);
    }
  };

  const handleEnroll = async (data) => {
    try {
      await createResident({
        name: data.name,
        age: data.age,
        status: data.status,
        image_url: data.imageUrl,
        barangay_id: randomBarangayId(),
        phone: data.phone || "",
      });
      await refreshResidents();
    } catch (err) {
      alert(`Failed to enroll resident: ${err.message}`);
    }
  };

  const handleAddMember = async (householdDbId, member) => {
    try {
      await createHouseholdMember({
        household: householdDbId,
        first_name: member.firstName,
        last_name: member.lastName,
        birthdate: member.birthdate,
        barangay_id: randomBarangayId(),
        status: "pending",
        relation: member.relation,
        phone: member.phone || "",
      });
      await refreshHouseholds();
    } catch (err) {
      alert(`Failed to add member: ${err.message}`);
      throw err;
    }
  };

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-6 h-14 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-bold" style={{ color: "var(--foreground)" }}>Residents</h1>
          </div>
          <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>
            ArcFace biometrics ·{" "}
            <span className="px-1.5 py-0.5 rounded text-[10px] font-medium" style={{ background: "rgba(16,185,129,0.1)", color: "#10b981" }}>
              RA 10173 compliant
            </span>
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setShowAddHH(true)}
            className="flex items-center gap-2.5 px-4 py-1.5 rounded-lg text-xs font-medium transition-all"
            style={{ background: "var(--secondary)", color: "var(--foreground)", border: "1px solid var(--border)" }}>
            <Home size={12} /> Add household
          </button>
          <button onClick={() => setShowEnroll(true)}
            className="flex items-center gap-2.5 px-4 py-1.5 rounded-lg text-xs font-medium"
            style={{ background: "var(--primary)", color: "var(--primary-foreground)" }}>
            <UserPlus size={12} /> Enroll resident
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-hidden flex flex-col px-6 py-4 gap-4">
        {/* Stats */}
        <div className="grid grid-cols-4 gap-3 flex-shrink-0">
          {[
            { label: "Households", value: households.length,  color: "var(--muted-foreground)" },
            { label: "Enrolled",   value: totalEnrolled,       color: "var(--muted-foreground)" },
            { label: "Minors",     value: totalMinors,         color: "#f59e0b" },
            { label: "Flagged",    value: totalFlagged,        color: "#ef4444" },
          ].map((s) => (
            <div key={s.label} className="rounded-xl p-4 text-center" style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
              <div className="text-2xl font-semibold leading-none" style={{ color: s.color }}>{s.value}</div>
              <div className="text-[11px] mt-1.5" style={{ color: "var(--muted-foreground)" }}>{s.label}</div>
            </div>
          ))}
        </div>

        {/* Controls */}
        <div className="flex items-center gap-3 flex-shrink-0">
          <div className="flex-1 relative">
            <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "var(--muted-foreground)" }} />
            <input value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by name, household, or BRG ID…"
              className="w-full pl-9 pr-3 py-2 rounded-lg text-sm outline-none"
              style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }} />
          </div>
          <div className="flex gap-1">
            {["all", "minor", "adult", "flagged"].map((f) => (
              <button key={f} onClick={() => setFilter(f)}
                className="px-3 py-2 text-xs font-medium rounded-lg transition-all capitalize"
                style={{
                  background: filter === f ? "var(--primary)" : "var(--secondary)",
                  color: filter === f ? "var(--primary-foreground)" : "var(--muted-foreground)",
                  border: `1px solid ${filter === f ? "var(--primary)" : "var(--border)"}`,
                }}>
                {f === "all" ? "All" : f.charAt(0).toUpperCase() + f.slice(1)}
              </button>
            ))}
          </div>
          <div className="flex rounded-lg overflow-hidden" style={{ border: "1px solid var(--border)" }}>
            {[{ mode: "household", icon: Home }, { mode: "list", icon: LayoutList }].map(({ mode, icon: Icon }) => (
              <button key={mode} onClick={() => setViewMode(mode)} className="p-2 transition-all"
                style={{ background: viewMode === mode ? "var(--primary)" : "var(--secondary)", color: viewMode === mode ? "var(--primary-foreground)" : "var(--muted-foreground)" }}>
                <Icon size={14} />
              </button>
            ))}
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex flex-col items-center justify-center py-16 gap-2">
              <Loader2 size={24} className="animate-spin" style={{ color: "var(--muted-foreground)" }} />
              <div className="text-sm font-medium" style={{ color: "var(--foreground)" }}>Loading residents…</div>
            </div>
          ) : loadError ? (
            <div className="flex flex-col items-center justify-center py-16 gap-2">
              <AlertTriangle size={24} style={{ color: "#ef4444" }} />
              <div className="text-sm font-medium" style={{ color: "var(--foreground)" }}>Failed to load data</div>
              <div className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>{loadError}</div>
            </div>
          ) : viewMode === "household" ? (
            filteredHouseholds.length === 0
              ? <div className="flex flex-col items-center justify-center py-16 gap-2"><Home size={28} style={{ color: "var(--muted-foreground)" }} /><div className="text-sm font-medium" style={{ color: "var(--foreground)" }}>No households found</div></div>
              : filteredHouseholds.map((h) => (
                  <HouseholdCard
                    key={h.id}
                    household={h}
                    onManage={setManagingHH}
                    filter={filter}
                    onAddMember={(member) => handleAddMember(h.dbId, member)}
                  />
                ))

          ) : (
            <div className="space-y-2">
              {filteredResidents.map((r) => <ResidentRow key={r.id} r={r} />)}
              {filteredResidents.length === 0 && (
                <div className="flex flex-col items-center justify-center py-16 gap-2">
                  <User size={28} style={{ color: "var(--muted-foreground)" }} />
                  <div className="text-sm font-medium" style={{ color: "var(--foreground)" }}>No residents found</div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {showAddHH && <AddHouseholdModal nextHouseholdId="Auto-generated on save" onSave={handleAddHousehold} onClose={() => setShowAddHH(false)} />}
      {showEnroll && <EnrollModal onClose={() => setShowEnroll(false)} onEnroll={handleEnroll} />}
      {managingHH && <ManageHouseholdModal household={managingHH} onSave={handleSaveHousehold} onClose={() => setManagingHH(null)} />}
    </div>
  );
}