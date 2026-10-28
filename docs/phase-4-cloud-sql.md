# Phase 4 — Cloud SQL, Secret Manager, Workload Identity

Phase 4 replaced the temporary in-cluster Postgres with **Cloud SQL for PostgreSQL** and the Kubernetes Secrets with
**Secret Manager**, and gave every component its own keyless identity: the pods through **Workload Identity
Federation for GKE**, the nodes through a dedicated service account, and the app's database login through **IAM
database authentication**. This document is the runbook for the whole phase: the working commands in order, how to
verify, how to tear down and rebuild. The part docs hold the full story of each session.

| Part | What | Doc | PR |
|---|---|---|---|
| **Design** | Options, trade-offs, cost, decisions, teardown policy | [phase-4-design.md](phase-4-design.md) | #13 |
| **4A** | Cloud SQL instance, IAM database user, migrations and seed through the Auth Proxy | [phase-4a-cloud-sql.md](phase-4a-cloud-sql.md) | #14 |
| **4B** | Secret Manager, identities, `config.py` reads secret files, proxy sidecar manifests | [phase-4b-secrets-identity.md](phase-4b-secrets-identity.md) | #15 |
| **4C** | The app on GKE against Cloud SQL, drills, Cloud Logging | [phase-4c-gke-cloud-sql.md](phase-4c-gke-cloud-sql.md) | #16 |
| **4D** | Teardown verification, this runbook | (this doc) | (this PR) |

> **About the commands in this doc.** Code blocks contain commands only, no `#` comments: in zsh, `#` is not a
> comment unless `setopt interactivecomments` is on. **Run them from the repo root** unless a block starts with `cd`.
> The commands fill in your project with `$(gcloud config get project)`, so nothing project-specific is typed or
> committed.

> **Placeholders**
>
> | Placeholder | Meaning |
> |---|---|
> | `PROJECT_ID` | Your GCP project ID (`gcloud config get project`) |
> | `PROJECT_NUMBER` | The numeric project number (`gcloud projects describe PROJECT_ID --format="value(projectNumber)"`) |
> | `OPERATION_ID` | A Cloud SQL operation from `gcloud sql operations list --instance=exit-ticket-db` |

**Contents**

