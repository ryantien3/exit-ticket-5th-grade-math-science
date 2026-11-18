# Phase 5E — GKE Autopilot in the Terraform cluster layer

Phase 5E builds the GKE Autopilot cluster with Terraform, in its own **`cluster` layer** that is created and
destroyed every session. The cluster runs on the custom VPC from [5C](phase-5c-network.md) with **private nodes**
and **no external control plane endpoint**: kubectl on the Mac goes through the **IAM-authorized DNS endpoint**.
The app was deployed with kubectl as before, with one change: the Auth Proxy sidecars use **`--private-ip`**, so pods
reach Cloud SQL at `10.40.0.3` through private services access. The 4C drills were repeated, with Cloud SQL now
stopped and started **through Terraform**. Everything was torn down at the end.

| Part | What | PR |
|---|---|---|
| **5A** | State bucket, layer layout | #21 |
| **5B** | Foundation part 1: imports, Editor removed | #22 |
| **5C** | Foundation part 2: VPC, subnet, private services access | #23 |
| **5D** | `data` layer: Cloud SQL imported, private IP | #25 |
| **5E** | `cluster` layer: private Autopilot cluster, DNS endpoint; `--private-ip`; drills; Terraform teardown | (this PR) |

> **About the commands in this doc.** Code blocks contain commands only, no `#` comments: in zsh, `#` is not a
> comment unless `setopt interactivecomments` is on. Run them from the repo root unless a block starts with `cd`.
> No project ID, project number or endpoint name is committed.

> **Placeholders**
>
> | Placeholder | Meaning |
> |---|---|
> | `PROJECT_ID` | Your GCP project ID (`gcloud config get project`) |

**Contents**

