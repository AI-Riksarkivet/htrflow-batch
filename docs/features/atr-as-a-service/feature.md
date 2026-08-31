---
type: Feature
id: 2831
parent: 2830
title: ATR as a Service (ATRaaS)
---

# Feature: ATR as a Service (ATRaaS) (#2831)

## I ett stycke

En avgiftsfri, registreringspliktig tjänst där myndigheter, kommuner,
regioner, lärosäten och ABM-institutioner laddar upp dokumentbilder, väljer
pipeline och hämtar transkriptioner i etablerade format — asynkront, i kö,
på Riksarkivets GPU-kapacitet. Tjänsten är htrflow-batch med ett
tjänstelager ovanpå: organisationer, API, uppladdning, kvoter, gallring och
ett tunt webbgränssnitt. Det som redan finns (wrapper, Kueue-kö, GitOps,
statussida, viewer, supply chain) återanvänds; det som saknas är stories
nedan. Underlag: produktbrief *Transkribering som tjänst* v0.1.

## Fastslagna egenskaper (inte stories)

- **API:et är tjänsten.** Webbgränssnittet är ett tunt lager; allt som går
  i UI:t går via API:et.
- **Teknisk bearbetning för kundens räkning.** Bilder blir inte allmänna
  handlingar hos Riksarkivet (TF 2 kap. 13 §); Riksarkivet använder aldrig
  kundens material för egna ändamål, inte heller för modellträning.
- **Ingen redigering, ingen lagring, ingen kundträning.** Tjänsten är inte
  Transkribus, inte ett arkiv, inte en bildbank.
- **Version 1 utan sekretessreglerade uppgifter.** En riskavgränsning som
  upprätthålls med villkor och försäkran; säkerheten dimensioneras ändå som
  om avgränsningen kan brytas.
- **Kö och kvoter är policy.** Utan prissignal är kön det enda styrmedlet.
- **Leverantörsoberoende.** Allt körs på standard-Kubernetes; inga
  molnspecifika tjänster; kod, chart och images publiceras öppet.
- **Samma pipeline internt.** Riksarkivets egen produktion kör i tjänsten,
  så att den får last och fel upptäcks tidigt.

## Beslut som krävs innan stories nedan kan slutföras

Mandat och finansiering; kapacitetsram och kvotnivåer (`[N]`); gallringsfrister
(`[7]`/`[30]` dagar); inloggningslösning (SWAMID/eduGAIN, Sambi, egen); relation
till kommersiella alternativ; juridisk granskning av avsnitt 7; intern
förvaltningsresurs; licenskedja; om version 2 med sekretess ska förberedas.

## Redan täckt av htrflow-batch (inga nya stories)

Wrapper och format ALTO/PAGE (B01), Kueue-kö (B02), Helm-installation (B03),
GitOps (B04), statussida (B05, C-serien), HCP-lagring (B10), dev/prod-miljö
(B12, B15), arkivskala (B16), separata credentials (B17), prioritet för brådskande
(B18), kvalitetsprediktion (B20), promotion med Kargo (B34), modeller som OCI
(B35), metrics (B40), dashboards/larm (B59/B60), runbook (B57), viewer (U-serien).

## Stories

### Inte påbörjade — i ordning

| Id | Story |
|---|---|
| [T01](stories/T01-organisations-users-roles.md) | Organisationer registrerar sig och styr sina användare, roller och API-nycklar |
| [T02](stories/T02-terms-dpa-attestation.md) | Villkor, personuppgiftsbiträdesavtal och försäkran tecknas i tjänstens flöden |
| [T03](stories/T03-job-api.md) | Ett publikt jobb-API med öppen specifikation |
| [T04](stories/T04-upload-formats-validation.md) | Uppladdning av bilder, PDF, zip och IIIF-manifest med validering vid dörren |
| [T05](stories/T05-quotas-fairness-per-org.md) | Kön fördelas rättvist mellan organisationer med kvoter och en klass för små jobb |
| [T06](stories/T06-queue-position-cancel.md) | Köposition, uppskattad start och avbrytande med delresultat |
| [T07](stories/T07-web-ui-wcag.md) | Ett tunt webbgränssnitt ovanpå API:et som uppfyller WCAG 2.1 AA |
| [T08](stories/T08-result-formats-manifest.md) | Resultat i ALTO, PAGE, text och JSON med ett leveransmanifest som anger modellversion |
| [T09](stories/T09-webhook-on-completion.md) | Webhook när ett jobb är klart |
| [T10](stories/T10-retention-purge-logs.md) | Bilder gallras, resultat har hämtningsfrist och loggar saknar dokumentinnehåll |
| [T11](stories/T11-llm-ocr-track.md) | Ett LLM-baserat OCR-spår för samtida tryckt och maskinskrivet material |
| [T12](stories/T12-model-registry-cer-wer.md) | Ett modellregister med uppmätt CER/WER per modell och materialtyp, synligt i tjänsten |
| [T13](stories/T13-tenant-isolation.md) | En organisations material och resultat är åtskilda från alla andras |
| [T14](stories/T14-service-metrics-per-org.md) | Tjänstens nyckeltal mäts per organisation och per pipeline |
| [T15](stories/T15-recreate-in-five-days.md) | Tjänsten kan återskapas i en annan miljö inom fem arbetsdagar |
| [T16](stories/T16-license-chain.md) | Licenskedjan för kod, modeller och träningsdata är kartlagd före publicering |
| [T17](stories/T17-ops-dev-interface.md) | Gränssnittet mellan leverantörens drift och Riksarkivets utveckling är beskrivet och övat |
| [T18](stories/T18-pilot.md) | Pilot med ett fåtal inbjudna organisationer på verkliga avtal och verklig last |
