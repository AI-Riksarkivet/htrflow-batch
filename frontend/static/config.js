// Runtime configuration for the campaign browser. This file is served
// same-origin and loaded before the app, so it passes the page's CSP
// (script-src 'self'); an inline <script> injected into index.html would
// be blocked. Overwrite it at deploy time to point at another read API:
//
//   window.API_BASE = "https://batch.example.org/api/v1";
//
// The default below is same-origin: since B63 Task 17 the read API serves
// this very file (packages/web), so /api/v1 is always the API next to the
// page. Deleting the line falls back to the build-time default in
// src/lib/config.ts, which is the same value.
window.API_BASE = "/api/v1";

// Where this deployment's results live — the read API's own
// HTRFLOW_PUBLIC_RESULTS_BASE. The service SERVES this file (packages/web
// builds it from its own environment), so what a browser gets is always
// the API's answer and never this line; the file is here for `bun run dev`
// and for a site-only build, where there is no API to ask. Empty means
// nobody said, and the run-log route then accepts any absolute http(s) URL.
window.RESULTS_BASE = "";
