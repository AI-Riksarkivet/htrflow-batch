---
type: Product Backlog Item
id: 2856
parent: 2800
title: The same checks run locally and in CI
---

# B27 · The same checks run locally and in CI

**Story.** As a developer, I want to run exactly what CI will run, on my
own machine, with one command, so that a red pull request is never a
surprise and CI never does something I cannot reproduce.

## What this delivers

- All checks and tests (B06, B21–B25) are defined once as a **Dagger**
  module; `make ci` locally and the GitHub workflow call the same
  functions in the same containers.
- The CI action is pinned to the module's engine version, so a Dagger
  release cannot change behaviour underneath us.
- The RA network's TLS interception is handled once (a CA bundle passed
  into the containers), so the pipeline works on office machines too.

## Done when

- [ ] `make ci` green locally implies the workflow is green, and vice
      versa, for the same commit.
- [ ] The workflow contains no logic that is not also in the Dagger module.
