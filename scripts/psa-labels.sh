#!/usr/bin/env bash
# Label the namespace for Pod Security Admission. Helm cannot label a
# namespace it did not create. enforce is the installed release's
# `security.psaEnforce`, or PSA_ENFORCE when set; warn/audit are always
# restricted so the hardened pods stay provably restricted.
# Fails closed (finding 3102): a missing release is the chart's own default,
# said out loud, but any other failure to read the release refuses -- it
# says nothing about the level, and guessing baseline would quietly
# downgrade a restricted namespace. A level the chart's schema does not take
# is refused rather than written.
# Usage: psa-labels.sh <release> <namespace>
set -euo pipefail
release=${1:?release} ns=${2:?namespace}

if [ -n "${PSA_ENFORCE:-}" ]; then
  level=$PSA_ENFORCE from="PSA_ENFORCE"
else
  err=$(mktemp)
  trap 'rm -f "$err"' EXIT
  if values=$(helm get values "$release" -n "$ns" --all -o json 2>"$err"); then
    level=$(jq -r '.security.psaEnforce // "baseline"' <<<"$values")
    from="release $release/$ns security.psaEnforce"
  elif grep -q 'release: not found' "$err"; then
    level=baseline from="the chart default: no release $release in $ns yet"
  else
    cat "$err" >&2
    echo "psa-labels: cannot read release $release/$ns; set PSA_ENFORCE=baseline|restricted to label anyway" >&2
    exit 1
  fi
fi
case "$level" in
  baseline | restricted) ;;
  *) echo "psa-labels: enforce level '$level' (from $from) is not baseline or restricted" >&2; exit 1 ;;
esac
echo "enforce=$level (from $from)"
kubectl label ns "$ns" --overwrite \
  "pod-security.kubernetes.io/enforce=$level" \
  pod-security.kubernetes.io/warn=restricted \
  pod-security.kubernetes.io/audit=restricted
