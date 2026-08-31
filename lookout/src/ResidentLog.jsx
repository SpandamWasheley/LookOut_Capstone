import { useState, useEffect } from "react";
import {
  Search, User, ChevronLeft, Loader2, AlertTriangle, GitMerge, Shield, Calendar, FileText,
} from "lucide-react";
import { getViolators, getCitations, searchViolators, mergeViolators } from "./api";

function fmtDate(ts) {
  return new Date(ts).toLocaleDateString("en-PH", { month: "short", day: "numeric", year: "numeric" });
}

function fmtDateTime(ts) {
  return new Date(ts).toLocaleString("en-PH", { month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: true });
}

// ── Merge picker ──────────────────────────────────────────────────────────────
function MergeModal({ violator, suggestions, onMerge, onClose }) {
  const [search, setSearch] = useState("");
  const [fuzzyResults, setFuzzyResults] = useState(null);
  const [searching, setSearching] = useState(false);
  const [merging, setMerging] = useState(false);

  useEffect(() => {
    if (!search.trim()) return;
    const timer = setTimeout(async () => {
      setSearching(true);
      try {
        const found = await searchViolators(search.trim());
        setFuzzyResults((found ?? []).filter((v) => v.id !== violator.id));
      } catch {
        setFuzzyResults([]);
      } finally {
        setSearching(false);
      }
    }, 350);
    return () => clearTimeout(timer);
  }, [search, violator.id]);

  const results = search.trim() ? (fuzzyResults ?? []) : suggestions;

  const confirmMerge = async (loser) => {
    setMerging(true);
    try {
      await onMerge(loser);
    } finally {
      setMerging(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.72)", backdropFilter: "blur(6px)" }}
      onClick={(e) => { if (e.target === e.currentTarget && !merging) onClose(); }}>
      <div className="w-full max-w-md rounded-2xl overflow-hidden shadow-2xl flex flex-col"
        style={{ background: "var(--card)", border: "1px solid var(--border)", maxHeight: "85vh" }}>
        <div className="px-5 py-4 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
          <div className="text-sm font-semibold" style={{ color: "var(--foreground)" }}>Merge into {violator.full_name}</div>
          <div className="text-[11px] mt-0.5" style={{ color: "var(--muted-foreground)" }}>
            Pick the duplicate record — its citations move here, and it's deleted.
          </div>
        </div>
        <div className="px-5 py-3 flex-shrink-0">
          <div className="relative">
            <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "var(--muted-foreground)" }} />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search other violators…"
              className="w-full pl-9 pr-3 py-2.5 rounded-xl text-sm outline-none"
              style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }}
            />
          </div>
        </div>
        <div className="flex-1 overflow-y-auto px-5 pb-4 space-y-1.5" style={{ minHeight: 0 }}>
          {searching ? (
            <div className="flex items-center justify-center gap-2 py-8 text-sm" style={{ color: "var(--muted-foreground)" }}>
              <Loader2 size={16} className="animate-spin" /> Searching…
            </div>
          ) : results.length === 0 ? (
            <div className="py-8 text-center text-sm" style={{ color: "var(--muted-foreground)" }}>
              {search.trim() ? "No matches found" : "No likely duplicates found automatically — search by name above."}
            </div>
          ) : results.map((r) => (
            <div key={r.id} className="flex items-center justify-between gap-3 px-3 py-2.5 rounded-xl"
              style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
              <div className="min-w-0">
                <div className="text-[13px] font-medium truncate" style={{ color: "var(--foreground)" }}>{r.full_name}</div>
                <div className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>
                  {r.citation_count} citation{r.citation_count !== 1 ? "s" : ""}
                </div>
              </div>
              <button
                disabled={merging}
                onClick={() => confirmMerge(r)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-medium flex-shrink-0"
                style={{ background: "#f59e0b", color: "#0c0f16", cursor: merging ? "not-allowed" : "pointer", opacity: merging ? 0.6 : 1 }}>
                <GitMerge size={11} /> Merge
              </button>
            </div>
          ))}
        </div>
        <div className="px-5 py-4 flex-shrink-0" style={{ borderTop: "1px solid var(--border)" }}>
          <button onClick={onClose} disabled={merging} className="w-full px-4 py-2 rounded-xl text-sm font-medium"
            style={{ background: "var(--secondary)", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Detail view ───────────────────────────────────────────────────────────────
function ViolatorDetail({ violator, onBack, onMerged }) {
  const [citations, setCitations] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [showMerge, setShowMerge] = useState(false);
  const [dupeSuggestions, setDupeSuggestions] = useState([]);

  const refresh = async () => {
    setLoading(true);
    setLoadError("");
    try {
      const [citRes, dupeRes] = await Promise.all([
        getCitations({ violator: violator.id }),
        searchViolators(violator.full_name.replace(",", "")),
      ]);
      setCitations(citRes.results ?? citRes);
      setDupeSuggestions((dupeRes ?? []).filter((v) => v.id !== violator.id));
    } catch (err) {
      setLoadError(err.message || "Failed to load citation history.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    (async () => { await refresh(); })();
  }, [violator.id]);

  const handleMerge = async (loser) => {
    await mergeViolators(violator.id, loser.id);
    setShowMerge(false);
    await refresh();
    onMerged?.();
  };

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex items-center gap-3 px-6 h-14 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
        <button onClick={onBack} className="p-1.5 rounded-lg" style={{ color: "var(--muted-foreground)", background: "var(--secondary)" }}>
          <ChevronLeft size={16} />
        </button>
        <div className="flex-1 min-w-0">
          <div className="text-base font-bold truncate" style={{ color: "var(--foreground)" }}>{violator.full_name}</div>
          <div className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>
            {violator.citation_count} citation{violator.citation_count !== 1 ? "s" : ""}
            {violator.last_seen && ` · last seen ${fmtDate(violator.last_seen)}`}
          </div>
        </div>
        <button onClick={() => setShowMerge(true)}
          className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-medium"
          style={{ background: "rgba(245,158,11,0.12)", color: "#f59e0b", border: "1px solid rgba(245,158,11,0.25)" }}>
          <GitMerge size={12} /> Merge duplicate
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-6 space-y-4">
        {violator.aliases?.length > 0 && (
          <div className="rounded-xl px-4 py-3" style={{ background: "var(--secondary)", border: "1px solid var(--border)" }}>
            <div className="text-[10px] uppercase tracking-wide font-semibold mb-1.5" style={{ color: "var(--muted-foreground)" }}>
              Also known as (merged records)
            </div>
            <div className="flex flex-wrap gap-1.5">
              {violator.aliases.map((a, i) => (
                <span key={i} className="text-[11px] px-2 py-0.5 rounded-full" style={{ background: "var(--card)", border: "1px solid var(--border)", color: "var(--foreground)" }}>
                  {a}
                </span>
              ))}
            </div>
          </div>
        )}

        <div className="text-[10px] font-semibold uppercase tracking-wide" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
          Citation history · {citations.length}
        </div>

        {loading ? (
          <div className="flex items-center justify-center gap-2 py-16 text-sm" style={{ color: "var(--muted-foreground)" }}>
            <Loader2 size={18} className="animate-spin" /> Loading citations…
          </div>
        ) : loadError ? (
          <div className="flex flex-col items-center justify-center gap-2 py-16">
            <AlertTriangle size={22} style={{ color: "#ef4444" }} />
            <div className="text-sm" style={{ color: "var(--muted-foreground)" }}>{loadError}</div>
          </div>
        ) : citations.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 py-16">
            <FileText size={22} style={{ color: "var(--muted-foreground)" }} />
            <div className="text-sm" style={{ color: "var(--muted-foreground)" }}>No citations recorded.</div>
          </div>
        ) : (
          <div className="space-y-2">
            {citations.map((c) => (
              <div key={c.id} className="rounded-xl px-4 py-3" style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
                <div className="flex items-center justify-between gap-3 flex-wrap">
                  <div className="flex items-center gap-1.5 text-[11px]" style={{ color: "var(--muted-foreground)" }}>
                    <Calendar size={10} /> {fmtDateTime(c.created_at)}
                  </div>
                  <div className="flex items-center gap-1.5 text-[11px]" style={{ color: "var(--muted-foreground)" }}>
                    <Shield size={10} /> {c.officer_name || `Officer #${c.officer}`}
                  </div>
                </div>
                <div className="text-[12px] mt-1" style={{ color: "var(--foreground)" }}>
                  Entered as: {c.violator_name}
                </div>
                <div className="text-[11px] mt-0.5" style={{ color: "var(--muted-foreground)" }}>
                  {c.violator_barangay} · {(c.violation_labels ?? []).join(", ")}
                </div>
                {c.notes && (
                  <div className="text-[11px] mt-1.5 italic" style={{ color: "var(--muted-foreground)" }}>{c.notes}</div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {showMerge && (
        <MergeModal violator={violator} suggestions={dupeSuggestions} onMerge={handleMerge} onClose={() => setShowMerge(false)} />
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────
export function ResidentLog() {
  const [violators, setViolators] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState(null);

  const refresh = async () => {
    const data = await getViolators();
    setViolators(data.results ?? data);
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setLoadError("");
      try {
        await refresh();
      } catch (err) {
        if (!cancelled) setLoadError(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  if (selected) {
    return (
      <ViolatorDetail
        violator={selected}
        onBack={() => { setSelected(null); refresh().catch(() => {}); }}
        onMerged={refresh}
      />
    );
  }

  const filtered = violators.filter((v) => !search || v.full_name.toLowerCase().includes(search.toLowerCase()));
  const sorted = [...filtered].sort((a, b) => b.citation_count - a.citation_count);

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex items-center justify-between px-6 h-14 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
        <h1 className="text-xl font-bold" style={{ color: "var(--foreground)" }}>Violator Log</h1>
        <span className="text-xs" style={{ color: "var(--muted-foreground)" }}>{violators.length} on record</span>
      </div>

      <div className="flex-1 overflow-hidden flex flex-col px-6 py-4 gap-4">
        <div className="relative flex-shrink-0">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: "var(--muted-foreground)" }} />
          <input value={search} onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by name…"
            className="w-full pl-9 pr-3 py-2 rounded-lg text-sm outline-none"
            style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }} />
        </div>

        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="flex items-center justify-center gap-2 py-16 text-sm" style={{ color: "var(--muted-foreground)" }}>
              <Loader2 size={18} className="animate-spin" /> Loading violator log…
            </div>
          ) : loadError ? (
            <div className="flex flex-col items-center justify-center gap-2 py-16">
              <AlertTriangle size={22} style={{ color: "#ef4444" }} />
              <div className="text-sm" style={{ color: "var(--muted-foreground)" }}>{loadError}</div>
            </div>
          ) : sorted.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-2 py-16">
              <User size={22} style={{ color: "var(--muted-foreground)" }} />
              <div className="text-sm" style={{ color: "var(--muted-foreground)" }}>No violators on record yet.</div>
            </div>
          ) : (
            <div className="space-y-2">
              {sorted.map((v) => (
                <button key={v.id} onClick={() => setSelected(v)}
                  className="w-full flex items-center gap-3 px-4 py-3 rounded-xl text-left transition-all hover:bg-white/[0.02]"
                  style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
                  <div className="w-9 h-9 rounded-lg flex items-center justify-center text-xs font-bold flex-shrink-0"
                    style={{ background: "var(--secondary)", color: "var(--muted-foreground)" }}>
                    {v.first_name[0]}{v.last_name[0]}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-[13px] font-medium truncate" style={{ color: "var(--foreground)" }}>{v.full_name}</div>
                    {v.last_seen && (
                      <div className="text-[11px]" style={{ color: "var(--muted-foreground)" }}>Last seen {fmtDate(v.last_seen)}</div>
                    )}
                  </div>
                  <span className="text-[11px] font-semibold px-2.5 py-1 rounded-full flex-shrink-0"
                    style={{ background: "rgba(239,68,68,0.1)", color: "#ef4444" }}>
                    {v.citation_count} citation{v.citation_count !== 1 ? "s" : ""}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
