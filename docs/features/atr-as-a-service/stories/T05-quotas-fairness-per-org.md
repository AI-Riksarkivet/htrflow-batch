---
type: Product Backlog Item
id: 2963
parent: 2831
title: Kön fördelas rättvist mellan organisationer med kvoter och en klass för små jobb
---

# T05 · Kön fördelas rättvist mellan organisationer med kvoter och en klass för små jobb

**Story.** Som Riksarkivet vill jag att GPU-kapaciteten fördelas rättvist
mellan organisationer, att varje organisation har en månadskvot och att
små jobb (under `[20]` sidor) går i en egen snabb klass, så att en enda
stor körning aldrig blockerar övriga och en ny användare kan utvärdera
tjänsten samma dag.

## Varför det är viktigt

Tjänsten är avgiftsfri: ingen prissignal dämpar efterfrågan, så kön och
kvoterna är det enda styrmedlet och måste utformas som policy. Kueue finns
redan (B02) men med en kö för hela systemet; B18 ger prioritet för
brådskande volymer internt, inte rättvisa mellan tenants.

## Vad som levereras

- En Kueue `ClusterQueue` per organisation i en gemensam cohort med
  nominell kvot, lån och återtag (preemption inom cohort), så att ledig
  kapacitet används men varje organisation alltid får sin andel.
- Månadskvot i sidor per organisation (`[N]`), räknad vid inlämning;
  överskridande avvisas med besked; ansökan om höjning som flöde.
- Prioritetsklass för små jobb med egen reserverad kapacitet.
- Kvot, förbrukning och köläge i API (T03) och UI (T07).

## Klart när

- [ ] Två organisationer som köar samtidigt får båda jobb körda i
      proportion till sina kvoter; ett 10 000-sidorsjobb stoppar inte ett
      15-sidorsjobb från en annan organisation mer än några minuter.
- [ ] Ett jobb över månadskvoten avvisas vid inlämning med kvarvarande
      kvot i svaret.
- [ ] Kvotnivåerna ligger i values, inte i kod.
