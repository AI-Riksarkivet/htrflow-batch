# Föreslagna stories ur revisionen 2026-09-07

Underlag: [repository audit 2026-09-07](2026-09-07-repo-audit.md), commit
`b515bce`. **Ingenting är fixat** — det här är förslag för produktägarens genomgång
innan något går till Azure DevOps. `id:` är blank i varje story; `parent: 2800`
= Feature "Batch, Kueue, Helm", `parent: 2923` = Feature "Kampanjstatus-sidan".
Numreringen fortsätter efter B71 och C14.

## 1. Nya stories

| Fynd | Sev | Story | Rubrik |
|---|---|---|---|
| X1, X8, X35 | C | **B72** | Kampanjsplitten producerar alltid något klustret accepterar |
| X2 | C | **B73** | Wrapperns minne växer inte med antalet sidor |
| X3, X5 | H | **B74** | Ett trasigt warm-up håller inte GPU-kvoten i timmar |
| X4 | H | **B75** | Ett warm-up som inte fungerade misslyckas synligt |
| X6 | H | **B76** | En slutförd kampanj återuppstår inte när TTL städat bort Jobbet |
| X7 | H | **B77** | Ändrad pipeline stoppas i validate, inte som "field is immutable" |
| X9, X34 | H | **B78** | `rendered/` är exakt det convertern producerade — och PR-CI bevisar det |
| X10 | H | **B79** | Kyverno-policy för pod-form: kommando, volymer och secrets |
| X11 | H | **B80** | Säkra defaults: prod-values-profil och PSA som del av installationen |
| X14 | H | **B81** | CI gatar varje push, inte bara efter merge |
| X15 | H | **B82** | LOC-budgeten körs i `make ci` och varje budget får headroom |
| X20 | M | **B83** | Resume räknar om sidor när `pipeline_sha256` eller `image_digest` ändrats |
| X18 | H | **B84** | `htrflow-campaigns apply` varnar när `window` inte ryms i kvoten |
| X37 | M | **B85** | Spec, plan, story B63 och decision-loggen beskriver det som byggdes |
| X25, X27 | M | **B86** | Convertern och wrappern avvisar orimliga värden och säger vilken rad |
| X12 | H | **C15** | Läs-API:t svarar med en mening och rätt headers även när något går sönder |
| X13 | H | **C16** | Hämta bara de Pods som behövs — fieldSelector, paging och kort cache |
| X28 | M | **C17** | `Cache-Control` på HTML, `config.js` och API-svaren |

## 2. Fynd som viks in i befintliga stories

| Fynd | Sev | Story | Meningen som behöver läggas till |
|---|---|---|---|
| X16 | H | B25 | Pin-testet körs automatiskt i CI (`dagger call test-driver` i `scan-wrapper` på main-push), inte bara för hand. |
| X17 | H | B18 | Utan `WorkloadPriorityClass`-objekt och preemption i ClusterQueue är `priority:` avvisad av Kueues webhook — de hör till den här storyn. |
| X19 | H | C08 | Skriv om i Indexed-Job-termer och behåll ett kampanjnivå-facit över `failedIndexes` som överlever Jobbets TTL. |
| X38 | M | C08 | Läs-API:t amorterar polling med en kort cache i stället för en list per request och kort. |
| X21 | M | B21 | Policyerna testas som ett kontraktstest: `helm template --show-only` + `kyverno apply` mot ett godkänt och ett avvisat fixture-par. |
| X31 | M | B21 | `kube.Reader` testas mot en fejkad Kubernetes-klient som kontrollerar selectors, namespace-fan-out och 404-vägen. |
| X22 | M | B10 | Gallring och storleksvakt för resultat, körloggar och modellcache; körloggsnyckeln får namespace/kampanj-prefix; `logship` slutar re-PUT:a hela bufferten var 15 s. |
| X23 | M | B12 | ServiceAccount:en för in-cluster apply får också en NetworkPolicy till API-servern, annars är `apply.rbac.enabled` död under `network.defaultDeny`. |
| X24 | M | B66 | Migrera `Cluster` till Kueue `v1beta2` i samma vända och låt "Kueue is not installed" nämna API-versionen. |
| X26 | M | B32 | Hela CSP:n skickas som header även för `/uv.html`, inte bara via SvelteKits prerender-meta. |
| X29 | M | C14 | En saknad kampanj-ConfigMap säger det i en mening i stället för en tom tabell med ett `load more (0/N)` som inte gör något. |
| X30 | M | B67 | `HTRFLOW_NAMESPACES` och RBAC:en stämmer överens (eller listan tas bort), och `get_job` kontrollerar namespace mot konfigurationen. |
| X32 | M | B44 | En bevisad publish-körning gör `compose-test` grön; därefter kopplas den in på main-push och compose-imagerna pinnas på digest. |
| X33 | M | B11 | Kampanj-repots CI läser policyvärdena från releasen i stället för att kopiera dem, och pinnar `CONVERTER_REF` och sina actions. |
| X36 | M | B68 | PoC-quickstartens `allowedImageRepos` täcker varje image devstacken installerar (registry, device plugin). |
| X39 | M | B02 | Sista run-log-uploaden har en hård tidsbudget så att `finish()` ryms i `terminationGracePeriodSeconds`. |
| D9, D10 | L | B23 | `helm lint` ensam räcker inte (`fail`/`required` är osynliga för den), och en andra release i ett annat namespace krockar inte på `ResourceFlavor`/`ClusterQueue`. |
| E9 | L | B14 | Exempelvärdenas cosign-subject matchar hur publish faktiskt körs (branch-ref, inte tag-ref). |
| E10 | L | B80 | Egress-undantaget täcker även länklokala adresser (`169.254.0.0/16`). |
| F-6 | L | B27 | `make ci` vägrar köra på en dagger-version som inte är `dagger.json`:s. |
| F-7 | L | B82 | Budgeten räknar inte tomrader och omfattar även `.dagger/` och devstack-chartet. |
| C-7 | L | B32 | Volymraderna nycklas på `index`, inte `id`, volym-id URL-kodas i länkarna, och `AbortController` skickas in i `fetchJobs`. |
| C-7 | L | C13 | `newest(pods)` bryter lika tidsstämplar deterministiskt i stället för att visa den pod API-servern råkade lista först. |
| G10 | L | B58 | 2026-09-02-handoffarna får en statusrad och plats i navet; `reference/frontend.md` nämner `reasons.ts` och `make viewer-image` byts mot `build-web`. |
| — | L | B22 | `bun run lint` (prettier) körs i `CheckFrontend`. |
| — | L | B58 | `docs.yml` deployar på merge — skälet "medan repot är privat" gäller inte längre. |
| — | L | B57 | Runbooken får kommandon för de fyra lägen den beskriver: wedged `warmup-wait`, oadmitterbar Workload, full modellcache, bumpad pipeline-image. |
| G7, A9 | L | B86 | Avvisningarna namnger filen och den rad som skiljer, och `init`-texten säger inte längre att allow-listan bor i `converter.yaml`. |

