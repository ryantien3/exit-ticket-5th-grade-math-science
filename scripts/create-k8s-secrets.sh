#!/usr/bin/env bash
# Create the two Kubernetes Secrets the app needs, without any secret value appearing in
# a command line, your shell history, or the repo.
#
#   db-secrets:  POSTGRES_PASSWORD (newly generated)
#   api-secrets: DATABASE_URL (built from that password, pointing at the db Service),
#                SESSION_SECRET (newly generated), TEACHER_PASSCODE (from app/api/.env,
#                so you sign in with the passcode you already know)
#
# Usage (from anywhere in the repo, with kubectl pointed at the cluster):
#   scripts/create-k8s-secrets.sh
#
# The temporary database is recreated with each cluster, so new passwords each time are fine.
# To start over: kubectl -n exit-ticket delete secret db-secrets api-secrets
set -euo pipefail

NAMESPACE="exit-ticket"
cd "$(git rev-parse --show-toplevel)"

for name in db-secrets api-secrets; do
    if kubectl -n "$NAMESPACE" get secret "$name" >/dev/null 2>&1; then
        echo "Secret $name already exists in $NAMESPACE; not overwriting it." >&2
        echo "The running database was initialized with its password. To start over, delete" >&2
        echo "both Secrets and the db pod, then run this again." >&2
        exit 1
    fi
done

TEACHER_PASSCODE="$(sed -n 's/^TEACHER_PASSCODE=//p' app/api/.env)"
if [ -z "$TEACHER_PASSCODE" ]; then
    echo "TEACHER_PASSCODE not found in app/api/.env" >&2
    exit 1
fi

# Temporary files readable only by you (umask 077), deleted on exit even if a command fails.
umask 077
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

DB_PASSWORD="$(openssl rand -hex 24)"
SESSION_SECRET="$(openssl rand -base64 48 | tr -d '\n')"

printf 'POSTGRES_PASSWORD=%s\n' "$DB_PASSWORD" > "$TMP_DIR/db.env"
printf 'DATABASE_URL=%s\nSESSION_SECRET=%s\nTEACHER_PASSCODE=%s\n' \
    "postgresql+psycopg://exitticket:${DB_PASSWORD}@db:5432/exitticket" \
    "$SESSION_SECRET" \
    "$TEACHER_PASSCODE" > "$TMP_DIR/api.env"

kubectl -n "$NAMESPACE" create secret generic db-secrets --from-env-file="$TMP_DIR/db.env"
kubectl -n "$NAMESPACE" create secret generic api-secrets --from-env-file="$TMP_DIR/api.env"

echo "Created db-secrets and api-secrets in $NAMESPACE (values not shown)."
