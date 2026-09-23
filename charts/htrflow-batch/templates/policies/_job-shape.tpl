{{/*
The pieces job-shape.yaml repeats per rule. A Kyverno rule's context is its
own, so the variables every rule reads are rendered into each of them.
*/}}

{{/* Which requests the job-shape rules see: every Job created in the
namespace, and the apply identity's updates too. */}}
{{- define "htrflow-batch.jobShapeMatch" -}}
match:
  any:
    - resources:
        kinds: [Job]
        namespaces: [{{ .Release.Namespace | quote }}]
        operations: [CREATE]
    - resources:
        kinds: [Job]
        namespaces: [{{ .Release.Namespace | quote }}]
        operations: [UPDATE]
      subjects:
        - kind: ServiceAccount
          name: htrflow-campaigns
          namespace: {{ .Release.Namespace }}
{{- end }}

{{- define "htrflow-batch.jobShapeContext" -}}
- name: pod
  variable:
    jmesPath: request.object.spec.template.spec
    default: {}
- name: labels
  variable:
    jmesPath: request.object.spec.template.metadata.labels || `{}`
    default: {}
- name: role
  variable:
    jmesPath: labels.app || ''
    default: ""
- name: ctrs
  variable:
    jmesPath: "[pod.containers || `[]`, pod.initContainers || `[]`][]"
    default: []
{{- end }}