1. [Concepts](#1-concepts)
2. [The code](#2-the-code)
3. [A session, start to finish](#3-a-session-start-to-finish)
4. [Creating the cluster](#4-creating-the-cluster)
5. [Verifying the cluster](#5-verifying-the-cluster)
6. [Deploying with --private-ip](#6-deploying-with---private-ip)
7. [Verification and drills](#7-verification-and-drills)
8. [Teardown](#8-teardown)
9. [Troubleshooting](#9-troubleshooting)
10. [Known gaps](#10-known-gaps)
11. [Lessons learned](#11-lessons-learned)
12. [Interview talking points](#12-interview-talking-points)
13. [ACE exam notes](#ace-exam-notes)

---

## 1. Concepts

```
 Mac: kubectl ──HTTPS + IAM token──► DNS endpoint gke-….us-east4.gke.goog   (external IP endpoint: OFF)
                                            │ container.clusters.connect, then RBAC
 exit-ticket-vpc ───────────────────────────▼───────────────────────────────────────────────────────┐
 │  control plane internal endpoint 10.10.0.2                                                       │
 │  subnet 10.10.0.0/20: private nodes (no external IPs, VMs hidden by Autopilot)                   │
 │    pod api 10.20.0.x ─ api ──127.0.0.1:5432── cloud-sql-proxy --private-ip ──┐                   │
 │    pod web 10.20.0.x ─ nginx ──► Service api 10.30.x.x                         │ TLS 3307          │
 │  Private Google Access → Artifact Registry, gcr.io, Secret Manager, Cloud SQL Admin, Logging     │
 └─────────────────────────────────────────────────────────── VPC peering (PSA) ──▼─────────────────┘
                                                          Cloud SQL exit-ticket-db  10.40.0.3
```

### Private nodes vs control plane access

Two separate choices:
- **Private nodes**: worker VMs have only internal IPs; nothing on the internet can reach them.
- **The control plane endpoint**: where kubectl (and the nodes) send Kubernetes API requests.

| Option | kubectl from the Mac | Verdict |
|---|---|---|
| A. External IP endpoint, no authorized networks (3C, 4C) | Public IP, open to the internet; authentication still required | Scannable by anyone |
| B. External IP + **authorized networks** | Only from allowlisted CIDRs | Allows an **IP, not a person**; breaks when the Mac's IP changes |
| C. Internal IP endpoint only | Only from inside the VPC (bastion + IAP, VPN) | No Mac access without more infrastructure |
| **D. DNS-based endpoint, external IP off (chosen)** | `gke-….REGION.gke.goog` from anywhere; IAM (`container.clusters.connect`) checks every request before Kubernetes RBAC | Identity-based, no allowlist, no bastion |

D is the same idea as the Cloud SQL Auth Proxy: identity, not IP address. The **internal** IP endpoint stays on for
the nodes.

### Why no Cloud NAT

Private nodes can't reach the public internet, and don't need to: images come from Artifact Registry and
`gcr.io/cloud-sql-connectors`, and Secret Manager, the Cloud SQL Admin API and Logging are Google APIs, all reachable
through **Private Google Access** ([5C](phase-5c-network.md#cloud-nat)). NAT would bill hourly plus per GB for
nothing. If a workload ever needs Docker Hub or PyPI, NAT belongs in this layer so it's destroyed with the cluster.

### Why the manifests stay with kubectl until Phase 7

- **Chicken and egg:** the `kubernetes` provider needs the cluster's endpoint and credentials, which don't exist until
  the cluster does; one layer doing both is a known source of broken plans.
- **Different lifecycles:** infrastructure changes rarely, the app on every commit (new digest). Phase 7's Cloud Build
  deploys manifests per push.
- **Kubernetes already reconciles** its own desired state; a Terraform copy would only drift.

### `deletion_protection = false`, on purpose

The provider defaults to `true`, which makes every `terraform destroy` fail until the code is edited. Right for
things that hold data; wrong for a layer that is **destroyed every session** and holds nothing: data is in Cloud SQL
(three locks, [5D §6](phase-5d-cloud-sql.md#6-delete-protection)), images and secrets in foundation (locked). Keeping
the lock would only teach switching it off without thinking.

---

## 2. The code

[`infra/terraform/cluster/`](../infra/terraform/cluster/): `versions.tf` and `.terraform.lock.hcl` copied from
`data/`, `backend.tf` (prefix `cluster`), `variables.tf`, `providers.tf` (read-only `terraform_remote_state` for
`foundation` and `data`), [`gke.tf`](../infra/terraform/cluster/gke.tf), `outputs.tf`. Every field was checked in
the 8.11 schema (`terraform providers schema -json`).

| Requirement | Field | Value |
|---|---|---|
| Autopilot, us-east4 | `enable_autopilot`, `location` | `true`, `us-east4` (Autopilot is regional) |
| Release channel | `release_channel.channel` | `REGULAR` |
| Network | `network`, `subnetwork`, `networking_mode` | foundation `network_id`, `subnet_id`; `VPC_NATIVE` |
| Pod and Service ranges | `ip_allocation_policy.cluster_secondary_range_name` / `services_secondary_range_name` | foundation `pods_range_name` / `services_range_name` |
| Private nodes | `private_cluster_config.enable_private_nodes` | `true` |
| External IP endpoint off | `private_cluster_config.enable_private_endpoint` | `true` |
| Required with it | `master_authorized_networks_config` (no `cidr_blocks`), `gcp_public_cidrs_access_enabled`, `private_endpoint_enforcement_enabled` | on, `false`, `false` ([§4](#4-creating-the-cluster)) |
| DNS endpoint | `control_plane_endpoints_config.dns_endpoint_config.allow_external_traffic` | `true` |
| Internal IP endpoint | `control_plane_endpoints_config.ip_endpoints_config.enabled` | `true` |
| Workload Identity | `workload_identity_config.workload_pool` | `PROJECT_ID.svc.id.goog` |
| Secret Manager add-on | `secret_manager_config.enabled` | `true` |
| Node SA | **`cluster_autoscaling.auto_provisioning_defaults.service_account`** | foundation `nodes_sa_email`. Autopilot creates the node pools, so the SA is a node auto-provisioning default |
| | `deletion_protection` | `false` |
| | `resource_labels` | `app`, `env` (the provider adds `goog-terraform-provisioned = "true"` to anything it creates) |

**Not duplicated:** the `workloadIdentityUser` binding for `PROJECT_ID.svc.id.goog[exit-ticket/api]` lives in
foundation. The pool is per **project**, not per cluster, so it keeps working across rebuilds.

Outputs: `cluster_name`, `dns_endpoint`, `get_credentials`, and `cloud_sql_connection_name` /
`cloud_sql_private_ip` passed through from `data`.

---

## 3. A session, start to finish

```bash
cd ~/projects/exit-ticket-5th-grade-math-science/infra/terraform/data
terraform apply -var="activation_policy=ALWAYS"
cd ../cluster
terraform apply
gcloud container clusters get-credentials exit-ticket --region=us-east4 --dns-endpoint
```
Start Cloud SQL first (~11–13 minutes); the cluster takes ~4.5. Then deploy ([§6](#6-deploying-with---private-ip)),
work, and tear down ([§8](#8-teardown)). While Cloud SQL runs, every plan in `data/` needs the `-var`.

---

## 4. Creating the cluster

```bash
cd ~/projects/exit-ticket-5th-grade-math-science/infra/terraform/cluster
terraform init -backend-config="bucket=$(gcloud config get project)-tfstate"
terraform plan -out=tfplan
terraform apply tfplan
rm tfplan
```

**First apply: refused.** `Error 400: … enable_master_authorized_networks should be enabled if private endpoint is
enabled.` Turning off the external IP endpoint requires master authorized networks to be on, even with an empty
list. `validate` can't catch it (it checks the schema, not which settings go together). Nothing was created (0 state
entries).

**Fix:** the block with no `cidr_blocks`. The schema: *"Omit the nested cidr_blocks attribute to disallow external
access (except the cluster node IPs, which GKE automatically whitelists)."* It filters only the **IP** endpoints; the
DNS endpoint is guarded by IAM.

**Second apply:** `Creation complete after 4m26s`, then `terraform plan` → **`No changes`** (Google filled in dozens
of fields and none disagreed with the code).

---

## 5. Verifying the cluster

```bash
gcloud container clusters get-credentials exit-ticket --region=us-east4 --dns-endpoint
kubectl config view --minify --output=jsonpath='{.clusters[0].cluster.server}{"\n"}'
kubectl get nodes -o wide
gcloud container clusters describe exit-ticket --region=us-east4 --format="yaml(autopilot.enabled,releaseChannel.channel,network,subnetwork,ipAllocationPolicy.clusterSecondaryRangeName,ipAllocationPolicy.servicesSecondaryRangeName,privateClusterConfig,controlPlaneEndpointsConfig,masterAuthorizedNetworksConfig,autoscaling.autoprovisioningNodePoolDefaults.serviceAccount,secretManagerConfig,workloadIdentityConfig)"
gcloud compute firewall-rules list --filter="network:exit-ticket-vpc" --format="table(name,direction,priority,sourceRanges.list(),allowed[].map().firewall_rule().list(),targetTags.list())"
```

| Check | Result (2026-11-18) |
|---|---|
| kubectl server | `https://gke-….us-east4.gke.goog`: a name, not an IP |
| Cluster | Autopilot, `REGULAR`, `exit-ticket-vpc` / `exit-ticket-us-east4`, ranges `pods` / `services` |
| Endpoints | `enablePrivateNodes: true`, `enablePublicEndpoint: false`, internal `privateEndpoint: 10.10.0.2`, authorized networks on and empty, DNS `allowExternalTraffic: true` |
| Node SA, add-on, WI | `exit-ticket-nodes@…`, `secretManagerConfig.enabled: true`, `workloadPool: PROJECT_ID.svc.id.goog` |
| Nodes right after creation | **None.** Autopilot runs zero nodes until a workload needs one |

**The external IP endpoint, tested.** `privateClusterConfig.publicEndpoint` still lists an external address: GKE
allocates it, but `enablePublicEndpoint: false` disables the endpoint on it.
```bash
curl -sk --max-time 10 -o /dev/null -w "%{http_code}\n" "https://$(gcloud container clusters describe exit-ticket --region=us-east4 --format='value(privateClusterConfig.publicEndpoint)')/version"
curl -s --max-time 10 -o /dev/null -w "%{http_code}\n" "https://$(terraform -chdir=infra/terraform/cluster output -raw dns_endpoint)/version"
kubectl version
```
External IP **`000`** (nothing answers); DNS endpoint without credentials **`403`** (reachable, refused);
`kubectl version` as you: `Server Version: v1.36.4-gke.2377000`.

**Firewall rules GKE created** (deleted with the cluster):

| Rule | Allows | From | To |
|---|---|---|---|
| `gke-exit-ticket-HASH-all` | All protocols | `10.20.0.0/17` (Pods) | Nodes tagged `gke-exit-ticket-HASH-node` |
| `gke-exit-ticket-HASH-vms` | TCP/UDP 1–65535, ICMP | `10.10.0.0/20` (nodes) | The same nodes |

Internal only; nothing from `0.0.0.0/0`. No control-plane-to-node rule: newer clusters connect the control plane
through Private Service Connect.

### Autopilot hides its node VMs

`gcloud compute instances list` returned **0** and `instances describe` "not found", although the node's
`spec.providerID` pointed at a VM in this project. That's by design: in Autopilot, the underlying Compute Engine VMs
**aren't visible or accessible through gcloud or the Console**; nodes are seen only through kubectl
([GKE cluster architecture](https://cloud.google.com/kubernetes-engine/docs/concepts/autopilot-architecture)). The
node claims were checked another way:

| Claim | Evidence |
|---|---|
| No external IPs | `kubectl get nodes -o wide` → `EXTERNAL-IP <none>`, `INTERNAL-IP 10.10.0.3`; `enable_private_nodes = true` on the cluster and on the node pool's `network_config` |
| Node SA | The auto-provisioned pool `nap-…` in state: `service_account = "exit-ticket-nodes@…"`; images pulled (only that SA reads the repository) |

The node type was `ek-standard-8` (Autopilot's container-optimized compute platform); the datapath
`ADVANCED_DATAPATH` (GKE Dataplane V2).

---

## 6. Deploying with --private-ip

**Images:** unchanged. `git log 5fefc95..HEAD -- app/` → 0 commits, so the pinned digests (`api@sha256:55c56a56…`,
`web@sha256:eab714b7…`) are still the current code.

**The only manifest change**, in both proxy sidecars ([`api.yaml`](../k8s/api.yaml),
[`migrate-job.yaml`](../k8s/migrate-job.yaml)):
```diff
             - --exit-zero-on-sigterm
+            - --private-ip
             - PROJECT_ID:us-east4:exit-ticket-db
```
The proxy still uses the connection name, the Admin API (certificate as `exit-ticket-api`), mutual TLS on 3307 and
IAM login; only the address it dials changes. Without a private IP it fails loudly instead of falling back.

Deployed in the 4C order ([4C §6](phase-4c-gke-cloud-sql.md#6-deploying)): namespace, ConfigMap, SA,
SecretProviderClass → migrate Job → api and web.

| Step | Result |
|---|---|
| Objects | Created; `get secrets` → `No resources found` |
| Migrate Job | Autopilot created the first node (~2 min); secrets mounted first try; proxy ready in 3 s; alembic at head, no upgrades; `condition met` |
| App | `api 2/2`, `web 1/1`, all `RESTARTS 0`; pod IPs `10.20.0.x` (the Pod range); Autopilot added a second node |

---

## 7. Verification and drills

### Workload Identity, from inside the pod

The four checks from [4C §8](phase-4c-gke-cloud-sql.md#8-workload-identity-checked-from-inside-the-pod): identity
`exit-ticket-api@…` (not the node SA); token `Bearer`, 3576 s, 1024 characters; secret files 64 and 14 bytes, **0**
secret env vars; `/readyz` `{"status":"ready"}`.

### Proof the proxy uses the private IP

Containers in a pod share one network namespace, so the api container can read the proxy's sockets in
`/proc/net/tcp` (the proxy image has no shell). Connections with remote port 3307 (`0CEB`):
```bash
kubectl -n exit-ticket exec deploy/api -c api -- python -c 'import socket,struct; h=lambda x: "%s:%d" % (socket.inet_ntoa(struct.pack("<I",int(x.split(":")[0],16))), int(x.split(":")[1],16)); rows=[l.split() for l in open("/proc/net/tcp").read().splitlines()[1:]]; [print("local", h(r[1]), "-> remote", h(r[2]), "state", r[3]) for r in rows if r[2].endswith(":0CEB")]'
```
**`local 10.20.0.19:59778 -> remote 10.40.0.3:3307 state 01`**: from the pod's IP to Cloud SQL's private IP,
ESTABLISHED. During the outage drill the proxy's error also named `10.40.0.3:3307`.

### The app

```bash
kubectl -n exit-ticket port-forward svc/web 8080:80
```
(one tab, left running), then in another:
```bash
curl -s -o /dev/null -w "/ %{http_code}\n" http://localhost:8080/
kubectl -n exit-ticket exec deploy/api -c api -- python -c "import os,psycopg; c=psycopg.connect(os.environ['DATABASE_URL'].replace('+psycopg','')); print(c.execute('SELECT count(*), max(answered_at) FROM answers').fetchone())"
gcloud secrets versions access 1 --secret=teacher-passcode | pbcopy
```
The count runs **inside the api pod**, through its own proxy (the private path); `DATABASE_URL` is SQLAlchemy's
`postgresql+psycopg://` form, so `+psycopg` is dropped for psycopg. Teacher sign-in, Release, a student submission
in a private window, the dashboard showed it; `pbcopy < /dev/null` afterwards. Answers **5 → 9**, latest
2026-11-18 02:36:39 UTC (the 5 from 4C survived the 5D import, the private-IP restart and a new cluster).

### Drill: replace the api pod

```bash
kubectl -n exit-ticket delete pod -l app=api
kubectl -n exit-ticket rollout status deployment/api --timeout=300s
```
New pod and IP (`10.20.0.22`), `2/2`, `RESTARTS 0` in ~30 s; still **9 answers, latest 02:36:39**; its proxy opened
four connections (the connection pool), all to `10.40.0.3:3307`. The port-forward to `svc/web` survived (it
targets a web pod).

### Drill: stop Cloud SQL through Terraform

Three tabs: the port-forward, a watch (`kubectl -n exit-ticket get pods -l app=api -w`), and commands:
```bash
cd ~/projects/exit-ticket-5th-grade-math-science/infra/terraform/data
terraform apply
cd ~/projects/exit-ticket-5th-grade-math-science
kubectl -n exit-ticket get endpointslices -l kubernetes.io/service-name=api -o jsonpath='{range .items[*].endpoints[*]}{.addresses[0]} ready={.conditions.ready}{"\n"}{end}'
kubectl -n exit-ticket describe pod -l app=api | grep -E "Readiness probe failed|Restart Count|^    Ready:"
kubectl -n exit-ticket logs deploy/api -c cloud-sql-proxy --tail=5
```

| Evidence | Meaning |
|---|---|
| Plan only `~ activation_policy = "ALWAYS" -> "NEVER"`; **51 s**; `STOPPED NEVER` | Stop through Terraform |
| Watch `2/2` → **`1/2`**, `RESTARTS 0` | Readiness failed, liveness didn't |
| EndpointSlice `ready=false` | Out of rotation |
| Proxy `Ready: True`, api `Ready: False`, both `Restart Count: 0` | Only the api is unready |
| `context deadline exceeded`, then `503` ×10 | As in 4C: the first check hung, then failed fast |
| `/api/readyz` through nginx: **`502 Bad Gateway`** | The api Service had no ready endpoint |
| Proxy: **`dial tcp 10.40.0.3:3307: i/o timeout`** | Still the private IP |

Restore, without touching the pod:
```bash
cd ~/projects/exit-ticket-5th-grade-math-science/infra/terraform/data
terraform apply -var="activation_policy=ALWAYS"
```
**12 min 48 s**; the same pod went back to **`2/2` by itself, `RESTARTS 0`** (at pod age 19 min, after `1/2` at
5 min); `ready=true`; `/api/readyz` 200 through nginx; still **9 answers, latest 02:36:39**.

### Ingress: skipped

The web → load balancer → NEG path hasn't changed since [3C §9](phase-3c-gke.md#9-public-http-through-the-ingress),
and it bills hourly. Note for next time: with private nodes, the Ingress controller still creates the
`k8s-fw-l7-…` rule for Google's health checkers on `exit-ticket-vpc`.

---

## 8. Teardown

Ctrl+C the port-forward and the watch, then:
```bash
cd ~/projects/exit-ticket-5th-grade-math-science
kubectl delete namespace exit-ticket
cd infra/terraform/cluster
terraform destroy
cd ../data
terraform apply
```
- `terraform destroy` in `cluster/` can only touch the one resource in its state: `Plan: 0 to add, 0 to change, 1
  to destroy`; **`Destruction complete after 5m3s`**.
- A plain `apply` in `data/` stops Cloud SQL (51 s).

### Verify

The [Phase 4 §7](phase-4-cloud-sql.md#7-teardown) list, plus the cluster layer:
```bash
echo "--- clusters"; gcloud container clusters list
echo "--- forwarding rules"; gcloud compute forwarding-rules list
echo "--- backend services"; gcloud compute backend-services list
echo "--- NEGs"; gcloud compute network-endpoint-groups list
echo "--- disks"; gcloud compute disks list
echo "--- addresses"; gcloud compute addresses list
echo "--- instances"; gcloud compute instances list
echo "--- sql"; gcloud sql instances describe exit-ticket-db --format="value(state,settings.activationPolicy,settings.deletionProtectionEnabled)"
echo "--- backups"; gcloud sql backups list --instance=exit-ticket-db --format="table(id,windowStartTime,type,status)"
echo "--- firewall rules, all networks"; gcloud compute firewall-rules list --format="table(name,network.basename(),sourceRanges.list())"
echo "--- firewall rules on exit-ticket-vpc"; gcloud compute firewall-rules list --filter="network:exit-ticket-vpc" --format="value(name)" | wc -l
echo "--- terraform cluster state"; terraform -chdir=infra/terraform/cluster state list | wc -l
echo "--- processes"; pgrep -fl "cloud-sql-proxy|kubectl" || echo "none"
```

| Section | Healthy (verified 2026-11-18) |
|---|---|
| clusters, forwarding rules, backend services, NEGs, disks, instances | Empty |
| addresses | Only `exit-ticket-psa 10.40.0.0/20 INTERNAL VPC_PEERING exit-ticket-vpc RESERVED` (foundation; free) |
| sql | `STOPPED NEVER True` |
| backups | The 5D `ON_DEMAND` backup |
| firewall rules | Only the four `default-allow-*` on `default`; **0** on `exit-ticket-vpc` (the `gke-…` rules went with the cluster) |
| cluster state | `0` entries: ready for the next `apply` |
| processes | `none` |

Optional: `kubectl config delete-context gke_PROJECT_ID_us-east4_exit-ticket` (the context points at an endpoint that
no longer exists; the next `get-credentials` rewrites it anyway).

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Error 400: … enable_master_authorized_networks should be enabled if private endpoint is enabled` | External IP endpoint off without authorized networks | `master_authorized_networks_config {}` with no `cidr_blocks` ([§4](#4-creating-the-cluster)) |
| `terraform destroy` fails: deletion protection | `deletion_protection` unset (defaults to `true`) | `false` in this layer only |
| kubectl hangs or `Unable to connect to the server` | Kubeconfig points at the (disabled) external IP | `get-credentials … --dns-endpoint` |
| kubectl `403` / `container.clusters.connect` denied | Your identity lacks the permission (DNS endpoint is IAM-checked) | `roles/container.developer` or higher |
| `gcloud compute instances list` empty, `describe` not found, but `kubectl get nodes` shows nodes | Autopilot hides its VMs | Use kubectl, the cluster's `describe`, and state ([§5](#autopilot-hides-its-node-vms)) |
| `kubectl get nodes` → `No resources found` on a new cluster | Autopilot scales to zero without workloads | Apply something; a node appears in 1–3 minutes |
| Proxy: instance has no IP of type PRIVATE | `--private-ip` with no private IP on the instance | Phase 5D §7 |
| Proxy: `dial tcp 10.40.0.3:3307: i/o timeout` | Instance stopped or starting; or no route (peering) | `terraform apply -var="activation_policy=ALWAYS"` in `data/`; check `peerings list` |
| `--private-ip` proxy on the Mac never connects | The Mac is outside the VPC | Use the public path there (no `--private-ip`) |
| `502 Bad Gateway` from nginx on `/api/…` | The api Service has no ready endpoints | Check `/readyz` and the database |
| `-w` watch "never finishes" | Watches run until Ctrl+C | Expected |
| A plain `plan` in `data/` wants `ALWAYS -> NEVER` | The variable defaults to stopped | Pass the `-var` while it runs |

---

## 10. Known gaps

| Gap | Why it's acceptable now | Later |
|---|---|---|
| `private_endpoint_enforcement_enabled = false`: the internal IP endpoint is open to the whole VPC | Only this cluster lives in it | Turn on and retest; or disable IP endpoints entirely (DNS only) |
| Cloud SQL public IP still exists | Mac admin and seed path (decision d) | Seed as a Job; drop `ipv4_enabled` |
| Manifests applied by hand with `sed` | Phase 7 | Cloud Build |
| No Ingress tested on this cluster | Unchanged path, hourly cost | Next session that needs a public URL |
| One replica each, no PodDisruptionBudget | Cost | Two replicas for real traffic |
| No NetworkPolicy | Dataplane V2 supports it; one app | Phase 8: default-deny + allow web → api |
| `ttlSecondsAfterFinished` deletes the Job after an hour | Logs also in Cloud Logging | — |

---

## 11. Lessons learned

| Session | What happened | Lesson |
|---|---|---|
| 5E | First create refused with a 400 on authorized networks | Some rules about which settings combine live only in the API; `validate` can't see them. A refused create creates nothing: check state, fix, re-plan |
| 5E | A `publicEndpoint` address existed although the endpoint was disabled | Allocated ≠ enabled: test reachability (`000` vs `403`) instead of reading fields |
| 5E | `instances list` showed 0 VMs while kubectl showed nodes | Autopilot hides its VMs; check the docs before assuming something broke |
| 5E | `kubectl get nodes` empty after the create | Autopilot scales to zero; nodes come with workloads |
| 5E | The proxy's destination was invisible in its logs | Containers in a pod share the network namespace: read `/proc/net/tcp` from a container that has Python |
| 5E | A watch was started in the commands tab | Name every tab, and say "open a new tab" explicitly |
| 5E | The start through Terraform took 12m48s, the stop 51s | Start Cloud SQL first; it's the long pole of every session |
| 5E | The destroy plan listed hundreds of `-` lines | Read the header: which resources, and the `Plan:` line |

---

## 12. Interview talking points

**1. "No public endpoint anywhere."** Private nodes, the control plane's external IP disabled (proven: no answer on
it), and kubectl through the DNS endpoint where IAM authorizes each request. No allowlists, no bastion.

**2. "Private data path, still identity-based."** Pods reach Cloud SQL at its private IP through private services
access, and I proved it from the pod's socket table; the proxy still does IAM login and mutual TLS.

**3. "Ephemeral by design."** The cluster layer is created and destroyed each session (about 4.5 and 5 minutes),
with `deletion_protection = false` only there; the data layer keeps three delete locks.

**4. "Resilience drills through IaC."** Stopping Cloud SQL with `terraform apply` took the api out of rotation with
zero restarts; starting it brought the same pod back by itself, data intact.

**5. "Reading the platform's real behaviour."** A refused create, a disabled-but-allocated endpoint IP, and hidden
Autopilot VMs: each checked against evidence or Google's docs before changing anything.

---

## ACE exam notes

**GKE networking**
- **VPC-native** clusters: Pod and Service IPs from subnet **secondary ranges** (alias IPs); required for Autopilot,
  private clusters and NEGs.
- **Private nodes**: internal IPs only; Google APIs through **Private Google Access**; the internet only through
  **Cloud NAT**.
- **Control plane access**: external IP endpoint (optionally **authorized networks**), internal IP endpoint (from the
  VPC; with `--enable-private-endpoint` the external one is off), and the **DNS-based endpoint** (IAM-authorized,
  reachable from anywhere; `get-credentials --dns-endpoint`). Old-style private clusters used VPC peering to the
  control plane; newer ones use **Private Service Connect**.
- GKE creates its own firewall rules (`gke-CLUSTER-…`) and removes them with the cluster; the Ingress controller adds
  the health-checker rule (`35.191.0.0/16`, `130.211.0.0/22`).

**Autopilot vs Standard**
- Autopilot: Google manages nodes (VMs **not visible** in Compute Engine), billed per Pod request, regional,
  Workload Identity always on, scales to zero nodes; no SSH, no privileged pods by default.
- Standard: you manage node pools (machine types, autoscaling, upgrades) and pay per node VM.
- Node identity: a dedicated SA (`--service-account` / the provisioning default), never the Compute Engine default
  SA with Editor.

**Clusters as code**
- `deletion_protection` (Terraform-side) blocks destroys; turn it off only for disposable layers.
- `gcloud container clusters create-auto` ↔ `google_container_cluster { enable_autopilot = true }`;
  `gcloud container clusters get-credentials` writes the kubeconfig entry.
- Release channels: Rapid, Regular, Stable (and Extended); maintenance windows and exclusions control when upgrades
  happen.
