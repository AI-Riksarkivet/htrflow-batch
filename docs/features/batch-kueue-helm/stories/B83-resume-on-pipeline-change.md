---
type: Product Backlog Item
id: 3016
parent: 2800
title: Resume räknar om sidor när pipeline_sha256 eller image_digest ändrats
---

# B83 · Resume räknar om sidor när `pipeline_sha256` eller `image_digest` ändrats

**Story.** Som den som läser en volyms `manifest.json` vill jag att den pipeline
och image den namnger verkligen producerade sidorna, så att proveniensen inte tyst
blir en efterhandskonstruktion vid ett resume.

## Varför det är viktigt

`_changed_sources` (`main.py:386-402`) jämför bara den redigerade bild-URL:en mot
`page_sources`; `pipeline_sha256` och `image_digest`, som `manifest.json` också
skriver (`publish.py:343,347`), läses aldrig tillbaka. Höj en modellrevision och
kör om: varje färdig sida behålls, medan det nya `manifest.json` påstår att den
nya sha:n och digesten producerade allihop — och ALTO-blocket
`<Processing ID="htrflow-batch">` saknas på precis de sidorna, så påståendet går
inte att motbevisa per sida heller (X20).

## Vad som levereras

- `done` nollställs när föregående `pipeline_sha256` skiljer sig, eller när
  `image_digest` skiljer sig och inte är `"unknown"` — `guards.check_drift`s regel.
- Loggraden säger varför sidorna görs om.
- `docs/how-it-works/wrapper.md`: resume-regeln med proveniensen inräknad.

## Klart när

- [ ] En volym som körs om med ändrad `pipeline_sha256` gör om alla sidor, och
      `manifest.json` stämmer med varje ALTO-fils Processing-block.
- [ ] En omkörning utan ändringar återupptar som förut.
