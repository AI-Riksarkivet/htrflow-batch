# Release on merge: one PR to signed images

## Why

Getting a new set of images out takes too long and too many steps. The last
release, measured from its workflow runs, took about 3 h 50 min from the
version-bump PR to the deployed chart being correct, of which the machines
worked about 45 minutes. Today a release is:

| # | Step | Kind | Time it took |
|---|---|---|---|
| 1 | version-bump PR, CI, merge | PR | 4 min |
| 2 | start `publish.yml` by hand with the tag | manual dispatch | ~10 min lost (one cancelled start) |
| 3 | approve the `release` environment for the six publish jobs | human click | 2 min |
| 4 | six publish jobs: test suite, build, driver test, Trivy, push, sign | machine | 15 min (wrapper is the long pole) |
| 5 | approve the `release` environment again for the three manifest-list jobs | human click | **1 h 36 min of waiting** |
| 6 | release-commit PR: digests copied by hand into ~8 places, `CONVERTER_REF`, examples regenerated, chart version and changelog | PR, hand edits | 12 min |
| 7 | push the tag; `release.yml` writes the GitHub release | manual | 1 min |

Inside step 4 the wrapper job spends ~3.5 min on the workspace test suite
(which the bump PR and main's CI have already run on the same commit, and
which every one of the six jobs runs again), ~2.5 min building the image and
running the driver test, ~3 min in Trivy (its vulnerability database is
downloaded on every run), then pushes and signs. Nothing is cached between
runs.

The product owner wants a release to need no click from them, and the time
from "release decided" to "images on Docker Hub" to be short.

## Goals

- **One trigger, no clicks.** Merging a PR that bumps the version to `main`
  is the whole human (or agent) part. Everything after it runs unattended:
  build, gate, push, sign, multi-arch manifest lists, digest pins, tag,
  GitHub release.
- **Fast.** Under **20 minutes** from the bump PR's merge to all three
  signed manifest lists on Docker Hub; about **30 minutes** to the GitHub
  release with its chart assets.
- **No hand-copied digests.** Every place a release digest is pinned is
  written by a script from what the publish run returned.
- **Each image built once per architecture per release**, and the workspace
  test suite run zero extra times in the release run.

## Non-goals

- Publishing images on every merge to `main`. Images are only built for
  Docker Hub when a release is deliberately cut by bumping the version.
- Updating the deployment repository (ai-dev) automatically. Out of scope for
  now; a later piece of work.
- Making ordinary pull-request CI faster (4–6 min today). Only what the
  release path needs.
- Changing what an image contains, or which architectures ship.

## Anti-goals (a failure even if it works)

- A tag or a manifest list that is ever overwritten. Tags stay immutable.
- A published image that skipped the gates that are specific to it: the
  driver test on the wrapper, Trivy's CRITICAL gate on every image, the
  `entrypoints-published` / `verify-published` / `scan-published` checks on
  the pins.
- Images that are not signed, attested and SBOM'd exactly as today
  (`.github/actions/sign-attest`).
- Cross-building under qemu. Every architecture stays built natively on a
  runner of its own architecture (see the header of `publish.yml`).
- A release run that leaves a half-finished state nobody can recover from
  without bumping the version again.

## Decided

- **Trigger:** a push to `main` whose commit carries a version (in
  `packages/wrapper/pyproject.toml`) for which no `v<version>` tag exists yet
  starts the release. Nothing else starts it.
- **No approval step.** The `release` environment's required-reviewers rule
  goes. The Docker Hub credential and the signing identity stay in the
  `release` environment, restricted to `main`; "only `main` can publish",
  plus branch protection on `main`, is now what guards them. This is a
  deliberate step down from finding 3068's setup, accepted by the product
  owner.
- **The workspace tests run once per release, beside the builds.** `main`
  does not require a branch to be up to date before merging, so the bump's
  merge commit is not itself tested by the PR's CI. The release run therefore
  runs `checks` and `test` once, in parallel with the six image builds, and
  no image is pushed under the final tag until they pass. The six publish
  jobs no longer run the suite each; they keep the gates that need the built
  image: the driver test on the wrapper and Trivy CRITICAL on every image.
  *(Builder's call how the gate is wired: pushing per-architecture tags early
  is fine, the manifest lists wait for the tests.)*
- **Pins arrive through an automatic pull request** (branch protection on
  `main` requires checks and forbids a workflow pushing to it directly).
  It merges itself once its required checks pass; no human reviews it.
  *(Product owner may later choose to merge it by hand instead; that is a
  matter of not enabling auto-merge, nothing else changes.)*
- **The tag goes on the pin commit**, as today, so `release.yml`'s notes and
  chart packages describe a commit whose pins are the release's images.
- **The tag waits for `published.yml` to pass on the pin commit.** A release
  whose pins fail the scan, signature or entrypoint checks does not ship
  (`releasing.md`). If it fails, the pins are on `main` but no tag exists;
  the fix is a new version, as with any failed published-image check today.
  This costs a few minutes against the 30-minute target and is worth it.
