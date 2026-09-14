---
type: Product Backlog Item
id: 3031
parent: 2800
title: En död inferens-tråd i htrflow stoppar inte volymen — sidan misslyckas, pipelinen byggs om, körningen fortsätter
---

# B88 · En död inferens-tråd i htrflow stoppar inte volymen — sidan misslyckas, pipelinen byggs om, körningen fortsätter

**Story.** Som operatör vill jag att en sida som får htrflows modelltråd att
krascha bokförs som misslyckad och att wrappern går vidare med nästa sida,
i stället för att podden står stilla med GPU:n reserverad tills deadline
och sedan gör om samma sida tre gånger.

## Varför det är viktigt

Den 2026-09-08 stannade volymen R0001203 efter 43 sidor: htrflows
YOLO-segmentering kastade `TypeError: 'NoneType' object is not iterable`
(`htrflow/models/ultralytics/yolo.py:87`, en detektion utan polygon) inne
i `Inference`-stegets daemon-tråd (`pipeline/steps.py:112`). Tråden dog,
`pipeline.run(document)` blockerade för evigt på trådens kö, och wrappern
väntade med 6 m CPU och en reserverad GPU. Ingen rad i run-loggen efter
undantaget, ingen stage-förändring, inget termination message — podden
hade stått till `activeDeadlineSeconds` (6 h), avslutats med 143,
och gjorts om tre gånger på samma sida: ett dygn GPU för en bild. Samma
dödläge väntar bakom varje undantag i ett modellanrop, inte bara det här
(upstream-buggen i htrflow rapporteras separat).

## Vad som levereras

- Wrappern upptäcker att ett pipeline-steg saknar sin arbetstråd
  (`Inference._thread.is_alive()` är falskt) — kontrollen sker innan och
  efter varje `pipeline.run`, och `process_page` körs med en vakt som
  slår larm när tråden dör mitt i en sida — och bokför sidan som misslyckad
  med en mening som namnger steget och modellen.
- Efter en död tråd byggs pipelinen om (`load_pipeline`, modellerna laddas
  igen från cachen) och nästa sida bearbetas; volymen **blir klar** med den
  sidan bokförd som `failed` i `manifest.json`, räknad i `pages_failed`, och
  med sin mening i `progress.json` — inte som en misslyckad volym utan
  markör.
- Felet räknas som sidfel, inte som volymfel: wrappern avslutar med 0 och
  indexet blir `Complete` (produktägaren, 2026-09-14). Exit 1 blir det bara
  om en sida saknas helt i resultaten, eller om ingen av de sidor körningen
  bearbetade lyckades.
- Dokumentation: `docs/how-it-works/failure-handling.md` (nytt fel-läge),
  `docs/how-it-works/wrapper.md`.

## Klart när

- [x] Ett test med en fejkad pipeline vars tråd dör på sida 2 ger: sida 2
      `failed` med meningen, sida 3 bearbetad av en ombyggd pipeline,
      volymen klar.
- [x] Volymen avslutas som klar med sidan bokförd som misslyckad: wrappern
      går ut med 0, `manifest.json` och `iiif.json` publiceras, `pages_failed`
      är 1 och kampanjraden läser "637 / 638 pages · 1 failed" — indexet görs
      inte om fyra gånger på en sida som ändå misslyckas likadant varje gång.
- [ ] R0001203 körs igenom på PoC-klustret: 637 sidor ok, sidan med
      polygon-felet `failed` med sin mening, ingen stillastående period
      längre än en sidas bearbetningstid.
