---
type: Product Backlog Item
id: 2857
parent: 2800
title: Independent repository audit (2026-08-26)
---

# B28 · Independent repository audit (2026-08-26)

**Story.** As the product owner, I want the whole repository reviewed by
readers other than its author — each angle independently, each finding
verified in code or by probing the running system — so that we know what
does *not* yet hold before we call it production-ready.

## What was done

- **Seven independent read-only reviews**, one per angle: reconciler
  correctness, wrapper correctness, security & supply chain, Kubernetes /
  Helm / operability, frontend, documentation drift, tests & CI. Each
  finding had to be verified (throwaway tests, Playwright, read-only
  `kubectl`/`helm`, `curl`) before it counted; nothing was inferred.
- **Cross-checked and deduplicated** into 19 cross-cutting findings
  (X1–X19) over **103 findings in total: 1 critical, 17 high, 41 medium,
  44 low**, each with a `file:line` pointer at the audited commit.
- **A verdict**: sound at the scale run so far (≤ 15 volumes, one GPU),
  pods genuinely locked down — but three assumptions the design leans on
  did not yet hold in the code: (1) failed volumes were not remembered
  once Kubernetes cleaned up the job, so they were resubmitted forever;
  (2) the reconciler's per-tick cost grew with the number of volumes and
  would have wedged at archive scale; (3) the campaigns repo was an
  unguarded code-execution boundary.
- **A recommended order of work**, which became remediation packages A1–A4
  and B1–B2 (stories B29–B32, B21–B27, B08).

## Done when

- [ ] Report published in the docs with every finding traceable to
      evidence.
- [ ] Every finding assigned to a remediation package or explicitly
      deferred with a reason.
