---
type: Product Backlog Item
id: 3140
parent: 2800
title: Valfri S3_VERIFY_TLS i S3-Secreten hoppar över certifikatkontrollen mot lagringen (tillfällig lösning)
---

# B110 · Valfri S3_VERIFY_TLS i S3-Secreten hoppar över certifikatkontrollen mot lagringen (tillfällig lösning)

**Story.** Som operatör på DEV vill jag kunna köra mot lagringen trots att dess
certifikat inte matchar värdnamnet, tills certifikatet är rättat, utan att
bygga om någon image.

## Varför det är viktigt

Lagringen på DEV svarar med ett certifikat vars namn inte matchar adressen, så
varje S3-anrop föll på TLS-kontrollen. Att rätta certifikatet ligger utanför
systemet; utan en väg förbi det gick ingen kampanj att köra.

## Vad som levereras

- Nyckeln `S3_VERIFY_TLS` i S3-Secreten; `"false"` hoppar över
  certifikatkontrollen i wrappern och i web-fronten och loggar en varning.
- `job-shape`-policyn släpper igenom nyckeln på kampanj-Jobben (chart 0.15.0).

Kvar utanför systemet: lagringens certifikat rättas, och då tas nyckeln bort på
DEV.

## Klart när

- [x] En kampanj på DEV läser och skriver lagringen med `S3_VERIFY_TLS=false`.
- [x] Varningen syns i körloggen.
