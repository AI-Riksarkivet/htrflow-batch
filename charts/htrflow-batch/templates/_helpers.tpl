{{/* charts/htrflow-batch/templates/_helpers.tpl */}}
{{- define "htrflow-batch.labels" -}}
app.kubernetes.io/name: htrflow-batch
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: htrflow-batch-{{ .Chart.Version }}
{{- end }}
