---
type: Product Backlog Item
id:
parent: 2800
title: Åtkomstkontroll för läs-API:t och ett beslut om vad som är publikt
---

# B67 · Åtkomstkontroll för läs-API:t och ett beslut om vad som är publikt

**Story.** Som ansvarig för tjänsten vill jag att status-sidan och dess
läs-API (`/api/v1/jobs`) bara kan nås av dem som ska se dem, och att det
är uttalat vilka resultat- och loggfiler som är publika, så att
kampanjernas inre — volymlistor, körloggar, felorsaker — inte ligger öppna
för vem som helst när klustret får en riktig adress.

## Varför det är viktigt

`packages/web` är en FastAPI-tjänst utan inloggning: den listar Jobs och
Pods i sina namespaces och serverar SPA:n. På PoC-klustret nås den bara
genom en SSH-tunnel, så det har inte spelat någon roll. Resultatbucketen
är avsiktligt publik (viewern läser ALTO och `iiif.json` direkt därifrån),
och varje volyms run-log skickas dit med URL:er redigerade — men loggen
avslöjar ändå filnamn, tider och feltexter. Frågan "vem får se vad" har
skjutits upp genom hela B63 och måste besvaras innan B12 (DEV-klustret)
gör tjänsten nåbar från arbetsnätet.

## Vad som levereras

- Ett beslut, nedskrivet i `docs/how-it-works/decision-log.md`: vilka delar som är
  publika (ALTO/PAGE, `iiif.json`, `manifest.json`), vilka som kräver
  inloggning (status-sidan, `/api/v1/*`, run-loggar) och vem som räknas
  som inloggad (Riksarkivets IdP via OIDC, eller nätverksgräns).
- Inloggning framför web-imagen på det sätt beslutet säger — i första hand
  på ingress-nivå (oauth2-proxy eller motsvarande) så att `packages/web`
  förblir utan egen användarhantering; chart-värden för det.
- Om run-loggarna inte längre är publika: `PUBLIC_RESULTS_BASE`-länkarna
  till dem går via API:t i stället för direkt mot bucketen.
- Dokumentation: `docs/reference/frontend.md`, `docs/getting-started/viewing.md`,
  `docs/reference/configuration.md` (genererad).

## Klart när

- [ ] En oinloggad förfrågan mot status-sidan och `/api/v1/jobs` på
      DEV-klustret avvisas; en inloggad medarbetare ser dem.
- [ ] Viewern fungerar fortfarande utan inloggning för det som beslutet
      kallar publikt.
- [ ] Beslutet står i decision-loggen med datum och vem som fattade det.
