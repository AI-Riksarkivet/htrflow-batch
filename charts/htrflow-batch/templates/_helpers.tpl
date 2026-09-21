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
The policies are off by default, because a policy nothing reconciles is
worse than none -- so an install that followed no profile enforced nothing
the repository built, and said nothing about it (B80). Off stays possible
for a cluster without Kyverno; it has to be said out loud.
*/}}
{{- if and (not .Values.security.policies.enabled) (not .Values.security.policies.allowDisabled) }}
{{- fail "security.policies.enabled is false, so nothing in this namespace refuses an image from any registry, a tag instead of a digest or an unpinned model: install Kyverno and set security.policies.enabled=true (values-prod.yaml does), or set security.policies.allowDisabled=true to accept that" }}
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
{{- /*
By prefix, not by string: `0.0.0.0/1` + `128.0.0.0/1` is every address in
two entries (finding 3064). A /8 is the widest range the chart takes
without the flag -- a site's 10.0.0.0/8, not somebody's internet. The
schema holds every entry to IPv4 a.b.c.d/n, so the prefix is the part
after the slash.
*/}}
{{- range .Values.network.web.ingressCidrs }}
{{- if lt (atoi (last (splitList "/" .))) 8 }}
{{- fail (printf "network.web.ingressCidrs has %s, wider than /8, and the web front has no authentication of its own: list the ranges your clients' addresses are in, or set network.web.allowPublicIngress=true to accept that any address that can route to a node may open the campaign browser, the viewer and the read API" .) }}
{{- end }}
{{- end }}
{{- end }}
{{- end }}

{{/*
The kube-apiserver as a pod reaches it AFTER service DNAT -- the addresses
an egress rule has to name, since kube-router (k3s) matches egress on the
backing endpoint rather than the ClusterVIP. An HA control plane has one
endpoint per API server and DNAT picks any of them, so the rule names every
one (finding 3101). Auto-detected at install time; `network.apiServer.cidr`
/ `.cidrs` are the answer for `helm template` and for a kubeconfig that may
not read Endpoints. Two policies need it (the web front and the apply pod),
so it is computed once here. Renders the whole egress rule, as JSON.
*/}}
{{- define "htrflow-batch.apiServerEgress" -}}
{{- $api := .Values.network.apiServer }}
{{- $cidrs := concat (compact (list $api.cidr)) ($api.cidrs | default list) | uniq }}
{{- $ports := list $api.port }}
{{- if not $cidrs }}
  {{- $found := include "htrflow-batch.apiServerFromEndpoints" (lookup "v1" "Endpoints" "default" "kubernetes") | fromJson }}
  {{- $cidrs = $found.cidrs }}
  {{- if $found.ports }}{{ $ports = $found.ports }}{{ end }}
{{- end }}
{{- if not $cidrs }}
{{- fail "network.apiServer.cidr or network.apiServer.cidrs is required when the kube-apiserver endpoints cannot be looked up (helm template / no RBAC); list every API server of an HA control plane" }}
{{- end }}
{{- $to := list }}
{{- range $cidrs }}{{ $to = append $to (dict "ipBlock" (dict "cidr" .)) }}{{ end }}
{{- $portRules := list }}
{{- range $ports }}{{ $portRules = append $portRules (dict "port" (int .)) }}{{ end }}
{{- toJson (dict "to" $to "ports" $portRules) }}
{{- end }}

{{/*
Every address and port of an Endpoints object (the `kubernetes` Service's,
from `lookup`), as JSON {"cidrs": [...], "ports": [...]}. Nil-safe: a
missing object, or one without subsets, is nothing found -- never
`index of nil`. Separate from the lookup so a test can feed it a fixture.
*/}}
{{- define "htrflow-batch.apiServerFromEndpoints" -}}
{{- $cidrs := list }}
{{- $ports := list }}
{{- range (default dict .).subsets }}
  {{- range .addresses }}{{ $cidrs = append $cidrs (printf "%s/32" .ip) }}{{ end }}
  {{- range .ports }}{{ $ports = append $ports (int .port) }}{{ end }}
{{- end }}
{{- toJson (dict "cidrs" ($cidrs | uniq) "ports" ($ports | uniq)) }}
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

