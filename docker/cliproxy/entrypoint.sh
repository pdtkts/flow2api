#!/bin/sh
set -eu

DATA_DIR="${CLIPROXY_DATA_DIR:-/data}"
CONFIG_PATH="${DATA_DIR}/config.yaml"
AUTH_DIR="${DATA_DIR}/auths"

if [ -z "${CLIPROXY_CLIENT_API_KEY:-}" ]; then
  echo "CLIPROXY_CLIENT_API_KEY is required" >&2
  exit 1
fi

if [ -z "${MANAGEMENT_PASSWORD:-}" ]; then
  echo "MANAGEMENT_PASSWORD is required" >&2
  exit 1
fi

case "${CLIPROXY_CLIENT_API_KEY}" in
  *[!A-Za-z0-9._~-]*)
    echo "CLIPROXY_CLIENT_API_KEY may contain only letters, numbers, dot, underscore, tilde, and hyphen" >&2
    exit 1
    ;;
esac

umask 077
mkdir -p "${AUTH_DIR}" "${DATA_DIR}/logs"

# Management changes are written back to this file. Bootstrap it once so
# account state and aliases survive Railway redeploys on the attached volume.
if [ ! -f "${CONFIG_PATH}" ]; then
  {
    printf '%s\n' \
      'host: ""' \
      'port: 8317' \
      'remote-management:' \
      '  allow-remote: true' \
      '  secret-key: ""' \
      '  disable-control-panel: false' \
      "auth-dir: \"${AUTH_DIR}\"" \
      'api-keys:' \
      "  - \"${CLIPROXY_CLIENT_API_KEY}\"" \
      'debug: false' \
      'request-log: false' \
      'logging-to-file: true' \
      'logs-max-total-size-mb: 64' \
      'error-logs-max-files: 10' \
      'usage-statistics-enabled: true' \
      'request-retry: 2' \
      'max-retry-credentials: 3' \
      'max-retry-interval: 15' \
      'force-model-prefix: false' \
      'ws-auth: true' \
      'disable-image-generation: true' \
      'routing:' \
      '  strategy: "round-robin"' \
      '  session-affinity: false' \
      '  session-affinity-ttl: "1h"'
  } > "${CONFIG_PATH}"
fi

ln -sf "${CONFIG_PATH}" /CLIProxyAPI/config.yaml
cd /CLIProxyAPI
exec ./CLIProxyAPI

