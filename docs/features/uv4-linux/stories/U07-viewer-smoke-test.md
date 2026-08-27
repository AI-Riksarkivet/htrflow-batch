---
type: Product Backlog Item
id: 2907
parent: 2801
title: Automatically check that the viewer still works after every change
---

# U07 · Automatically check that the viewer still works after every change

**Story.** As the team, we want CI to open a known volume in the built
viewer and confirm that the page, the text panel and the line outlines all
appear, so that a viewer regression is caught before it reaches anyone.

## Why it matters

CI currently proves the viewer *builds*; whether it *renders* is checked by
a person. U02's fixes are precisely the kind of thing a dependency bump can
silently undo.

## What this delivers

- A browser test (Playwright) run against the docker-compose stack:
  load the fixture volume, assert the image, the ALTO text and at least one
  line outline are present, click a line and assert the highlight.
- Wired into the Dagger checks alongside the frontend tests.

## Done when

- [ ] The test passes in CI on `main` and fails when the U02 patch is
      removed.
