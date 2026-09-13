import { useState, useEffect, useMemo, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Search, User, ChevronLeft, Loader2, AlertTriangle, GitMerge, Shield, Calendar, FileText,
  X, Filter, ChevronDown, Users, Repeat, Award, MapPin,
} from "lucide-react";
import { getViolators, getCitations, searchViolators, mergeViolators } from "./api";
import {
  VIOLATION_TYPES, UNKNOWN_VIOLATION_TYPE, resolveViolationType, loadViolationTypeIndex, violationChipStyle,
} from "./constants/violationTypes.js";
import { TypeFilterDropdown } from "./TypeFilterDropdown";

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

// ── Detail view helpers ──────────────────────────────────────────────────────
const DETAIL_SORT_OPTIONS = [
  { key: "latest", label: "Latest first" },
  { key: "oldest", label: "Oldest first" },
];

// A citation's resolved, deduped violation types — unknown/unmapped
// violations (e.g. a stray curfew row) are dropped from display here, same
// as the list page's icon cluster: safe fallback means "don't crash or
// mislabel," not "show a mystery chip."
function resolveCitationTypes(c) {
  const labels = c.violation_labels ?? [];
  const seen = new Map();
  (c.violations ?? []).forEach((typeId, i) => {
    const resolved = resolveViolationType({ id: typeId, label: labels[i] });
    if (resolved !== UNKNOWN_VIOLATION_TYPE && !seen.has(resolved.code)) seen.set(resolved.code, resolved);
  });
  return [...seen.values()];
}

function primaryType(c) {
  return resolveCitationTypes(c)[0] ?? UNKNOWN_VIOLATION_TYPE;
}

function aliasDiffers(citation, violator) {
  const entered = (citation.violator_name || "").trim().toLowerCase();
  const canonical = (violator.full_name || "").trim().toLowerCase();
  return Boolean(entered) && entered !== canonical;
}

function monthLabel(ts) {
  return new Date(ts).toLocaleDateString("en-PH", { month: "long", year: "numeric" });
}

function groupByMonth(list) {
  const groups = new Map();
  for (const c of list) {
    const label = monthLabel(c.created_at);
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(c);
  }
  return [...groups.entries()];
}

