---
type: Product Backlog Item
id:
parent: 2800
title: Ändrad pipeline stoppas i validate, inte som "field is immutable"
---

# B77 · Ändrad pipeline stoppas i validate, inte som "field is immutable"

**Story.** Som pipeline-författare vill jag få veta i `validate` att jag ändrat en
redan använd pipeline, så att jag slipper välja mellan ett warm-up som tyst aldrig
körs om och en apply som dör mitt i med webhook-prosa.

## Varför det är viktigt

Kampanjer har en immutability-guard (`cli.py:109,158-166`); pipelines har ingen —
`campaigns.md:163` kallar det "enforced by review". Ändras bara ConfigMappen är
warm-up-Jobbets manifest byte-identiskt: applyn blir en no-op, warm-up:en körs
aldrig om och markören ligger kvar, så under `HF_HUB_OFFLINE=1` saknas den nya
modellen. Ändras `image:` eller `max_seconds:` blir det i stället 422 vid apply, och
`cli.py:230-255` lägger hela loopen i ett `try`, så första felet blockerar alla
senare kampanjer. I båda fallen kör index som inte startat ett annat recept än de
färdiga, under samma pipeline-id och S3-prefix (X7).

## Vad som levereras

- `validate`/`render` avvisar en `pipeline-sha256` som skiljer sig från
  `rendered/pipelines/<id>.yaml` — samma guard och ton som för kampanjer.
- Warm-up-markören bär sha:n i sin sökväg, så ett nytt recept kräver ett nytt
  warm-up.
- `apply` rapporterar fel per objekt; `ClusterError` får en mening för 422
  immutable-field som nämner pipeline-id-regeln.

## Klart när

- [ ] En ändrad pipeline avvisas av `validate` med en mening som namnger filen och
      regeln; ett nytt pipeline-id går igenom.
- [ ] En apply där ett objekt ger 422 rapporterar det objektet och applyar resten.
