---
type: Product Backlog Item
id:
parent: 2923
title: Cache-Control på HTML, config.js och API-svaren
---

# C17 · `Cache-Control` på HTML, `config.js` och API-svaren

**Story.** Som operatör vill jag att en ny deploy syns i webbläsaren direkt, så att
ingen läser gammal HTML eller pekar mot en gammal `API_BASE` i dagar utan att veta
om det.

## Varför det är viktigt

Prob: `/`, `/config.js`, `/log` och `/api/v1/jobs` svarar med `ETag` och
`Last-Modified` men utan `Cache-Control` (`app.py:31-35,164-166`). Webbläsare
tillämpar då heuristisk färskhet utifrån `Last-Modified` — här imagens byggtid — så
en redeploy kan lämna både HTML-skalet och `/config.js` (deploy-hooken som sätter
`window.API_BASE`) inaktuella i dagar, medan de innehållshashade filerna under
`_app/immutable/*` inte får `immutable` och hämtas i onödan (X28).

## Vad som levereras

- `Cache-Control: no-cache` på HTML-skalet, `/config.js` och `/api/v1/*`;
  `public, max-age=31536000, immutable` på `_app/immutable/*`.
- Test som pinnar headern per rutt.

## Klart när

- [ ] En redeploy med ny `API_BASE` slår igenom vid nästa laddning utan hård
      omladdning.
- [ ] `_app/immutable/*` hämtas inte om mellan två laddningar.
