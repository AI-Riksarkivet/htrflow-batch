---
type: Product Backlog Item
id: 2978
parent: 2800
title: Reconcilern ersätts av en controller med en CRD i etcd som sanningskälla för jobb
---

# B63 · Reconcilern ersätts av en controller med en CRD i etcd som sanningskälla för jobb

**Story.** Som förvaltare av batch-systemet vill jag att ett
transkriberingsjobb är ett Kubernetes-objekt — en `TranscriptionJob`-CRD
med status — som en controller reconcilar, i stället för YAML som en
CronJob tolkar var femte minut och speglar till fyra JSON-filer i en
bucket, så att kodbasen krymper, tillståndet finns på ett ställe och varje
ATRaaS-behov (kvot, avbryt, gallring, tenant-isolation, webhook) blir en
controller-funktion i stället för ett tillägg på filer i S3.

## Varför det är viktigt

Reconcilern är systemets komplexitetscentrum: `main.py` 915 rader, en
580-raders `_Pass`-klass, en Lease mot överlappande ticks och
`status.json`, `attempts.json`, `validation.json`, `volumes.json` som
skrivs om varje tick — en egenbyggd databas och scheduler ovanpå en
bucket, som kopierar det API-servern redan vet (Kueue-kö, Jobs, utfall).
Auditen 2026-08-26 fann den O(färdiga volymer) per tick (B29). ATRaaS
(T01–T18) behöver ett jobblager med tillstånd, kö per organisation,
avbryt och gallring; alla finns nativt i Kubernetes + Kueue. Att sätta
tick-loopen i dev (I15) och sedan byta ut den är dubbelt arbete — därför
före I15.

## Vad som levereras

- **CRD `TranscriptionJob`** (namespace-scoped; namespace = organisation):
  `spec` med källor (IIIF-manifest eller intern manifest från
  uppladdning, T04), pipeline, prioritet; `status` med `done/failed/total`,
  fas, senaste fel, länkar till resultat. **En CR per jobb, inte per
  volym**; bara pågående volymer är Kubernetes Jobs (dagens `window`).
- **Controller** (samma mönster som rask-operator) som: validerar källor
  en gång, skapar Kueue-köade Jobs upp till fönstret, bevakar Job-utfall
  med watch i stället för tick, uppdaterar `status` med optimistisk
  låsning, försöker om enligt `attemptCap`, städar via ownerReferences
  och TTL.
- **Gränsen mot etcd hålls**: per-sida-tillstånd stannar i wrapperns
  `manifest.json` i S3; färdiga volymer finns bara i S3; historik och
  räkning (sidor per organisation, kötid) som Prometheus-counters satta
  vid slutförande — ingen databas.
- **Borttaget**: tick-loop, Lease, de fyra statusfilerna och deras
  läsare, `metrics-failed-latest.json`, thumbnails i wrapper/reconciler,
  grandfathering-guards, `exampleJob`; `devstack-*` ut ur prod-chartet
  till en dev-values-fil.
- **Kampanjer i Git oförändrat**: campaigns-repot innehåller
  `TranscriptionJob`-manifest som Argo CD applicerar (som `Project`-CR:er
  i rask); migreringsskript från dagens `campaigns/*.yaml`.
- **Statussidan** läser CR-status genom en liten skrivskyddad endpoint;
  derivationslagret från `status.json` tas bort (C-serien anpassas).
- Docs: how-it-works omskrivet kring CR:n; kontraktstester (B21) mot
  CRD-schemat i stället för JSON-filerna.

## Klart när

- [ ] En kampanj med 50 volymer körs igenom från CR till resultat i
      viewern på PoC-noden, utan att `status/*.json` skrivs.
- [ ] `kubectl get transcriptionjobs -A` visar fas och `done/failed/total`
      för varje jobb; `kubectl delete` på ett jobb avbryter dess Jobs och
      lämnar färdiga sidor i S3.
- [ ] Reconciler-paketet är under 1 000 rader inklusive controllern;
      `attempts.json`, `validation.json`, `volumes.json` förekommer inte i
      koden.
- [ ] Prod-chartet renderar utan `devstack`-objekt; dev-values ger dem.
- [ ] I15 deployar controllern, inte CronJob-reconcilern.
