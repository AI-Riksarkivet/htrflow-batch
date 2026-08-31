---
type: Product Backlog Item
id: 2961
parent: 2831
title: Ett publikt jobb-API med öppen specifikation
---

# T03 · Ett publikt jobb-API med öppen specifikation

**Story.** Som integratör hos en organisation vill jag skapa jobb, ladda upp
material, starta, följa status, hämta resultat, avbryta och läsa vår kvot
över ett dokumenterat REST-API, så att vi kan koppla tjänsten till våra
egna system utan att klicka i ett gränssnitt.

## Varför det är viktigt

API:et är tjänstens primära gränssnitt; webbgränssnittet (T07) är ett tunt
lager ovanpå. Antal organisationer som integrerar direkt är ett av
framgångsmåtten — det visar produktionsanvändning, inte prov. htrflow-batch
har i dag inget API: arbetet kommer från ett Git-repo och statusen från en
JSON-fil i en bucket.

## Vad som levereras

- REST över HTTPS med OpenAPI-specifikation, publicerad och versionerad
  (`/v1`); autentisering med API-nyckel eller token (T01).
- Operationer: skapa jobb, ladda upp (T04), starta, status, hämta resultat
  (T08), avbryta (T06), läsa kvot (T05).
- Idempotent uppladdning (klientgenererad nyckel), maskinläsbara felkoder
  och tydliga felmeddelanden; begränsning per nyckel mot oavsiktlig
  överbelastning.
- API:et skriver kampanjer/volymer till samma reconciler-flöde som i dag
  (B04), så att GitOps-vägen och API-vägen är samma system.
- Klientexempel i curl och Python i docs.

## Klart när

- [ ] Hela livscykeln — skapa, ladda upp, starta, status, resultat — går
      att köra med curl enligt docs, utan UI.
- [ ] Samma uppladdning skickad två gånger ger ett jobb.
- [ ] OpenAPI-filen validerar och kontrolltestas i CI mot den körande
      tjänsten.
