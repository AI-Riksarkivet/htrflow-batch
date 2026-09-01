// Runtime configuration for the campaign browser. This file is served
// same-origin and loaded before the app, so it passes the page's CSP
// (script-src 'self'); an inline <script> injected into index.html would
// be blocked. Overwrite it at deploy time to point at another read API:
//
//   window.API_BASE = "https://batch.example.org/api/v1";
//
// Left unset, the app falls back to its build-time default (see
// src/lib/config.ts and the README). The chart's viewer.yaml renders this
// file with window.API_BASE = "/api/v1" — same-origin, proxied by nginx to
// the read API Service.
