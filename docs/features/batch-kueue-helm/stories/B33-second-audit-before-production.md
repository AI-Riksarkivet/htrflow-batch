---
type: Product Backlog Item
id: 2893
parent: 2800
title: Second independent audit before production
---

# B33 · Second independent audit before production

**Story.** As the product owner, I want the system audited again — same
method as 2026-08-26, at the exact commit that will go to production, with
the DEV and staging environments as live evidence — so that the promotion decision (B15)
rests on a fresh, independent verdict rather than on the team's own
confidence.

## Why it matters

The first audit found that three of the design's core assumptions did not
hold in the code, and a live bug the tests had missed. Since then the
reconciler, wrapper, chart and frontend have all changed substantially,
new controls have been added (Kyverno policy, repo governance, SLSA
provenance), and the system will be running in an environment it has not
seen before. Auditing the same way, before production, is how we find out
what the *remediation* broke or missed.

## What to do — exactly

1. **Freeze the Freight**: the release that Kargo has promoted to staging
   after B12, B13, B14 and B34 are done; record its image digests and
   chart version in the report.
2. **Run the seven angles again**, independently, each verifying every
   finding in code or by probe: reconciler correctness · wrapper
   correctness · security & supply chain · Kubernetes/Helm/operability ·
   frontend · documentation drift · tests & CI.
3. **Add three angles the first audit could not cover**: the Kyverno
   policy and image allow-list *as enforced* (try to get a foreign image
   in); the campaigns-repo and deployment-repo governance (try to bypass
   the PR check, try to promote to prod without approval); the DEV and
   staging evidence (ingress, network policies, PSA, Argo CD self-heal on
   a shared, multi-tenant cluster).
4. **Re-verify every closed finding from 2026-08-26** — X1–X19 — against
   the current code; a regression is a new high.
5. **Cross-check, deduplicate, rank**, and write the report as
   `docs/audits/<date>-repo-audit.md` in the same format.
6. **Triage every finding into a story** under this feature, or defer it
   with a written reason.

## Done when

- [ ] Report published with severity counts and a verdict.
- [ ] No open *critical* or *high* finding when B15 (production) starts;
      all mediums either fixed or accepted by the product owner in writing.
- [ ] All 2026-08-26 findings re-verified.
