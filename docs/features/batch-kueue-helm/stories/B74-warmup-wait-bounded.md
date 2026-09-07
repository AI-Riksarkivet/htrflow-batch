---
type: Product Backlog Item
id: 3007
parent: 2800
title: Ett trasigt warm-up håller inte GPU-kvoten i timmar
---

# B74 · Ett trasigt warm-up håller inte GPU-kvoten i timmar

**Story.** Som operatör vill jag att en kampanj vars warm-up inte blev klar
misslyckas snabbt med en mening, i stället för att varje index står i sin init
container med en GPU allokerad tills poddens deadline går ut.

## Varför det är viktigt

Init containern renderas som `until [ -f /data/warmup/<id>.done ]; do sleep 10;
done`, utan timeout (`render.py:190-196`), och podden reserverar `nvidia.com/gpu: 1`
under hela sin livstid — init inräknad — så Kueue håller kvoten genom väntan.
Uteblir markören väntar varje index 21 600 s, avslutas med 143 och görs om tre
gånger: fyra gånger sex timmar hållen GPU per index, utan arbete och utan signal
(X3). Samma familj: `_warmup_job` (`render.py:83-99`) sätter varken
`runtimeClassName`, `nodeSelector` eller `tolerations` — som kampanj-Jobbet får
(`:198-207`) — så på ett kluster med taintade GPU-noder hamnar warm-up:en på fel
nod och markören dyker aldrig upp där den behövs (X5).

## Vad som levereras

- Väntan är tidsbegränsad (default ~900 s, ett `converter.yaml`-värde) och
  avslutas med 13; `podFailurePolicy` får `containerName: warmup-wait` →
  `FailIndex`.
- `_warmup_job` sätter `runtimeClassName`, `nodeSelector` och `tolerations` från
  samma `cfg` som kampanj-Jobbet.
- `failure-handling.md` får kommandot som visar varför markören saknas.

## Klart när

- [ ] En kampanj mot en pipeline vars warm-up misslyckats ger ett misslyckat
      index inom timeouten, med en mening om varför.
- [ ] Renderat warm-up-Job och kampanj-Job har samma `runtimeClassName`,
      `nodeSelector` och `tolerations`, verifierat av ett test.
