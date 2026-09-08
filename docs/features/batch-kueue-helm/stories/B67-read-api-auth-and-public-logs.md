---
type: Product Backlog Item
id: 2996
parent: 2800
title: Allt bakom en inloggning — status-sidan, API:t, viewern och resultaten
---

# B67 · Allt bakom en inloggning — status-sidan, API:t, viewern och resultaten

**Story.** Som ansvarig för tjänsten vill jag att allt som htrflow-batch
visar — status-sidan, läs-API:t, viewern och resultatfilerna i bucketen —
ligger bakom en och samma inloggning, så att regeln är enkel att
förklara och att kontrollera: ingen ser något utan att ha loggat in, och
det finns inget "publikt" att hålla reda på.

## Varför det är viktigt

`packages/web` är en FastAPI-tjänst utan inloggning: den listar Jobs och
Pods i sina namespaces och serverar SPA:n. På PoC-klustret nås den bara
genom en SSH-tunnel, så det har inte spelat någon roll. Resultatbucketen
är i dag anonymt läsbar (viewern läser ALTO och `iiif.json` direkt
därifrån) och run-loggarna ligger under ett privat prefix — två regler,
en bucket-policy att underhålla, och en fråga per ny fil: "är den här
publik?". Produktägarens beslut (2026-09-07): **alla loggar in**, en regel.
Priset är att viewern inte längre kan läsa bucketen direkt: dess
ALTO- och IIIF-anrop går genom samma inloggade origin som sidan. Det
måste vara på plats innan B12 (DEV-klustret) gör tjänsten nåbar från
arbetsnätet.

## Vad som levereras
- Beslutet nedskrivet i `docs/how-it-works/decision-log.md`: allt kräver
  inloggning — status-sidan, `/api/v1/*`, viewern, ALTO/PAGE, `iiif.json`,
  `manifest.json` och run-loggar — och konton som räknas är medlemmar i
  GitHub-organisationen AI-Riksarkivet och i Hugging Face-organisationen
  Riksarkivet, senare även Riksarkivets egen IdP (Entra ID).
- Bucketen slutar vara anonymt läsbar: bucket-policyn för anonym läsning
  och `rustfs.publicLogs` tas bort ur chartet. Resultatfilerna serveras
  genom den inloggade originen — web-imagen (eller oauth2-proxy framför
  en S3-läsväg) läser bucketen med egna credentials — och
  `PUBLIC_RESULTS_BASE` byter namn till det den blir: resultatens
  URL-bas bakom inloggningen. Viewern (U03) öppnar volymer via den basen.
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
- Dokumentation: `docs/reference/frontend.md`, `docs/getting-started/viewing.md`,
  `docs/reference/configuration.md` (genererad).
- `HTRFLOW_NAMESPACES` och RBAC:en stämmer överens (eller listan tas bort), och `get_job` kontrollerar namespace mot konfigurationen. (revision 2026-09-07, X30)

## Klart när

- [ ] En oinloggad förfrågan mot status-sidan och `/api/v1/jobs` på
      DEV-klustret avvisas; en inloggning med ett GitHub-konto i
      AI-Riksarkivet och med ett HF-konto i Riksarkivet-organisationen
      släpps in, ett konto utanför organisationerna avvisas med en mening
      som säger varför.
- [ ] Verifierat mot HF:s OIDC-dokumentation vilken scope som ger
      organisationstillhörighet i userinfo, och att Dex läser den.
- [ ] En direkt URL mot bucketen (ALTO, `iiif.json`, `manifest.json`,
      run-logg) svarar 403 utan inloggning; samma volym öppnar i viewern
      efter inloggning, med text och overlays.
- [ ] Beslutet står i decision-loggen med datum och vem som fattade det.
