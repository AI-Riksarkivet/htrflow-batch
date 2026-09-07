---
type: Product Backlog Item
id:
parent: 2800
title: Kyverno-policy för pod-form — kommando, volymer och secrets
---

# B79 · Kyverno-policy för pod-form: kommando, volymer och secrets

**Story.** Som ansvarig för klustret vill jag att policyerna säger något om vad en
podd får *göra*, inte bara vilken image den kör, så att en godkänd och signerad
image inte kan användas till att läsa ut S3-nycklarna.

## Varför det är viktigt

Alla fyra ClusterPolicies (`templates/policies/*`) tittar bara på `image` och
pipeline-ConfigMappen — aldrig på `command`, `args`, `env`, `volumes` eller
`initContainers`. S3-secreten kan monteras av vilken podd som helst i namespacet
(`campaign-job.yaml:165-168`), wrapper-imagen har ett skal, och labeln
`app: htrflow-warmup` ger publik egress på 443 (`network.yaml:105-121`). Ett Job på
den godkända, signerade, digest-pinnade imagen som monterar `htr-batch-s3` och kör
`sh -c 'curl … </secrets/s3/credentials'` passerar varenda grind — vägen dit är
B78:s `rendered/` (X10).

## Vad som levereras

- En policy som pinnar podd-formen: tillåtet `command`, ingen secret-mount utanför
  wrapper-containern, egress-labeln bara på renderade warm-ups.
- Ett fixture-par (godkänt/avvisat) tillsammans med de övriga policytesterna (B21).
- `docs/development/security.md`: vad policyerna nu täcker och vad de inte gör.

## Klart när

- [ ] Ett Job på en godkänd image som monterar S3-secreten i en egen container
      avvisas av policyn i `Enforce`.
- [ ] Alla renderade objekt från `examples/campaigns` passerar oförändrade.
