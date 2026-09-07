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
  inloggning (status-sidan, `/api/v1/*`, run-loggar) och vilka konton som
  räknas: medlemmar i GitHub-organisationen AI-Riksarkivet och i
  Hugging Face-organisationen Riksarkivet, senare även Riksarkivets egen
  IdP (Entra ID) för medarbetare.
- Inloggningen sker framför web-imagen, inte i den: **Dex** som
  OIDC-broker med två connectors — `github` (GitHub är OAuth 2.0, inte
  OIDC, för användarinloggning) och en generisk `oidc`-connector mot
  Hugging Face (`huggingface.co` publicerar OIDC discovery; en Developer
  application ger client id/secret) — och **oauth2-proxy** som litar på
  enbart Dex och skyddar status-sidan och `/api/v1/*`. En login-sida med
  två knappar; `packages/web` får ingen egen användarhantering.
- Auktorisering, inte bara autentisering: connectorerna släpper bara in
  medlemmar i organisationerna ovan; Dex mappar det till `groups` och
  oauth2-proxy kräver gruppen. En tredje connector (Entra ID) läggs till
  utan att appen rörs.
- Chart: Dex och oauth2-proxy från deras upstream-charts, client secrets
  och cookie secret som Kubernetes Secrets refererade med namn
  (`existingSecret`, som S3-credentials), aldrig som values; callback-URL
  och TLS från ingressen (B12/U05; på PoC-klustret via SSH-tunnel och
  `http://localhost`-redirect, som både GitHub och HF tillåter för
  utveckling).
- Om run-loggarna inte längre är publika: `PUBLIC_RESULTS_BASE`-länkarna
  till dem går via API:t i stället för direkt mot bucketen.
- Dokumentation: `docs/reference/frontend.md`, `docs/getting-started/viewing.md`,
  `docs/reference/configuration.md` (genererad).

## Klart när

- [ ] En oinloggad förfrågan mot status-sidan och `/api/v1/jobs` på
      DEV-klustret avvisas; en inloggning med ett GitHub-konto i
      AI-Riksarkivet och med ett HF-konto i Riksarkivet-organisationen
      släpps in, ett konto utanför organisationerna avvisas med en mening
      som säger varför.
- [ ] Verifierat mot HF:s OIDC-dokumentation vilken scope som ger
      organisationstillhörighet i userinfo, och att Dex läser den.
- [ ] Viewern fungerar fortfarande utan inloggning för det som beslutet
      kallar publikt.
- [ ] Beslutet står i decision-loggen med datum och vem som fattade det.
