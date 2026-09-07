---
type: Product Backlog Item
id: 3020
parent: 2923
title: Läs-API:t svarar med en mening och rätt headers även när något går sönder
---

# C15 · Läs-API:t svarar med en mening och rätt headers även när något går sönder

**Story.** Som läsare av statussidan vill jag att ett oväntat fel ger en mening som
säger vad som hänt, och att svaret bär samma säkerhetsheaders som alla andra — inte
ett naket 500 utan skydd.

## Varför det är viktigt

`parse_index_ranges` (`projection.py:20-36`) och `_pod_completion_index` (`:203`)
kör `int()` på label- och statussträngar utan skydd: en Pod med
`job-completion-index: NaN` gör `GET /api/v1/jobs/ns/j` till ett naket `500` i
klartext, och Kubernetes trunkerar dessutom `completedIndexes`/`failedIndexes` vid
stor skala, vilket fäller sidan på samma sätt. Värre: svaret bär då ingen av
`SECURITY_HEADERS` — middlewaren (`app.py:92-96`) når aldrig
`response.headers.update` när anropet kastar (prob: `headers: {}`). Läsaren får
"Can't reach the campaign service right now (HTTP 500)" för en databugg (X12).

## Vad som levereras

- En exception handler som svarar 502/503 med en mening och som applicerar
  `SECURITY_HEADERS`; headers sätts även på felsvar.
- `parse_index_ranges` och `_pod_completion_index` tål ett icke-heltal och ett
  trunkerat intervall utan att kasta.
- `frontend/src/lib/reasons.ts` får meningen för det här läget.

## Klart när

- [ ] En Pod med ogiltigt `job-completion-index` ger ett läsbart svar, inte ett
      500, och sidan visar resten av kampanjen.
- [ ] Varje felsvar från `/api/v1/*` bär `SECURITY_HEADERS`, verifierat av test.
