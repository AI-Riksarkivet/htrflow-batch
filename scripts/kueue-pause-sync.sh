#!/usr/bin/env bash
# Enforce a campaign's declared pause on its Kueue Workload.
#
# `suspend: true` in campaigns/<name>.yaml renders `spec.suspend: true` on the
# Job — but Kueue OWNS that field for a Workload it has admitted and flips it
# back within seconds (measured: two), so the rendered field is intent, not
# enforcement. The lever that holds is `spec.active` on the Workload: false
# evicts the pods and keeps the finished indexes, true re-admits and the Job
# continues at the next index. This script applies the intent to the Workload
# after every apply, and is idempotent.
#
# A Workload appears a moment AFTER its Job. For a campaign that is paused in
# git that moment is exactly the window in which Kueue would admit and start
# it, so a paused campaign waits for its Workload and fails loudly if it never
# turns up. A campaign that is NOT paused needs no wait: a Workload that does
# not exist yet is not admitted either, and the next apply catches it.
#
# `active: true` is written for every non-suspended campaign, so a Workload
# Kueue deactivated on its own (requeue limit, maximumExecutionTimeSeconds) is
# re-admitted at the next apply. Git is the truth about what should run.
#
# Usage: kueue-pause-sync.sh <namespace> <rendered/campaigns dir>
# With Argo CD, run the same script as a PostSync hook (docs:
# reference/campaign-yaml.md#pausing).
set -euo pipefail
ns=${1:?usage: kueue-pause-sync.sh <namespace> <rendered-campaigns-dir>}
dir=${2:?usage: kueue-pause-sync.sh <namespace> <rendered-campaigns-dir>}

# The Workload of $1, or empty. Kueue labels it with the Job's uid.
workload() {
  local uid wl
  uid=$(kubectl -n "$ns" get job "$1" -o jsonpath='{.metadata.uid}' 2>/dev/null || true)
  [ -n "$uid" ] || return 0
  wl=$(kubectl -n "$ns" get workload -l "kueue.x-k8s.io/job-uid=$uid" -o name 2>/dev/null || true)
  printf '%s' "${wl%%$'\n'*}"
}

fail=0
for file in "$dir"/*.yaml; do
  [ -e "$file" ] || continue
  # render.py writes one file per Job, named after it (cli.py `_render`).
  job=$(basename "$file" .yaml)
  # Read the intent from the Job document only: render.py writes the
  # ConfigMap first, then `---`, then the Job, so everything from the
  # separator on is the Job (no separator => single doc => the whole file).
  # The ERE is asserted against a rendered file by
  # packages/converter/tests/test_cli.py::test_the_pause_sync_regex_matches_a_rendered_paused_campaign
  job_doc=$(sed -n '/^---$/,$p' "$file")
  [ -n "$job_doc" ] || job_doc=$(cat "$file")
  if printf '%s\n' "$job_doc" |
     grep -qE '^[[:space:]]*suspend:[[:space:]]*true[[:space:]]*$'; then
    want=false
  else
    want=true
  fi
  wl=$(workload "$job")
  if [ -z "$wl" ] && [ "$want" = false ]; then
    for _ in $(seq 10); do
      sleep 1
      wl=$(workload "$job")
      if [ -n "$wl" ]; then break; fi
    done
  fi
  if [ -z "$wl" ]; then
    if [ "$want" = false ]; then
      echo "$job: paused in git, but no Kueue Workload appeared within 10s —" \
           "the pause is NOT enforced; re-run make campaigns-apply" >&2
      fail=1
    else
      echo "$job: no Workload yet, skipping"
    fi
    continue
  fi
  cur=$(kubectl -n "$ns" get "$wl" -o jsonpath='{.spec.active}' 2>/dev/null || true)
  [ "${cur:-true}" = "$want" ] && continue
  echo "$job: ${wl#workload.kueue.x-k8s.io/} active=$want"
  kubectl -n "$ns" patch "$wl" --type merge -p "{\"spec\":{\"active\":$want}}"
done
exit $fail
