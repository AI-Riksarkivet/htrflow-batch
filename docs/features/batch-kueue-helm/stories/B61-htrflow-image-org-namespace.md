---
type: Product Backlog Item
id: 2951
parent: 2800
title: htrflow-imagen publiceras under riksarkivet/ på Docker Hub, inte airiksarkivet/
---

# B61 · htrflow-imagen publiceras under `riksarkivet/` på Docker Hub, inte `airiksarkivet/`

**Story.** Som ägare av våra images vill jag att `htrflow`-basimagen — den
som batch-wrappern, Coder-workspaces och externa användare bygger på —
publiceras under organisationens namespace `riksarkivet/` på Docker Hub,
så att alla våra images hittas på ett ställe, ägs av organisationen och
inte av ett enskilt användarkonto, och så att policyn "bara våra images"
(B13, B37) kan uttryckas som ett enda prefix.

## Varför det är viktigt

`airiksarkivet/` är ett användarkonto på Docker Hub. Där ligger
`airiksarkivet/htrflow` (`v0.1.3` … `v0.2.6-35f48a7`, `latest`) tillsammans
med gamla experiment (`cuda-12-py310`, `htrflow_openmmlab`, `apache-polaris`,
`coder-workspace-*`, `mlflow`). Allt nytt — `ra-mcp`, `ra-hcp`,
`workspace-developer`, batch-systemets tre images — ligger under
organisationen `riksarkivet/`. Batch-wrappern har därmed sin bas i ett
namespace och sig själv i ett annat (`.docker/htrflow-batch.dockerfile:23`),
htrflows installationsdocs pekar på användarkontot, och Kyverno-regeln för
tillåtna registries måste lista två prefix. Ett användarkonto kan
dessutom stängas, byta lösenord eller tappa 2FA utan att organisationen
märker det.

## Vad som levereras

- htrflow-repots publish-pipeline (AI-Riksarkivet/htrflow) pushar till
  `docker.io/riksarkivet/htrflow` med samma taggformat (`vX.Y.Z-<sha>`),
  cosign-signerad och digest-pinnad som batch-imagerna (B44/B45-mönstret),
  med organisationens robot-/tokenuppgifter i CI, inte användarkontots.
- Befintliga taggar som fortfarande används (`v0.2.6-35f48a7` minst)
  kopierade till `riksarkivet/htrflow` med oförändrad digest (`crane copy`
  eller `docker buildx imagetools create`), så att pins fortsätter gälla.
- Konsumenterna omriktade och digest-pinnade mot det nya namespacet:
  batch-wrapperns `FROM` och `base.name`-label, `.env`/Makefile-defaults,
  docs (installation, local-k3s, deployment, test-log), Renovate-spårning.
- `airiksarkivet/htrflow` markerad som utgången: README på Docker Hub med
  hänvisning till `riksarkivet/htrflow`, ingen ny push dit; övriga
  experimentrepon under `airiksarkivet/` arkiverade eller borttagna efter
  beslut per repo.
- Kyverno-policyn "images bara från våra registries" och chartets
  `security.allowedImageRepos` reducerade till ett Docker Hub-prefix
  (`docker.io/riksarkivet/`) utöver Harbor.

## Klart när

- [ ] `docker pull riksarkivet/htrflow:v0.2.6-35f48a7` ger samma digest som
      `airiksarkivet/htrflow:v0.2.6-35f48a7`.
- [ ] En ny htrflow-release landar bara under `riksarkivet/htrflow`,
      signerad; `cosign verify` går igenom.
- [ ] `grep -r airiksarkivet/` i htrflow, htrflow-batch, ra-coder och ai-dev
      ger inga träffar utanför changelog/historik.
- [ ] Batch-wrappern bygger och publiceras (publish.yml) med den nya basen;
      `security.allowedImageRepos` i dev-values innehåller ett Docker
      Hub-prefix.
- [ ] Docker Hub-sidan för `airiksarkivet/htrflow` säger var imagen finns nu.
