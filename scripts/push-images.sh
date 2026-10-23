#!/usr/bin/env bash
# Build the api and web images for linux/amd64 and push them to Artifact Registry,
# tagged with the current git commit's short SHA.
#
# Usage (from anywhere in the repo, with Docker running):
#   scripts/push-images.sh
#
# Needs: a clean working tree, an active gcloud configuration with project and
# compute/region set, and "gcloud auth configure-docker REGION-docker.pkg.dev" done once.

# -e: stop at the first failing command.
# -u: treat an unset variable as an error instead of an empty string.
# -o pipefail: a pipeline fails if any command in it fails, not just the last one.
set -euo pipefail

REPO="exit-ticket"
IMAGES=(api web)

cd "$(git rev-parse --show-toplevel)"

# A tag must always match committed code: refuse to build from uncommitted or untracked files.
if [ -n "$(git status --porcelain)" ]; then
    echo "Working tree is not clean. Commit or stash your changes first:" >&2
    git status --short >&2
    exit 1
fi

# Read the project and region from the active gcloud configuration at run time,
# so no real project ID is ever written into the repo.
PROJECT_ID="$(gcloud config get project 2>/dev/null)"
REGION="$(gcloud config get compute/region 2>/dev/null)"
if [ -z "$PROJECT_ID" ] || [ -z "$REGION" ]; then
    echo "Set a project and region first: gcloud config set project ... / compute/region ..." >&2
    exit 1
fi

REGISTRY="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}"
SHA="$(git rev-parse --short HEAD)"
FULL_SHA="$(git rev-parse HEAD)"

echo "Registry: ${REGISTRY}"
echo "Tag:      ${SHA} (commit ${FULL_SHA})"

for image in "${IMAGES[@]}"; do
    ref="${REGISTRY}/${image}:${SHA}"

    # Tags are immutable in this repository, so this exact tag can only ever be pushed once.
    if docker buildx imagetools inspect "$ref" >/dev/null 2>&1; then
        echo "== ${image}: ${ref} already exists, skipping"
        continue
    fi

    echo "== ${image}: building and pushing ${ref}"
    # --push: buildx sends the result straight to the registry, no separate docker push.
    # --provenance/--sbom=false: push one plain amd64 image, without extra untagged
    #   attestation manifests that the cleanup policy's untagged rule would treat as leftovers.
    # The revision label records the full commit inside the image itself.
    docker buildx build \
        --platform linux/amd64 \
        --provenance=false \
        --sbom=false \
        --label "org.opencontainers.image.revision=${FULL_SHA}" \
        --tag "$ref" \
        --push \
        "app/${image}"
done

echo
echo "Pushed images (reference these digests in Kubernetes):"
for image in "${IMAGES[@]}"; do
    ref="${REGISTRY}/${image}:${SHA}"
    digest="$(docker buildx imagetools inspect "$ref" --format '{{.Manifest.Digest}}')"
    echo "  ${ref}"
    echo "  ${REGISTRY}/${image}@${digest}"
done
