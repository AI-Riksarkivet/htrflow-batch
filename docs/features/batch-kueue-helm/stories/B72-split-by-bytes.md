---
type: Product Backlog Item
id:
parent: 2800
title: Kampanjsplitten producerar alltid något klustret accepterar
---

# B72 · Kampanjsplitten producerar alltid något klustret accepterar

**Story.** Som den som lägger en stor kampanj i campaigns-repot vill jag att
`render` bara producerar objekt klustret kan skapa, så att en kampanj aldrig
commitas till git och sedan avvisas av API-servern.

## Varför det är viktigt

`render.split` delar på antal volymer (`render.py:17,39-42`) och hela listan
hamnar i en ConfigMap-nyckel (`:112`); ingenting mäter bytes. En `images:`-volym
är **en** rad med komma-joinade URL:er (`models.py:133-137`), så 300 sidor à 90
tecken ger 23 115 B per rad: 45 volymer spränger 1 MiB och 200 renderar 4,41 MiB
i en enda del. `validate` och `render` går igenom, CI commitar, och först `apply`
faller. Samma kodväg har två till: ett 58 tecken långt namn plus `-part1` blir en
label-value över 63 tecken, och `foo-part1.yaml` bredvid ett `foo.yaml` som delas
krockar på fil-, Job- och ConfigMap-namn (X1, X8, X35).

## Vad som levereras

- `split` delar på både antal och ackumulerade bytes (≈900 KiB per del).
- Namnet kapas till 63 − `len("-partN")` vid split; `tests/test_render.py:198-208`
  rättas i samma commit.
- `validate` avvisar filnamn som slutar på `-part\d+$` med en mening.

## Klart när

- [ ] 200 `images:`-volymer à 300 sidor ger delar under 1 MiB som
      `kubectl apply --dry-run=server` accepterar.
- [ ] Ett 58-teckens kampanjnamn som delas ger objekt API-servern accepterar,
      och `foo-part1.yaml` avvisas i `validate`.
