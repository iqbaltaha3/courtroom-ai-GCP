import { useEffect, useState } from "react";
import { userMessage, api, CaseState } from "../api/client";

type Msg = { role: "user" | "agent"; text: string };

export default function ChatPage() {
  const caseId = localStorage.getItem("case_id");
  const [state, setState] = useState<CaseState | null>(null);
  const [input, setInput] = useState("");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!caseId) return;
    api.getCase(caseId).then(setState).catch(() => setState(null));
  }, [caseId]);

  const unlocked = state?.status === "completed";

  async function send() {
    if (!caseId || !input.trim() || busy) return;
    const q = input.trim();
    setInput("");
    setMsgs((m) => [...m, { role: "user", text: q }]);
    setBusy(true);
    try {
      const r = await api.chat(caseId, q);
      setMsgs((m) => [...m, { role: "agent", text: r.answer }]);
    } catch (e: unknown) {
      setMsgs((m) => [
        ...m,
        { role: "agent", text: userMessage(e instanceof Error ? e.message : String(e)) },
      ]);
    } finally {
      setBusy(false);
    }
  }

  if (!caseId) {
    return (
      <>
        <h1>Case Chat</h1>
        <div className="locked">Run a simulation first. No active case is stored in this browser.</div>
      </>
    );
  }

  if (!unlocked) {
    return (
      <>
        <h1>Case Chat</h1>
        <div className="locked">
          Case Chat unlocks after the Judge delivers a verdict.
          <br />
          Current status: {state?.status || "unknown"}
        </div>
      </>
    );
  }

  return (
    <>
      <h1>Case Chat</h1>
      <p style={{ color: "var(--ink-muted)" }}>
        Ask questions about this simulation. Answers use only the case documents and saved reports.
      </p>
      <div className="panel">
        <div className="chat-log">
          {msgs.length === 0 && (
            <div style={{ color: "var(--ink-muted)", textAlign: "center" }}>
              Examples: Who are the parties? What was the verdict? List evidence items.
            </div>
          )}
          {msgs.map((m, i) => (
            <div key={i} className={`bubble ${m.role}`}>
              {m.text}
            </div>
          ))}
        </div>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask about the case…"
            onKeyDown={(e) => e.key === "Enter" && send()}
            disabled={busy}
          />
          <button className="btn" onClick={send} disabled={busy || !input.trim()}>
            {busy ? "…" : "Send"}
          </button>
        </div>
      </div>
    </>
  );
}
