---
type: Product Backlog Item
id: 2970
parent: 2831
title: Ett modellregister med uppmätt CER/WER per modell och materialtyp, synligt i tjänsten
---

# T12 · Ett modellregister med uppmätt CER/WER per modell och materialtyp, synligt i tjänsten

**Story.** Som användare vill jag innan jag väljer pipeline se vilken
kvalitet som är rimlig att vänta för mitt material — 1600-talshandskrift
ger inte samma resultat som 1800-tal — och efteråt kunna slå upp exakt
vilken modellversion som användes, så att mina förväntningar stämmer och
resultatet går att citera.

## Varför det är viktigt

Felaktiga förväntningar är den vanligaste orsaken till missnöje med HTR.
Kvaliteten ska framgå i tjänsten, inte bara i dokumentationen. Modeller
som signerade OCI-artefakter (B35) ger identiteten; det som saknas är
mätvärdena och kopplingen till varje resultat.

## Vad som levereras

- Register (i repot, publicerat i docs och via API) över pipelines: modell-id,
  version, avsett material och period, CER/WER per materialtyp med
  beskrivning av testmaterialet, kända begränsningar.
- Mätningen som ett reproducerbart CI-jobb mot fasta testset, körd vid
  varje ny modellversion.
- UI (T07) visar förväntad felnivå vid pipeline-val; manifestet (T08) bär
  modell-id/version.

## Klart när

- [ ] Varje pipeline som går att välja har CER/WER för minst två
      materialtyper och en beskrivning av testsetet.
- [ ] En ny modellversion utan uppdaterade mätvärden kan inte publiceras.
