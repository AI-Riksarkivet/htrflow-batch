// The read API boundary (packages/web, GET /api/v1/jobs — docs:
// reference/frontend.md). This is our own API, not a document we found: a
// response of the wrong shape is a bug on our side and parsing fails hard
// (Zod .parse). One campaign row the page cannot read is still a bug, but
// not a reason to hide every other campaign (B32): that row is left out,
// counted for the banner and logged once for the operator. Also carries the
// small pure view helpers every route needs (isHttpUrl, shortDate), since
// there is no derivation layer left to keep them in.
import { z } from "zod";
import { resolveApiBase, resolveResultsBase } from "./config.js";

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

/**
 * An http(s) URL this deployment's own results bucket serves. The run-log
 * route takes its `?log=` and `?manifest=` straight out of the query
 * string, so a link mailed to someone could point the page at any host at
 * all (2026-09-14 audit). An unset base — nobody said where the results
 * are — accepts any absolute http(s) URL, which is what it did before.
 */
export function isResultUrl(
  value: string,
  base: string = resolveResultsBase(),
): boolean {
  if (!isHttpUrl(value)) return false;
  if (base === "") return true;
  // Compared as URLs, not as text: `<base>/../evil.txt` starts with the
  // base and is not under it at all, and a host written in another case is
  // the same host (2026-09-14 review).
  let here: string;
  let root: string;
  try {
    here = new URL(value).href;
    root = new URL(base).href;
  } catch {
    return false;
  }
  return here.startsWith(root.endsWith("/") ? root : `${root}/`);
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

/**
 * A field this page turns into an href or a fetch target. They are the read
 * API's own, so one that is not an absolute http(s) URL is a bug on our
 * side and the row is unreadable — the same answer a field of the wrong
 * type gets (2026-09-14 audit).
 */
export const httpUrlSchema = z
  .string()
  .refine(isHttpUrl, { message: "must be an absolute http(s) URL" });

/** "10:56" — the clock half of `shortDate`, for the end of a same-day range. */
export function clockTime(iso: string, timeZone?: string): string | null {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone,
  });
}

/**
 * Whether two timestamps fall on the same day where the reader is. A
 * campaign that started and finished this morning reads `10:56 → 11:00`;
 * one that ran overnight has to say both dates.
 */
export function sameDay(a: string, b: string, timeZone?: string): boolean {
  const day = (iso: string) => {
    const d = new Date(iso);
    return Number.isNaN(d.getTime())
      ? null
      : d.toLocaleDateString("en-GB", {
          day: "numeric",
          month: "short",
          year: "numeric",
          timeZone,
        });
  };
  const first = day(a);
  return first !== null && first === day(b);
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
  // The campaign's Job is gone and no terminal record was ever written for
  // it (a Job deleted by hand or by a prune). Nothing is running, and how
  // it ended is not on record -- which is honest, where a `Running` that
  // can never change is not. Only ever seen with `jobGone` (B76).
  "Unknown",
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
  // When the campaign stopped, as the API observed it. Additive, and
  // defaulted: an older API answers without it.
  finishedAt: z.string().nullable().default(null),
  resultsBase: httpUrlSchema,
  warmup: warmupSchema,
  // The campaign's Job is past its `ttlSecondsAfterFinished` and has been
  // removed; this row is served from the campaign's ConfigMap and the
  // status ConfigMap beside it, which have no TTL (B76). The counts and the
  // dates are what the API last observed, and there are no per-volume rows.
  jobGone: z.boolean().default(false),
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
  // Seconds since updatedAt, computed by the API from its own clock at fetch
  // time -- never derived here from updatedAt and the browser's Date.now(),
  // since a reader's clock skew would then make "updated Ns ago" wrong (a
  // stale row could even read "0 s ago").
  ageSeconds: z.number().nullable(),
  // The most recent page failure and the ERROR-and-worse count (never
  // WARNING: the wrapper's own benign ones, a pipeline rebuild, "manifest
  // covers n/m pages", must not read as something wrong on a healthy run),
  // so a run with an exception in its log says so on the campaign page
  // instead of only in a log someone has to open. Rendering is $lib/reasons,
  // like `reason`.
  lastError: z
    .object({ page: z.string().nullable(), error: z.string() })
    .nullable(),
  errors: z.number(),
  // True only once the wrapper's own iiif.json PUT has actually succeeded
  // (interim or final) -- what the "open in the viewer" link switches on,
  // never a page count (a volume under PUBLISH_EVERY_PAGES pages would
  // otherwise link to a manifest that is not there yet).
  viewerPublished: z.boolean(),
});

export const volumeStateSchema = z.enum([
  "pending",
  "active",
  "done",
  "failed",
  // The campaign's Job is gone and no terminal record was ever written for
  // it, so what this volume did was never observed by anything. Only ever
  // seen on a `jobGone` campaign whose phase is `Unknown`; the row's links
  // still work, and its progress file says what actually happened when
  // there is one (2026-09-14 review).
  "unknown",
]);

