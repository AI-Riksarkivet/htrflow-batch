---
type: Product Backlog Item
id:
parent: 2800
title: Varje ALTO-fil säger vilken kampanj, volym och källbild den kommer från
---

# B70 · Varje ALTO-fil säger vilken kampanj, volym och källbild den kommer från

**Story.** Som arkivarie som får en ALTO-fil utanför sitt sammanhang vill
jag kunna läsa ur filen själv vilken kampanj och volym som skapade den,
vilken IIIF-bild som var källan och vilken organisation som körde
bearbetningen, så att proveniensen håller även när filen har lämnat
bucketen och status-sidan.

## Varför det är viktigt

Sedan 2026-09-07 bär varje ALTO ett `<Processing ID="htrflow-batch">`-block
med tid, image-digest, htrflow-basrevision och wrapperns version, bredvid
htrflows eget block med modeller och revisioner. Det säger *vad* som körde
men inte *på vad* eller *åt vem*. Det finns i volymens `manifest.json`
(kampanj-id, volym, källmanifest, canvas-id per sida), men den filen
följer inte med när en ALTO kopieras vidare. ALTO 4.4 har fält för exakt
detta: `sourceImageInformation/fileIdentifier` och `documentIdentifier`
för källan, `processingAgency` för organisationen, och fler
`processingStepDescription`-rader för kampanjen. Fälten valdes bort ur
den första leveransen för att hålla den liten.

## Vad som levereras

- I `sourceImageInformation`: canvas-id och bild-URL som `fileIdentifier`
  (typade), källmanifestets URL som `documentIdentifier`.
- I `htrflow-batch`-blocket: rader för kampanj-id, volym-referens,
  pipeline-id och URL:en till volymens `manifest.json`.
- `processingAgency` från ett nytt chart-värde (`provenance.agency`,
  tomt som default så att den öppna imagen inte är hårdkodad till
  Riksarkivet); wrappern får det som env.
- Tester på fixtur-ALTO; ALTO-filen validerad mot `alto-4-4.xsd` i E2E.
- Dokumentation: `docs/how-it-works/wrapper.md` (Provenance),
  `docs/reference/configuration.md` (genererad).

## Klart när

- [ ] En ALTO från PoC-klustret innehåller kampanj, volym, canvas-id,
      källmanifest och agency, och validerar mot ALTO 4.4.
- [ ] Ingen URL i blocket innehåller token eller inloggningsuppgifter
      (samma redigering som run-loggen).
- [ ] `processingAgency` saknas när chart-värdet är tomt, och filen
      validerar ändå.
