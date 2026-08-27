// Runtime/build-time configuration in one place.
//
// Resolution order for the status URL, highest first:
//   1. window.STATUS_URL — set at deploy time by overwriting /config.js
//      (same-origin, so it passes the CSP); also settable from the browser
//      console for a one-off (the next poll picks it up, no rebuild).
//   2. VITE_STATUS_URL — build-time, e.g. `VITE_STATUS_URL=… bun run build`.
//   3. The PoC default below (RustFS NodePort on the local k3s stack).
//
// Poll cadences are build-time only (VITE_RELOAD_MS, VITE_LIVE_MS); the
// defaults match the reconciler's tick and the wrapper's log-ship period.

const env = import.meta.env as Record<string, string | undefined>;

/** Positive integer from a VITE_* variable, else the default. */
export function envInt(value: string | undefined, fallback: number): number {
  if (value === undefined || value === "") return fallback;
  const n = Number(value);
  return Number.isInteger(n) && n > 0 ? n : fallback;
}

export const DEFAULT_STATUS_URL =
  env.VITE_STATUS_URL ??
  "http://localhost:30900/htr-results/status/status.json";

/** Resolved per fetch, not once at init: window.STATUS_URL may be set late. */
export function resolveStatusUrl(
  win: { STATUS_URL?: string } | undefined = typeof window === "undefined"
    ? undefined
    : window,
): string {
  const injected = win?.STATUS_URL;
  return typeof injected === "string" && injected !== ""
    ? injected
    : DEFAULT_STATUS_URL;
}

/** Campaign page re-fetch period (default 60 s; the reconciler ticks every 5 min). */
export const RELOAD_MS = envInt(env.VITE_RELOAD_MS, 60_000);

/** Live log re-fetch period (default 15 s = the wrapper's LOG_SHIP_SECONDS). */
export const LIVE_MS = envInt(env.VITE_LIVE_MS, 15_000);

/** Consecutive failed live polls before the viewer gives up (5 min at LIVE_MS). */
export const LIVE_MAX_FAILURES = 20;
