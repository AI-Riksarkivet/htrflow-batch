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
