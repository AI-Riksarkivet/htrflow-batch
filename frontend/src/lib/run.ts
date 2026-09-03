// The wrapper's manifest.json boundary (packages/wrapper/.../main.py) and
// the pure summary math behind the run viewer. The manifest is written once,
// when the wrapper finishes, and carries one result per page.
import { z } from "zod";
import { isHttpUrl } from "./api.js";

export const pageResultSchema = z.object({
  // "ok" | "failed" | "skipped" today; kept open so a new outcome renders as
  // a neutral cell instead of hiding the whole manifest.
  status: z.string(),
  seconds: z.number(),
  error: z.string().optional(),
});

export const runManifestSchema = z
  .object({
    volume: z.string(),
    pipeline_id: z.string(),
    htrflow_version: z.string(),
    image_digest: z.string(),
    pages: z.number(),
    results: z.record(pageResultSchema),
    pipeline_yaml: z.string().optional(),
    wall_seconds: z.number().optional(),
    bytes_fetched: z.number().optional(),
    pages_per_second: z.number().optional(),
    // Newer wrappers: the image each page was fetched from and its canvas
    // id; older runs have neither.
    page_sources: z.record(z.string()).optional(),
    canvas_ids: z.record(z.string().nullable()).optional(),
  })
  .passthrough();

export type PageResult = z.infer<typeof pageResultSchema>;
export type RunManifest = z.infer<typeof runManifestSchema>;

export interface PageStat extends PageResult {
  id: string;
  /** Source image URL when the manifest carries one and it is http(s). */
  source?: string;
}

export interface RunSummary {
  pages: number;
  ok: number;
  failed: number;
  skipped: number;
  totalSeconds: number;
  /** Timing stats over pages that did work (everything but `skipped`). */
  median: number | null;
  p95: number | null;
  max: number | null;
  /** Up to five slowest pages, slowest first. */
  slowest: PageStat[];
  /** Failed pages in id order, each with its error. */
  failedPages: PageStat[];
}

/** Linear-interpolated percentile of an ascending list; null when empty. */
export function percentile(
  sorted: readonly number[],
  p: number,
): number | null {
  if (sorted.length === 0) return null;
  const pos = (sorted.length - 1) * p;
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  const a = sorted[lo] as number;
  const b = sorted[hi] as number;
  return a + (b - a) * (pos - lo);
}

export function pageStats(
  results: Record<string, PageResult>,
  sources: Record<string, string> = {},
): PageStat[] {
  return Object.entries(results)
    .map(([id, r]) => {
      const source = sources[id];
      return source !== undefined && isHttpUrl(source)
        ? { id, ...r, source }
        : { id, ...r };
    })
    .sort((a, b) => a.id.localeCompare(b.id));
}

export function summarizeRun(
  results: Record<string, PageResult>,
  sources: Record<string, string> = {},
): RunSummary {
  const pages = pageStats(results, sources);
  const timed = pages.filter((p) => p.status !== "skipped");
  const seconds = timed.map((p) => p.seconds).sort((a, b) => a - b);
  const slowest = [...timed].sort((a, b) => b.seconds - a.seconds).slice(0, 5);
  return {
    pages: pages.length,
    ok: pages.filter((p) => p.status === "ok").length,
    failed: pages.filter((p) => p.status === "failed").length,
    skipped: pages.length - timed.length,
    totalSeconds: pages.reduce((acc, p) => acc + p.seconds, 0),
    median: percentile(seconds, 0.5),
    p95: percentile(seconds, 0.95),
    max: seconds.length > 0 ? (seconds[seconds.length - 1] as number) : null,
    slowest,
    failedPages: pages.filter((p) => p.status === "failed"),
  };
}

/**
 * A page cell's bar height as a fraction of the slowest page's seconds. The
 * 0.12 floor keeps a fast page visible as a sliver instead of nothing; no
 * usable maximum (a run where every page was skipped) means no bar at all.
 */
export function scale(seconds: number, max: number | null): number {
  if (max === null || max <= 0) return 0;
  return Math.max(0.12, Math.min(1, seconds / max));
}

/** "12.3 s" under a minute, "1 h 43 min 38 s" above — a 480-page run is hours. */
export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const whole = Math.round(seconds);
  const h = Math.floor(whole / 3600);
  const m = Math.floor((whole % 3600) / 60);
  const s = whole % 60;
  return h > 0 ? `${h} h ${m} min ${s} s` : `${m} min ${s} s`;
}

/**
 * The wrapper writes manifest.json only when the volume's run has ended, with
 * one result per page — so a manifest whose results cover every page belongs
 * to a finished run and the live poll can stop.
 */
export function isTerminalManifest(m: RunManifest): boolean {
  return m.pages > 0 && Object.keys(m.results).length >= m.pages;
}
