---
type: Product Backlog Item
parent: 2800
title: Chartet beskriver flera GPU-sorter, var och en med egen kvot för GPU, CPU och minne
---

# B104 · Chartet beskriver flera GPU-sorter, var och en med egen kvot för GPU, CPU och minne

*Inget Azure-ärende ännu.*

**Story.** Som operatör av ett kluster med mer än en sorts GPU vill jag kunna
beskriva varje sort som en egen ResourceFlavor med egen kvot, så att en kampanj
hamnar på rätt kort och kön räknar varje sort för sig.

## Varför det är viktigt

Chartet skapar i dag **en** tom ResourceFlavor (`values.yaml` `queue.flavor`,
`templates/kueue.yaml:4,22`) och **en** kvotlista för hela ClusterQueue:n
(`queue.resources`). Det räcker när alla GPU:er är lika. Ett kluster med t.ex.
A100 och L4 kan inte uttryckas: Kueue kan inte hålla isär korten, en stor modell
kan hamna på ett litet kort, och kvoten säger inget om vilken sort som tar slut.
Presentationen (del 1, "ResourceFlavor") beskriver redan detta som målbilden.

## Vad som levereras

- `queue.flavors` i values: en lista där varje post har ett namn, `nodeLabels`
  (t.ex. `nvidia.com/gpu.product: NVIDIA-A100-SXM4-80GB`), valfria `nodeTaints`
  och en kvot för `cpu`, `memory` och `nvidia.com/gpu`.
- ClusterQueue:n renderas med en resursgrupp vars flavors kommer i den ordning
  values listar dem — Kueue provar dem i den ordningen.
- Dagens `queue.flavor` + `queue.resources` blir specialfallet en tom flavor, så
  att ett befintligt install renderar oförändrat.
- `test_chart_render.py` täcker en och två flavors.
- `docs/how-it-works/queueing.md` och `reference/chart.md`: hur man beskriver
  flera GPU-sorter.

## Klart när

- [x] Ett values-exempel med två flavors renderar två ResourceFlavors med
      `nodeLabels` och en ClusterQueue med en kvot per flavor för alla tre resurser.
      (`ci/full-values.yaml`, `test_chart_render.py`; kubeconform mot Kueues
      CRD-scheman i `make helm-template`.)
- [x] Ett install utan `queue.flavors` renderar exakt samma objekt som i dag.
      (`tests/golden/chart-queue.yaml`, inspelad före ändringen, ett fall per
      D-9-växel.)
- [x] En podd som admitteras på en flavor får flavorns node selector och
      hamnar på en nod med det kortet (verifierat på ett kluster eller i en
      Kueue-integrationstest). Verifierat i en envtest-integrationstest mot
      Kueue v0.19.5 (i Kueues eget testträd, utanför det här repot): ett Job
      utan node selector admitteras på den första flavorn och får dess
      `nodeLabels` som `nodeSelector`; ett Job med den andra flavorns labels
      admitteras där och får dess toleration; ett andra sådant Job väntar i
      stället för att spilla över på den första. Att podden sedan hamnar på
      noden är kube-schedulerns vanliga `nodeSelector` — inte kört på ett
      kluster med två sorters GPU.

Relaterat: B105 (en pipeline väljer storlek och därmed flavor), T05 (cohorts
mellan organisationer).
