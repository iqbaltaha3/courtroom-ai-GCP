import { useEffect, useState } from "react";
import { api, AgentDetail } from "../api/client";

// Fetch one usage snapshot for all agents; poll only while the run is active.
export function useAgentDetails(caseId: string | null, active = false) {
  const [agents, setAgents] = useState<Record<string, AgentDetail>>({});
  useEffect(() => {
    let disposed = false;
    setAgents({});
    if (!caseId) return;
    const refresh = () => api.agentDetails(caseId).then(result => {
      if (!disposed) setAgents(result.agents);
    }).catch(() => {});
    void refresh();
    const timer = active ? window.setInterval(refresh, 2500) : undefined;
    return () => { disposed = true; window.clearInterval(timer); };
  }, [caseId, active]);
  return agents;
}

// Keep technical usage optional and separate from the readable report.
export default function AgentDetails({ detail }: { detail?: AgentDetail }) {
  const tokens = (value: number | null) => value == null ? "Not reported" : value.toLocaleString();
  return <details className="agent-details">
    <summary>Agent details</summary>
    <div className="agent-details-content">
      {!detail?.available ? <p>Usage details are unavailable for this stage. Older cases may not have recorded them.</p> : <>
        <p><strong>{detail.calls} API {detail.calls === 1 ? "call" : "calls"}</strong> · Includes retries and reviews</p>
        <div className="agent-usage-scroll"><table>
          <thead><tr><th>Model</th><th>Calls</th><th>Input tokens</th><th>Output tokens</th><th>Total tokens</th><th>Reasoning tokens</th></tr></thead>
          <tbody>{detail.models.map(model => <tr key={model.model}>
            <td>{model.model.replace("gemini-", "Gemini ").replace("-flash", " Flash").replace("-pro", " Pro")}</td>
            <td>{model.calls}</td><td>{tokens(model.input_tokens)}</td><td>{tokens(model.output_tokens)}</td>
            <td>{tokens(model.total_tokens)}</td><td>{tokens(model.reasoning_tokens)}</td>
          </tr>)}</tbody>
        </table></div>
        <p className="agent-usage-note">Provider-reported counts. Reasoning tokens may be included in output or total tokens; do not add them again.
          {detail.models.some(m => m.unreported_calls > 0) && " Counts are partial: some requests have no complete usage metadata."}
          {detail.models.some(m => m.failed_calls > 0) && ` ${detail.models.reduce((n, m) => n + m.failed_calls, 0)} requests failed.`}
        </p>
      </>}
      {detail && <><h4>What happened</h4><ul>{detail.steps.map((step, i) => <li key={i}>{step}</li>)}</ul>
        <p className="agent-usage-note">Recorded workflow steps. The report provides the agent's stated reasoning and supporting sources.</p></>}
    </div>
  </details>;
}
