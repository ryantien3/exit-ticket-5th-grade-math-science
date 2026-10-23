#!/bin/sh
# Runs before nginx starts (the image runs every *.sh in /docker-entrypoint.d/, in order).
# Stop the container if API_HOST is missing, instead of starting an nginx whose every
# API request fails. There is deliberately no default.
set -eu

if [ -z "${API_HOST:-}" ]; then
    echo "$0: API_HOST is not set. Set it to the API's host:port, e.g. api:8000." >&2
    exit 1
fi
