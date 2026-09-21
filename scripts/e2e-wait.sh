#!/usr/bin/env bash
# The waiting half of `make e2e`: block until the warm-up Job completes and
# every campaign Job reaches a terminal condition, printing completedIndexes
# as it goes. Fails closed (finding 3102): a kubectl that cannot read the
# cluster, no campaign Job at all, a Job that cannot be read, or any Job that
# ends Failed is a failed run -- none of them is "everything finished".
# Usage: e2e-wait.sh <namespace> <campaign-selector> <timeout-seconds>
# E2E_POLL_SECONDS sets the interval between looks (default 15).
set -euo pipefail
ns=${1:?namespace} sel=${2:?campaign selector} timeout=${3:?timeout in seconds}
poll=${E2E_POLL_SECONDS:-15}

kubectl -n "$ns" wait --for=condition=complete --timeout=600s job -l "$sel,app=htrflow-warmup"

deadline=$(($(date +%s) + timeout))
while :; do
  # Assigned first: in `for j in $(kubectl ...)` a failed kubectl is an
  # empty list, and an empty list used to mean done.
  jobs=$(kubectl -n "$ns" get job -l "app=htrflow-batch,$sel" -o name)
  [ -n "$jobs" ] || { echo "::error::no campaign Job matches app=htrflow-batch,$sel in $ns: nothing ran" >&2; exit 1; }
  pending="" failed=""
  for j in $jobs; do
    line=$(kubectl -n "$ns" get "$j" -o \
      jsonpath='{.spec.completions}|{.status.completedIndexes}|{.status.conditions[?(@.status=="True")].type}')
    IFS='|' read -r total done_idx cond <<<"$line"
    echo "$j completions=$total completedIndexes=[$done_idx] $cond"
    # Whole words: Kubernetes sets FailureTarget/SuccessCriteriaMet first,
    # and only Complete or Failed is the end.
    case " $cond " in
      *" Failed "*) failed="$failed $j" ;;
      *" Complete "*) ;;
      *) pending="$pending $j" ;;
    esac
  done
  if [ -z "$pending" ]; then
    [ -z "$failed" ] || { echo "::error::failed:$failed" >&2; exit 1; }
    exit 0
  fi
  [ "$(date +%s)" -lt "$deadline" ] || { echo "::error::still running:$pending" >&2; exit 1; }
  sleep "$poll"
done
