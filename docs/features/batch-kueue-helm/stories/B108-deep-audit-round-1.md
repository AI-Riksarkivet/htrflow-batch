---
type: Product Backlog Item
id: 3138
parent: 2800
title: Djupaudit omgång 1 — härdning av resultat-proxyn och en fungerande installationsväg för chart 0.16.0
---

# B108 · Djupaudit omgång 1 — härdning av resultat-proxyn och en fungerande installationsväg för chart 0.16.0

**Story.** Som plattformsansvarig vill jag att de viktiga fynden från djupauditen
av resultat-proxyn och chart 0.16.0 är åtgärdade innan releasen, så att login,
sessioner och installation håller på ett delat kluster.

## Varför det är viktigt

Resultat-proxyn är den första delen av systemet som hanterar användares
inloggningsuppgifter. Auditen hittade luckor i hur sessioner, klientadresser och
installationsstegen fungerade, och de måste vara stängda innan proxyn körs på
DEV.

## Vad som levereras

- Trusted hops för klientadressen bara när chartens ingress-uppsättning faktiskt
  har två hopp; login-begränsningen räknar den riktiga adressen.
- Sessionsnyckeln laddas om när dess Secret ändras.
- En installationsväg för chart 0.16.0 som fungerar från tom namespace.
- Kontrollen `entrypoints-published` i release-flödet.

## Klart när

- [x] Fynden i omgång 1 är åtgärdade och granskade.
- [x] Chart 0.16.0 installeras från början på DEV och login fungerar.
- [x] `make ci` grön.
