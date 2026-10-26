import { useEffect, useState } from "react";
import { userMessage, api, StatuteItem } from "../api/client";

/** Fallback so the dropdown is complete even if /statutes/acts fails */
const FALLBACK_ACTS = [
  "BNS",
  "BNSS",
  "BSA",
  "Constitution of India",
  "Information Technology Act, 2000",
  "Maternity Benefit Act 1961",
  "Sexual Harassment of Women At Workplace Act,2013",
  "The Code on Wages, 2019",
  "The Minimum Wages Act 1948",
  "Payment Of Wages 1937",
  "The Payment Of Gratuity Act 1972",
  "The Apprentices Act1961",
  "The Industrial Disputes Act, 1947",
  "The Industrial Employment",
  "The Insolvency And Bankruptcy Code, 2016",
  "Llp Act 2008",
  "The Inter-State Migrant Workmen Regulation Of Employment And Conditions  Of Service Act 1979",
  "A2013-14",
  "wages 2017",
];

export default function StatutesPage() {
  const [q, setQ] = useState("");
  const [act, setAct] = useState("All");
  const [actOptions, setActOptions] = useState<string[]>(FALLBACK_ACTS);
  const [results, setResults] = useState<StatuteItem[]>([]);
  const [browse, setBrowse] = useState<StatuteItem[]>([]);
  const [browseLoading, setBrowseLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [actsNote, setActsNote] = useState("");
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);

  useEffect(() => {
    api
      .listStatuteActs()
      .then((r) => {
        if (r.acts?.length) {
          setActOptions(r.acts);
          setActsNote(`${r.acts.length} acts available`);
        } else {
          setActsNote("Showing available acts");
        }
      })
      .catch(() => {
        setActOptions(FALLBACK_ACTS);
        setActsNote("The full act list is temporarily unavailable. You can still try searching the listed acts.");
      });
  }, []);

  useEffect(() => {
    setResults([]);
    setSelectedIndex(null);
    setErr(null);
    if (act === "All") {
      setBrowse([]);
      return;
    }
    setBrowseLoading(true);
    api
      .browseStatute(act)
      .then((r) => {
        setBrowse(r.sections || []);
        setSelectedIndex((r.sections || []).length ? 0 : null);
        if (!(r.sections || []).length) setErr("No browse headings are available for this act yet. Try search.");
      })
      .catch((e: unknown) => {
        setBrowse([]);
        setErr(userMessage(e instanceof Error ? e.message : String(e)));
      })
      .finally(() => setBrowseLoading(false));
  }, [act]);

  async function search() {
    if (!q.trim()) return;
    setLoading(true);
    setErr(null);
    try {
      const r = await api.searchStatutes(q.trim(), act);
      setResults(r.results || []);
      setSelectedIndex((r.results || []).length ? 0 : null);
      if (!(r.results || []).length) setErr("No matching sections for this query.");
    } catch (e: unknown) {
      setErr(userMessage(e instanceof Error ? e.message : String(e)));
    } finally {
      setLoading(false);
    }
  }

  const visibleItems = results.length ? results : browse;
  const modeLabel = results.length ? "Search Results" : act === "All" ? "Select An Act" : "Browse Headings";

  return (
    <>
      <h1>Statute Library</h1>
      <p className="page-kicker">
        Browse an act by clickable headings, or search across sections when you already know the issue, article, or phrase.
      </p>
      {actsNote && (
        <p style={{ color: "#75552d", fontSize: "0.85rem", fontWeight: 700 }}>{actsNote}</p>
      )}
      <div className="panel">
        <div className="toolbar-row">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search by section, article, phrase, or issue"
            onKeyDown={(e) => e.key === "Enter" && search()}
          />
          <select value={act} onChange={(e) => setAct(e.target.value)} style={{ maxWidth: "100%" }}>
            <option value="All">All acts</option>
            {actOptions.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
          <button className="btn" onClick={search} disabled={loading}>
            {loading ? "Searching…" : "Search"}
          </button>
        </div>
      </div>
      {err && <div className="panel" style={{ color: "var(--danger)" }}>{err}</div>}

      {act === "All" && !results.length && (
        <div className="panel">
          <h2>Choose An Act</h2>
          <div className="topic-grid">
            {actOptions.map((a) => (
              <button key={a} className="topic-chip" onClick={() => setAct(a)}>
                {a}
              </button>
            ))}
          </div>
        </div>
      )}

      {browseLoading && <div className="panel">Loading headings for {act}…</div>}

      {visibleItems.length > 0 && (
        <div className="statute-shell">
          <div>
            <div className="section-ref" style={{ margin: "0 0 0.75rem 0.25rem" }}>{modeLabel}</div>
            {visibleItems.map((r, i) => (
              <div
                key={`${r.act}-${r.section}-${i}`}
                className="panel statute-result"
                style={{
                  borderColor: selectedIndex === i ? "var(--brass)" : undefined,
                  marginBottom: "0.75rem",
                }}
                onClick={() => setSelectedIndex(i)}
              >
                <div className="section-ref">{r.act} · Section {r.section}</div>
                <h2 style={{ fontSize: "1rem", marginTop: 4 }}>
                  {r.chapter_title || "Statutory provision"}
                </h2>
                <p className="snippet">
                  {r.text.slice(0, 210)}
                  {r.text.length > 210 ? "..." : ""}
                </p>
              </div>
            ))}
          </div>

          {selectedIndex !== null && visibleItems[selectedIndex] && (
            <div className="panel" style={{ position: "sticky", top: 20 }}>
              <div className="section-ref">
                {visibleItems[selectedIndex].act} · Section {visibleItems[selectedIndex].section}
              </div>
              <h2 style={{ fontSize: "1.25rem", marginTop: 6 }}>
                {visibleItems[selectedIndex].chapter_title || "Selected provision"}
              </h2>
              <div className="stage-body" style={{ maxHeight: 620 }}>
                {visibleItems[selectedIndex].text}
              </div>
            </div>
          )}
        </div>
      )}
    </>
  );
}
