---
type: Product Backlog Item
id: 3008
parent: 2800
title: Ett warm-up som inte fungerade misslyckas synligt
---

# B75 · Ett warm-up som inte fungerade misslyckas synligt

**Story.** Som operatör vill jag att ett warm-up som inte kunde skriva sin markör,
eller som dödades av sin deadline, syns som ett misslyckat warm-up med en mening —
inte som ett grönt Job vars kampanjer sedan står och väntar.

## Varför det är viktigt

`_write_marker` (`warmup.py:118-134`) returnerar tyst när `PIPELINE_ID` eller
`HF_HOME` saknas och loggar bara en varning vid `OSError`, medan `main` returnerar
`EXIT_OK` (`:114-115`): ett "lyckat" warm-up utan markör — exakt det läge B74
förvandlar till arton GPU-timmar — och det finns ingen warm-up-logg som säger
varför. Dessutom sätter `warmup-job.yaml:23` en terminal
`activeDeadlineSeconds: 3600` och `warmup.main` installerar ingen SIGTERM-hanterare,
till skillnad från `main.py:136-139`, så en långsam första nedladdning slutar med
ett tomt termination message — hålet `_fail` finns för att täppa till (X4).

## Vad som levereras

- Markören skrivs före lyckat-loggen och dess fel går genom `_fail`
  (permanent → 13 → `FailJob`).
- Samma SIGTERM-hanterare som batch-wrappern, som skriver
  `{"stage": "warmup", "permanent": false}`.
- Kampanjkortets warm-up-chip visar meningen (Task 28:s väg finns redan).

## Klart när

- [ ] Ett warm-up med oskrivbar markörkatalog avslutas med 13 och ett
      termination message som säger vad som inte gick att skriva.
- [ ] Ett warm-up som dödas av sin deadline lämnar ett termination message som
      syns på kampanjkortet.
