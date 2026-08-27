// Runtime configuration for the campaign browser. This file is served
// same-origin and loaded before the app, so it passes the page's CSP
// (script-src 'self'); an inline <script> injected into index.html would
// be blocked. Overwrite it at deploy time to point at another status.json:
//
//   window.STATUS_URL = "https://results.example.org/status/status.json";
//
// Left unset, the app falls back to its build-time default (see
// src/lib/config.ts and the README).
