---
type: Product Backlog Item
id: 2974
parent: 2831
title: Licenskedjan för kod, modeller och träningsdata är kartlagd före publicering
---

# T16 · Licenskedjan för kod, modeller och träningsdata är kartlagd före publicering

**Story.** Som en organisation som vill sätta upp tjänsten själv vill jag
veta under vilka villkor varje del får användas — koden, modellvikterna,
de förtränade modellerna de bygger på och träningsdatan — så att en
försiktig jurist hos oss kan säga ja.

## Varför det är viktigt

HTRflow är EUPL-1.2, men modellvikter licensieras separat och förtränade
modeller och träningsdata kan bära villkor som begränsar vidarespridning.
En oklar licenskedja gör återanvändningen omöjlig i praktiken och är en
uttalad risk i briefen.

## Vad som levereras

- En licensinventering per artefakt: htrflow, htrflow-batch, UI, varje
  modell (vikter, basmodell, träningsdata), LLM-spårets modell och
  ramverk (T11), med licens, källa och eventuella villkor.
- Licensfil och `LICENSES`-sida i docs; SBOM (B44/B45) kompletterad med
  modellernas licenser i OCI-annotationer (B35).
- Beslut per artefakt: publiceras, publiceras med villkor, publiceras inte.

## Klart när

- [ ] Varje artefakt tjänsten kör har en rad i inventeringen och en licens
      i sitt paket/manifest.
- [ ] Ingen artefakt med "okänd" licens finns i en publicerad release.
