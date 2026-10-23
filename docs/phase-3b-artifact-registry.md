# Phase 3B — Artifact Registry

Phase 3B publishes the two images from Phase 3A to **Artifact Registry**, Google Cloud's private registry. It's the
first phase that creates a billable GCP resource. The images are tagged with the git commit they were built from,
recorded by digest, and kept in check by a cleanup policy. Nothing runs them yet; that's Phase 3C (GKE).

| Part | What was built | PR |
|---|---|---|
| **3B** | Docker repository `exit-ticket` in `us-east4` (immutable tags, labels), cleanup policy, Docker credential helper, `scripts/push-images.sh`, first push of `api` and `web` | (this PR) |

> **About the commands in this doc.** Each code block contains commands only, no `#` comments: in zsh, `#` is not a
> comment unless `setopt interactivecomments` is on. Commands that use a relative path are run from the repo root.

> **Placeholders used below.** Replace these with your own values when you run a command.
>
> | Placeholder | Meaning |
> |---|---|
> | `PROJECT_ID` | Your GCP project ID (`gcloud config get project`) |
> | `PROJECT_NUMBER` | Your numeric project number |
> | `REPO` | The repository name: `exit-ticket` |
> | `IMAGE` | `api` or `web` |
> | `SHA` | A short git commit SHA used as a tag, e.g. `ac93f06` |
> | `DIGEST` | An image digest, `sha256:` followed by 64 hex characters |

**Contents**

