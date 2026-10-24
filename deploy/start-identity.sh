#!/bin/bash
set -euo pipefail
export KC_DB_PASSWORD="$(cat /run/secrets/identity_password)"
export KC_BOOTSTRAP_ADMIN_PASSWORD="$(cat /run/secrets/admin_password)"
exec /opt/keycloak/bin/kc.sh start-dev
