---
type: Product Backlog Item
id: 2854
parent: 2800
title: Lint, formatting and type checks from locked tool versions
---

# B24 · Lint, formatting and type checks from locked tool versions

**Story.** As the team, we want code style, lint rules and static type
checks enforced automatically with the *same tool versions* everywhere,
so that reviews are about design rather than formatting, and "it passed on
my machine" cannot differ from CI.

## What this delivers

- **ruff** for formatting and linting, **ty** for type checking, both
  Python packages.
- Tools run from the locked virtual environment (`uv run --no-sync`), not
  from whatever version a runner happens to download — the version in the
  lockfile is the version that judges the code.
- One root ruff configuration for the workspace.

## Done when

- [ ] `make ci` and the GitHub workflow run identical ruff/ty versions and
      both pass on `main`.
- [ ] A type error in either package fails the pull request.
