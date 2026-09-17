---
type: Product Backlog Item
parent: 2800
title: Flera team delar klustret — ett repo, en namespace, en LocalQueue och en egen ClusterQueue per team i en gemensam cohort
---

# B106 · Flera team delar klustret — ett repo, en namespace, en LocalQueue och en egen ClusterQueue per team i en gemensam cohort

*Inget Azure-ärende ännu.*

**Story.** Som plattformsansvarig vill jag kunna låta flera team köra kampanjer på
samma GPU-kluster, var och ett från sitt eget campaigns-repo och med en garanterad
andel av GPU:erna, så att ett team aldrig tar hela klustret och ledig kapacitet
ändå används av den som har arbete.

## Varför det är viktigt

Chartet är i dag byggt för ett team per install: ClusterQueue:ns
`namespaceSelector` släpper bara in release-namespacen
(`templates/kueue.yaml:13-19`), och det finns en LocalQueue, en S3-secret
(`values.yaml` `s3.existingSecret`) och en modellcache (`ReadWriteOnce`). Två team
på samma ClusterQueue skulle dela kvoten först till kvarn — den som köar först tar
allt.

Formen följer två saker som redan finns. Kueue ser en namespace som en tenant
och en ClusterQueue som en budget; en cohort bestämmer hur budgetar lånar av
varandra. Och vår förtroendegräns är repot: den som kan skriva i campaigns-repot
väljer image och modeller som körs med bucketens skrivrättigheter
(`docs/how-it-works/security.md`, "Trust boundary"). Ett team = ett repo = en
namespace håller granskning, credentials och historik på samma ställe.

## Vad som levereras

- `teams` i values: en lista där varje team har ett namn, en namespace, en kvot
  per flavor (B104), valfria låne- och utlåningsgränser och sin S3-secret.
- Per team renderar chartet LocalQueue, ClusterQueue (med `namespaceSelector` på
  teamets namespace och `cohort` satt), modellcache, NetworkPolicies och
  apply-RBAC. ResourceFlavors, WorkloadPriorityClasses och Kyverno-policyer
  delas av alla team.
- Webbens `HTRFLOW_NAMESPACES` fylls från `teams`, så status-sidan visar alla
  team (`packages/web/src/htrflow_web/kube.py:91` läser redan flera).
- Varje teams campaigns-repo har sin converter.yaml med sin `namespace` och `queue`;
  ingen ändring i convertern krävs. Brådska inom ett team uttrycks med `priority`,
  inte med fler köer.
- Ett install utan `teams` renderar exakt som i dag.
- `docs/how-it-works/queueing.md` och `reference/chart.md`: modellen team, repo,
  namespace, kö och cohort på ett ställe.
- Känd kostnad: modellcachen är per namespace, så varje team laddar ner sina
  modeller själv. En delad läs-cache är en uppföljning (roadmap, cache-lagret).

## Klart när

- [ ] Values med två team renderar två namespaces med var sin LocalQueue och
      ClusterQueue i samma cohort, och delade flavors och prioritetsklasser.
- [ ] När team A är tomt kör team B över sin egen kvot på A:s lediga GPU:er;
      när B:s kampanjer är klara admitteras A:s nästa kampanj inom A:s kvot.
- [ ] En Job i team A:s namespace kan inte ställa sig i team B:s kö
      (ClusterQueue:ns `namespaceSelector` avvisar den).
- [ ] Status-sidan visar båda teamens kampanjer.
- [ ] Ett install utan `teams` renderar samma objekt som i dag.

Relaterat: B104 (kvot per flavor), B105 (storlekar per pipeline), T05 (rättvisa
och månadskvot mellan organisationer bygger på detta).
