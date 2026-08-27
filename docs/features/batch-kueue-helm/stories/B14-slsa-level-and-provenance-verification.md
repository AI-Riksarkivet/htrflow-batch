---
type: Product Backlog Item
id: 2867
parent: 2800
title: Raise the SLSA level and verify provenance, not just signatures
---

# B14 · Raise the SLSA level and verify provenance, not just signatures

**Story.** As the security owner, I want the cluster to check not only
*that* an image is signed by our CI but *what the provenance says* — built
from our repository, on the expected branch, by the expected workflow —
and I want our build to meet SLSA Build Level 3, so that the supply-chain
claim we make to auditors is the strongest the tooling supports.

## Why it matters

B09 gives us SLSA Build Level 2: provenance exists and is signed. Two
things separate that from the level auditors increasingly ask for:

1. **The policy only checks the signature.** The provenance and SBOM are
   attached but nobody reads them at admission. A policy that verifies the
   provenance predicate closes the gap between "signed by our CI" and
   "built from our source at this commit".
2. **Level 3 needs a hardened builder** — in GitHub terms, the build runs
   in a reusable workflow so the provenance cannot be influenced by the
   calling repository's own steps.

This is a deliberately separate, later story: B13 is the control that
matters most; this is its hardening. In NIS2 terms it raises the quality
of the supply-chain evidence (Art. 21(2)(d), 21(3)): Level 3 provenance
cannot be forged by anyone with write access to our own repository, which
is the residual risk Level 2 leaves open.

## What this delivers

- The publish build moved into a reusable workflow that emits Level-3
  provenance (`slsa-github-generator` or GitHub's Level-3 attestations).
- The Kyverno policy extended with an `attestations` check on the SLSA
  provenance predicate: source repository, workflow, and branch/tag.
- Optionally, an SBOM check at admission (no CRITICAL findings at publish
  time recorded in the attestation).

## Done when

- [ ] `gh attestation verify` reports SLSA Build Level 3 for a published
      image.
- [ ] An image signed by the right identity but with provenance from a
      different repository or branch is refused on the dev cluster.
- [ ] The verification steps are documented so an auditor can repeat them.
