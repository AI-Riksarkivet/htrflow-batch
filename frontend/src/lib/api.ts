// The read API boundary (packages/web, GET /api/v1/jobs — docs:
// reference/frontend.md). This is our own API, not a document we found: a
// response of the wrong shape is a bug on our side and parsing fails hard
// (Zod .parse). One campaign row the page cannot read is still a bug, but
// not a reason to hide every other campaign (B32): that row is left out,
// counted for the banner and logged once for the operator. Also carries the
// small pure view helpers every route needs (isHttpUrl, shortDate), since
// there is no derivation layer left to keep them in.
import { z } from "zod";
import { resolveApiBase } from "./config.js";

/**
 * Only absolute http(s) URLs may reach an href/src: query strings and
 * console-set config are untrusted, and `javascript:`/`data:` would
 * otherwise render straight into the DOM.
 */
export function isHttpUrl(value: string): boolean {
  if (!/^https?:\/\//i.test(value)) return false;
  try {
    new URL(value);
    return true;
  } catch {
    return false;
  }
}

/** "25 Aug, 14:32" — viewer-local unless a timeZone is forced (tests use UTC). */
export function shortDate(
  iso: string | null,
  timeZone?: string,
): string | null {
  if (iso === null) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone,
  });
}

export const jobPhaseSchema = z.enum([
  "Succeeded",
  // The Job gave up with some indexes already published — not the same
  // story as Failed, which means nothing came out of the campaign.
  "PartiallyFailed",
  "Failed",
  "Queued",
  "Paused",
  "Running",
]);

export const jobCountsSchema = z.object({
  total: z.number(),
  active: z.number(),
  done: z.number(),
  failed: z.number(),
});

/**
 * Why a volume or warm-up failed, as the API parsed it out of the wrapper's
 * termination message. `stage`/`permanent` are null when that message was
 * not the wrapper's JSON object (an older wrapper, a kubelet log tail), in
 * which case `error` is the raw text. Rendering is $lib/reasons — never
 * these fields straight into the DOM.
 */
export const volumeReasonSchema = z.object({
  stage: z.string().nullable(),
  permanent: z.boolean().nullable(),
  error: z.string(),
});

// The pipeline's warm-up Job, matched onto this campaign's row (Task 28):
// `missing` means no warm-up Job exists for the pipeline at all, so the
// campaign's pods will sit blocked on it forever.
export const warmupPhaseSchema = z.enum([
  "missing",
  "pending",
  "running",
  "succeeded",
  "failed",
]);

export const warmupSchema = z.object({
  phase: warmupPhaseSchema,
  reason: volumeReasonSchema.optional(),
});

// One row per campaign Job — GET /api/v1/jobs.
export const jobSummarySchema = z.object({
  namespace: z.string(),
  name: z.string(),
  pipeline: z.string(),
  phase: jobPhaseSchema,
  counts: jobCountsSchema,
  suspended: z.boolean(),
  createdAt: z.string().nullable(),
  resultsBase: z.string(),
  warmup: warmupSchema,
});

/**
 * How far a volume has got, read by the API out of the wrapper's
 * progress.json in the results bucket (it is not in the Kubernetes API at
 * all). `null` when there is nothing to read yet: a volume with no pod, a
 * bucket that did not answer, a run that predates the file. `stage` is the
 * wrapper's own (`setup`/`resume`/`load`/`stream`/`verify`/`publish`/`done`);
 * rendering is $lib/reasons, like every other field a person reads.
 */
export const volumeProgressSchema = z.object({
  done: z.number(),
  total: z.number(),
  failed: z.number(),
  lastPage: z.string().nullable(),
  stage: z.string().nullable(),
  updatedAt: z.string().nullable(),
});

export const volumeStateSchema = z.enum([
  "pending",
  "active",
  "done",
  "failed",
]);

// One row per line of the campaign's volumes.txt ConfigMap.
export const volumeViewSchema = z.object({
  index: z.number(),
  id: z.string(),
  state: volumeStateSchema,
  manifestUrl: z.string(),
  iiifUrl: z.string(),
  altoPrefix: z.string(),
  logUrl: z.string(),
  // The volume's source manifest, straight off its volumes.txt line; null
  // for an `images:` volume, which has no manifest to open.
  sourceUrl: z.string().nullable(),
  reason: volumeReasonSchema.optional(),
  progress: volumeProgressSchema.nullable(),
});

