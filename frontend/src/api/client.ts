const BASE = (import.meta.env.VITE_API_URL || "").replace(/\/+$/, "");

// Translate service diagnostics into useful messages without exposing provider or hosting details.
export function userMessage(message: string): string {
  if (/quota|resource.exhausted|rate.limit|429/i.test(message)) {
    return "The service is busy. Please try again shortly.";
  }
  if (/gemini|vertex|tavily|gcp_project|api.key|google.auth|traceback|cloud.run|sqlite|OperationalError|ProgrammingError|no such table|syntax error|\b(?:HTTP|status|error)\s*[:=]?\s*(?:500|502|503)\b/i.test(message)) {
    return "This request could not be completed. Please try again. If it continues, contact support.";
  }
  if (/failed to fetch|networkerror|network request|load failed/i.test(message)) {
    return "Unable to connect. Check your internet connection and try again.";
  }
  return message;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init);
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const detail = typeof body?.detail === "string" ? body.detail : "Your request could not be completed. Please try again.";
    throw new Error(userMessage(detail));
  }
  return res.json() as Promise<T>;
}

// Per-stage provider usage; null means the provider did not report a count.
export type AgentDetail = {
  available: boolean; calls: number; steps: string[];
  models: { model: string; calls: number; failed_calls: number; unreported_calls: number;
    input_tokens: number | null; output_tokens: number | null;
    total_tokens: number | null; reasoning_tokens: number | null }[];
};
export type Judge = { id: string; display_name: string };
export type CaseState = {
  case_id: string;
  status: string;
  selected_judge?: string | null;
  run_id?: string | null;
  progress_pct: number;
  current_stage?: string | null;
  stages: {
    key: string;
    title: string;
    status: string;
    error?: string;
    started_at?: string | null;
    finished_at?: string | null;
    duration_seconds?: number | null;
  }[];
  error?: string | null;
  cancel_requested?: boolean;
  ingestion_pages_done?: number;
  ingestion_pages_total?: number;
  ingestion_current_page?: string | null;
  run_started_at?: string | null;
  run_finished_at?: string | null;
  elapsed_seconds?: number | null;
};

export type StatuteItem = { act: string; section: string; chapter_title: string; text: string };

