---
type: Product Backlog Item
id: 2969
parent: 2831
title: Ett LLM-baserat OCR-spår för samtida tryckt och maskinskrivet material
---

# T11 · Ett LLM-baserat OCR-spår för samtida tryckt och maskinskrivet material

**Story.** Som kommun med inskannade diarier och protokoll från 1900-talet
vill jag välja en pipeline byggd på öppna vision-språkmodeller för svenska
och engelska, så att maskinskrivet, tryckt och sen handskrift ger bättre
text än de historiska HTR-modellerna — och så att jag varnas för att
modellen kan hitta på text där bilden är otydlig.

## Varför det är viktigt

Riksarkivets egna modeller är tränade på historisk svensk handskrift;
målgruppens vanligaste material är 1900-talets maskinskrivna och tryckta
handlingar. Vision-språkmodeller läser det väl men kan producera flytande,
påhittad text — konfidens per rad, dokumenterade begränsningar och
rekommendation om stickprov är därför obligatoriska delar, inte tillval.

## Vad som levereras

- En htrflow-pipeline med ett öppet OCR-ramverk och en öppen VLM (svenska,
  engelska), körbar i samma wrapper och Kueue-kö som HTR-spåret, som
  modellartefakt (B35).
- Radkonfidens i resultatet (T08); uppmätt CER/WER på ett samtida testset
  publicerat i modellregistret (T12).
- Hallucinationsskydd: lågkonfidens-markering, tydlig text i UI vid val av
  spåret, rekommendation om stickprov i leveransen.

## Klart när

- [ ] Ett 100-sidors maskinskrivet testset ger lägre CER med LLM-spåret än
      med HTR-spåret; siffrorna står i registret.
- [ ] Rader med konfidens under tröskeln är markerade i JSON och i UI.
- [ ] Spåret väljs per jobb och körs på samma GPU-kvot som övriga.
