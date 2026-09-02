---
type: Product Backlog Item
id:
parent: 2800
title: En sida som gör retry ska inte stoppa GPU:n för sidorna bakom den (completion order i wrappern)
---

# B65 · En sida som gör retry ska inte stoppa GPU:n för sidorna bakom den (completion order i wrappern)

**Story.** Som operatör vill jag att wrappern matar GPU:n med nästa
nedladdade sida så fort den finns på disk, i stället för att vänta på en
sida som håller på med retry mot IIIF-servern, så att en långsam eller
tillfälligt trasig bild inte lämnar GPU:n sysslolös i flera minuter medan
60 färdiga sidor ligger och väntar.

## Varför det är viktigt

Wrapperns streaming-loop (`stream.fetched`, tidigare relay-tråden i
`fetch.run_downloader`) lämnar sidorna till GPU:n **i manifestets
ordning**. Det är avsiktligt: page-first-uploaden och den live-skickade
run-loggen visar sidorna i samma ordning som volymen, och `manifest.json`
är oberoende av ordningen. Baksidan hittades under B63 (Task 11): en sida
i sitt tredje retry-försök (upp till ~6 minuter med 120 s timeout per
försök) blockerar alla sidor bakom sig även när de redan är nedladdade —
head-of-line blocking. Reproduktion: två sidor, en mock-server som håller
sida 1 tills sida 2 är serverad; konsumenten får ingenting förrän sida 1
är klar. På en volym med 480 sidor och en flaky bildserver kan det kosta
tiotals minuter GPU-tid per volym.

Ändringen är liten i kod (generatorn yieldar i completion order i stället
för submission order) men är en **beteendeförändring**: sidor bearbetas
och laddas upp i den ordning de anländer, run-loggen blir oordnad, och
fyra tester i `test_main.py` som antar manifestordning måste skrivas om.
Därför togs den inte in i B63:s refaktorering (zero behaviour change) utan
lyfts hit för beslut.

## Vad som levereras

- `stream.fetched` yieldar i completion order; lookahead-gränsen (max N
  sidor på disk) och stop-on-failure är oförändrade.
- Run-loggen och page-first-uploaden accepterar oordnade sidor;
  `manifest.json` är redan sorterad på sidnamn. Frontendens run viewer
  (`/log`) visar sidor sorterade på namn, inte på ankomst.
- Tester: head-of-line-reproduktionen som ett test som ska passera
  (sida 2 bearbetas medan sida 1 gör retry); de fyra ordningskänsliga
  testerna ersätts av ordningsoberoende med samma påståenden.
- Dokumentation: `docs/how-it-works/wrapper.md` (streaming-loopens
  ordningsgaranti), `docs/reference/wrapper.md`.

## Klart när

- [ ] Ett test visar att sida 2 når GPU:n medan sida 1 fortfarande gör
      retry, och att `stall_seconds` inte ökar under tiden.
- [ ] En volym med en medvetet långsam bild (mock eller `MAX_SECONDS`-
      probe på PoC-klustret) blir klar utan att GPU:n står stilla mellan
      sidorna; `manifest.json` och `iiif.json` är identiska med en körning
      i manifestordning.
- [ ] Run viewern visar sidorna i namnordning oavsett ankomstordning.
