# Release on Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merging a version-bump PR to `main` publishes signed multi-arch images, pins them through a self-merging PR, tags the pin commit and writes the GitHub release, with no human step after the merge.

**Architecture:** One script, `scripts/release.py`, owns the release's file edits (`bump`, `pin`) and decides what `main` needs next (`state`: `released` / `publish` / `tag`). `publish.yml` becomes push-triggered: it asks `state`, builds and gates every image once, runs the test suite once beside the builds, joins the manifest lists, runs `pin` and opens the pin PR, starting that PR's required checks by `workflow_dispatch`. A new `tag-release.yml` fires when `published.yml` passes on `main`, tags the pin commit and dispatches `release.yml` on the tag. Only `GITHUB_TOKEN` is used.

**Tech Stack:** GitHub Actions YAML, Python 3 (stdlib + PyYAML, already a test dependency), dagger Go module (`.dagger/`), pytest.

**Spec:** `docs/superpowers/specs/2026-10-16-release-on-merge-design.md`

## Global Constraints

- Tags are immutable: never overwrite `v<version>` or `v<version>-<arch>` on Docker Hub; a re-run reuses a per-arch tag only if its signature verifies.
- Every image is built natively per architecture (`ubuntu-24.04` amd64, `ubuntu-24.04-arm` arm64). No qemu.
- Every pushed digest goes through `.github/actions/sign-attest` exactly as today.
- Gates that stay in the publish jobs: the driver test on the wrapper, Trivy CRITICAL (`--ignore-unfixed`) on every image. The workspace test suite runs once per release, and the manifest lists wait for it.
- Chart packages stay signed as `release.yml@refs/tags/<tag>`; no `workflow_call` of `release.yml`.
- No credential beyond `GITHUB_TOKEN` and the existing `release` environment secrets. Docker Hub secrets appear only in `publish.yml` jobs that run in the `release` environment.
- No `${{ inputs.* }}` or `${{ steps.* }}` inside `run:` scripts (the existing tests enforce this); pass values through `env:`.
- Every `uses:` pinned by commit SHA with a version comment.
- Only one release at a time: `concurrency: { group: release, cancel-in-progress: false }`.
- Docs pages: no versions, dates, hardware, hosts or people's names (`scripts/docs_lint.py`).
- Before every push run: `make ci`, `bash scripts/loc-budget.sh`, `uv run --no-sync ty check packages/wrapper/src packages/converter/src packages/web/src`, `uv run --no-sync ruff format --check .`, `uv run --no-sync ruff check .`.
- One logical step per commit; push the branch after each reviewed task.

## Review Focus

1. **The pin commit's own push to `main`** must not start a second publish: `state` returns `tag` there, and `publish.yml` stops after `plan`. Test in Task 2.
2. **A file that pins a release digest but is not in `release.py`'s list** would ship stale: a tree-wide test finds every `riksarkivet/htrflow-(batch|web|campaigns)@sha256:` and `CONVERTER_REF:` occurrence and requires it be in `PINNED` or `GENERATED`. Test in Task 3.
3. **A re-run after a partial failure** (per-arch tag pushed, job failed after) must finish without a new version; an existing per-arch tag whose signature does not verify must stop the run. Tested by workflow-shape test in Task 5 and live in Task 9.
4. **`release.yml` dispatched on a tag** must produce the release with provenance, while a dispatch from `main` still only backfills. Test in Task 7.
5. **A weekly `published.yml` run, or a manual one between bump and pin,** must not tag anything: only a `push`/`workflow_dispatch` run whose `head_sha` has `state == tag` tags. Test in Task 6.

---

### Task 1: Prove dispatched checks satisfy required checks

A spike, nothing merged. The whole no-token design rests on it.

**Files:** none committed.

