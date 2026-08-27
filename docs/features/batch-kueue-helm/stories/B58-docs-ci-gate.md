---
type: Product Backlog Item
id: 2892
parent: 2800
title: Docs CI gate — broken links, missing nav entries and undocumented chart values fail the build
---

# B58 · Docs CI gate — broken links, missing nav entries and undocumented chart values fail the build

**Story.** As the team, we want the build to fail when the documentation goes stale in a way a machine can detect, so that "update the docs" — part of every story's definition of done — is checked by a machine, not remembered by a person.

## Why it matters

A docs check that runs on every pull request is the only thing that keeps documentation current after the people who wrote it move on.

## What this delivers

- Docs built with the strict flag in the CI checks (B27): unknown pages and bad nav entries fail.
- An internal link and anchor check across `docs/`, the feature and story pages included.
- A chart-values ↔ reference-page check: every value in `values.yaml` / `values.schema.json` appears on the Chart Values page and vice versa.
- The docs site published automatically on merge to `main` (today the publish workflow is manual).

## Done when

- [ ] `make ci` fails on a broken internal link, a missing nav entry and an undocumented chart value — each tested once by breaking it.
- [ ] Docs deploy on merge; the URL is in the README.
