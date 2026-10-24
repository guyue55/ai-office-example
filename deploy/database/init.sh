#!/bin/bash
set -euo pipefail
export OFFICE_OWNER_PASSWORD="$(cat /run/secrets/owner_password)"
export OFFICE_APP_PASSWORD="$(cat /run/secrets/app_password)"
export OFFICE_IDENTITY_PASSWORD="$(cat /run/secrets/identity_password)"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<'SQL'
\getenv owner_password OFFICE_OWNER_PASSWORD
\getenv app_password OFFICE_APP_PASSWORD
\getenv identity_password OFFICE_IDENTITY_PASSWORD
SELECT format('CREATE ROLE office_owner LOGIN NOSUPERUSER PASSWORD %L', :'owner_password') \gexec
SELECT format('CREATE ROLE office_app LOGIN NOSUPERUSER PASSWORD %L', :'app_password') \gexec
SELECT format('CREATE ROLE office_identity LOGIN NOSUPERUSER PASSWORD %L', :'identity_password') \gexec
CREATE DATABASE office OWNER office_owner;
CREATE DATABASE office_identity OWNER office_identity;
REVOKE ALL ON DATABASE office FROM PUBLIC;
GRANT CONNECT ON DATABASE office TO office_app;
\connect office
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA office AUTHORIZATION office_owner;
GRANT USAGE ON SCHEMA office TO office_app;
ALTER ROLE office_app IN DATABASE office SET search_path TO office;
ALTER ROLE office_owner IN DATABASE office SET search_path TO office;
ALTER DEFAULT PRIVILEGES FOR ROLE office_owner IN SCHEMA office GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO office_app;
SQL
