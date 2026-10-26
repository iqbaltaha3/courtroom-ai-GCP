import { useEffect, useRef, useState } from "react";
import { userMessage, api, caseWebSocket, CaseState, Judge } from "../api/client";

import AgentDetails, { useAgentDetails } from "../components/AgentDetails";

const STAGE_ORDER = [
  ["clarify_extract", "Clarify + Extract"],
  ["case_manager", "Case Manager"],
  ["prosecution_consultant", "Prosecution Consultant"],
  ["defense_consultant", "Defense Consultant"],
  ["prosecution_research", "Prosecution Research"],
  ["defense_research", "Defense Research"],
  ["pros_r1", "Prosecutor Round 1"],
  ["def_r1", "Defense Round 1"],
  ["pros_r2", "Prosecutor Round 2"],
  ["def_r2", "Defense Round 2"],
  ["judge", "Judge"],
];

function formatDuration(seconds?: number | null): string {
  if (seconds == null || Number.isNaN(seconds)) return "—";
  const s = Math.max(0, Math.round(seconds));
  const m = Math.floor(s / 60);
  const r = s % 60;
  if (m <= 0) return `${r}s`;
  return `${m}m ${r.toString().padStart(2, "0")}s`;
}

export default function SimulationPage() {
  const [judges, setJudges] = useState<Judge[]>([]);
  const [judge, setJudge] = useState("");
  const [narrative, setNarrative] = useState("");
  const [allegation, setAllegation] = useState<File[]>([]);
  const [petition, setPetition] = useState<File[]>([]);
  const [evidence, setEvidence] = useState<File[]>([]);
  const [caseId, setCaseId] = useState<string | null>(null);
  const [state, setState] = useState<CaseState | null>(null);
  const [reports, setReports] = useState<Record<string, string>>({});
  const [openStage, setOpenStage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [terminating, setTerminating] = useState(false);
  const [downloadingPdf, setDownloadingPdf] = useState(false);
  const [pdfError, setPdfError] = useState<string | null>(null);
  const [searchAttributions, setSearchAttributions] = useState<Record<string, string>>({});
  const [nowTick, setNowTick] = useState(() => Date.now());
  const agentDetails = useAgentDetails(caseId, busy || state?.status === "running" || state?.status === "ingesting");
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<number | undefined>(undefined);
  const reconnectAttemptsRef = useRef(0);

  useEffect(() => {
    api.judges().then((j) => {
      setJudges(j);
      if (j[0]) setJudge(j[0].id);
    });
  }, []);

  // Live elapsed timer while a run is active
  useEffect(() => {
    if (!busy && state?.status !== "ingesting" && state?.status !== "running") return;
    const id = window.setInterval(() => setNowTick(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [busy, state?.status]);

  // Track an in-progress run for this browser session only.
  // Simulation always opens clean; finished cases are reopened from Case Library.
  // WebSocket is only opened while status is ingesting/running to avoid
  // Vite proxy EPIPE noise when the backend rejects unknown/stale case ids.
  useEffect(() => {
    if (!caseId) return;
    let disposed = false;
    let timer: number | undefined;

    async function refresh() {
      try {
        const latest = await api.getCase(caseId!);
        if (disposed) return;
        setState(latest);
        const active = latest.status === "running" || latest.status === "ingesting";
        setBusy(active);
        setTerminating(active && !!latest.cancel_requested);
        if (latest.status === "failed") setError(latest.error || "Simulation failed");

        if (active && (!wsRef.current || wsRef.current.readyState > 1)) {
          connectWs(caseId!);
        }
        if (!active) {
          if (reconnectTimerRef.current) window.clearTimeout(reconnectTimerRef.current);
          wsRef.current?.close();
          wsRef.current = null;
        }

        const result = await api.reports(caseId!);
        if (!disposed) setReports(result.reports);
        if (["completed", "failed", "terminated"].includes(latest.status)) {
          window.clearInterval(timer);
        }
      } catch (err) {
        if (disposed) return;
        if (err instanceof Error && err.message.includes("Case not found")) {
          localStorage.removeItem("case_id");
          setCaseId(null);
          setState(null);
          setBusy(false);
          if (reconnectTimerRef.current) window.clearTimeout(reconnectTimerRef.current);
          wsRef.current?.close();
          wsRef.current = null;
        }
      }
    }

    void refresh();
    timer = window.setInterval(refresh, 2500);

    return () => {
      disposed = true;
      window.clearInterval(timer);
      if (reconnectTimerRef.current) window.clearTimeout(reconnectTimerRef.current);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [caseId]);

  useEffect(() => {
    if (!caseId || !openStage || !reports[openStage]) return;
    let disposed = false;
    api.report(caseId, openStage).then((report) => {
      const html = report.structured?.search_entry_point;
      if (!disposed && html) setSearchAttributions((prev) => ({ ...prev, [openStage]: html }));
    }).catch(() => {});
    return () => { disposed = true; };
  }, [caseId, openStage, reports[openStage || ""]]);

  useEffect(() => {
    if (!caseId) return;
    if (state?.status === "completed") {
      api.reports(caseId).then((r) => setReports(r.reports)).catch(() => {});
    }
  }, [caseId, state?.status]);

  function connectWs(id: string) {
    if (reconnectTimerRef.current) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = undefined;
    }
    wsRef.current?.close();
    const ws = caseWebSocket(id);
    wsRef.current = ws;

    ws.onopen = () => {
      reconnectAttemptsRef.current = 0;
    };

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "snapshot" || msg.case_id) {
          setState((prev) => ({ ...(prev || { case_id: id, progress_pct: 0, stages: [] }), ...msg }));
        }
        if (msg.type === "ingestion_progress") {
          setState((prev) => prev ? {
            ...prev,
            progress_pct: msg.progress_pct ?? prev.progress_pct,
            ingestion_pages_done: msg.pages_done,
            ingestion_pages_total: msg.pages_total,
            ingestion_current_page: msg.current_page,
          } : prev);
        }
        if (msg.type === "stage_update" || msg.type === "run_completed" || msg.type === "run_failed") {
          api.getCase(id).then(setState).catch(() => {});
        }
        if (msg.type === "stage_report" && msg.stage && typeof msg.report === "string") {
          setReports((prev) => ({ ...prev, [msg.stage]: msg.report }));
          setOpenStage((prev) => prev || msg.stage);
        }
        if (msg.type === "stage_update" && msg.status === "done" && msg.stage) {
          api.report(id, msg.stage)
            .then((r) => setReports((prev) => ({ ...prev, [msg.stage]: r.content })))
            .catch(() => {});
        }
        if (msg.type === "run_completed") {
          api.reports(id).then((r) => setReports(r.reports)).catch(() => {});
          setBusy(false);
          setTerminating(false);
        }
        if (msg.type === "run_failed") {
          setError(msg.error || "Run failed");
          setBusy(false);
          setTerminating(false);
        }
        if (msg.type === "run_terminated") {
          setError(msg.error || "Simulation terminated");
          setBusy(false);
          setTerminating(false);
          api.getCase(id).then(setState).catch(() => {});
        }
      } catch {
        /* ignore */
      }
    };

    ws.onerror = () => {
      ws.close();
    };

    ws.onclose = (ev) => {
      if (wsRef.current !== ws) return; // a newer connection has already taken over
      if (ev.code === 4404) return; // case not found server-side — no point retrying
      // Only reconnect while a run is still active; otherwise REST polling is enough
      // and avoids Vite proxy EPIPE spam after the backend closes the socket.
      const attempt = reconnectAttemptsRef.current + 1;
      if (attempt > 8) return;
      reconnectAttemptsRef.current = attempt;
      const delay = Math.min(1000 * attempt, 8000);
      reconnectTimerRef.current = window.setTimeout(() => connectWs(id), delay);
    };
  }

  async function onRun() {
    setError(null);
    setBusy(true);
    setTerminating(false);
    setReports({});
    setSearchAttributions({});
    try {
      const created = await api.createCase();
      let id = created.case_id;
      if (allegation.length || petition.length || evidence.length) {
        try {
          await api.upload(id, allegation, petition, evidence);
        } catch (e) {
          if (e instanceof Error && e.message.includes("Case not found")) {
            const created = await api.createCase();
            id = created.case_id;
            setCaseId(id);
            localStorage.setItem("case_id", id);
            await api.upload(id, allegation, petition, evidence);
          } else {
            throw e;
          }
        }
      }
      connectWs(id);
      try {
        await api.startRun(id, judge, narrative);
        setCaseId(id);
        localStorage.setItem("case_id", id);
      } catch (e) {
        if (e instanceof Error && e.message.includes("Case not found")) {
          const created = await api.createCase();
          id = created.case_id;
          setCaseId(id);
          localStorage.setItem("case_id", id);
          if (allegation.length || petition.length || evidence.length) {
            await api.upload(id, allegation, petition, evidence);
          }
          connectWs(id);
          await api.startRun(id, judge, narrative);
        } else {
          throw e;
        }
      }
    } catch (e: unknown) {
      setError(userMessage(e instanceof Error ? e.message : String(e)));
      setBusy(false);
    }
  }

  function clearAll() {
    localStorage.removeItem("case_id");
    setCaseId(null);
    setState(null);
    setReports({});
    setNarrative("");
    setAllegation([]);
    setPetition([]);
    setEvidence([]);
    setError(null);
    setTerminating(false);
    if (reconnectTimerRef.current) window.clearTimeout(reconnectTimerRef.current);
    wsRef.current?.close();
    wsRef.current = null;
  }

  async function terminateSimulation() {
    if (!caseId || terminating) return;
    setTerminating(true);
    setError(null);
    try {
      await api.terminateRun(caseId);
      const latest = await api.getCase(caseId);
      setState(latest);
      setError("Stopping simulation after the active request settles.");
    } catch (e: unknown) {
      setError(userMessage(e instanceof Error ? e.message : String(e)));
      setTerminating(false);
    }
  }

  const stages: CaseState['stages'] = state?.stages?.length
    ? state.stages
    : STAGE_ORDER.map(([key, title]) => ({ key, title, status: "pending" }));

  const judgeReport = reports["judge"] || "";
  const verdictMatch = judgeReport.match(/Verdict:\s*(.+)/i);
  const confMatch = judgeReport.match(/Confidence:\s*(.+)/i);
  const punishMatch = judgeReport.match(/Probable punishment:\s*(.+)/i);
  const isActiveRun = busy || state?.status === "ingesting" || state?.status === "running";
  const statusLabel = state?.status || (busy ? "starting" : "ready");

  // Total simulation time (live while running, final when finished)
  void nowTick; // re-render each second while active
  let elapsedDisplay = "—";
  if (state?.run_started_at) {
    const startMs = Date.parse(state.run_started_at);
    const endMs = state.run_finished_at
      ? Date.parse(state.run_finished_at)
      : (isActiveRun ? Date.now() : Date.parse(state.run_started_at));
    if (!Number.isNaN(startMs) && !Number.isNaN(endMs)) {
      elapsedDisplay = formatDuration((endMs - startMs) / 1000);
    }
  } else if (state?.elapsed_seconds != null) {
    elapsedDisplay = formatDuration(state.elapsed_seconds);
  }
  const activeKeys = new Set((state?.current_stage || "").split(",").filter(Boolean));
  const parallelRunning = stages.filter((s) => activeKeys.has(s.key) && s.status === "running").length > 1;

  return (
    <>
      <div className="simulation-header">
        <div>
          <h1>Simulation</h1>
          <p className="page-kicker">
            Upload case documents, select a presiding judge, and explore the arguments and a reasoned case assessment.
          </p>
        </div>
        <div className={`run-state ${statusLabel}`}>{statusLabel}</div>
      </div>

      <div className="panel">
        <h2>Document intake</h2>
        <div className="grid-3">
          <Drop kind="Allegation / FIR" files={allegation} setFiles={setAllegation} disabled={busy} />
          <Drop kind="Petition / Charge sheet" files={petition} setFiles={setPetition} disabled={busy} />
          <Drop kind="Supporting evidence" files={evidence} setFiles={setEvidence} disabled={busy} />
        </div>
        <div className="field-block">
          <label>Optional typed narrative</label>
          <textarea
            value={narrative}
            onChange={(e) => setNarrative(e.target.value)}
            disabled={busy}
            placeholder="Short summary or additional facts…"
          />
        </div>
      </div>

      <div className="panel">
        <h2>Presiding judge</h2>
        <select value={judge} onChange={(e) => setJudge(e.target.value)} disabled={busy}>
          {judges.map((j) => (
            <option key={j.id} value={j.id}>
              {j.display_name}
            </option>
          ))}
        </select>
      </div>

      <div className="action-row">
        <button
          className="btn primary"
          disabled={
            busy || (!allegation.length && !petition.length && !evidence.length && !narrative.trim())
          }
          onClick={onRun}
        >
          {busy ? "Proceedings in progress…" : "Run full simulation"}
        </button>
        <button className="btn ghost" disabled={busy} onClick={clearAll}>
          Clear
        </button>
        <button className="btn danger" disabled={!isActiveRun || terminating} onClick={terminateSimulation}>
          {terminating ? "Terminating…" : "Terminate simulation"}
        </button>
      </div>

      {error && (
        <div className="alert">
          {userMessage(error)}
        </div>
      )}

      {(state || busy) && (
        <div className="panel">
          <h2>Proceedings</h2>
          <div className="stage-meta">
            Status: {state?.status || "starting"} · Progress {state?.progress_pct ?? 0}%
            {" · "}Elapsed {elapsedDisplay}
            {parallelRunning ? " · Preparing both sides" : ""}
          </div>
          <div className="progress-track">
            <div className="progress-fill" style={{ width: `${state?.progress_pct ?? 0}%` }} />
          </div>

          {state?.status === "ingesting" && (
            <div className="stage-meta ingestion-meta">
              {state?.ingestion_pages_total
                ? `Transcribing documents… page ${state.ingestion_pages_done ?? 0} of ${state.ingestion_pages_total}`
                  + (state.ingestion_current_page ? ` (${state.ingestion_current_page})` : "")
                : "Preparing documents for transcription…"}
            </div>
          )}

          {state?.status === "completed" && judgeReport && (
            <div className="verdict-strip">
              <div className="metric">
                <div className="label">Verdict</div>
                <div className="value">{verdictMatch?.[1] || "—"}</div>
              </div>
              <div className="metric">
                <div className="label">Record sufficiency</div>
                <div className="value">{confMatch?.[1] || "—"}</div>
              </div>
              <div className="metric">
                <div className="label">Punishment</div>
                <div className="value">{punishMatch?.[1] || "—"}</div>
              </div>
            </div>
          )}

          {state?.status === "completed" && caseId && (
            <div>
              <button type="button" className="btn primary" disabled={downloadingPdf}
                onClick={async () => {
                  setDownloadingPdf(true);
                  setPdfError(null);
                  try {
                    await api.downloadPdf(caseId);
                  } catch (error) {
                    setPdfError(userMessage(error instanceof Error ? error.message : "PDF download failed."));
                  } finally {
                    setDownloadingPdf(false);
                  }
                }}>
                {downloadingPdf ? "Preparing PDF…" : "Download Simulation PDF"}
              </button>
              {pdfError && <p role="alert">{pdfError}</p>}
            </div>
          )}

          <AgentDetails detail={agentDetails.ingestion} />
          <ul className="stage-list">
            {stages.map((s, index) => (
              <li key={s.key} className="stage-item">
                <button type="button" className="stage-head" aria-expanded={openStage === s.key} onClick={() => setOpenStage(openStage === s.key ? null : s.key)}>
                  <span className="stage-title-wrap">
                    <span className="stage-index">{index + 1}</span>
                    <span className="title">{s.title}</span>
                  </span>
                  <span className="stage-actions">
                    {parallelRunning && activeKeys.has(s.key) && s.status === "running" && (
                      <span className="parallel-pill">Parallel</span>
                    )}
                    <span className={`report-dot ${reports[s.key] ? "ready" : ""}`}>
                      {reports[s.key] ? "Report ready" : "Waiting"}
                    </span>
                    <span className={`badge ${s.status}`}>{s.status}</span>
                  </span>
                </button>
                <AgentDetails detail={agentDetails[s.key]} />
                {openStage === s.key && (
                  <div className="stage-body">
                    <ReportText text={reports[s.key] || (s.error ? userMessage(s.error) : "No report yet for this stage.")} />
                    {searchAttributions[s.key] && <iframe
                      title="Google Search sources" className="search-attribution"
                      srcDoc={searchAttributions[s.key]}
                      sandbox="allow-popups allow-popups-to-escape-sandbox"
                    />}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}

function ReportText({ text }: { text: string }) {
  return <>{text.split(/(https?:\/\/[^\s]+)/g).map((part, index) =>
    /^https?:\/\//.test(part)
      ? <a key={index} href={part} target="_blank" rel="noopener noreferrer">Open source</a>
      : part
  )}</>;
}

function Drop({
  kind,
  files,
  setFiles,
  disabled,
}: {
  kind: string;
  files: File[];
  setFiles: (f: File[]) => void;
  disabled?: boolean;
}) {
  return (
    <label className="dropzone">
      <strong>{kind}</strong>
      <input
        type="file"
        accept="application/pdf"
        multiple
        disabled={disabled}
        style={{ display: "none" }}
        onChange={(e) => setFiles(Array.from(e.target.files || []))}
      />
      {files.length ? (
        <span className="drop-files">{files.map((f) => f.name).join(", ")}</span>
      ) : (
        <span className="drop-hint">Click to attach PDF</span>
      )}
    </label>
  );
}