1. [Concepts](#1-concepts)
2. [What exists, and what it costs](#2-what-exists-and-what-it-costs)
3. [Build from scratch](#3-build-from-scratch)
4. [Starting a session](#4-starting-a-session)
5. [Verification checklist](#5-verification-checklist)
6. [Troubleshooting](#6-troubleshooting)
7. [Teardown](#7-teardown)
8. [Known gaps](#8-known-gaps)
9. [Lessons learned](#9-lessons-learned)
10. [Interview talking points](#10-interview-talking-points)
11. [ACE exam notes](#ace-exam-notes)

---

## 1. Concepts

### Architecture

```
                         GKE Autopilot (us-east4), nodes run as exit-ticket-nodes
 ┌─────────────────────────────────────────────────────────────────────┐
 │ namespace exit-ticket                                               │
 │                                                                     │
 │  web (nginx) ──/api/──► api pod, KSA "api" → Google SA exit-ticket-api
 │                          ├─ cloud-sql-proxy (native sidecar) ═══TLS 3307═══► Cloud SQL exit-ticket-db
 │                          │    IAM database login, no password                (public IP, no authorized
 │                          └─ api: DATABASE_URL → 127.0.0.1:5432                 networks, connector
 │                               /var/secrets/exit-ticket/* ◄── Secret Manager    enforcement REQUIRED)
 │                                                                     │
 │  migrate Job: alembic + the same proxy sidecar                      │
 └─────────────────────────────────────────────────────────────────────┘
```

### Identities: one per job, no keys anywhere

| Identity | Is | Can |
|---|---|---|
| `exit-ticket-nodes@…` | The **nodes'** Google SA | Write logs/metrics (`container.defaultNodeServiceAccount`); pull from **one** repository (`artifactregistry.reader`) |
| KSA `exit-ticket/api` | The **pods'** Kubernetes identity | Act as `exit-ticket-api@…` (`iam.workloadIdentityUser`, annotation `iam.gke.io/gcp-service-account`) |
| `exit-ticket-api@…` | The **app's** Google SA, used by the proxy *and* the Secret Manager add-on | Connect to Cloud SQL (`cloudsql.client`), log in as the IAM database user (`cloudsql.instanceUser`), read **two** secrets (`secretAccessor`) |
| `exit-ticket-api@PROJECT_ID.iam` | The **database user** for that SA | Create tables in `public`; owns every table |
| `postgres` | Cloud SQL's built-in admin | Grants and emergencies only; password in Secret Manager, readable only by the project Owner |

Every credential is a short-lived token (about an hour) from the GKE metadata server or IAM. A KSA **annotated** with a
Google SA acts as that SA for every Google API, including the Secret Manager add-on.

### Cloud SQL

- **Instance** (VM + disk in a Google tenant project: what bills, backs up, stops) → **databases** → **users**
  (per instance). **Connection name** `PROJECT_ID:us-east4:exit-ticket-db`.
- Enterprise edition, `db-f1-micro` (shared core, no SLA), zonal, SSD 10 GB, no auto-increase, daily backups at
  07:00 UTC, no PITR, deletion protection. **gcloud defaults Postgres 16+ to Enterprise Plus**: pass
  `--edition=enterprise`.
- **Stop/start** with `--activation-policy=NEVER|ALWAYS`. A start from stopped takes **~11.5 minutes** here.

### Secrets

- `session-secret`, `teacher-passcode`, `postgres-admin-password`: user-managed replication in `us-east4`, version 1.
- The **GKE Secret Manager add-on** (`--enable-secret-manager`) mounts pinned versions as files in
  `/var/secrets/exit-ticket` (`k8s/secretproviderclass.yaml`); `config.py` reads them. No Kubernetes Secrets, no
  secret environment variables.

### The proxy as a native sidecar

`initContainers` + `restartPolicy: Always`: it starts first, the main container waits for its startup probe
(`/startup` on 9090), it stops last, and in the migrate Job it's stopped when alembic exits so the Job completes.

---

## 2. What exists, and what it costs

| Resource | While a session runs | Between sessions |
|---|---|---|
| GKE Autopilot cluster | $0.10/hr fee (free-tier credit) + pods ~$0.015/hr | **Deleted** |
| Cloud SQL `exit-ticket-db` | ~$0.011/hr compute + disk + IP | **Stopped**: disk (10 GB SSD) + IP, ~$2–4/month. Deleted if no session within 7 days |
| Cloud SQL backups | Cents | None exist yet (they only run while the instance is running, at 07:00 UTC) |
| Secrets (3), service accounts (2), IAM bindings | Free | Free (kept) |
| Artifact Registry images | Cents | Cents (cleanup policy keeps 5 per image) |
| Ingress / load balancer | ~$0.025/hr, only if applied | None |

A 3-hour session costs about $0.40 gross, a few cents net of credits.

---

## 3. Build from scratch

The working commands, in order. Free steps first; 💰 marks where billing starts. Details and outputs are in the part
docs.

### 3.1 Identities (free) — [4A §2](phase-4a-cloud-sql.md#2-the-apps-google-service-account), [4B §4](phase-4b-secrets-identity.md#4-iam-bindings)

```bash
gcloud services list --enabled --format="value(config.name)" | grep -E "sqladmin|secretmanager|iamcredentials|container|compute"
gcloud iam service-accounts create exit-ticket-api --display-name="Exit Ticket API (pods)" --description="Pod identity for the api and migrate Job: Cloud SQL IAM login"
gcloud projects add-iam-policy-binding $(gcloud config get project) --member="serviceAccount:exit-ticket-api@$(gcloud config get project).iam.gserviceaccount.com" --role="roles/cloudsql.client" --condition=None
gcloud projects add-iam-policy-binding $(gcloud config get project) --member="serviceAccount:exit-ticket-api@$(gcloud config get project).iam.gserviceaccount.com" --role="roles/cloudsql.instanceUser" --condition=None
gcloud iam service-accounts add-iam-policy-binding exit-ticket-api@$(gcloud config get project).iam.gserviceaccount.com --member="user:$(gcloud config get account)" --role="roles/iam.serviceAccountTokenCreator"
gcloud iam service-accounts add-iam-policy-binding exit-ticket-api@$(gcloud config get project).iam.gserviceaccount.com --role="roles/iam.workloadIdentityUser" --member="serviceAccount:$(gcloud config get project).svc.id.goog[exit-ticket/api]"
gcloud iam service-accounts create exit-ticket-nodes --display-name="Exit Ticket GKE nodes" --description="Node identity: pull images, write logs and metrics"
gcloud projects add-iam-policy-binding $(gcloud config get project) --member="serviceAccount:exit-ticket-nodes@$(gcloud config get project).iam.gserviceaccount.com" --role="roles/container.defaultNodeServiceAccount" --condition=None
gcloud artifacts repositories add-iam-policy-binding exit-ticket --location=us-east4 --member="serviceAccount:exit-ticket-nodes@$(gcloud config get project).iam.gserviceaccount.com" --role="roles/artifactregistry.reader"
```
The `workloadIdentityUser` binding needs the project's workload identity pool, which exists once any GKE cluster has
been created in the project.

### 3.2 Secrets (free) — [4A §4](phase-4a-cloud-sql.md#4-the-postgres-password), [4B §2](phase-4b-secrets-identity.md#2-creating-the-secrets)

```bash
openssl rand -base64 32 | tr -d '\n' | gcloud secrets create postgres-admin-password --replication-policy=user-managed --locations=us-east4 --data-file=-
scripts/create-app-secrets.sh
gcloud secrets add-iam-policy-binding session-secret --member="serviceAccount:exit-ticket-api@$(gcloud config get project).iam.gserviceaccount.com" --role="roles/secretmanager.secretAccessor"
gcloud secrets add-iam-policy-binding teacher-passcode --member="serviceAccount:exit-ticket-api@$(gcloud config get project).iam.gserviceaccount.com" --role="roles/secretmanager.secretAccessor"
```
Values travel through pipes only: never a command line, history, file or the repo.

### 3.3 Cloud SQL (💰 billing starts) — [4A §3–7](phase-4a-cloud-sql.md#3-creating-the-instance)

```bash
gcloud sql instances create exit-ticket-db --database-version=POSTGRES_16 --edition=enterprise --tier=db-f1-micro --zone=us-east4-a --availability-type=zonal --storage-type=SSD --storage-size=10GB --no-storage-auto-increase --backup-start-time=07:00 --retained-backups-count=7 --no-enable-point-in-time-recovery --maintenance-window-day=SUN --maintenance-window-hour=8 --database-flags=cloudsql.iam_authentication=on --connector-enforcement=REQUIRED --deletion-protection
gcloud beta sql instances patch exit-ticket-db --update-labels=app=exit-ticket,env=dev
gcloud secrets versions access 1 --secret=postgres-admin-password | pbcopy
gcloud sql users set-password postgres --instance=exit-ticket-db --prompt-for-password
pbcopy < /dev/null
gcloud sql databases create exitticket --instance=exit-ticket-db
gcloud sql users create exit-ticket-api@$(gcloud config get project).iam --instance=exit-ticket-db --type=cloud_iam_service_account
```
(`--region` can't be combined with `--zone`; the GA command has no `--labels`, hence the beta `patch`. At the
password prompt: Cmd+V, Enter.)

One-time grants as `postgres`, through the proxy on the Mac (`brew install cloud-sql-proxy libpq` once). Tab 1:
```bash
cloud-sql-proxy --port=15432 $(gcloud config get project):us-east4:exit-ticket-db
```
Tab 2:
```bash
APP_USER="exit-ticket-api@$(gcloud config get project).iam"
PGPASSWORD="$(gcloud secrets versions access 1 --secret=postgres-admin-password)" /opt/homebrew/opt/libpq/bin/psql "host=127.0.0.1 port=15432 dbname=exitticket user=postgres" -v ON_ERROR_STOP=1 -c "GRANT CONNECT ON DATABASE exitticket TO \"$APP_USER\";" -c "GRANT USAGE, CREATE ON SCHEMA public TO \"$APP_USER\";" -c "\dn+ public"
```
Then Ctrl+C the admin proxy.

### 3.4 Images (cents) — [4C §2](phase-4c-gke-cloud-sql.md#2-images)

Only when the app code changed. Needs a clean tree; then put the printed digests into `k8s/api.yaml`,
`k8s/migrate-job.yaml` and `k8s/web.yaml`:
```bash
scripts/push-images.sh
```

### 3.5 Cluster (💰 billing starts) — [4C §4](phase-4c-gke-cloud-sql.md#4-the-cluster)

```bash
gcloud container clusters create-auto exit-ticket --region=us-east4 --release-channel=regular --service-account=exit-ticket-nodes@$(gcloud config get project).iam.gserviceaccount.com --enable-secret-manager --labels=app=exit-ticket,env=dev
gcloud container clusters get-credentials exit-ticket --region=us-east4
```

### 3.6 Deploy (💰 pods bill) — [4C §6](phase-4c-gke-cloud-sql.md#6-deploying)

Cloud SQL must be `RUNNABLE` first.
```bash
kubectl apply -f k8s/namespace.yaml
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/configmap.yaml | kubectl apply -f -
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/serviceaccount.yaml | kubectl apply -f -
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/secretproviderclass.yaml | kubectl apply -f -
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/migrate-job.yaml | kubectl apply -f -
kubectl -n exit-ticket wait --for=condition=complete job/migrate --timeout=300s
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/api.yaml | kubectl apply -f -
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/web.yaml | kubectl apply -f -
kubectl -n exit-ticket rollout status deployment/api --timeout=300s
kubectl -n exit-ticket rollout status deployment/web --timeout=300s
```

### 3.7 Seed, as the app — [4A §8](phase-4a-cloud-sql.md#8-migrations-and-seed-as-the-app)

Tab 1:
```bash
cloud-sql-proxy --auto-iam-authn --impersonate-service-account=exit-ticket-api@$(gcloud config get project).iam.gserviceaccount.com --port=15432 $(gcloud config get project):us-east4:exit-ticket-db
```
Tab 2:
```bash
cd ~/projects/exit-ticket-5th-grade-math-science/app/api
DATABASE_URL="postgresql+psycopg://exit-ticket-api%40$(gcloud config get project).iam@127.0.0.1:15432/exitticket" .venv/bin/python ../../scripts/seed.py
cd ~/projects/exit-ticket-5th-grade-math-science
```
Then open the app with `kubectl -n exit-ticket port-forward svc/web 8080:80` → http://localhost:8080/teacher.html.

---

## 4. Starting a session

**If the instance is stopped** (the usual case), start it first: it takes ~11.5 minutes, longer than the cluster, so
create the cluster (§3.5) in a second tab meanwhile:
```bash
gcloud sql instances patch exit-ticket-db --activation-policy=ALWAYS
gcloud sql operations list --instance=exit-ticket-db --limit=1
```
`Patching … failed … taking longer than expected` means gcloud stopped waiting; wait with
`gcloud sql operations wait OPERATION_ID --timeout=900` until `DONE`. Then §3.6 (the migrate Job is a no-op when the
schema is current) and, if wanted, §3.7.

**If the instance was deleted:** §3.3 (the name is reusable immediately), then §3.5–3.7. Identities and secrets
(§3.1–3.2) are kept between sessions.

---

## 5. Verification checklist

| Check | Command | Healthy |
|---|---|---|
| Instance settings | `gcloud sql instances describe exit-ticket-db --format="yaml(settings.edition,settings.tier,settings.connectorEnforcement,settings.ipConfiguration.authorizedNetworks,settings.deletionProtectionEnabled)"` | `ENTERPRISE`, `db-f1-micro`, `REQUIRED`, no `authorizedNetworks`, `true` |
| Database users | `gcloud sql users list --instance=exit-ticket-db --format="table(name,type)"` | `postgres` `BUILT_IN`; `exit-ticket-api@…iam` `CLOUD_IAM_SERVICE_ACCOUNT` |
| No keys | `gcloud iam service-accounts keys list --iam-account=exit-ticket-api@$(gcloud config get project).iam.gserviceaccount.com --managed-by=user` (and for `exit-ticket-nodes`) | `Listed 0 items.` |
| Secret readers | `gcloud secrets get-iam-policy session-secret --format="value(bindings.members)"` (and `teacher-passcode`) | Only `serviceAccount:exit-ticket-api@…` |
| Admin password | `gcloud secrets get-iam-policy postgres-admin-password` | No bindings (`etag: ACAB`) |
| Node SA | `gcloud container clusters describe exit-ticket --region=us-east4 --format="value(autoscaling.autoprovisioningNodePoolDefaults.serviceAccount)"` | `exit-ticket-nodes@…` |
| No Kubernetes Secrets | `kubectl -n exit-ticket get secrets` | `No resources found` |
| Pods | `kubectl -n exit-ticket get pods` | `api 2/2`, `web 1/1`, `migrate 0/2 Completed`, `RESTARTS 0` |
| Pod identity | [4C §8](phase-4c-gke-cloud-sql.md#8-workload-identity-checked-from-inside-the-pod) | `exit-ticket-api@…`; secrets as files; `secret env vars: 0` |
| End to end | `kubectl -n exit-ticket exec deploy/api -c api -- python -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8000/readyz').read().decode())"` | `{"status":"ready"}` |
| Table owner | `SELECT tablename, tableowner FROM pg_tables WHERE schemaname = 'public';` (through the app proxy) | All `exit-ticket-api@…iam` |

---

## 6. Troubleshooting

The most likely problems; the full tables are in [4A §13](phase-4a-cloud-sql.md#13-troubleshooting) and
[4C §14](phase-4c-gke-cloud-sql.md#14-troubleshooting).

| Symptom | Cause | Fix |
|---|---|---|
| Pod at `Init:0/1`; `FailedMount … PermissionDenied … secretmanager.versions.access` | `secretAccessor` not on the identity the add-on uses (annotated KSA → the Google SA) | Grant it on each secret to `exit-ticket-api@…` |
| Proxy `dial tcp …:3307: i/o timeout`; startup probe failing | Instance stopped or still starting | `gcloud sql instances list` → `RUNNABLE` |
| Proxy `403` / `NOT_AUTHORIZED` | Missing `cloudsql.client`, or Workload Identity mapping (`workloadIdentityUser`, annotation, `serviceAccountName`) | §3.1 bindings; check `kubectl -n exit-ticket get sa api -o yaml` |
| `Cloud SQL IAM service account authentication failed` | No IAM database user, missing `cloudsql.instanceUser`, or the IAM flag off | §3.3 |
| `permission denied for schema public` | Postgres 15+ default; grants not done | §3.3 grants |
| Proxy `invalid instance connection name` / 404 | Applied without `sed` (literal `PROJECT_ID`) or a typo | Always apply with `sed` |
| `unrecognized arguments` on a gcloud flag | The flag is beta-only or doesn't exist in your gcloud | `--help \| grep` first; `gcloud beta …` |
| `Patching … failed … taking longer than expected` | gcloud's wait timeout | `gcloud sql operations wait OPERATION_ID` |
| Job `DeadlineExceeded` | The pod never started in 300 s (mount or probe) | Read events, fix, `kubectl -n exit-ticket delete job migrate`, re-apply |
| curl `000` through a port-forward | The port-forward died with its pod | Restart it |

---

## 7. Teardown

**Order: Ingress (if applied) → namespace → cluster, then stop Cloud SQL, then verify.** The controllers that clean
up load balancers and NEGs run inside the cluster, so the cluster must exist while they finish.

```bash
kubectl delete -f k8s/ingress.yaml --wait=true
kubectl delete namespace exit-ticket
gcloud container clusters delete exit-ticket --region=us-east4
gcloud sql instances patch exit-ticket-db --activation-policy=NEVER
```
(Skip the Ingress line if it wasn't applied. The cluster delete and the Cloud SQL stop can run in two tabs.) Stop every
`cloud-sql-proxy`, `port-forward` and `-w` watch with Ctrl+C.

### Verify

```bash
gcloud container clusters list
gcloud compute forwarding-rules list
gcloud compute backend-services list
gcloud compute network-endpoint-groups list
gcloud compute disks list
gcloud compute addresses list
gcloud compute instances list
gcloud sql instances list
gcloud sql instances describe exit-ticket-db --format="value(state,settings.activationPolicy,settings.deletionProtectionEnabled)"
gcloud sql backups list --instance=exit-ticket-db --format="table(id,windowStartTime,type,status)"
gcloud compute firewall-rules list --format="table(name,sourceRanges.list())"
pgrep -fl cloud-sql-proxy
```
Healthy (Phase 4D, 2026-10-28): no clusters; six `Listed 0 items.`; `exit-ticket-db` **`STOPPED`**; `describe` →
`STOPPED NEVER True`; no backups; only the four `default-allow-*` firewall rules; no proxy process.

### What to keep between sessions

| Keep | Why |
|---|---|
| Service accounts, IAM bindings, secrets | Free; recreating them means repeating §3.1–3.2 |
| Images | Cents; the manifests pin their digests |
| Cloud SQL, **stopped** | ~$2–4/month; saves ~15–20 minutes next time. **Delete it if no session is planned within 7 days** |

### Deleting the instance

```bash
gcloud sql instances patch exit-ticket-db --no-deletion-protection
gcloud sql instances delete exit-ticket-db
gcloud sql instances list
```
The data and **all its backups** go with it. The name can be reused immediately. Rebuild with §3.3.

Optional local cleanup:
```bash
kubectl config delete-context gke_$(gcloud config get project)_us-east4_exit-ticket
```

---

## 8. Known gaps

| Gap | Why it's acceptable now | Later |
|---|---|---|
| One identity and database user for api and migrate, so the api has DDL rights | Same app; simple grants | Separate KSAs/SAs; migrate owns the schema, api gets DML only |
| Cloud SQL public IP | No authorized networks, connector enforcement, IAM | Private IP (private services access) |
| Shared-core tier, no SLA; slow starts | Dev, synthetic data | `db-custom-*` or Enterprise Plus, HA |
| **No backups taken yet** (only run while running, at 07:00 UTC); PITR off | Data is re-seedable | On-demand `gcloud sql backups create` before risky changes; PITR for real data |
| Cloud SQL roles on the project | No per-instance IAM in Cloud SQL | IAM Conditions on `resource.name` |
| Compute Engine default SA still has **Editor** | No longer used by our nodes; other services may fall back to it | Remove the role in the Terraform phase, after checking dependents |
| Secret rotation is manual | Pinned versions make it deliberate | A rotation runbook or scheduled job |
| Data Access logs off (who read a secret, who logged in) | Cost | Enable for Secret Manager and Cloud SQL when needed |
| Ingress plain HTTP, ephemeral IP | Synthetic data, minutes-long tests | Static IP, managed certificate, HTTPS redirect, IAP |
| Everything created by hand | Learning the gcloud commands for the ACE | Terraform with remote state in GCS, importing these resources |

---

## 9. Lessons learned

| Session | What happened | Lesson |
|---|---|---|
| 4A | Create failed on `--labels`; the `--help` check had shown no such line | A pre-flight check helps only if every expected line is confirmed |
| 4A | Two `instances.create` audit entries, 11 minutes apart | Long-running operations log request and completion |
| 4A | psql stopped at `:` | The pager; use `-P pager=off` |
| 4B | The manifests pointed at an image that couldn't read secret files | A config change is a deploy: rebuild, push, update digests |
| 4C | First migrate Job: `FailedMount PermissionDenied`, then `DeadlineExceeded` | An annotated KSA acts as the Google SA, add-on included; test the exact identity setup you deploy |
| 4C | `instances patch … failed`, but the start succeeded | Trust `gcloud sql operations list`, not the CLI's wait |
| 4C | Starting Cloud SQL took ~11.5 minutes, twice | Start it first; create the cluster in parallel |
| 4C | Proxy log text missing from a `gcloud logging read` table | The proxy's text is in `jsonPayload.textPayload`; inspect one raw entry |
| 4D | `describe` showed `state: STOPPED`; 4A's docs said it stays `RUNNABLE` | That claim was never tested; verify before documenting (now corrected) |
| 4D | `backups list` was empty | Automated backups run only while the instance is running; a stopped-most-of-the-time dev instance has none |
| All | Commands for "next time" next to commands to run | Label every block **RUN NOW** or **REFERENCE** |

---

## 10. Interview talking points

**1. "No keys, no passwords, no Kubernetes Secrets."** Nodes, pods and the database login all use short-lived tokens:
Workload Identity for the pods, IAM database authentication for Postgres. The only password, the `postgres` admin's,
was generated straight into Secret Manager and has no app access.

**2. "Least privilege per identity."** The node SA can write logs and pull from one repository; the app's SA can read
two secrets and log in to one database; inside Postgres, the app user owns only the tables it created.

**3. "Stateless pods, managed state."** I replaced the api pod and stopped the database: no data lost, and readiness
took the api out of rotation and back with zero restarts.

**4. "Identity over IP allowlists."** Cloud SQL has a public IP but no authorized networks; connector enforcement
accepts only the Auth Proxy, which authorizes each workload through IAM over mutual TLS.

**5. "Evidence-driven debugging."** A stuck `Init:0/1` led to a `FailedMount PermissionDenied` event, which showed which
identity the add-on used; a controlled grant removal proved it. The audit log let me reconstruct every API call.

**6. "Cost discipline as a habit."** Smallest tiers, explicit edition, cluster deleted and database stopped every
session, a written teardown policy with a verification checklist, and a budget that counts real usage.

---

## ACE exam notes

**Cloud SQL**
- Editions: Enterprise vs Enterprise Plus; Postgres 16+ defaults to Enterprise Plus. Shared-core tiers have no SLA.
- **HA** (`--availability-type=regional`): synchronous standby in another zone, automatic failover, same IP; protects
  against a zone outage. **Read replicas** are asynchronous, for read scaling or cross-region DR, not HA; promoted by
  hand.
- Backups: automated (window, retention) + on-demand; restore overwrites the target; PITR needs backups + transaction
  logs; exports to Cloud Storage are separate. Deleting the instance deletes its backups.
- Storage grows (auto-increase) but never shrinks; storage type fixed at creation. Tier changes restart the instance.
- Maintenance windows and deny periods. Stop/start: `--activation-policy=NEVER|ALWAYS`; stopped still bills storage
  and IP.
- Connection name `PROJECT:REGION:INSTANCE`. IAM database auth: flag `cloudsql.iam_authentication=on`, user types
  `cloud_iam_user` / `cloud_iam_service_account` / `cloud_iam_group`, role `roles/cloudsql.instanceUser`.
- Roles `cloudsql.client` (connect), `cloudsql.instanceUser`, `cloudsql.editor`, `cloudsql.admin`, on the project.

**Connecting from GKE**
- **Cloud SQL Auth Proxy** sidecar (recommended) or language connectors: IAM-authorized, mutual TLS on port 3307, no
  allowlist. Private IP needs private services access (VPC peering) or Private Service Connect.
- **Why not authorized networks:** they allow IPs, not identities; GKE egress comes from changing node IPs (or a shared
  NAT IP that admits every workload behind it).

**Choosing a database**
- Cloud SQL: regional relational. **Spanner**: global, horizontally scaling, 99.999%. **AlloyDB**: PostgreSQL-compatible,
  high performance. **Bigtable**: wide-column, high write throughput. **Firestore**: documents. **BigQuery**: analytics.

**Secret Manager**
- `secretAccessor` (values), `viewer` (metadata only), `secretVersionAdder` / `secretVersionManager`, `admin`; grant per
  secret. Versions immutable (enabled / disabled / destroyed); replication automatic or user-managed, fixed at
  creation; CMEK via Cloud KMS. Reading a value is a **Data Access** log (off by default).

**Workload Identity Federation for GKE**
- The answer to "how does a pod authenticate without keys". Annotated KSA → acts as the Google SA (needs
  `roles/iam.workloadIdentityUser` for `serviceAccount:PROJECT_ID.svc.id.goog[NS/KSA]`); otherwise grant the
  `principal://…/subject/ns/NS/sa/KSA` identifier. On by default in Autopilot.
- Node SA ≠ pod identity; set a least-privilege node SA at creation with `--service-account`.

**Kubernetes**
- Native sidecars: init containers with `restartPolicy: Always`; Jobs complete when the main container exits.
- Liveness → restart; readiness → out of the Service; startup → gates the others.
- Autopilot bills every container's requests, sidecars included.

**gcloud**
- GA vs `beta` vs `alpha` command groups; `--help | grep` before relying on a flag; `gcloud sql operations list|wait`.
