---
type: Product Backlog Item
id: 2973
parent: 2831
title: Tjänsten kan återskapas i en annan miljö inom fem arbetsdagar
---

# T15 · Tjänsten kan återskapas i en annan miljö inom fem arbetsdagar

**Story.** Som Riksarkivet vill jag kunna sätta upp tjänsten hos en annan
leverantör eller i egen miljö inom `[5]` arbetsdagar från publicerade
artefakter och dokumentation, så att vi inte är beroende av en enskild
driftleverantör och kan avveckla utan att förlora tjänsten.

## Varför det är viktigt

Kontinuitetskravet och leverantörsoberoendet är arkitekturprinciper i
briefen. Chart, GitOps och promotion finns (B03, B12, B34), men
"återskapa på fem dagar" är ett påstående tills det har övats. Detsamma
gäller att en extern organisation kan sätta upp motsvarande tjänst — ett
av framgångsmåtten.

## Vad som levereras

- En återuppsättningsrunbook: förutsättningar (Kubernetes, GPU, S3,
  IdP), ordning, values att fylla i, verifiering — utan steg som bara
  finns i någons huvud.
- Alla artefakter publicerade och versionerade: chart, images med
  provenance, modeller som OCI (B35), UI, OpenAPI.
- En genomförd övning: tjänsten uppsatt i en ren miljö (t.ex. lokal k3s
  eller ett tomt namespace) av någon som inte byggde den, tid loggad.

## Klart när

- [ ] Övningen tog högst `[5]` arbetsdagar och ett testjobb gick igenom i
      den nya miljön.
- [ ] Runbooken är den enda källan som användes; avvikelser är rättade i
      den.
