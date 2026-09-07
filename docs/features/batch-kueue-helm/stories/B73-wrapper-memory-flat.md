---
type: Product Backlog Item
id: 3006
parent: 2800
title: Wrapperns minne växer inte med antalet sidor
---

# B73 · Wrapperns minne växer inte med antalet sidor

**Story.** Som operatör vill jag att en volym med tusentals sidor använder lika
mycket minne som en med tio, så att en lång volym slutar med publicerade resultat
i stället för en OOMKill som ingen ser.

## Varför det är viktigt

`stream.consume` unlinkar bara den nedladdade bilden (`stream.py:196-202`), aldrig
`files["alto"]`/`files["page"]`, och `main.py:198-200` tog bort städningen av
workdir — som är `emptyDir {medium: Memory, sizeLimit: 2Gi}`, alltså minne. Prob:
20 sidor lämnade 40 filer / 8 000 280 B, ≈300 KB per sida: 2 Gi runt 7 000 sidor,
inom 6-timmarsdeadlinen. Ovanpå det håller htrflows modulglobala `progress`-register
varje `Document` (`htrflow/progress.py:19-21`), eftersom wrappern kör en långlivad
`Pipeline` över hela volymen. Slutet är OOMKill eller en eviction som bär
`DisruptionTarget` och sväljs av `Ignore`-regeln: försöket räknas inte och indexet
görs om med en GPU varje gång (X2). `memory-budget.md:6,13` påstår motsatsen.

## Vad som levereras

- Varje `files[fmt]` unlinkas efter lyckad `upload()`; `publish.alto_dims` har
  redan sin fallback till `store.get_bytes`.
- `driver.process_page` släpper dokumentet ur htrflows `progress`-register.
- `docs/how-it-works/memory-budget.md` skrivs om med den uppmätta siffran.

## Klart när

- [ ] En körning på 2 000 sidor visar konstant tmpfs-användning och konstant RSS.
- [ ] Ett test räknar filerna i workdir efter N sidor och kräver att bara den
      pågående sidans filer finns kvar.
