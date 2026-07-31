#!/usr/bin/env bash
set -euo pipefail

base_url="${1:-https://register.canxu.top}"
base_url="${base_url%/}"

if [[ "$base_url" != https://* && "${ALLOW_INSECURE_HTTP:-0}" != "1" ]]; then
  echo "ERROR: public panel URL must use HTTPS" >&2
  exit 2
fi

request_code() {
  curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
    --connect-timeout "${CONNECT_TIMEOUT_SECONDS:-10}" \
    --max-time "${MAX_TIME_SECONDS:-20}" "$1"
}

root_code="$(request_code "$base_url/")"
health_code="$(request_code "$base_url/api/health")"
status_code="$(request_code "$base_url/api/status")"

[[ "$root_code" == "200" ]] || {
  echo "ERROR: root returned HTTP $root_code, expected 200" >&2
  exit 1
}
[[ "$health_code" == "200" ]] || {
  echo "ERROR: health returned HTTP $health_code, expected 200" >&2
  exit 1
}
[[ "$status_code" == "401" ]] || {
  echo "ERROR: anonymous status returned HTTP $status_code, expected 401" >&2
  exit 1
}

if [[ -n "${MONITOR_TOKEN:-}" ]]; then
  auth_code="$({
    printf 'header = "Authorization: Bearer %s"\n' "$MONITOR_TOKEN"
  } | curl --silent --show-error --config - --output /dev/null --write-out '%{http_code}' \
      --connect-timeout "${CONNECT_TIMEOUT_SECONDS:-10}" \
      --max-time "${MAX_TIME_SECONDS:-20}" "$base_url/api/status")"
  [[ "$auth_code" == "200" ]] || {
    echo "ERROR: authenticated status returned HTTP $auth_code, expected 200" >&2
    exit 1
  }
  echo "OK public panel root=200 health=200 anonymous_status=401 authenticated_status=200"
else
  echo "OK public panel root=200 health=200 anonymous_status=401 (authenticated check skipped)"
fi