---

## 3. Storytexterna

Varje block är en färdig story-fil: `yaml`-blocket blir front matter, resten
brödtext under `docs/features/<feature>/stories/`. Bevisen står i revisionen
under angivet X-nummer.

## B72 · Kampanjsplitten producerar alltid något klustret accepterar

```yaml
type: Product Backlog Item
id:
parent: 2800
title: Kampanjsplitten producerar alltid något klustret accepterar
```

**Story.** Som den som lägger en stor kampanj i campaigns-repot vill jag att
`render` bara producerar objekt klustret kan skapa, så att en kampanj aldrig
commitas till git och sedan avvisas av API-servern.

### Varför det är viktigt

`render.split` delar på antal volymer (`render.py:17,39-42`) och hela listan
hamnar i en ConfigMap-nyckel (`:112`); ingenting mäter bytes. En `images:`-volym
är **en** rad med komma-joinade URL:er (`models.py:133-137`), så 300 sidor à 90
tecken ger 23 115 B per rad: 45 volymer spränger 1 MiB och 200 renderar 4,41 MiB
i en enda del. `validate` och `render` går igenom, CI commitar, och först `apply`
faller. Samma kodväg har två till: ett 58 tecken långt namn plus `-part1` blir en
label-value över 63 tecken, och `foo-part1.yaml` bredvid ett `foo.yaml` som delas
krockar på fil-, Job- och ConfigMap-namn (X1, X8, X35).

### Vad som levereras

- `split` delar på både antal och ackumulerade bytes (≈900 KiB per del).
- Namnet kapas till 63 − `len("-partN")` vid split; `tests/test_render.py:198-208`
  rättas i samma commit.
- `validate` avvisar filnamn som slutar på `-part\d+$` med en mening.

### Klart när

- [ ] 200 `images:`-volymer à 300 sidor ger delar under 1 MiB som
      `kubectl apply --dry-run=server` accepterar.
- [ ] Ett 58-teckens kampanjnamn som delas ger objekt API-servern accepterar,
      och `foo-part1.yaml` avvisas i `validate`.

## B73 · Wrapperns minne växer inte med antalet sidor

```yaml
type: Product Backlog Item
id:
parent: 2800
title: Wrapperns minne växer inte med antalet sidor
```

**Story.** Som operatör vill jag att en volym med tusentals sidor använder lika
mycket minne som en med tio, så att en lång volym slutar med publicerade resultat
i stället för en OOMKill som ingen ser.

### Varför det är viktigt

