# Phase 4 — Design: Cloud SQL, Secret Manager, Workload Identity

Phase 4 replaces the temporary in-cluster Postgres with **Cloud SQL for PostgreSQL**, and the Kubernetes Secrets
with **Secret Manager**, read by the pods through **Workload Identity Federation for GKE**. This document is the
approved design. The build happens in later sessions, each with its own doc: [Phase 4A](phase-4a-cloud-sql.md)
(Cloud SQL) so far.

| Part | What | PR |
|---|---|---|
| **4 design** | Options, trade-offs, cost and the decisions below | (this PR) |

> **Commands in this doc are planned, not yet run.** They show the shape of each step; each one is re-checked
> with `--help` when it's run. No code block contains `#` comments (zsh treats `#` as an argument).

> **Placeholders**
>
> | Placeholder | Meaning |
> |---|---|
> | `PROJECT_ID` | Your GCP project ID (`gcloud config get project`) |
> | `PROJECT_NUMBER` | The numeric project number |

**Contents**

1. [Decisions at a glance](#1-decisions-at-a-glance)
2. [Target architecture](#2-target-architecture)
3. [Cloud SQL instance](#3-cloud-sql-instance)
4. [Connecting: Auth Proxy, network, database auth](#4-connecting-auth-proxy-network-database-auth)
5. [Secrets](#5-secrets)
6. [Identity and IAM](#6-identity-and-iam)
7. [Migrations and native sidecars](#7-migrations-and-native-sidecars)
8. [Local development](#8-local-development)
9. [Tooling: gcloud now, Terraform later](#9-tooling-gcloud-now-terraform-later)
10. [Cost](#10-cost)
11. [Teardown policy (enforced)](#11-teardown-policy-enforced)
12. [Build plan](#12-build-plan)
13. [Known gaps and things to verify](#13-known-gaps-and-things-to-verify)
14. [ACE exam notes](#ace-exam-notes)
15. [Sources](#sources)

---

## 1. Decisions at a glance

| Area | Decision |
|---|---|
| **Instance** | `exit-ticket-db`: Postgres **16**, **`--edition=enterprise`**, `db-f1-micro`, zonal in us-east4-a, SSD 10 GB, auto-increase off, daily backups (7 kept), PITR off, deletion protection on |
| **Network** | Public IP with **no authorized networks** and **connector enforcement REQUIRED**; private IP deferred to the Terraform phase |
| **Connection** | Cloud SQL Auth Proxy v2 as a **native sidecar** in the api Deployment and the migrate Job; the app connects to `127.0.0.1:5432` |
| **Database auth** | **IAM database authentication** for the app (no app password); the built-in `postgres` user keeps a password for admin use only |
| **Secrets** | `session-secret`, `teacher-passcode` (and `postgres-admin-password`, for you only) in Secret Manager, **pinned versions**, mounted as files by the **GKE Secret Manager add-on**; the app reads them through pydantic-settings `secrets_dir` |
| **`DATABASE_URL`** | No password any more, so it moves to the ConfigMap |
| **Pod identity** | Kubernetes SA `api` (api and migrate) **impersonates** Google SA `exit-ticket-api` |
| **Node identity** | Dedicated Google SA `exit-ticket-nodes`, set at cluster creation (closes the Phase 3 Editor gap) |
| **Cluster** | Recreated with `--service-account` and `--enable-secret-manager`; `k8s/db.yaml` retired |
| **Local dev** | Compose Postgres daily; the Auth Proxy binary on the Mac only to test against Cloud SQL and to seed |
| **Tooling** | gcloud by hand; Terraform with imports in a later phase |
| **Cost control** | **Strict teardown every session** ([§11](#11-teardown-policy-enforced)) |

---

## 2. Target architecture

```
                       GKE Autopilot (us-east4)                                Google APIs
 ┌─────────────────────────────────────────────────────────────┐
 │ namespace exit-ticket                                       │
 │                                                             │
 │  web pod (nginx) ──/api/──► api pod                         │
 │                             ├─ api container                │
 │                             │    DATABASE_URL → 127.0.0.1   │
 │                             │    /var/secrets/* (CSI files) ◄──── Secret Manager
 │                             └─ cloud-sql-proxy (native      │       session-secret, teacher-passcode
 │                                  sidecar, --auto-iam-authn) ─────► Cloud SQL exit-ticket-db
 │                                                             │       (public IP, no authorized networks,
 │  migrate Job: alembic + the same proxy sidecar              │        connector enforcement REQUIRED)
 │                                                             │
 │  KSA "api" ──impersonates──► GSA exit-ticket-api            │
 │  nodes run as GSA exit-ticket-nodes (pull images, logs)     │
 └─────────────────────────────────────────────────────────────┘
```

Two identities, never mixed: the **node** identity pulls images and ships logs; the **pod** identity reads secrets
and connects to the database.

---

## 3. Cloud SQL instance

### Edition

| | Enterprise | Enterprise Plus |
|---|---|---|
| Smallest machine | Shared-core `db-f1-micro` (0.6 GB) | `db-perf-optimized-N-2` (2 vCPU, 16 GB) |
| Rough cost, always on | ~$8–10/month | ~$200+/month |
| SLA | 99.95% (**none for shared-core**) | 99.99% |

**gcloud defaults PostgreSQL 16+ to Enterprise Plus**, which has no shared-core tiers, so **`--edition=enterprise`
must be passed explicitly**.

### Settings

| Setting | Choice | Why |
|---|---|---|
| Tier | `db-f1-micro` | Cheapest; enough for synthetic classes. No SLA: dev only |
| Version | `POSTGRES_16` | Matches compose and the Phase 3C temporary database |
| Availability | Zonal, us-east4-a | HA (regional, synchronous standby, automatic failover) doubles the cost. A read replica is **not** HA |
| Storage | SSD, 10 GB (the minimum) | Storage type can't be changed later; HDD is slower |
| Auto-increase | Off | Storage can grow but **never shrink**; a few MB of data doesn't need it |
| Backups | Daily, 7 kept | Cents per month; restore practice |
| PITR | Off | Re-seedable synthetic data; saves WAL storage |
| Deletion protection | On | Delete needs an explicit `patch --no-deletion-protection` first |
| Flags | `cloudsql.iam_authentication=on` | Required for IAM database auth |

Create command, as run in [Phase 4A](phase-4a-cloud-sql.md) (`--region` and `--zone` are mutually exclusive; the
GA command has no `--labels`, so labels are added with `gcloud beta sql instances patch --update-labels`):
```bash
gcloud sql instances create exit-ticket-db --database-version=POSTGRES_16 --edition=enterprise --tier=db-f1-micro --zone=us-east4-a --availability-type=zonal --storage-type=SSD --storage-size=10GB --no-storage-auto-increase --backup-start-time=07:00 --retained-backups-count=7 --no-enable-point-in-time-recovery --maintenance-window-day=SUN --maintenance-window-hour=8 --database-flags=cloudsql.iam_authentication=on --connector-enforcement=REQUIRED --deletion-protection
```
The `postgres` password is set by prompt, never as a flag (flags land in shell history):
```bash
gcloud sql users set-password postgres --instance=exit-ticket-db --prompt-for-password
```

---

## 4. Connecting: Auth Proxy, network, database auth

### Auth Proxy, not the Python Connector

| | Auth Proxy sidecar | Python Connector |
|---|---|---|
| How | App connects to `127.0.0.1:5432`; the proxy opens an encrypted, IAM-authorized tunnel | A library inside the app |
| Code change | None (only `DATABASE_URL`) | Engine rewrite |
| Drivers | Any | Postgres: **pg8000, asyncpg only**; the app uses **psycopg 3** |
| Cost | One small container (~50m CPU / 128Mi, billed by Autopilot) | None |

### Public IP + proxy, not private IP (yet)

- **Public IP** with **no authorized networks**: nothing can connect directly. **Connector enforcement REQUIRED**
  accepts only Auth Proxy / Connector connections, which are encrypted and authorized by IAM
  (`roles/cloudsql.client`).
- **Private IP** needs private services access: `servicenetworking.googleapis.com` (not enabled), a reserved
  range, and VPC peering that is hard to undo; the Mac then can't reach the instance directly. It's the production
  best practice and moves in with Terraform.

### IAM database authentication

The app logs in **as the Google SA `exit-ticket-api`**: the proxy (`--auto-iam-authn`) fetches a short-lived
OAuth token and uses it as the password. **There is no app database password to store or rotate.**

| Piece | Value |
|---|---|
| Instance flag | `cloudsql.iam_authentication=on` |
| Database user | `exit-ticket-api@PROJECT_ID.iam` (the SA email **without** `.gserviceaccount.com`), type `cloud_iam_service_account` |
| IAM role | `roles/cloudsql.instanceUser` (permission `cloudsql.instances.login`) |
| `DATABASE_URL` | `postgresql+psycopg://exit-ticket-api%40PROJECT_ID.iam@127.0.0.1:5432/exitticket` (`@` in a user name is `%40`) |
| Privileges | A new IAM user has none; on Postgres 15+ not even `CREATE` on `public`. Plan: make it the **owner of `exitticket`** (as `postgres`: grant yourself the role, then `ALTER DATABASE … OWNER TO …`) |

Cloud SQL IAM database users can be users, service accounts or groups. A Workload Identity *principal* isn't
documented as one, which is why the pod identity impersonates a Google SA ([§6](#6-identity-and-iam)).

```bash
gcloud sql databases create exitticket --instance=exit-ticket-db
gcloud sql users create exit-ticket-api@PROJECT_ID.iam --instance=exit-ticket-db --type=cloud_iam_service_account
```

---

## 5. Secrets

### What goes where

| Value | Where | Why |
|---|---|---|
| `SESSION_SECRET` | Secret Manager `session-secret` | Signs login tokens |
| `TEACHER_PASSCODE` | Secret Manager `teacher-passcode` | Shared teacher sign-in |
| App database password | **None** | IAM database auth |
| `postgres` password | Secret Manager `postgres-admin-password`, accessible to you only | Break-glass admin; never mounted in the cluster |
| `DATABASE_URL` | ConfigMap (project ID filled in by `sed` at apply time) | No password in it any more |

Secrets are created from generated values piped on stdin, so no value appears on a command line or in history,
with replication pinned to us-east4 (the same region as everything else):
```bash
openssl rand -base64 48 | tr -d '\n' | gcloud secrets create session-secret --replication-policy=user-managed --locations=us-east4 --data-file=-
```

### How pods read them: the GKE Secret Manager add-on

| Option | Verdict |
|---|---|
| **Secret Manager add-on (CSI driver)** | **Chosen.** Google-managed, supported on Autopilot (`--enable-secret-manager`). A `SecretProviderClass` lists the secrets; the pod mounts them as files. Values never touch etcd |
| Sync to Kubernetes Secrets | Puts values back in etcd; first released as a preview |
| External Secrets Operator | A third-party operator to install and patch |
| App calls the Secret Manager API | Couples the app to GCP and complicates local dev |

App change: pydantic-settings reads a file per field from `secrets_dir`. Files named `session_secret` and
`teacher_passcode` fill the existing `Settings` fields; environment variables and the local `.env` keep working.
`secrets_dir` is set only when the mount folder exists, so local runs don't warn.

### Versions and rotation

- Versions are **immutable**: enabled → disabled → destroyed (permanent). References are a number or `latest`.
- **Pinned version numbers** in the `SecretProviderClass` (like deploying images by digest). To rotate: add a
  version, bump the number in the manifest, `kubectl rollout restart`. Rotating `SESSION_SECRET` signs everyone out.
- The add-on's auto-rotation stays off: the app reads settings once at startup.
- Secret Manager rotation **schedules only send a Pub/Sub notification**; they don't rotate anything.

---

## 6. Identity and IAM

### Pod identity: impersonating a Google SA

| | Direct grant to the KSA principal | **Impersonate a GSA (chosen)** |
|---|---|---|
| Member | `principal://iam.googleapis.com/projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/PROJECT_ID.svc.id.goog/subject/ns/exit-ticket/sa/api` | GSA `exit-ticket-api`, impersonated by `serviceAccount:PROJECT_ID.svc.id.goog[exit-ticket/api]` |
| Cloud SQL IAM database user | Not documented | **Yes** (a service account) |
| Setup | One step | GSA + `roles/iam.workloadIdentityUser` binding + KSA annotation `iam.gke.io/gcp-service-account` |

One KSA (`api`) for both the api Deployment and the migrate Job: same image, same database user, and alembic loads
the full settings. Splitting them is a known gap ([§13](#13-known-gaps-and-things-to-verify)).

### Role matrix

| Principal | Role | Granted on | Why |
|---|---|---|---|
| `serviceAccount:PROJECT_ID.svc.id.goog[exit-ticket/api]` | `roles/iam.workloadIdentityUser` | The GSA `exit-ticket-api` | The KSA may act as the GSA |
| GSA `exit-ticket-api` | `roles/cloudsql.client` | Project (Cloud SQL has no per-instance IAM) | The proxy may connect |
| GSA `exit-ticket-api` | `roles/cloudsql.instanceUser` | Project | IAM database login |
| GSA `exit-ticket-api` | `roles/secretmanager.secretAccessor` | **Each secret**: `session-secret`, `teacher-passcode` | Read only those two |
| You | `roles/iam.serviceAccountTokenCreator` | The GSA `exit-ticket-api` | Seed from the Mac as the app ([§8](#8-local-development)) |
| GSA `exit-ticket-nodes` | `roles/container.defaultNodeServiceAccount` | Project | Node logging, monitoring, metadata |
| GSA `exit-ticket-nodes` | `roles/artifactregistry.reader` | **The `exit-ticket` repository** | Pull images |

Attaching `exit-ticket-nodes` to the cluster needs `iam.serviceAccounts.actAs` on it (in
`roles/iam.serviceAccountUser`; you have it as Owner).

---

## 7. Migrations and native sidecars

A Job completes only when **all** its containers exit; an ordinary proxy sidecar never does, so the Job would hang
at `1/2 Running`. A **native sidecar** fixes it: the proxy goes in **`initContainers` with `restartPolicy: Always`**.

1. It starts before the main container; with a **startup probe** (proxy `--health-check --http-address=0.0.0.0`,
   path `/startup`) the main container waits until the tunnel is up, so alembic never races it.
2. It runs alongside the main container and is restarted if it crashes.
3. When the main container exits, Kubernetes stops the sidecar and the **Job completes**.
4. In the Deployment it's stopped **after** the app on shutdown, so in-flight queries finish.

Native sidecars were on by default from Kubernetes 1.29 and are GA since 1.33; the Phase 3C cluster ran 1.36.

Planned proxy arguments (image pinned by digest when the manifest is written):
```
--auto-iam-authn --structured-logs --port=5432 --health-check --http-address=0.0.0.0 PROJECT_ID:us-east4:exit-ticket-db
```

---

## 8. Local development

- **Daily work:** compose Postgres, unchanged.
- **Against Cloud SQL:** the Auth Proxy binary on the Mac (`brew install cloud-sql-proxy`; a local tool, so arm64 is
  fine). It uses Application Default Credentials from Phase 1.
- **Seeding** runs **as the app identity**, so every table has one owner:
  ```bash
  cloud-sql-proxy --auto-iam-authn --port=15432 --impersonate-service-account=exit-ticket-api@PROJECT_ID.iam.gserviceaccount.com PROJECT_ID:us-east4:exit-ticket-db
  ```
  then the seed script with `DATABASE_URL` pointing at `127.0.0.1:15432`, like the Phase 3C port-forward.

---

## 9. Tooling: gcloud now, Terraform later

Phase 4 is built **by hand with gcloud**, like Phase 3: one new service at a time, every API call visible, and the
ACE exam tests gcloud. A later phase writes Terraform (remote state in GCS) for the node SA, cluster, Cloud SQL,
secrets and IAM, **imports** what exists, and switches Cloud SQL to private IP.

---

## 10. Cost

Approximate list prices; check the [Pricing Calculator](https://cloud.google.com/products/calculator) for us-east4.
The budget (excluding credits, forecasted alert) is the backstop.

| Item | Running | Stopped / idle |
|---|---|---|
| Cloud SQL `db-f1-micro` compute | ~$0.011/hr | $0 (activation policy `NEVER`) |
| SSD storage, 10 GB | ~$1.70–2/month | **Still billed** |
| Instance IP address | Small hourly charge | **Still billed** |
| Backups | Cents/month | Still billed |
| GKE cluster fee | $0.10/hr (free-tier credit; counted by the budget) | $0 (deleted) |
| Autopilot pods: api + proxy + web | ~$0.015/hr | $0 |
| Ingress, if applied | ~$0.025/hr + data | $0 |
| Secret Manager (3 secrets), Google SAs, IAM | Free tier / free | Free |

A 3-hour build session: **~$0.40 gross, a few cents net**. Stopped between sessions: **a few dollars a month**
(storage, IP, backups). Deleted: **$0**. A deleted instance's name can be reused immediately, but **its backups are
deleted with it**.

---

## 11. Teardown policy (enforced)

**Goal: about $0 whenever the app isn't being used.** A session isn't finished until the checklist below has been
run and its output checked.

### End of every session

1. Ingress first (if applied), then the namespace, then the cluster ([phase-3c §11](phase-3c-gke.md#11-teardown)):
   ```bash
   kubectl delete -f k8s/ingress.yaml --wait=true
   kubectl delete namespace exit-ticket
   gcloud container clusters delete exit-ticket --region=us-east4
   ```
2. Stop Cloud SQL (compute billing stops; storage and IP continue):
   ```bash
   gcloud sql instances patch exit-ticket-db --activation-policy=NEVER
   ```
3. Stop any `cloud-sql-proxy` on the Mac (Ctrl+C).
4. Verify:
   ```bash
   gcloud container clusters list
   gcloud sql instances list
   gcloud compute forwarding-rules list
   gcloud compute backend-services list
   gcloud compute network-endpoint-groups list
   gcloud compute disks list
   gcloud compute addresses list
   gcloud compute instances list
   gcloud compute firewall-rules list --format="table(name,sourceRanges.list())"
   ```
   Healthy: no clusters; `exit-ticket-db` with `STATUS` `STOPPED`; every compute list empty; only the four
   `default-allow-*` firewall rules. (Don't check the API's `state` field: it stays `RUNNABLE` while stopped;
   `list` combines it with `activationPolicy`.)

### No session planned within 7 days

Delete the instance. The data is synthetic and re-seedable; the Google SAs, IAM bindings and secrets stay (free):
```bash
gcloud sql instances patch exit-ticket-db --no-deletion-protection
gcloud sql instances delete exit-ticket-db
gcloud sql instances list
```

### Start of every session

Check nothing was left running from last time: the verification list above, before creating anything.

---

## 12. Build plan

| Session | Work | Bills? |
|---|---|---|
| **A. Identities and secrets** | Create `exit-ticket-api` and `exit-ticket-nodes`; IAM bindings (§6); `session-secret`, `teacher-passcode`, `postgres-admin-password` | No (free) |
| **B. Cloud SQL** | Create the instance (§3), set the `postgres` password, create the database and IAM user, make it the database owner (Mac proxy as `postgres`); test an IAM login from the Mac; **stop the instance** | Instance while running |
| **C. App and manifests** (local) | `secrets_dir` in `config.py` + tests; `serviceaccount.yaml`, `secretproviderclass.yaml`, proxy sidecar in `api.yaml` and `migrate-job.yaml`, `DATABASE_URL` in the ConfigMap; remove `db.yaml` and `scripts/create-k8s-secrets.sh`; kubeconform | No |
| **D. Deploy** | Start Cloud SQL; create the cluster with the node SA and the add-on; apply; migrate; seed from the Mac; test; **full teardown (§11)** | Cluster, pods, instance |

Each session ends with §11. **Phase 4A** ([notes](phase-4a-cloud-sql.md)) did session B plus the `exit-ticket-api`
service account and its Cloud SQL roles from session A (the IAM database user needs the account to exist).

---

## 13. Known gaps and things to verify

| Gap | Why it's acceptable now | Later |
|---|---|---|
| One identity (and database user) for api and migrate, so the api has DDL rights | Same app; simpler grants | Separate KSAs/GSAs; migrate owns the schema, api gets DML only (`GRANT`, `ALTER DEFAULT PRIVILEGES`) |
| Public IP | No authorized networks + connector enforcement + IAM | Private IP (private services access) in the Terraform phase |
| Shared-core tier, no SLA | Dev with synthetic data | `db-custom-*` or Enterprise Plus, HA |
| Cloud SQL roles granted on the project | Cloud SQL has no per-instance IAM | IAM Conditions on `resource.name` |
| PITR off | Re-seedable data | On for anything real |

To confirm during the build:
- The Secret Manager add-on with a KSA that **impersonates** a GSA (the docs centre on direct principal grants).
  Fallback: grant `secretAccessor` on the two secrets to the KSA principal instead.
- `automountServiceAccountToken: false` still works with Workload Identity (the GKE metadata server fetches the
  token itself).
- Exact `gcloud sql instances create` flags against `--help` on the day.
- Autopilot's resource adjustments for the proxy sidecar (`autopilot.gke.io/resource-adjustment`).

---

## ACE exam notes

**Cloud SQL**
- Editions: Enterprise vs Enterprise Plus; **PostgreSQL 16+ defaults to Enterprise Plus** in gcloud/API.
- Shared-core tiers (`db-f1-micro`, `db-g1-small`) have **no SLA**.
- **HA** = regional instance, synchronous standby in another zone, automatic failover, same IP. **Read replicas
  aren't HA.**
- Storage: SSD or HDD (fixed at creation); grows (auto-increase) but **never shrinks**.
- Backups (automated + on-demand) enable restores; **PITR** needs backups + WAL. Deleting an instance deletes its
  backups unless a final backup is kept.
- Stop with `--activation-policy=NEVER`: compute stops billing; storage and IP don't.
- Connections: Auth Proxy / language connectors (IAM-authorized, encrypted); authorized networks for direct public
  IP; private IP via **private services access** (`servicenetworking`, VPC peering) or Private Service Connect.
- Roles: `cloudsql.client` (connect), `cloudsql.instanceUser` (IAM login), `cloudsql.editor`, `cloudsql.admin`.
  Granted on the project.
- The `postgres` user has `cloudsqlsuperuser`, not true superuser.

**Secret Manager**
- Secrets have immutable versions (enabled / disabled / destroyed); access with
  `gcloud secrets versions access latest --secret=NAME`.
- Replication: automatic or user-managed (chosen regions).
- `roles/secretmanager.secretAccessor` reads values; grant it per secret.
- Rotation schedules only publish to Pub/Sub.

**Workload Identity Federation for GKE**
- On by default in Autopilot. KSA → GSA: `roles/iam.workloadIdentityUser` on the GSA for
  `serviceAccount:PROJECT_ID.svc.id.goog[NAMESPACE/KSA]`, plus the annotation `iam.gke.io/gcp-service-account`.
- Or grant roles directly to the KSA's `principal://…` identifier.
- No keys anywhere; the GKE metadata server issues short-lived tokens.
- Node SA ≠ pod identity; set a least-privilege node SA at cluster creation (`--service-account`).

---

## Sources

- [Cloud SQL: Create instances (default edition)](https://docs.cloud.google.com/sql/docs/postgres/create-instance)
- [Cloud SQL for PostgreSQL release notes](https://docs.cloud.google.com/sql/docs/postgres/release-notes)
- [gcloud sql instances create](https://docs.cloud.google.com/sdk/gcloud/reference/sql/instances/create)
- [Cloud SQL editions overview](https://docs.cloud.google.com/sql/docs/postgres/editions-intro)
- [Cloud SQL pricing](https://cloud.google.com/sql/pricing)
- [Start, stop, and restart instances](https://docs.cloud.google.com/sql/docs/mysql/start-stop-restart-instance)
- [Cloud SQL FAQ](https://docs.cloud.google.com/sql/faq)
- [Manage users with IAM database authentication](https://cloud.google.com/sql/docs/postgres/add-manage-iam-users)
- [Identity federation: products and limitations](https://docs.cloud.google.com/iam/docs/federated-identity-supported-services)
- [Authenticate to Google Cloud APIs from GKE workloads](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/workload-identity)
- [Use the Secret Manager add-on with GKE](https://docs.cloud.google.com/secret-manager/docs/secret-manager-managed-csi-component)
- [Synchronize secrets to Kubernetes Secrets](https://docs.cloud.google.com/secret-manager/docs/sync-k8-secrets)
- [Cloud SQL language connectors](https://docs.cloud.google.com/sql/docs/postgres/connect-connectors)
- [Connect to Cloud SQL from GKE](https://docs.cloud.google.com/sql/docs/mysql/connect-kubernetes-engine)
