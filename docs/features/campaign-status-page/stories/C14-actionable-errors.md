---
type: Product Backlog Item
id:
parent: 2923
title: Varje fel på status-sidan säger vad som hände, var, och vad användaren gör åt det
---

# C14 · Varje fel på status-sidan säger vad som hände, var, och vad användaren gör åt det

**Story.** Som den som beställt en kampanj vill jag att ett fel på
status-sidan säger vad som gick fel, i vilket steg, och vad *jag* kan göra
— rätta en revision i pipeline-filen, vänta för att systemet försöker
igen, eller be plattformsteamet — så att jag kan agera direkt i stället
för att tolka en stack trace eller gissa vem som äger felet.

## Varför det är viktigt

Regeln i htrflow-batch är att varje fel är en mening en människa kan
handla på, och frontendens `reasons`-modul är den enda platsen där
maskinformerna (`{stage, permanent, error}`, exit-koder) blir meningar.
Meningarna säger i dag *vad* och *var* ("reading the manifest failed:
…"), men sällan *vad man gör*, och de bär ofta wrapperns råa feltext
vidare — Hugging Face-fel med request-id och URL, IIIF-serverns HTTP-kod,
Kubernetes egna villkorsnamn. Skillnaden mellan permanent (rätta och
applicera igen) och tillfälligt (systemet försöker igen, vänta) finns i
termination-meddelandet men är inte det första en läsare ser.

## Vad som levereras

- En katalog över varje fel som kan nå sidan — per steg (config, setup,
  resume, load, stream, verify, publish, warm-up) och per orsak (modell
  saknas, revision fel, IIIF-server svarar inte, bild för stor, kvot,
  deadline, S3) — med tre delar: vad hände, var, nästa steg, och vem som
  äger nästa steg (beställaren, plattformsteamet, ingen: systemet
  försöker igen).
- Wrapperns och warm-up-jobbets feltexter normaliserade till den formen
  vid källan (permanent/tillfälligt först, orsak i ord, råa detaljer
  sist i en egen del), så att `reasons` inte behöver tolka
  leverantörernas text.
- Kampanjkortet visar "nästa steg"-delen först och länkar till
  pipeline- eller kampanjfilen i kampanjrepot när felet sitter där.
- Testet `reasons.test.ts` pinnar varje mening i katalogen; wrappern
  har ett test per normaliserad orsak.
- Dokumentation: katalogen som sida under `docs/reference/`,
  `docs/how-it-works/failure-handling.md`.

## Klart när

- [ ] De fyra felen från B63 Task 22:s E2E (osignerad image, opinnad
      image, fel registry, saknad modellrevision) och de tre från Task
      28 (modell saknas, revision saknas, cache-miss) visar på
      PoC-klustret var sitt nästa steg och sin ägare.
- [ ] Ingen mening på sidan innehåller en URL, ett request-id eller en
      stack trace utanför "detaljer"-delen.
- [ ] Ett tillfälligt fel säger att systemet försöker igen och när; ett
      permanent säger vad som ska rättas och att kampanjen måste
      appliceras igen.
