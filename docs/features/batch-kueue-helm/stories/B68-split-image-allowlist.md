---
type: Product Backlog Item
id:
parent: 2800
title: Plattformens egna imagar och pipelinernas imagar har varsin allow-list
---

# B68 · Plattformens egna imagar och pipelinernas imagar har varsin allow-list

**Story.** Som säkerhetsansvarig vill jag att listan över tillåtna
image-registry för kampanjernas pipelines är skild från listan över de
imagar plattformen själv kör (S3-lagring, hjälpcontainrar), så att den som
granskar en kampanj ser exakt en kort lista — "härifrån får HTR-imagar
komma" — och inte behöver släppa in plattformens leverantörer i samma
regel.

## Varför det är viktigt

Kyverno-policyn `images-allowed` (B63 Task 22) gäller hela namespace:t med
avsikt: en etikett-avgränsad regel vore något en kampanj kunde välja bort.
Priset är att `security.allowedImageRepos` i dag måste innehålla både
`docker.io/riksarkivet/` (pipelinernas wrapper-image) och sådant som
devstackens `rustfs/` och `docker.io/amazon/aws-cli` — annars stoppas
plattformens egna poddar. Den listan är fel abstraktion för en granskare:
den blandar "våra HTR-imagar" med "det plattformen råkar bestå av". Två
listor gör kampanjregeln skarp och plattformsregeln till chartets eget
ansvar.

## Vad som levereras

- Chart-värdena `security.allowedImageRepos` (pipelines) och ett nytt
  `security.platformImageRepos` (plattformens egna imagar, med chartets
  defaults ifyllda), och policyn renderad så att kampanjernas Jobs prövas
  mot den första och allt annat i namespace:t mot den andra.
- Kampanjrepots CI (`render.yml` i `examples/campaigns`) kör Kyverno CLI
  med enbart pipeline-listan.
- Dokumentation: `docs/how-it-works/campaigns.md` (policy-avsnittet), `docs/reference/chart.md`,
  `docs/reference/configuration.md` (genererad), quickstart-värdena.

## Klart när

- [ ] På PoC-klustret innehåller pipeline-listan bara registryt för
      wrapper-imagen, plattformens poddar startar ändå, och en kampanj med
      en image från plattformslistan avvisas.
- [ ] Kyverno CLI i kampanjrepots CI ger samma utslag som klustret för
      båda fallen.
- [ ] `helm template` misslyckas med en mening om någon av listorna är
      tom när policyn är på.
