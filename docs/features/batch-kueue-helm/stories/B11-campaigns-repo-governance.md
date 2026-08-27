---
type: Product Backlog Item
id: 2864
parent: 2800
title: Govern the campaigns repo — protected main, reviewed pull requests
---

# B11 · Govern the campaigns repo — protected `main`, reviewed pull requests

**Story.** As the product owner, I want every change to what the cluster
transcribes — and with which model image — to go through a pull request
that someone else approves, with direct pushes to `main` impossible, so
that no single person or leaked token can start work, change a model, or
run a foreign image on our GPUs.

## Why it matters

B04 built the machinery; it assumes the repo it reads is governed. On the
proof-of-concept node it is not: the campaigns repo is served by an
in-cluster git daemon that anyone with cluster access can push to, and
there are no pull requests at all. The docs carry a red "protect `main`
before the reconciler clones it" warning for exactly this reason. Write
access to the campaigns repo is, in effect, the right to run any container
on the GPU with write access to the results bucket. Governance is the
cheapest control in the whole system and must be in place before the dev
cluster (B12) points at a real repo.

## What this delivers

- **A hosted campaigns repository** on the organisation's git host
  (GitHub or Azure DevOps Repos) with the documented layout, replacing the
  PoC's in-cluster daemon.
- **Branch protection on `main`**: no direct pushes, no force pushes,
  at least one required reviewer who is not the author, and the PR check
  required to pass before merge. Administrators included.
- **The immutability check as a required PR check** (`guard.yml` /
  `check_immutable.sh` from the PoC repo), so a modified pipeline file
  turns the PR red before a human even looks.
- **Ownership rules** (`CODEOWNERS` or the Azure equivalent) so pipeline
  files — the ones that name images and models — require review from the
  people who own the model side, while campaign files need only a
  colleague's approval.
- **A read-only token** for the reconciler, stored as a Kubernetes secret,
  scoped to that one repo, with an expiry and an owner.
- The repo link in the status page header pointing at the hosted repo.

## Done when

- [ ] A direct push to `main` by an administrator is refused by the host.
- [ ] A PR that edits an existing pipeline file cannot be merged.
- [ ] A PR that adds a campaign requires one approval; one that adds a
      pipeline requires approval from the pipeline owners.
- [ ] The reconciler on the dev cluster clones the hosted repo with its
      read-only token; the token's rotation date and owner are documented.
- [ ] The "user action" warning in the docs is replaced by a link to the
      configured protection.
