---
type: Product Backlog Item
parent: 2800
title: En pipeline väljer en namngiven poddstorlek som operatören definierar i converter.yaml
---

# B105 · En pipeline väljer en namngiven poddstorlek som operatören definierar i converter.yaml

*Inget Azure-ärende ännu.*

**Story.** Som den som skriver en pipeline vill jag kunna säga att min modell
behöver ett stort kort och mer minne — utan att kunna klustrets siffror — så att
en tung modell inte kraschar av minnesbrist och en lätt inte binder ett stort kort.

## Varför det är viktigt

Varje kampanjpodd begär i dag 1 GPU, 4 CPU och 8 GiB minne med 16 GiB tak,
hårdkodat i `manifests/campaign-job.yaml:159-167`. Inget i converter.yaml eller i
pipeline-filen kan ändra det (`render.py:_scheduling` sätter bara runtime class,
node selector och tolerations). En modell som behöver 32 GiB eller ett visst kort
går inte att köra; en liten modell reserverar lika mycket som en stor.

## Vad som levereras

- `sizes` i converter.yaml: operatören definierar namngivna storlekar, var och en
  med valfri `flavor` (från B104), antal GPU, CPU och minne (request och limit lika,
  så att `/work` i minnet aldrig tränger ut en sida):
  ```yaml
  sizes:
    small: { flavor: l4,   gpu: 1, cpu: 4, memory: 16Gi }
    large: { flavor: a100, gpu: 1, cpu: 8, memory: 32Gi }
  ```
- `size:` i pipeline-filen väljer en av dem; utan `size` gäller en default-storlek
  som motsvarar dagens värden.
- `validate` avvisar ett okänt storleksnamn och säger vilka som finns — samma
  mönster som `priority_classes`.
- `render` skriver storlekens requests/limits på wrapper-containern och flavorns
  node selector på podden, så att Kueue bara kan tilldela den flavorn.
- Storleken är en del av pipelinen: den omfattas av att en refererad pipeline är
  oföränderlig, och ALTO:ns proveniens kan säga vilken storlek som körde.
- `docs/reference/configuration.md` (converter.yaml) och `campaign-yaml.md` (pipeline-filen).

## Klart när

- [ ] En pipeline med `size: large` renderar en Job vars wrapper begär 8 CPU,
      32 GiB och 1 GPU, med a100-flavorns node selector.
- [ ] `size: huge` som inte finns i converter.yaml avvisas av `validate` med
      en mening som räknar upp storlekarna.
- [ ] En pipeline utan `size` renderar exakt som i dag.
- [ ] Att ändra `size` på en pipeline som en kampanj redan refererar avvisas
      som vilken annan ändring av pipelinen.

Relaterat: B104 (flavors med egen kvot), B84 (apply varnar när `window` inte
ryms i kvoten — räkningen blir per storlek).
