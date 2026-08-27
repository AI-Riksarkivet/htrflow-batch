---
type: Product Backlog Item
id:
parent: 2923
title: Swedish user interface
---

# C01 · Swedish user interface

**Story.** As an archivist, I want the status page in Swedish — labels, states, banners, dates and numbers in Swedish conventions — so that the people the page is for can read it without translating in their heads.

## Why it matters

The page was built in English by and for the development team. Its audience from the first real campaign on is Swedish-speaking archive staff; state words like "needs attention" carry meaning that must be exact.

## What this delivers

- All UI strings moved to a message catalogue with Swedish as the default and English kept; dates, times and numbers formatted for `sv-SE`.
- State names and banner texts reviewed by an archivist for wording (e.g. what "needs attention" is called and what it asks the reader to do).
- Component tests (B22) run against both languages.

## Done when

- [ ] The page renders in Swedish by default; every visible string is in the catalogue (a test fails on a hard-coded string).
- [ ] An archivist has approved the wording of the states and banners.
