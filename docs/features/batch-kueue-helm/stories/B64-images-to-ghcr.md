---
type: Product Backlog Item
id: 2979
parent: 2800
title: Imagerna publiceras till GHCR med workflow-identitet i stället för Docker Hub-token
---

# B64 · Imagerna publiceras till GHCR med workflow-identitet i stället för Docker Hub-token

**Story.** Som ansvarig för leveranskedjan vill jag att `publish.yml`
pushar imagerna (`htrflow-batch`, `htrflow-api` och viewer-imagen så
länge den finns) till `ghcr.io/ai-riksarkivet/*` med workflow-runets
egna kortlivade `GITHUB_TOKEN`, så att det inte finns någon stående
Docker Hub-token att skapa, rotera, läcka eller läsa ut ur repots
Secrets.

## Varför det är viktigt

Idag kräver publiceringen en långlivad Docker Hub access token
(`DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN` i GitHub Secrets). Den är knuten
till ett personligt Docker Hub-konto, går ut först när någon roterar den
för hand, fungerar från vilken maskin som helst och kan läsas ut av alla
med write-rättighet på repot via ett workflow. Läcker den kan någon pusha
som `riksarkivet/htrflow-batch` — precis det supply chain-hot som
H01–H05 (SLSA, cosign) ska skydda mot. GitHub kan autentisera sina egna
workflows mot sitt eget registry utan hemlighet: `GITHUB_TOKEN` gäller
bara det körande runet, styrs av `permissions: packages: write` och är
dött när runet är slut. Identiteten som pushar blir samma workflow som
cosign keyless redan signerar med, vilket SLSA-provenance-verifiering
förutsätter. Publika GHCR-paket kostar inget (storage, bandwidth,
Actions-minuter är fria för publika repon, inklusive arm64-runners) och
saknar Docker Hubs rate limit på anonyma pulls — vilket spelar roll när
ai-dev drar den 6 GB stora wrapper-imagen upprepade gånger.

## Vad som levereras

- `publish.yml`: `docker/login-action` mot `ghcr.io` med
  `github.actor`/`secrets.GITHUB_TOKEN`; `permissions: packages: write`
  (`id-token: write` finns redan för cosign); Docker Hub-stegen och de
  två Secrets tas bort; Trivy-scan-steget läser från GHCR.
- Imagenamnen byts till `ghcr.io/ai-riksarkivet/htrflow-batch`,
  `ghcr.io/ai-riksarkivet/htrflow-api` (och viewer-imagen om B63 Task 17
  inte hunnit pensionera den) i `.dagger/publish.go`, chart-defaults
  (`values.yaml`), `renovate.json`, docs och I15-storyn i ai-dev.
- Paketen görs publika en gång per paket (GHCR skapar dem privata, samma
  fälla som Docker Hub) och kopplas till repot så att SBOM-, SLSA- och
  cosign-attesteringarna hamnar på rätt paket.
- Kontroll att RA:s brandvägg släpper igenom `ghcr.io` och
  `pkg-containers.githubusercontent.com` från dev-klustret (Docker Hub är
  redan öppet; GHCR är inte verifierat) — utfallet dokumenteras i
  `docs/development/deployment.md`.
- Docker Hub-repona `riksarkivet/htrflow-batch|htrflow-api|uv4-viewer`
  lämnas kvar med en README-notis som pekar på GHCR; inga nya taggar
  pushas dit.

## Klart när

- [ ] En tagg-push kör `publish.yml` utan `DOCKERHUB_*`-Secrets och
      `cosign verify` mot `ghcr.io/ai-riksarkivet/htrflow-batch:<tag>`
      lyckas med workflow-identiteten som issuer/subject.
- [ ] `helm install` med chart-defaults drar imagerna anonymt från GHCR
      på PoC-klustret och i ai-dev.
- [ ] `gh secret list` visar inga Docker Hub-hemligheter; GitHub Packages
      visar paketen som publika och kopplade till repot.
