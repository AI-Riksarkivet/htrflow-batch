---
type: Product Backlog Item
id: 2972
parent: 2831
title: Tjänstens nyckeltal mäts per organisation och per pipeline
---

# T14 · Tjänstens nyckeltal mäts per organisation och per pipeline

**Story.** Som produktägare vill jag se kölängd, genomströmning,
felfrekvens, kapacitetsutnyttjande och mediankötid — per organisation och
per pipeline — samt antal aktiva organisationer och API-integrationer, så
att kvoter och kapacitetsram kan sättas på siffror och framgångsmåtten
kan rapporteras.

## Varför det är viktigt

Framgångsmåtten i produktbriefen (aktiva organisationer per sektor, sidor
per månad, mediankötid, andel felfria jobb, direktintegrationer) går inte
att räkna utan tenant-dimensionen. Metrics-exportern (B40) och
dashboards/larm (B59/B60) finns för systemet som helhet.

## Vad som levereras

- Etiketter `organisation`, `sektor`, `pipeline` på befintliga metrics;
  nya: sidor per månad per organisation, mediankötid, andel felfria jobb,
  API-nycklar med anrop senaste 30 dagarna.
- En dashboard "Tjänsten" i Grafana som kod (B59) och en månadsrapport som
  exporteras från samma frågor.
- Larm på kölängd och kapacitetsutnyttjande (B60) med tröskel från
  kapacitetsramen.

## Klart när

- [ ] Månadsrapporten visar alla framgångsmått i produktbriefen avsnitt
      12 utan handräkning.
- [ ] Ett larm går när kön överstiger tröskeln under en timme.
