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
{{- /*
Ingress mode (T3): a ClusterIP Service behind an ingress-nginx controller
(web.yaml's Ingress object), gated by the controller's own allow-list
(network.web.ingressFrom) rather than client address ranges -- behind a
controller the pod only ever sees the controller's own address, never the
browser's. Checked before the ingressCidrs guards below, and independent of
them: dropping ingressFrom must fail on ITS OWN sentence, not the catch-all
one that network.web.ingressCidrs' still-default 0.0.0.0/0 would otherwise
raise first.
*/}}
{{- if .Values.web.ingress.enabled }}
{{- if ne .Values.web.service.type "ClusterIP" }}
{{- fail "web.ingress.enabled needs web.service.type=ClusterIP" }}
{{- end }}
{{- if not .Values.web.ingress.host }}
{{- fail "web.ingress.enabled needs web.ingress.host" }}
{{- end }}
{{- if and .Values.network.enabled (not .Values.network.web.ingressFrom) }}
{{- fail "web.ingress.enabled needs network.web.ingressFrom" }}
{{- end }}
{{- end }}
{{- /*
The two guards below are about client address ranges reaching the web front
directly; in ingress mode (or whenever an operator has already named the
controller's peers in ingressFrom) the addresses they would be asked to
police belong to the controller, not the browser, so they have nothing to
check.
*/}}
{{- /*
The ranges a production install cannot leave to a default (0923 D-8). An
empty list is not a narrow one here: no S3 route fails every volume after
its GPU time, no cluster ranges leave the pod and service networks out of
every carve-out, and no IIIF range fetches nothing. Only while the
NetworkPolicies are rendered -- a campaigns repo's CI renders the chart with
network.enabled=false to get at the policy objects alone.
*/}}
{{- if .Values.network.enabled }}
{{- if and (not .Values.network.s3Cidrs) (not .Values.network.s3InNamespace) }}
{{- fail "network.s3Cidrs is empty and network.s3InNamespace is false, so campaign pods and the web front have no route to the results bucket and every volume would fail after its GPU time: list the S3 endpoint's ranges in network.s3Cidrs, or set network.s3InNamespace=true when the bucket is the in-namespace RustFS of charts/htrflow-devstack" }}
{{- end }}
{{- if not .Values.network.clusterCidrs }}
{{- fail "network.clusterCidrs is empty, so no egress range the chart renders would carve the cluster's own pod and service ranges out of itself: list your cluster's pod and service CIDRs" }}
{{- end }}
{{- if not .Values.network.iiifCidrs }}
{{- fail "network.iiifCidrs is empty and has no default: name the address ranges of the IIIF servers your campaigns fetch page images from, e.g. --set network.iiifCidrs='{<cidr>}' (0.0.0.0/0 admits any origin and still reaches no cluster or private address)" }}
{{- end }}
{{- end }}
{{- if and .Values.network.enabled (not .Values.network.web.allowPublicIngress) (not (or .Values.web.ingress.enabled .Values.network.web.ingressFrom)) }}
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


{{/*
What an egress rule to an address range must NOT reach (2026-09-14 audit,
then 0923 D-3): the pod and service ranges, every node address, the API
server,
link-local 169.254.0.0/16 -- where a cloud serves instance credentials to
whoever asks -- loopback, and `network.privateCidrs`, the ranges the
cluster's own network is carved out of. Node addresses are
`network.nodeCidrs`, or every node's InternalIP when that is empty (Helm
`lookup`; nothing under `helm template`). As JSON, a list.
*/}}
{{- define "htrflow-batch.internalCidrs" -}}
{{- $nodes := list }}
{{- range .Values.network.nodeCidrs }}{{ $nodes = append $nodes . }}{{ end }}
{{- if not $nodes }}
  {{- range (lookup "v1" "Node" "" "").items }}
    {{- range .status.addresses }}
      {{- if and (eq .type "InternalIP") (not (contains ":" .address)) }}{{ $nodes = append $nodes (printf "%s/32" .address) }}{{ end }}
    {{- end }}
  {{- end }}
{{- end }}
{{- /* The API server by its own values, or its looked-up endpoints (0923 M-3):
     carved out even on a public address, not only as a node or a private one. */}}
{{- $api := concat (compact (list .Values.network.apiServer.cidr)) (.Values.network.apiServer.cidrs | default list) }}
{{- if not $api }}
  {{- $api = (include "htrflow-batch.apiServerFromEndpoints" (lookup "v1" "Endpoints" "default" "kubernetes") | fromJson).cidrs }}
{{- end }}
{{- toJson (concat .Values.network.clusterCidrs $nodes $api (list "169.254.0.0/16" "127.0.0.0/8") .Values.network.privateCidrs | uniq) }}
{{- end }}