- **The chart packages' signing identity does not change.** They stay signed
  as `release.yml@refs/tags/<tag>`, the identity the verify recipe in
  `releasing.md` anchors to. `release.yml` is started by name
  (`workflow_dispatch`) with the tag as its ref, so `github.ref` is the tag
  exactly as on a tag push; its conditions that today accept only a tag push
  or `main` (the job's `if`, the provenance step) are widened to accept a
  dispatch on a tag ref, and nothing else. Calling it with `workflow_call`
  from `main` is ruled out, since that changes the identity.
- **No extra credential.** Everything runs on the workflow's own
  `GITHUB_TOKEN`; no GitHub App and no personal token.
- **Merges to `main` during a release are allowed** (as they were during the
  last release, when an unrelated PR landed between the bump and the pins).
  The images and `CONVERTER_REF` come from the bump's merge commit; the tag
  goes on the pin commit, whatever else is on `main` by then. Only one
  release runs at a time (a concurrency group); a second version bump waits.

## How it fits together (what is fixed, what is left to the builder)

The flow, end to end:

1. A PR bumps the version (the three `pyproject.toml` files and the chart's
   `appVersion`) and, as part of the same PR, the chart's `version` and its
   changelog entry, so the follow-up pin PR is purely mechanical. *Builder
   may move the chart-version bump into the pin PR instead if a test or the
   chart rules require it; the pin PR must stay generated, not hand-written.*
2. Its merge to `main` starts the release workflow. It checks that the
   version's tags are free on Docker Hub, then builds, gates, pushes and
   signs each image natively per architecture, and joins each pair into the
   manifest list, as `publish.yml` does today but without the test suite and
   without approvals.
3. A script writes the three manifest-list digests into every file listed in
   `.dagger/published.go`'s `publishedPins` and the table in
   `docs/development/releasing.md` (chart values, demo pipeline and its copy
   in `examples/campaigns/`, compose stack, the Argo CD hook with its tag
   comment), sets `CONVERTER_REF` in both CI templates to the SHA of the
   commit that triggered the run (the bump's merge commit on `main`), and
   regenerates `examples/campaigns/` with `htrflow-campaigns init --force`.
   The script is the single owner of "where digests are pinned": it reads
   the same list the published-image checks read, so the two cannot drift.
   It must be runnable locally as well, for recovery.
4. The workflow opens the pin PR, dispatches its required checks and
   enables auto-merge. A merge made under `GITHUB_TOKEN` starts no workflow
   on `main`, so the workflow waits for the merge and then dispatches
   `published.yml` (scan, signature and entrypoint checks against the
   new pins), as today.
5. When `published.yml` has passed on the pin commit, the tag `v<version>`
   is created on it and `release.yml` is dispatched on the tag, producing
   the GitHub release (unchanged in what it attaches).

Constraints the builder must design around:

- **Branch protection on `main`, as it stands:** the required checks are
  the `ci.yml`, CodeQL and TruffleHog jobs; zero approving reviews are
  required; branches need not be up to date; administrators are not
  enforced; auto-merge is enabled on the repository. The pin PR can
  therefore merge itself once its checks pass, with no approval and no
  rebase.
- **The pin PR's own merge must not start a second release.** After it
  merges, the version still has no tag until step 5 creates it; the
  trigger must tell a pin commit from a bump.
- **Pins outside `publishedPins`.** The compose stack, the copies in
  `examples/`, and the generated `docs/reference/configuration.md` all
  moved in the last release commit. The pin script covers every file that
  release commit touched, not only the three `publishedPins` names; a test
  holds the list to the tree.
- **A reused per-architecture tag must be signed.** On a re-run, an
  existing `<version>-<arch>` tag is reused only if its signature and
  attestations verify; otherwise the run stops.

- **Events from `GITHUB_TOKEN` start no workflows, except a dispatch.** A
  pin PR opened, or a tag pushed, with the workflow's default token gets no
  CI and no `release.yml` run on its own. `workflow_dispatch` is the
  exception, so the release workflow starts the pin PR's required checks
  itself by dispatching each required workflow (`ci.yml`, CodeQL,
  TruffleHog) on the pin branch, whose check runs then report on the PR's
  head commit, and starts `release.yml` on the tag the same way. Workflows
  that lack a `workflow_dispatch` trigger get one. The builder confirms on a
  test PR that dispatched check runs satisfy the required checks before
  relying on it.
- **Recovery without a new version.** If any job fails, re-running the
  failed jobs (or dispatching the workflow by hand for the same version)
  must finish the release as long as the final `v<version>` manifest lists
  do not yet exist. Per-architecture tags already pushed by a successful job
  are reused, not rebuilt. Keep a `workflow_dispatch` entry for this.
- **Caching for speed.** Persist what is safe to persist between runs so the
  20-minute target holds: build layers of the wrapper's htrflow base and the
  web image's viewer build, and Trivy's vulnerability database (the database
  may be at most a day old; it must never be skipped). Which mechanism
  (GitHub Actions cache, a registry cache, dagger cache volumes) is the
  builder's call, measured against the target.
- **Existing tests hold the workflow shape.** `test_publish_workflow.py` and
  its neighbours assert today's `publish.yml` (environment on every job,
  native runners, no qemu, digest handling). Update them to assert the new
  shape, keeping every security property above.
- **Docs.** `docs/development/releasing.md` and `docs/development/ci.md`
  describe the new flow. The general-docs rules apply: no versions, dates,
  hardware, hosts or people's names on the site's pages.

## Left to the builder

- Whether the release lives in `publish.yml` turned into a push-triggered
  workflow, or a new workflow that calls `publish.yml` and `release.yml`.
- How "a version without a tag" is detected (pyproject version vs. `git tag`
  vs. Docker Hub).
- The pin script's language and location (a dagger function, or a script
  under `scripts/` with a test), as long as it reads `publishedPins`.
- The caching mechanism and the order of steps inside each publish job.
- How a failed release is reported (the failed run is enough; anything more
  is optional).

## Success

- The next release after this lands: one merged PR, no clicks, signed images
  on Docker Hub in under 20 minutes of the merge and the GitHub release in
  about 30, every pin correct without a hand edit, and `published.yml` green
  on the pin commit.
