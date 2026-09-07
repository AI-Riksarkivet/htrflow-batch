---
type: Product Backlog Item
id: 3018
parent: 2800
title: Spec, plan, story B63 och decision-loggen beskriver det som byggdes
---

# B85 · Spec, plan, story B63 och decision-loggen beskriver det som byggdes

**Story.** Som den som läser projektets skriftliga historik vill jag att den
beskriver systemet som det blev, så att nästa agent eller kollega inte återinför
en form som redan är förkastad.

## Varför det är viktigt

Spec-beslut D6 `:36` ger wrappern `MAX_SECONDS` (borta sedan Task 25), D8 `:38`
namnger `packages/api` "proxied by the viewer nginx" (det är `packages/web`), D9
`:39` och §5 `:75` namnger `legacyLayout` (borta sedan Task 15), och §5 säger chart
0.3.0 mot `Chart.yaml:7` = 0.6.0. Planens Global Constraints `:16` kräver
annotationen `job-min-parallelism: "1"` som spec D1 `:31` avvisar. `decision-log.md`
— sidan som kallar sig "index into everything else" — slutar vid D20 (2026-07-29):
septemberbesluten står bara i story B67, `open-items.md:16` och `evolution.md:99`,
och D13 står som "proposed, open" trots att `render.py:31,142-143` renderar den
(X37).

## Vad som levereras

- En supersessionstabell i specen (D6, D8, D9, §5) med task-referens per rad, och
  planens Global Constraints i takt med D1 och det renderade Job-skelettet.
- D21–D24 i decision-loggen (modeller aldrig i imagen, alla loggar in, policy är
  Kyvernos, ingen CRD/controller); D13 rättas till byggd.
- B63:s leveranstext uppdateras; specens öppna D11 hamnar i `open-items.md`.

## Klart när

- [ ] Ingen spec-, plan- eller storytext nämner `MAX_SECONDS`, `legacyLayout`,
      `packages/api`, `job-min-parallelism` eller chart 0.3.0.
- [ ] Decision-loggen svarar på vad som beslutades i september 2026, med datum.
