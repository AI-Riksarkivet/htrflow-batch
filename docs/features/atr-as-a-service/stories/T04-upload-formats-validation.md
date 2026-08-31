---
type: Product Backlog Item
id: 2962
parent: 2831
title: Uppladdning av bilder, PDF, zip och IIIF-manifest med validering vid dörren
---

# T04 · Uppladdning av bilder, PDF, zip och IIIF-manifest med validering vid dörren

**Story.** Som användare vill jag lämna in mitt material som enskilda bilder
(JPEG, PNG, TIFF, JPEG 2000), som PDF med bildinnehåll, som zip eller som en
referens till ett IIIF Presentation-manifest, och få veta direkt om något
inte går att tolka, så att ett jobb aldrig misslyckas långt ned i
pipelinen på grund av en fil som kunde ha avvisats vid uppladdningen.

## Varför det är viktigt

Wrappern läser i dag bara IIIF-manifest över http(s). Målgruppens material
är inskannade PDF:er och bildmappar. Att extrahera PDF-sidor, packa upp
zip och validera format innan jobbet köas är skillnaden mellan "fel efter
tre timmar i kö" och "fel på en sekund".

## Vad som levereras

- Uppladdning till temporär objektlagring med storleksgränser per fil och
  per jobb (`[X]` MB / `[Y]` GB), och valfritt jobbnamn + referensfält som
  följer med i resultatet.
- Normalisering till en intern manifest per jobb (sidordning, kanvasid,
  bild-URL) så att wrappern kör oförändrad oavsett inmatningsform.
- PDF-sidextraktion och zip-uppackning som ett förbearbetningssteg i
  jobbet, med gränser mot zip-bomber och sidantal.
- Validering vid uppladdning: format, läsbarhet, storlek; avvisning med
  fel per fil.

## Klart när

- [ ] Ett jobb med 500 sidor ur en PDF och ett med en zip med 500 JPEG ger
      samma resultatstruktur som ett IIIF-jobb.
- [ ] En korrupt fil i en zip avvisas vid uppladdningen med filnamn och
      orsak; inget jobb skapas.
- [ ] Gränserna syns i API-svaret och i UI:t innan uppladdning börjar.