`stream.consume` unlinkar bara den nedladdade bilden (`stream.py:196-202`), aldrig
`files["alto"]`/`files["page"]`, och `main.py:198-200` tog bort städningen av
workdir — som är `emptyDir {medium: Memory, sizeLimit: 2Gi}`, alltså minne. Prob:
20 sidor lämnade 40 filer / 8 000 280 B, ≈300 KB per sida: 2 Gi runt 7 000 sidor,
inom 6-timmarsdeadlinen. Ovanpå det håller htrflows modulglobala `progress`-register
varje `Document` (`htrflow/progress.py:19-21`), eftersom wrappern kör en långlivad
`Pipeline` över hela volymen. Slutet är OOMKill eller en eviction som bär
`DisruptionTarget` och sväljs av `Ignore`-regeln: försöket räknas inte och indexet
görs om med en GPU varje gång (X2). `memory-budget.md:6,13` påstår motsatsen.

### Vad som levereras

- Varje `files[fmt]` unlinkas efter lyckad `upload()`; `publish.alto_dims` har
  redan sin fallback till `store.get_bytes`.
- `driver.process_page` släpper dokumentet ur htrflows `progress`-register.
- `docs/how-it-works/memory-budget.md` skrivs om med den uppmätta siffran.

### Klart när

- [ ] En körning på 2 000 sidor visar konstant tmpfs-användning och konstant RSS.
- [ ] Ett test räknar filerna i workdir efter N sidor och kräver att bara den
      pågående sidans filer finns kvar.

## B74 · Ett trasigt warm-up håller inte GPU-kvoten i timmar

```yaml
type: Product Backlog Item
id:
parent: 2800
title: Ett trasigt warm-up håller inte GPU-kvoten i timmar
```

**Story.** Som operatör vill jag att en kampanj vars warm-up inte blev klar
misslyckas snabbt med en mening, i stället för att varje index står i sin init
container med en GPU allokerad tills poddens deadline går ut.

### Varför det är viktigt

Init containern renderas som `until [ -f /data/warmup/<id>.done ]; do sleep 10;
done`, utan timeout (`render.py:190-196`), och podden reserverar `nvidia.com/gpu: 1`
under hela sin livstid — init inräknad — så Kueue håller kvoten genom väntan.
Uteblir markören väntar varje index 21 600 s, avslutas med 143 och görs om tre
gånger: fyra gånger sex timmar hållen GPU per index, utan arbete och utan signal
(X3). Samma familj: `_warmup_job` (`render.py:83-99`) sätter varken
`runtimeClassName`, `nodeSelector` eller `tolerations` — som kampanj-Jobbet får
(`:198-207`) — så på ett kluster med taintade GPU-noder hamnar warm-up:en på fel
nod och markören dyker aldrig upp där den behövs (X5).

### Vad som levereras

- Väntan är tidsbegränsad (default ~900 s, ett `converter.yaml`-värde) och
  avslutas med 13; `podFailurePolicy` får `containerName: warmup-wait` →
  `FailIndex`.
- `_warmup_job` sätter `runtimeClassName`, `nodeSelector` och `tolerations` från
  samma `cfg` som kampanj-Jobbet.
- `failure-handling.md` får kommandot som visar varför markören saknas.

### Klart när

- [ ] En kampanj mot en pipeline vars warm-up misslyckats ger ett misslyckat
      index inom timeouten, med en mening om varför.
- [ ] Renderat warm-up-Job och kampanj-Job har samma `runtimeClassName`,
      `nodeSelector` och `tolerations`, verifierat av ett test.

## B75 · Ett warm-up som inte fungerade misslyckas synligt

```yaml
type: Product Backlog Item
id:
parent: 2800
title: Ett warm-up som inte fungerade misslyckas synligt
```

**Story.** Som operatör vill jag att ett warm-up som inte kunde skriva sin markör,
eller som dödades av sin deadline, syns som ett misslyckat warm-up med en mening —
inte som ett grönt Job vars kampanjer sedan står och väntar.

### Varför det är viktigt

`_write_marker` (`warmup.py:118-134`) returnerar tyst när `PIPELINE_ID` eller
`HF_HOME` saknas och loggar bara en varning vid `OSError`, medan `main` returnerar
`EXIT_OK` (`:114-115`): ett "lyckat" warm-up utan markör — exakt det läge B74
förvandlar till arton GPU-timmar — och det finns ingen warm-up-logg som säger
varför. Dessutom sätter `warmup-job.yaml:23` en terminal
`activeDeadlineSeconds: 3600` och `warmup.main` installerar ingen SIGTERM-hanterare,
till skillnad från `main.py:136-139`, så en långsam första nedladdning slutar med
ett tomt termination message — hålet `_fail` finns för att täppa till (X4).

### Vad som levereras

- Markören skrivs före lyckat-loggen och dess fel går genom `_fail`
  (permanent → 13 → `FailJob`).
- Samma SIGTERM-hanterare som batch-wrappern, som skriver
  `{"stage": "warmup", "permanent": false}`.
- Kampanjkortets warm-up-chip visar meningen (Task 28:s väg finns redan).

### Klart när

