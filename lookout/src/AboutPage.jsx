import { Cigarette, Beer, Car, Siren, ShieldCheck, Lock, Users, Info } from "lucide-react";

// About page. Plain language for barangay staff: no points, scores or numbers about how an alert is
// rated. EDIT THE CONSTANTS BELOW to fill in the study title and team.

const STUDY_TITLE = "LookOut: A Computer Vision-Based System for Detecting Selected Barangay Ordinance Violations with Automated Barangay Authority Response in Zamboanga City";   // TODO: replace with the full study title
const TAGLINE =
  "LookOut watches a CCTV feed or footage for four violations it then detects possible violations and proposes an alert " +
  "for an officer to review. It never decides on its own.";
const VERSION = "Version 1.0";
const TEAM = [
  { name: "Mathew T. Angeles", role: "Project Manager" },
  { name: "Sheena Dianne L. De Guzman", role: "UI/UX Designer" },
  { name: "Alsamhel J. Jawadil", role: "Lead Developer" },
  { name: "Dr. John Ed Augustus A. Escorial", role: "Adviser" },
];

const VIOLATIONS = [
  { icon: Cigarette, color: "#f97316", name: "Smoking", law: "", note: "Smoking in public places." },
  { icon: Beer, color: "#8b5cf6", name: "Drinking", law: "", note: "Drinking in public." },
  { icon: Car, color: "#ef4444", name: "Parking Obstruction", law: "", note: "Vehicles obstructing the road." },
  { icon: Siren, color: "#dc2626", name: "Holdup", law: "", note: "A person threatening another with a knife." },
];

const STATUSES = [
  { name: "Monitoring", color: "#64748b",
    text: "An object linked to a violation was detected." },
  { name: "Possible", color: "#f59e0b",
    text: "Some signs of a violation, but not enough to be sure. Review the alert before acting." },
  { name: "Likely", color: "#dc2626",
    text: "Strong evidence of a violation. Review and respond." },
];

function Section({ icon: Icon, title, children }) {
  return (
    <section className="rounded-xl p-5" style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
      <div className="flex items-center gap-2 mb-3">
        <Icon size={16} style={{ color: "var(--muted-foreground)" }} />
        <h2 className="text-[16px] font-semibold" style={{ color: "var(--foreground)" }}>{title}</h2>
      </div>
      {children}
    </section>
  );
}

export function AboutPage() {
  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex items-center px-6 h-14 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
        <h1 className="text-xl font-bold" style={{ color: "var(--foreground)" }}>About</h1>
      </div>

      <div className="flex-1 overflow-y-auto p-6">
        <div className="max-w-3xl space-y-4">
          <div className="pb-1">
            <div className="text-[22px] font-bold leading-snug" style={{ color: "var(--foreground)" }}>{STUDY_TITLE}</div>
            <p className="text-[15px] mt-2 leading-relaxed" style={{ color: "var(--muted-foreground)" }}>{TAGLINE}</p>
          </div>

          <Section icon={Info} title="What it watches for">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {VIOLATIONS.map((v) => {
                const Icon = v.icon;
                return (
                  <div key={v.name} className="flex items-start gap-3 rounded-lg p-3"
                    style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
                    <Icon size={18} style={{ color: v.color, flexShrink: 0, marginTop: 2 }} />
                    <div>
                      <div className="text-[15px] font-semibold" style={{ color: "var(--foreground)" }}>{v.name}</div>
                      <div className="text-[13px] font-medium" style={{ color: v.color }}>{v.law}</div>
                      <div className="text-[13px] mt-0.5" style={{ color: "var(--muted-foreground)" }}>{v.note}</div>
                    </div>
                  </div>
                );
              })}
            </div>
          </Section>

          <Section icon={ShieldCheck} title="The system proposes, the officer confirms">
            <p className="text-[14px] leading-relaxed mb-3" style={{ color: "var(--muted-foreground)" }}>
              LookOut specifically detects 3 types of classes or objects: a bottle for the drinking violation, a cigarette for smoking and a knife for Holdup. Once the heuristic rules are applied, it proposes a alert with a status. An officer then reviews
              the alert and marks it accordingly. A person always makes the final call.
            </p>
            <div className="space-y-2">
              {STATUSES.map((s) => (
                <div key={s.name} className="flex items-start gap-3">
                  <span className="text-[13px] font-semibold px-2.5 py-0.5 rounded-full flex-shrink-0 mt-0.5"
                    style={{ color: s.color, border: `1px solid ${s.color}66`, background: "var(--secondary)", minWidth: 92, textAlign: "center" }}>
                    {s.name}
                  </span>
                  <span className="text-[14px] leading-relaxed" style={{ color: "var(--foreground)" }}>{s.text}</span>
                </div>
              ))}
            </div>
            <p className="text-[14px] leading-relaxed mt-3" style={{ color: "var(--muted-foreground)" }}>
              An <b>AI checker</b> also looks at a short clip and describes what it sees. It is shown beside the
              status as extra context and a suggestion. It never changes the status.
            </p>
          </Section>

          <Section icon={Lock} title="Privacy">
            <ul className="space-y-1.5 text-[14px] leading-relaxed list-disc pl-5" style={{ color: "var(--foreground)" }}>
              <li>The AI checker runs on this computer. Video frames never leave the device.</li>
              <li>There is no facial recognition. LookOut does not identify who anyone is.</li>
              <li>Alert images and clips are kept only for the evidence retention period set in Settings.</li>
              <li>Handling of footage follows the Data Privacy Act of 2012 (RA 10173).</li>
            </ul>
          </Section>

          <Section icon={Users} title="Team">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {TEAM.map((m, i) => (
                <div key={i} className="rounded-lg px-3 py-2" style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
                  <div className="text-[14px] font-medium" style={{ color: "var(--foreground)" }}>{m.name}</div>
                  <div className="text-[13px]" style={{ color: "var(--muted-foreground)" }}>{m.role}</div>
                </div>
              ))}
            </div>
            <div className="text-[13px] mt-4" style={{ color: "var(--muted-foreground)" }}>{VERSION}</div>
          </Section>
        </div>
      </div>
    </div>
  );
}
