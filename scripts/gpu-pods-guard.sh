#!/usr/bin/env bash
# `make install-devstack NVIDIA_DEVICE_PLUGIN=false` renders the devstack
# chart without the RuntimeClass and device-plugin DaemonSet every GPU pod
# depends on; doing that while GPU pods ran deleted both from under them once
# (docs/development/e2e-indexed-jobs.md). Exits 0 only when the cluster was
# read and no pod outside kube-system is Running or Pending on the nvidia
# runtime or with a GPU request. A kubectl that cannot list the pods is not
# an empty list (finding 3102): the answer is unknown, so the guard refuses.
set -euo pipefail
refuse() { echo "install-devstack: refusing NVIDIA_DEVICE_PLUGIN=false -- $1; set FORCE=1 to override." >&2; exit 1; }

pods=$(kubectl get pods -A -o json) || refuse "cannot list the cluster's pods to check for GPU pods"
# Slurped, so an empty or non-list answer is an error rather than no pods.
gpu=$(jq -rs 'if length == 1 and (.[0].items | type) == "array" then .[0].items[] else error("not a pod list") end
  | select(.metadata.namespace != "kube-system")
  | select(.status.phase == "Running" or .status.phase == "Pending")
  | select(.spec.runtimeClassName == "nvidia"
      or any(.spec.containers[]?; (.resources.requests["nvidia.com/gpu"]? // .resources.limits["nvidia.com/gpu"]?) != null))
  | "\(.metadata.namespace)/\(.metadata.name)"' <<<"$pods") || refuse "cannot read the pod list kubectl returned"
[ -z "$gpu" ] || refuse "GPU pods are running and depend on the RuntimeClass/DaemonSet this would delete: ${gpu//$'\n'/ }"