- [ ] Ett warm-up med oskrivbar markörkatalog avslutas med 13 och ett
      termination message som säger vad som inte gick att skriva.
- [ ] Ett warm-up som dödas av sin deadline lämnar ett termination message som
      syns på kampanjkortet.

## B76 · En slutförd kampanj återuppstår inte när TTL städat bort Jobbet

```yaml
type: Product Backlog Item
id:
parent: 2800
title: En slutförd kampanj återuppstår inte när TTL städat bort Jobbet
```

**Story.** Som beställare vill jag att en kampanj som är klar förblir klar, så att
nästa `apply` — en commit i campaigns-repot eller Argo CD:s self-heal — inte kör
om alla volymer och betalar hela GPU-notan igen.

### Varför det är viktigt

`campaign-job.yaml:30` hårdkodar `ttlSecondsAfterFinished: 86400` (inget värde),
och `cli.py:237-242` applyar varje renderad kampanj vid varje körning; en
apply-patch mot ett objekt som inte finns **skapar** det (`apply-rbac.yaml:11-14`).
Ett dygn efter att kampanjen blivit klar är Jobbet borta, och nästa apply
återskapar det och kör om samtliga index. Ingenting säger åt en operatör att ta
bort en färdig kampanjfil (X6).

### Vad som levereras

- TTL blir ett `converter.yaml`-värde med en lång default, och kan sättas per
  pipeline där det behövs.
- `apply` hoppar över — eller varnar tydligt om — en kampanj vars renderade Job
  saknas medan `volumes.txt` är oförändrad.
- `docs/how-it-works/campaigns.md`: en färdig kampanj tas bort ur `campaigns/`.

### Klart när

- [ ] En kampanj som körts klart och vars Job städats bort körs inte om vid nästa
      `htrflow-campaigns apply`, verifierat på PoC-klustret.
- [ ] TTL:t går att sätta i `converter.yaml` och syns i den genererade
      konfigurationsreferensen.

## B77 · Ändrad pipeline stoppas i validate, inte som "field is immutable"

```yaml
type: Product Backlog Item
id:
parent: 2800
title: Ändrad pipeline stoppas i validate, inte som "field is immutable"
```

**Story.** Som pipeline-författare vill jag få veta i `validate` att jag ändrat en
redan använd pipeline, så att jag slipper välja mellan ett warm-up som tyst aldrig
körs om och en apply som dör mitt i med webhook-prosa.

### Varför det är viktigt

Kampanjer har en immutability-guard (`cli.py:109,158-166`); pipelines har ingen —
`campaigns.md:163` kallar det "enforced by review". Ändras bara ConfigMappen är
warm-up-Jobbets manifest byte-identiskt: applyn blir en no-op, warm-up:en körs
aldrig om och markören ligger kvar, så under `HF_HUB_OFFLINE=1` saknas den nya
modellen. Ändras `image:` eller `max_seconds:` blir det i stället 422 vid apply, och
`cli.py:230-255` lägger hela loopen i ett `try`, så första felet blockerar alla
senare kampanjer. I båda fallen kör index som inte startat ett annat recept än de
färdiga, under samma pipeline-id och S3-prefix (X7).

### Vad som levereras

- `validate`/`render` avvisar en `pipeline-sha256` som skiljer sig från
  `rendered/pipelines/<id>.yaml` — samma guard och ton som för kampanjer.
- Warm-up-markören bär sha:n i sin sökväg, så ett nytt recept kräver ett nytt
  warm-up.
- `apply` rapporterar fel per objekt; `ClusterError` får en mening för 422
  immutable-field som nämner pipeline-id-regeln.

### Klart när

- [ ] En ändrad pipeline avvisas av `validate` med en mening som namnger filen och
      regeln; ett nytt pipeline-id går igenom.
- [ ] En apply där ett objekt ger 422 rapporterar det objektet och applyar resten.

## B78 · `rendered/` är exakt det convertern producerade — och PR-CI bevisar det

```yaml
type: Product Backlog Item
id:
parent: 2800
title: rendered/ är exakt det convertern producerade — och PR-CI bevisar det
```

**Story.** Som ansvarig för klustret vill jag kunna lita på att allt under
`rendered/` är precis vad `htrflow-campaigns render` skrev, så att en pull request
i campaigns-repot inte kan smyga in ett godtyckligt manifest som Argo CD applyar.

### Varför det är viktigt

`cli.py:173-174` prunar bara `rendered/pipelines` och `rendered/campaigns`, och
inte rekursivt, medan Argo CD är den dokumenterade applyern (`campaigns.md:112-114`:
"nothing applies to the cluster outside of what CI committed"). Prob: både
`rendered/evil.yaml` och `rendered/extra/evil.yaml` överlevde en ny render.
PR-halvan är blind på samma sätt: `render.yml:96` renderar till `$RUNNER_TEMP`, så
append-only-guarden utlöses först efter merge — röd workflow, ingen commit, och ett
`rendered/` som beskriver ett äldre `campaigns/` som Argo CD fortsätter applya.
Render är inte heller atomisk (X9, X34).

