---
type: Product Backlog Item
id: 3017
parent: 2800
title: htrflow-campaigns apply varnar när window inte ryms i kvoten
---

# B84 · `htrflow-campaigns apply` varnar när `window` inte ryms i kvoten

**Story.** Som den som applyar sin första kampanj vill jag få veta direkt att den
aldrig kommer att admitteras, i stället för att se "Queued" i evighet.

## Varför det är viktigt

Chartets kvot är cpu 4 / minne 8Gi / `nvidia.com/gpu` 1 — en podd
(`values.yaml:57-62`) — medan converterns default-`window` är 20
(`models.py:265`) och partial admission är medvetet borttaget
(`render.py:126-130`). En första kampanj på default-värden renderar
`parallelism: 20` = 80 CPU / 20 GPU och blir oadmitterbar för alltid, vilket syns
som "Queued" och ingenting annat. `test_chart_agreement.py` jämför namn, inte
aritmetik (X18).

## Vad som levereras

- `apply` läser ClusterQueue:ns `nominalQuota` — den talar redan med API-servern —
  och varnar när `parallelism × per-podd-request` överstiger den, med en mening som
  säger vilket värde som ska ändras.
- `docs/reference/campaign-yaml.md` och `chart.md`: relationen mellan `window` och
  kvoten, på ett ställe.

## Klart när

- [ ] En kampanj med `window: 20` mot default-kvoten ger en varning vid apply som
      namnger både kvoten och det renderade `parallelism`.
- [ ] En kampanj som ryms applyar tyst som förut.
