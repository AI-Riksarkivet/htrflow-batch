---
type: Product Backlog Item
id: 3054
parent: 2800
title: Warm-up-loggen går att läsa från status-sidan
---

# B103 · Warm-up-loggen går att läsa från status-sidan

**Story.** Som operatör vill jag kunna läsa warm-up-poddens logg direkt från
status-sidan, så att jag ser vad ett warm-up håller på med eller varför det
misslyckades — utan `kubectl` mot klustret.

## Varför det är viktigt

Det enda ett warm-up säger i dag är sitt termination message: en mening på
kampanjkortets warm-up-chip (B75). Det räcker för ett fel som wrappern själv
formulerar, men inte för allt annat: en nedladdning som tar timmar, ett
modell-repo som kräver ett token, en cache-disk som tar slut. Allt det står i
poddens logg, som bara den med kluster-åtkomst kan läsa — och den som väntar
på att en kampanj ska börja köra är ofta inte den personen. Read-API:t läser
redan Jobs och poddar i namespacet; loggen är samma slags läsning.

## Vad som levereras

- Read-API:t serverar warm-up-poddens logg för en pipeline, hämtad genom
  Kubernetes `pods/log`-endpoint (strömmad, med samma svar för en pod som är
  borta som för en kampanj utan Job).
- Ett enda nytt RBAC-steg: `get` på `pods/log` i webbens Role. Inget annat
  utökas, och skrivförbudet står kvar.
- Warm-up-chippet blir en länk till loggen, med samma mening som i dag som
  chippets text.
- Inget hemligt i loggen: warm-up-containern får Hub-token som env, så den
  loggar aldrig sin miljö, och svaret går genom samma redaction som
  run-loggen. Ett test som matar redaction med ett token-liknande värde.

## Klart när

- [ ] En pipelines warm-up-logg går att läsa från kampanjkortet medan
      warm-up:en kör, och efter att den blivit klar.
- [ ] Ett warm-up som misslyckats visar både sin mening och den logg som
      ledde fram till den.
- [ ] Webbens Role ger fortfarande ingen rätt att skriva Jobs eller poddar,
      verifierat av det test som redan vaktar det.
