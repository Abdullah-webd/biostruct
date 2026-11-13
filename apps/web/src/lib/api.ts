/**
 * Typed client for the Biostruct API.
 *
 * Two things it has to get right:
 *
 * - **CSRF.** The API requires an `x-csrf-token` header on every mutation,
 *   matching a readable cookie. The token is read fresh on each request rather
 *   than cached, because the server rotates it on logout and a cached value
 *   goes stale. (That exact bug cost an afternoon on the backend.)
 * - **Errors.** The API speaks RFC 9457 problem details with stable `type`
 *   codes, so the UI can branch on the code instead of matching prose.
 */

export const CSRF_COOKIE = "biostruct_csrf";

export type ProblemCode =
  | "unauthenticated"
  | "invalid_credentials"
  | "email_taken"
  | "weak_password"
  | "not_found"
  | "forbidden"
  | "budget_exceeded"
  | "cost_estimate_stale"
  | "engine_unavailable"
  | "rate_limited"
  | "csrf_failed"
  | "validation_failed"
  | "residue_not_modelled"
  | "residue_mismatch"
  | "epitope_required"
  | "source_unavailable"
  | "spec_rejected"
  | "structure_unavailable"
  | "http_error";

export class ApiError extends Error {
  readonly status: number;
  readonly code: ProblemCode | string;
  readonly detail?: string;
  readonly extra: Record<string, unknown>;

  constructor(status: number, body: Record<string, unknown>) {
    const title = typeof body.title === "string" ? body.title : `Request failed (${status})`;
    super(title);
    this.name = "ApiError";
    this.status = status;
    const type = typeof body.type === "string" ? body.type : "";
    this.code = type.split("/").pop() ?? "http_error";
    this.detail = typeof body.detail === "string" ? body.detail : undefined;
    this.extra = body;
  }

  /** The sentence to put in front of the user: specific where we have it. */
  get message_for_user(): string {
    return this.detail ? `${this.message}. ${this.detail}` : this.message;
  }

  get isAuth(): boolean {
    return this.status === 401 || this.code === "unauthenticated";
  }
}

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

let csrfPrimed = false;

/** Obtain a CSRF token before the first mutation, as a browser client must. */
async function ensureCsrf(): Promise<string | null> {
  const existing = readCookie(CSRF_COOKIE);
  if (existing) return existing;
  if (!csrfPrimed) {
    csrfPrimed = true;
    await fetch("/api/v1/auth/csrf", { credentials: "same-origin" }).catch(() => {});
  }
  return readCookie(CSRF_COOKIE);
}

type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