// One row per line of the campaign's volumes.txt ConfigMap.
export const volumeViewSchema = z.object({
  index: z.number(),
  id: z.string(),
  state: volumeStateSchema,
  manifestUrl: httpUrlSchema,
  iiifUrl: httpUrlSchema,
  altoPrefix: httpUrlSchema,
  logUrl: httpUrlSchema,
  // The volume's source manifest, straight off its volumes.txt line; null
  // for an `images:` volume, which has no manifest to open. The one URL
  // field the API does not build: it is a line of a file people edit, and
  // the API's check and this browser's URL parser need not agree on every
  // string. So it is read on its own -- one this page cannot use is no
  // link, where it used to make the whole campaign unreadable, re-polled
  // for ever (2026-09-23 audit).
  sourceUrl: httpUrlSchema.nullable().catch(null),
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
  // Summed over every volume of the campaign whose progress the API has
  // read — from its cache, and at most PROGRESS_FETCH_CAP bucket reads per
  // request for the rest — so they fill in over a few polls rather than
  // being the loaded page's. Both 0 when none is known.
  pagesDone: z.number(),
  pagesTotal: z.number(),
  // How many of the campaign's run volumes those sums cover. Until `counted`
  // reaches `of`, a volume that lost pages may simply not be read yet.
  pagesCoverage: z.object({ counted: z.number(), of: z.number() }),
  // The same three, campaign-wide: the failed pages and errors summed, and
  // the most recent error with the volume it happened in and that volume's
  // run log — the row it came from is usually outside the page being shown.
  pagesFailed: z.number(),
  errors: z.number(),
  lastError: z
    .object({
      page: z.string().nullable(),
      error: z.string(),
      volume: z.string(),
      logUrl: httpUrlSchema,
    })
    .nullable(),
});

export type JobPhase = z.infer<typeof jobPhaseSchema>;
export type JobCounts = z.infer<typeof jobCountsSchema>;
export type WarmupPhase = z.infer<typeof warmupPhaseSchema>;
export type Warmup = z.infer<typeof warmupSchema>;
export type JobSummary = z.infer<typeof jobSummarySchema>;
export type VolumeState = z.infer<typeof volumeStateSchema>;
export type VolumeReason = z.infer<typeof volumeReasonSchema>;
export type VolumeProgress = z.infer<typeof volumeProgressSchema>;
export type CampaignNotice = Pick<
  JobDetail,
  "pagesFailed" | "errors" | "lastError"
>;
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

async function getJson(url: string, signal?: AbortSignal): Promise<unknown> {
  return (await getResponse(url, signal)).json();
}

async function getResponse(
  url: string,
  signal?: AbortSignal,
): Promise<Response> {
  let res: Response;
  try {
    // The signal is what makes abandoning a poll actually stop it: without
    // it the request ran to completion and only its answer was dropped
    // (2026-09-14 audit).
    res = await fetch(url, { cache: "no-store", signal: signal ?? null });
  } catch (e) {
    throw new ApiUnreachable(e instanceof Error ? e.message : String(e), {
      cause: e,
    });
  }
  if (!res.ok) throw new ApiUnreachable(`HTTP ${res.status}`);
  return res;
}

/**
 * The campaign list as the page shows it: rows it could read, how many it
 * could not, and how many campaigns whose Jobs are gone the API has in all
 * -- it sends only as many of those as it was asked for.
 */
export type JobList = {
  jobs: JobSummary[];
  unreadable: number;
  reapedTotal: number;
};

/**
 * How many campaigns whose Jobs are gone the page asks for at a time. Their
 * records have no TTL, so without a window every campaign ever run was a
 * card (2026-09-23 audit); the API's own default is the same number.
 */
export const REAPED_PAGE = 20;

/**
 * GET /api/v1/version — what is deployed: `version` is the tag both images
 * are published under, baked into the image at build time (`dev` for a local
 * build), and `web` is the read API package's own version beside it. The tag
 * is what an operator chose and what the header shows; the package versions
 * move independently of it.
 */
export const versionSchema = z.object({
  version: z.string(),
  web: z.string(),
});

export type Version = z.infer<typeof versionSchema>;

export async function fetchVersion(): Promise<Version> {
  return versionSchema.parse(await getJson(`${resolveApiBase()}/version`));
}

/**
 * GET /api/v1/jobs?reaped= — every campaign Job and the `reaped` newest
 * campaigns whose Jobs are gone, newest first (server-sorted). The total of
 * those is the X-Reaped-Total header; an API that sends none has no more
 * than it sent.
 */
export async function fetchJobs(
  signal?: AbortSignal,
  reaped: number = REAPED_PAGE,
): Promise<JobList> {
  const res = await getResponse(
    `${resolveApiBase()}/jobs?reaped=${reaped}`,
    signal,
  );
  const rows = z.array(z.unknown()).parse(await res.json());
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
  const total = Number(res.headers.get("x-reaped-total") ?? "");
  const sent = jobs.filter((j) => j.jobGone).length;
  const reapedTotal =
    res.headers.has("x-reaped-total") && Number.isInteger(total) && total >= 0
      ? Math.max(total, sent)
      : sent;
  return { jobs, unreadable, reapedTotal };
}

/** GET /api/v1/jobs/{namespace}/{name}?offset&limit — volumes paged by index. */
export async function fetchJob(
  namespace: string,
  name: string,
  offset = 0,
  limit = 200,
  signal?: AbortSignal,
): Promise<JobDetail> {
  const url =
    `${resolveApiBase()}/jobs/${encodeURIComponent(namespace)}/${encodeURIComponent(name)}` +
    `?offset=${offset}&limit=${limit}`;
  const raw = await getJson(url, signal);
  return jobDetailSchema.parse(raw);
}
