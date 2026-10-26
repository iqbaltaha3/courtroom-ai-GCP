import { useEffect, useRef, useState } from "react";
import { userMessage, researchApi, ResearchJob } from "../api/client";

const STAGE_LABELS: Record<string, string> = {
  extraction: "Understanding your question",
  statutes: "Finding relevant laws",
  planning: "Research plan",
  web_search: "Live precedent search",
  ranking: "Reviewing relevant decisions",
  reasoning: "Preparing your research memo",
};

export default function ResearchPage() {
  const [query, setQuery] = useState("");
  const [job, setJob] = useState<ResearchJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [openStage, setOpenStage] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, []);

  async function start() {
    if (!query.trim() || query.trim().length < 10) {
      setError("Enter a research query of at least 10 characters.");
      return;
    }
    setError(null);
    setBusy(true);
    setJob(null);
    try {
      const started = await researchApi.start(query.trim());
      const poll = async () => {
        try {
          const j = await researchApi.get(started.job_id);
          setJob(j);
          if (j.status === "completed" || j.status === "failed") {
            setBusy(false);
            if (pollRef.current) window.clearInterval(pollRef.current);
            pollRef.current = null;
            if (j.status === "failed") setError(userMessage(j.error || "Research could not be completed."));
          }
        } catch (e: unknown) {
          setError(userMessage(e instanceof Error ? e.message : String(e)));
          setBusy(false);
          if (pollRef.current) window.clearInterval(pollRef.current);
        }
      };
      await poll();
      pollRef.current = window.setInterval(poll, 2000);
    } catch (e: unknown) {
      setError(userMessage(e instanceof Error ? e.message : String(e)));
      setBusy(false);
    }
  }

  const stages = job?.stages || {};
  const stageOrder = ["extraction", "statutes", "planning", "web_search", "ranking", "reasoning"];

  return (
    <>
      <h1>Legal Research</h1>
      <p style={{ color: "var(--ink-muted)" }}>
        Explore relevant laws and court decisions, with sources and a clear research memo.
      </p>

      <div className="panel">
        <label>Case description or legal question</label>
        <textarea
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          disabled={busy}
          placeholder="Describe the facts and what you need researched (offences, procedure, precedents)…"
          style={{ minHeight: 140 }}
        />
        <div style={{ marginTop: "0.75rem" }}>
          <button className="btn" onClick={start} disabled={busy || query.trim().length < 10}>
            {busy ? "Research running…" : "Run legal research"}
          </button>
        </div>
      </div>

      {error && (
        <div className="panel" style={{ color: "#f0c0c0", borderColor: "var(--danger)" }}>
          {error}
        </div>
      )}

      {job && (
        <div className="panel">
          <h2>Research progress</h2>
          <div style={{ fontSize: "0.85rem", color: "var(--ink-muted)", marginBottom: 8 }}>
            Status: {job.status}
          </div>
          <ul className="stage-list">
            {stageOrder.map((key) => {
              const s = stages[key];
              const status = s?.status || (job.status === "queued" ? "pending" : "pending");
              return (
                <li key={key} className="stage-item">
                  <div className="stage-head" onClick={() => setOpenStage(openStage === key ? null : key)}>
                    <span className="title">{STAGE_LABELS[key] || key}</span>
                    <span className={`badge ${status === "done" ? "done" : status === "running" ? "running" : status === "error" ? "error" : "pending"}`}>
                      {status}
                    </span>
                  </div>
                  {openStage === key && s && (
                    <div className="stage-body">
                      {status === "done" ? "This step is complete." : status === "error"
                        ? "This step could not be completed. See the message above."
                        : status === "running" ? "This step is in progress."
                        : "This step will begin shortly."}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {job?.status === "completed" && job.result && (
        <div className="panel">
          <h2>Research memo</h2>
          <div className="stage-body" style={{ maxHeight: 640 }}>
            {formatMemo(job.result)}
          </div>
        </div>
      )}
    </>
  );
}

function formatMemo(result: Record<string, unknown>): string {
  // Present the legal findings in plain language rather than showing the stored JSON.
  const labels: Record<string, string> = {
    laws: "Relevant laws", sections_applied: "Sections applied", precedents: "Court decisions",
    legal_research: "Research findings", issues: "Legal questions", applicable_law: "Applicable law",
    precedent_analysis: "Analysis of court decisions", application: "How the law applies",
    uncertainties: "Uncertainties", research_queries: "Research questions explored",
    evidence_completeness: "Search coverage", claim: "Finding", support: "Basis", confidence: "Confidence",
  };
  const values: Record<string, string> = {
    statutory_text: "Statute text", judicial_authority: "Court decision", secondary_source: "Secondary source",
    unverified: "Not verified", limited_search_may_have_missed_authority: "This was a limited search; relevant decisions may not have been found.",
    exhaustive_search_no_authority_found: "No relevant authority was identified in the reported search.",
  };
  function format(value: unknown): string {
    if (value == null || value === "") return "Not recorded";
    if (Array.isArray(value)) return value.map((item, index) => `${index + 1}. ${format(item)}`).join("\n\n");
    if (typeof value === "object") return Object.entries(value).map(([key, item]) =>
      `${labels[key] || key.replace(/_/g, " ")}:\n${format(item)}`).join("\n\n");
    return values[String(value)] || String(value);
  }
  return format(result);
}