- [ ] **Step 1:** Ask the product owner before touching GitHub (this opens a throwaway PR on the real repository). On approval, from a worktree: create branch `spike/dispatch-checks` off `origin/main`, change one line in a scratch file under `docs/superpowers/` and commit with message `spike: dispatched checks [skip ci]` (`[skip ci]` suppresses the push and `pull_request` runs, which is what a `GITHUB_TOKEN`-made PR also gets).
- [ ] **Step 2:** `git push -u origin spike/dispatch-checks && gh pr create --draft --title "spike: dispatched checks" --body "throwaway"`. Expected: the PR shows the required checks as "Expected — Waiting for status to be reported".
- [ ] **Step 3:** `for w in ci.yml codeql.yml trufflehog.yml; do gh workflow run $w --ref spike/dispatch-checks; done`, wait for them.
- [ ] **Step 4:** `gh pr view --json mergeStateStatus,statusCheckRollup`. Expected: every required check name present on the head SHA and `mergeStateStatus` is `CLEAN` (or `BLOCKED` only by draft). Record which required names did *not* appear (the scan jobs run on dispatch today; note their names and durations).
- [ ] **Step 5:** Close the PR, delete the branch. Report the result. If dispatched check runs do **not** satisfy the required checks, stop and bring it back to the product owner: the design falls back to a GitHub App token.

### Task 2: `release.py state` and `bump`

**Files:**
- Create: `scripts/release.py`
- Test: `packages/wrapper/tests/test_release_script.py` (import the script the way `packages/converter/tests/test_chart_agreement.py` imports `scripts/config_reference.py`)
- Modify: `Makefile` (target `release-bump`)

**Interfaces:**
- Produces:
  - `version(root: Path) -> str` — `project.version` of `packages/wrapper/pyproject.toml`.
  - `state(root: Path, tags: set[str]) -> Literal["released", "publish", "tag"]` — `released` if `"v"+version` in `tags`; `tag` if `HOOK` pins the converter by digest with the comment `# v<version>` on all three images lines; else `publish`.
  - `bump(root: Path, new: str, chart_version: str) -> list[Path]` — sets `project.version` in the three `pyproject.toml` (wrapper, web, converter), `appVersion: "<new>"` and `version: <chart_version>` in `charts/htrflow-batch/Chart.yaml`, rewrites the three hook image lines in `HOOK` and `examples/campaigns/argocd/apply.yaml` to `docker.io/riksarkivet/htrflow-campaigns:v<new>` (no comment), runs `uv lock`, returns changed paths. Refuses (`SystemExit`) a `new` that is not greater than the current version.
  - CLI: `python scripts/release.py state` prints the state (reads tags from `git tag --list 'v*'`); `python scripts/release.py bump <version> --chart <chart-version>`.
  - Constants: `HOOK = "packages/converter/src/htrflow_converter/template/argocd/apply.yaml"`.

- [ ] **Step 1: Write the failing tests** (each on a `tmp_path` copy of the files it touches, copied from the repo):

```python
def test_state_is_released_when_the_version_has_a_tag(repo_copy):
    assert release.state(repo_copy, {"v" + release.version(repo_copy)}) == "released"

def test_state_is_publish_right_after_a_bump(repo_copy):
    release.bump(repo_copy, "99.0.0", "99.0.0")          # hook now names :v99.0.0
    assert release.state(repo_copy, set()) == "publish"

def test_state_is_tag_once_the_hook_pins_this_versions_digest(repo_copy):
    release.bump(repo_copy, "99.0.0", "99.0.0")
    release.pin(repo_copy, "99.0.0", DIGESTS, "a" * 40)  # from Task 3
    assert release.state(repo_copy, set()) == "tag"

def test_state_is_publish_when_the_hook_pins_an_older_release(repo_copy):
    # the bump PR merged but someone hand-edited one hook line back
    ...  # one of three lines left at the old digest -> "publish"

def test_bump_sets_every_version_and_the_hook_tag(repo_copy):
    changed = release.bump(repo_copy, "99.0.0", "99.1.0")
    for p in WRAPPER, WEB, CONVERTER: assert 'version = "99.0.0"' in (repo_copy / p).read_text()
    chart = (repo_copy / "charts/htrflow-batch/Chart.yaml").read_text()
    assert 'appVersion: "99.0.0"' in chart and "\nversion: 99.1.0\n" in chart
    assert (repo_copy / release.HOOK).read_text().count("htrflow-campaigns:v99.0.0") == 3

def test_bump_refuses_a_version_that_is_not_newer(repo_copy):
    with pytest.raises(SystemExit): release.bump(repo_copy, "0.0.1", "0.0.1")
```

Task 3 adds `pin`; write `test_state_is_tag…` now and mark it `xfail(strict=True, reason="pin: Task 3")`, removing the marker in Task 3. Stub `uv lock` in tests with a `run` parameter (`bump(..., run=subprocess.run)`).

