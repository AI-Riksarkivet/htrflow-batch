---
type: Product Backlog Item
id: 3137
parent: 2800
title: Resultaten bakom en login — proxyn läser bucketen med varje användares egna nycklar
---

# B107 · Resultaten bakom en login — proxyn läser bucketen med varje användares egna nycklar

**Story.** Som ansvarig för materialet vill jag att ALTO, IIIF-manifest och
körloggar bara går att läsa för den som loggat in med sitt eget konto i
lagringen, så att resultat ur arkivmaterial inte ligger öppna för alla som når
adressen.

## Varför det är viktigt

Resultat-bucketen var publikt läsbar: varje länk i campaign-browsern och
viewern pekade rakt in i bucketen. På ett delat kluster är det inte acceptabelt,
och det räcker inte med en gemensam tjänstenyckel — då går det inte att se vem
som läst vad.

## Vad som levereras

- En resultat-proxy i web-imagen under `/results`: login med lagringens egna
  konton (HCP-nyckelhärledning, eller `none` för en lokal S3), en krypterad
  session-cookie, och varje läsning görs med den inloggades egna nycklar.
- `/api/v1` kräver en giltig session; progress läses per användare.
- Chart-värdena `results.sessionSecret` och `results.keyDerivation`;
  `resultsUrl` måste sluta på `/results`, både i chart och converter.
- Körloggarna under `<namespace>/status/logs/` går också genom proxyn.

## Klart när

- [x] En användare loggar in med sitt eget lagringskonto och ser ALTO i viewern.
- [x] Utan session ger `/results` och `/api/v1` ingen data.
- [x] Två användare i samma webbläsare delar aldrig nycklar (inget gemensamt
      cookie jar).
- [x] Körd på DEV-klustret mot riktig lagring.
