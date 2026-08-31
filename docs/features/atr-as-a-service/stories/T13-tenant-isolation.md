---
type: Product Backlog Item
id: 2971
parent: 2831
title: En organisations material och resultat är åtskilda från alla andras
---

# T13 · En organisations material och resultat är åtskilda från alla andras

**Story.** Som kund vill jag att ingen annan organisation — och ingen
process som kör en annan organisations jobb — kan nå mina bilder eller
resultat, och att all behandling sker inom EU/EES, så att jag kan teckna
biträdesavtalet med gott samvete.

## Varför det är viktigt

htrflow-batch är byggt för en tenant: en bucket, ett par credentials
(B17), en kö. En tjänst med många organisationer måste isolera per
organisation i lagring, credentials, nätverk och kö, och säkerheten ska
dimensioneras som om sekretessreglerat material ändå laddas upp.

## Vad som levereras

- Per organisation: eget prefix (eller bucket) med egna kortlivade
  credentials som Job:et får vid start; ingen delad nyckel med läsrätt
  över allt.
- NetworkPolicy per jobb-namespace/klass så att ett Job bara når sin
  källa, sin lagring och modellregistret.
- Auktorisationstest i CI: användare A får 404, inte 403, på
  organisation B:s jobb; ett Job kan inte lista andra prefix.
- Dataplacering dokumenterad: regioner, leverantör, underbiträden (T02).

## Klart när

- [ ] Ett medvetet försök att läsa en annan organisations resultat via API
      och via credentials från ett Job misslyckas, med test i CI.
- [ ] En säkerhetsgranskning av isolationen är genomförd och dokumenterad
      före pilot (T18).
