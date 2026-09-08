---
type: Product Backlog Item
id:
parent: 2800
title: apply städar bort warm-up-Jobbet och ConfigMappen för en pipeline som inte längre finns
---

# B87 · apply städar bort warm-up-Jobbet och ConfigMappen för en pipeline som inte längre finns

**Story.** Som operatör vill jag att en pipeline som tas bort ur
campaigns-repot också försvinner ur klustret — dess warm-up-Job och
pipeline-ConfigMap — så att `kubectl get jobs` visar det som gäller och
inte varje pipeline som någonsin funnits.

## Varför det är viktigt

`htrflow-campaigns apply --prune` tar bort kampanj-Jobs och deras
ConfigMaps som inte längre renderas (`CAMPAIGN_SELECTOR`), men inte
warm-up-Jobs (`app: htrflow-warmup`) eller pipeline-ConfigMaps. Efter
2026-09-08:s städning av tre pipelines i PoC-repot låg
`htr-warmup-e2e-prov`, `htr-warmup-e2e-t22-v2` och `htr-warmup-e2e-t22-v3`
kvar som Complete i klustret, utan TTL, med sina ConfigMaps. På ett kluster
som byter pipeline var vecka växer listan utan slut, och revisionen (X22,
H) noterade redan att "ingenting någonsin tas bort".

## Vad som levereras

- `apply --prune` tar bort warm-up-Jobs och pipeline-ConfigMaps vars
  pipeline-id inte längre renderas, med samma `propagation_policy` som
  kampanj-Jobs och en `removed:`-rad per objekt.
- Markörfilen på PVC:n (`/data/warmup/<id>.done`) tas inte bort av apply
  (den kräver PVC-åtkomst); dokumentationen säger det och pekar på
  kommandot för att städa cachen.
- Tester: rendera två pipelines, ta bort en, prune tar bort exakt dess två
  objekt.

## Klart när

- [ ] På PoC-klustret finns efter `apply --prune` bara warm-up-Jobs för de
      pipelines som ligger i repot.
- [ ] Ett warm-up-Job som fortfarande används rörs inte, verifierat av ett
      test.
