---
type: Product Backlog Item
id:
parent: 2800
title: LOC-budgeten körs i make ci och varje budget får headroom
---

# B82 · LOC-budgeten körs i `make ci` och varje budget får headroom

**Story.** Som utvecklare vill jag att `make ci` kör samma grindar som CI och att
budgetarna har marginal, så att "passed" lokalt betyder grönt i CI och en rättning
inte kräver att jag först tar bort en rad någon annanstans.

## Varför det är viktigt

`Makefile:75-78` kör `typecheck` + `dagger call checks` + `dagger call test`;
`ci.yml:48` lägger till en tredje grind, `scripts/loc-budget.sh`, som inget make-mål
kör. Alla fem budgetar ligger dessutom exakt på taket — 2117/2117, 1283/1283,
667/667, 3100/3100, 738/738 — så en tillagd kommentarsrad fäller bygget, och varje
rättning i den här revisionen kräver en motsvarande borttagning. Skriptet räknar
också tomrader och lämnar `.dagger/`, `scripts/`, devstack-chartet och frontendens
css/html obudgeterade (X15, F-7).

## Vad som levereras
- `scripts/loc-budget.sh` läggs till i `ci:`, och varje budget får en liten
  marginal i samma commit, motiverad där höjningarna redan bor.
- Räkningen hoppar över tomrader; `.dagger/` och devstack-chartet får budgetar.
- Spec, plan och story B63 citerar skriptet i stället för att kopiera siffror (B85).
- Budgeten räknar inte tomrader och omfattar även `.dagger/` och devstack-chartet. (revision 2026-09-07, F-7)

## Klart när

- [ ] `make ci` faller på en budgetöverträdelse, med samma utdata som CI.
- [ ] Ingen budget ligger på exakt 100 % efter ändringen.
