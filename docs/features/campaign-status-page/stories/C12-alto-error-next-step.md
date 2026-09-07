---
type: Product Backlog Item
id: 3002
parent: 2923
title: Viewerns felmeddelanden för ALTO säger vad man gör härnäst
---

# C12 · Viewerns felmeddelanden för ALTO säger vad man gör härnäst

**Story.** Som den som öppnar `/alto` med en länk som inte fungerar vill
jag få ett meddelande som säger vad som var fel *och* vad jag kan göra åt
det, så att jag inte behöver gissa om felet sitter i länken, i filen eller
i tjänsten.

## Varför det är viktigt

Regeln i hela htrflow-batch är att varje fel är en mening en människa kan
handla på. Två meddelanden i viewern faller utanför: "The ALTO URL must be
an absolute http(s) URL." säger vad som gäller men inte hur man rättar
länken, och "Could not load the ALTO file: HTTP 404. Check that the raw
link still works." visar en statuskod i stället för att säga att filen
inte finns på den adressen. Ändringen är ett tiotal rader i frontendens
`reasons`-modul plus ett fastnaglat test.

## Vad som levereras

- De två meddelandena omskrivna med ett nästa steg (t.ex. "Länken måste
  börja med http:// eller https:// — kopiera den från kampanjsidan igen"
  och "Det finns ingen ALTO-fil på den adressen — öppna volymen från
  kampanjsidan så att länken blir rätt").
- Statuskoder översatta till ord för de fall som förekommer (404, 403,
  5xx, nätverksfel), koden kvar sist i parentes.
- Testet `reasons.test.ts` pinnar varje mening.

## Klart när

- [ ] Varje felväg i `/alto` ger en mening med ett nästa steg, verifierad
      i testet.
- [ ] Frontendens LOC-budget hålls exakt.