{{/*
An IPv4 range as JSON {"net": <first address as an integer>, "bits": <prefix>}.
The schema holds every range a value names to a.b.c.d/n.
*/}}
{{- define "htrflow-batch.ipv4Range" -}}
{{- $parts := splitList "/" . }}
{{- $net := 0 }}
{{- range splitList "." (first $parts) }}{{ $net = add (mul $net 256) (atoi .) }}{{ end }}
{{- toJson (dict "net" $net "bits" (atoi (last $parts))) }}
{{- end }}

{{/*
The `to:` entries of an egress rule to address ranges, as JSON: one ipBlock
per range, each with an `except` of every internal range (internalCidrs
above) that lies strictly inside it (0923 D-3). The carve-out used to
apply to a literal `0.0.0.0/0` alone, so `s3Cidrs: [0.0.0.0/0]` or the two
halves `0.0.0.0/1` + `128.0.0.0/1` reopened the metadata address -- the
same split the web-ingress guard already refuses (finding 3064). Ranges
nest or are disjoint, so "inside" is: a longer prefix, and the same first
`bits` bits. A range named inside an internal one, or equal to it, holds
none and stays whole: that is the operator naming a host on their own
network, and egress rules are a union. Argument: (list $ <ranges>).
*/}}
{{- define "htrflow-batch.egressTo" -}}
{{- $root := index . 0 }}
{{- $internal := include "htrflow-batch.internalCidrs" $root | fromJsonArray }}
{{- $to := list }}
{{- range index . 1 }}
  {{- $outer := include "htrflow-batch.ipv4Range" . | fromJson }}
  {{- $block := 1 }}
  {{- range until (sub 32 (int $outer.bits) | int) }}{{ $block = mul $block 2 }}{{ end }}
  {{- $except := list }}
  {{- range $internal }}
    {{- if not (contains ":" .) }}
      {{- $inner := include "htrflow-batch.ipv4Range" . | fromJson }}
      {{- if and (gt (int $inner.bits) (int $outer.bits)) (eq (div (int64 $inner.net) $block) (div (int64 $outer.net) $block)) }}
        {{- $except = append $except . }}
      {{- end }}
    {{- end }}
  {{- end }}
  {{- $ipBlock := dict "cidr" . }}
  {{- if $except }}{{ $_ := set $ipBlock "except" $except }}{{ end }}
  {{- $to = append $to (dict "ipBlock" $ipBlock) }}
{{- end }}
{{- toJson $to }}
{{- end }}

{{/*
S3 egress, for the two pods that reach the bucket -- the batch Job and the
web front's progress reader -- as a JSON list of rules: with
network.s3InNamespace, the in-namespace `app: rustfs` pod on 9000
(charts/htrflow-devstack's RustFS; the two charts share no values), plus
network.s3Cidrs on network.s3Ports -- named ports, not the whole range
(2026-09-14 audit): a self-hosted endpoint's range is a slice of the
operator's own network.
*/}}
{{- define "htrflow-batch.s3Egress" -}}
{{- $s3 := list }}
{{- if .Values.network.s3InNamespace }}
{{- $s3 = append $s3 (dict "to" (list (dict "podSelector" (dict "matchLabels" (dict "app" "rustfs")))) "ports" (list (dict "port" 9000))) }}
{{- end }}
{{- with .Values.network.s3Cidrs }}
  {{- $ports := list }}
  {{- range $.Values.network.s3Ports }}{{ $ports = append $ports (dict "port" .) }}{{ end }}
  {{- $s3 = append $s3 (dict "to" (include "htrflow-batch.egressTo" (list $ .) | fromJsonArray) "ports" $ports) }}
{{- end }}
{{- toJson $s3 }}
{{- end }}
