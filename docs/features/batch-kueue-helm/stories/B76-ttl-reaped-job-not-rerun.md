---
type: Product Backlog Item
id:
parent: 2800
title: En slutförd kampanj återuppstår inte när TTL städat bort Jobbet
---

# B76 · En slutförd kampanj återuppstår inte när TTL städat bort Jobbet

**Story.** Som beställare vill jag att en kampanj som är klar förblir klar, så att
nästa `apply` — en commit i campaigns-repot eller Argo CD:s self-heal — inte kör
om alla volymer och betalar hela GPU-notan igen.

## Varför det är viktigt

`campaign-job.yaml:30` hårdkodar `ttlSecondsAfterFinished: 86400` (inget värde),
och `cli.py:237-242` applyar varje renderad kampanj vid varje körning; en
apply-patch mot ett objekt som inte finns **skapar** det (`apply-rbac.yaml:11-14`).
Ett dygn efter att kampanjen blivit klar är Jobbet borta, och nästa apply
återskapar det och kör om samtliga index. Ingenting säger åt en operatör att ta
bort en färdig kampanjfil (X6).

## Vad som levereras

- TTL blir ett `converter.yaml`-värde med en lång default, och kan sättas per
  pipeline där det behövs.
- `apply` hoppar över — eller varnar tydligt om — en kampanj vars renderade Job
  saknas medan `volumes.txt` är oförändrad.
- `docs/how-it-works/campaigns.md`: en färdig kampanj tas bort ur `campaigns/`.

## Klart när

- [ ] En kampanj som körts klart och vars Job städats bort körs inte om vid nästa
      `htrflow-campaigns apply`, verifierat på PoC-klustret.
- [ ] TTL:t går att sätta i `converter.yaml` och syns i den genererade
      konfigurationsreferensen.
