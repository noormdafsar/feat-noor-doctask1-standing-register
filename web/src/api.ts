const BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!r.ok) {
    let detail = r.statusText;
    try {
      detail = (await r.json()).detail ?? detail;
    } catch {
      /* body was not json */
    }
    throw new Error(detail);
  }
  return r.json() as Promise<T>;
}

export type Family = { id: string; slug: string; name: string; documents: number };

export type Step = {
  seq: number;
  node: string;
  decision: string;
  confidence: number | null;
  detail: Record<string, unknown>;
  model: string | null;
  tokens_in: number;
  tokens_out: number;
  usd: number;
  ms: number;
  replayed: boolean;
};

export type Run = {
  run_id: string;
  family_id: string;
  mode: string;
  status: string;
  stats: Record<string, any>;
  approval_batch_id: string | null;
  started_at: string | null;
  steps: Step[];
};

export type ChainLink = {
  filename: string;
  doc_type: string;
  value: string;
  unit: string;
  effective_from: string | null;
  quote: string;
  char_start: number;
  char_end: number;
  document_id: string;
};

export type Item = {
  item_id: string;
  kind: "row_update" | "finding" | "conflict" | "escalation";
  ref_id: string;
  title: string;
  payload: any;
  decision: "pending" | "accept" | "reject";
  reason: string | null;
};

export type Batch = { batch_id: string | null; run_id?: string; items: Item[] };

export type Doc = {
  id: string;
  filename: string;
  doc_type: string;
  confidence: number;
  tier: string;
  doc_date: string | null;
  blocked_reason: string | null;
  sha256: string;
  invoice_lines: number;
};

export type IngestResult = {
  filename: string;
  status: "accepted" | "duplicate" | "rejected";
  document_id?: string;
  reason?: string;
};

export type Row = {
  term_key: string;
  label: string;
  value: { display?: string; value?: string; effective_from?: string };
  status: string;
  version: number;
  content_hash: string;
};

export const api = {
  families: () => req<Family[]>("/families"),
  documents: (slug: string) => req<Doc[]>(`/families/${slug}/documents`),
  upload: async (slug: string, files: File[]): Promise<IngestResult[]> => {
    // multipart: no JSON content-type header, the browser sets the boundary itself
    const body = new FormData();
    for (const f of files) body.append("files", f);
    const r = await fetch(`${BASE}/families/${slug}/documents`, { method: "POST", body });
    if (!r.ok) {
      let detail = r.statusText;
      try { detail = (await r.json()).detail ?? detail; } catch { /* body was not json */ }
      throw new Error(detail);
    }
    return (await r.json()).results as IngestResult[];
  },
  runs: (slug?: string) => req<Run[]>(`/runs${slug ? `?family=${slug}` : ""}`),
  run: (id: string) => req<Run>(`/runs/${id}`),
  cost: (id: string) => req<any>(`/runs/${id}/cost`),
  startRun: (family: string, mode = "full", rule_pack = "procurement-baseline") =>
    req<Run>("/runs", { method: "POST", body: JSON.stringify({ family, mode, rule_pack }) }),
  approvals: (runId: string) => req<Batch>(`/runs/${runId}/approvals`),
  decide: (batchId: string, decisions: any[]) =>
    req<any>(`/approvals/${batchId}/decide`, {
      method: "POST",
      body: JSON.stringify({ decisions, decided_by: "review-ui" }),
    }),
  resume: (runId: string) => req<Run>(`/runs/${runId}/resume`, { method: "POST" }),
  register: (slug: string) => req<{ rows: Row[]; unsupported_terms: string[] }>(
    `/families/${slug}/register`
  ),
  findings: (slug: string) => req<any>(`/families/${slug}/findings`),
  changelog: (slug: string) => req<any>(`/families/${slug}/changelog`),
  provenance: (slug: string, term: string) =>
    req<{ label: string; status: string; chain: ChainLink[]; conflicts: any[] }>(
      `/families/${slug}/provenance/${term}`
    ),
  documentText: (id: string) => req<{ filename: string; text: string }>(`/documents/${id}/text`),
};
