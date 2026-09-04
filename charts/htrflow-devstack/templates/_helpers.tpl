{{/* charts/htrflow-devstack/templates/_helpers.tpl */}}
{{- define "htrflow-devstack.labels" -}}
app.kubernetes.io/name: htrflow-devstack
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- /* Every object this chart renders carries this label (B63 Task 5): it
     is how CI asserts the prod chart (charts/htrflow-batch) never renders
     one of these PoC-only objects itself. */}}
app.kubernetes.io/component: devstack
helm.sh/chart: htrflow-devstack-{{ .Chart.Version }}
{{- end }}

{{/*
Cross-value checks that must fire even when the object they concern is
disabled — mirrors charts/htrflow-batch's htrflow-batch.validate.
*/}}
{{- define "htrflow-devstack.validate" -}}
{{- if and .Values.rustfs.console.enabled (not .Values.rustfs.enabled) }}
{{- fail "rustfs.console.enabled needs rustfs.enabled" }}
{{- end }}
{{- end }}

{{/* Pod Security `restricted` — container part. */}}
{{- define "htrflow-devstack.restrictedContainer" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities: { drop: ["ALL"] }
{{- end }}

{{/* Pod Security `restricted` — pod part; argument: the uid to run as. */}}
{{- define "htrflow-devstack.restrictedPod" -}}
runAsNonRoot: true
runAsUser: {{ . }}
runAsGroup: {{ . }}
fsGroup: {{ . }}
seccompProfile: { type: RuntimeDefault }
{{- end }}

{{/*
Digest refs are immutable, so IfNotPresent is safe; a tag must be re-pulled
on every rollout or a re-pushed `:dev` never lands (audit O13). Every image
in this chart's default values.yaml is already digest-pinned; this only
matters if a caller overrides one with a tag.
*/}}
{{- define "htrflow-devstack.pullPolicy" -}}
{{- if contains "@sha256:" . }}IfNotPresent{{ else }}Always{{ end }}
{{- end }}

{{/*
RustFS root credentials: values → existing Secret (upgrade) → random.
Returns a dict {access, secret}; Helm `lookup` is empty under `helm template`.
*/}}
{{- define "htrflow-devstack.rustfsCreds" -}}
{{- $access := .Values.rustfs.accessKey }}
{{- $secret := .Values.rustfs.secretKey }}
{{- $existing := lookup "v1" "Secret" .Release.Namespace .Values.s3.secretName }}
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
is readable except the platform's private state. RustFS honours NotResource
on an Allow (verified 2026-08-26 against rustfs@sha256:41fe8938…); a Deny
statement would also block the credentialed principals, and an anonymous-only
Condition is ignored — hence this shape. Keep in step with scripts/compose_init.py.
*/}}
{{- define "htrflow-devstack.bucketPolicy" -}}
{{- $b := .Values.s3.bucket }}
{{- $private := list
      (printf "arn:aws:s3:::%s/status/attempts.json" $b)
      (printf "arn:aws:s3:::%s/status/validation.json" $b)
      (printf "arn:aws:s3:::%s/status/volumes.json" $b)
      (printf "arn:aws:s3:::%s/status/failures/*" $b) }}
{{- if not .Values.rustfs.publicLogs }}
{{- $private = append $private (printf "arn:aws:s3:::%s/status/logs/*" $b) }}
{{- end }}
{{- dict "Version" "2012-10-17" "Statement" (list (dict
      "Sid" "AnonymousReadResults"
      "Effect" "Allow"
      "Principal" (dict "AWS" (list "*"))
      "Action" (list "s3:GetObject")
      "NotResource" $private)) | toJson }}
{{- end }}
