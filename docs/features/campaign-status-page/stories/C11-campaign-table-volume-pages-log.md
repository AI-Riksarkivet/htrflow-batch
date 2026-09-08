---
type: Product Backlog Item
id: 3001
parent: 2923
title: Kampanjtabellen — volym-id öppnar viewern, en kolumn för sidor, en logg-länk
---

# C11 · Kampanjtabellen — volym-id öppnar viewern, en kolumn för sidor, en logg-länk

**Story.** Som den som följer en kampanj vill jag klicka på volymens id för
att öppna den i viewern, se hur många sidor som är klara av hur många, och
ha en enda logg-länk per volym, så att tabellen svarar på "hur långt har
den kommit och hur ser det ut" utan en rad med länkar att välja bland.

## Varför det är viktigt

Kampanjsidan visar i dag en "open"-länk och en kolumn med flera länkar
(viewer, källa, logg) per volym, men inget om antalet sidor. Det man vill
göra med en volym är nästan alltid att titta på resultatet eller läsa
loggen; källmanifestets länk behövs sällan och hör hemma i run-loggens
sammanfattning. Sidantalet finns redan i varje volyms publika
`manifest.json` (`pages` och `results`), som webbläsaren kan hämta för de
rader som syns.

## Vad som levereras

- Volymens id är länken till viewern; "open"-länken tas bort.
- En kolumn *Sidor* med `klara/totalt`, hämtad per synlig rad från
  volymens `manifest.json` i bucketen (ingen ändring i API:t); tom tills
  manifestet finns.
- Kolumnen *Länkar* blir *Logg* med en länk; källmanifestets länk flyttar
  till run-loggens sammanfattning.
- Frontend-tester för de tre ändringarna; dokumentation:
  `docs/reference/frontend.md`, `docs/getting-started/viewing.md`.
- Levererat 2026-09-08: en *Models*-rad på kampanjkortet (varje modell länkad
  till sitt Hugging Face-repo på den revision pipelinen pinnat), en
  GitHub-länk i sidhuvudet och den körande versionen där
  (`GET /api/v1/version` = webbpaketets egen version).

## Klart när

- [ ] På PoC-klustret öppnar ett klick på volym-id:t viewern på rätt
      volym, sidkolumnen visar `1/1` för en färdig envolymskampanj, och
      logg-länken öppnar run-loggen.
- [ ] En volym utan `manifest.json` ännu visar en tom sidkolumn, inte ett
      fel.
- [ ] Frontendens LOC-budget hålls exakt.
