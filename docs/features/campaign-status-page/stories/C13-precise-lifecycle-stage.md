---
type: Product Backlog Item
id:
parent: 2923
title: Status-sidan säger exakt var i cykeln en kampanj och varje volym befinner sig
---

# C13 · Status-sidan säger exakt var i cykeln en kampanj och varje volym befinner sig

**Story.** Som den som beställt en kampanj vill jag se exakt var i
cykeln den är — väntar på GPU-kvot, värmer upp modellerna, läser
manifestet, bearbetar sida 37 av 480, kontrollerar resultatet, publicerar,
klar — för kampanjen som helhet och för varje volym, så att "Running" aldrig
är hela svaret och jag kan bedöma om något står stilla utan att fråga en
operatör.

## Varför det är viktigt

I dag visar kampanjkortet fem faser (Queued, Paused, Running, Succeeded,
Failed) plus en warm-up-chip, och varje volym ett av fyra tillstånd
(pending, active, done, failed). Wrappern vet mycket mer: den går igenom
stegen `setup` → `resume` → `load` → `stream` → `verify` → `publish`, räknar
sidor och mäter GPU-stopp — men det syns bara i run-loggen och, vid fel, i
termination-meddelandet. En volym som är "active" i fyrtio minuter kan
vara mitt i 480 sidor eller fast i ett modellinläsningsförsök; en "Queued"
kampanj kan vänta på kvot, på warm-up eller på att en annan kampanj ska
lämna GPU:n. Precisionen finns i systemet, den når inte sidan.

## Vad som levereras

- Wrappern skriver sitt aktuella steg och sin sidräkning (`klara/totalt`,
  senaste sidan, tid sedan förra sidan) till en liten statusfil per volym
  under bucketens **privata** `status/`-prefix (samma skydd som
  run-loggen), uppdaterad i samma takt som run-loggen
  (`LOG_SHIP_SECONDS`), och till poddens termination-meddelande när den
  slutar. Inget nytt skrivs till det publika resultatträdet.
- Läs-API:t (`/api/v1/jobs`) är den enda vägen till statusen: det läser
  statusfilerna med egna S3-läsrättigheter, cachar dem några sekunder
  och ger varje volym ett `stage` med de orden och sidräkningen, och
  kampanjen ett `waiting_on` när den är Queued: kvot i Kueue
  (`htr-batch-cq`), warm-up, eller paus. Webbläsaren gör ett anrop per
  sida, inte ett per volym, så kostnaden växer med antalet kampanjer som
  visas, inte med arkivets storlek (C08).
- Kampanjkortet och volymtabellen visar steget i klartext ("bearbetar
  sida 37 av 480, senaste för 12 s sedan") och en stillastående-markering
  när ingen sida blivit klar på längre än en konfigurerad tid.
- Tester i wrapper, web och frontend; dokumentation:
  `docs/reference/frontend.md`, `docs/reference/s3-layout.md`,
  `docs/how-it-works/wrapper.md`.

## Klart när

- [ ] En kampanj med två volymer på PoC-klustret visar, medan den kör:
      "väntar på kvot" för den andra volymen, steg och sidräkning för den
      första, och "klar" med sidantal när den är färdig.
- [ ] En kampanj som väntar på warm-up säger det, och en pausad säger
      "pausad" — inte "Queued".
- [ ] En volym vars senaste sida är äldre än gränsen markeras som
      stillastående, och markeringen försvinner när nästa sida blir klar.
- [ ] LOC-budgetarna för wrapper, web och frontend hålls exakt.
