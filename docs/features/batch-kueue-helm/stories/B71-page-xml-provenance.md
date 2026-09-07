---
type: Product Backlog Item
id: 3000
parent: 2800
title: PAGE XML bär samma proveniens som ALTO
---

# B71 · PAGE XML bär samma proveniens som ALTO

**Story.** Som den som hellre arbetar med PAGE XML än ALTO vill jag att
PAGE-filen säger samma sak om vem och vad som skapade den — htrflow-batch,
image, htrflow-bas, kampanj och volym — så att valet av format inte avgör
om proveniensen följer med.

## Varför det är viktigt

Wrappern exporterar varje sida i båda formaten, men bara ALTO får
`htrflow-batch`-blocket (2026-09-07). PAGE XML:s `<Metadata>` har platsen
för det: `Creator`, `Created`/`LastChange` och `MetadataItem
type="processingStep"` med namn/värde-par, som andra HTR-verktyg redan
använder. Utan det är PAGE-filen bara märkt med htrflows egen `Creator`.

## Vad som levereras

- `provenance.py` stämplar även PAGE: `MetadataItem` per fakta-rad (image,
  htrflow-bas, wrapper-version, och det B70 lägger till för ALTO),
  `LastChange` satt till stämplingstiden, htrflows egna fält orörda.
- Tester på fixtur-PAGE; filen validerad mot PAGE 2019-schemat i E2E.
- Dokumentation: `docs/how-it-works/wrapper.md` (Provenance).

## Klart när

- [ ] En PAGE-fil från PoC-klustret innehåller samma fakta som ALTO-filen
      för samma sida och validerar mot `page2019.xsd`.
- [ ] Viewern (som läser ALTO) och `manifest.json` är opåverkade.
