# Phase 3C — GKE Autopilot

Phase 3C runs the app on **GKE Autopilot** in `us-east4`: a temporary Postgres, the api and web images from
Artifact Registry (by digest), a migration Job, and probes, Secrets and a ConfigMap, all tested end to end through
`kubectl port-forward`. The cluster is created at the start of a session and **deleted at the end**, because it
bills by the hour. The Ingress (public load balancer) is written but not yet applied.

| Part | What was built | PR |
|---|---|---|
| **3C** | `k8s/` manifests (namespace, configmap, db, api, web, migrate Job, ingress), `scripts/create-k8s-secrets.sh`, first deploy, readiness drill, Cloud Logging check, teardown | (this PR) |

> **About the commands in this doc.** Each code block contains commands only, no `#` comments: in zsh, `#` is not a
> comment unless `setopt interactivecomments` is on. **Run kubectl commands from the repo root**: the manifests are
> referenced by relative path (see [Lessons learned](#13-lessons-learned)).

> **Placeholders used below.**
>
> | Placeholder | Meaning |
> |---|---|
> | `PROJECT_ID` | Your GCP project ID. In the manifests it's a literal word, filled in by `sed` at apply time |
> | `PROJECT_NUMBER` | Your numeric project number (`gcloud projects describe PROJECT_ID --format="value(projectNumber)"`) |
> | `POD` | A pod name from `kubectl get pods`, e.g. `api-844bd8578b-4h6vm` |

**Contents**

1. [Concepts](#1-concepts)
2. [Compose → Kubernetes](#2-compose--kubernetes)
3. [Creating the cluster](#3-creating-the-cluster)
4. [The manifests](#4-the-manifests)
5. [Secrets](#5-secrets)
6. [Deploying and testing](#6-deploying-and-testing)
7. [The readiness drill](#7-the-readiness-drill)
8. [Logs in Cloud Logging](#8-logs-in-cloud-logging)
9. [Troubleshooting](#9-troubleshooting)
10. [Teardown](#10-teardown)
11. [Recreate from scratch](#11-recreate-from-scratch)
12. [Known gaps](#12-known-gaps)
13. [Lessons learned](#13-lessons-learned)
14. [Interview talking points](#14-interview-talking-points)
15. [ACE exam notes](#ace-exam-notes)

---

## 1. Concepts

### Autopilot vs Standard

| | Standard | Autopilot |
|---|---|---|
| Nodes | You choose machine types and node pools, and manage upgrades | Google creates, sizes, patches and removes nodes |
| Billing | Every node VM, used or not, + cluster fee | **Pod resource requests** (CPU, memory, ephemeral storage) per second + cluster fee |
| Security | Your choice | Hardened by default: no privileged pods, no node SSH, Workload Identity on |

- **Requests are the bill.** `resources.requests` in each manifest is what you pay for while the pod runs. Completed
  and pending pods aren't billed.
- **Autopilot minimums and ratios** may round requests up; any change is recorded in the pod annotation
  `autopilot.gke.io/resource-adjustment`. Ours were accepted as written (`<none>`). Rules:
  https://cloud.google.com/kubernetes-engine/docs/concepts/autopilot-resource-requests
- **Cluster management fee:** per cluster per hour. GKE's monthly free-tier credit covers it for one Autopilot (or
  zonal Standard) cluster per billing account. Prices: https://cloud.google.com/kubernetes-engine/pricing

### Regional

Autopilot clusters are always **regional**: the control plane is replicated across the region's zones, and pods can
land in any zone (`topology.kubernetes.io/zone=us-east4-c` on our api pod). Use `--region`, never `--zone`.

### The temporary database

A single-replica `postgres:16.15-trixie` Deployment with its data in an **`emptyDir`**: it survives a container
restart, but **is lost when the pod is deleted or rescheduled** (scaling to 0, a rollout, a node upgrade, deleting
the cluster). Then re-run the migration Job and the seed.

A **PersistentVolumeClaim** would keep data on a Persistent Disk, but it bills per GB-month, complicates rollouts
(one disk, one pod), and must be deleted separately or it outlives the cluster. Phase 4 replaces this database with
Cloud SQL, so the persistence isn't worth it yet.

---

## 2. Compose → Kubernetes

| Phase 3A (compose) | Phase 3C (Kubernetes) |
|---|---|
| The compose project | **Namespace** `exit-ticket` |
| A service (`api:`, `web-full:`) | **Deployment** (keeps N pods running, rolls out changes) |
| A running container | **Pod** (one or more containers, one IP) |
| `api` with no `ports:`, reached as `api:8000` | **ClusterIP Service** `api`, reached as `api.exit-ticket.svc.cluster.local:8000` |
| `environment:` | **ConfigMap** `exit-ticket-config` |
| `env_file: app/api/.env` | **Secrets** `api-secrets`, `db-secrets` (created by a script, never in a file) |
| `docker compose run --rm api alembic upgrade head` | **Job** `migrate` |
| `docker compose run -p 8001:8000 api` | `kubectl port-forward svc/web 8080:80` |
| `ports: 127.0.0.1:8080:8080` on web | **Ingress** → external Application Load Balancer |
| `depends_on: condition: service_healthy` | Readiness probes + `rollout status` / `wait` between steps (Kubernetes has no start order) |
| Docker's DNS `127.0.0.11` | Cluster DNS (nginx reads it from `/etc/resolv.conf`) |
| `pgdata` named volume | `emptyDir` (temporary) → Cloud SQL in Phase 4 |

---

## 3. Creating the cluster

Check which identity the nodes will pull images with (the Compute Engine default service account, unless you set
one at creation):
```bash
gcloud projects describe PROJECT_ID --format="value(projectNumber)"
gcloud projects get-iam-policy PROJECT_ID --flatten="bindings[].members" --filter="bindings.members:PROJECT_NUMBER-compute@developer.gserviceaccount.com" --format="table(bindings.role)"
gcloud compute networks list
```
It has `roles/editor` (automatic in a project with no organization), which includes reading Artifact Registry, so
nothing was granted. See [Known gaps](#12-known-gaps).

**💰 Billing starts with this command** (cluster fee; pods bill once scheduled):
```bash
gcloud container clusters create-auto exit-ticket --region=us-east4 --release-channel=regular --labels=app=exit-ticket,env=dev
```

| Part | Meaning |
|---|---|
| `create-auto` | An Autopilot cluster (`create` makes a Standard one) |
| `--region=us-east4` | Autopilot is regional |
| `--release-channel=regular` | Upgrade cadence: `rapid` / `regular` (default) / `stable` |
| `--labels` | Shows up in billing reports, like the repository's labels |

It took about 5–10 minutes and reported `MASTER_VERSION 1.36.4-gke...`, `STATUS RUNNING`, and no node count:
Autopilot has no node pools to size.

```bash
gcloud container clusters get-credentials exit-ticket --region=us-east4
kubectl config current-context
kubectl get nodes
kubectl get namespaces
```

- **`get-credentials`** writes a **cluster** (API server address + CA certificate), a **user** (run
  `gke-gcloud-auth-plugin` for a short-lived token, no key), and a **context** joining them into `~/.kube/config`,
  and makes it the current context: `gke_PROJECT_ID_us-east4_exit-ticket`.
- **`kubectl get nodes` said `No resources found`.** Autopilot creates nodes only when pods need them; the first
  node (`gk3-exit-ticket-nap-...`, *node auto-provisioning*) appeared with the first pod.
- **kubectl version skew:** kubectl supports one minor version either side of the cluster (1.35 client, 1.36
  cluster: fine).

---

## 4. The manifests

All in [`k8s/`](../k8s/), apply order in [`k8s/README.md`](../k8s/README.md). Validated offline with
**kubeconform** (strict, 10 resources valid):
```bash
docker run --rm -v "$PWD/k8s:/k8s:ro" ghcr.io/yannh/kubeconform:v0.7.0 -strict -summary /k8s/namespace.yaml /k8s/configmap.yaml /k8s/db.yaml /k8s/api.yaml /k8s/web.yaml /k8s/migrate-job.yaml /k8s/ingress.yaml
```

### Keeping PROJECT_ID out of git

Image lines contain the literal word `PROJECT_ID`. `sed` fills it in while applying, in memory, so no file with
the real value is ever written:
```bash
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/api.yaml | kubectl apply -f -
```
(Kustomize, `kubectl apply -k`, is the formal alternative, but it means committing the value or editing a tracked
file.) The `Your active configuration is: [exit-ticket]` line it prints is gcloud's note on **stderr**; `$(...)`
only captures stdout, so `sed` gets just the project ID.

### What's in each

| File | Highlights |
|---|---|
| `namespace.yaml` | `exit-ticket` |
| `configmap.yaml` | `LOG_LEVEL=INFO`, `ENVIRONMENT=gke`, `API_HOST=api.exit-ticket.svc.cluster.local:8000` (full name: nginx's resolver ignores search domains) |
| `db.yaml` | **Temporary.** `Recreate` strategy; runs as UID/GID 999 with `fsGroup: 999`; `PGDATA` in an `emptyDir` subdirectory; readiness `pg_isready -h 127.0.0.1` |
| `api.yaml` | Image by digest; env from ConfigMap and `secretKeyRef`; 100m / 256Mi; startup, liveness and readiness probes; UID 10001; read-only root + `emptyDir` `/tmp` |
| `web.yaml` | Image by digest; `containerPort: 8080`; Service port 80 → 8080; probes on `/`; UID 101; read-only root + `emptyDir` for `/tmp` and `/etc/nginx/conf.d` |
| `migrate-job.yaml` | `alembic upgrade head`; `backoffLimit: 3`, `activeDeadlineSeconds: 300`, `ttlSecondsAfterFinished: 3600`, `restartPolicy: Never` |
| `ingress.yaml` | One default backend → `web:80` (written, not applied yet) |

### Resource requests (what's billed)

| Pod | CPU | Memory | Ephemeral storage |
|---|---|---|---|
| api | 100m | 256Mi | 100Mi |
| web | 50m | 64Mi | 100Mi |
| db | 100m | 256Mi | 1Gi |
| migrate (only while running) | 100m | 256Mi | 100Mi |

**Limits equal requests**: predictable cost and the **Guaranteed** QoS class (confirmed in `describe pod`).

### Probes

| Probe | api | Effect when it fails |
|---|---|---|
| **startup** | `GET /healthz`, every 2 s, up to 30 times | Liveness waits until it passes; after 60 s the container is restarted |
| **liveness** | `GET /healthz`, every 10 s | Container **restarted**. Never checks the database |
| **readiness** | `GET /readyz` (checks Postgres), every 5 s, 2 failures | Pod **removed from the Service**, not restarted |

This is the Phase 3A `/healthz` vs `/readyz` test, now enforced by Kubernetes (see [§7](#7-the-readiness-drill)).

The startup probe earned its place: at `cpu: 100m` the api needed about **22 seconds** to start (11 failed checks
in the Events). Without a startup probe, liveness would have restarted a slow-but-healthy container.

### Security context (every pod)

- `runAsNonRoot: true` + `runAsUser` matching the image's user (10001 api, 101 web, 999 db).
- `allowPrivilegeEscalation: false`, `capabilities: drop: ["ALL"]`, `seccompProfile: RuntimeDefault`.
- `readOnlyRootFilesystem: true` for api, web and the Job, with `emptyDir` volumes where they must write.
- `automountServiceAccountToken: false`: no pod calls the Kubernetes API.

These were tested locally first by running the same images with the matching Docker flags (`--user`,
`--cap-drop ALL`, `--security-opt no-new-privileges`, `--read-only`, `--tmpfs`).

### Why a Job for migrations

It runs **once** no matter how many api replicas exist, retries on its own (`backoffLimit`) if the database isn't
ready, keeps each try's logs (`restartPolicy: Never`), and cleans itself up an hour later
(`ttlSecondsAfterFinished`). A Job's pod template can't be changed, so **delete it before re-applying**.

---

## 5. Secrets

The namespace must exist first:
```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml
scripts/create-k8s-secrets.sh
kubectl -n exit-ticket get secrets
kubectl -n exit-ticket describe secret api-secrets
```

[`scripts/create-k8s-secrets.sh`](../scripts/create-k8s-secrets.sh):
- generates the Postgres password and `SESSION_SECRET`, reads `TEACHER_PASSCODE` from `app/api/.env`;
- writes them to temporary files readable only by you (`umask 077`), passes them with `--from-env-file`, and deletes
  them on exit (`trap`), so **no value appears in a command line, your shell history, or the repo**;
- refuses to overwrite existing Secrets (the running database was initialized with the old password).

`describe secret` shows key names and byte sizes only (`DATABASE_URL: 99 bytes`).

**A Kubernetes Secret is only base64-encoded**, not encrypted: anyone who can read it can decode it. Protection
comes from RBAC (who may read Secrets) and GKE's encryption of etcd at rest (optionally with your own Cloud KMS
key). **Phase 4** moves the source of truth to **Secret Manager**, read with **Workload Identity**.

---

## 6. Deploying and testing

**💰 Pods bill from the first apply.**
```bash
kubectl apply -f k8s/db.yaml
kubectl -n exit-ticket rollout status deployment/db --timeout=300s
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/migrate-job.yaml | kubectl apply -f -
kubectl -n exit-ticket wait --for=condition=complete job/migrate --timeout=300s
kubectl -n exit-ticket logs job/migrate
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/api.yaml | kubectl apply -f -
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/web.yaml | kubectl apply -f -
kubectl -n exit-ticket rollout status deployment/api --timeout=300s
kubectl -n exit-ticket rollout status deployment/web --timeout=300s
kubectl -n exit-ticket get pods -o wide
```

Healthy: `db`, `api`, `web` at `1/1 Running`, `RESTARTS 0`; `migrate` at `0/1 Completed`. The first db rollout
takes 1–3 minutes (Autopilot creates a node, pulls Postgres, runs initdb).

What Autopilot bills:
```bash
kubectl -n exit-ticket get pods -o custom-columns='POD:.metadata.name,CPU:.spec.containers[0].resources.requests.cpu,MEM:.spec.containers[0].resources.requests.memory,DISK:.spec.containers[0].resources.requests.ephemeral-storage,ADJUSTED:.metadata.annotations.autopilot\.gke\.io/resource-adjustment'
```

### Seeding through a port-forward

`kubectl port-forward` opens a temporary tunnel from your laptop, through the API server, to a pod behind a
Service. Nothing is exposed publicly; Ctrl+C closes it. Port 15432 avoids clashing with local compose Postgres.

Tab 1:
```bash
kubectl -n exit-ticket port-forward svc/db 15432:5432
```
Tab 2:
```bash
cd ~/projects/exit-ticket-5th-grade-math-science/app/api
DATABASE_URL="$(kubectl -n exit-ticket get secret api-secrets -o jsonpath='{.data.DATABASE_URL}' | base64 -d | sed 's/@db:5432/@localhost:15432/')" .venv/bin/python ../../scripts/seed.py
```

`VAR="..." command` sets the variable for that one command only; history stores the command text, not the
password.

### Testing the app without a load balancer

```bash
kubectl -n exit-ticket port-forward svc/web 8080:80
```
```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/
curl -s http://localhost:8080/api/healthz
curl -s http://localhost:8080/api/readyz
kubectl -n exit-ticket logs deployment/api --tail=15
```

Browser: http://localhost:8080/teacher.html (sign in, **Release**, **Show student codes**), then in a private
window http://localhost:8080 (sign in with a code, answer, submit). The submission appeared on the dashboard within
seconds.

In the api logs, **`169.254.4.6`** is the kubelet running probes (`/readyz` every 5 s, `/healthz` every 10 s) and
**`10.52.128.20`** is the web pod: nginx is the api's only caller.

---

## 7. The readiness drill

```bash
kubectl -n exit-ticket get pods -l app=api -w
```
```bash
kubectl -n exit-ticket scale deployment/db --replicas=0
kubectl -n exit-ticket get endpointslices -l kubernetes.io/service-name=api -o jsonpath='{range .items[*].endpoints[*]}{.addresses[0]} ready={.conditions.ready}{"\n"}{end}'
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/api/healthz
kubectl -n exit-ticket describe pod -l app=api
kubectl -n exit-ticket scale deployment/db --replicas=1
kubectl -n exit-ticket rollout status deployment/db --timeout=180s
```

| Observation | Meaning |
|---|---|
| api `1/1` → `0/1` → `1/1`, **`RESTARTS 0`** | Readiness failed, liveness didn't: taken out of rotation, never restarted |
| Events: `Readiness probe failed: HTTP probe failed with statuscode: 503` | `/readyz` reporting the database gone |
| `502` for `/api/healthz` through nginx | A not-ready pod gets **no** Service traffic, even for paths that would work |
| The EndpointSlice still listed the pod IP | Not-ready pods stay in the list with `ready=false`; the plain table doesn't show the flag |
| Back to `1/1` on its own | `pool_pre_ping` reconnected; no restart needed |

**The trap:** the new db pod was **empty**, but `/readyz` only checks `SELECT 1`, so the api was ready while every
real request failed. Cloud Logging showed `Exception in ASGI application` at ERROR from the dashboard's refreshes
during that window. Fix: re-run the Job (delete, then apply) and the seed. **Readiness proves reachability, not
correctness.**

---

## 8. Logs in Cloud Logging

GKE ships every container's stdout/stderr to Cloud Logging. Our one-JSON-object-per-line logs become structured
entries: **`severity`** sets the level, **`message`** is the summary, **`time`** the timestamp, and the rest lands in
`jsonPayload`.

Logs Explorer:
```
resource.type="k8s_container"
resource.labels.cluster_name="exit-ticket"
resource.labels.namespace_name="exit-ticket"
resource.labels.container_name="api"
```

Terminal, errors only:
```bash
gcloud logging read 'resource.type="k8s_container" AND resource.labels.cluster_name="exit-ticket" AND resource.labels.namespace_name="exit-ticket" AND resource.labels.container_name="api" AND severity>=ERROR' --freshness=1h --limit=10 --format="table(timestamp,severity,jsonPayload.logger,jsonPayload.message)"
```

Logs stay after the cluster is deleted (30 days in the `_Default` bucket).

---

## 9. Troubleshooting

| `STATUS` / symptom | Meaning | First command | Usual causes |
|---|---|---|---|
| `Pending` | Not on a node yet | `kubectl -n exit-ticket describe pod POD` → Events | Autopilot still creating a node (wait 1–2 min); requests above Autopilot's maximums |
| `ContainerCreating` | On a node; pulling the image or mounting | `describe pod` | Large image; `CreateContainerConfigError` = a referenced Secret/ConfigMap is missing |
| `ImagePullBackOff` / `ErrImagePull` | Can't fetch the image | `describe pod` → `Failed to pull image` | Wrong path or digest; `PROJECT_ID` not substituted; node service account lacks `artifactregistry.reader` |
| `CrashLoopBackOff` | Starts, exits, restarted with growing delays | `kubectl -n exit-ticket logs POD --previous` | Missing or invalid settings; `securityContext` mismatch (e.g. read-only root without a writable `/tmp`) |
| `Running`, `READY 0/1` | Alive but not ready: no traffic | `describe pod` → `Readiness probe failed` | api: database down (`/readyz` 503); web: nginx not on 8080 |
| `RESTARTS` climbing | Liveness keeps failing | `describe pod` | Wrong probe path/port; probe too strict; no startup probe for a slow start |
| Job never completes | Retries exhausted or deadline hit | `kubectl -n exit-ticket get pods -l app=migrate`, then `logs` | Database not ready; wrong `DATABASE_URL` |
| `sed: k8s/...: No such file or directory` | Wrong working directory | `pwd` | Run from the repo root |

`logs --previous` shows the **last crashed** container; plain `logs` shows the current attempt.

---

## 10. Teardown

**Order matters once an Ingress exists:** delete the Ingress first, so GKE's controller removes the load balancer
resources it created outside the cluster (forwarding rule, target proxy, URL map, backend service, health check,
NEGs). Deleting the cluster first can orphan them, still billing. The same goes for PVCs and their disks.

Stop every `port-forward` and `-w` watch (Ctrl+C), then:
```bash
kubectl delete -f k8s/ingress.yaml --wait=true
```
(only if it was applied)
```bash
gcloud container clusters delete exit-ticket --region=us-east4
```

Check that nothing is left:
```bash
gcloud container clusters list
gcloud compute forwarding-rules list
gcloud compute backend-services list
gcloud compute network-endpoint-groups list
gcloud compute disks list
```
All should be empty (`Listed 0 items.`).

Optionally remove the stale kubectl context:
```bash
kubectl config delete-context gke_PROJECT_ID_us-east4_exit-ticket
```

**What stays:** the Artifact Registry images (storage only), logs (30 days), the `default` network.

---

## 11. Recreate from scratch

About 15 minutes, mostly waiting. From the repo root:

```bash
gcloud container clusters create-auto exit-ticket --region=us-east4 --release-channel=regular --labels=app=exit-ticket,env=dev
gcloud container clusters get-credentials exit-ticket --region=us-east4
```
```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml
scripts/create-k8s-secrets.sh
kubectl apply -f k8s/db.yaml
kubectl -n exit-ticket rollout status deployment/db --timeout=300s
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/migrate-job.yaml | kubectl apply -f -
kubectl -n exit-ticket wait --for=condition=complete job/migrate --timeout=300s
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/api.yaml | kubectl apply -f -
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/web.yaml | kubectl apply -f -
kubectl -n exit-ticket rollout status deployment/api --timeout=300s
kubectl -n exit-ticket rollout status deployment/web --timeout=300s
```
Then seed through the db port-forward ([§6](#seeding-through-a-port-forward)), and open the app through
`kubectl -n exit-ticket port-forward svc/web 8080:80`. Student login codes are new each time.

---

## 12. Known gaps

| Gap | Why it's acceptable now | Planned fix |
|---|---|---|
| Nodes pull as the Compute Engine default SA, which has **Editor** | Works, and the cluster lives for one session | Dedicated node SA with `roles/container.defaultNodeServiceAccount` + `roles/artifactregistry.reader` on the repository, when the cluster moves to Terraform |
| Temporary Postgres in an `emptyDir` | Synthetic data, quick to recreate | **Cloud SQL** (Phase 4) |
| Secrets as Kubernetes Secrets | RBAC-protected, short-lived cluster | **Secret Manager + Workload Identity** (Phase 4) |
| One replica each | Smallest bill | 2+ replicas and a PodDisruptionBudget once there's real traffic |
| nginx stops on SIGTERM (fast), not SIGQUIT (graceful) | One replica, no real traffic | A `preStop` hook running `nginx -s quit` |
| Ingress written but not applied; no HTTPS | Load balancer bills hourly | Apply when needed; HTTPS (managed certificate), IAP, Cloud Armor later |
| Created by hand with gcloud | Learning the commands first | Terraform with remote state in GCS |

---

## 13. Lessons learned

| What happened | Cause | Lesson |
|---|---|---|
| `sed: k8s/migrate-job.yaml: No such file or directory`, then the seed failed with `relation "standards" does not exist` | The command ran in `app/api` (left over from seeding) | Start each block with `cd` to the repo root |
| `pg_isready` passed but clients couldn't connect yet (found in a local test) | On first start the image runs a temporary init server on a Unix socket only | Probe over TCP: `pg_isready -h 127.0.0.1` |
| nginx: `/etc/nginx/conf.d is not writable` (local test) | Read-only root filesystem; the entrypoint renders its template there | `emptyDir` for `/tmp` and `/etc/nginx/conf.d` |
| api took ~22 s to start | `cpu: 100m` limit throttles Python's imports | A startup probe, so liveness doesn't restart a slow start |
| api "ready" while every request failed | `/readyz` checks connectivity, not the schema; the new db pod was empty | Migrations are a deploy step; watch error logs, not just probes |
| EndpointSlice table still showed the not-ready pod | Not-ready endpoints stay in the slice with `ready=false` | Look at `.conditions.ready` |
| Predicted readiness errors, got `Exception in ASGI application` | The dashboard kept refreshing against the empty database | Check what the logs actually say |
| `Your active configuration is: [exit-ticket]` mixed into apply output | gcloud's note on stderr | Harmless; `$(...)` captures stdout only |

---

## 14. Interview talking points

**1. "Same images, every environment."** The digests pushed in Phase 3B run unchanged on GKE; only environment
variables differ. Config in a ConfigMap, secrets in Secrets created by a script, and the project ID filled in at
apply time so nothing environment-specific is committed.

**2. "Probes with distinct jobs."** Startup protects a slow first start (22 s at 100m CPU), liveness only asks "is
the process alive?", readiness asks "can it serve?". In the drill, a database outage took the api out of rotation
without a single restart, and it recovered by itself.

**3. "Readiness isn't correctness."** After the drill the database came back empty: probes were green while every
request failed. That's why migrations are a deliberate step and why I'd alert on error rates, not just health.

**4. "Least privilege, tested before deploy."** Every pod is non-root, read-only where possible, with all
capabilities dropped and no service account token. I reproduced those settings locally with Docker first, which
caught two failures (Postgres' socket-only init server, nginx needing a writable `conf.d`) before they became
`CrashLoopBackOff`s.

**5. "Cost discipline."** Autopilot bills by requests, so I sized them at about 0.25 vCPU and 576 MiB total,
verified Autopilot didn't adjust them, and the cluster lives only for a session: create, test, delete, verify
nothing is left (forwarding rules, NEGs, disks).

---

## ACE exam notes

**Creating and connecting**
- `gcloud container clusters create-auto NAME --region=REGION` (Autopilot, always regional) vs
  `gcloud container clusters create NAME --zone=ZONE` or `--region=REGION` (Standard, with node pools,
  `--num-nodes`, `--machine-type`).
- `gcloud container clusters get-credentials NAME --region=REGION` writes the kubeconfig context; kubectl
  authenticates through `gke-gcloud-auth-plugin` (short-lived tokens).
- Release channels: `rapid`, `regular` (default), `stable`.

**kubectl basics**
- `apply -f` (declarative), `get`, `describe` (Events!), `logs` (`--previous`, `-f`), `rollout status`,
  `scale --replicas=N`, `port-forward`, `delete`, `config current-context`, `-n NAMESPACE`, `-l LABEL=VALUE`,
  `-o wide|yaml|jsonpath`.

**Workloads**
- **Deployment** → ReplicaSet → Pods; strategies `RollingUpdate` (default) and `Recreate`.
- **Job** runs to completion (`backoffLimit`, `activeDeadlineSeconds`, `ttlSecondsAfterFinished`); **CronJob** on a
  schedule; **StatefulSet** for stable identity and per-pod storage; **DaemonSet** for one pod per node.

**Services**
| Type | Reachable from |
|---|---|
| **ClusterIP** (default) | Inside the cluster only |
| **NodePort** | Each node's IP on a high port |
| **LoadBalancer** | A Google **external passthrough Network Load Balancer** (L4) with an external IP |
| **Ingress** (not a Service type) | A Google **external Application Load Balancer** (L7, HTTP(S), path/host rules) in front of Services |

- Pods find Services by DNS: `SERVICE.NAMESPACE.svc.cluster.local`.

**Probes**
- Liveness failure → restart; readiness failure → removed from Service endpoints; startup → delays the others.
- GKE Ingress builds its load balancer **health check from the readiness probe**.

**Configuration**
- ConfigMap for settings, Secret for credentials (base64, not encryption). On GCP: **Secret Manager** + **Workload
  Identity**; optional application-layer secrets encryption with **Cloud KMS**.

**Autopilot billing**
- Per second for **pod requests** (vCPU, memory, ephemeral storage) + a per-cluster management fee (free-tier credit
  covers one cluster). Load balancers, Persistent Disks and logging beyond the free allotment bill separately.
- Requests may be adjusted to Autopilot's minimums/ratios (`autopilot.gke.io/resource-adjustment`).

**Images**
- Nodes pull with the **node service account**; `ImagePullBackOff` on GKE is usually a missing
  `roles/artifactregistry.reader` on that account, or a wrong image path.

**Cost hygiene**
- Delete Ingresses (load balancers) and PVCs (disks) **before** the cluster; then check forwarding rules, backend
  services, NEGs and disks.
