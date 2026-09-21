---
type: Product Backlog Item
id: 3013
parent: 2800
title: Säkra defaults — prod-values-profil och PSA som del av installationen
---

# B80 · Säkra defaults: prod-values-profil och PSA som del av installationen

**Story.** Som den som installerar chartet i en riktig miljö vill jag ha en
värdeprofil där skyddet är på från början, så att en installation enligt
dokumentationen faktiskt upprätthåller det repot har byggt.

## Varför det är viktigt

`values.yaml:95,99,108,115,127` levererar `allowedImageRepos: []`,
`requireModelRevision: false`, `policies.enabled: false`, `psaEnforce: baseline`
och `verifyImages.enabled: false`, och PSA-labels sätts av ett Makefile-steg
utanför Helm (`Makefile:316-321`, `deploy.md:78`) — en ren Helm-installation har
alltså ingen PSA alls. Default-installationen upprätthåller ingenting av det repot
byggt. Samma familj: `network.yaml:40`:s `except`-lista saknar `169.254.0.0/16`,
så catch-allen `iiifCidrs: ["0.0.0.0/0"]` lämnar länklokala adresser nåbara från
batch-poddarna (X11, E10).

## Vad som levereras
- `values-prod.yaml` som `getting-started/deploy.md` utgår från: policies på,
  allow-list satt, `requireModelRevision: true`, `psaEnforce: restricted`,
  `verifyImages` på med rätt signeringsidentitet. (levererad 2026-09-14,
  revisionsrundan; filen ligger i chartroten, inte under `ci/`, eftersom den är
  en installationsprofil och inte en renderingsfixtur)
- `htrflow-batch.validate` gör `fail` på `policies.enabled: false` utan ett uttalat
  opt-out-värde; chartet renderar PSA-labels när det äger namespacet.
- Egress-undantaget täcker länklokala adresser.
- Egress-undantaget täcker även länklokala adresser (`169.254.0.0/16`). (revision 2026-09-07, E10)

## Klart när

- [x] `helm install -f values-prod.yaml` ger policies i Enforce och
      `psaEnforce: restricted`. PSA-etiketterna sätts fortfarande av
      `make psa-labels`: Helm kan inte etikettera ett namespace det inte
      själv skapat, så det steget står kvar på installationssidan.
- [x] En installation utan opt-out och utan policies misslyckas med en mening.
      `htrflow-batch.validate` gör `fail` när `security.policies.enabled` är
      `false` och opt-out-värdet `security.policies.allowDisabled` (default
      `false`) inte är satt; meningen namnger båda. Bevis:
      `test_an_install_without_the_policies_has_to_say_so`
      (`test_chart_render.py`), och renderingen `no-policies` med
      `mustFail` i `.dagger/checks.go` samt motsvarande steg i
      `make helm-template`. Dev-installationer sätter opt-out-värdet
      uttryckligen (`try-it.md`, `deploy.md`, `chart.md`). (2026-09-21)
