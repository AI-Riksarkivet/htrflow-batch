---
type: Product Backlog Item
id: 2975
parent: 2831
title: Gränssnittet mellan leverantörens drift och Riksarkivets utveckling är beskrivet och övat
---

# T17 · Gränssnittet mellan leverantörens drift och Riksarkivets utveckling är beskrivet och övat

**Story.** Som Riksarkivet vill jag att det står skrivet vem som gör vad —
vid en felrättning som kräver både kodändring och omstart, vid planerat
underhåll med pausad kö, vid incident nattetid — så att en driftleverantör
kan ta över driften utan att utvecklingstakten eller supporten faller
mellan stolarna.

## Varför det är viktigt

Driftmodellen lägger drift hos leverantören och utveckling, modeller och
support hos Riksarkivet. Briefens egen varning: utan avsatt intern
förvaltningsresurs stannar tjänsten i lanseringsskicket. Runbook-index
(B57) finns för systemet; det som saknas är rollfördelningen och
överlämningarna.

## Vad som levereras

- En ansvarsmatris (drift / utveckling / support / avtal) per händelsetyp:
  deploy av ny version, hotfix, modellbyte, underhållsfönster, incident,
  kapacitetsändring, gallringsfel, personuppgiftsincident.
- Tekniska förutsättningar för gränssnittet: pausa/återuppta kön utan att
  räknas som otillgänglighet, underhållsläge i UI/API, promotion via Kargo
  (B34) som det enda sättet att deploya.
- En övning av två scenarier (hotfix, incident) med leverantören i
  pilotfasen; utfallet i runbooken.

## Klart när

- [ ] Ansvarsmatrisen är bilaga till driftavtalet.
- [ ] Kön kan pausas och återupptas från runbooken utan att ett pågående
      jobb förloras.
- [ ] Båda övningarna är genomförda och dokumenterade.