1. [Concepts](#1-concepts)
2. [Tags, digests and immutable tags](#2-tags-digests-and-immutable-tags)
3. [Creating the repository](#3-creating-the-repository)
4. [Cleanup policy](#4-cleanup-policy)
5. [Docker authentication without keys](#5-docker-authentication-without-keys)
6. [Building and pushing](#6-building-and-pushing)
7. [Verifying](#7-verifying)
8. [Cost and vulnerability scanning](#8-cost-and-vulnerability-scanning)
9. [How GKE will pull these images (Phase 3C preview)](#9-how-gke-will-pull-these-images-phase-3c-preview)
10. [Teardown](#10-teardown)
11. [Quirks](#11-quirks)
12. [Lessons learned](#12-lessons-learned)
13. [ACE exam notes](#ace-exam-notes)

---

## 1. Concepts

**Artifact Registry** stores build outputs: Docker images, plus Python, npm, Maven, apt, yum and Go packages, and
generic files. Access is controlled with IAM, per repository.

**It replaced Container Registry** (`gcr.io`), which has been shut down. Container Registry kept images in Cloud
Storage buckets with bucket-level permissions. Artifact Registry adds real repositories, regional placement, IAM per
repository, cleanup policies and immutable tags.

**A repository has one format and one location**, and neither can be changed after creation.

| Choice | Ours | Why |
|---|---|---|
| Format | `docker` | Speaks the Docker registry protocol: `docker push` / `docker pull` work as-is |
| Mode | standard | Stores our own images. (*Remote* repositories cache an upstream like Docker Hub; *virtual* ones put one URL over several repositories.) |
| Location | `us-east4` | Same region as the GKE cluster: faster pulls, and same-region pulls aren't billed as network transfer |

**The image path:**
```
us-east4-docker.pkg.dev / PROJECT_ID / exit-ticket / api : ac93f06
└── registry host ─────┘  └ project ┘  └ repository ┘ └image┘ └tag ┘
```

One repository (`exit-ticket`) with two images (`api`, `web`): one place for IAM and the cleanup policy, and a
separate version history per image.

---

## 2. Tags, digests and immutable tags

| | Tag (`:ac93f06`) | Digest (`@sha256:294082fd…`) |
|---|---|---|
| What it is | A label you choose | A SHA-256 hash of the image manifest |
| Can it change? | By default, yes: it can be moved to another image | Never: different bytes give a different digest |
| Good for | Humans: "which commit is this?" | Machines: "run exactly this" |

- **Never `latest`.** It means "whatever was pushed last", so two nodes pulling an hour apart can run different code.
- **Tag with the git commit SHA.** Every image traces back to the exact committed code. The full SHA is also stored
  in the image as the label `org.opencontainers.image.revision`.
- **Deploy by digest.** Kubernetes manifests will reference `IMAGE@DIGEST`, so the cluster runs exactly what was
  tested.

**Why a digest pull always gets the same bytes:** the manifest lists the hash of every layer and of the config,
and the digest is the hash of the manifest. Docker re-checks every hash as it downloads and rejects any mismatch.

**Immutable tags are on.** Once `api:ac93f06` exists, no one can push a different image under that tag; it can only
be deleted. That's exactly what a commit-SHA tag should mean. The trade-off: rebuilding the same commit produces a
slightly different digest (timestamps), so a second push of the same tag is rejected. The push script checks for
the tag first and skips it.

---

## 3. Creating the repository

```bash
gcloud artifacts repositories create exit-ticket --repository-format=docker --location=us-east4 --description="Exit Ticket container images (api, web)" --immutable-tags --labels=app=exit-ticket,env=dev
```

| Flag | Meaning |
|---|---|
| `--repository-format=docker` | Docker registry protocol (permanent) |
| `--location=us-east4` | Region where the images are stored (permanent) |
| `--description` | Shown in the console |
| `--immutable-tags` | A tag can never be moved to a different image |
| `--labels=app=exit-ticket,env=dev` | Key/value labels; they appear in **billing reports**, so costs can be filtered by app |

```bash
gcloud artifacts repositories list --location=us-east4 --format="table(name.basename(),format,mode,sizeBytes)"
gcloud artifacts repositories describe exit-ticket --location=us-east4
```

In `describe`, check `registryUri: us-east4-docker.pkg.dev/PROJECT_ID/exit-ticket`, `format: DOCKER`,
`mode: STANDARD_REPOSITORY`, `dockerConfig.immutableTags: true`, the labels, and
`vulnerabilityScanningConfig.enablementState: SCANNING_DISABLED` (see [§8](#8-cost-and-vulnerability-scanning)).

Creation is a **long-running operation**: gcloud waits for `Waiting for operation ... done`.

---

## 4. Cleanup policy

Every push adds a version, and versions are kept forever unless a policy says otherwise. Storage is billed per GB
per month, so without a policy the bill creeps up with every push. A cleanup policy is applied automatically, in the
background, about once a day.

[`infra/artifact-registry/cleanup-policy.json`](../infra/artifact-registry/cleanup-policy.json):

| Rule | Action | Condition | Effect, per image |
|---|---|---|---|
| `keep-5-most-recent` | Keep | `mostRecentVersions.keepCount: 5` | The 5 newest versions always survive, so a rollback target always exists |
| `delete-untagged-after-7d` | Delete | `tagState: untagged`, `olderThan: 7d` | Leftovers (failed pushes, versions whose tag was deleted) go after a week |
| `delete-tagged-after-30d` | Delete | `tagState: tagged`, `olderThan: 30d` | Old releases go after a month, unless they're among the 5 newest |

- **Keep always wins over Delete.** A version is deleted only if a Delete rule matches and no Keep rule protects it.
- **No `packageNamePrefixes`** means each rule applies to every image in the repository; `keepCount` counts per image.
- **Google stores durations in seconds:** `7d` comes back as `604800s`, `30d` as `2592000s`. Enum values come back
  uppercased and rules are listed alphabetically.

**Dry run first.** With `--dry-run` the policy is saved and evaluated on schedule, but deletions are only logged,
never executed:
```bash
gcloud artifacts repositories set-cleanup-policies exit-ticket --location=us-east4 --policy=infra/artifact-registry/cleanup-policy.json --dry-run
gcloud artifacts repositories list-cleanup-policies exit-ticket --location=us-east4
gcloud artifacts repositories describe exit-ticket --location=us-east4 --format="yaml(cleanupPolicies,cleanupPolicyDryRun)"
```

Then for real:
```bash
gcloud artifacts repositories set-cleanup-policies exit-ticket --location=us-east4 --policy=infra/artifact-registry/cleanup-policy.json --no-dry-run
```

`describe` then prints `null` for `cleanupPolicyDryRun`: the API leaves out fields that are false.

---

## 5. Docker authentication without keys

```bash
gcloud auth configure-docker us-east4-docker.pkg.dev
```

This adds one entry to `~/.docker/config.json`:
```json
"credHelpers": { "us-east4-docker.pkg.dev": "gcloud" }
```

- For that host only, Docker runs `docker-credential-gcloud`, which asks gcloud for a **short-lived OAuth access
  token** (about an hour) from your `gcloud auth login`. A fresh one is fetched for every push or pull.
- **No key file, no stored password.** Other registries (Docker Hub) still use the macOS Keychain
  (`credsStore: desktop`).
- **Name the host explicitly.** Without it, `configure-docker` adds a dozen `gcr.io` and regional hosts we don't use.
- This is Phase 1's [no-keys rule](phase-1-foundations.md#5-why-we-use-no-service-account-keys) in practice: you
  push as yourself; Cloud Build and GKE nodes will authenticate as their own service accounts with tokens Google
  issues automatically.

---

## 6. Building and pushing

[`scripts/push-images.sh`](../scripts/push-images.sh) runs from anywhere in the repo, with Docker running:
```bash
scripts/push-images.sh
```

What it does:
1. **Refuses a dirty working tree** (`git status --porcelain`), so a tag always matches committed code.
2. **Reads the project and region from the active gcloud configuration** at run time, so no project ID is ever
   committed.
3. **Tags with the short SHA** (`git rev-parse --short HEAD`) and labels the image with the full SHA.
4. **Skips a tag that already exists** (tags are immutable).
5. **Builds and pushes in one step** with `docker buildx build --platform linux/amd64 --push`.
6. **Prints each image by tag and by digest.**

Details worth knowing:
- **`set -euo pipefail`:** `-e` stops at the first failing command, `-u` makes an unset variable an error instead of
  an empty string, and `pipefail` makes a pipeline fail if any part of it fails.
- **`--push`:** BuildKit sends the result straight to the registry; nothing is loaded into local Docker.
- **`--provenance=false --sbom=false`:** pushes one plain `linux/amd64` manifest. By default buildx adds untagged
  attestation manifests, which the untagged cleanup rule would treat as leftovers.
- **Speed on an M2:** `RUN` steps run under QEMU emulation, but cached layers skip that entirely. The first push
  uploads every layer (about 71 MB + 26 MB, base images included); later pushes upload only changed layers.

First push (commit `ac93f06`):

| Image | Digest | Size |
|---|---|---|
| `api` | `sha256:294082fd63314145a3693fffb52cb7ab6d368824ea67c724b6c67ec1bb02de7a` | 71.1 MB |
| `web` | `sha256:e0db2a9b347f5c4064d753de9cb21c76cad647d9368fc4d665be5312e467da12` | 25.6 MB |

---

## 7. Verifying

Images and tags:
```bash
gcloud artifacts docker images list us-east4-docker.pkg.dev/PROJECT_ID/exit-ticket --include-tags
gcloud artifacts docker tags list us-east4-docker.pkg.dev/PROJECT_ID/exit-ticket/api
```

Platform and commit label, read from the registry:
```bash
docker buildx imagetools inspect us-east4-docker.pkg.dev/PROJECT_ID/exit-ticket/api:SHA
docker buildx imagetools inspect us-east4-docker.pkg.dev/PROJECT_ID/exit-ticket/api:SHA --format '{{.Image.OS}}/{{.Image.Architecture}} {{index .Image.Config.Labels "org.opencontainers.image.revision"}}'
```
Expect `MediaType: application/vnd.oci.image.manifest.v1+json` (a single image, not a list) and
`linux/amd64 <full commit SHA>`.

Pull by digest:
```bash
docker pull --platform linux/amd64 us-east4-docker.pkg.dev/PROJECT_ID/exit-ticket/web@DIGEST
docker image inspect us-east4-docker.pkg.dev/PROJECT_ID/exit-ticket/web@DIGEST --format '{{.Os}}/{{.Architecture}} {{json .RepoDigests}}'
```

Your own actions in the Admin Activity audit log:
```bash
gcloud logging read 'logName="projects/PROJECT_ID/logs/cloudaudit.googleapis.com%2Factivity" AND protoPayload.serviceName="artifactregistry.googleapis.com"' --freshness=1d --format="table(timestamp, protoPayload.authenticationInfo.principalEmail, protoPayload.methodName, protoPayload.resourceName)"
```

| Entry | Meaning |
|---|---|
| `CreateRepository` on `.../locations/us-east4` | The request starting (a long-running operation logs its start…) |
| `CreateRepository` on `.../repositories/exit-ticket` | …and its end |
| `UpdateRepository` × 2 | The dry-run policy, then the real one |
| (no push entries) | Pushes and pulls are **data** operations, recorded only in Data Access logs (off by default, billable) |

---

## 8. Cost and vulnerability scanning

**What Artifact Registry bills for:**
- **Storage**, per GB per month, with a monthly free allowance. Layers shared between versions are stored once.
- **Network transfer** out of the region: pulls from other regions, or to the internet, including your laptop.
  Same-region pulls (GKE in `us-east4`) aren't billed as transfer.
- There is **no charge for the repository existing**.

Current prices: https://cloud.google.com/artifact-registry/pricing

**Vulnerability scanning** (Artifact Analysis) checks images for known CVEs in OS and language packages.
- **Automatic scanning** needs `containerscanning.googleapis.com`. Once enabled, every pushed image is scanned and
  **billed per image**. **On-demand scanning** (`gcloud artifacts docker images scan`) uses
  `ondemandscanning.googleapis.com`.
- **Not enabled yet**, so pushes during development cost nothing extra. It belongs in the Cloud Build pipeline later.
- Pricing: https://cloud.google.com/artifact-analysis/pricing

---

## 9. How GKE will pull these images (Phase 3C preview)

- **The node pulls the image, not the pod.** The kubelet uses the **node's service account**. The pod's Workload
  Identity is for calling Google APIs (Secret Manager, Cloud SQL), not for pulling images.
- **Needed role:** `roles/artifactregistry.reader`, granted on the `exit-ticket` repository (least privilege).
- **By default** Autopilot nodes run as the **Compute Engine default service account**
  (`PROJECT_NUMBER-compute@developer.gserviceaccount.com`). In a project with no organization it usually has
  **Editor**, so pulls would work, but far more access than a node needs.
- **Plan for 3C:** a dedicated node service account with `roles/container.defaultNodeServiceAccount` (logging and
  monitoring) and `roles/artifactregistry.reader` on the repository.
- **Nothing is granted in 3B:** there's no cluster yet, and in a single project there's no cross-project access to
  set up.

---

## 10. Teardown

Delete one image version (its immutable tag goes with it):
```bash
gcloud artifacts docker images delete us-east4-docker.pkg.dev/PROJECT_ID/exit-ticket/api@DIGEST --delete-tags
```

Delete the whole repository, with every image and the cleanup policy:
```bash
gcloud artifacts repositories delete exit-ticket --location=us-east4
```

Remove the local copies:
```bash
docker image rm us-east4-docker.pkg.dev/PROJECT_ID/exit-ticket/web@DIGEST
```

**Leaving it in place while idle** costs only the storage of about 97 MB (check the pricing page against the free
allowance). Unlike GKE or Cloud SQL, there's no hourly charge, so it stays up between sessions.

---

## 11. Quirks

- **Wide tables wrap.** `repositories list` printed only its header because the row wrapped; narrow the table with
  `--format="table(name.basename(),format,mode,sizeBytes)"`.
- **Two time zones.** `docker images list` shows **local time** (`20:56:59` EDT); audit logs show **UTC**
  (`00:56:59Z`). Same moment.
- **Sizes are in bytes.** `71103625` is 71.1 MB.
- **A `%` after command output** is zsh marking a line that ended without a newline, not part of the output.
- **The `default` gcloud configuration** points at the same project but region `us-east1`. Every command here names
  `--location` explicitly so the active configuration can't send it to the wrong region.
- **The credential helper runs gcloud behind the scenes.** Any Docker command that touches
  `us-east4-docker.pkg.dev` (`pull`, `push`, `buildx imagetools inspect`) calls `docker-credential-gcloud` for a
  token.

---

## 12. Lessons learned

| What happened | Cause | Lesson |
|---|---|---|
| A test `imagetools inspect` of a made-up registry path ran gcloud indirectly | The credential helper now handles that host | Docker commands against the registry are gcloud commands in disguise |
| `cleanupPolicyDryRun` printed `null` | The API omits false fields | Absent means false |
| Pushing the same commit twice would fail | Immutable tags + rebuilds produce new digests | Check for the tag first and skip |
| Untagged attestation manifests would appear next to each image | buildx's default `--provenance` | `--provenance=false --sbom=false` for plain single-platform images |
| No push entries in Admin Activity logs | Pushes are data operations | Admin Activity = who changed the setup; Data Access = who used it |

---

## ACE exam notes

**Artifact Registry basics**
- **Replaces Container Registry** (`gcr.io`, shut down). Exam answers about storing images point to Artifact
  Registry.
- **Formats:** Docker, Maven, npm, Python, apt, yum, Go, generic and more. **Modes:** standard, remote (caches an
  upstream such as Docker Hub), virtual (one endpoint over several repositories).
- **Location:** a region or multi-region, fixed at creation. Keep it in the same region as the workloads that pull.
- **Image path:** `LOCATION-docker.pkg.dev/PROJECT_ID/REPOSITORY/IMAGE:TAG` or `...IMAGE@sha256:DIGEST`.

**Authentication**
- `gcloud auth configure-docker LOCATION-docker.pkg.dev` registers gcloud as Docker's **credential helper**: short-
  lived tokens from your login, no keys.
- Automated pushers (Cloud Build) and pullers (GKE nodes) use their **service accounts**. Never download a key.

**IAM roles** (grant at the repository level where possible)

| Role | Can |
|---|---|
| `roles/artifactregistry.reader` | Pull (read) artifacts: GKE nodes, Cloud Run |
| `roles/artifactregistry.writer` | Reader + push: Cloud Build, CI |
| `roles/artifactregistry.repoAdmin` | Writer + delete artifacts and manage the repository's contents |
| `roles/artifactregistry.admin` | Everything, including creating and deleting repositories and setting IAM |

- **Pull failures on GKE** (`ImagePullBackOff`, 403) are fixed on the **node service account**, not the Kubernetes
  service account.

**Tags vs digests**
- A tag is a movable label; a digest identifies exact content. Deploy by digest for certainty.
- **Immutable tags** stop a tag from being moved; it can only be deleted.

**Cleanup policies**
- Rules are **Keep** or **Delete**; **Keep wins**. Conditions include `tagState`, `tagPrefixes`,
  `packageNamePrefixes`, `olderThan`, `newerThan`, and `mostRecentVersions.keepCount`.
- Apply with `gcloud artifacts repositories set-cleanup-policies --policy=FILE`; use `--dry-run` first.
- They run in the background (about daily), not at the moment you apply them.

**Cost**
- Storage (per GB-month) plus network transfer out of the region. Labels on the repository show up in billing
  reports.
- Vulnerability scanning is a **separate, billed** API (`containerscanning.googleapis.com`).

**Audit logs**
- Repository create/update/delete appear in **Admin Activity** (always on, free). Image pushes and pulls are **Data
  Access** events (off by default).
