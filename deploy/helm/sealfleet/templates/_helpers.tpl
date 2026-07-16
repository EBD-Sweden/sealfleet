{{/*
Expand the name of the chart.
*/}}
{{- define "mcpfinder.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Fully qualified app name.
*/}}
{{- define "mcpfinder.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Common labels.
*/}}
{{- define "mcpfinder.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/name: {{ include "mcpfinder.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: mcpfinder
part-of: mcpfinder
{{- with .Values.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{/*
Selector labels for a given component. Call: (dict "ctx" . "component" "router")
*/}}
{{- define "mcpfinder.selectorLabels" -}}
app.kubernetes.io/name: {{ include "mcpfinder.name" .ctx }}
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
app.kubernetes.io/component: {{ .component }}
app: {{ include "mcpfinder.componentName" (dict "ctx" .ctx "component" .component) }}
{{- end -}}

{{/*
Stable per-component resource name, e.g. mcp-router (matches existing k8s/ naming
and the app: labels the NetworkPolicies select on).
*/}}
{{- define "mcpfinder.componentName" -}}
{{- if eq .component "portal" -}}
mcpfinder-portal
{{- else if eq .component "core-agent" -}}
core-agent
{{- else -}}
mcp-{{ .component }}
{{- end -}}
{{- end -}}

{{/*
Resolve a service image: <registry>/<imageName>:<tag> or @<digest>.
A digest (per-service .svc.digest or global image.digest) pins immutably and
wins over the tag. Call: (dict "ctx" . "svc" .Values.router)
*/}}
{{- define "mcpfinder.image" -}}
{{- $tag := .svc.tag | default .ctx.Values.image.tag -}}
{{- $digest := .svc.digest | default .ctx.Values.image.digest -}}
{{- if $digest -}}
{{- printf "%s/%s@%s" .ctx.Values.image.registry .svc.imageName $digest -}}
{{- else -}}
{{- printf "%s/%s:%s" .ctx.Values.image.registry .svc.imageName $tag -}}
{{- end -}}
{{- end -}}

{{/*
Name of the platform Secret (created or existing).
*/}}
{{- define "mcpfinder.secretName" -}}
{{- if .Values.secrets.existingSecret -}}
{{- .Values.secrets.existingSecret -}}
{{- else -}}
{{- printf "%s-secrets" (include "mcpfinder.fullname" .) -}}
{{- end -}}
{{- end -}}

{{/*
In-cluster Postgres service name.
*/}}
{{- define "mcpfinder.postgresName" -}}
{{- printf "%s-postgresql" (include "mcpfinder.fullname" .) -}}
{{- end -}}

{{/*
Effective DATABASE_URL: computed for in-cluster Postgres, else from the Secret.
This template returns ONLY the value string (used inside the Secret data).
*/}}
{{- define "mcpfinder.databaseUrl" -}}
{{- if .Values.postgresql.enabled -}}
{{- printf "postgresql://%s:%s@%s:5432/%s?sslmode=%s" .Values.postgresql.auth.username .Values.postgresql.auth.password (include "mcpfinder.postgresName" .) .Values.postgresql.auth.database .Values.postgresql.sslmode -}}
{{- else -}}
{{- .Values.secrets.databaseUrl -}}
{{- end -}}
{{- end -}}

{{/*
Portal public URL (for NEXTAUTH_URL / AUTH_URL).
*/}}
{{- define "mcpfinder.portalUrl" -}}
{{- if .Values.portal.publicUrl -}}
{{- .Values.portal.publicUrl -}}
{{- else if and .Values.ingress.enabled .Values.ingress.hosts.portal -}}
{{- printf "https://%s" .Values.ingress.hosts.portal -}}
{{- else -}}
{{- printf "http://%s:%d" (include "mcpfinder.componentName" (dict "ctx" . "component" "portal")) (int .Values.portal.port) -}}
{{- end -}}
{{- end -}}
