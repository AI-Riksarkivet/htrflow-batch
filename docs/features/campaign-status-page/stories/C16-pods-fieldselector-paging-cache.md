---
type: Product Backlog Item
id:
parent: 2923
title: Hämta bara de Pods som behövs — fieldSelector, paging och kort cache
---

# C16 · Hämta bara de Pods som behövs — fieldSelector, paging och kort cache

**Story.** Som läsare av en stor kampanj vill jag att kortet uppdateras utan att
API:t hämtar tiotusentals Pod-objekt varje minut, så att statussidan går att ha
öppen medan arkivet körs.

## Varför det är viktigt

`kube.py:112-119` listar Pods på `batch.kubernetes.io/job-name` utan
`field_selector`, utan `limit` och utan continue-token, och avkodar varje komplett
Pod-objekt till minne. Poddarna ligger kvar (`restartPolicy: Never`,
`backoffLimitPerIndex: 3`, TTL 24 h), så en kampanj med 10 000 volymer lämnar
10 000–40 000 Pods vid liv under körningen plus ett dygn. `app.py:132` gör anropet
vid varje detaljförfrågan, plus hela kampanj-ConfigMappen (630 KB) och en färsk
warm-up-lista. Projektionen är snabb (10 000 rader på 0,03 s) — kostnaden är
rundturerna (X13, X38).

## Vad som levereras

- `list_pods` tar `field_selector` och pagar; detaljvyn hämtar bara Pods för aktiva
  och misslyckade index.
- En kort TTL-cache (eller en informer) bakom `Reader` för Job- och
  warm-up-listorna.
- `docs/reference/frontend.md`: vad en pollning faktiskt kostar.

## Klart när

- [ ] En detaljförfrågan mot en kampanj med 10 000 index gör ett begränsat, pagat
      antal Pod-anrop — mätt, inte uppskattat.
- [ ] Två öppna kort i två flikar ger inte fyra warm-up-listningar per minut.
