---
type: Product Backlog Item
id: 2998
parent: 2800
title: Policyerna skrivs som Kyverno ValidatingPolicy (CEL) i stället för ClusterPolicy
---

# B69 · Policyerna skrivs som Kyverno ValidatingPolicy (CEL) i stället för ClusterPolicy

**Story.** Som plattformsteam vill vi att våra Kyverno-regler är skrivna i
den policytyp Kyverno går vidare med, så att de fortsätter fungera vid
uppgraderingar, kan testas med samma CLI som i dag och uttrycks i det
CEL-språk Kubernetes självt använder för admission-regler.

## Varför det är viktigt

De regler chartet installerar under `templates/policies/` (signerade
imagar, digest-pinnade imagar, tillåtna registry, modellrevision) är
`ClusterPolicy`-objekt. Kyverno 1.19, som chartet redan pinnar, markerar
`ClusterPolicy` som föråldrad till förmån för `ValidatingPolicy` med
CEL-uttryck. Reglerna är dessutom skrivna runt begränsningar i det gamla
formatet (Kyverno vägrar variabler från `foreach` i meddelanden, så de
använder `context` + `deny`), som CEL inte har. Att byta nu, medan reglerna
är fyra och små, är billigare än när DEV-klustret (B12) kör dem skarpt.

## Vad som levereras

- Samma fyra regler som `ValidatingPolicy`, med samma inparametrar från
  `values.yaml` och samma envägs-felmeddelanden till den som applicerar en
  kampanj.
- Kampanjrepots CI (`render.yml`) kör Kyverno CLI mot de nya objekten;
  policy-testerna i chartet uppdaterade.
- Audit-läge först på PoC-klustret (Kyverno rapporterar utan att stoppa)
  tills utslagen matchar de gamla reglerna, sedan enforce.
- Dokumentation: `docs/how-it-works/campaigns.md` (policy-avsnittet), `docs/reference/chart.md`.

## Klart när

- [ ] De fyra fallen från B63 Task 22:s E2E (osignerad, opinnad, fel
      registry, saknad modellrevision) avvisas med samma meningar av de nya
      reglerna på PoC-klustret, och den korrekta pipelinen släpps igenom.
- [ ] Inga `ClusterPolicy`-objekt finns kvar i chartet.
- [ ] Kyverno CLI i kampanjrepots CI ger samma utslag som klustret.
