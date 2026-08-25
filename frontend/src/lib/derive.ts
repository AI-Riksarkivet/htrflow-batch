// Pure view-derivation over status.json (schema: reconciler main.py).
import type { VolumeEntry } from "./status.js";

/** A reconcile older than this many ticks means the reconciler is presumed dead. */
const STALE_TICKS = 3;

export function viewerHref(volume: VolumeEntry): string {
  const manifest =
    volume.status === "done" && volume.viewer_manifest !== null
      ? volume.viewer_manifest
      : volume.source_manifest;
  return `uv.html#?manifest=${manifest}`;
}

export function progress(totals: { done: number; total: number }): number {
  return totals.total === 0 ? 0 : Math.round((100 * totals.done) / totals.total);
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
