---
type: Product Backlog Item
id: 2852
parent: 2923
title: Frontend tests and checks
---

# B22 · Frontend tests and checks

**Story.** As a developer touching the campaign browser, I want its
components, routes and data handling tested, its TypeScript strict-checked
and its formatting enforced, so that a status-page regression is caught
before a product owner sees a blank card.

## What this delivers

- **Component and route tests** (vitest on jsdom): every volume state
  renders distinctly, the run-log viewer groups and summarises correctly,
  keyboard navigation works, the volume-scale controls behave.
- **Fail-soft schema tests**: a malformed or partial `status.json` degrades
  to a warning, never a blank page; unexpected URLs are refused.
- **`svelte-check`** in strict TypeScript mode and **prettier** as a check,
  both in CI.

## Done when

- [ ] `bun run test`, `bun run check` and `bun run lint` pass and run in
      CI on every pull request.
- [ ] Coverage is reported for the pure and component tests.
