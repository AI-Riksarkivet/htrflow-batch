// Runtime/build-time configuration in one place.
//
// Resolution order for the read API base, highest first:
//   1. window.API_BASE — set at deploy time by overwriting /config.js
//      (same-origin, so it passes the CSP); also settable from the browser
//      console for a one-off (the next poll picks it up, no rebuild). The
//      chart's viewer.yaml sets this to "/api/v1" (nginx proxies /api/ to
//      the read API Service) — never the in-cluster Service DNS name, which
//      the browser cannot resolve.
//   2. VITE_API_BASE — build-time, e.g. `VITE_API_BASE=… bun run build`.
//   3. The default below: "/api/v1", same-origin (works for `bun run dev`
//      via Vite's dev-server proxy, and for the built site behind nginx).
//
// Poll cadences are build-time only (VITE_RELOAD_MS, VITE_LIVE_MS); the
// defaults match the wrapper's log-ship period (there is no reconciler tick
// any more — the read API computes everything live on each request).

const env = import.meta.env as Record<string, string | undefined>;

/** Positive integer from a VITE_* variable, else the default. */
export function envInt(value: string | undefined, fallback: number): number {
  if (value === undefined || value === "") return fallback;
  const n = Number(value);
  return Number.isInteger(n) && n > 0 ? n : fallback;
}

export const DEFAULT_API_BASE = env.VITE_API_BASE ?? "/api/v1";

/** Resolved per fetch, not once at init: window.API_BASE may be set late. */
export function resolveApiBase(
  win: { API_BASE?: string } | undefined = typeof window === "undefined"
    ? undefined
    : window,
): string {
  const injected = win?.API_BASE;
  return typeof injected === "string" && injected !== ""
    ? injected
    : DEFAULT_API_BASE;
}

/** Campaign list re-fetch period (default 60 s). */
export const RELOAD_MS = envInt(env.VITE_RELOAD_MS, 60_000);

/** Live log re-fetch period (default 15 s = the wrapper's LOG_SHIP_SECONDS). */
export const LIVE_MS = envInt(env.VITE_LIVE_MS, 15_000);

/** Consecutive failed live polls before the viewer gives up (5 min at LIVE_MS). */
export const LIVE_MAX_FAILURES = 20;
