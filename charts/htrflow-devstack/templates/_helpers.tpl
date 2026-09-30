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
{{- /* Credentials nobody chose: empty (generated afresh on every render) or a
     value this repo publishes. See values.yaml's devStack block. */}}
{{- $known := list "" "ci-access-key" "ci-secret-key-0123456789" "minioadmin" "rustfsadmin" }}
{{- if and .Values.rustfs.enabled (not .Values.devStack.insecureDefaults) }}
{{- if or (has .Values.rustfs.accessKey $known) (has .Values.rustfs.secretKey $known) }}
{{- fail "rustfs.accessKey/secretKey is empty or a value published in this repo: set credentials of your own, or set devStack.insecureDefaults: true to accept generated or known ones (charts/htrflow-devstack/values.yaml says why)" }}
{{- end }}
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
The login user's password: existing Secret (upgrade) → random. Nobody sets
it in values -- it is read back from the Secret -- so there is no value to
refuse; `helm template` has no cluster to look up and renders a fresh one.
*/}}
{{- define "htrflow-devstack.loginPassword" -}}
{{- $existing := lookup "v1" "Secret" .Release.Namespace .Values.s3.loginSecret }}
{{- if and $existing $existing.data (hasKey $existing.data "password") }}
{{- index $existing.data "password" | b64dec }}
{{- else }}
{{- randAlphaNum 32 }}
{{- end }}
{{- end }}

{{/*
The login user's policy: read one object of the results bucket. No
s3:ListBucket (the proxy never lists, and a listing would show every
namespace's keys), no write, no other bucket -- the image cache included.
*/}}
{{- define "htrflow-devstack.readPolicy" -}}
{{- dict "Version" "2012-10-17" "Statement" (list (dict
      "Effect" "Allow"
      "Action" (list "s3:GetObject")
      "Resource" (list (printf "arn:aws:s3:::%s/*" .Values.s3.bucket)))) | toJson }}
{{- end }}