// GET /api/v1/jobs/{namespace}/{name}: JobSummary + paged volumes/failures,
// plus the pipeline the campaign runs (detail only — the list would carry
// one YAML document per row for a chip nobody has clicked).
export const jobDetailSchema = jobSummarySchema.extend({
  // Step names in order, for the pipeline chip's tooltip; the raw YAML the
  // chip toggles. Both empty when the pipeline ConfigMap is gone.
  pipelineSteps: z.array(z.string()),
  pipelineYaml: z.string(),
  // The volume a folded card shows: newest active, else newest done, else
  // null. Computed by the API over EVERY volume — picking it here would
  // only ever see the page that happens to be loaded.
  latest: volumeViewSchema.nullable(),
  failures: z.array(volumeViewSchema),
  volumes: z.array(volumeViewSchema),
  // Summed over the volumes THIS response carries (the page, plus `latest`
  // and the failures) — the API reads one progress file per row it answers
  // with, never one per volume in the campaign. Both 0 when none is known.
  pagesDone: z.number(),
  pagesTotal: z.number(),
});

export type JobPhase = z.infer<typeof jobPhaseSchema>;
export type JobCounts = z.infer<typeof jobCountsSchema>;
export type WarmupPhase = z.infer<typeof warmupPhaseSchema>;
export type Warmup = z.infer<typeof warmupSchema>;
export type JobSummary = z.infer<typeof jobSummarySchema>;
export type VolumeState = z.infer<typeof volumeStateSchema>;
export type VolumeReason = z.infer<typeof volumeReasonSchema>;
export type VolumeProgress = z.infer<typeof volumeProgressSchema>;
export type VolumeView = z.infer<typeof volumeViewSchema>;
export type JobDetail = z.infer<typeof jobDetailSchema>;

/**
 * The two ways the read API can be "not there": a network error (DNS,
 * refused connection, CORS) or a non-2xx status. Both render the same
 * "API unreachable" banner — there is no separate STALE/age logic any more,
 * since every response is computed live from the Kubernetes API.
 */
export class ApiUnreachable extends Error {
  constructor(message: string, options?: { cause?: unknown }) {
    super(message, options);
    this.name = "ApiUnreachable";
  }
}

async function getJson(url: string): Promise<unknown> {
  let res: Response;
  try {
    res = await fetch(url, { cache: "no-store" });
  } catch (e) {
    throw new ApiUnreachable(e instanceof Error ? e.message : String(e), {
      cause: e,
    });
  }
  if (!res.ok) throw new ApiUnreachable(`HTTP ${res.status}`);
  return res.json();
}

/** The campaign list as the page shows it: rows it could read, and how many it could not. */
export type JobList = { jobs: JobSummary[]; unreadable: number };

/** GET /api/v1/jobs — every campaign Job, newest first (server-sorted). */
export async function fetchJobs(): Promise<JobList> {
  const rows = z
    .array(z.unknown())
    .parse(await getJson(`${resolveApiBase()}/jobs`));
  const jobs: JobSummary[] = [];
  let unreadable = 0;
  for (const row of rows) {
    const parsed = jobSummarySchema.safeParse(row);
    if (parsed.success) jobs.push(parsed.data);
    else if (++unreadable === 1)
      console.error(
        "campaign row the page cannot read:",
        parsed.error.issues[0],
      );
  }
  // Every row unreadable is the whole list unreadable: fail like a wrong shape.
  if (unreadable > 0 && jobs.length === 0)
    z.array(jobSummarySchema).parse(rows);
  return { jobs, unreadable };
}

/** GET /api/v1/jobs/{namespace}/{name}?offset&limit — volumes paged by index. */
export async function fetchJob(
  namespace: string,
  name: string,
  offset = 0,
  limit = 200,
): Promise<JobDetail> {
  const url =
    `${resolveApiBase()}/jobs/${encodeURIComponent(namespace)}/${encodeURIComponent(name)}` +
    `?offset=${offset}&limit=${limit}`;
  const raw = await getJson(url);
  return jobDetailSchema.parse(raw);
}
