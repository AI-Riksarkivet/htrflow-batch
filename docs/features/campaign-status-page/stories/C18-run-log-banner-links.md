---
type: Product Backlog Item
id: 3025
parent: 2923
title: Run-loggen berättar vad som kördes och länkar till varje färdig sida
---

# C18 · Run-loggen berättar vad som kördes och länkar till varje färdig sida

**Story.** Som den som följer en volym vill jag att run-loggen inleds med
vad som körs — wrapperns version och image, pipelinens modeller och
revisioner, källan, hur många sidor som redan var klara — att varje färdig
sida får en rad med länk till sin ALTO och till viewern på just den sidan,
och att loggen avslutas med en sammanfattning med länkar, så att loggen är
en berättelse jag kan följa och klicka i, inte bara stdout.

## Varför det är viktigt

Run-loggen är wrapperns egen stdout/stderr, skickad till bucketen var
femtonde sekund (`logship.py`) och visad på `/log`. I dag är den htrflows
INFO-rader och wrapperns stegbyten: den säger inte vilken image eller vilka
modellrevisioner som körde (det står i ALTO-filerna och `manifest.json`,
men inte där man tittar under körningen), den ger ingen väg från "sida 0044
klar" till att titta på sidan, och `/log` visar URL:er som text. Med den
inkrementella viewer-manifesten (C13/C11, 2026-09-08) finns sidan i viewern
innan volymen är klar — loggen är den naturliga platsen för länken dit.

## Vad som levereras

- **En inledning** när wrappern startar: wrapper-version och image-digest,
  pipeline-id med varje modell och revision, volymens källa (manifest-URL
  eller antal bild-URL:er), resume-läget ("12 av 638 sidor redan klara") —
  samma fakta som ALTO-proveniensen och `manifest.json`, sagda en gång
  överst.
- **En rad per färdig sida** med två länkar: sidans ALTO i bucketen och
  viewern öppnad på den canvasen (`iiif.json` + canvas-id), skriven när
  uppladdningen är klar. Misslyckade sidor får sin mening på samma plats
  (C14).
- **En avslutning**: klara och misslyckade sidor med orsaker, väggtid,
  sidor per sekund, och länkar till viewern, `manifest.json` och
  ALTO-katalogen.
- **`/log` gör URL:er klickbara**, genom samma URL-allow-list som sidans
  övriga länkar (`isHttpUrl`); wrapperns redigering av URL:er i loggen
  (credentials, query-tokens) är oförändrad, så inget hemligt blir en länk.
- Tester i wrapper och frontend; dokumentation:
  `docs/how-it-works/live-run-log.md`, `docs/reference/frontend.md`.

## Klart när

- [ ] Run-loggen för en volym på PoC-klustret börjar med inledningen, har
      en rad med två fungerande länkar per färdig sida, och slutar med
      sammanfattningen.
- [ ] Länkarna på `/log` går att klicka och öppnar rätt sida i viewern
      medan volymen fortfarande körs.
- [ ] En rad i loggen innehåller aldrig en URL med credentials eller
      query-token (testet för redigeringen täcker de nya raderna).
