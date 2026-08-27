---
type: Product Backlog Item
id:
parent: 2923
title: Accessibility per DOS-lagen
---

# C02 · Accessibility per DOS-lagen

**Story.** As a public agency, we want the status page to meet the accessibility requirements Swedish law places on public-sector websites — WCAG 2.1 level AA per DOS-lagen — with a published accessibility statement, so that every colleague can use it and the agency meets its legal obligation.

## Why it matters

B32 fixed contrast, keyboard navigation and small screens as audit findings. That is not the same as a documented conformance check: DOS-lagen (lagen om tillgänglighet till digital offentlig service) requires WCAG 2.1 AA and a *tillgänglighetsredogörelse* for internal tools too.

## What this delivers

- A WCAG 2.1 AA review of every route (campaign list, run-log viewer, and the pages added by C04–C07) with automated checks (axe) in the component tests and a manual screen-reader pass.
- Findings fixed; the accessibility statement published on the page and in the docs, with the known exceptions and the contact for reporting problems.

## Done when

- [ ] axe reports no violations in CI for every route; the manual pass is recorded; the statement is linked from the page footer.
