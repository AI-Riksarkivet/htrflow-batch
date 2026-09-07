---
type: Product Backlog Item
id:
parent: 2800
title: rendered/ är exakt det convertern producerade — och PR-CI bevisar det
---

# B78 · `rendered/` är exakt det convertern producerade — och PR-CI bevisar det

**Story.** Som ansvarig för klustret vill jag kunna lita på att allt under
`rendered/` är precis vad `htrflow-campaigns render` skrev, så att en pull request
i campaigns-repot inte kan smyga in ett godtyckligt manifest som Argo CD applyar.

## Varför det är viktigt

`cli.py:173-174` prunar bara `rendered/pipelines` och `rendered/campaigns`, och
inte rekursivt, medan Argo CD är den dokumenterade applyern (`campaigns.md:112-114`:
"nothing applies to the cluster outside of what CI committed"). Prob: både
`rendered/evil.yaml` och `rendered/extra/evil.yaml` överlevde en ny render.
PR-halvan är blind på samma sätt: `render.yml:96` renderar till `$RUNNER_TEMP`, så
append-only-guarden utlöses först efter merge — röd workflow, ingen commit, och ett
`rendered/` som beskriver ett äldre `campaigns/` som Argo CD fortsätter applya.
Render är inte heller atomisk (X9, X34).

## Vad som levereras

- `render` skriver till en temporär katalog och flyttar in den på plats; en
  misslyckad körning lämnar `rendered/` orört.
- Prune går rekursivt över hela `rendered/` och misslyckas på varje fil den här
  renderingen inte skrev.
- Campaigns-repots PR-jobb renderar mot den incheckade `rendered/` och kör
  `git diff --exit-code`; Kyverno körs även mot det incheckade trädet.

## Klart när

- [ ] En fil som läggs till under `rendered/`, även i en underkatalog, gör nästa
      render röd.
- [ ] En PR som ändrar en kampanjs volymlista blir röd i PR-CI, inte först efter
      merge.
