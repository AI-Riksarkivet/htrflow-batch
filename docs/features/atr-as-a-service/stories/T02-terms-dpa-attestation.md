---
type: Product Backlog Item
id: 2960
parent: 2831
title: Villkor, personuppgiftsbiträdesavtal och försäkran tecknas i tjänstens flöden
---

# T02 · Villkor, personuppgiftsbiträdesavtal och försäkran tecknas i tjänstens flöden

**Story.** Som Riksarkivet vill jag att villkoren, personuppgiftsbiträdesavtalet
och försäkran om sekretessfritt material ingår i registrerings- och
uppladdningsflödena, versionshanterade och loggade, så att den rättsliga
konstruktionen — teknisk bearbetning för kundens räkning — finns i produkten
och inte bara i ett dokument.

## Varför det är viktigt

Gallringen (T10) förutsätter att bilderna inte blir allmänna handlingar hos
Riksarkivet (TF 2 kap. 13 § första stycket); det kräver att villkoren
uttryckligen anger teknisk bearbetning för annans räkning och att
Riksarkivet inte använder materialet för egna ändamål. Kunden är
personuppgiftsansvarig, Riksarkivet biträde, driftleverantören
underbiträde — kedjan ska godkännas av kunden. Sekretessavgränsningen i
version 1 kan inte kontrolleras tekniskt; den upprätthålls med villkor och
en försäkran vid varje uppladdning.

## Vad som levereras

- Villkorstext och PUB-avtal (ändamål, varaktighet, gallringsfrister,
  säkerhetsåtgärder, underbiträden, registrerades rättigheter,
  incidenter) som versionerade dokument i repot, tecknade digitalt vid
  registrering; ny version kräver nytt godkännande.
- Redovisad underbiträdeskedja på en publik sida, uppdaterad vid ändring.
- Försäkran vid uppladdning ("materialet innehåller inte uppgifter som
  omfattas av sekretess enligt OSL") loggad per jobb.
- Rutin för personuppgiftsincident och för misstänkt sekretessbrott, med
  kontaktväg.
- Juridisk granskning av flödet genomförd och dokumenterad (HFD 2018:48,
  OSL 40 kap. 5 §, 11 kap. 4 a §, lag 2020:914).

## Klart när

- [ ] Ingen organisation kan skicka jobb utan tecknat PUB-avtal i gällande
      version; ett jobb utan försäkran avvisas av API:et.
- [ ] Godkännanden är spårbara: vem, när, vilken version.
- [ ] Det juridiska utlåtandet finns i repot och pekar på det flöde som
      faktiskt körs.