### Vad som levereras

- `render` skriver till en temporär katalog och flyttar in den på plats; en
  misslyckad körning lämnar `rendered/` orört.
- Prune går rekursivt över hela `rendered/` och misslyckas på varje fil den här
  renderingen inte skrev.
- Campaigns-repots PR-jobb renderar mot den incheckade `rendered/` och kör
  `git diff --exit-code`; Kyverno körs även mot det incheckade trädet.

### Klart när

- [ ] En fil som läggs till under `rendered/`, även i en underkatalog, gör nästa
      render röd.
- [ ] En PR som ändrar en kampanjs volymlista blir röd i PR-CI, inte först efter
      merge.

## B79 · Kyverno-policy för pod-form: kommando, volymer och secrets

```yaml
type: Product Backlog Item
id:
parent: 2800
title: Kyverno-policy för pod-form — kommando, volymer och secrets
```

**Story.** Som ansvarig för klustret vill jag att policyerna säger något om vad en
podd får *göra*, inte bara vilken image den kör, så att en godkänd och signerad
image inte kan användas till att läsa ut S3-nycklarna.

### Varför det är viktigt

Alla fyra ClusterPolicies (`templates/policies/*`) tittar bara på `image` och
pipeline-ConfigMappen — aldrig på `command`, `args`, `env`, `volumes` eller
`initContainers`. S3-secreten kan monteras av vilken podd som helst i namespacet
(`campaign-job.yaml:165-168`), wrapper-imagen har ett skal, och labeln
`app: htrflow-warmup` ger publik egress på 443 (`network.yaml:105-121`). Ett Job på
den godkända, signerade, digest-pinnade imagen som monterar `htr-batch-s3` och kör
`sh -c 'curl … </secrets/s3/credentials'` passerar varenda grind — vägen dit är
B78:s `rendered/` (X10).

### Vad som levereras

- En policy som pinnar podd-formen: tillåtet `command`, ingen secret-mount utanför
  wrapper-containern, egress-labeln bara på renderade warm-ups.
- Ett fixture-par (godkänt/avvisat) tillsammans med de övriga policytesterna (B21).
- `docs/development/security.md`: vad policyerna nu täcker och vad de inte gör.

### Klart när

- [ ] Ett Job på en godkänd image som monterar S3-secreten i en egen container
      avvisas av policyn i `Enforce`.
- [ ] Alla renderade objekt från `examples/campaigns` passerar oförändrade.

## B80 · Säkra defaults: prod-values-profil och PSA som del av installationen

```yaml
type: Product Backlog Item
id:
parent: 2800
title: Säkra defaults — prod-values-profil och PSA som del av installationen
```

**Story.** Som den som installerar chartet i en riktig miljö vill jag ha en
värdeprofil där skyddet är på från början, så att en installation enligt
dokumentationen faktiskt upprätthåller det repot har byggt.

### Varför det är viktigt

`values.yaml:95,99,108,115,127` levererar `allowedImageRepos: []`,
`requireModelRevision: false`, `policies.enabled: false`, `psaEnforce: baseline`
och `verifyImages.enabled: false`, och PSA-labels sätts av ett Makefile-steg
utanför Helm (`Makefile:316-321`, `deploy.md:78`) — en ren Helm-installation har
alltså ingen PSA alls. Default-installationen upprätthåller ingenting av det repot
byggt. Samma familj: `network.yaml:40`:s `except`-lista saknar `169.254.0.0/16`,
så catch-allen `iiifCidrs: ["0.0.0.0/0"]` lämnar länklokala adresser nåbara från
batch-poddarna (X11, E10).

### Vad som levereras

- `ci/prod-values.yaml` som `getting-started/deploy.md` utgår från: policies på,
  allow-list satt, `requireModelRevision: true`, `psaEnforce: restricted`.
- `htrflow-batch.validate` gör `fail` på `policies.enabled: false` utan ett uttalat
  opt-out-värde; chartet renderar PSA-labels när det äger namespacet.
- Egress-undantaget täcker länklokala adresser.

### Klart när

- [ ] `helm install -f ci/prod-values.yaml` ger ett namespace där policies är
      Enforce och PSA är `restricted`, utan extra Makefile-körning.
- [ ] En installation utan opt-out och utan policies misslyckas med en mening.

## B81 · CI gatar varje push, inte bara efter merge

```yaml
type: Product Backlog Item
id:
parent: 2800
title: CI gatar varje push, inte bara efter merge
```

**Story.** Som utvecklare vill jag att checkarna körs på det jag pushar innan det
blir main, så att ett ruff-fel inte hittas först när main redan är rött.

### Varför det är viktigt

