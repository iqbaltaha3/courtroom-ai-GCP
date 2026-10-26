import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { userMessage, libraryApi, LibraryCase } from "../api/client";

import AgentDetails, { useAgentDetails } from "../components/AgentDetails";

export default function LibraryPage() {
  const [cases, setCases] = useState<LibraryCase[]>([]);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<LibraryCase | null>(null);
  const [reports, setReports] = useState<Record<string, string>>({});
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const agentDetails = useAgentDetails(selected?.case_id || null);
  const navigate = useNavigate();

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const r = await libraryApi.list(q || undefined, status || undefined);
      setCases(r.cases);
    } catch (e: unknown) {
      setError(userMessage(e instanceof Error ? e.message : String(e)));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function openCase(c: LibraryCase) {
    setSelected(c);
    setEditTitle(c.title);
    setReports({});
    setOpenKey(null);
    try {
      const r = await libraryApi.reports(c.case_id);
      setReports(r.reports || {});
    } catch {
      setReports({});
    }
  }

  async function saveTitle() {
    if (!selected || !editTitle.trim()) return;
    const updated = await libraryApi.update(selected.case_id, { title: editTitle.trim() });
    setSelected(updated);
    setCases((list) => list.map((x) => (x.case_id === updated.case_id ? updated : x)));
  }

  async function removeCase(id: string) {
    if (!confirm("Delete this case from the library? This cannot be undone.")) return;
    await libraryApi.remove(id);
    if (selected?.case_id === id) {
      setSelected(null);
      setReports({});
    }
    load();
  }

  return (
    <>
      <h1>Case Library</h1>
      <p style={{ color: "var(--ink-muted)" }}>
        Archive of saved simulations. Open a case to review reports, rename it, or continue in Case Chat.
      </p>

      <div className="panel">
        <div style={{ display: "grid", gridTemplateColumns: "1fr 160px auto", gap: "0.75rem" }}>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search title, verdict, notes…"
            onKeyDown={(e) => e.key === "Enter" && load()}
          />
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
            <option value="created">Created</option>
            <option value="running">Running</option>
          </select>
          <button className="btn" onClick={load} disabled={loading}>
            {loading ? "Loading…" : "Search"}
          </button>
        </div>
      </div>

      {error && (
        <div className="panel" style={{ color: "#f0c0c0" }}>
          {error}
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: selected ? "1fr 1.2fr" : "1fr", gap: "1rem" }}>
        <div>
          {cases.length === 0 && !loading && (
            <div className="locked">No cases in the library yet. Run a simulation to archive one.</div>
          )}
          {cases.map((c) => (
            <div
              key={c.case_id}
              className="panel"
              style={{
                cursor: "pointer",
                borderColor: selected?.case_id === c.case_id ? "var(--brass)" : undefined,
              }}
              onClick={() => openCase(c)}
            >
              <div style={{ display: "flex", justifyContent: "space-between", gap: "0.5rem" }}>
                <h2 style={{ fontSize: "1rem", margin: 0 }}>{c.title}</h2>
                <span className={`badge ${c.status === "completed" ? "done" : c.status === "failed" ? "error" : "pending"}`}>
                  {c.status}
                </span>
              </div>
              <div style={{ fontSize: "0.85rem", color: "var(--ink-muted)", marginTop: 6 }}>
                {c.selected_judge ? c.selected_judge.replace(/_/g, " ") : "No judge"} ·{" "}
                {c.completed_at || c.updated_at || c.created_at || ""}
              </div>
              {c.verdict && (
                <div style={{ marginTop: 8, color: "var(--brass-bright)", fontFamily: "var(--font-serif)" }}>
                  Verdict: {c.verdict}
                  {c.confidence ? ` · ${c.confidence}` : ""}
                </div>
              )}
              {c.narrative_preview && (
                <div style={{ marginTop: 6, fontSize: "0.85rem", color: "var(--ink-muted)" }}>
                  {c.narrative_preview.slice(0, 140)}
                  {c.narrative_preview.length > 140 ? "…" : ""}
                </div>
              )}
            </div>
          ))}
        </div>

        {selected && (
          <div className="panel">
            <h2 style={{ fontSize: "1.05rem" }}>Case detail</h2>
            <label>Title</label>
            <div style={{ display: "flex", gap: "0.5rem", marginBottom: "0.75rem" }}>
              <input value={editTitle} onChange={(e) => setEditTitle(e.target.value)} />
              <button className="btn secondary" onClick={saveTitle}>
                Save
              </button>
            </div>
            <div style={{ fontSize: "0.85rem", color: "var(--ink-muted)", marginBottom: "0.75rem" }}>
              Judge: {selected.selected_judge?.replace(/_/g, " ") || "—"}
              <br />
              Verdict: {selected.verdict || "—"}
            </div>
            <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", marginBottom: "1rem" }}>
              <button
                className="btn"
                onClick={() => {
                  localStorage.setItem("case_id", selected.case_id);
                  navigate("/chat");
                }}
                disabled={selected.status !== "completed"}
              >
                Open Chat
              </button>
              <button className="btn secondary" onClick={() => removeCase(selected.case_id)}>
                Delete
              </button>
            </div>

            <h2 style={{ fontSize: "0.95rem" }}>Reports</h2>
            <AgentDetails detail={agentDetails.ingestion} />
            {Object.keys(reports).length === 0 && (
              <div style={{ color: "var(--ink-muted)", fontSize: "0.9rem" }}>
                No report snapshot available for this case.
              </div>
            )}
            <ul className="stage-list">
              {Object.entries(reports).map(([key, content]) => (
                <li key={key} className="stage-item">
                  <div className="stage-head" onClick={() => setOpenKey(openKey === key ? null : key)}>
                    <span className="title">{key.replace(/_/g, " ")}</span>
                    <span className="badge done">saved</span>
                  </div>
                  <AgentDetails detail={agentDetails[key]} />
                  {openKey === key && <div className="stage-body">{content}</div>}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </>
  );
}