- [ ] **Step 2:** `uv run --no-sync pytest packages/wrapper/tests/test_release_script.py -q` → FAIL (module missing).
- [ ] **Step 3:** Implement `version`, `state`, `bump` and the CLI in `scripts/release.py`. Edit files with anchored regexes line by line (keep comments and formatting); do not round-trip YAML/TOML through a parser.
- [ ] **Step 4:** Re-run → PASS (one xfail).
- [ ] **Step 5:** Add `release-bump:` to `Makefile`: `uv run --no-sync python scripts/release.py bump $(VERSION) --chart $(CHART)`; list it under Makefile targets in `docs/development/ci.md`.
- [ ] **Step 6:** Commit: `feat(release): release.py state and bump`.

### Task 3: `release.py pin`

**Files:**
- Modify: `scripts/release.py`
- Test: `packages/wrapper/tests/test_release_script.py`

**Interfaces:**
- Consumes: `version`, `HOOK` (Task 2).
- Produces:
  - `PINNED: dict[str, tuple[str, ...]]` — per image the hand-pinned files: `web`: `charts/htrflow-batch/values.yaml`, `.docker/docker-compose.yml`; `wrapper`: `packages/converter/src/htrflow_converter/template/pipelines/demo-v1.yaml`, `.docker/docker-compose.yml`; `campaigns`: `HOOK`; plus `CONVERTER_REF_FILES = ("packages/converter/src/htrflow_converter/ci/github/.github/workflows/render.yml", "packages/converter/src/htrflow_converter/ci/azure/azure-pipelines.yml")`.
  - `GENERATED: tuple[str, ...] = ("examples/campaigns/", "docs/reference/configuration.md")` — regenerated, never edited.
  - `pin(root: Path, version: str, digests: dict[str, str], converter_ref: str, run=subprocess.run) -> list[Path]` — `digests` keys `wrapper|web|campaigns`, values `sha256:<64 hex>`; `converter_ref` 40-hex SHA. Writes `<repo>@<digest>` (keeping each file's existing registry prefix: `docker.io/` in chart/templates, none in compose) and the `# v<version>` comment where the file already carries one (compose, hook; the values.yaml prose comment "published v<x> web image" too); sets `CONVERTER_REF: <sha>   # v<version>`; then runs `uv run --no-sync htrflow-campaigns init --force examples/campaigns` and `uv run --no-sync python scripts/config_reference.py`. Refuses malformed digests or SHA (`SystemExit`).
  - CLI: `python scripts/release.py pin <version> --wrapper sha256:… --web sha256:… --campaigns sha256:… --converter-ref <sha>`.
- Note: `.dagger/published.go`'s `publishedPins` stays the checks' source; a test here asserts every path it names is in `PINNED` (read the Go file with a regex for the three quoted paths).

- [ ] **Step 1: Write the failing tests:**

```python
def test_pin_writes_every_pin_the_last_release_commit_wrote(repo_copy):
    release.bump(repo_copy, "99.0.0", "99.0.0")
    release.pin(repo_copy, "99.0.0", DIGESTS, SHA, run=fake_run)
    compose = (repo_copy / ".docker/docker-compose.yml").read_text()
    assert compose.count(f"riksarkivet/htrflow-web@{DIGESTS['web']}}}  # v99.0.0") == 2
    assert f"riksarkivet/htrflow-batch@{DIGESTS['wrapper']}}}  # v99.0.0" in compose
    assert f"image: docker.io/riksarkivet/htrflow-web@{DIGESTS['web']}" in (repo_copy / "charts/htrflow-batch/values.yaml").read_text()
    assert (repo_copy / release.HOOK).read_text().count(f"htrflow-campaigns@{DIGESTS['campaigns']}  # v99.0.0") == 3
    for f in release.CONVERTER_REF_FILES:
        assert f"CONVERTER_REF: {SHA}   # v99.0.0" in (repo_copy / f).read_text()
    assert fake_run.calls == [INIT_CMD, CONFIG_REF_CMD]

def test_pin_is_idempotent(repo_copy): ...      # second pin with same inputs changes nothing

def test_pin_refuses_a_malformed_digest(repo_copy):
    with pytest.raises(SystemExit): release.pin(repo_copy, "99.0.0", {**DIGESTS, "web": "sha256:abc"}, SHA, run=fake_run)

def test_every_release_pin_in_the_tree_is_owned_by_release_py():
    """Review Focus 2: no file pins a release image or CONVERTER_REF unless pin() writes or regenerates it."""
    pattern = re.compile(r"riksarkivet/htrflow-(batch|web|campaigns)@sha256:|CONVERTER_REF:")
    owned = {p for ps in release.PINNED.values() for p in ps} | set(release.CONVERTER_REF_FILES)
    for path in git_ls_files():           # tracked files only, skip docs/superpowers/ and tests/
        if pattern.search(path.read_text(errors="ignore")):
            assert str(path) in owned or str(path).startswith(release.GENERATED), path

def test_published_go_pins_are_a_subset_of_pinned(): ...
```

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement `pin`, `PINNED`, `CONVERTER_REF_FILES`, `GENERATED`, CLI. Remove Task 2's `xfail`.
- [ ] **Step 4:** Run the file → PASS. If the tree-wide test names a file the last release commit did not touch, add it to `PINNED` or `GENERATED` (whichever is true) rather than to the test's skip list.
- [ ] **Step 5:** Local proof against the real tree: in the worktree, `uv run --no-sync python scripts/release.py pin 0.12.0 --wrapper sha256:17804e4e274f6188d8d877af1a875fb6a641e27d73fe04caa38f35ecf12de5a8 --web sha256:b7f204e22d300e920ca7b7b01b0d196a34c7b9acd21886b776a2588ee3df7657 --campaigns sha256:efea124aaa6eb3f3a7b05a11fac4727972a7d50147b849db24fca6e7ffdb980d --converter-ref 492794f4e7b59f9360129be254e22e2c3134b59a` → `git status` shows no changes (main already carries exactly these pins).
- [ ] **Step 6:** Commit: `feat(release): release.py pin writes every release pin`.

### Task 4: `ci.yml` dispatch that behaves like a pull request

**Files:**
- Modify: `.github/workflows/ci.yml` (`on.workflow_dispatch.inputs`, the `if:` at lines 122, 147, 184, 249, and the `changes` job)
- Test: `packages/wrapper/tests/test_release_on_merge.py` (new; collects the workflow-shape tests of Tasks 4–7)

**Interfaces:**
- Produces: `ci.yml` input `pull_request_checks` (boolean, default `false`). When `true`, every job and step that today checks `github.event_name != 'pull_request'` behaves as on a pull request, and the `changes` job reports no image inputs changed. Required check names are unchanged.

- [ ] **Step 1: Write the failing test** `test_a_pull_request_checks_dispatch_skips_what_a_pull_request_skips`: load `ci.yml`, assert the input exists with `type: boolean` and `default: false`, and that every `if:` mentioning `pull_request` also references `inputs.pull_request_checks`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement. Pass the input to `run:` scripts only through `env:`.
- [ ] **Step 4:** Run → PASS; `make ci`.
- [ ] **Step 5:** Commit: `ci: a dispatch can run the pull-request checks only`.

### Task 5: `publish.yml` publishes on a version bump

**Files:**
- Modify: `.github/workflows/publish.yml`
- Modify: `.dagger/publish.go` (`PublishDocker`: new `// +optional skipTests bool`; when true, skip `m.Test`)
- Test: `packages/wrapper/tests/test_publish_workflow.py` (update existing), `packages/wrapper/tests/test_release_on_merge.py`

**Interfaces:**
- Consumes: `release.py state`, `release.py pin` CLI (Tasks 2–3); `ci.yml` input `pull_request_checks` (Task 4).
- Produces: jobs `plan` (outputs `state`, `version`), `tests`, `publish` (matrix, unchanged entries), `manifest` (outputs per-component digest via artifact `manifest-<component>`), `pin`. Pin branch name `release/v<version>-pin`; pin PR title `release: pin the v<version> images and converter`.

Shape:
- `on: push: branches: [main]` + `workflow_dispatch` with no inputs (recovery re-runs the release for the version on `main`). Keep `permissions: contents: read` at top.
- `concurrency: { group: release, cancel-in-progress: false }`.
- `plan` (no environment, no secrets): checkout with tags (`fetch-depth: 0`), run `python3 scripts/release.py state`, output it and the version. Every later job has `if: needs.plan.outputs.state == 'publish'`.
- `tests` (no environment, no secrets): `dagger call checks` and `dagger call test` once.
- `publish`: as today, plus a step before dagger: if `${IMAGE}:${TAG}${TAG_SUFFIX}` exists, `cosign verify` it against `^https://github\.com/AI-Riksarkivet/htrflow-batch/\.github/workflows/publish\.yml@refs/heads/main$` and Sigstore's issuer; verified → reuse its digest, skip dagger and sign-attest; not verified → fail. The bare `${IMAGE}:${TAG}` existing still fails. Dagger call gets `--skip-tests`. `TAG` comes from `needs.plan.outputs.version` via `env:`.
- `manifest`: `needs: [plan, tests, publish]`, otherwise as today; uploads its digest as an artifact.
- `pin`: `needs: [plan, manifest]`, `permissions: contents: write, pull-requests: write, actions: write`, no environment, no Docker Hub secrets. Checks out `main` at `github.sha`, runs `release.py pin` with the three digests and `--converter-ref "$GITHUB_SHA"`, commits as `github-actions[bot]` on `release/v<version>-pin`, pushes, `gh pr create`, then `gh workflow run ci.yml --ref <branch> -f pull_request_checks=true`, `gh workflow run codeql.yml --ref <branch>`, `gh workflow run trufflehog.yml --ref <branch>`, then `gh pr merge --auto --merge`. **A merge made under `GITHUB_TOKEN` starts no workflow on `main` either**, so the job then waits for the PR to reach `MERGED` (poll `gh pr view --json state,mergeCommit` every 30 s, fail after 30 min with the PR link in the error) and dispatches `published.yml` and `ci.yml` on `main` itself. Those two dispatches are what start the tag stage (Task 6).

- [ ] **Step 1: Write failing tests** in `test_release_on_merge.py`:
  - `test_publish_runs_on_main_and_by_hand_only` (`on` keys are exactly `push` with `branches: [main]` and `workflow_dispatch`).
  - `test_one_release_at_a_time` (the concurrency block above).
  - `test_every_release_job_waits_for_the_plan_to_say_publish`.
  - `test_the_suite_runs_once_and_the_manifest_lists_wait_for_it` (`tests` job runs `dagger call checks` and `dagger call test`; `manifest.needs` contains `tests`; the publish step passes `--skip-tests`).
  - `test_a_reused_per_arch_tag_must_verify` (the publish job has a `cosign verify` step with the identity regexp above before the dagger call).
  - `test_the_pin_job_holds_no_registry_credential_and_starts_the_required_checks` (no `DOCKERHUB_` in `pin`; its script dispatches `ci.yml` with `pull_request_checks=true`, `codeql.yml`, `trufflehog.yml`, and enables auto-merge).
  - `test_the_pin_job_starts_main_checks_after_the_merge` (after `gh pr merge --auto`, the script waits for `MERGED` and then runs `gh workflow run published.yml --ref main` and `gh workflow run ci.yml --ref main`).
  - Update `test_publish_workflow.py` where it reads `inputs.tag` (the tag now comes from `needs.plan.outputs.version`); keep every other assertion, notably `test_every_job_with_the_registry_credential_runs_in_the_release_env`.
- [ ] **Step 2:** Run both files → FAIL.
- [ ] **Step 3:** Add `skipTests` to `PublishDocker`; `go vet ./... && go test ./...` in `.dagger/` pass.
- [ ] **Step 4:** Rewrite `publish.yml` to the shape above. Update its header comment (what triggers it, why no approvals, the re-run rule).
- [ ] **Step 5:** Run both test files → PASS; `make ci`; loc budget.
- [ ] **Step 6:** Commit in two: `feat(dagger): publish-docker can skip the suite the release ran once`, then `ci(publish): a merged version bump publishes, pins and opens the pin PR`.

### Task 6: `tag-release.yml` tags the pin commit when its checks pass

**Files:**
- Create: `.github/workflows/tag-release.yml`
- Test: `packages/wrapper/tests/test_release_on_merge.py`

**Interfaces:**
- Consumes: `release.py state` (Task 2); workflow name `Published images` (`published.yml`).
- Produces: tag `v<version>` on the pin commit; a `release.yml` dispatch with `--ref v<version>`.

Shape: `on: workflow_run: workflows: ["Published images"], types: [completed], branches: [main]`. One job, `if: github.event.workflow_run.conclusion == 'success' && contains(fromJSON('["push","workflow_dispatch"]'), github.event.workflow_run.event)` (the pin merge is a `GITHUB_TOKEN` push, so the run that matters is the pin job's dispatch; the weekly `schedule` run never tags), `permissions: contents: write, actions: write`. Checks out `github.event.workflow_run.head_sha` (via `env:`), fetches tags, runs `release.py state`; only on `tag`: confirms `head_sha` is still on `main` (`git merge-base --is-ancestor`), creates an annotated tag `v<version>` on it, pushes it, then `gh workflow run release.yml --ref v<version>`.

- [ ] **Step 1: Failing tests:** `test_tagging_follows_a_passing_published_run_never_a_scheduled_one` (trigger and `if` as above); `test_tagging_happens_only_when_state_says_tag` (script runs `release.py state` and exits 0 without tagging otherwise); `test_the_tag_goes_on_the_checked_commit_not_main_head` (no `git tag` without the `head_sha`).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit: `ci(release): tag the pin commit once its published-image checks pass`.

### Task 7: `release.yml` runs when dispatched on a tag

**Files:**
- Modify: `.github/workflows/release.yml` (`charts.if`, the two provenance steps' `if`, `release.if`, `backfill.if`)
- Test: `packages/wrapper/tests/test_release_workflow.py`

**Interfaces:**
- Produces: a dispatch with `github.ref` = `refs/tags/v*` behaves exactly like a tag push (release with provenance); a dispatch on `refs/heads/main` still only backfills. Define the condition once per job as `github.event_name == 'push' || startsWith(github.ref, 'refs/tags/v')`.

- [ ] **Step 1: Failing tests:** `test_a_dispatch_on_a_tag_releases_like_a_tag_push` (`release` and provenance steps accept it); `test_a_dispatch_from_main_only_backfills` (`backfill.if` requires `refs/heads/main`). Keep `test_signing_runs_only_as_an_identity_the_recipe_accepts` passing; extend it if it reads the conditions.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement; `TAG` already reads `github.ref_name`, which is the tag in both cases. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit: `ci(release): a dispatch on a tag writes the release`.

### Task 8: Docs

**Files:**
- Modify: `docs/development/releasing.md` ("The publish workflow", "The GitHub release" order of steps, the `release` environment section: required reviewers removed, branch rule and environment secrets kept; recovery: re-run failed jobs or dispatch `publish.yml`), `docs/development/ci.md` (Workflows list: `publish.yml` trigger, new `tag-release.yml`, `ci.yml`'s `pull_request_checks` input; Makefile `release-bump`).

- [ ] **Step 1:** Rewrite those sections to describe the flow in the spec: bump PR (`make release-bump VERSION=… CHART=…` plus a changelog entry), merge, then everything automatic. Remove every instruction that tells a person to dispatch `publish.yml`, copy a digest, or push a tag.
- [ ] **Step 2:** `uv run --no-sync python scripts/docs_lint.py` and `scripts/docs-site.sh build --clean --strict` → both pass.
- [ ] **Step 3:** Commit: `docs: releases start from a merged version bump`.

### Task 9: Settings, first release, measurement

Outward-facing: each step needs the product owner's go-ahead.

- [ ] **Step 1:** After the branch is merged, the product owner (or, with their explicit go-ahead, `gh api -X PUT repos/AI-Riksarkivet/htrflow-batch/environments/release` keeping the `main` branch policy) removes the required-reviewers rule from the `release` environment. Verify with `gh api repos/AI-Riksarkivet/htrflow-batch/environments/release --jq '.protection_rules[].type'` → only `branch_policy`.
- [ ] **Step 2:** Cut the next real release with the product owner's go-ahead: `make release-bump VERSION=<next> CHART=<next chart>`, changelog entry, PR, merge.
- [ ] **Step 3:** Watch it through: `publish.yml` → pin PR auto-merges → `published.yml` → `tag-release.yml` → `release.yml`. Record merge time, manifest-lists-done time and release time from `gh run list`.
- [ ] **Step 4:** Targets: images under 20 min after merge, release about 30 min. If the image target is missed, find the long pole from job timings and add caching for that step only (wrapper base layers, web viewer build, or Trivy DB, never skipping Trivy), as a follow-up PR with the before/after timings in its description.
- [ ] **Step 5:** Report the timings to the product owner.
