/**
 * The whole client. Two steps, six calls.
 *
 * Both steps run for minutes on Boltz, so the POST only starts the job and the
 * page polls. That is why every call here is small: there is nothing clever to
 * do, just ask and wait.
 */

import { CSRF_COOKIE } from "@/lib/api";

export type JobState = "pending" | "running" | "complete" | "failed";

export type Prediction = {
  id: string;
  name: string;
  length: number;
  state: JobState;
  error: string;
  livemode: boolean;
  structure_confidence: number | null;
  ptm: number | null;
  has_structure: boolean;
  created_at: string;
};

export type Hit = {
  rank: number;
  smiles: string;
  name: string;
  external_id: string;
  is_control: boolean;
  binding_confidence: number | null;
  structure_confidence: number | null;
  known_potency_nm: number | null;
  lipophilicity: number | null;
  permeability: number | null;
  solubility: string;
};

export type Screen = {
  id: string;
  prediction_id: string;
  state: JobState;
  error: string;
  livemode: boolean;
  submitted: number;
  returned: number;
  control_rank: number | null;
  control_name: string;
  /** null when there was no control to judge by, which only happens in test mode. */
  control_looks_right: boolean | null;
  hits: Hit[];
  created_at: string;
};

/** The enzyme families, in the words a microbiologist uses for them. */
export const AMBLER_CLASSES = [
  { value: "A", label: "Class A — KPC, CTX-M, TEM, SHV" },
  { value: "B", label: "Class B — metallo: NDM, VIM, IMP" },
  { value: "C", label: "Class C — AmpC, CMY" },
  { value: "D", label: "Class D — OXA" },
] as const;

function readCsrf(): string {
  const match = document.cookie.match(
    new RegExp(`(?:^|; )${CSRF_COOKIE}=([^;]*)`),
  );
  return match ? decodeURIComponent(match[1]) : "";
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    accept: "application/json",
    ...(init.body ? { "content-type": "application/json" } : {}),
  };
  if (init.method && init.method !== "GET") headers["x-csrf-token"] = readCsrf();
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    headers,
    credentials: "same-origin",
  });
  const text = await response.text();
  const parsed: unknown = text ? JSON.parse(text) : {};
  if (!response.ok) {
    const problem = parsed as { detail?: string; title?: string };
    throw new Error(
      problem.detail ?? problem.title ?? `Something went wrong (${response.status}).`,
    );
  }
  return parsed as T;
}

export const lab = {
  predictions: () => call<Prediction[]>("/predictions"),
  prediction: (id: string) => call<Prediction>(`/predictions/${id}`),
  predict: (sequence: string, name: string) =>
    call<Prediction>("/predictions", {
      method: "POST",
      body: JSON.stringify({ sequence, name }),
    }),
  screens: (predictionId: string) =>
    call<Screen[]>(`/predictions/${predictionId}/screens`),
  screen: (predictionId: string, amblerClass: string) =>
    call<Screen>(`/predictions/${predictionId}/screens`, {
      method: "POST",
      body: JSON.stringify({ ambler_class: amblerClass }),
    }),
};

/** Served from our own storage, never the engine's expiring URL. */
export function structureUrl(predictionId: string): string {
  return `/api/v1/predictions/${predictionId}/structure.cif`;
}

export function shortlistUrl(screenId: string): string {
  return `/api/v1/screens/${screenId}/shortlist.csv`;
}

/** A sequence to try, for anyone who arrives without one to hand. */
export const EXAMPLE = {
  name: "KPC-2 carbapenemase (Klebsiella pneumoniae)",
  sequence:
    "MSIQHFRVALIPFFAAFCLPVFAHPETLVKVKDAEDQLGARVGYIELDLNSGKILESFRPEERFPMMSTFKVLLCGAVLSR" +
    "IDAGQEQLGRRIHYSQNDLVEYSPVTEKHLTDGMTVRELCSAAITMSDNTAANLLLTTIGGPKELTAFLHNMGDHVTRLDR" +
    "WEPELNEAIPNDERDTTMPVAMATTLRKLLTGELLTLASRQQLIDWMEADKVAGPLLRSALPAGWFIADKSGAGERGSRGI" +
    "IAALGPDGKPSRIVVIYTTGSQATMDERNRQIAEIGASLIKHW",
};
