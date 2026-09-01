// The read API boundary (packages/api, GET /api/v1/jobs — docs:
// task-4-brief / task-7-brief). Unlike the old reconciler-written status
// document, this is our own API: a malformed response is a bug on our side,
// not an untrusted document, so parsing fails hard (Zod .parse, not
// .safeParse) instead of degrading row by row. Also carries the small pure
// view helpers every route needs (isHttpUrl, shortDate), now that the old
// status/derive modules are gone.
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
  reason: z.string().optional(),
});

// GET /api/v1/jobs/{namespace}/{name}: JobSummary + paged volumes/failures.
export const jobDetailSchema = jobSummarySchema.extend({
  failures: z.array(volumeViewSchema),
  volumes: z.array(volumeViewSchema),
});

export type JobPhase = z.infer<typeof jobPhaseSchema>;
export type JobCounts = z.infer<typeof jobCountsSchema>;
export type JobSummary = z.infer<typeof jobSummarySchema>;
export type VolumeState = z.infer<typeof volumeStateSchema>;
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

/** GET /api/v1/jobs — every campaign Job, newest first (server-sorted). */
export async function fetchJobs(): Promise<JobSummary[]> {
  const raw = await getJson(`${resolveApiBase()}/jobs`);
  return z.array(jobSummarySchema).parse(raw);
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
