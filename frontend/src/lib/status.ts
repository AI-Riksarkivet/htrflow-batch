// The status.json boundary: parse, don't validate. Shape mirrors the
// reconciler's emitted document (packages/reconciler/.../main.py).
//
// Fail soft: one bad field must not blank the page. The envelope is parsed
// strictly (without it there is nothing to show); each campaign and each
// volume is parsed on its own, and a bad entry degrades to an error row
// while the rest of the document renders as usual.
import { z, type ZodIssue } from "zod";
import { isHttpUrl } from "./derive.js";

// Every URL field: anything but an absolute http(s) URL becomes null (and a
// warning row) rather than an href. `source_manifest` used to be required;
// a volume whose source is not a URL now renders "invalid url" in its slot.
const httpUrlOrNull = z
  .string()
  .nullable()
  .default(null)
  .transform((value) => (value !== null && isHttpUrl(value) ? value : null));

export const volumeStatusSchema = z
  .enum([
    "done",
    "running",
    "queued",
    "retry",
    "needs-attention",
    "pending",
    "unreachable",
    "unsupported",
    // A status this build does not know (a newer reconciler) renders as a
    // neutral chip instead of failing the whole document.
    "unknown",
  ])
  .catch("unknown");

export const volumeEntrySchema = z.object({
  id: z.string(),
  status: volumeStatusSchema,
  attempts: z.number(),
  pages_done: z.number().nullable(),
  pages_total: z.number().nullable(),
  error: z.string().nullable(),
  viewer_manifest: httpUrlOrNull,
  // manifest.json of the run (live or finished) — for the run viewer's
  // summary card; null for volumes whose pod never started.
  run_manifest: httpUrlOrNull,
  source_manifest: httpUrlOrNull,
  thumbnail: httpUrlOrNull,
  updated: z.string().nullable().default(null),
  failure_log: httpUrlOrNull,
  run_log: httpUrlOrNull,
});

const URL_FIELDS = [
  "viewer_manifest",
  "run_manifest",
  "source_manifest",
  "thumbnail",
  "failure_log",
  "run_log",
] as const;

const totalsSchema = z.object({
  done: z.number(),
  total: z.number(),
  pages_done: z.number().nullable().default(null),
  pages_total: z.number().nullable().default(null),
});

export const campaignEntrySchema = z.object({
  name: z.string(),
  pipeline: z.string().nullable(),
  pipeline_steps: z.array(z.string()).nullable().default(null),
  pipeline_yaml: z.string().nullable().default(null),
  error: z.string().nullable(),
  totals: totalsSchema,
  volumes: z.array(volumeEntrySchema),
  orphans: z.array(z.string()).default([]),
});

export const statusDocSchema = z.object({
  generated_at: z.string(),
  tick_seconds: z.number(),
  campaigns_repo_url: z.string().nullable().default(null),
  warnings: z.array(z.string()),
  campaigns: z.array(campaignEntrySchema),
});

export type VolumeStatus = z.infer<typeof volumeStatusSchema>;
/** `invalid` marks a row that stands in for an entry that did not parse. */
export type VolumeEntry = z.infer<typeof volumeEntrySchema> & { invalid?: true };
export type CampaignEntry = Omit<z.infer<typeof campaignEntrySchema>, "volumes"> & {
  volumes: VolumeEntry[];
};
export type StatusDoc = Omit<z.infer<typeof statusDocSchema>, "campaigns"> & {
  campaigns: CampaignEntry[];
};

/** "volumes[2].attempts: expected number, received string" — no ZodError dumps. */
export function formatIssues(issues: readonly ZodIssue[]): string {
  return issues
    .map((issue) => {
      const path = issue.path
        .map((p, i) => (typeof p === "number" ? `[${p}]` : i === 0 ? p : `.${p}`))
        .join("");
      const message = issue.message.charAt(0).toLowerCase() + issue.message.slice(1);
      return path === "" ? message : `${path}: ${message}`;
    })
    .join("; ");
}

export interface ParsedStatus {
  /** null only when the envelope itself is unusable. */
  doc: StatusDoc | null;
  /** One line per degraded entry (or per envelope issue when doc is null). */
  problems: string[];
}

const INVALID = "invalid status entry";

function nameOf(raw: unknown, key: string, fallback: string): string {
  if (raw !== null && typeof raw === "object") {
    const v = (raw as Record<string, unknown>)[key];
    if (typeof v === "string" && v !== "") return v;
  }
  return fallback;
}

function degradedVolume(raw: unknown, index: number, why: string): VolumeEntry {
  return {
    id: nameOf(raw, "id", `volume #${index + 1}`),
    status: "unknown",
    attempts: 0,
    pages_done: null,
    pages_total: null,
    error: `${INVALID}: ${why}`,
    viewer_manifest: null,
    run_manifest: null,
    source_manifest: null,
    thumbnail: null,
    updated: null,
    failure_log: null,
    run_log: null,
    invalid: true,
  };
}

/** Field names whose raw value was a string the schema refused as a URL. */
function refusedUrls(raw: unknown, parsed: VolumeEntry): string[] {
  if (raw === null || typeof raw !== "object") return [];
  const r = raw as Record<string, unknown>;
  return URL_FIELDS.filter((f) => typeof r[f] === "string" && parsed[f] === null);
}

function degradedCampaign(raw: unknown, index: number, why: string): CampaignEntry {
  return {
    name: nameOf(raw, "name", `campaign #${index + 1}`),
    pipeline: null,
    pipeline_steps: null,
    pipeline_yaml: null,
    error: `${INVALID}: ${why}`,
    totals: { done: 0, total: 0, pages_done: null, pages_total: null },
    volumes: [],
    orphans: [],
  };
}

const envelopeSchema = statusDocSchema.extend({ campaigns: z.array(z.unknown()) });
const campaignShellSchema = campaignEntrySchema.extend({ volumes: z.array(z.unknown()) });

export function parseStatusDoc(raw: unknown): ParsedStatus {
  const envelope = envelopeSchema.safeParse(raw);
  if (!envelope.success) {
    return { doc: null, problems: [formatIssues(envelope.error.issues)] };
  }
  const problems: string[] = [];
  const campaigns = envelope.data.campaigns.map((rawCampaign, ci) => {
    const shell = campaignShellSchema.safeParse(rawCampaign);
    if (!shell.success) {
      const why = formatIssues(shell.error.issues);
      const degraded = degradedCampaign(rawCampaign, ci, why);
      problems.push(`${degraded.name}: ${why}`);
      return degraded;
    }
    const volumes = shell.data.volumes.map((rawVolume, vi) => {
      const parsed = volumeEntrySchema.safeParse(rawVolume);
      if (parsed.success) {
        for (const field of refusedUrls(rawVolume, parsed.data)) {
          problems.push(
            `${shell.data.name}/${parsed.data.id}: ${field} is not an http(s) URL, ignored`,
          );
        }
        return parsed.data;
      }
      const why = formatIssues(parsed.error.issues);
      const degraded = degradedVolume(rawVolume, vi, why);
      problems.push(`${shell.data.name}/${degraded.id}: ${why}`);
      return degraded;
    });
    return { ...shell.data, volumes };
  });
  return { doc: { ...envelope.data, campaigns }, problems };
}
