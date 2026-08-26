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
{{- if and .Values.devStack.rustfs.console.enabled (not .Values.devStack.rustfs.enabled) }}
{{- fail "devStack.rustfs.console.enabled needs devStack.rustfs.enabled" }}
{{- end }}
{{- if and .Values.devStack.gitDaemon.enabled (not .Values.devStack.gitDaemon.seedUrl) (not .Values.devStack.rustfs.enabled) }}
{{- fail "devStack.gitDaemon needs devStack.rustfs.enabled (seed bucket) or an explicit devStack.gitDaemon.seedUrl" }}
{{- end }}
{{- if .Values.security.verifyImages.enabled }}
{{- if or (not .Values.security.verifyImages.issuer) (not .Values.security.verifyImages.subject) }}
{{- fail "security.verifyImages.issuer and .subject are required when security.verifyImages.enabled" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Control-plane images (reconciler, viewer) must be digest-pinned: a tag can be
re-pushed by anyone with registry write access (audit S3). devStack.allowTagImages
opens the PoC iteration loop. Usage: include "htrflow-batch.requireDigest" (list $ <ref> "<values key>")
*/}}
{{- define "htrflow-batch.requireDigest" -}}
{{- $root := index . 0 }}{{- $ref := index . 1 }}{{- $key := index . 2 }}
{{- if and (not (contains "@sha256:" $ref)) (not $root.Values.devStack.allowTagImages) }}
{{- fail (printf "%s must be pinned by digest (…@sha256:<64 hex>), got %q; set devStack.allowTagImages=true only for a PoC iteration loop" $key $ref) }}
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
devStack RustFS root credentials: values → existing Secret (upgrade) → random.
Returns a dict {access, secret}; Helm `lookup` is empty under `helm template`.
*/}}
{{- define "htrflow-batch.rustfsCreds" -}}
{{- $access := .Values.devStack.rustfs.accessKey }}
{{- $secret := .Values.devStack.rustfs.secretKey }}
{{- $existing := lookup "v1" "Secret" .Release.Namespace .Values.s3.existingSecret }}
{{- if and $existing $existing.data }}
  {{- if and (not $access) (hasKey $existing.data "AWS_ACCESS_KEY_ID") }}{{ $access = index $existing.data "AWS_ACCESS_KEY_ID" | b64dec }}{{ end }}
  {{- if and (not $secret) (hasKey $existing.data "AWS_SECRET_ACCESS_KEY") }}{{ $secret = index $existing.data "AWS_SECRET_ACCESS_KEY" | b64dec }}{{ end }}
{{- end }}
{{- if not $access }}{{ $access = randAlphaNum 32 }}{{ end }}
{{- if not $secret }}{{ $secret = randAlphaNum 32 }}{{ end }}
{{- dict "access" $access "secret" $secret | toJson }}
{{- end }}

{{/*
Anonymous-read bucket policy for the results bucket (audit X14/S6): everything
is readable except the reconciler's private state. RustFS honours NotResource
on an Allow (verified 2026-08-26 against rustfs@sha256:41fe8938…); a Deny
statement would also block the credentialed principals, and an anonymous-only
Condition is ignored — hence this shape. Keep in step with scripts/compose_init.py.
*/}}
{{- define "htrflow-batch.bucketPolicy" -}}
{{- $b := .Values.s3.bucket }}
{{- $private := list
      (printf "arn:aws:s3:::%s/status/attempts.json" $b)
      (printf "arn:aws:s3:::%s/status/validation.json" $b)
      (printf "arn:aws:s3:::%s/status/failures/*" $b)
      (printf "arn:aws:s3:::%s/status/warmup/*" $b) }}
{{- if not .Values.devStack.rustfs.publicLogs }}
{{- $private = append $private (printf "arn:aws:s3:::%s/status/logs/*" $b) }}
{{- end }}
{{- dict "Version" "2012-10-17" "Statement" (list (dict
      "Sid" "AnonymousReadResults"
      "Effect" "Allow"
      "Principal" (dict "AWS" (list "*"))
      "Action" (list "s3:GetObject")
      "NotResource" $private)) | toJson }}
{{- end }}