`ci.yml:2-6` triggar på `push: branches: [main]`, `pull_request` och
`workflow_dispatch`, men `gh pr list --state all` ger en enda PR (en stängd
dependabot-bump): `pull_request`-triggern har aldrig gatat en merge, och arbetet
landar som direkta pushar till main med handstartade branch-körningar. Körning
`34108845568` föll på "Checks" efter 44 s och main var rött till `96ffc89` (X14).

### Vad som levereras

- `push:` på alla brancher — scans och arm64-bygget gatar redan på `event_name` —
  alternativt branch protection så att den befintliga `pull_request`-triggern blir
  grinden.
- `docs/development/index.md`: vilken grind som gäller och vad den kör.

### Klart när

- [ ] En push till en feature-branch startar Checks och test utan
      `workflow_dispatch`.
- [ ] Ett medvetet ruff-fel på en branch blir rött innan det når main.

## B82 · LOC-budgeten körs i `make ci` och varje budget får headroom

```yaml
type: Product Backlog Item
id:
parent: 2800
title: LOC-budgeten körs i make ci och varje budget får headroom
```

**Story.** Som utvecklare vill jag att `make ci` kör samma grindar som CI och att
budgetarna har marginal, så att "passed" lokalt betyder grönt i CI och en rättning
inte kräver att jag först tar bort en rad någon annanstans.

### Varför det är viktigt

`Makefile:75-78` kör `typecheck` + `dagger call checks` + `dagger call test`;
`ci.yml:48` lägger till en tredje grind, `scripts/loc-budget.sh`, som inget make-mål
kör. Alla fem budgetar ligger dessutom exakt på taket — 2117/2117, 1283/1283,
667/667, 3100/3100, 738/738 — så en tillagd kommentarsrad fäller bygget, och varje
rättning i den här revisionen kräver en motsvarande borttagning. Skriptet räknar
också tomrader och lämnar `.dagger/`, `scripts/`, devstack-chartet och frontendens
css/html obudgeterade (X15, F-7).

### Vad som levereras

- `scripts/loc-budget.sh` läggs till i `ci:`, och varje budget får en liten
  marginal i samma commit, motiverad där höjningarna redan bor.
- Räkningen hoppar över tomrader; `.dagger/` och devstack-chartet får budgetar.
- Spec, plan och story B63 citerar skriptet i stället för att kopiera siffror (B85).

### Klart när

- [ ] `make ci` faller på en budgetöverträdelse, med samma utdata som CI.
- [ ] Ingen budget ligger på exakt 100 % efter ändringen.

## B83 · Resume räknar om sidor när `pipeline_sha256` eller `image_digest` ändrats

```yaml
type: Product Backlog Item
id:
parent: 2800
title: Resume räknar om sidor när pipeline_sha256 eller image_digest ändrats
```

**Story.** Som den som läser en volyms `manifest.json` vill jag att den pipeline
och image den namnger verkligen producerade sidorna, så att proveniensen inte tyst
blir en efterhandskonstruktion vid ett resume.

### Varför det är viktigt

`_changed_sources` (`main.py:386-402`) jämför bara den redigerade bild-URL:en mot
`page_sources`; `pipeline_sha256` och `image_digest`, som `manifest.json` också
skriver (`publish.py:343,347`), läses aldrig tillbaka. Höj en modellrevision och
kör om: varje färdig sida behålls, medan det nya `manifest.json` påstår att den
nya sha:n och digesten producerade allihop — och ALTO-blocket
`<Processing ID="htrflow-batch">` saknas på precis de sidorna, så påståendet går
inte att motbevisa per sida heller (X20).

### Vad som levereras

- `done` nollställs när föregående `pipeline_sha256` skiljer sig, eller när
  `image_digest` skiljer sig och inte är `"unknown"` — `guards.check_drift`s regel.
- Loggraden säger varför sidorna görs om.
- `docs/how-it-works/wrapper.md`: resume-regeln med proveniensen inräknad.

### Klart när

- [ ] En volym som körs om med ändrad `pipeline_sha256` gör om alla sidor, och
      `manifest.json` stämmer med varje ALTO-fils Processing-block.
- [ ] En omkörning utan ändringar återupptar som förut.

## B84 · `htrflow-campaigns apply` varnar när `window` inte ryms i kvoten

```yaml
type: Product Backlog Item
id:
parent: 2800
title: htrflow-campaigns apply varnar när window inte ryms i kvoten
```

**Story.** Som den som applyar sin första kampanj vill jag få veta direkt att den
aldrig kommer att admitteras, i stället för att se "Queued" i evighet.

### Varför det är viktigt

Chartets kvot är cpu 4 / minne 8Gi / `nvidia.com/gpu` 1 — en podd
(`values.yaml:57-62`) — medan converterns default-`window` är 20
(`models.py:265`) och partial admission är medvetet borttaget
(`render.py:126-130`). En första kampanj på default-värden renderar
`parallelism: 20` = 80 CPU / 20 GPU och blir oadmitterbar för alltid, vilket syns
som "Queued" och ingenting annat. `test_chart_agreement.py` jämför namn, inte
aritmetik (X18).

