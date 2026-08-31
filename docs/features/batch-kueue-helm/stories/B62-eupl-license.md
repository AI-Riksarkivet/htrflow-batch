---
type: Product Backlog Item
id: 2977
parent: 2800
title: htrflow-batch licensieras under EUPL-1.2, samma som htrflow
---

# B62 · htrflow-batch licensieras under EUPL-1.2, samma som htrflow

**Story.** Som en organisation som vill sätta upp batch-systemet i egen
miljö vill jag att htrflow-batch har samma licens som htrflow — European
Union Public Licence v1.2 — tydligt angiven i repot, i varje paket, i
chartet och i imagerna, så att jag vet vad jag får göra utan att fråga.

## Varför det är viktigt

Repot `AI-Riksarkivet/htrflow-batch` är publikt sedan 2026-08-31 men
saknar licens helt: ingen `LICENSE`-fil, inget `license`-fält i något
`pyproject.toml`, inget i `Chart.yaml` eller `frontend/package.json`;
GitHub visar "none". Utan licens gäller upphovsrätten fullt ut — ingen får
kopiera, ändra eller köra koden — vilket är motsatsen till briefens
princip att koden ska kunna återanvändas av andra (ATRaaS, T16).
htrflow är EUPL-1.2; samma licens på wrappern, reconcilern, chartet och
statussidan gör kedjan enhetlig.

## Vad som levereras

- `LICENSE` i repots rot med EUPL-1.2-texten (kopierad från htrflow) och
  ett *License*-avsnitt i README och docs-startsidan.
- `license = {file = "LICENSE"}` (eller SPDX `EUPL-1.2`) i rotens och
  varje paket-`pyproject.toml`; `"license": "EUPL-1.2"` i
  `frontend/package.json`; `artifacthub.io/license: EUPL-1.2` och
  `annotations` i `charts/htrflow-batch/Chart.yaml`.
- OCI-label `org.opencontainers.image.licenses=EUPL-1.2` på de tre
  imagerna i publish-flödet, så att licensen följer med i SBOM och
  registry.
- Kontroll av tredjepartskomponenter som paketeras i imagerna — UV4-forken
  (MIT), frontendens npm-beroenden, Python-beroenden — så att inget
  strider mot EUPL-1.2; utfallet i licensinventeringen (ATRaaS T16).

## Klart när

- [ ] GitHub visar *EUPL-1.2* på repots startsida.
- [ ] `uv build` av varje paket och `helm show chart` visar licensen;
      `docker inspect` av en publicerad image visar labeln.
- [ ] Inventeringen listar inga komponenter med okänd eller oförenlig
      licens.
