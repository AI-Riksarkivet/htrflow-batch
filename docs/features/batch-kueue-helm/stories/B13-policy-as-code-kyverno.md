---
type: Product Backlog Item
id: 2866
parent: 2800
title: Only images we built may run — policy as code with Kyverno
---

# B13 · Only images we built may run — policy as code with Kyverno

**Story.** As the security owner, I want the cluster itself to refuse to
start any container in the batch namespace whose image was not built and
signed by our CI, with that rule written as code and installed with the
chart, so that a compromised campaigns repo, a mistyped image name or a
tampered registry cannot get foreign code onto our GPUs.

## Why it matters

The campaigns repo decides which `htrflow` image a pipeline runs. Anyone
who can write to that repo can therefore run any image — the audit called
this out as the system's main code-execution boundary. Today two controls
exist but neither is switched on: the reconciler's image allow-list
(`allowedImageRepos`, a prefix check) and a Kyverno `verifyImages` policy in
the chart that checks the cosign signature at admission. Turning them on,
proving they block what they should, and keeping them on is this story.
B09 makes the signatures exist; this story makes the cluster *demand* them.

For NIS2 this is the difference between a policy and a control: Art. 21
asks for measures that are *effective*, and "the cluster refuses to start
an unsigned image, and here is the rejected attempt in the policy report"
is effectiveness evidence an auditor can check. The same policy is also
the technical answer to the directive's supply-chain requirement (Art.
21(2)(d)): a supplier's or a colleague's mistake cannot become code running
on our infrastructure.

## What this delivers

- **Kyverno installed** on the dev cluster (B12), and the chart's
  `security.verifyImages` policy enabled with the real CI identity (the
  publish workflow's OIDC issuer and subject).
- **`allowedImageRepos` set** to our registry prefix, so the reconciler
  refuses a bad pipeline before a Job even exists, and the Kyverno policy
  refuses it again at admission if anything slips past.
- **Every image in the namespace signed** — including the GPU wrapper,
  which today is built by hand for the PoC (B09's known gap). Either it is
  published and signed through CI on an arm64 runner, or the PoC keeps a
  separate, documented dev-only identity.
- **A negative test** kept in the repo: an unsigned image submitted through
  a campaign is rejected, and the rejection is visible in `status.json` and
  in Kyverno's policy report.
- Policy audit mode documented for the first days on a new cluster
  (`Audit` before `Enforce`), so a misconfigured identity stalls nothing.

## Done when

- [ ] On the dev cluster, a pipeline pinning an unsigned or foreign image
      is refused by the reconciler; a hand-applied Pod with such an image
      is refused by Kyverno; both rejections are reported.
- [ ] All chart images and the GPU wrapper pass the policy in `Enforce`
      mode; a full campaign runs with it on.
- [ ] The policy is part of the chart (no hand-applied objects) and its
      identity values are in the dev-cluster values file.
- [ ] The Security page documents the two layers and how to rotate the
      identity when the workflow moves.

- [ ] The Security → Trust boundary table gains a row for the Kyverno policy (what it refuses, where) and the "user action" warning about the allow-list is replaced by a link to the configured value.

## Delivered so far

**B63 Task 22 delivers the policy half** (chart 0.6.0, 2026-09-04). The
image allow-list and the model-revision rule stopped being converter code
and became `ClusterPolicy` objects the chart ships under
`templates/policies/`, behind `security.policies.enabled`, alongside a
digest-pin policy and the existing `verifyImages` one:
`htrflow-batch-images-pinned-<ns>`, `htrflow-batch-images-allowed-<ns>`,
`htrflow-batch-model-revision-<ns>`, all `Enforce`. `make install-kyverno`
installs Kyverno (chart 3.9.0 / app v1.19.0), and a campaigns repo's CI
runs the same policies over its rendered manifests with the Kyverno CLI.
Proven on the PoC — a foreign registry, a hand-edited tag and an unpinned
model are each refused at admission with a message naming the offending
value; run log in `docs/development/e2e-indexed-jobs.md`, "Task 22".

What this story still wants beyond that: **cosign signatures**
(`security.verifyImages` with the real CI identity, which needs B09), the
signed arm64 wrapper, and the audit-mode-first rollout note.
