# Phase 4B — Secret Manager and Workload Identity

Phase 4B moves the app's secrets to **Secret Manager**, sets up the identities the pods and nodes will use
(**Workload Identity Federation for GKE**, a dedicated node service account), and prepares the code and manifests:
the app reads secrets from mounted files, and the api and migrate pods get the **Cloud SQL Auth Proxy** as a native
sidecar. Nothing bills: no cluster yet, and Cloud SQL stays stopped. The first deploy is the next part.

| Part | What was built | PR |
|---|---|---|
| **4B** | `session-secret`, `teacher-passcode`; node SA `exit-ticket-nodes`; Workload Identity and per-secret IAM; `config.py` reads secret files (+ tests); `serviceaccount.yaml`, `secretproviderclass.yaml`, proxy sidecar, `DATABASE_URL` in the ConfigMap; `db.yaml` and `create-k8s-secrets.sh` removed | (this PR) |

> **About the commands in this doc.** Code blocks contain commands only, no `#` comments. Run them from the repo
> root.

> **Placeholders**
>
> | Placeholder | Meaning |
> |---|---|
> | `PROJECT_ID` | Your GCP project ID (the commands use `$(gcloud config get project)`) |
> | `PROJECT_NUMBER` | The numeric project number |

**Contents**

1. [Secrets and versions](#1-secrets-and-versions)
2. [Creating the secrets](#2-creating-the-secrets)
3. [How a pod authenticates without a key](#3-how-a-pod-authenticates-without-a-key)
4. [IAM bindings](#4-iam-bindings)
5. [From Secret Manager to the container](#5-from-secret-manager-to-the-container)
6. [The manifests](#6-the-manifests)
7. [What became obsolete](#7-what-became-obsolete)
8. [Checks before committing](#8-checks-before-committing)
9. [Before the first deploy](#9-before-the-first-deploy)
10. [Lessons learned](#10-lessons-learned)
11. [Interview talking points](#11-interview-talking-points)
12. [ACE exam notes](#ace-exam-notes)
13. [Sources](#sources)

---

## 1. Secrets and versions

- A **secret** holds metadata: name, replication policy, labels, IAM. A **version** (1, 2, 3…) holds an
  **immutable** value and is enabled → disabled → destroyed (permanent).
- Read by number or `latest`. The manifests **pin numbers**, so a rotation is a deliberate manifest change (like
  deploying images by digest).

| Replication | Automatic | User-managed (ours) |
|---|---|---|
| Where | Google picks (global) | Listed regions: `us-east4` |
| Why | Simplest, most available | Data residency; CMEK per region |
| Changeable | **No, fixed at creation** | **No** |

All three secrets (`postgres-admin-password` from [4A](phase-4a-cloud-sql.md), and the two below) are user-managed
in `us-east4`.

---

## 2. Creating the secrets

[`scripts/create-app-secrets.sh`](../scripts/create-app-secrets.sh) creates `session-secret` (generated) and
`teacher-passcode` (from `app/api/.env`, so the passcode you know keeps working). Values reach gcloud through a pipe
(`--data-file=-`): never a command line, the shell history, a file or the repo. `printf` is a shell builtin, so the
passcode isn't another program's argument either. It refuses to overwrite existing secrets.

```bash
scripts/create-app-secrets.sh
gcloud secrets list
```
Three secrets, all `user_managed`, `us-east4`.

### Checking a value without printing it

```bash
gcloud secrets versions describe 1 --secret=session-secret --format="value(state,createTime)"
gcloud secrets versions access 1 --secret=session-secret | wc -c
gcloud secrets versions access 1 --secret=teacher-passcode | shasum -a 256
sed -n 's/^TEACHER_PASSCODE=//p' app/api/.env | tr -d '\n' | shasum -a 256
```
`ENABLED`; **64** characters (48 random bytes, base64, no newline); the two SHA-256 fingerprints **match**, proving
Secret Manager holds exactly the `.env` passcode without showing it. Metadata, length and fingerprints are the safe
checks; plain `versions access` puts the value on screen and in scrollback.

To rotate later (then bump the version in `k8s/secretproviderclass.yaml` and restart the api):
```bash
openssl rand -base64 48 | tr -d '\n' | gcloud secrets versions add session-secret --data-file=-
```

---

## 3. How a pod authenticates without a key

```
Pod running as KSA exit-ticket/api
  │ asks for a token at the metadata address (intercepted on the node)
  ▼
GKE metadata server ──► Security Token Service: the KSA's Kubernetes token
  │                     becomes a Google federated token for the principal
  │   principal://iam.googleapis.com/projects/PROJECT_NUMBER/locations/global/
  │     workloadIdentityPools/PROJECT_ID.svc.id.goog/subject/ns/exit-ticket/sa/api
  │
  ├─ Secret Manager add-on: uses the principal directly → secretAccessor on each secret
  └─ Cloud SQL Auth Proxy: the KSA annotation names exit-ticket-api@… → IAM Credentials
       issues a token for that Google SA (allowed by workloadIdentityUser) → Cloud SQL
```

| Part of the principal | Meaning |
|---|---|
| `projects/PROJECT_NUMBER` | The project **number**, not the ID |
| `workloadIdentityPools/PROJECT_ID.svc.id.goog` | The project's one workload identity pool, created with its first GKE cluster |
| `subject/ns/exit-ticket/sa/api` | Namespace and KSA name |

- **Identity sameness:** a KSA `api` in namespace `exit-ticket` is the same principal in every cluster of the
  project.
- The older member format `serviceAccount:PROJECT_ID.svc.id.goog[exit-ticket/api]` names the same KSA; it's used for
  `roles/iam.workloadIdentityUser`.
- **Why no key file:** tokens last about an hour, are issued only to pods running as that KSA, are revoked by
  removing a binding, and are logged. A JSON key works for anyone who copies it, from anywhere, until deleted.

**One design change:** the Secret Manager add-on's documentation authenticates as the **KSA principal** and doesn't
cover the impersonation annotation. So `secretAccessor` goes to the KSA principal; the Google SA keeps only Cloud
SQL. Each identity has exactly one job.

---

## 4. IAM bindings

### Node service account (used at cluster creation)

```bash
gcloud iam service-accounts create exit-ticket-nodes --display-name="Exit Ticket GKE nodes" --description="Node identity: pull images, write logs and metrics"
gcloud projects add-iam-policy-binding $(gcloud config get project) --member="serviceAccount:exit-ticket-nodes@$(gcloud config get project).iam.gserviceaccount.com" --role="roles/container.defaultNodeServiceAccount" --condition=None
gcloud artifacts repositories add-iam-policy-binding exit-ticket --location=us-east4 --member="serviceAccount:exit-ticket-nodes@$(gcloud config get project).iam.gserviceaccount.com" --role="roles/artifactregistry.reader"
```
`artifactregistry.reader` is on the **repository**, not the project.

### Workload Identity (the proxy)

```bash
gcloud iam service-accounts add-iam-policy-binding exit-ticket-api@$(gcloud config get project).iam.gserviceaccount.com --role="roles/iam.workloadIdentityUser" --member="serviceAccount:$(gcloud config get project).svc.id.goog[exit-ticket/api]"
```

### Each secret (the add-on)

```bash
PROJECT_NUMBER="$(gcloud projects describe $(gcloud config get project) --format='value(projectNumber)')"
KSA_PRINCIPAL="principal://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$(gcloud config get project).svc.id.goog/subject/ns/exit-ticket/sa/api"
gcloud secrets add-iam-policy-binding session-secret --member="$KSA_PRINCIPAL" --role="roles/secretmanager.secretAccessor"
gcloud secrets add-iam-policy-binding teacher-passcode --member="$KSA_PRINCIPAL" --role="roles/secretmanager.secretAccessor"
```

### The result

| Principal | Role | On |
|---|---|---|
| KSA principal `…/subject/ns/exit-ticket/sa/api` | `secretmanager.secretAccessor` | `session-secret`, `teacher-passcode` (one binding each) |
| `serviceAccount:PROJECT_ID.svc.id.goog[exit-ticket/api]` | `iam.workloadIdentityUser` | Google SA `exit-ticket-api` |
| You | `iam.serviceAccountTokenCreator` | Google SA `exit-ticket-api` (4A) |
| Google SA `exit-ticket-api` | `cloudsql.client`, `cloudsql.instanceUser` | Project (4A; Cloud SQL has no per-instance IAM) |
| Google SA `exit-ticket-nodes` | `container.defaultNodeServiceAccount` | Project |
| Google SA `exit-ticket-nodes` | `artifactregistry.reader` | Repository `exit-ticket` |

```bash
gcloud secrets get-iam-policy session-secret
gcloud secrets get-iam-policy teacher-passcode
gcloud secrets get-iam-policy postgres-admin-password
gcloud artifacts repositories get-iam-policy exit-ticket --location=us-east4
```
`postgres-admin-password` returned only `etag: ACAB` (an empty policy): no app identity can read it.

---

## 5. From Secret Manager to the container

```
Secret Manager session-secret/versions/1
  → GKE Secret Manager add-on (CSI driver on the node, as the pod's KSA principal)
  → in-memory, read-only volume: /var/secrets/exit-ticket/session_secret
  → app/api/app/config.py reads the file at startup
```
**A mounted file, not an environment variable.** Environment variables leak into child processes, crash reports and
`/proc/*/environ`; these files are read-only and in memory, and the values never pass through etcd.

### The code change

[`config.py`](../app/api/app/config.py) gains a third source. First match wins:

| Order | Source | Where it's used |
|---|---|---|
| 1 | Environment variables | Tests (`conftest.py`), any override |
| 2 | `app/api/.env` | Local dev, unchanged |
| 3 | Files in `/var/secrets/exit-ticket`, named after the setting | GKE |

`secrets_dir` is set only if the folder exists (`existing_dir`), so local runs don't warn. pydantic-settings strips a
trailing newline, and the file values go through the same validation (length, not the `.env.example`
placeholder). [`tests/test_config.py`](../app/api/tests/test_config.py) covers all four behaviours.

Tested in the built image too, with GKE's restrictions (user 10001, read-only root, no capabilities): with a fake
secrets folder mounted read-only it read both values (printed lengths only); without the mount it refused to start
(`Field required`).

---

## 6. The manifests

| File | What |
|---|---|
| [`serviceaccount.yaml`](../k8s/serviceaccount.yaml) | KSA `api`, annotation `iam.gke.io/gcp-service-account: exit-ticket-api@PROJECT_ID.iam.gserviceaccount.com`, no Kubernetes API token |
| [`secretproviderclass.yaml`](../k8s/secretproviderclass.yaml) | `provider: gke`; `projects/PROJECT_ID/secrets/session-secret/versions/1` → file `session_secret`, the same for `teacher-passcode` |
| [`configmap.yaml`](../k8s/configmap.yaml) | Adds `DATABASE_URL: postgresql+psycopg://exit-ticket-api%40PROJECT_ID.iam@127.0.0.1:5432/exitticket` (no password); applied with `sed` now |
| [`api.yaml`](../k8s/api.yaml), [`migrate-job.yaml`](../k8s/migrate-job.yaml) | `serviceAccountName: api`; proxy sidecar; CSI volume `app-secrets` at `/var/secrets/exit-ticket`; no `secretKeyRef` |

### The proxy as a native sidecar

```yaml
initContainers:
  - name: cloud-sql-proxy
    image: gcr.io/cloud-sql-connectors/cloud-sql-proxy@sha256:1d2285ce…
    restartPolicy: Always
    args: [--auto-iam-authn, --structured-logs, --port=5432, --health-check,
           --http-address=0.0.0.0, --http-port=9090, --exit-zero-on-sigterm,
           PROJECT_ID:us-east4:exit-ticket-db]
```
- **`restartPolicy: Always` in `initContainers`** makes it a native sidecar: it starts first, the main container
  waits for its **startup probe** (`/startup` on 9090), it runs alongside, and Kubernetes stops it when the main
  container exits, so **the migrate Job completes**.
- Pinned to the **linux/amd64** image of 2.26.3 by digest.
- `--http-address=0.0.0.0`: the kubelet probes the pod IP, not localhost.
- 50m CPU / 128Mi per pod (Autopilot bills it): the api pod is now 150m / 384Mi.

---

## 7. What became obsolete

| Removed | Replaced by |
|---|---|
| `scripts/create-k8s-secrets.sh` | `scripts/create-app-secrets.sh` (Secret Manager); no database password at all (IAM auth) |
| `k8s/db.yaml` | Cloud SQL `exit-ticket-db` ([4A](phase-4a-cloud-sql.md)) |
| Kubernetes Secrets `api-secrets`, `db-secrets` | Secret Manager + the add-on |

`scripts/push-images.sh` and `scripts/seed.py` stay. [k8s/README.md](../k8s/README.md) has the new apply order.

---

## 8. Checks before committing

```bash
cd ~/projects/exit-ticket-5th-grade-math-science/app/api
.venv/bin/pytest -q
DATABASE_URL="postgresql+psycopg://exitticket:localdev@localhost:5432/exitticket_test" .venv/bin/alembic check
cd ~/projects/exit-ticket-5th-grade-math-science
docker run --rm -v "$PWD/k8s:/k8s:ro" ghcr.io/yannh/kubeconform:v0.7.0 -strict -summary -schema-location default -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' /k8s/namespace.yaml /k8s/configmap.yaml /k8s/serviceaccount.yaml /k8s/secretproviderclass.yaml /k8s/api.yaml /k8s/web.yaml /k8s/migrate-job.yaml /k8s/ingress.yaml
```

| Check | Result |
|---|---|
| pytest (compose Postgres running) | **91 passed** (4 new) |
| `alembic check` (against the test database, not dev data) | `No new upgrade operations detected.` (`0002 (head)`) |
| kubeconform (strict + the CRD schema catalog for `SecretProviderClass`) | **10/10 valid, 0 skipped** |
| shellcheck `create-app-secrets.sh` | Clean |

---

## 9. Before the first deploy

- **The api image must be rebuilt.** `api.yaml` and `migrate-job.yaml` still pin the Phase 3B digest, built before
  `config.py` could read files. After this merges: `scripts/push-images.sh`, then update both digests.
- Cluster creation needs `--service-account=exit-ticket-nodes@PROJECT_ID.iam.gserviceaccount.com` and
  `--enable-secret-manager`.
- To confirm on the first deploy: the add-on mounts with the KSA principal grant; Workload Identity works with
  `automountServiceAccountToken: false`; Autopilot's resource adjustments for the sidecar.

---

## 10. Lessons learned

| What happened | Cause | Lesson |
|---|---|---|
| The design's "add-on via impersonation" didn't match the docs | The add-on authenticates as the KSA principal | Check the integration's own docs before granting; split roles by who actually calls the API |
| The manifests point at an image that can't read the new secret files | The image predates the code change | A config change is a deploy: rebuild, push, update digests |
| `postgres-admin-password` policy is just `etag: ACAB` | No bindings; Owner inherits from the project | An empty policy is a good result for an admin secret |

---

## 11. Interview talking points

**1. "No keys, no Kubernetes Secrets."** Pods get Google credentials from Workload Identity; the secrets are mounted
from Secret Manager as in-memory files, so nothing sensitive is in the repo, in etcd or in environment variables.

**2. "One identity per job."** The KSA principal can read exactly two secrets; the Google SA can only log in to Cloud
SQL; the node SA can only pull from one repository and write logs. The admin password has no app access at all.

**3. "Verify without revealing."** I checked secrets by state, length and SHA-256 fingerprint, never by printing them.

**4. "Native sidecars for Jobs."** The Auth Proxy runs as a native sidecar, so the migration Job completes on its own
and the app never races the database tunnel.

---

## ACE exam notes

**Secret Manager**
- Roles: **`secretAccessor`** (read values), **`viewer`** (metadata only, no values), `secretVersionAdder`,
  `secretVersionManager`, **`admin`** (everything incl. IAM). Grant per secret.
- Versions are immutable; `latest` alias; enable / disable / destroy.
- Replication automatic or user-managed, fixed at creation.
- **CMEK**: a Cloud KMS key you control (`--kms-key-name`); the Secret Manager service agent needs
  `roles/cloudkms.cryptoKeyEncrypterDecrypter`; disabling the key makes the secret unreadable.
- Rotation schedules only publish a Pub/Sub notification.

**Audit logs**
- Creating secrets, adding versions, IAM changes: **Admin Activity** (always on, free).
- **Reading a value (`AccessSecretVersion`) is Data Access (DATA_READ): off by default**, billed when on. Enable per
  service in IAM & Admin → Audit Logs. "Who read this secret?" → Data Access logs.

**Workload Identity Federation for GKE**
- The GKE answer to "how does a pod authenticate without keys": a KSA maps to an IAM principal
  (`principal://…/subject/ns/NS/sa/KSA`), or to a Google SA via `roles/iam.workloadIdentityUser` and the annotation
  `iam.gke.io/gcp-service-account`.
- Wrong answers: a mounted JSON key; relying on the node's service account (every pod on the node shares it).
- On by default in Autopilot; on Standard: `--workload-pool=PROJECT_ID.svc.id.goog` per cluster, plus per node pool.
- Node SA ≠ pod identity; least-privilege node SA: `roles/container.defaultNodeServiceAccount` + Artifact Registry
  reader.

---

## Sources

- [Use the Secret Manager add-on with GKE](https://docs.cloud.google.com/secret-manager/docs/secret-manager-managed-csi-component)
- [Synchronize secrets to Kubernetes Secrets](https://docs.cloud.google.com/secret-manager/docs/sync-k8-secrets)
- [Access secrets stored outside GKE clusters using client libraries](https://cloud.google.com/kubernetes-engine/docs/tutorials/workload-identity-secrets)
- [Authenticate to Google Cloud APIs from GKE workloads](https://cloud.google.com/kubernetes-engine/docs/tutorials/authenticating-to-cloud-platform)