{{/*
One role's shape: the Secrets it may read, the ConfigMap name prefixes it
may mount, its container and init-container names, its script and, when
security.jobImageRepos is set, the repositories its images come from. The
model cache is the one PVC; the batch pod mounts it read-only (the warm-up
fills it). Argument: a dict of root, role, secrets, configMaps,
containers, initContainers, args, imageRe.
*/}}
{{- define "htrflow-batch.jobShapeRule" -}}
{{- $root := .root }}
{{- $prefixes := list }}
{{- range .configMaps }}{{ $prefixes = append $prefixes (printf "starts_with(configMap.name, '%s')" .) }}{{ end }}
{{- $allowedKeys := "['name', 'image', 'command', 'args', 'env', 'volumeMounts', 'securityContext', 'resources', 'imagePullPolicy', 'terminationMessagePath', 'terminationMessagePolicy']" }}
- name: {{ trimPrefix "htrflow-" .role }}-job-shape
  {{- include "htrflow-batch.jobShapeMatch" $root | nindent 2 }}
  preconditions:
    all:
      - key: {{ `"{{ request.object.spec.template.metadata.labels.app || '' }}"` }}
        operator: Equals
        value: {{ .role }}
  context:
    {{- include "htrflow-batch.jobShapeContext" $root | nindent 4 }}
    - name: bad
      variable:
        jmesPath: >-
          [pod.containers[].name != `{{ toJson .containers }}`
          && join('', ['containers are ', to_string(pod.containers[].name), ', not {{ join ", " .containers }}']),
          (pod.initContainers || `[]`)[].name != `{{ toJson .initContainers }}`
          && join('', ['init containers are ', to_string((pod.initContainers || `[]`)[].name), ', not {{ toJson .initContainers }}']),
          pod.containers[0].command != ['/bin/sh', '-c']
          && join('', ['the command is ', to_string(pod.containers[0].command), ', not /bin/sh -c']),
          (length(pod.containers[0].args || `[]`) != `1`
          || base64_encode(to_string(pod.containers[0].args[0] || '')) != '{{ b64enc .args }}')
          && 'the script is not the one the converter renders',
          {{- if .initContainers }}
          (length(pod.initContainers[0].command || `[]`) != `3`
          || pod.initContainers[0].command[0:2] != ['/bin/sh', '-c']
          || !regex_match('^n=0; until \[ -f /data/[A-Za-z0-9._/-]+\.done \]; do n=\$\(\(n\+10\)\); \[ "\$n" -le [0-9]+ \] \|\| \{ echo "no warm-up marker at /data/[A-Za-z0-9._/-]+\.done after [0-9]+s: the pipeline\'s warm-up Job has not finished" >&2; exit 13; \}; sleep 10; done$', to_string(pod.initContainers[0].command[2] || ''))
          || pod.initContainers[0].args != null)
          && 'the init container is not the warm-up gate the converter renders',
          {{- end }}
          length(ctrs[?length(keys(@)[?!contains({{ $allowedKeys }}, @)]) > `0`]) > `0`
          && join('', ['fields the converter never renders (a probe or a hook is a command of its own) in: ',
          join(', ', ctrs[?length(keys(@)[?!contains({{ $allowedKeys }}, @)]) > `0`].name)]),
          length((pod.volumes || `[]`)[?length(keys(@)) != `2` || !contains(['configMap', 'secret', 'persistentVolumeClaim', 'emptyDir'], join('', keys(@)[?@ != 'name']))]) > `0`
          && join('', ['volumes other than configMap, secret, persistentVolumeClaim and emptyDir: ',
          join(', ', (pod.volumes || `[]`)[?length(keys(@)) != `2` || !contains(['configMap', 'secret', 'persistentVolumeClaim', 'emptyDir'], join('', keys(@)[?@ != 'name']))].name)]),
          length([ctrs[].env[].valueFrom.secretKeyRef.name, (pod.volumes || `[]`)[].secret.secretName][] | [?!contains(`{{ toJson .secrets }}`, @)]) > `0`
          && join('', ['Secrets this pod may not read: ',
          join(', ', [ctrs[].env[].valueFrom.secretKeyRef.name, (pod.volumes || `[]`)[].secret.secretName][] | [?!contains(`{{ toJson .secrets }}`, @)]),
          ' (allowed: {{ join ", " .secrets | default "none" }})']),
          length(ctrs[].envFrom[]) > `0` && 'envFrom, which the converter never renders',
          length(ctrs[].env[] | [?valueFrom && keys(valueFrom) != ['secretKeyRef']]) > `0`
          && join('', ['env from something other than a Secret key: ',
          join(', ', ctrs[].env[] | [?valueFrom && keys(valueFrom) != ['secretKeyRef']].name)]),
          length((pod.volumes || `[]`)[?configMap && !({{ join " || " $prefixes }})]) > `0`
          && join('', ['ConfigMaps this pod may not mount: ',
          join(', ', (pod.volumes || `[]`)[?configMap && !({{ join " || " $prefixes }})].configMap.name)]),
          length((pod.volumes || `[]`)[?configMap.items]) > `0`
          && 'a ConfigMap volume remapping its keys with items',
          length((pod.volumes || `[]`)[?persistentVolumeClaim && persistentVolumeClaim.claimName != '{{ $root.Values.modelCache.name }}']) > `0`
          && join('', ['PVCs other than the model cache ({{ $root.Values.modelCache.name }}): ',
          join(', ', (pod.volumes || `[]`)[?persistentVolumeClaim && persistentVolumeClaim.claimName != '{{ $root.Values.modelCache.name }}'].persistentVolumeClaim.claimName)]),
          {{- if eq .role "htrflow-batch" }}
          (length((pod.volumes || `[]`)[?persistentVolumeClaim && name != 'data']) > `0`
          || length(ctrs[].volumeMounts[] | [?name == 'data' && readOnly != `true`]) > `0`)
          && 'the model cache mounted writable: only the warm-up writes it',
          {{- end }}
          pod.containers[0].env[?name == 'PIPELINE_PATH'].value != ['/config/pipeline.yaml']
          && 'PIPELINE_PATH is not /config/pipeline.yaml',
          pod.containers[0].volumeMounts[?starts_with(mountPath, '/config')].[mountPath, name, subPath || '', subPathExpr || ''] != `[["/config", "pipeline", "", ""]]`
          && 'the pipeline volume is not the one mount at /config',
          !starts_with((pod.volumes || `[]`)[?name == 'pipeline'] | [0].configMap.name || '', 'htr-pipeline-')
          && 'the pipeline volume is not an htr-pipeline-* ConfigMap'
          {{- if .imageRe }},
          length(ctrs[].image | [?!regex_match('{{ .imageRe }}', split(@, '@')[0])]) > `0`
          && join('', ['images from outside security.jobImageRepos: ',
          join(', ', ctrs[].image | [?!regex_match('{{ .imageRe }}', split(@, '@')[0])])])
          {{- end }}][?@]
        default: []
  validate:
    message: >-
      not a {{ trimPrefix "htrflow-" .role }} Job as the converter renders it:
      {{ `{{ join('; ', bad) }}` }}
    deny:
      conditions:
        all:
          - key: {{ `"{{ length(bad) }}"` }}
            operator: GreaterThan
            value: 0
{{- end }}
