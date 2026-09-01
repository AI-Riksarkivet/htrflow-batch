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
{{- end }}

{{/*
Control-plane images (viewer, api) must be digest-pinned: a tag can be
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

