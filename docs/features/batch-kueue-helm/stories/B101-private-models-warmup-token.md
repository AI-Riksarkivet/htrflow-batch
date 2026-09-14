---
type: Product Backlog Item
id: 3052
parent: 2800
title: Privata modeller på Hugging Face hämtas av warm-up med en token
---

# B101 · Privata modeller på Hugging Face hämtas av warm-up med en token

**Story.** Som produktägare vill jag kunna köra en kampanj på min egen
finjusterade TrOCR-modell, som ligger privat på Hugging Face Hub, så att en
modell som ännu inte är publicerad kan utvärderas i batch utan att först
göras publik.

## Varför det är viktigt

Modellerna hämtas en gång per pipeline av warm-up-Jobbet
(`manifests/warmup-job.yaml`, `warmup.py`) in i modellcachen; kampanj-Jobben
kör `HF_HUB_OFFLINE=1` mot cachen och behöver aldrig nå Hub. Warm-up laddar
ned anonymt, och en privat eller gated modell går därför inte att använda
alls: Jobbet misslyckas redan på nedladdningen, med ett fel som ser ut som
ett felstavat modell-id. Hemligheten hör hemma i klustret, inte i
campaigns-repot — precis som S3-nyckeln — och den hör hemma i exakt en pod:
warm-up är den enda som NetworkPolicyn släpper ut till Hub, den monterar
ingen S3-hemlighet och den avslutas när nedladdningen är klar.

## Vad som levereras

- `converter.yaml` får `hf_token_secret: <Secret-namn>` (tomt som default).
  Namnet valideras som ett Kubernetes-namn (DNS-1123 subdomain), så ett
  stavfel blir ett `validate`-fel i stället för ett avslag vid apply.
- `render` ger warm-up-containern `HF_TOKEN` via
  `secretKeyRef {key: token, optional: false}`. Kampanj-Jobbet får
  ingenting — varken env eller Secret-namnet — och ett test håller fast det.
- Ingen ny chart-mall och inget nytt chart-värde: Secreten är operatörens
  eget objekt, som S3-hemligheten, och ingen mall läser den.
- Warm-up loggar en rad som säger att en token finns — aldrig värdet,
  aldrig dess längd — så att en körning går att läsa tillbaka.
- Dokumentation: `converter.yaml`-tabellen, modellcache-avsnittet, säkerhets-
  sidan och deploy-sidans genomgång av hemligheter. Token behöver bara
  read-scope.

## Klart när

- [ ] En pipeline som pekar på en privat modell warmar upp grönt när
      `hf_token_secret` är satt, och kampanjen kör mot den fyllda cachen.
- [ ] Samma pipeline utan Secreten misslyckas i warm-up, inte i kampanjen.
- [ ] Ett test visar att kampanj-Jobbets podspec varken innehåller
      `HF_TOKEN` eller Secret-namnet.
- [ ] Warm-up-loggen säger att en token fanns, utan att avslöja något om
      den.