### Vad som levereras

- `apply` läser ClusterQueue:ns `nominalQuota` — den talar redan med API-servern —
  och varnar när `parallelism × per-podd-request` överstiger den, med en mening som
  säger vilket värde som ska ändras.
- `docs/reference/campaign-yaml.md` och `chart.md`: relationen mellan `window` och
  kvoten, på ett ställe.

### Klart när

- [ ] En kampanj med `window: 20` mot default-kvoten ger en varning vid apply som
      namnger både kvoten och det renderade `parallelism`.
- [ ] En kampanj som ryms applyar tyst som förut.

## B85 · Spec, plan, story B63 och decision-loggen beskriver det som byggdes

```yaml
type: Product Backlog Item
id:
parent: 2800
title: Spec, plan, story B63 och decision-loggen beskriver det som byggdes
```

**Story.** Som den som läser projektets skriftliga historik vill jag att den
beskriver systemet som det blev, så att nästa agent eller kollega inte återinför
en form som redan är förkastad.

### Varför det är viktigt

Spec-beslut D6 `:36` ger wrappern `MAX_SECONDS` (borta sedan Task 25), D8 `:38`
namnger `packages/api` "proxied by the viewer nginx" (det är `packages/web`), D9
`:39` och §5 `:75` namnger `legacyLayout` (borta sedan Task 15), och §5 säger chart
0.3.0 mot `Chart.yaml:7` = 0.6.0. Planens Global Constraints `:16` kräver
annotationen `job-min-parallelism: "1"` som spec D1 `:31` avvisar. `decision-log.md`
— sidan som kallar sig "index into everything else" — slutar vid D20 (2026-07-29):
septemberbesluten står bara i story B67, `open-items.md:16` och `evolution.md:99`,
och D13 står som "proposed, open" trots att `render.py:31,142-143` renderar den
(X37).

### Vad som levereras

- En supersessionstabell i specen (D6, D8, D9, §5) med task-referens per rad, och
  planens Global Constraints i takt med D1 och det renderade Job-skelettet.
- D21–D24 i decision-loggen (modeller aldrig i imagen, alla loggar in, policy är
  Kyvernos, ingen CRD/controller); D13 rättas till byggd.
- B63:s leveranstext uppdateras; specens öppna D11 hamnar i `open-items.md`.

### Klart när

- [ ] Ingen spec-, plan- eller storytext nämner `MAX_SECONDS`, `legacyLayout`,
      `packages/api`, `job-min-parallelism` eller chart 0.3.0.
- [ ] Decision-loggen svarar på vad som beslutades i september 2026, med datum.

## B86 · Convertern och wrappern avvisar orimliga värden och säger vilken rad

```yaml
type: Product Backlog Item
id:
parent: 2800
title: Convertern och wrappern avvisar orimliga värden och säger vilken rad
```

**Story.** Som kampanjförfattare vill jag att orimliga indata stoppas där de läses,
med en mening som pekar på raden, i stället för att bli ett tomt Job eller en volym
som aldrig körs.

### Varför det är viktigt

`_http_url` (`models.py:67-70`) validerar med `urlsplit`, som strippar `\n\r\t`,
medan `source_line()` skriver ut originalsträngen: en manifest-URL med nyrad och tab
renderade ett tresidigt `volumes.txt` medan `spec.completions` stannade på 2 — sista
volymen körs aldrig, Jobbet blir grönt, och ett index hämtar en främmande URL med ett
ovaliderat `VOLUME_REF` (X25). En tom `volumes:` ger `completions: 0` och
`parallelism: 20`, accepterat av allt (X27). Wrapperns fyra numeriska gränser
(`config.py:48,53,57,58`) saknar `gt=0`, så `MAX_IMAGE_WIDTH=0` hämtar tyst
originalupplösning. Avvisningarna kan också bli bättre: `cli.py:165` namnger varken
fil eller rad, och `cli.py:25` pekar på `allowed_image_repos` som chartet äger.

### Vad som levereras

- Kontrolltecken avvisas i `manifest`/`images`; en tom `volumes:` avvisas i
  `validate` och `parallelism` klampas till `completions`.
- `gt=0`/`ge=0` på wrapperns fyra numeriska gränser.
- Append-only-meningen namnger filen och raden som skiljer; `init`-texten säger
  inte längre att allow-listan bor i `converter.yaml`.

### Klart när

- [ ] En URL med nyrad eller tab avvisas i `validate` med filnamn och rad, och en
      kampanj utan volymer avvisas.
- [ ] `MAX_IMAGE_WIDTH=0` ger ett permanent konfigurationsfel i stället för
      fullupplösta bilder.

## C15 · Läs-API:t svarar med en mening och rätt headers även när något går sönder

```yaml
type: Product Backlog Item
id:
parent: 2923
title: Läs-API:t svarar med en mening och rätt headers även när något går sönder
```

