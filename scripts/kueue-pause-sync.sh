#!/usr/bin/env bash
# Enforce a campaign's declared pause on its Kueue Workload.
#
# `suspend: true` in campaigns/<name>.yaml renders `spec.suspend: true` on the
# Job — but Kueue OWNS that field for a Workload it has admitted and flips it
# back within seconds (measured: two), so the rendered field is intent, not
# enforcement. The lever that holds is `spec.active` on the Workload: false
# evicts the pods and keeps the finished indexes, true re-admits and the Job
# continues at the next index. This script applies the intent to the Workload
# after every apply. Idempotent; a Workload Kueue has not created yet is
# skipped (the next apply catches it).
#
# Usage: kueue-pause-sync.sh <namespace> <rendered/campaigns dir>
# With Argo CD, run the same script as a PostSync hook (docs:
# reference/campaign-yaml.md#pausing).
set -euo pipefail
ns=${1:?usage: kueue-pause-sync.sh <namespace> <rendered-campaigns-dir>}
dir=${2:?usage: kueue-pause-sync.sh <namespace> <rendered-campaigns-dir>}

for file in "$dir"/*.yaml; do
  [ -e "$file" ] || continue
  # render.py writes one file per Job, named after it (cli.py `_render`).
  job=$(basename "$file" .yaml)
  # Job-level key: the ConfigMap doc in the same file indents its data deeper.
  if grep -qE '^  suspend: true$' "$file"; then want=false; else want=true; fi
  uid=$(kubectl -n "$ns" get job "$job" -o jsonpath='{.metadata.uid}' 2>/dev/null || true)
  [ -n "$uid" ] || continue
  wl=$(kubectl -n "$ns" get workload -l "kueue.x-k8s.io/job-uid=$uid" -o name 2>/dev/null || true)
  wl=${wl%%$'\n'*}
  [ -n "$wl" ] || { echo "$job: no Workload yet, skipping"; continue; }
  cur=$(kubectl -n "$ns" get "$wl" -o jsonpath='{.spec.active}' 2>/dev/null || true)
  [ "${cur:-true}" = "$want" ] && continue
  echo "$job: ${wl#workload.kueue.x-k8s.io/} active=$want"
  kubectl -n "$ns" patch "$wl" --type merge -p "{\"spec\":{\"active\":$want}}"
done
