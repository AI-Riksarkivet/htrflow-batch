---
type: Product Backlog Item
id: 3139
parent: 2800
title: publicResultsBase heter resultsUrl — ett namn för adressen webbläsaren hämtar resultat från
---

# B109 · publicResultsBase heter resultsUrl — ett namn för adressen webbläsaren hämtar resultat från

**Story.** Som operatör vill jag att adressen som resultat-länkarna pekar på har
ett tydligt namn, och att det gamla namnet avvisas med en mening som säger det
nya, så att en uppgradering inte tyst lämnar länkar som pekar fel.

## Vad som levereras

- Chart-värdet `publicResultsBase` heter `resultsUrl` (chart 0.14.0); det gamla
  nyckelnamnet avvisas med en mening som nämner det nya.
- Converterns `results_url` på samma sätt.

## Klart när

- [x] Det gamla namnet avvisas i chart och converter med det nya namnet i felet.
- [x] DEV-klustrets värden använder `resultsUrl`.
