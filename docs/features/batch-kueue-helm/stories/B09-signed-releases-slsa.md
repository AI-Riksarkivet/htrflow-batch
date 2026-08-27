---
type: Product Backlog Item
id: 2850
parent: 2800
title: Signed releases with SLSA provenance and a software bill of materials
---

# B09 · Signed releases with SLSA provenance and a software bill of materials

**Story.** As the security owner, I want every container image we publish
to carry a cryptographic signature, a statement of *how and from what source
it was built* (SLSA provenance), and a list of everything inside it (SBOM),
so that "is this image really ours, and what is in it?" has a verifiable
answer — and so that a cluster policy (B13) has something to check against.

## Why it matters

SLSA (*Supply-chain Levels for Software Artifacts*) is the industry
framework for answering "can this build be trusted?". Without provenance,
an image tag is just a name; anyone with registry access could push
something else under it. With signed provenance, the cluster can refuse
anything that was not produced by our CI from our repository. Scanning the
SBOM also means a newly announced vulnerability can be matched against what
is actually deployed without rebuilding anything.

### Why this is a compliance matter, not just good hygiene — NIS2

**NIS2** (EU directive 2022/2555, transposed in Sweden as the
*cybersäkerhetslag* in force from 2026) covers public administration, so
Riksarkivet is an in-scope entity and its management is personally
accountable for approving and overseeing the cybersecurity risk measures
(Art. 20). Two of the measures the directive requires (Art. 21(2)) map
directly onto this story:

| NIS2 requires | What we would have to show | What answers it |
|---|---|---|
| **(d) Supply-chain security** — including the security of the relationship with each direct supplier, and (Art. 21(3)) taking account of the vulnerabilities and the secure-development practices of each supplier and product | That the software running on our GPUs is exactly what our process built, from the source we reviewed, and not something substituted along the way; that we know which third-party components we depend on and how they were built | **SLSA provenance + signature** — a tamper-evident statement of *who built it, from what, how*, verifiable by an auditor with one command |
| **(e) Security in acquisition, development and maintenance of systems, including vulnerability handling and disclosure** | That when a vulnerability is announced (say, in a Python library or a CUDA base image) we can tell — quickly and with evidence — whether any deployed image contains the affected version | **SBOM** — the machine-readable inventory of every package in every image, attached to the image itself, so the answer is a query, not an investigation |

Two further NIS2 obligations make the *speed* of those answers matter:

- **Incident reporting deadlines (Art. 23)** — an early warning within
  24 hours and a notification within 72 hours of becoming aware of a
  significant incident. A supply-chain incident (a compromised upstream
  package, for example) is only reportable with accuracy if we can list
  what we run; an SBOM per image is what makes "are we affected?" a
  minutes-long question.
- **Effectiveness assessment (Art. 21(2)(f))** — the measures must be
  demonstrably effective, not merely written down. A signature that the
  cluster *enforces* (B13) and a provenance level an auditor can *verify*
  (B14) are effectiveness evidence; a policy document is not.

Adjacent: the **Cyber Resilience Act** (EU 2024/2847) obliges
manufacturers of products with digital elements to ship SBOMs from 2027.
Our upstreams (PyTorch, `htrflow`'s dependencies) will increasingly carry
them; producing our own keeps the chain unbroken from their SBOM to what
runs on our cluster.

In short: SLSA answers *"is this ours?"*, the SBOM answers *"what is in
it?"*, and NIS2 requires us to be able to answer both, fast, with evidence
that management has signed off on.

## What this delivers

- **Release publishing** (`publish.yml`) that builds each component
  (wrapper, reconciler, viewer) with the same Dagger pipeline CI tests,
  pushes it under an immutable tag, and refuses to overwrite a tag that
  already exists.
- **Keyless signing** of the pushed digest with cosign/Sigstore — the
  identity is the GitHub workflow itself, so there is no signing key to
  leak or rotate.
- **SLSA build-provenance attestation** (GitHub artifact attestations —
  SLSA Build Level 2 out of the box) and a **SPDX SBOM attestation**
  generated with Trivy, both attached to the image.
- **Vulnerability scanning** that blocks a release on CRITICAL findings.

## Done when

- [ ] A published image can be verified with `cosign verify` against the
      workflow identity, and its provenance and SBOM retrieved with
      `gh attestation verify`.
- [ ] A CRITICAL CVE in a base image fails the publish job.
- [ ] Re-publishing an existing tag is refused.

## Known gap (closed by B41/B42)

The arm64 GPU wrapper image used on the proof-of-concept node is built
locally (`make poc-push`) because its base image is not published; it is
therefore *not* signed. B42 publishes its base image and B41 builds it in CI; until then a cluster that enforces signatures (B13) would refuse it.
