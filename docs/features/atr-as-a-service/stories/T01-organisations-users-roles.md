---
type: Product Backlog Item
id: 2959
parent: 2831
title: Organisationer registrerar sig och styr sina användare, roller och API-nycklar
---

# T01 · Organisationer registrerar sig och styr sina användare, roller och API-nycklar

**Story.** Som administratör hos en myndighet eller ett lärosäte vill jag
registrera min organisation en gång, lägga till kollegor med rätt roll och
utfärda API-nycklar, så att vi kan använda tjänsten som organisation —
inte som privatpersoner — och se vår kvot, förbrukning och pågående jobb
samlat.

## Varför det är viktigt

Tjänsten riktar sig till organisationer; registrering, avtal (T02), kvoter
(T05) och nyckeltal (T14) hänger alla på att *organisationen* är
grundenheten. htrflow-batch har i dag inga användare alls: arbetet
definieras i ett campaigns-repo av Riksarkivets egna data scientists.
Valet av inloggningslösning (SWAMID/eduGAIN, Sambi eller egen
kontohantering) är en öppen fråga inför RFI och får inte låsas av
implementationen.

## Vad som levereras

- Datamodell och admin-API för organisation, användare, roller
  (administratör, får skicka jobb, enbart läsrätt) och API-nycklar med
  utgångsdatum och återkallelse.
- Autentisering bakom ett utbytbart identitetslager: OIDC mot valfri IdP
  (Keycloak i dev) så att SWAMID/Sambi kan kopplas in utan omskrivning;
  API-nycklar som alternativ för integrationer.
- Auktorisation per roll på varje API-operation (T03).
- Organisationens vy: kvot, förbrukning innevarande månad, pågående och
  avslutade jobb.

## Klart när

- [ ] En ny organisation kan registreras, en administratör lägga till en
      användare med läsrätt, och den användaren kan se men inte skicka jobb.
- [ ] En återkallad API-nyckel avvisas inom en minut.
- [ ] Bytet av IdP från Keycloak till en annan OIDC-utgivare är en
      konfigurationsändring, inte en kodändring.
