{{/* charts/htrflow-batch/templates/_helpers.tpl */}}
{{- define "htrflow-batch.labels" -}}
app.kubernetes.io/name: htrflow-batch
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: htrflow-batch-{{ .Chart.Version }}
{{- end }}

{{/*
Guard against a values tree that lost a whole section — `helm upgrade
--reuse-values` with a chart that added `network` once rendered every
NetworkPolicy away (audit O6). values.schema.json rejects the same shape,
but only when the schema is evaluated; this fires from any template.
*/}}
{{- define "htrflow-batch.validate" -}}
{{- if not .Values.network }}
{{- fail "`.Values.network` is missing: upgrade with --reset-then-reuse-values (or a full values file), never plain --reuse-values" }}
{{- end }}
{{- if .Values.security.verifyImages.enabled }}
{{- if or (not .Values.security.verifyImages.issuer) (not .Values.security.verifyImages.subject) }}
{{- fail "security.verifyImages.issuer and .subject are required when security.verifyImages.enabled" }}
{{- end }}
{{- end }}
{{- /*
The web front is an unauthenticated NodePort, and its ingress list defaults
to every address (2026-09-14, audit). The default stays -- the dev stack and
the compose smoke rely on it and a narrowed default would cut them off on
upgrade -- but the operator has to say the exposure is deliberate. Only when
the policies are actually rendered: a campaigns repo's CI renders this chart
with network.enabled=false to get at the policy objects alone.
An EMPTY list is the same catch-all in other words: an ingress rule whose
`from` names no source matches every source, so the list an operator writes
to shut the port would open it (finding 3100).
*/}}
{{- if and .Values.network.enabled (not .Values.network.web.allowPublicIngress) }}
{{- if not .Values.network.web.ingressCidrs }}
{{- fail "network.web.ingressCidrs is empty, and a NetworkPolicy rule with no sources admits every address, so an empty list would open the unauthenticated web front to everyone rather than close it: list the ranges that may reach it, or set network.web.allowPublicIngress=true to accept that any address may" }}
{{- end }}
{{- if has "0.0.0.0/0" .Values.network.web.ingressCidrs }}
{{- fail "network.web.ingressCidrs allows 0.0.0.0/0, and the web front has no authentication of its own: either list the ranges that may reach it (include the node range — NodePort traffic arrives SNAT'd from the node), or set network.web.allowPublicIngress=true to accept that any address that can route to a node may open the campaign browser, the viewer and the read API" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
The kube-apiserver as a pod reaches it AFTER service DNAT -- the address an
egress rule has to name, since kube-router (k3s) matches egress on the
backing endpoint rather than the ClusterVIP. Auto-detected at install time;
`network.apiServer.cidr` is the answer for `helm template` and for a
kubeconfig that may not read Endpoints. Two policies need it (the web front
and the apply pod), so it is computed once here rather than a third time.
*/}}
{{- define "htrflow-batch.apiServerCidr" -}}
{{- $api := .Values.network.apiServer.cidr }}
{{- if not $api }}
  {{- with (lookup "v1" "Endpoints" "default" "kubernetes") }}
    {{- with (index .subsets 0) }}{{ $api = printf "%s/32" (index .addresses 0).ip }}{{ end }}
  {{- end }}
{{- end }}
{{- if not $api }}
{{- fail "network.apiServer.cidr is required when the kube-apiserver endpoint cannot be looked up (helm template / no RBAC)" }}
{{- end }}
{{- $api }}
{{- end }}

{{/*
Control-plane images (web.image) must be digest-pinned: a tag can be
re-pushed by anyone with registry write access (audit S3).
security.allowTagImages opens the PoC iteration loop. Usage:
include "htrflow-batch.requireDigest" (list $ <ref> "<values key>")
*/}}
{{- define "htrflow-batch.requireDigest" -}}
{{- $root := index . 0 }}{{- $ref := index . 1 }}{{- $key := index . 2 }}
{{- if and (not (contains "@sha256:" $ref)) (not $root.Values.security.allowTagImages) }}
{{- fail (printf "%s must be pinned by digest (…@sha256:<64 hex>), got %q; set security.allowTagImages=true only for a PoC iteration loop" $key $ref) }}
{{- end }}
{{- end }}

{{/*
Digest refs are immutable, so IfNotPresent is safe; a tag must be re-pulled
on every rollout or a re-pushed `:dev` never lands (audit O13).
*/}}
{{- define "htrflow-batch.pullPolicy" -}}
{{- if contains "@sha256:" . }}IfNotPresent{{ else }}Always{{ end }}
{{- end }}

{{/* Pod Security `restricted` — container part. */}}
{{- define "htrflow-batch.restrictedContainer" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities: { drop: ["ALL"] }
{{- end }}

{{/* Pod Security `restricted` — pod part; argument: the uid to run as. */}}
{{- define "htrflow-batch.restrictedPod" -}}
runAsNonRoot: true
runAsUser: {{ . }}
runAsGroup: {{ . }}
fsGroup: {{ . }}
seccompProfile: { type: RuntimeDefault }
{{- end }}

