#!/usr/bin/env bash
# Create the app's secrets in Secret Manager, without any value appearing in a command line,
# your shell history, a file, or the repo. Values go to gcloud through a pipe (--data-file=-).
#
#   session-secret:   newly generated (signs login tokens)
#   teacher-passcode: TEACHER_PASSCODE from app/api/.env, so you sign in with the passcode
#                     you already know
#
# Usage (from anywhere in the repo, with the exit-ticket gcloud configuration active):
#   scripts/create-app-secrets.sh
#
# It refuses to touch existing secrets. To rotate one, add a new version instead, e.g.
#   openssl rand -base64 48 | tr -d '\n' | gcloud secrets versions add session-secret --data-file=-
# then bump the version number in k8s/secretproviderclass.yaml.
set -euo pipefail

# Same region as everything else; the replication policy can't be changed later.
LOCATION="us-east4"
cd "$(git rev-parse --show-toplevel)"

for name in session-secret teacher-passcode; do
    if gcloud secrets describe "$name" >/dev/null 2>&1; then
        echo "Secret $name already exists; not overwriting it." >&2
        echo "To rotate it, add a version (see the comment at the top of this script)." >&2
        exit 1
    fi
done

TEACHER_PASSCODE="$(sed -n 's/^TEACHER_PASSCODE=//p' app/api/.env)"
if [ "${#TEACHER_PASSCODE}" -lt 8 ] || [ "${TEACHER_PASSCODE#replace-me}" != "$TEACHER_PASSCODE" ]; then
    echo "TEACHER_PASSCODE in app/api/.env is missing, shorter than 8 characters, or the placeholder." >&2
    exit 1
fi

create() {
    gcloud secrets create "$1" --replication-policy=user-managed --locations="$LOCATION" \
        --labels=app=exit-ticket --data-file=-
}

# printf is a shell builtin, so the passcode isn't passed to another program's command line.
openssl rand -base64 48 | tr -d '\n' | create session-secret
printf '%s' "$TEACHER_PASSCODE" | create teacher-passcode

echo "Created session-secret and teacher-passcode (version 1 each; values not shown)."
