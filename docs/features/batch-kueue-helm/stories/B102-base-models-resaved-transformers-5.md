---
type: Product Backlog Item
id: 3053
parent: 2800
title: Basmodellerna sparas om under transformers 5 så att en enda image räcker
---

# B102 · Basmodellerna sparas om under transformers 5 så att en enda image räcker

**Story.** Som operatör vill jag att basmodellerna för handskrift laddas av
den nyare transformers-linjen, så att en enda wrapper-image räcker för alla
pipelines och den äldre linjen kan pensioneras.

## Varför det är viktigt

Imagen bygger i dag på två transformers-linjer, valda med build-argumentet
`TRANSFORMERS_VERSION`, därför att modellerna inte är överens: en TrOCR-modell
som sparats av den nyare linjen går inte att läsa med den äldre (dess
tokenizer-config har nycklar den äldre inte förstår, och även när den
laddats avkodar den äldre linjen byte-level-tokenizern fel — mellanslag runt
varje icke-ASCII-tecken), medan basmodellerna, som sparats av den äldre
linjen, inte laddas alls under den nyare (positionsinbäddningens
`_float_tensor`-buffert rapporteras som UNEXPECTED och laddningen dör på
`Cannot copy out of meta tensor`). Två linjer betyder två images att bygga,
skanna, signera och publicera, och en operatör som måste veta vilken linje en
pipeline kräver innan hen pinnar en digest.

**Arbetet ligger inte i det här repot.** Det ligger i modell-repona (modellerna
sparas om) och i htrflows egen TrOCR-laddning (bufferten måste materialiseras i
stället för att lämnas på `meta`-enheten). Det här repot byter bara default när
båda delarna finns, och tar då bort den andra linjen.

## Vad som levereras

- Basmodellerna för handskrift sparade om av den nyare linjen, som en ny
  revision i sina modell-repon — en pipeline pinnar modellens revision, så
  ingen kampanj byter modell av sig själv.
- Rättningen i htrflows TrOCR-laddning rapporterad och gjord uppströms, så
  att en modell som sparats av den äldre linjen inte heller kräver ett hand-
  redigerat repo.
- Verifiering: warm-up och en volym per pipeline som används, körd på båda
  linjerna, med ALTO-texten jämförd rad för rad.
- När det håller: dockerfilens default flyttas till den nyare linjen,
  build-argumentet och den andra linjens dokumentation tas bort, och
  beslutsloggens ruling uppdateras.

## Klart när

- [ ] Basmodellerna laddas i en wrapper-image på den nyare linjen, utan
      hand-redigerad tokenizer-config.
- [ ] En volym ger samma text på båda linjerna, verifierat av en diff.
- [ ] Den äldre linjen behövs inte längre: defaulten är flyttad och
      `TRANSFORMERS_VERSION` är borta ur dockerfilen, Makefile, dagger-modulen
      och publish-workflowet.
