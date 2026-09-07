---
type: Product Backlog Item
id:
parent: 2800
title: CI gatar varje push, inte bara efter merge
---

# B81 · CI gatar varje push, inte bara efter merge

**Story.** Som utvecklare vill jag att checkarna körs på det jag pushar innan det
blir main, så att ett ruff-fel inte hittas först när main redan är rött.

## Varför det är viktigt

`ci.yml:2-6` triggar på `push: branches: [main]`, `pull_request` och
`workflow_dispatch`, men `gh pr list --state all` ger en enda PR (en stängd
dependabot-bump): `pull_request`-triggern har aldrig gatat en merge, och arbetet
landar som direkta pushar till main med handstartade branch-körningar. Körning
`34108845568` föll på "Checks" efter 44 s och main var rött till `96ffc89` (X14).

## Vad som levereras

- `push:` på alla brancher — scans och arm64-bygget gatar redan på `event_name` —
  alternativt branch protection så att den befintliga `pull_request`-triggern blir
  grinden.
- `docs/development/index.md`: vilken grind som gäller och vad den kör.

## Klart när

- [ ] En push till en feature-branch startar Checks och test utan
      `workflow_dispatch`.
- [ ] Ett medvetet ruff-fel på en branch blir rött innan det når main.
