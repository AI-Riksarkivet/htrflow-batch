---
type: Product Backlog Item
id: 2968
parent: 2831
title: Bilder gallras, resultat har hämtningsfrist och loggar saknar dokumentinnehåll
---

# T10 · Bilder gallras, resultat har hämtningsfrist och loggar saknar dokumentinnehåll

**Story.** Som kund vill jag att mina uppladdade bilder försvinner `[7]`
dagar efter avslutat jobb — eller omedelbart om jag begär det — att
resultaten finns kvar i `[30]` dagar, och att tjänstens loggar aldrig
innehåller mitt dokumentinnehåll, så att tjänsten är teknisk bearbetning
och inte en lagringsplats.

## Varför det är viktigt

Gallringen är grunden för att bilderna inte blir allmänna handlingar (T02)
och en del av PUB-avtalet. Den måste vara verifierbar — inklusive i
säkerhetskopior — inte en förhoppning. htrflow-batch behåller i dag allt i
resultat-bucketen; wrapperns loggar innehåller inte sidinnehåll men det
är inte kontrollerat som krav.

## Vad som levereras

- Livscykelregler i objektlagringen per prefix: bilder `[7]` dagar efter
  jobbets slut, resultat `[30]` dagar; "radera nu" som API-operation som
  också tar bort resultat.
- Säkerhetskopior som följer samma frister (eller inte tas för
  kundmaterial alls); leverantörens bekräftelse i driftavtalet.
- Åtkomst- och jobbloggar `[90]` dagar, utan dokumentinnehåll — ett test i
  CI som söker efter transkriberad text i loggarna.
- Gallringen loggad per jobb (vad, när) så att den kan visas för kunden.

## Klart när

- [ ] Ett jobbs bilder är oåtkomliga dag `[7]+1`, resultatet dag `[30]+1`,
      verifierat med API och direkt mot lagringen.
- [ ] "Radera nu" ger tomt svar inom en minut; gallringsloggen visar det.
- [ ] Loggsökningen i CI hittar inga OCR-strängar från testkorpusen.
