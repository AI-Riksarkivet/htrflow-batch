---
type: Product Backlog Item
id: 2966
parent: 2831
title: Resultat i ALTO, PAGE, text och JSON med ett leveransmanifest som anger modellversion
---

# T08 · Resultat i ALTO, PAGE, text och JSON med ett leveransmanifest som anger modellversion

**Story.** Som forskare vill jag hämta transkriptionen i det format mitt
verktyg läser — ALTO XML, PAGE XML, oformaterad text eller JSON med
radkoordinater och konfidensvärden — och veta exakt vilken pipeline och
modellversion som skapade den, så att jag kan citera resultatet i en
publikation och återskapa det senare.

## Varför det är viktigt

Wrappern levererar ALTO/PAGE och en IIIF-manifest per volym (B01) med
provenance i miljön (image-digest). Text och JSON saknas, och det finns
inget leveransmanifest på jobbnivå som en användare kan spara. Ett
versionerat JSON-schema är det som gör att integrationer överlever
förändringar.

## Vad som levereras

- Två nya exportformat i wrapperns serializers: `.txt` per sida/volym och
  JSON med rad-id, koordinater, text, konfidens.
- Leveransmanifest per jobb: jobb-id, tidpunkt, pipeline, modell-id och
  modellversion (från T12), sidantal, status, kundens referensfält, digest
  för varje resultatfil.
- Versionerat JSON-schema (`schemaVersion`) publicerat i docs; ändringar
  bakåtkompatibla inom major.
- Nedladdning som zip via API och UI.

## Klart när

- [ ] Ett jobb ger alla fyra formaten och ett manifest som validerar mot
      schemat.
- [ ] Manifestets modell-id/version räcker för att köra om samma pipeline
      och få samma text.
- [ ] Ett schema-tillägg bryter inte det Python-klientexempel som ligger i
      docs.
