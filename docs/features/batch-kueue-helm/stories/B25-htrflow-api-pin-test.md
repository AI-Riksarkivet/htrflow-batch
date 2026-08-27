---
type: Product Backlog Item
id: 2855
parent: 2800
title: A canary test for htrflow version bumps
---

# B25 · A canary test for `htrflow` version bumps

**Story.** As the team, we want a single test that runs the real
`htrflow` library, inside the exact container image we ship, on a one-page
fixture, so that when `htrflow` releases a new version we learn in minutes
— not on the GPU node — whether our wrapper still drives it correctly.

## Why it matters

The wrapper uses `htrflow` as a library and depends on a few of its
internals (how a pipeline is built from config, how exports are wired).
Those have already changed once during this project and broke the driver;
the unit tests could not see it because they run against fakes to stay
fast and torch-free.

## What this delivers

- A "level 0" pin test (`test_driver.py`) that imports the real
  `Pipeline.from_config` and runs one page, executed *inside the built
  wrapper image* so the versions are exactly those deployed.
- Opt-in in CI (`dagger call test-driver`) and locally
  (`make test-driver-real`) because it needs the model weights; the
  ordinary suite stays fast.

## Done when

- [ ] The test passes against the pinned `htrflow` version and fails when
      the driver's assumptions about the library are broken.