**Story.** Som läsare av statussidan vill jag att ett oväntat fel ger en mening som
säger vad som hänt, och att svaret bär samma säkerhetsheaders som alla andra — inte
ett naket 500 utan skydd.

### Varför det är viktigt

`parse_index_ranges` (`projection.py:20-36`) och `_pod_completion_index` (`:203`)
kör `int()` på label- och statussträngar utan skydd: en Pod med
`job-completion-index: NaN` gör `GET /api/v1/jobs/ns/j` till ett naket `500` i
klartext, och Kubernetes trunkerar dessutom `completedIndexes`/`failedIndexes` vid
stor skala, vilket fäller sidan på samma sätt. Värre: svaret bär då ingen av
`SECURITY_HEADERS` — middlewaren (`app.py:92-96`) når aldrig
`response.headers.update` när anropet kastar (prob: `headers: {}`). Läsaren får
"Can't reach the campaign service right now (HTTP 500)" för en databugg (X12).

### Vad som levereras

- En exception handler som svarar 502/503 med en mening och som applicerar
  `SECURITY_HEADERS`; headers sätts även på felsvar.
- `parse_index_ranges` och `_pod_completion_index` tål ett icke-heltal och ett
  trunkerat intervall utan att kasta.
- `frontend/src/lib/reasons.ts` får meningen för det här läget.

### Klart när

- [ ] En Pod med ogiltigt `job-completion-index` ger ett läsbart svar, inte ett
      500, och sidan visar resten av kampanjen.
- [ ] Varje felsvar från `/api/v1/*` bär `SECURITY_HEADERS`, verifierat av test.

## C16 · Hämta bara de Pods som behövs — fieldSelector, paging och kort cache

```yaml
type: Product Backlog Item
id:
parent: 2923
title: Hämta bara de Pods som behövs — fieldSelector, paging och kort cache
```

**Story.** Som läsare av en stor kampanj vill jag att kortet uppdateras utan att
API:t hämtar tiotusentals Pod-objekt varje minut, så att statussidan går att ha
öppen medan arkivet körs.

### Varför det är viktigt

`kube.py:112-119` listar Pods på `batch.kubernetes.io/job-name` utan
`field_selector`, utan `limit` och utan continue-token, och avkodar varje komplett
Pod-objekt till minne. Poddarna ligger kvar (`restartPolicy: Never`,
`backoffLimitPerIndex: 3`, TTL 24 h), så en kampanj med 10 000 volymer lämnar
10 000–40 000 Pods vid liv under körningen plus ett dygn. `app.py:132` gör anropet
vid varje detaljförfrågan, plus hela kampanj-ConfigMappen (630 KB) och en färsk
warm-up-lista. Projektionen är snabb (10 000 rader på 0,03 s) — kostnaden är
rundturerna (X13, X38).

### Vad som levereras

- `list_pods` tar `field_selector` och pagar; detaljvyn hämtar bara Pods för aktiva
  och misslyckade index.
- En kort TTL-cache (eller en informer) bakom `Reader` för Job- och
  warm-up-listorna.
- `docs/reference/frontend.md`: vad en pollning faktiskt kostar.

### Klart när

- [ ] En detaljförfrågan mot en kampanj med 10 000 index gör ett begränsat, pagat
      antal Pod-anrop — mätt, inte uppskattat.
- [ ] Två öppna kort i två flikar ger inte fyra warm-up-listningar per minut.

## C17 · `Cache-Control` på HTML, `config.js` och API-svaren

```yaml
type: Product Backlog Item
id:
parent: 2923
title: Cache-Control på HTML, config.js och API-svaren
```

**Story.** Som operatör vill jag att en ny deploy syns i webbläsaren direkt, så att
ingen läser gammal HTML eller pekar mot en gammal `API_BASE` i dagar utan att veta
om det.

### Varför det är viktigt

Prob: `/`, `/config.js`, `/log` och `/api/v1/jobs` svarar med `ETag` och
`Last-Modified` men utan `Cache-Control` (`app.py:31-35,164-166`). Webbläsare
tillämpar då heuristisk färskhet utifrån `Last-Modified` — här imagens byggtid — så
en redeploy kan lämna både HTML-skalet och `/config.js` (deploy-hooken som sätter
`window.API_BASE`) inaktuella i dagar, medan de innehållshashade filerna under
`_app/immutable/*` inte får `immutable` och hämtas i onödan (X28).

### Vad som levereras

- `Cache-Control: no-cache` på HTML-skalet, `/config.js` och `/api/v1/*`;
  `public, max-age=31536000, immutable` på `_app/immutable/*`.
- Test som pinnar headern per rutt.

### Klart när

- [ ] En redeploy med ny `API_BASE` slår igenom vid nästa laddning utan hård
      omladdning.
- [ ] `_app/immutable/*` hämtas inte om mellan två laddningar.
