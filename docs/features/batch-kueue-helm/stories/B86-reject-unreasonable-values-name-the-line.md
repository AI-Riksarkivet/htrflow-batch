---
type: Product Backlog Item
id: 3019
parent: 2800
title: Convertern och wrappern avvisar orimliga värden och säger vilken rad
---

# B86 · Convertern och wrappern avvisar orimliga värden och säger vilken rad

**Story.** Som kampanjförfattare vill jag att orimliga indata stoppas där de läses,
med en mening som pekar på raden, i stället för att bli ett tomt Job eller en volym
som aldrig körs.

## Varför det är viktigt

`_http_url` (`models.py:67-70`) validerar med `urlsplit`, som strippar `\n\r\t`,
medan `source_line()` skriver ut originalsträngen: en manifest-URL med nyrad och tab
renderade ett tresidigt `volumes.txt` medan `spec.completions` stannade på 2 — sista
volymen körs aldrig, Jobbet blir grönt, och ett index hämtar en främmande URL med ett
ovaliderat `VOLUME_REF` (X25). En tom `volumes:` ger `completions: 0` och
`parallelism: 20`, accepterat av allt (X27). Wrapperns fyra numeriska gränser
(`config.py:48,53,57,58`) saknar `gt=0`, så `MAX_IMAGE_WIDTH=0` hämtar tyst
originalupplösning. Avvisningarna kan också bli bättre: `cli.py:165` namnger varken
fil eller rad, och `cli.py:25` pekar på `allowed_image_repos` som chartet äger.

## Vad som levereras
- Kontrolltecken avvisas i `manifest`/`images`; en tom `volumes:` avvisas i
  `validate` och `parallelism` klampas till `completions`.
- `gt=0`/`ge=0` på wrapperns fyra numeriska gränser.
- Append-only-meningen namnger filen och raden som skiljer; `init`-texten säger
  inte längre att allow-listan bor i `converter.yaml`.
- Avvisningarna namnger filen och den rad som skiljer, och `init`-texten säger inte längre att allow-listan bor i `converter.yaml`. (revision 2026-09-07, G7, A9)
- En `images:`-volym vars URL-lista inte ryms i en enda miljövariabel (Linux tillåter 128 KiB per argument; 2 000 URL:er à 100 tecken är 200 KB) avvisas i `validate` med en mening som namnger volymen och vägen ut (dela volymen eller använd ett IIIF-manifest) — i dag dör podden med "Argument list too long" innan wrappern startar. (live-körning 2026-09-08)

## Klart när

- [ ] En URL med nyrad eller tab avvisas i `validate` med filnamn och rad, och en
      kampanj utan volymer avvisas.
- [ ] `MAX_IMAGE_WIDTH=0` ger ett permanent konfigurationsfel i stället för
      fullupplösta bilder.
