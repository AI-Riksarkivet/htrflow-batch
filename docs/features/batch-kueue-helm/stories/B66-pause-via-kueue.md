---
type: Product Backlog Item
id: 2995
parent: 2800
title: Paus av en kampanj uttrycks i Kueue, inte genom att vi patchar dess Workload
---

# B66 · Paus av en kampanj uttrycks i Kueue, inte genom att vi patchar dess Workload

**Story.** Som operatör vill jag att `paused: true` i kampanjfilen blir en
paus som Kueue själv förstår och äger, så att en pausad kampanj lämnar
tillbaka sin GPU-kvot, återupptas utan sidoeffekter och inte kräver att vårt
apply-steg har rätt att skriva i Kueues egna objekt.

## Varför det är viktigt

I B63 äger Kueue fältet `suspend` på Indexed Job-objektet, så en kampanj
kan inte pausas genom att sätta det själv. Den lösning som finns i dag är
att `htrflow-campaigns apply` (`cluster.sync_pause`) letar upp kampanjens
Workload och sätter `spec.active: false`. Det fungerar på Kueue 0.18, men
det är en andra API-rundtur mot ett objekt vi inte renderar, det kräver
`patch` på `workloads` i apply-stegets RBAC, och det är ett beteende Kueue
inte lovar att behålla. Ett försök att i stället pausa vid rendering (ta
bort kö-etiketten) förkastades på PoC-klustret: Workloaden behöll sin kvot
och etiketten är oföränderlig när kampanjen ska återupptas. Den rätta
formen är att kampanjens paus är ett deklarerat tillstånd på det objekt vi
renderar, som Kueue läser.

## Vad som levereras

- En utredning av vad Kueue (från 0.18 och framåt) erbjuder som deklarativ
  paus på Job-nivå — ett fält eller en annotation på Jobbet som Kueue
  själv översätter till inaktiv Workload — och när det kan användas i vår
  version.
- Konvertern renderar pausen som det tillståndet; `sync_pause` och
  `patch`-rättigheten på `workloads` tas bort ur `cluster.py` och
  `apply-rbac.yaml`.
- Om Kueue inte har någon sådan mekanism: en issue uppströms med vårt
  användningsfall, och den nuvarande lösningen dokumenterad som medveten
  övergångslösning i `docs/how-it-works/campaigns.md`.
- Dokumentation: `docs/how-it-works/campaigns.md`, `docs/reference/`
  (RBAC-tabellen).

## Klart när

- [ ] `paused: true` → `paused: false` på en kampanj i PoC-klustret
      frigör kvoten under pausen och startar om utan att någon
      Workload patchas av oss.
- [ ] Apply-stegets ServiceAccount har inte längre `patch` på
      `workloads`, och `make ci` är grön.
- [ ] Beteendet är beskrivet på en sida, med Kueue-versionen det kräver.
