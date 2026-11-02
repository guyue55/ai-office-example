#!/bin/bash
set -euo pipefail
KC_DB_PASSWORD="$(cat /run/secrets/identity_password)"
KC_BOOTSTRAP_ADMIN_PASSWORD="$(cat /run/secrets/admin_password)"
export KC_DB_PASSWORD KC_BOOTSTRAP_ADMIN_PASSWORD
exec /opt/keycloak/bin/kc.sh start-dev
