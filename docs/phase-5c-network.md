# Phase 5C — VPC and private networking

Phase 5C adds a **custom VPC** to the Terraform `foundation` layer, ready for a **private GKE Autopilot cluster**
(VPC-native, no public node IPs) and **Cloud SQL private IP**: one us-east4 subnet with secondary ranges for Pods and
Services and Private Google Access, plus **private services access** (a reserved range and a VPC peering to Google's
service producer network). It's all free. The default network is left alone for now.

| Part | What | PR |
|---|---|---|
| **5A** | State bucket, layer layout | #21 |
| **5B** | Foundation part 1: imports, Editor removed | #22 |
| **5C** | Foundation part 2: VPC, subnet + secondary ranges, PGA, PSA range + peering, `PREVENT` on the network and peering | (this PR) |

> **About the commands in this doc.** Code blocks contain commands only, no `#` comments: in zsh, `#` is not a
> comment unless `setopt interactivecomments` is on. **Run them from `infra/terraform/foundation`** unless a block
> starts with `cd`.

> **Placeholders**
>
> | Placeholder | Meaning |
> |---|---|
> | `PROJECT_ID` | Your GCP project ID (`gcloud config get project`) |
> | `PROJECT_NUMBER` | The numeric project number |

**Contents**

1. [Concepts](#1-concepts)
2. [The IP plan](#2-the-ip-plan)
3. [The code](#3-the-code)
4. [Plan and apply](#4-plan-and-apply)
5. [Verification checklist](#5-verification-checklist)
6. [Destroy order and the peering problem](#6-destroy-order-and-the-peering-problem)
7. [Troubleshooting](#7-troubleshooting)
8. [Known gaps](#8-known-gaps)
9. [Lessons learned](#9-lessons-learned)
10. [Interview talking points](#10-interview-talking-points)
11. [ACE exam notes](#ace-exam-notes)

---

## 1. Concepts

```
 Project  ─  VPC "exit-ticket-vpc"  (custom mode; a VPC is global)
┌──────────────────────────────────────────────────────────────────────────────┐
│  Subnet "exit-ticket-us-east4"  (regional: us-east4)  Private Google Access ON │
│    primary range        10.10.0.0/20  ← GKE node IPs                          │
│    secondary "pods"     10.20.0.0/17  ← Pod IPs (alias IPs on the nodes)      │
│    secondary "services" 10.30.0.0/22  ← Kubernetes Service (ClusterIP) IPs    │
│                                                                              │
│  Reserved range "exit-ticket-psa"  10.40.0.0/20  (purpose VPC_PEERING)        │
└──────────────┬──────────────────────────────────┬────────────────────────────┘
               │ VPC peering                       │ Private Google Access
               │ servicenetworking-googleapis-com  │ (no public IP needed)
┌──────────────▼───────────────────────┐   ┌──────▼──────────────────────────────┐
│ Google's service producer network     │   │ Google APIs: Artifact Registry,      │
│ (tenant project …-tp)                 │   │ Secret Manager, Logging, Cloud SQL   │
│   Cloud SQL exit-ticket-db            │   │ Admin, IAM …                         │
│   private IP 10.40.x.y  (Phase 5D)    │   └─────────────────────────────────────┘
└───────────────────────────────────────┘
        ✗ public internet (Docker Hub, PyPI …): would need Cloud NAT, not built
        ✗ your Mac: outside the VPC, can't reach 10.x addresses
```

### Default, auto and custom networks

- A **VPC network is global**; **subnets are regional** (usable in every zone of their region).
- The **default network** is an **auto mode** network created with each project: a subnet in every region inside
  `10.128.0.0/9`, plus `default-allow-*` rules (SSH, RDP, ICMP from `0.0.0.0/0`; internal traffic).
- **Custom mode** (`auto_create_subnetworks = false`): only the subnets you create. **No allow rules**: only the two
  **implied rules** apply (deny all ingress, allow all egress, priority 65535, can't be deleted).

### Primary and secondary ranges (VPC-native GKE)

A subnet has one **primary** range (VM, i.e. node, IPs) and optional **secondary** ranges. In a **VPC-native**
cluster (Autopilot always is), each Pod gets a real VPC address from the Pod secondary range as an **alias IP** on
its node; Service ClusterIPs come from a second secondary range. So Pods are routable inside the VPC (NEGs send
load-balancer traffic straight to them, Phase 3C), replies from Cloud SQL's private IP route back without extra
translation, and the ranges are reserved in the VPC so nothing else can use them.

### Private Google Access (PGA)

Per subnet. Lets VMs **without public IPs** reach **Google APIs and services** (`*.googleapis.com`, `*.pkg.dev`,
`gcr.io`) over Google's network, along the default `0.0.0.0/0` route. That's how private nodes pull images from
Artifact Registry, mount Secret Manager secrets and ship logs.

### Private services access (PSA)

Managed services like Cloud SQL run in a **Google-owned service producer network**. PSA gives them addresses in your
IP plan: (1) **reserve a range** (`google_compute_global_address`, purpose `VPC_PEERING`); (2) **peer** your VPC with
Google's (`google_service_networking_connection`, via `servicenetworking.googleapis.com`); (3) the service takes a
private IP from the range. **Peering isn't transitive**: networks peered with yours can't reach Cloud SQL through it,
and the Mac (outside the VPC) can't reach it at all.

### Cloud NAT

Private nodes have no public IP, so they can't reach the **public internet** (Docker Hub, PyPI, webhooks). Cloud NAT
lends them a public address for outbound connections; it bills per hour per gateway plus per GB. **Not needed here:**
every image comes from Google's registries (PGA), and the app calls no external services. If that changes, NAT goes
in the `cluster` layer so it's deleted with the cluster.

---

## 2. The IP plan

`/n` → 2^(32−n) addresses; Google reserves **4** in each subnet's primary range. All ranges are **RFC 1918**
(`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`) and inside `10.0.0.0/9`, below the default network's
`10.128.0.0/9`.

| Range | CIDR | From – to | Addresses | Sizing |
|---|---|---|---|---|
| Subnet primary (nodes) | `10.10.0.0/20` | 10.10.0.0 – 10.10.15.255 | 4,092 usable | One per node; never the limit here |
| Secondary `pods` | `10.20.0.0/17` | 10.20.0.0 – 10.20.127.255 | 32,768 | Autopilot: up to 32 Pods per node, a **/26 (64) per node** (twice the Pods, so IPs aren't reused instantly). 32,768 ÷ 64 = **512 nodes**. Autopilot's default size |
| Secondary `services` | `10.30.0.0/22` | 10.30.0.0 – 10.30.3.255 | 1,024 | One per Service (we have 2–3). Unset, GKE would use a Google-managed `34.118.224.0/20` |
| PSA reserved range | `10.40.0.0/20` | 10.40.0.0 – 10.40.15.255 | 4,096 | Google carves per-service, per-region subnets from it; /24 is the minimum. Room for a second region or Memorystore |

Each range starts its own `10.N.0.0` block, leaving a gap to grow into. Secondary and PSA ranges are effectively
**fixed** once a cluster or Cloud SQL uses them; a primary range can be expanded, never shrunk.

---

## 3. The code

All in [`network.tf`](../infra/terraform/foundation/network.tf), ranges in one `locals` block.

| Resource | Key settings | Why |
|---|---|---|
| `google_compute_network.vpc` | `auto_create_subnetworks = false`, `routing_mode = "REGIONAL"`, `delete_default_routes_on_create = false`, `deletion_policy = "PREVENT"` | Custom mode; one region; keep the default route PGA uses; the cluster and data layers depend on it from other states |
| `google_compute_subnetwork.us_east4` | `10.10.0.0/20`, `private_ip_google_access = true`, `secondary_ip_range` `pods` and `services` | VPC-native GKE with private nodes |
| `google_compute_global_address.psa` | `purpose = "VPC_PEERING"`, `address_type = "INTERNAL"`, `10.40.0.0/20` | PSA step 1. "Global address" here is a private range, not a public IP |
| `google_service_networking_connection.psa` | `service = "servicenetworking.googleapis.com"`, `reserved_peering_ranges = [psa]`, `deletion_policy = "PREVENT"` | PSA step 2 ([§6](#6-destroy-order-and-the-peering-problem)) |

**Firewall rules: none of our own.** Ingress is denied by the implied rule. GKE creates the rules a cluster needs at
cluster creation (`gke-CLUSTER-…-all`, `-vms`) and deletes them with it; the Ingress controller adds `k8s-fw-l7-…`
for Google's health checkers (`35.191.0.0/16`, `130.211.0.0/22`), as seen in [Phase 3C](phase-3c-gke.md). Pods →
Cloud SQL is egress (allowed). An IAP SSH rule (`35.235.240.0/20`) would only be needed for a bastion VM.

**Flow logs: off** (billed as log ingestion; turn on to debug).

**The default network: untouched.** Phase 8 note: once nothing uses it, delete it (or at least its
`default-allow-ssh` and `default-allow-rdp` rules, open to `0.0.0.0/0`).

New outputs in [`outputs.tf`](../infra/terraform/foundation/outputs.tf): `network_id`, `network_name`, `subnet_id`,
`subnet_name`, `pods_range_name`, `services_range_name`, `psa_range_name`, `psa_peering`.

---

## 4. Plan and apply

```bash
terraform plan -out=tfplan
```
`Plan: 4 to add, 0 to change, 0 to destroy.` The 30 resources from 5B were only refreshed. What to read: the ranges
and `private_ip_google_access = true` on the subnet; `purpose`, `address_type`, `prefix_length` on the range;
`PREVENT` on the network and the connection; `network = (known after apply)` on the subnet, range and connection is
the dependency on the VPC, which is why Terraform creates them in order.

```bash
terraform apply tfplan
rm tfplan
```
Network 23 s → range 12 s and subnet 21 s (in parallel) → connection **51 s**. `4 added`. Output `psa_peering =
"servicenetworking-googleapis-com"`, the name every PSA peering gets. The connection's ID is URL-encoded
(`projects%2F…:servicenetworking.googleapis.com`). Then `terraform plan` → `No changes.` (35 entries in state).

---

## 5. Verification checklist

```bash
echo "--- networks"; gcloud compute networks list
echo "--- subnet"; gcloud compute networks subnets describe exit-ticket-us-east4 --region=us-east4 --format="yaml(name,ipCidrRange,privateIpGoogleAccess,secondaryIpRanges,stackType,purpose)"
echo "--- reserved range"; gcloud compute addresses list --global --filter="purpose=VPC_PEERING" --format="table(name,address,prefixLength,purpose,network.basename(),status)"
echo "--- PSA connection"; gcloud services vpc-peerings list --network=exit-ticket-vpc
echo "--- VPC peering"; gcloud compute networks peerings list --network=exit-ticket-vpc
echo "--- firewall rules on the new VPC"; gcloud compute firewall-rules list --filter="network:exit-ticket-vpc" --format="table(name,direction,sourceRanges.list())"
echo "--- routes"; gcloud compute routes list --filter="network:exit-ticket-vpc" --format="table(name,destRange,nextHopGateway.basename(),priority)"
```

| Section | Healthy (verified 2026-11-11) |
|---|---|
| networks | `default AUTO REGIONAL`; **`exit-ticket-vpc CUSTOM REGIONAL`** |
| subnet | `10.10.0.0/20`, `privateIpGoogleAccess: true`, `pods 10.20.0.0/17`, `services 10.30.0.0/22`, `IPV4_ONLY`, `purpose: PRIVATE` (an ordinary subnet) |
| reserved range | `exit-ticket-psa 10.40.0.0 20 VPC_PEERING exit-ticket-vpc RESERVED` (held, not yet used) |
| PSA connection | `peering: servicenetworking-googleapis-com`, `reservedPeeringRanges: [exit-ticket-psa]`, `service: services/servicenetworking.googleapis.com` (network shown by project **number**) |
| VPC peering | `servicenetworking-googleapis-com`, peer project `…-tp` (Google's **tenant project**), peer network `servicenetworking`, custom routes not imported/exported, **`ACTIVE` "Connected."** |
| firewall rules | **None** |
| routes | `default-route-…` `0.0.0.0/0` → `default-internet-gateway`, priority 1000; one **subnet route** per range (`10.10.0.0/20`, `10.20.0.0/17`, `10.30.0.0/22`), created automatically, shown with priority 0 |
| Terraform | `terraform plan` → `No changes.` |

The PSA connection (Service Networking's record of which ranges Google may use) and the VPC peering (Compute Engine's
network link) are **two views of one thing**.

---

## 6. Destroy order and the peering problem

Deleting a PSA connection often fails with *"Producer services (e.g. CloudSQL) are still using this connection"*:

- **Terraform can't order across states.** Cloud SQL will live in the `data` layer, the peering in `foundation`.
  Destroying foundation first would try to remove a peering the database still uses.
- **After Cloud SQL is deleted**, Google can take a while (reportedly days) to release its producer subnet, so the
  delete keeps failing.

Provider 8.x `deletion_policy` on `google_service_networking_connection` (from the provider schema):

| Value | On destroy |
|---|---|
| `DELETE` (default) | Deletes through the Service Networking API; fails while a producer still holds it |
| `ABANDON` | Drops it from state, leaves the peering, which **blocks deleting the network** |
| `REMOVE_PEERING` | Deletes it; if the API refuses, removes the VPC peering from the network directly. Only once Cloud SQL etc. are already deleted |
| **`PREVENT`** (chosen) | Any plan that would delete it fails |

**End-of-project teardown, in order:** `cluster` destroy → `data` destroy (Cloud SQL) → in `network.tf` set the
connection's and the network's `deletion_policy` to `REMOVE_PEERING` / `DELETE` and apply → `foundation` destroy (the
SAs, secrets and repository have their own `PREVENT`/`deletion_protection` to lift first) → `bootstrap` last
([5A §9](phase-5a-bootstrap.md#9-teardown)).

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Producer services … are still using this connection` | Cloud SQL (or a recently deleted one) still holds the range | Destroy `data` first; wait; `REMOVE_PEERING` ([§6](#6-destroy-order-and-the-peering-problem)) |
| `Cannot modify allocated ranges in CreateConnection` / connection already exists | A peering made outside Terraform, or a retry | Import it, or `update_on_creation_fail = true` |
| `IP_SPACE_EXHAUSTED` / overlap error on create | A range overlaps another subnet, secondary range or reserved range | Recheck the plan in [§2](#2-the-ip-plan) |
| Private node can't pull an image | PGA off on the subnet, or the default route deleted | `privateIpGoogleAccess: true`; a `0.0.0.0/0` route to `default-internet-gateway` |
| Private node can't reach Docker Hub / PyPI | No Cloud NAT (by design) | Use Google's registries, or add NAT in the cluster layer |
| `gcloud … list --filter` prints nothing | No matching items (some table formats print no "Listed 0 items.") | Label outputs with `echo "--- name"` |

---

## 8. Known gaps

| Gap | Why it's acceptable now | Later |
|---|---|---|
| The default network and its open SSH/RDP rules still exist | Nothing listens; nothing new uses it | Phase 8: delete it (or its `default-allow-*` rules) |
| Cloud SQL still on public IP only | The instance isn't imported yet | Phase 5D: import, add private IP alongside public (decision d) |
| No VPC flow logs or firewall rule logging | Cost | Turn on when debugging |
| Single region | Dev | Another subnet + region; the PSA /20 has room |
| No Cloud NAT | Not needed (PGA covers Google's registries) | Cluster layer, only if workloads need the internet |

---

## 9. Lessons learned

| Session | What happened | Lesson |
|---|---|---|
| 5C | The schema's `deletion_policy` for the PSA connection offers `REMOVE_PEERING`, which older guides don't mention | Read the provider's own schema for the version you pin (`terraform providers schema -json`) |
| 5C | The PSA connection took 51 s, longer than the network, subnet and range | Service Networking provisions the peering on Google's side too; expect a minute |
| 5C | `vpc-peerings list` and `networks peerings list` showed the same peering differently | Two APIs, one link: Service Networking (ranges) vs Compute Engine (peering state) |
| 5C | Routes showed one subnet route per secondary range, not only the primary | Every range in a subnet is routed inside the VPC automatically |

---

## 10. Interview talking points

**1. "Designed the IP plan, not just the network."** RFC 1918 ranges sized for Autopilot (a /26 per node, 512 nodes in
the Pod range), no overlap with the default network or each other, with room to grow.

**2. "Private by default."** Custom-mode VPC with no allow rules; private nodes reach Google APIs through Private
Google Access, so no NAT and no public IPs; Cloud SQL gets a private address through private services access.

**3. "Thought about teardown up front."** The PSA peering is the classic stuck-destroy in GCP Terraform; it's protected
with `PREVENT` and the cross-layer teardown order is documented.

---

## ACE exam notes

**VPC and subnets**
- VPC networks are **global**, subnets **regional**; a subnet's primary range can be **expanded** (`gcloud compute
  networks subnets expand-ip-range`), never shrunk.
- **Auto mode** (one subnet per region in `10.128.0.0/9`, can be converted to custom, not back) vs **custom mode**
  (recommended for production). The **default** network is auto mode with permissive rules.
- **Secondary ranges** + alias IPs = **VPC-native** GKE (required for Autopilot, NEGs, private clusters).
- Routing mode **regional vs global** (what Cloud Routers advertise). **Subnet routes** are automatic; the
  **default route** `0.0.0.0/0` → internet gateway can be deleted (then even PGA needs a custom route).
- **Shared VPC** (a host project shares subnets with service projects) vs **VPC Network Peering** (two VPCs, non-
  transitive, no overlapping ranges, each side manages its own firewall).

**Firewall rules**
- Stateful; **direction** (ingress/egress), **priority** (0–65535, lower wins), **action** (allow/deny), **targets**
  (all instances, network tags, service accounts), **sources** (CIDR ranges, tags, service accounts).
- **Implied rules**: deny all ingress, allow all egress (priority 65535). Default network rules
  (`default-allow-internal`, `-ssh`, `-rdp`, `-icmp`) are ordinary rules at 65534 and can be deleted.
- IAP TCP forwarding needs ingress from `35.235.240.0/20`; Google health checks come from `35.191.0.0/16`,
  `130.211.0.0/22`.
- Firewall **policies** (hierarchical, network, regional) are the newer layer; classic **rules** are per network.

**Private access options**

| | Private Google Access | Private services access | Private Service Connect |
|---|---|---|---|
| Reaches | Google **APIs** (Storage, Artifact Registry, BigQuery …) | Google **managed services** in a producer VPC (Cloud SQL, Memorystore, Filestore …) | Google APIs **or** published services (Google's or third parties') |
| How | A subnet setting; traffic uses Google's public API IPs over Google's network | A **reserved range** + **VPC peering** (`servicenetworking`) | An **endpoint**: an internal IP (forwarding rule) in your subnet |
| IPs used | None of yours | Your reserved range (the service's IP comes from it) | One internal IP per endpoint |
| Overlap / transitivity | — | Range must not overlap; peering **not transitive** | No peering; works across overlapping ranges and from on-prem |
| Typical exam answer | "VMs without external IPs must reach Cloud Storage" | "Cloud SQL private IP" | "Private, per-endpoint access to a service; avoid peering limits" |

- PGA can use the special VIPs `private.googleapis.com` (`199.36.153.8/30`) or `restricted.googleapis.com`
  (`199.36.153.4/30`, for VPC Service Controls) with custom DNS.
- **Cloud NAT**: outbound internet for private IPs (regional, on a Cloud Router); no inbound.
