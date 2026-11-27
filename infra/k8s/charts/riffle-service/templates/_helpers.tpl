{{/* The service's name; required. */}}
{{- define "riffle.name" -}}
{{- required "name is required" .Values.name -}}
{{- end -}}

{{/* Labels on every object. app.kubernetes.io/name is what the network rules match on. */}}
{{- define "riffle.labels" -}}
app.kubernetes.io/name: {{ include "riffle.name" . }}
app.kubernetes.io/part-of: riffle
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "riffle.selector" -}}
app.kubernetes.io/name: {{ include "riffle.name" . }}
{{- end -}}

{{- define "riffle.autoscaled" -}}
{{- if or .Values.autoscaling.hpa.enabled .Values.autoscaling.keda.enabled -}}true{{- end -}}
{{- end -}}