function DetailSkeletonEntry() {
  return (
    <div className="relative pl-6 pb-4" aria-hidden="true">
      <div className="absolute left-[3px] top-1.5 w-2.5 h-2.5 rounded-full" style={{ background: "var(--secondary)" }} />
      <div className="rounded-xl px-4 py-3 animate-pulse space-y-2.5" style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
        <div className="h-3 w-1/3 rounded" style={{ background: "var(--secondary)" }} />
        <div className="h-5 w-2/3 rounded-full" style={{ background: "var(--secondary)" }} />
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
  const [searchParams, setSearchParams] = useSearchParams();

  const sortParam = searchParams.get("csort") === "oldest" ? "oldest" : "latest";
  const typeFilter = useMemo(
    () => new Set((searchParams.get("ctype") || "").split(",").filter(Boolean)),
    [searchParams]
  );

  const refresh = async () => {
    setLoading(true);
    setLoadError("");
    try {
      const [, citRes, dupeRes] = await Promise.all([
        loadViolationTypeIndex().catch(() => {}),
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

  const toggleTypeFilter = (code) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      const codes = new Set((next.get("ctype") || "").split(",").filter(Boolean));
      if (codes.has(code)) codes.delete(code); else codes.add(code);
      if (codes.size) next.set("ctype", [...codes].join(",")); else next.delete("ctype");
      return next;
    }, { replace: true });
  };

  const setSort = (key) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (key === "latest") next.delete("csort"); else next.set("csort", key);
      return next;
    }, { replace: true });
  };

  const clearFilters = () => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("ctype");
      return next;
    }, { replace: true });
  };

  // Summary figures, derived from the citations already fetched above.
  const totalCitations = citations.length;
  const typeTallies = new Map();
  citations.forEach((c) => {
    resolveCitationTypes(c).forEach((t) => {
      const entry = typeTallies.get(t.code) ?? { type: t, count: 0 };
      entry.count += 1;
      typeTallies.set(t.code, entry);
    });
  });
  const mostCommon = [...typeTallies.values()].reduce((best, e) => (!best || e.count > best.count ? e : best), null);
  const now = new Date();
  const thisMonthCount = citations.filter((c) => {
    const d = new Date(c.created_at);
    return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth();
  }).length;

  const filteredCitations = citations.filter((c) => {
    if (typeFilter.size === 0) return true;
    const codes = resolveCitationTypes(c).map((t) => t.code);
    return codes.some((code) => typeFilter.has(code));
  });
  const sortedCitations = [...filteredCitations].sort((a, b) => {
    const diff = new Date(a.created_at) - new Date(b.created_at);
    return sortParam === "oldest" ? diff : -diff;
  });
  const groups = groupByMonth(sortedCitations);
  const hasActiveFilters = typeFilter.size > 0;

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex items-center gap-3 px-6 py-4 flex-shrink-0 flex-wrap" style={{ borderBottom: "1px solid var(--border)" }}>
        <button onClick={onBack}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium flex-shrink-0"
          style={{ color: "var(--muted-foreground)", background: "var(--secondary)" }}>
          <ChevronLeft size={14} /> Back to log
        </button>
        <div className="w-11 h-11 rounded-xl flex items-center justify-center text-sm font-bold flex-shrink-0" style={avatarStyle(violator.full_name)}>
          {violator.first_name[0]}{violator.last_name[0]}
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-base font-bold truncate" style={{ color: "var(--foreground)" }}>{violator.full_name}</div>
          {violator.last_seen && (
            <div className="text-[11px]" style={{ color: "var(--muted-foreground)" }} title={fmtDate(violator.last_seen)}>
              Last seen {relativeTime(violator.last_seen)}
            </div>
          )}
        </div>
        <div className="text-right flex-shrink-0">
          <div className="text-lg font-bold tabular-nums leading-none" style={{ color: "var(--foreground)" }}>{violator.citation_count}</div>
          <div className="text-[10px] uppercase tracking-wide font-semibold mt-0.5" style={{ color: "var(--muted-foreground)" }}>
            Citation{violator.citation_count !== 1 ? "s" : ""}
          </div>
        </div>
        <button onClick={() => setShowMerge(true)} aria-label="Merge duplicate record"
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-medium flex-shrink-0"
          style={{ background: "transparent", color: "var(--muted-foreground)", border: "1px solid var(--border)" }}>
          <GitMerge size={11} /> Merge duplicate
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

        {!loading && !loadError && totalCitations > 0 && (
          <div className="flex flex-wrap rounded-xl overflow-hidden" style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
            <div className="flex-1 min-w-[120px] px-4 py-3" style={{ borderRight: "1px solid var(--border)" }}>
              <div className="text-[10px] uppercase tracking-wide font-semibold" style={{ color: "var(--muted-foreground)" }}>Total citations</div>
              <div className="text-lg font-bold tabular-nums mt-1" style={{ color: "var(--foreground)" }}>{totalCitations}</div>
            </div>
            <div className="flex-1 min-w-[160px] px-4 py-3" style={{ borderRight: "1px solid var(--border)" }}>
              <div className="text-[10px] uppercase tracking-wide font-semibold" style={{ color: "var(--muted-foreground)" }}>Most common violation</div>
              {mostCommon ? (
                <div className="flex items-center gap-1.5 mt-1">
                  <mostCommon.type.icon size={13} style={{ color: `var(--violation-${mostCommon.type.code}-dot)` }} />
                  <div className="text-[13px] font-semibold truncate" style={{ color: "var(--foreground)" }}>{mostCommon.type.label}</div>
                </div>
              ) : (
                <div className="text-lg font-bold mt-1" style={{ color: "var(--muted-foreground)" }}>—</div>
              )}
            </div>
            <div className="flex-1 min-w-[100px] px-4 py-3">
              <div className="text-[10px] uppercase tracking-wide font-semibold" style={{ color: "var(--muted-foreground)" }}>This month</div>
              <div className="text-lg font-bold tabular-nums mt-1" style={{ color: "var(--foreground)" }}>{thisMonthCount}</div>
            </div>
          </div>
        )}

        {!loading && !loadError && totalCitations > 0 && (
          <div className="flex items-center gap-2 flex-wrap">
            <TypeFilterDropdown selected={typeFilter} onToggle={toggleTypeFilter} onClear={clearFilters} />

            <div className="flex items-center rounded-lg overflow-hidden flex-shrink-0 ml-auto" style={{ border: "1px solid var(--border)" }}>
              {DETAIL_SORT_OPTIONS.map((opt) => (
                <button
                  key={opt.key}
                  onClick={() => setSort(opt.key)}
                  aria-pressed={sortParam === opt.key}
                  className="px-3 py-1.5 text-[12px] font-medium"
                  style={{
                    background: sortParam === opt.key ? "var(--primary)" : "var(--secondary)",
                    color: sortParam === opt.key ? "var(--primary-foreground)" : "var(--muted-foreground)",
                  }}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="text-[10px] font-semibold uppercase tracking-wide" style={{ color: "var(--muted-foreground)", fontFamily: "'DM Mono', monospace" }}>
          Citation history · {totalCitations}
        </div>

        {loading ? (
          <div aria-busy="true" aria-label="Loading citation history">
            {Array.from({ length: 4 }).map((_, i) => <DetailSkeletonEntry key={i} />)}
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
        ) : sortedCitations.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-3 py-16">
            <Filter size={22} style={{ color: "var(--muted-foreground)" }} />
            <div className="text-sm" style={{ color: "var(--muted-foreground)" }}>No citations match the selected type filter.</div>
            {hasActiveFilters && (
              <button onClick={clearFilters} className="text-[12px] font-medium px-3 py-1.5 rounded-lg"
                style={{ background: "var(--secondary)", color: "var(--primary)", border: "1px solid var(--border)" }}>
                Clear filters
              </button>
            )}
          </div>
        ) : (
          <div>
            {groups.map(([label, group], groupIdx) => (
              <div key={label}>
                <div className="text-[10px] font-semibold uppercase tracking-wide mb-2" style={{ color: "var(--muted-foreground)" }}>
                  {label}
                </div>
                <div>
                  {group.map((c, idx) => {
                    const isLast = groupIdx === groups.length - 1 && idx === group.length - 1;
                    const dot = primaryType(c);
                    const types = resolveCitationTypes(c);
                    return (
                      <div key={c.id} className="relative pl-6 pb-4">
                        {!isLast && (
                          <div className="absolute left-[3px] top-3 bottom-0 w-px" style={{ background: "var(--border)" }} />
                        )}
                        <div className="absolute left-0 top-1.5 w-2.5 h-2.5 rounded-full" style={{ background: `var(--violation-${dot.code}-dot)` }} />

                        <div className="rounded-xl px-4 py-3" style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
                          <div className="flex items-start justify-between gap-3 flex-wrap">
                            <div className="flex items-center gap-1.5 text-[12px] font-medium" style={{ color: "var(--foreground)" }}>
                              <Calendar size={11} style={{ color: "var(--muted-foreground)" }} />
                              {fmtDateTime(c.created_at)}
                              <span className="text-[11px] font-normal" style={{ color: "var(--muted-foreground)" }}>· {relativeTime(c.created_at)}</span>
                            </div>
                            <div className="flex items-center gap-1 text-[11px] flex-shrink-0 px-2 py-0.5 rounded-full"
                              style={{ color: "var(--muted-foreground)", background: "var(--secondary)" }}>
                              <Shield size={10} /> {c.officer_name || `Officer #${c.officer}`}
                            </div>
                          </div>

                          <div className="flex flex-wrap items-center gap-1.5 mt-2">
                            {types.map((t) => {
                              const Icon = t.icon;
                              return (
                                <span key={t.code} className="flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium border" style={violationChipStyle(t)}>
                                  <Icon size={10} /> {t.short}
                                </span>
                              );
                            })}
                            {c.violator_barangay && (
                              <span className="flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px]"
                                style={{ color: "var(--muted-foreground)", background: "transparent", border: "1px dashed var(--border)" }}>
                                <MapPin size={10} /> {c.violator_barangay}
                              </span>
                            )}
                          </div>

                          {aliasDiffers(c, violator) && (
                            <div className="flex items-center gap-1.5 mt-2 px-2.5 py-1.5 rounded-lg text-[11px]"
                              style={{ background: "rgba(245,158,11,0.1)", color: "#f59e0b", border: "1px dashed rgba(245,158,11,0.3)" }}>
                              <GitMerge size={11} /> Entered as: <span className="font-medium">{c.violator_name}</span>
                            </div>
                          )}

                          {c.notes && (
                            <div className="text-[11px] mt-2 italic" style={{ color: "var(--muted-foreground)" }}>{c.notes}</div>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
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

// ── List page helpers ────────────────────────────────────────────────────────
function useDebouncedValue(value, delay) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

function relativeTime(ts) {
  if (!ts) return "";
  const sec = Math.max(0, Math.floor((Date.now() - new Date(ts).getTime()) / 1000));
  if (sec < 60) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} minute${min !== 1 ? "s" : ""} ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} hour${hr !== 1 ? "s" : ""} ago`;
  const day = Math.floor(hr / 24);
  if (day < 7) return `${day} day${day !== 1 ? "s" : ""} ago`;
  const week = Math.floor(day / 7);
  if (week < 5) return `${week} week${week !== 1 ? "s" : ""} ago`;
  const month = Math.floor(day / 30);
  if (month < 12) return `${month} month${month !== 1 ? "s" : ""} ago`;
  const year = Math.floor(day / 365);
  return `${year} year${year !== 1 ? "s" : ""} ago`;
}

// Deterministic avatar tint from the violator's name — same name always
// lands on the same slot, so near-identical names ("Angeles, Martin Tuble"
// vs "Angeles, Mathew Tuble") are still visually distinct at a glance.
function hashString(str) {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = (hash << 5) - hash + str.charCodeAt(i);
    hash |= 0;
  }
  return Math.abs(hash);
}
const AVATAR_SLOTS = 6;
function avatarStyle(name) {
  const slot = hashString(name || "") % AVATAR_SLOTS;
  return { background: `var(--avatar-${slot}-bg)`, color: `var(--avatar-${slot}-text)` };
}

function citationTier(count) {
  if (count >= 4) return "high";
  if (count >= 2) return "mid";
  return "low";
}
const TIER_STYLE = {
  low: { background: "var(--secondary)", color: "var(--muted-foreground)" },
  mid: { background: "rgba(245,158,11,0.12)", color: "#f59e0b" },
  high: { background: "rgba(239,68,68,0.1)", color: "#ef4444" },
};

const SORT_OPTIONS = [
  { key: "citations", label: "Most citations" },
  { key: "recent", label: "Recently cited" },
  { key: "name", label: "Name A–Z" },
];
const FILTERABLE_TYPES = Object.values(VIOLATION_TYPES);

// Pages through GET /citations/ once (it's paginated, 50/page) to build a
// violator id -> Set<violation type code> map. /violators/ has no per-row
// breakdown of violation types, so this is the only way to show "what has
// this person been cited for" without a backend change — fine at barangay
// scale, but the cost grows with total citation volume, not list size.
async function fetchAllCitations() {
  let page = 1;
  let all = [];
  while (true) {
    const res = await getCitations({ page: String(page) });
    const results = res?.results ?? res ?? [];
    all = all.concat(results);
    if (!res?.next || results.length === 0) break;
    page += 1;
  }
  return all;
}

function buildViolationsByViolator(citations) {
  const map = new Map();
  for (const c of citations) {
    if (c.violator == null) continue;
    const codes = map.get(c.violator) ?? new Set();
    const labels = c.violation_labels ?? [];
    (c.violations ?? []).forEach((typeId, i) => {
      const resolved = resolveViolationType({ id: typeId, label: labels[i] });
      if (resolved !== UNKNOWN_VIOLATION_TYPE) codes.add(resolved.code);
    });
    map.set(c.violator, codes);
  }
  return map;
}

function ViolationIconCluster({ codes }) {
  const types = FILTERABLE_TYPES.filter((t) => codes?.has(t.code));
  if (types.length === 0) return null;
  return (
    <div className="flex items-center gap-1 flex-shrink-0" aria-label={`Cited for: ${types.map((t) => t.label).join(", ")}`}>
      {types.map((t) => {
        const Icon = t.icon;
        return (
          <span key={t.code} title={t.label} className="w-5 h-5 rounded-md flex items-center justify-center" style={violationChipStyle(t)}>
            <Icon size={11} />
          </span>
        );
      })}
    </div>
  );
}

function SkeletonRow() {
  return (
    <div className="w-full flex items-center gap-3 px-4 py-3 rounded-xl animate-pulse" aria-hidden="true"
      style={{ background: "var(--card)", border: "1px solid var(--border)", borderLeft: "3px solid transparent" }}>
      <div className="w-9 h-9 rounded-lg flex-shrink-0" style={{ background: "var(--secondary)" }} />
      <div className="flex-1 min-w-0 space-y-2">
        <div className="h-3 w-2/5 rounded" style={{ background: "var(--secondary)" }} />
        <div className="h-2.5 w-1/4 rounded" style={{ background: "var(--secondary)" }} />
      </div>
      <div className="h-5 w-20 rounded-full flex-shrink-0" style={{ background: "var(--secondary)" }} />
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────
export function ResidentLog() {
  const [violators, setViolators] = useState([]);
  const [violationsByViolator, setViolationsByViolator] = useState(new Map());
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [selected, setSelected] = useState(null);
  const [searchInput, setSearchInput] = useState("");
  const [searchParams, setSearchParams] = useSearchParams();
  const searchRef = useRef(null);

  const debouncedSearch = useDebouncedValue(searchInput, 250);
  const sortKey = searchParams.get("sort") || "citations";
  const typeFilter = useMemo(
    () => new Set((searchParams.get("type") || "").split(",").filter(Boolean)),
    [searchParams]
  );

  const refresh = async () => {
    const violatorsPromise = getViolators();
    const citationsPromise = loadViolationTypeIndex().then(() => fetchAllCitations()).catch(() => []);
    const [violatorsData, citations] = await Promise.all([violatorsPromise, citationsPromise]);
    setViolators(violatorsData.results ?? violatorsData);
    setViolationsByViolator(buildViolationsByViolator(citations));
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

  // "/" focuses search, unless focus is already in a text field.
  useEffect(() => {
    function handleKeydown(e) {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      const tag = document.activeElement?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || document.activeElement?.isContentEditable) return;
      e.preventDefault();
      searchRef.current?.focus();
    }
    window.addEventListener("keydown", handleKeydown);
    return () => window.removeEventListener("keydown", handleKeydown);
  }, []);

  const toggleTypeFilter = (code) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      const codes = new Set((next.get("type") || "").split(",").filter(Boolean));
      if (codes.has(code)) codes.delete(code); else codes.add(code);
      if (codes.size) next.set("type", [...codes].join(",")); else next.delete("type");
      return next;
    }, { replace: true });
  };

  const setSortKey = (key) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (key === "citations") next.delete("sort"); else next.set("sort", key);
      return next;
    }, { replace: true });
  };

  const clearFilters = () => {
    setSearchInput("");
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("type");
      return next;
    }, { replace: true });
  };

  // Type-only clear for the filter dropdown's own "Clear all" — clearFilters
  // above also wipes the search box, which is right for its own "no matches"
  // empty-state button but wrong for a control scoped to just the type list.
  const clearTypeFilter = () => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("type");
      return next;
    }, { replace: true });
  };

  if (selected) {
    return (
      <ViolatorDetail
        violator={selected}
        onBack={() => { setSelected(null); refresh().catch(() => {}); }}
        onMerged={refresh}
      />
    );
  }

  const totalViolators = violators.length;
  const repeatOffenders = violators.filter((v) => v.citation_count >= 2).length;
  const mostCited = violators.reduce((best, v) => (!best || v.citation_count > best.citation_count ? v : best), null);

  const filtered = violators.filter((v) => {
    if (debouncedSearch && !v.full_name.toLowerCase().includes(debouncedSearch.toLowerCase())) return false;
    if (typeFilter.size > 0) {
      const codes = violationsByViolator.get(v.id);
      if (!codes || ![...typeFilter].some((code) => codes.has(code))) return false;
    }
    return true;
  });

  const sorted = [...filtered].sort((a, b) => {
    if (sortKey === "name") return a.full_name.localeCompare(b.full_name);
    if (sortKey === "recent") return new Date(b.last_seen ?? 0) - new Date(a.last_seen ?? 0);
    return b.citation_count - a.citation_count;
  });

  const hasActiveFilters = Boolean(debouncedSearch) || typeFilter.size > 0;

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex items-center justify-between px-6 h-14 flex-shrink-0" style={{ borderBottom: "1px solid var(--border)" }}>
        <h1 className="text-xl font-bold" style={{ color: "var(--foreground)" }}>Violator Log</h1>
        <div className="text-right">
          <div className="text-lg font-bold tabular-nums leading-none" style={{ color: "var(--foreground)" }}>{totalViolators}</div>
          <div className="text-[10px] uppercase tracking-wide font-semibold mt-0.5" style={{ color: "var(--muted-foreground)" }}>On record</div>
        </div>
      </div>

      <div className="flex-1 overflow-hidden flex flex-col px-6 py-4 gap-4">
        {/* Summary figures */}
        <div className="flex flex-wrap flex-shrink-0 rounded-xl overflow-hidden" style={{ background: "var(--card)", border: "1px solid var(--border)" }}>
          <div className="flex-1 min-w-[140px] px-4 py-3" style={{ borderRight: "1px solid var(--border)" }}>
            <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide font-semibold" style={{ color: "var(--muted-foreground)" }}>
              <Users size={11} /> Total violators
            </div>
            <div className="text-lg font-bold tabular-nums mt-1" style={{ color: "var(--foreground)" }}>{totalViolators}</div>
          </div>
          <div className="flex-1 min-w-[140px] px-4 py-3" style={{ borderRight: "1px solid var(--border)" }}>
            <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide font-semibold" style={{ color: "var(--muted-foreground)" }}>
              <Repeat size={11} /> Repeat offenders
            </div>
            <div className="text-lg font-bold tabular-nums mt-1" style={{ color: repeatOffenders > 0 ? "#f59e0b" : "var(--foreground)" }}>
              {repeatOffenders}
            </div>
          </div>
          <div className="flex-1 min-w-[180px] px-4 py-3">
            <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide font-semibold" style={{ color: "var(--muted-foreground)" }}>
              <Award size={11} /> Most cited
            </div>
            {mostCited ? (
              <div className="mt-1">
                <div className="text-[13px] font-semibold truncate" style={{ color: "var(--foreground)" }}>{mostCited.full_name}</div>
                <div className="text-[11px] tabular-nums" style={{ color: "var(--muted-foreground)" }}>
                  {mostCited.citation_count} citation{mostCited.citation_count !== 1 ? "s" : ""}
                </div>
              </div>
            ) : (
              <div className="text-lg font-bold mt-1" style={{ color: "var(--muted-foreground)" }}>—</div>
            )}
          </div>
        </div>

        {/* Search + sort */}
        <div className="flex items-center gap-2 flex-shrink-0 flex-wrap">
          <div className="relative flex-1 min-w-[180px]">
            <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" style={{ color: "var(--muted-foreground)" }} />
            <input
              ref={searchRef}
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search by name… (press / to focus)"
              aria-label="Search violators by name"
              className="w-full pl-9 pr-8 py-2 rounded-lg text-sm outline-none"
              style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }}
            />
            {searchInput && (
              <button
                onClick={() => setSearchInput("")}
                aria-label="Clear search"
                className="absolute right-2 top-1/2 -translate-y-1/2 p-1 rounded-md"
                style={{ color: "var(--muted-foreground)" }}
              >
                <X size={13} />
              </button>
            )}
          </div>

          <div className="relative flex-shrink-0">
            <select
              value={sortKey}
              onChange={(e) => setSortKey(e.target.value)}
              aria-label="Sort violators"
              className="appearance-none pl-3 pr-8 py-2 rounded-lg text-sm outline-none"
              style={{ background: "var(--secondary)", border: "1px solid var(--border)", color: "var(--foreground)" }}
            >
              {SORT_OPTIONS.map((opt) => <option key={opt.key} value={opt.key}>{opt.label}</option>)}
            </select>
            <ChevronDown size={13} className="absolute right-2.5 top-1/2 -translate-y-1/2 pointer-events-none" style={{ color: "var(--muted-foreground)" }} />
          </div>
        </div>

        {/* Type filter — shared with the Violations tab's, so the two
            can't drift into different rules/looks again. */}
        <div className="flex-shrink-0">
          <TypeFilterDropdown selected={typeFilter} onToggle={toggleTypeFilter} onClear={clearTypeFilter} />
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="space-y-2" aria-busy="true" aria-label="Loading violator log">
              {Array.from({ length: 6 }).map((_, i) => <SkeletonRow key={i} />)}
            </div>
          ) : loadError ? (
            <div className="flex flex-col items-center justify-center gap-2 py-16">
              <AlertTriangle size={22} style={{ color: "#ef4444" }} />
              <div className="text-sm" style={{ color: "var(--muted-foreground)" }}>{loadError}</div>
            </div>
          ) : violators.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-2 py-16">
              <User size={22} style={{ color: "var(--muted-foreground)" }} />
              <div className="text-sm" style={{ color: "var(--muted-foreground)" }}>No violators on record yet.</div>
            </div>
          ) : sorted.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-3 py-16">
              <Search size={22} style={{ color: "var(--muted-foreground)" }} />
              <div className="text-sm" style={{ color: "var(--muted-foreground)" }}>No matches for your search or filters.</div>
              {hasActiveFilters && (
                <button onClick={clearFilters} className="text-[12px] font-medium px-3 py-1.5 rounded-lg"
                  style={{ background: "var(--secondary)", color: "var(--primary)", border: "1px solid var(--border)" }}>
                  Clear filters
                </button>
              )}
            </div>
          ) : (
            <div className="space-y-2">
              {sorted.map((v) => {
                const tier = citationTier(v.citation_count);
                return (
                  <button
                    key={v.id}
                    onClick={() => setSelected(v)}
                    className="violator-row w-full flex items-center gap-3 px-4 py-3 rounded-xl text-left outline-none"
                    style={{
                      background: "var(--card)",
                      border: "1px solid var(--border)",
                      borderLeft: `3px solid ${tier === "high" ? "#ef4444" : "transparent"}`,
                    }}
                    onMouseEnter={(e) => { e.currentTarget.style.background = "var(--secondary)"; }}
                    onMouseLeave={(e) => { e.currentTarget.style.background = "var(--card)"; }}
                  >
                    <div className="w-9 h-9 rounded-lg flex items-center justify-center text-xs font-bold flex-shrink-0"
                      style={avatarStyle(v.full_name)}>
                      {v.first_name[0]}{v.last_name[0]}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="text-[13px] font-medium truncate" style={{ color: "var(--foreground)" }}>{v.full_name}</div>
                      {v.last_seen && (
                        <div className="text-[11px]" style={{ color: "var(--muted-foreground)" }} title={fmtDate(v.last_seen)}>
                          Last seen {relativeTime(v.last_seen)}
                        </div>
                      )}
                    </div>
                    <ViolationIconCluster codes={violationsByViolator.get(v.id)} />
                    <span className="text-[11px] font-semibold px-2.5 py-1 rounded-full flex-shrink-0 tabular-nums" style={TIER_STYLE[tier]}>
                      {v.citation_count} citation{v.citation_count !== 1 ? "s" : ""}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
