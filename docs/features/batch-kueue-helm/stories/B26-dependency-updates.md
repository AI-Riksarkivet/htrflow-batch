---
type: Product Backlog Item
id: 2868
parent: 2800
title: Automatic dependency updates with Dependabot
---

# B26 · Automatic dependency updates with Dependabot

**Story.** As the team owning this for years, we want base images, GitHub
actions and both lockfiles (Python and frontend) kept current by automatic
pull requests from **Dependabot**, each one run through the full test
suite, so that security fixes arrive as a review instead of a scramble —
and so that the vulnerability handling NIS2 asks for (B09) has a routine
behind it.

## Why it matters

Everything here is pinned — by digest, by SHA, by lockfile — which is what
makes builds reproducible and signable. Pins nobody updates turn into a
museum of known vulnerabilities. Dependabot is GitHub's built-in updater:
no extra service to run, security advisories drive priority, and its
pull requests carry the advisory they fix. The repo currently has a
`renovate.json` from the audit remediation; this story replaces it with
Dependabot so we run one updater, the one native to the host.

## What this delivers

- A `.github/dependabot.yml` covering:
    - **GitHub Actions** (SHA-pinned; Dependabot updates the pin and the
      version comment);
    - **Python** via the `uv.lock` (the `uv` ecosystem);
    - **frontend** via `bun.lock` (the `bun` ecosystem);
    - **Docker** base images in `.docker/*.dockerfile` (digest bumps).
- **Grouping and cadence**: weekly, minor/patch grouped per ecosystem so a
  week is one PR each, security updates ungrouped and immediate.
- **Dependabot security alerts and security updates enabled** on the
  repository, so a published advisory opens a PR without waiting for the
  weekly run.
- Each PR runs the CI checks (B06, B21–B25, B27); a human merges; merged
  updates flow to DEV through the normal release and promotion path
  (B09, B34).
- `renovate.json` removed once Dependabot has produced its first PRs.

## Known limit

Dependabot has no ecosystem for the image digests pinned in the **Helm
chart values** (`devStack.*`, control-plane images). Those stay a
deliberate manual bump at release time, listed in the chart README
upgrade notes — recorded here so nobody assumes they are covered.

## Done when

- [ ] A new action release, a Python or frontend lockfile update and a
      base-image digest each produce a Dependabot PR with passing checks
      without anyone asking for it.
- [ ] A security advisory for a pinned dependency opens a PR within a day.
- [ ] `renovate.json` is gone; the CI page documents what Dependabot covers
      and what it does not.

- [ ] The CI page's dependency-pins section names Dependabot and what it does not cover.
