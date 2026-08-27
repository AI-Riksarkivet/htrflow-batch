// Pure view-derivation over status.json (schema: reconciler main.py).
import type { TickSummary, VolumeEntry } from "./status.js";

/** A reconcile older than this many ticks means the reconciler is presumed dead. */
const STALE_TICKS = 3;

/**
 * Only absolute http(s) URLs may reach an href/src: campaign data and the
 * query string are untrusted, and `javascript:` / `data:` would otherwise
 * render straight into the DOM.
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

/** null when the volume has no usable manifest to open (invalid URL). */
export function viewerHref(volume: VolumeEntry): string | null {
  const manifest =
    volume.status === "done" && volume.viewer_manifest !== null
      ? volume.viewer_manifest
      : volume.source_manifest;
  return manifest === null ? null : `uv.html#?manifest=${manifest}`;
}

export type CampaignHealth = "failed" | "active" | "done" | "idle";

/**
 * One-glance verdict for a campaign, worst volume wins: anything that needs a
 * human beats anything still moving, which beats "all published"; a campaign
 * with nothing done and nothing moving is idle (planned/empty).
 */
const FAILED_STATUSES: ReadonlySet<VolumeEntry["status"]> = new Set([
  "needs-attention",
  "unreachable",
  "unsupported",
]);
const ACTIVE_STATUSES: ReadonlySet<VolumeEntry["status"]> = new Set([
  "running",
  "queued",
  "retry",
  "deleting",
]);

export function campaignHealth(
  volumes: readonly { status: VolumeEntry["status"] }[],
): CampaignHealth {
  const statuses = volumes.map((v) => v.status);
  if (statuses.some((s) => FAILED_STATUSES.has(s))) return "failed";
  if (statuses.some((s) => ACTIVE_STATUSES.has(s))) return "active";
  if (statuses.length > 0 && statuses.every((s) => s === "done")) return "done";
  return "idle";
}

export function isStale(
  generatedAt: string,
  tickSeconds: number,
  now: Date = new Date(),
): boolean {
  const ageSeconds = (now.getTime() - new Date(generatedAt).getTime()) / 1000;
  return ageSeconds > STALE_TICKS * tickSeconds;
}

/**
 * Formats a pages label from pages_done and pages_total counts.
 * Treats null pages_done as 0. Returns null if pages_total is unknown.
 */
export function pagesLabel(totals: {
  pages_done: number | null;
  pages_total: number | null;
}): string | null {
  if (totals.pages_total === null) return null;
  return `${totals.pages_done ?? 0}/${totals.pages_total} pages`;
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
 * "last tick 4.1 s · 12 S3 calls · 3 validations · 1 submitted · 0 retried"
 * — only the fields the reconciler sent; null when it sent none (or an old
 * document with no summary at all).
 */
export function tickSummaryLabel(summary: TickSummary | null): string | null {
  if (summary === null) return null;
  const parts: string[] = [];
  if (summary.seconds !== undefined)
    parts.push(`last tick ${summary.seconds.toFixed(1)} s`);
  if (summary.s3_calls !== undefined)
    parts.push(`${summary.s3_calls} S3 calls`);
  if (summary.validations !== undefined)
    parts.push(`${summary.validations} validations`);
  if (summary.submitted !== undefined)
    parts.push(`${summary.submitted} submitted`);
  if (summary.retried !== undefined) parts.push(`${summary.retried} retried`);
  return parts.length > 0 ? parts.join(" · ") : null;
}
