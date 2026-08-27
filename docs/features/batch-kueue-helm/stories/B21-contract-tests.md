---
type: Product Backlog Item
id: 2851
parent: 2800
title: Contract tests between the wrapper, the reconciler and the browser
---

# B21 · Contract tests between the wrapper, the reconciler and the browser

**Story.** As the team, we want the *agreements* between components — the
exit codes the wrapper returns and the reconciler acts on, the environment
variables the chart renders and the wrapper reads, the exact shape of
`status.json` the browser displays — pinned by tests, so that changing one
side without the other fails in CI rather than on the cluster.

## Why it matters

The four codebases never call each other; they agree on files and
numbers. Those agreements are invisible to ordinary unit tests, and they
are where the audit found the real bugs.

## What this delivers

- A **golden `status.json`**: the reconciler's output for a fixed scenario,
  checked byte-for-byte, and the same file used as the browser's fixture.
- **Exit-code and environment contract tests**: the wrapper's exit codes
  and the reconciler's interpretation are one shared table; the chart's
  rendered environment is checked against what the wrapper's settings
  accept.
- Kube adapter tests against real client models with honest label
  selectors, so a Job the reconciler creates is one it can find again.

## Done when

- [ ] Changing an exit code, a status field or a rendered env var on one
      side without the other fails CI.
- [ ] The browser's fixture and the reconciler's golden file are the same
      artefact.
