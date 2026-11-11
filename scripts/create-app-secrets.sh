#!/usr/bin/env bash
# Add the first version (the value) of the app's secrets, without any value appearing in a
# command line, your shell history, a file, or the repo. Values go to gcloud through a pipe
# (--data-file=-).
#
# The secrets themselves (name, us-east4 replication, labels, IAM) are created by Terraform
# (infra/terraform/foundation). Terraform never holds the values: they would land in its
# state in plain text.
#
#   session-secret:   newly generated (signs login tokens)
#   teacher-passcode: TEACHER_PASSCODE from app/api/.env, so you sign in with the passcode
#                     you already know
#
# Usage (from anywhere in the repo, with the exit-ticket gcloud configuration active):
#   scripts/create-app-secrets.sh
#
# It refuses to touch a secret that already has a version. To rotate one, add a version, e.g.
#   openssl rand -base64 48 | tr -d '\n' | gcloud secrets versions add session-secret --data-file=-
# then bump the version number in k8s/secretproviderclass.yaml.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

for name in session-secret teacher-passcode; do
    if ! gcloud secrets describe "$name" >/dev/null 2>&1; then
        echo "Secret $name doesn't exist. Create it with Terraform first (infra/terraform/foundation)." >&2
        exit 1
    fi
    if [ -n "$(gcloud secrets versions list "$name" --limit=1 --format='value(name)')" ]; then
        echo "Secret $name already has a version; not adding another." >&2
        echo "To rotate it, add a version (see the comment at the top of this script)." >&2
        exit 1
    fi
done

TEACHER_PASSCODE="$(sed -n 's/^TEACHER_PASSCODE=//p' app/api/.env)"
if [ "${#TEACHER_PASSCODE}" -lt 8 ] || [ "${TEACHER_PASSCODE#replace-me}" != "$TEACHER_PASSCODE" ]; then
    echo "TEACHER_PASSCODE in app/api/.env is missing, shorter than 8 characters, or the placeholder." >&2
    exit 1
fi

add_version() {
    gcloud secrets versions add "$1" --data-file=-
}

# printf is a shell builtin, so the passcode isn't passed to another program's command line.
openssl rand -base64 48 | tr -d '\n' | add_version session-secret
printf '%s' "$TEACHER_PASSCODE" | add_version teacher-passcode

echo "Added version 1 of session-secret and teacher-passcode (values not shown)."
