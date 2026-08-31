---
type: Product Backlog Item
id: 2967
parent: 2831
title: Webhook när ett jobb är klart
---

# T09 · Webhook när ett jobb är klart

**Story.** Som integratör vill jag registrera en webhook-URL på jobbet och
få ett signerat anrop när det är klart, avbrutet eller misslyckat, så att
vårt system hämtar resultatet direkt i stället för att fråga tjänsten var
femte minut.

## Varför det är viktigt

Jobb tar timmar; polling från många integrationer belastar API:et och ger
sämre upplevelse än en notis. Statussidan har en motsvarande story (C09,
notis till beställaren) för Riksarkivets interna kampanjer; den här är
kundvänd och API-baserad.

## Vad som levereras

- Webhook-URL och secret per jobb (eller per organisation som
  standard); anrop med HMAC-signatur, jobb-id, status och resultat-länk.
- Återförsök med backoff och en synlig leveranslogg per jobb.
- Dokumenterat payload-schema, versionerat med resultatschemat (T08).

## Klart när

- [ ] Ett jobb som slutförs anropar webhooken inom en minut; en mottagare
      som svarar 500 får tre nya försök.
- [ ] Signaturen kan verifieras med kodexemplet i docs.
