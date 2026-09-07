---
type: Product Backlog Item
id:
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
- `ci/prod-values.yaml` som `getting-started/deploy.md` utgår från: policies på,
  allow-list satt, `requireModelRevision: true`, `psaEnforce: restricted`.
- `htrflow-batch.validate` gör `fail` på `policies.enabled: false` utan ett uttalat
  opt-out-värde; chartet renderar PSA-labels när det äger namespacet.
- Egress-undantaget täcker länklokala adresser.
- Egress-undantaget täcker även länklokala adresser (`169.254.0.0/16`). (revision 2026-09-07, E10)

## Klart när

- [ ] `helm install -f ci/prod-values.yaml` ger ett namespace där policies är
      Enforce och PSA är `restricted`, utan extra Makefile-körning.
- [ ] En installation utan opt-out och utan policies misslyckas med en mening.