async function request<T>(method: Method, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { accept: "application/json" };
  if (body !== undefined) headers["content-type"] = "application/json";
  if (method !== "GET") {
    const token = await ensureCsrf();
    if (token) headers["x-csrf-token"] = token;
  }

  const response = await fetch(`/api/v1${path}`, {
    method,
    headers,
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const parsed: unknown = text ? JSON.parse(text) : {};

  if (!response.ok) {
    throw new ApiError(response.status, parsed as Record<string, unknown>);
  }
  return parsed as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};

/* --- shapes, mirroring the API's models ------------------------------- */

export type User = {
  id: string;
  email: string;
  name: string;
  role: string;
  created_at: string;
};

export type Infection = {
  name: string;
  pathogen?: string | null;
  amr_context?: string | null;
};

export type Budget = {
  limit_usd: string;
  spent_usd: string;
  reserved_usd: string;
};

export type RankingWeights = Record<string, string>;

export type Project = {
  id: string;
  user_id: string;
  name: string;
  infection: Infection;
  ranking_weights: RankingWeights;
  budget: Budget;
  created_at: string;
  updated_at: string;
};

export type ActiveSite = { position: number; description: string };

export type TargetCandidate = {
  accession: string;
  protein_name: string;
  organism: string;
  organism_id: number | null;
  length: number;
  reviewed: boolean;
  evidence_level: string;
  function: string;
  gene_names: string[];
  pdb_count: number;
  active_sites: ActiveSite[];
};

export type TargetSearchResponse = {
  items: TargetCandidate[];
  query: string;
  reviewed_only: boolean;
  degraded_sources: string[];
  degraded_detail: Record<string, string>;
};

export type StructureRef = {
  pdb_id: string;
  title: string;
  method: string;
  resolution_a: number | null;
  released: string;
  cif_url: string;
  quality_note: string;
};

export type Reference = {
  pmid: string;
  title: string;
  journal: string;
  pubdate: string;
  doi: string;
  url: string;
};

export type ResidueMapSummary = {
  pdb_id: string;
  chain_id: string;
  modelled_residues: number;
  author_numbering: string;
  contiguous: boolean;
  gaps: string[];
};

export type Epitope = {
  pdb_id: string;
  chain_id: string;
  cif_url: string;
  author_residues: number[];
  uniprot_positions: number[];
  design_indices: number[];
  residue_letters: string;
  mapping: ResidueMapSummary;
  verified: boolean;
};

export type Target = {
  id: string;
  project_id: string;
  accession: string;
  protein_name: string;
  organism: string;
  length: number;
  sequence: string;
  reviewed: boolean;
  function: string;
  gene_names: string[];
  active_sites: ActiveSite[];
  structures: StructureRef[];
  references: Reference[];
  epitope: Epitope | null;
  degraded_sources: string[];
  evidence_fetched_at: string;
  created_at: string;
};

export type RunStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "stopped"
  | "submit_failed"
  | "harvesting"
  | "harvest_partial"
  | "complete";

export const ACTIVE_STATUSES: RunStatus[] = ["pending", "running", "harvesting"];

export type DesignRun = {
  id: string;
  project_id: string;
  target_id: string;
  engine: string;
  engine_job_id: string | null;
  status: RunStatus;
  livemode: boolean;
  num_proteins: number;
  modality: string;
  name: string | null;
  epitope_pdb_id: string;
  epitope_chain_id: string;
  epitope_design_indices: number[];
  progress: {
    total_to_generate: number | null;
    generated: number;
    harvested: number;
  };
  cost_estimate_usd: string;
  cost_actual_usd: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
};

export type Estimate = {
  total_usd: string;
  unit_usd: string;
  units: number;
  disclaimer: string;
  budget_limit_usd: string;
  budget_remaining_usd: string;
  within_budget: boolean;
  livemode: boolean;
};

export type CandidateMetrics = {
  structure_confidence: number;
  binding_confidence?: number | null;
  iptm?: number | null;
  min_interaction_pae?: number | null;
  ipsae_min?: number | null;
  helix_fraction?: number | null;
  sheet_fraction?: number | null;
  loop_fraction?: number | null;
};

export type Candidate = {
  id: string;
  run_id: string;
  project_id: string;
  engine_result_id: string;
  sequences: string[];
  chain_ids: string[];
  binder_chain_ids: string[];
  target_chain_ids: string[];
  metrics: CandidateMetrics;
  warnings: { code: string; message: string }[];
  structure_key: string | null;
  structure_bytes: number | null;
  livemode: boolean;
  created_at: string;
};

export type MetricContribution = {
  metric: string;
  label: string;
  raw: number;
  normalised: string;
  weight: string;
  contribution: string;
  higher_is_better: boolean;
  is_constant: boolean;
};

export type Verdict =
  | "worth_testing_first"
  | "mixed"
  | "weak"
  | "no_predicted_binding";

export type RankedCandidate = {
  candidate: Candidate;
  rank: number;
  score: string;
  percentile: string;
  binding_confidence: number | null;
  binding_band: string;
  verdict: Verdict;
  explanation: string;
  contributions: MetricContribution[];
  missing_metrics: string[];
  weight_redistributed: boolean;
};

export type Ranking = {
  items: RankedCandidate[];
  total: number;
  run_status: RunStatus;
  livemode: boolean;
  weights: RankingWeights;
  excluded: { candidate_id: string; reason: string }[];
  constant_metrics: string[];
  metrics_absent_everywhere: string[];
  best_binding_confidence: number | null;
  nothing_predicted_to_bind: boolean;
  assessment: string;
  caveat: string;
};

/* --- endpoints -------------------------------------------------------- */

export const endpoints = {
  me: () => api.get<User>("/auth/me"),
  register: (body: { email: string; name: string; password: string }) =>
    api.post<User>("/auth/register", body),
  login: (body: { email: string; password: string }) =>
    api.post<User>("/auth/login", body),
  logout: () => api.post<void>("/auth/logout"),

  projects: () => api.get<{ items: Project[]; total: number }>("/projects"),
  project: (id: string) => api.get<Project>(`/projects/${id}`),
  createProject: (body: {
    name: string;
    infection: Infection;
    budget_usd?: string;
  }) => api.post<Project>("/projects", body),
  setWeights: (id: string, weights: RankingWeights) =>
    api.put<Project>(`/projects/${id}/ranking-weights`, weights),
  budget: (id: string) =>
    api.get<{
      limit_usd: string;
      spent_usd: string;
      reserved_usd: string;
      remaining_usd: string;
      should_warn: boolean;
    }>(`/projects/${id}/budget`),

  searchTargets: (
    projectId: string,
    body: { query: string; organism_id?: number; reviewed_only?: boolean },
  ) => api.post<TargetSearchResponse>(`/projects/${projectId}/targets/search`, body),
  targets: (projectId: string) =>
    api.get<{ items: Target[] }>(`/projects/${projectId}/targets`),
  target: (projectId: string, id: string) =>
    api.get<Target>(`/projects/${projectId}/targets/${id}`),
  addTarget: (projectId: string, accession: string) =>
    api.post<Target>(`/projects/${projectId}/targets`, { accession }),
  setEpitope: (
    projectId: string,
    targetId: string,
    body: {
      pdb_id: string;
      chain_id: string;
      author_residues?: number[];
      uniprot_positions?: number[];
      expected_residues?: Record<string, string>;
    },
  ) => api.put<Target>(`/projects/${projectId}/targets/${targetId}/epitope`, body),
  residueMap: (pdbId: string, chainId: string, authorResidues?: string) =>
    api.get<{
      mapping: ResidueMapSummary;
      sequence: string;
      design_indices?: number[];
      residue_letters?: string;
    }>(
      `/structures/${pdbId}/chains/${chainId}/residue-map` +
        (authorResidues ? `?author_residues=${encodeURIComponent(authorResidues)}` : ""),
    ),

  estimate: (
    projectId: string,
    body: { target_id: string; num_proteins: number; modality?: string },
  ) => api.post<Estimate>(`/projects/${projectId}/runs/estimate`, body),
  createRun: (
    projectId: string,
    body: {
      request: { target_id: string; num_proteins: number; modality?: string; name?: string };
      confirmed_cost_usd: string;
    },
  ) => api.post<DesignRun>(`/projects/${projectId}/runs`, body),
  runs: (projectId: string) =>
    api.get<{ items: DesignRun[] }>(`/projects/${projectId}/runs`),
  run: (projectId: string, runId: string) =>
    api.get<DesignRun>(`/projects/${projectId}/runs/${runId}`),
  stopRun: (projectId: string, runId: string) =>
    api.post<DesignRun>(`/projects/${projectId}/runs/${runId}/stop`),
  ranking: (projectId: string, runId: string) =>
    api.get<Ranking>(`/projects/${projectId}/runs/${runId}/ranking`),
  structureUrl: (projectId: string, candidateId: string) =>
    `/api/v1/projects/${projectId}/candidates/${candidateId}/structure.cif`,
  runEventsUrl: (projectId: string, runId: string) =>
    `/api/v1/projects/${projectId}/runs/${runId}/events`,
};

/* --- presentation helpers --------------------------------------------- */

/** Format a model confidence without implying more precision than it has. */
export function formatMetric(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value === 0) return "0";
  const magnitude = Math.abs(value);
  if (magnitude < 0.001) return value.toExponential(2);
  if (magnitude < 1) return value.toFixed(4);
  return value.toFixed(3);
}

export function formatMoney(value: string | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const n = Number(value);
  return Number.isFinite(n) ? `$${n.toFixed(2)}` : `$${value}`;
}

export const VERDICT_LABEL: Record<Verdict, string> = {
  worth_testing_first: "Worth testing first",
  mixed: "Mixed signals",
  weak: "Weak",
  no_predicted_binding: "Not predicted to stick",
};

/** A band's colour token. Negligible is deliberately not red-alarming: it is
 *  the expected outcome for a small run, not an error. */
export const BAND_TONE: Record<string, string> = {
  high: "text-good",
  moderate: "text-good",
  low: "text-caution",
  negligible: "text-muted",
  "not reported": "text-faint",
};
