---
type: Product Backlog Item
id: 2964
parent: 2831
title: Köposition, uppskattad start och avbrytande med delresultat
---

# T06 · Köposition, uppskattad start och avbrytande med delresultat

**Story.** Som användare vill jag se var mitt jobb står i kön och ungefär när
det startar, och kunna avbryta det och ändå få de sidor som hunnit
bearbetas, så att jag kan planera och inte förlorar timmar av GPU-tid för
att jag ändrade mig.

## Varför det är viktigt

Ingen svarstid utlovas per jobb i version 1; då måste tjänsten i stället
vara ärlig om läget. Wrappern streamar redan resultat sida för sida till
lagringen (B01) och hanterar SIGTERM — delresultat vid avbrott är därför
nära, men "avbryt" finns inte som operation och köposition beräknas inte.

## Vad som levereras

- Köposition per jobb och uppskattad starttid från kölängd, genomsnittlig
  sidhastighet per pipeline och organisationens andel (T05); visas i API
  och UI, med tydlig "uppskattning".
- Avbryt-operation: jobbet tas ur kön eller Job:et termineras; redan
  färdiga sidor paketeras som delresultat med markering i manifestet (T08).
- Statusmodell dokumenterad: köad, startar, pågår (n/m sidor), avbrutet,
  klart, misslyckat, gallrat.

## Klart när

- [ ] Ett avbrutet jobb på sida 300 av 1 000 ger ett resultat med 300 sidor
      och `status: cancelled` i manifestet.
- [ ] Uppskattad starttid för tio testjobb avviker i median mindre än
      30 % från verklig start.