export const api = {
  agentDetails: (id: string) => req<{ agents: Record<string, AgentDetail> }>(`/api/v1/cases/${id}/agent-details`),
  health: () => req<{ status: string; llm_configured: boolean; llm_provider: string; llm_model: string; judges: number }>("/health"),
  judges: () => req<Judge[]>("/api/v1/judges"),
  createCase: () => req<{ case_id: string; status: string }>("/api/v1/cases", { method: "POST" }),
  getCase: (id: string) => req<CaseState>(`/api/v1/cases/${id}`),
  upload: async (id: string, allegation: File[], petition: File[], evidence: File[]) => {
    // Reset the selected inventory, then keep each upload request below the
    // hosting HTTP/1 body limit, including when many files are selected.
    await req(`/api/v1/cases/${id}/documents`, { method: "POST", body: new FormData() });
    const chunkBytes = 8 * 1024 * 1024;
    const groups: [string, File[]][] = [["allegation", allegation], ["petition", petition], ["evidence", evidence]];
    for (const [kind, files] of groups) {
      for (const file of files) {
        const uploadId = crypto.randomUUID();
        let offset = 0;
        do {
          const end = Math.min(offset + chunkBytes, file.size);
          const fd = new FormData();
          fd.append("chunk", file.slice(offset, end), file.name);
          fd.append("upload_id", uploadId);
          fd.append("kind", kind);
          fd.append("filename", file.name);
          fd.append("offset", String(offset));
          fd.append("total_size", String(file.size));
          const result = await req<{ received: number; complete: boolean }>(`/api/v1/cases/${id}/documents/chunk`, {
            method: "POST", body: fd,
          });
          if (result.received !== end || result.complete !== (end === file.size)) {
            throw new Error("Upload did not complete correctly. Please retry.");
          }
          offset = end;
        } while (offset < file.size);
      }
    }
    return { ok: true };
  },
  startRun: async (id: string, judge: string, narrative: string) => {
    const fd = new FormData();
    if (judge) fd.append("judge", judge);
    if (narrative) fd.append("narrative", narrative);
    return req<{ case_id: string; run_id: string; status: string }>(`/api/v1/cases/${id}/runs`, {
      method: "POST",
      body: fd,
    });
  },
  terminateRun: (id: string) =>
    req<{ case_id: string; status: string; ok: boolean }>(`/api/v1/cases/${id}/terminate`, {
      method: "POST",
    }),
  downloadPdf: async (id: string) => {
    // Fetch as a blob so downloads work with both local proxy and remote backend URLs.
    const response = await fetch(`${BASE}/api/v1/cases/${encodeURIComponent(id)}/export.pdf`);
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw new Error(userMessage(typeof body?.detail === "string" ? body.detail : "Could not download the simulation PDF."));
    }
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = `simulation-${id}.pdf`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  },
  reports: (id: string) => req<{ case_id: string; reports: Record<string, string> }>(`/api/v1/cases/${id}/reports`),
  report: (id: string, stage: string) =>
    req<{ stage_key: string; content: string; structured?: {
      search_entry_point?: string; sources?: {id: string; title: string; url: string}[];
    } }>(`/api/v1/cases/${id}/reports/${stage}`),
  listStatuteActs: () =>
    req<{ acts: string[]; core: string[]; extra: string[] }>("/api/v1/statutes/acts"),
  browseStatute: (act: string) =>
    req<{ act: string; sections: StatuteItem[] }>(
      `/api/v1/statutes/browse?act=${encodeURIComponent(act)}&limit=500`
    ),
  searchStatutes: (q: string, act: string) =>
    req<{ query: string; results: StatuteItem[] }>(
      `/api/v1/statutes/search?q=${encodeURIComponent(q)}&act=${encodeURIComponent(act)}&limit=12`
    ),
  chat: (id: string, message: string) =>
    req<{ answer: string; sql?: string; rows?: unknown[] }>(`/api/v1/cases/${id}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    }),
};

export function caseWebSocket(caseId: string): WebSocket {
  // Prefer same-origin (Vite proxy) in local dev so WS goes through the
  // proxy and Origin/CORS issues disappear. When VITE_API_URL is set
  // (production), talk directly to the backend host.
  if (import.meta.env.VITE_API_URL) {
    const host = BASE.replace(/^http/, "ws");
    return new WebSocket(`${host}/api/v1/cases/${caseId}/ws`);
  }
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return new WebSocket(`${proto}://${window.location.host}/api/v1/cases/${caseId}/ws`);
}

export type LibraryCase = {
  case_id: string;
  title: string;
  status: string;
  selected_judge?: string | null;
  verdict?: string | null;
  confidence?: string | null;
  narrative_preview?: string | null;
  tags: string[];
  created_at?: string | null;
  updated_at?: string | null;
  completed_at?: string | null;
  notes?: string | null;
};

export const libraryApi = {
  list: (q?: string, status?: string) => {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (status) params.set("status", status);
    const qs = params.toString();
    return req<{ cases: LibraryCase[]; total: number }>(
      `/api/v1/library/cases${qs ? `?${qs}` : ""}`
    );
  },
  get: (id: string) => req<LibraryCase>(`/api/v1/library/cases/${id}`),
  update: (id: string, body: { title?: string; tags?: string[]; notes?: string }) =>
    req<LibraryCase>(`/api/v1/library/cases/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  remove: (id: string) =>
    req<{ ok: boolean }>(`/api/v1/library/cases/${id}`, { method: "DELETE" }),
  reports: (id: string) =>
    req<{ case_id: string; reports: Record<string, string>; meta: LibraryCase }>(
      `/api/v1/library/cases/${id}/reports`
    ),
};


export type ResearchJob = {
  job_id: string;
  query: string;
  status: string;
  stages: Record<string, { status: string; data?: unknown; message?: string }>;
  result?: Record<string, unknown> | null;
  error?: string | null;
};

export const researchApi = {
  start: (query: string) =>
    req<{ job_id: string; status: string }>("/api/v1/research/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    }),
  get: (id: string) => req<ResearchJob>(`/api/v1/research/runs/${id}`),
  list: () => req<ResearchJob[]>("/api/v1/research/runs"),
};
