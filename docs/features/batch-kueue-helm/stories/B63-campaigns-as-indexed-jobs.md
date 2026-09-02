---
type: Product Backlog Item
id: 2978
parent: 2800
title: Kampanjer körs som Kubernetes Indexed Jobs — reconcilern och dess statusfiler tas bort
---

# B63 · Kampanjer körs som Kubernetes Indexed Jobs — reconcilern och dess statusfiler tas bort

**Story.** Som förvaltare av batch-systemet vill jag att en kampanj är ett
vanligt Kubernetes-objekt — en Indexed Job där index *i* är volym *i* — som
Kubernetes och Kueue själva schemalägger, gör om vid fel, pausar och
rapporterar, i stället för YAML som en CronJob tolkar var femte minut och
speglar till fyra JSON-filer i en bucket, så att kodbasen krymper till
wrapper + en ren converter + ett tunt read API, och varje ATRaaS-behov
(kö per organisation, avbryt, kvot) blir Kueue-konfiguration.

## Varför det är viktigt

Reconcilern är systemets komplexitetscentrum: 2 600 rader, en 580-raders
tick-klass, Lease, `status.json`/`attempts.json`/`validation.json`/`volumes.json`
som skrivs om varje tick — en egenbyggd scheduler ovanpå en bucket som
kopierar det API-servern redan vet. Ett första försök att ersätta den med en
CRD + Go-controller (gren `b63-controller`, sju tasks) visade att även det
återuppfann något Kubernetes har: **Indexed Jobs** ger per-volym-retry
(`backoffLimitPerIndex`), permanent fel utan retry (`FailIndex` på exit 13),
progress (`completedIndexes`/`failedIndexes`), paus som bevarar klara volymer
(`suspend`) och rättvis kö med partiell admission via Kueue — allt GA i
Kubernetes 1.33 och verifierat mot officiell dokumentation 2026-09-01.

## Vad som levereras

- **Converter** (`packages/converter`, Python, ren funktion utan kluster- eller
  S3-åtkomst): `campaigns/*.yaml` + `pipelines/*.yaml` → per pipeline en
  ConfigMap `htr-pipeline-<id>` och warm-up Job; per kampanj en ConfigMap
  med volymlistan (en rad per index, ≤ 10 000, annars delad) och en
  **Indexed Job** (`completions` = antal volymer, `parallelism` = fönster,
  `backoffLimitPerIndex: 3`, `maxFailedIndexes` = alla, podFailurePolicy,
  Kueue-labels; `parallelism` klipps vid `converter.yaml: window`, ingen
  partiell admission — Kueue skriver annars om `spec.parallelism` på det
  levande jobbet och avvisar varje senare apply av samma renderade fil).
  `validate` körs i campaigns-repots
  CI; `render` committar `rendered/` som Argo CD pekar på. Kampanjer är
  append-only: ändrad volymlista avvisas, ny kampanj skapas.
- **Wrapper**: två små tillägg — `MAX_SECONDS` (tidsgräns per volym, exit 1)
  och `IMAGES` (bildlista som alternativ till IIIF-manifest; wrappern
  bygger och publicerar den syntetiska manifesten själv) — och två
  borttagningar: thumbnails och `metrics-failed-latest.json`.
  Env-/exit-kontraktet är i övrigt oförändrat.
- **Webbfronten** (`packages/web`, Python, read-only RBAC): `/api/v1/jobs`
  och `/api/v1/jobs/{ns}/{name}` som projektion av Job-status × volymlistan,
  med S3-länkar och felorsak från pod-termineringsmeddelandet. Ersätter
  `status.json`; statussidan läser detta. Samma process serverar också
  kampanjwebben på `/` och Universal Viewer på `/uv.html`, så nginx-imagen
  och `/api/`-proxyn är borta (0.4.0).
- **Chart 0.3.0/0.4.0**: `web.yaml` tillkommer (och tar NodePorten i 0.4.0);
  `viewer.yaml`, `reconciler.yaml`, `pipelines.yaml`,
  `job-example.yaml` och devstack-mallarna (→ `charts/htrflow-devstack`)
  försvinner; `legacyLayout` behåller `<pipeline>/<volym>/` för befintlig data,
  nya tenants får `<namespace>/<pipeline>/<volym>/`.
- **Borttaget**: `packages/reconciler`, CronJob, Lease, de fyra statusfilerna,
  frontendens derivationslager, Go-controllern (aldrig mergad).
- **Storleksbudget i CI** (`scripts/loc-budget.sh`): wrapper ≤ 2 000,
  converter ≤ 1 000, web ≤ 500, frontend ≤ 2 500, chart ≤ 700 rader; bara
  Python i batch-systemet.

## Klart när

- [x] En kampanj med 50 volymer körs igenom på PoC-noden från `rendered/`
      till resultat i viewern, utan att `status/*.json` skrivs.
      (`campaigns/e2e-50.yaml`, 50/50 indexes på en GPU; de enda
      `status/*.json` på bucketen är reconcilerns egna, orörda sedan
      13:30 UTC — se [E2E-loggen](../../../development/e2e-indexed-jobs.md).)
- [x] `kubectl get job <kampanj>` visar `completedIndexes`/`failedIndexes`;
      `suspend: true` mitt i körningen lämnar klara volymer klara; borttagen
      kampanjfil → Job prunad, S3 orört.
      Pausen deklareras i git (`suspend:` i kampanjfilen renderas till
      `spec.suspend`) och **verkställs av apply-steget**: Kueue äger
      `spec.suspend` för en admitterad Workload och ångrar den på sekunder,
      så `make campaigns-apply` kör `htrflow-campaigns apply` som sätter
      samma avsikt på Workloadens `spec.active` (med Argo CD: samma skript
      som PostSync-hook). Verifierat: pausen håller, tre klara index bevaras,
      API:t rapporterar `Paused`, och `suspend: false` + apply fortsätter på
      nästa index. En kampanj som skapas *redan pausad* pausas också (fix
      round 2) — men Kueue admitterar Workloaden i samma sekund som jobbet
      skapas, så en pod hinner leva ~4 s innan apply-steget hinner ingripa.
      Inget resultat skrivs, men "ingen pod startar någonsin" kräver ett
      render-tidsbeslut (se E2E-loggen, Fix round 2).
- [x] En volym med trasigt manifest hamnar i `failedIndexes` utan att stoppa
      kampanjen (exit 13 → `FailIndex`, ett enda försök); en volym över
      `MAX_SECONDS` görs om — och återupptar från redan publicerade sidor,
      så 60 sidor blev klara på tredje försöket. `max_seconds` sätts numera
      per pipeline (`pipelines/<id>.yaml`) och faller tillbaka på
      `converter.yaml`.
- [x] `packages/reconciler` finns inte; `grep -r status.json` ger noll träffar
      utanför historik; alla fem budgetrader gröna
      (`scripts/loc-budget.sh`: wrapper 1942/1950, converter 924/1000,
      api 390/400, frontend 2286/2500, chart 613/700).
- [ ] I15 deployar chart 0.3.0 i dev, inte CronJob-reconcilern.
