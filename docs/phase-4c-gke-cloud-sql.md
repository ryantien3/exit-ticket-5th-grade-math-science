# Phase 4C — The app on GKE with Cloud SQL

Phase 4C runs the app on a new GKE Autopilot cluster against **Cloud SQL**: nodes run as a least-privilege service
account, the api and the migrate Job reach the database through the **Cloud SQL Auth Proxy** as a native sidecar,
and secrets are mounted from **Secret Manager**. Two drills prove the point of Phase 4: replacing the api pod loses
nothing, and stopping Cloud SQL takes the api out of rotation without a single restart. Everything was torn down
at the end.

| Part | What was done | PR |
|---|---|---|
| **4C** | New images; cluster with node SA + Secret Manager add-on; deploy; Workload Identity checks; seed and app test; pod-replacement and database-outage drills; Cloud Logging; teardown. Fix: the add-on's secret access goes to the Google SA | (this PR) |

> **About the commands in this doc.** Code blocks contain commands only, no `#` comments. Run them from the repo
> root.

**Contents**

1. [What changed since Phase 3C](#1-what-changed-since-phase-3c)
2. [Images](#2-images)
3. [Starting Cloud SQL](#3-starting-cloud-sql)
4. [The cluster](#4-the-cluster)
5. [How the api waits for the proxy](#5-how-the-api-waits-for-the-proxy)
6. [Deploying](#6-deploying)
7. [The first migrate Job failed: whose identity reads the secrets](#7-the-first-migrate-job-failed-whose-identity-reads-the-secrets)
8. [Workload Identity, checked from inside the pod](#8-workload-identity-checked-from-inside-the-pod)
9. [Seed and app test](#9-seed-and-app-test)
10. [Drill: replace the api pod](#10-drill-replace-the-api-pod)
11. [Drill: stop Cloud SQL](#11-drill-stop-cloud-sql)
12. [Cloud Logging](#12-cloud-logging)
13. [Teardown](#13-teardown)
14. [Troubleshooting](#14-troubleshooting)
15. [Lessons learned](#15-lessons-learned)
16. [Interview talking points](#16-interview-talking-points)
17. [ACE exam notes](#ace-exam-notes)

---

## 1. What changed since Phase 3C

| | Phase 3C | Phase 4C |
|---|---|---|
| Database | Postgres pod with an `emptyDir` | Cloud SQL `exit-ticket-db` |
| Secrets | Kubernetes Secrets from a script | Secret Manager, mounted as files |
| Database password | In a Secret | **None** (IAM database auth) |
| Node identity | Compute Engine default SA (**Editor**) | `exit-ticket-nodes` (node role + one repository) |
| Pod identity | None | KSA `api` → Google SA `exit-ticket-api` |
| Pods | Stateful: a replaced db pod lost everything | **Stateless**: replace or move them freely |

---

## 2. Images

`api.yaml` pinned the Phase 3B image, which predates `config.py` reading secret files, so new images were built
from the merged 4B commit (clean tree required) and the digests updated:
```bash
scripts/push-images.sh
```
Tag `5fefc95`: `api@sha256:55c56a56…` (api and migrate), `web@sha256:eab714b7…`.

---

## 3. Starting Cloud SQL

```bash
gcloud sql instances patch exit-ticket-db --activation-policy=ALWAYS
gcloud sql operations list --instance=exit-ticket-db --limit=3
gcloud sql operations wait OPERATION_ID --timeout=900
```
- gcloud printed **`Patching Cloud SQL instance...failed`** with `Operation … is taking longer than expected`. That's
  gcloud **giving up waiting**, not the operation failing; it continued on Google's side.
- `operations list` showed `UPDATE RUNNING`; `wait` blocks until `DONE`. Don't send a second patch on top.
- **The start took 11 min 30 s** (and 11 min 39 s again in the drill). A stopped shared-core instance can take longer
  to start than the cluster takes to create, so start it first and create the cluster in parallel.

---

## 4. The cluster

Check the flags first (the 4A lesson), then create:
```bash
gcloud container clusters create-auto --help | grep -E -- "--(service-account|enable-secret-manager|release-channel|labels|region)"
gcloud container clusters create-auto exit-ticket --region=us-east4 --release-channel=regular --service-account=exit-ticket-nodes@$(gcloud config get project).iam.gserviceaccount.com --enable-secret-manager --labels=app=exit-ticket,env=dev
gcloud container clusters get-credentials exit-ticket --region=us-east4
gcloud container clusters describe exit-ticket --region=us-east4 --format="yaml(autopilot.enabled,autoscaling.autoprovisioningNodePoolDefaults.serviceAccount,secretManagerConfig,workloadIdentityConfig)"
```
`describe` showed `serviceAccount: exit-ticket-nodes@…` (under `autoprovisioningNodePoolDefaults`: Autopilot creates
the node pools), `secretManagerConfig: enabled: true`, and `workloadPool: PROJECT_ID.svc.id.goog`. **The Phase 3
"nodes run with Editor" gap is closed.**

---

## 5. How the api waits for the proxy

The proxy is a **native sidecar**: in `initContainers` with `restartPolicy: Always`. The kubelet starts it, **waits
for its startup probe** (`GET /startup` on 9090, every 2 s, up to 60 s), and only then creates the main container.
On shutdown it stops the main container first and the proxy last. In the Job, Kubernetes stops the proxy once
alembic exits, so the Job completes.

There's no readiness probe on the proxy on purpose: the api's `/readyz` runs `SELECT 1` *through* it, which tests the
whole path at once ([§11](#11-drill-stop-cloud-sql)).

| Pod | Containers | CPU | Memory |
|---|---|---|---|
| api | api 100m/256Mi + proxy 50m/128Mi | 150m | 384Mi |
| web | nginx | 50m | 64Mi |
| migrate (while running) | alembic + proxy | 150m | 384Mi |

Autopilot bills the sidecar's requests too; it accepted all of them (`autopilot.gke.io/resource-adjustment` was
`<none>` on every pod).

---

## 6. Deploying

**Gate:** `gcloud sql instances list` shows `RUNNABLE` and the kube context is the new cluster.

### A. Namespace, settings, identity, secret mapping

```bash
kubectl apply -f k8s/namespace.yaml
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/configmap.yaml | kubectl apply -f -
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/serviceaccount.yaml | kubectl apply -f -
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/secretproviderclass.yaml | kubectl apply -f -
kubectl -n exit-ticket get configmap,serviceaccount,secretproviderclass
kubectl -n exit-ticket get secrets
```
`get secrets`: **`No resources found`**. No Kubernetes Secrets at all.

### B. The migrate Job

```bash
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/migrate-job.yaml | kubectl apply -f -
kubectl -n exit-ticket get pods -l app=migrate -w
kubectl -n exit-ticket wait --for=condition=complete job/migrate --timeout=300s
kubectl -n exit-ticket logs job/migrate -c migrate
kubectl -n exit-ticket logs job/migrate -c cloud-sql-proxy
```
The first attempt failed ([§7](#7-the-first-migrate-job-failed-whose-identity-reads-the-secrets)). After the fix:

| Watch | Meaning |
|---|---|
| `Init:0/1` (0–4 s) | Secrets mounted; proxy starting |
| `PodInitializing`, `1/2` (7 s) | Proxy's startup probe passed |
| `2/2 Running` (11 s) | Only now did alembic start |
| `1/2 Completed` (36 s) | alembic exited 0 |
| `0/2 Completed` | Kubernetes stopped the proxy; the Job completed |

Proxy log: `ready for new connections` at 03:09:02, alembic's connection at 03:09:28, then `Sending SIGTERM signal
for proxy to shutdown`. alembic ran no upgrades (already at `0002` since [4A](phase-4a-cloud-sql.md)).

### C. The api and web

```bash
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/api.yaml | kubectl apply -f -
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/web.yaml | kubectl apply -f -
kubectl -n exit-ticket rollout status deployment/api --timeout=300s
kubectl -n exit-ticket rollout status deployment/web --timeout=300s
kubectl -n exit-ticket get pods
```
`api 2/2 Running`, `web 1/1 Running`, `migrate 0/2 Completed`, all `RESTARTS 0`. `2/2` counts the proxy.

---

## 7. The first migrate Job failed: whose identity reads the secrets

The pod sat at `Init:0/1` for 4 minutes and was killed at 5 minutes (`DeadlineExceeded`, from
`activeDeadlineSeconds: 300`).
```bash
kubectl -n exit-ticket get events --sort-by=.lastTimestamp | tail -25
```

| Event | Meaning |
|---|---|
| `FailedScheduling` → `TriggeredScaleUp` → `Scheduled` | Autopilot created a node (normal) |
| `driver name secrets-store-gke.csi.k8s.io not found in the list of registered CSI drivers` (once) | Brand-new node; the add-on's driver registered seconds later (transient) |
| **`PermissionDenied … 'secretmanager.versions.access' denied`** on both secrets | **The cause.** Volumes mount before *any* container starts, so the pod never left `Init:0/1` |

**Why:** [4B](phase-4b-secrets-identity.md) granted `secretAccessor` to the **KSA principal**, as the add-on's docs show
for a KSA *without* an annotation. Ours is **annotated** with `iam.gke.io/gcp-service-account`, so the add-on acts as
the **Google SA `exit-ticket-api`**, which had no secret access.

**Fix**, still per secret:
```bash
gcloud secrets add-iam-policy-binding session-secret --member="serviceAccount:exit-ticket-api@$(gcloud config get project).iam.gserviceaccount.com" --role="roles/secretmanager.secretAccessor"
gcloud secrets add-iam-policy-binding teacher-passcode --member="serviceAccount:exit-ticket-api@$(gcloud config get project).iam.gserviceaccount.com" --role="roles/secretmanager.secretAccessor"
kubectl -n exit-ticket delete job migrate
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/migrate-job.yaml | kubectl apply -f -
```
(A Job's pod template is immutable, and a failed Job doesn't retry after its deadline: delete and re-apply.)

Then the now-unused KSA principal grant was removed:
```bash
PROJECT_NUMBER="$(gcloud projects describe $(gcloud config get project) --format='value(projectNumber)')"
KSA_PRINCIPAL="principal://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$(gcloud config get project).svc.id.goog/subject/ns/exit-ticket/sa/api"
gcloud secrets remove-iam-policy-binding session-secret --member="$KSA_PRINCIPAL" --role="roles/secretmanager.secretAccessor"
gcloud secrets remove-iam-policy-binding teacher-passcode --member="$KSA_PRINCIPAL" --role="roles/secretmanager.secretAccessor"
```
The api pod then mounted the secrets with **only** the Google SA grant in place: proof of which identity the add-on
uses. Each secret has exactly one reader.

**Rule:** a KSA **annotated** with a Google SA acts as that SA for Google APIs, so grant the SA. Grant the
`principal://` identifier only for KSAs without the annotation.

---

## 8. Workload Identity, checked from inside the pod

Identities, lengths and counts only, never values:
```bash
kubectl -n exit-ticket exec deploy/api -c api -- python -c "import urllib.request as u; r=u.Request('http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/email', headers={'Metadata-Flavor':'Google'}); print(u.urlopen(r).read().decode())"
kubectl -n exit-ticket exec deploy/api -c api -- python -c "import json,urllib.request as u; r=u.Request('http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token', headers={'Metadata-Flavor':'Google'}); t=json.load(u.urlopen(r)); print('type:', t['token_type'], '| expires in:', t['expires_in'], 's | length:', len(t['access_token']))"
kubectl -n exit-ticket exec deploy/api -c api -- sh -c 'wc -c /var/secrets/exit-ticket/session_secret /var/secrets/exit-ticket/teacher_passcode; echo "secret env vars: $(env | grep -c -E "^(SESSION_SECRET|TEACHER_PASSCODE)=")"'
kubectl -n exit-ticket exec deploy/api -c api -- python -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8000/readyz').read().decode())"
```

| Check | Result |
|---|---|
| Metadata server identity | `exit-ticket-api@PROJECT_ID.iam.gserviceaccount.com` (not the node SA) |
| Token | `Bearer`, expires in 3574 s, 1024 characters: short-lived, no key file |
| Secrets | Files of 64 and 14 bytes; **`secret env vars: 0`** |
| `/readyz` | `{"status":"ready"}`: api → proxy → IAM login → Cloud SQL |

`automountServiceAccountToken: false` didn't get in the way: the GKE metadata server obtains the KSA token itself.

---

## 9. Seed and app test

The seed ran through the same Mac path as [4A](phase-4a-cloud-sql.md) (proxy impersonating `exit-ticket-api` on
port 15432); it upserts, so re-running is safe. The app ran through
`kubectl -n exit-ticket port-forward svc/web 8080:80`: `200`, `/api/healthz` ok, `/api/readyz` ready; teacher sign-in
with the passcode from Secret Manager, Release, a student submission in a private window, and the dashboard showed it.

```bash
PGOPTIONS="-c default_transaction_read_only=on" /opt/homebrew/opt/libpq/bin/psql -P pager=off "host=127.0.0.1 port=15432 dbname=exitticket user=exit-ticket-api@$(gcloud config get project).iam" -c "SELECT count(*) AS answers, max(answered_at) AS latest FROM answers;"
```
**5 answers, latest 03:17:09.** (The timestamp column is `answered_at`; `created_at` doesn't exist.)

The Ingress was skipped: the web → load balancer path is unchanged since [3C §9](phase-3c-gke.md#9-public-http-through-the-ingress).

---

## 10. Drill: replace the api pod

```bash
kubectl -n exit-ticket get pods -l app=api -w
kubectl -n exit-ticket delete pod -l app=api
kubectl -n exit-ticket rollout status deployment/api --timeout=300s
```
A new pod reached `2/2`; the same query still returned **5 answers, latest 03:17:09**, and the dashboard still showed
the submission. In 3C, a replaced database pod meant an empty database. **The api is stateless now.**

The port-forward died with the old pod (curl then returns `000`): restart it.

---

## 11. Drill: stop Cloud SQL

```bash
gcloud sql instances patch exit-ticket-db --activation-policy=NEVER
kubectl -n exit-ticket get endpointslices -l kubernetes.io/service-name=api -o jsonpath='{range .items[*].endpoints[*]}{.addresses[0]} ready={.conditions.ready}{"\n"}{end}'
kubectl -n exit-ticket describe pod -l app=api | grep -E "Readiness probe failed|Restart Count|^    Ready:"
kubectl -n exit-ticket logs deploy/api -c cloud-sql-proxy --tail=5
```
(Stopping the instance rather than revoking `cloudsql.client`: the proxy keeps its certificate for up to an hour
and the pool keeps open connections, so a revoke may not show anything.)

| Evidence | Meaning |
|---|---|
| Watch `2/2` → **`1/2 Running`**, **`RESTARTS 0`** | Readiness failed, liveness didn't |
| `describe`: proxy `Ready: True`, api `Ready: False`, both `Restart Count: 0` | Only the api container is unready |
| First failure `context deadline exceeded`, then `statuscode: 503` ×12 | The check first hung (3 s connect timeout vs the 4 s probe timeout), then failed fast |
| EndpointSlice `ready=false` | Out of the Service's rotation |
| Proxy `dial tcp …:3307: i/o timeout` | Port **3307**: the proxy's channel to the instance |

Restore, without touching the pod:
```bash
gcloud sql instances patch exit-ticket-db --activation-policy=ALWAYS
gcloud sql operations wait OPERATION_ID --timeout=900
```
The start took 11 min 39 s; the api went back to **`2/2` by itself, `RESTARTS 0`** (`pool_pre_ping` replaced the dead
connections), and the data was intact: **5 answers, latest 03:17:09**.

---

## 12. Cloud Logging

The two containers log their text under **different fields**: our app's JSON uses `message` →
`jsonPayload.message`; the proxy's `message` arrives as **`jsonPayload.textPayload`** (`--structured-logs` still sets
`severity`, so severity filters work for both).

```bash
gcloud logging read 'resource.type="k8s_container" AND resource.labels.cluster_name="exit-ticket" AND resource.labels.namespace_name="exit-ticket" AND resource.labels.container_name="api" AND severity>=WARNING' --freshness=1h --limit=10 --format="table(timestamp,severity,jsonPayload.logger,jsonPayload.message)"
gcloud logging read 'resource.type="k8s_container" AND resource.labels.cluster_name="exit-ticket" AND resource.labels.container_name="cloud-sql-proxy" AND severity>=ERROR' --freshness=2h --limit=6 --format="table(timestamp,resource.labels.pod_name,jsonPayload.textPayload)"
gcloud logging read 'resource.type="k8s_container" AND resource.labels.cluster_name="exit-ticket" AND resource.labels.container_name="cloud-sql-proxy" AND jsonPayload.textPayload:"ready for new connections"' --freshness=2h --limit=5 --format="table(timestamp,resource.labels.pod_name,jsonPayload.textPayload)"
```

| Query | Result |
|---|---|
| api, WARNING+ | `Readiness check failed: database unreachable` (`app.routes.health`) every 5 s during the drill; the last at 03:38:22, just before the start completed |
| proxy, ERROR | `dial tcp …:3307: i/o timeout` during the drill; two `connection reset by peer` at 03:38:29 (the api discarding dead pooled connections: harmless) |
| proxy startups | Exactly three: the second migrate pod, the first api pod, the replacement api pod. **The first migrate pod is absent**: its volume never mounted, so its proxy never started |

To find a field when a column comes back empty, look at one raw entry:
`--limit=1 --format="yaml(severity,jsonPayload,textPayload)"`.

---

## 13. Teardown

Ctrl+C every proxy, port-forward and watch, then:
```bash
kubectl delete namespace exit-ticket
gcloud container clusters delete exit-ticket --region=us-east4
gcloud sql instances patch exit-ticket-db --activation-policy=NEVER
```
(The cluster delete and the Cloud SQL stop are independent: two tabs. No Ingress was applied, so there was no load
balancer to delete first.)

Verified: no clusters; `exit-ticket-db` `STOPPED`; no forwarding rules, backend services, NEGs, disks, addresses or
instances; only the four `default-allow-*` firewall rules; no `cloud-sql-proxy` process on the Mac. The stopped
instance still bills its disk, IP and backups; per the [teardown policy](phase-4-design.md#11-teardown-policy-enforced)
it's deleted if no session follows within 7 days.

---

## 14. Troubleshooting

| Symptom | Where to look | Likely cause | Fix |
|---|---|---|---|
| Pod stuck at `Init:0/1`; event `FailedMount … PermissionDenied … secretmanager.versions.access` | `kubectl -n exit-ticket get events --sort-by=.lastTimestamp` | `secretAccessor` granted to the wrong identity (annotated KSA → the **Google SA** reads), or not on that secret | Grant `secretAccessor` on each secret to `exit-ticket-api@…` |
| `FailedMount … driver name secrets-store-gke.csi.k8s.io not found` | Events | Brand-new node (transient), or the cluster lacks `--enable-secret-manager` | Wait one retry; else check `secretManagerConfig` |
| `no matches for kind "SecretProviderClass"` on apply | apply output | Add-on not enabled | `gcloud container clusters update … --enable-secret-manager` |
| Proxy: `403` / `NOT_AUTHORIZED` / `cloudsql.instances.connect` denied | `kubectl logs … -c cloud-sql-proxy` | Google SA lacks `roles/cloudsql.client` | Grant it on the project |
| Proxy or metadata: `iam.serviceAccounts.getAccessToken` denied / `Unable to generate access token` | Proxy log; the metadata check in [§8](#8-workload-identity-checked-from-inside-the-pod) | Missing `roles/iam.workloadIdentityUser` binding, wrong annotation, or wrong `serviceAccountName` | Bind `serviceAccount:PROJECT_ID.svc.id.goog[exit-ticket/api]`; check the KSA annotation |
| Metadata check prints `exit-ticket-nodes@…` or `…-compute@…` | [§8](#8-workload-identity-checked-from-inside-the-pod) | Pod not running as KSA `api`, or annotation missing | `serviceAccountName: api`; re-apply `serviceaccount.yaml` |
| api log: `Cloud SQL IAM service account authentication failed` / `password authentication failed` | `kubectl logs … -c api` | No IAM database user, missing `roles/cloudsql.instanceUser`, or `cloudsql.iam_authentication` off | See [4A §5](phase-4a-cloud-sql.md#5-database-and-iam-user) |
| Proxy: `dial tcp …:3307: i/o timeout`; startup probe failing | Proxy log, events | Instance stopped or still starting | `gcloud sql instances list` → `RUNNABLE`; wait for the start |
| Proxy: `invalid instance connection name` / `instance does not exist` / 404 | Proxy log | Wrong `PROJECT:REGION:INSTANCE`, or `PROJECT_ID` not substituted (applied without `sed`) | Fix the arg; always apply with `sed` |
| `CreateContainerConfigError` | `describe pod` → Events | A referenced ConfigMap or key is missing (e.g. api applied before the ConfigMap, or an old ConfigMap without `DATABASE_URL`) | Apply `configmap.yaml` with `sed`, then `rollout restart` |
| Job never completes (`1/2 Running` forever) | `get pods` | Proxy in `containers` instead of a native sidecar | `initContainers` + `restartPolicy: Always` |
| Job `DeadlineExceeded` | `describe job` | Pod never started (mount/probe) within `activeDeadlineSeconds` | Fix the cause, `delete job`, re-apply |
| `gcloud sql instances patch … failed … taking longer than expected` | `gcloud sql operations list` | gcloud stopped waiting; the operation continues | `gcloud sql operations wait ID` |
| curl `000` through the port-forward | The port-forward tab | Port-forward died (its pod was replaced) | Restart it |
| Proxy log column empty in a `gcloud logging read` table | One raw entry as YAML | Text is in `jsonPayload.textPayload` | Use that field |

---

## 15. Lessons learned

| What happened | Cause | Lesson |
|---|---|---|
| First migrate Job: `FailedMount PermissionDenied`, then `DeadlineExceeded` | Secret access granted to the KSA principal; an annotated KSA acts as the Google SA | Test the exact identity setup you deploy; events explain a stuck `Init:0/1` |
| Removing the KSA grant and seeing the api mount anyway | (none) | A controlled removal proves which grant is actually used |
| Cloud SQL start: `failed`, but it was fine | gcloud's wait timeout | Check `operations list`, not the CLI's exit status |
| Starting Cloud SQL took ~11.5 min, twice | Shared-core instance from stopped | Start it first; build the cluster in parallel |
| `created_at` doesn't exist | The column is `answered_at` | Check the model before writing queries |
| curl `000` in the drill | The port-forward had died with the replaced pod | `000` = no connection at all, not an HTTP error |
| Proxy messages blank in Cloud Logging | Stored as `jsonPayload.textPayload` | Inspect one raw entry before writing filters |

---

## 16. Interview talking points

**1. "Stateless pods, managed state."** I replaced the api pod and stopped the database: no data lost, and the api
dropped out of rotation and came back by itself with zero restarts, because liveness and readiness check different
things.

**2. "No keys, no database password, no Kubernetes Secrets."** Nodes, pods and the database login all use short-lived
tokens from Workload Identity and IAM; secrets are files mounted from Secret Manager.

**3. "Native sidecars."** The Auth Proxy starts before the app, which waits for its startup probe, and the migration
Job completes on its own because Kubernetes stops the sidecar.

**4. "Debugging from evidence."** The first Job hung; events showed `PermissionDenied` on the secret mount, which told
me the add-on was acting as the annotated Google SA. I granted it, removed the unused grant, and the next pod's
successful mount proved it.

**5. "Least-privilege nodes."** The cluster runs on a dedicated node SA with the GKE node role and read access to one
repository, instead of the default SA with Editor.

---

## ACE exam notes

**How GKE talks to Cloud SQL**

| Option | How | Notes |
|---|---|---|
| **Cloud SQL Auth Proxy sidecar** | App → `127.0.0.1` → proxy → mutual TLS on port 3307 | IAM-authorized, encrypted, works with public or private IP; Google's recommended pattern for GKE |
| Cloud SQL language connectors | A library in the app | Same security; only some drivers |
| Private IP, direct | App → instance's private IP in the VPC | Needs private services access (VPC peering) or Private Service Connect; still use TLS and IAM/passwords |
| Public IP + authorized networks | App → instance's public IP from an allowlisted address | Avoid for GKE (below) |

**Why the Auth Proxy beats authorized networks**
- Authorized networks allow **IP addresses**, not identities. GKE pod traffic leaves from **node IPs that change**
  as Autopilot adds and replaces nodes; allowlisting would need Cloud NAT with a reserved static IP, and then *every*
  workload behind that NAT is allowed.
- The proxy authorizes **each workload's identity** through IAM (`roles/cloudsql.client`), encrypts with short-lived
  certificates, needs no allowlist, and is audited (`cloudsql.instances.connect`).
- With **connector enforcement REQUIRED**, only the proxy or connectors can connect at all.

**Workload Identity**
- A KSA annotated with `iam.gke.io/gcp-service-account` acts as that Google SA (needs
  `roles/iam.workloadIdentityUser` for `serviceAccount:PROJECT_ID.svc.id.goog[NS/KSA]`); without the annotation, grant
  IAM to the KSA's `principal://` identifier.
- Check from a pod: the metadata server's `service-accounts/default/email`.

**Probes and sidecars**
- Liveness failure → restart; readiness failure → out of the Service, no restart; startup → gates the others.
- Native sidecar = init container with `restartPolicy: Always`: starts first, can gate the main container with a
  startup probe, is stopped after it; lets Jobs complete.

**Autopilot**
- Bills every container's requests, sidecars included. Node SA set at creation (`--service-account`). Add-ons such as
  `--enable-secret-manager` at creation or with `clusters update`.

**Cloud SQL operations**
- `--activation-policy=NEVER|ALWAYS` to stop/start; `gcloud sql operations list|wait`.
