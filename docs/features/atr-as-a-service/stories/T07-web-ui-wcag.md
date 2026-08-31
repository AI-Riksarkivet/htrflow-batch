---
type: Product Backlog Item
id: 2965
parent: 2831
title: Ett tunt webbgränssnitt ovanpå API:et som uppfyller WCAG 2.1 AA
---

# T07 · Ett tunt webbgränssnitt ovanpå API:et som uppfyller WCAG 2.1 AA

**Story.** Som arkivarie utan utvecklare vill jag ladda upp material, välja
pipeline, följa mitt jobb och hämta resultatet i webbläsaren, och som
offentlig aktör vill jag att gränssnittet uppfyller tillgänglighetslagen,
så att tjänsten kan användas av alla i målgruppen utan integration.

## Varför det är viktigt

Statussidan (B05, C-serien) är intern och skrivskyddad; den visar
Riksarkivets kampanjer för data scientists. Kunderna behöver ett
gränssnitt för *sina* jobb. Lagen (2018:1937) om tillgänglighet till
digital offentlig service gäller och kräver tillgänglighetsredogörelse.

## Vad som levereras

- Sidor: logga in, organisation (T01), nytt jobb (T04, pipeline-val med
  förväntad kvalitet från T12), jobblista med köläge (T06), resultat och
  nedladdning (T08), kvot (T05). Enbart API-anrop under huven.
- Byggt på samma SvelteKit-grund och designtokens som statussidan;
  tangentbordsnavigering, kontrast, skärmläsarstöd; svenska och engelska.
- Tillgänglighetsredogörelse publicerad; automatiska tillgänglighetstester
  i CI (axe) på varje sida.

## Klart när

- [ ] Hela flödet — uppladdning till nedladdat resultat — går att göra
      med enbart tangentbord och med skärmläsare.
- [ ] axe rapporterar noll fel på nivå A/AA; redogörelsen är länkad från
      sidfoten.
- [ ] Inget gränssnittet gör saknas i OpenAPI-specifikationen.
