# Phase 5D — Cloud SQL in the Terraform data layer

Phase 5D brings the hand-built Cloud SQL instance `exit-ticket-db` ([Phase 4A](phase-4a-cloud-sql.md)) under
Terraform in its own **`data` layer**: the instance, the `exitticket` database and the app's IAM database user were
**imported** after a first-ever on-demand backup, with **zero diffs** on the first plan. Then a separate apply gave
the instance a **private IP on `exit-ticket-vpc`** through the private services access built in
[5C](phase-5c-network.md), keeping the proxy-only public IP so the Mac still works. Start and stop now go through
Terraform.

| Part | What | PR |
|---|---|---|
| **5A** | State bucket, layer layout | #21 |
| **5B** | Foundation part 1: imports, Editor removed | #22 |
| **5C** | Foundation part 2: VPC, subnet, private services access | #23 |
| **5D** | `data` layer: backup, 3 imports, 3 delete locks, `activation_policy` variable, private IP alongside public, outputs | (this PR) |

> **About the commands in this doc.** Code blocks contain commands only, no `#` comments: in zsh, `#` is not a
> comment unless `setopt interactivecomments` is on. **Run them from `infra/terraform/data`** unless a block starts
> with `cd`. No project ID or public IP is committed: they come from `$(gcloud config get project)`, the gitignored
> `terraform.tfvars`, or foundation's state.

> **Placeholders**
>
> | Placeholder | Meaning |
> |---|---|
> | `PROJECT_ID` | Your GCP project ID (`gcloud config get project`) |
> | `OPERATION_ID` | A Cloud SQL operation name from `gcloud sql operations list` |

**Contents**

1. [Concepts](#1-concepts)
2. [What the layer manages](#2-what-the-layer-manages)
3. [Safety first: the backup](#3-safety-first-the-backup)
4. [How the import was done](#4-how-the-import-was-done)
5. [Every diff, and what was done about it](#5-every-diff-and-what-was-done-about-it)
6. [Delete protection](#6-delete-protection)
7. [Private IP](#7-private-ip)
8. [Verification checklist](#8-verification-checklist)
9. [Starting and stopping through Terraform](#9-starting-and-stopping-through-terraform)
10. [Troubleshooting](#10-troubleshooting)
11. [Known gaps](#11-known-gaps)
12. [Lessons learned](#12-lessons-learned)
13. [Interview talking points](#13-interview-talking-points)
14. [ACE exam notes](#ace-exam-notes)

---

## 1. Concepts

```
 data layer (state prefix "data")                       foundation layer (prefix "foundation")
┌─────────────────────────────────────────────┐  reads  ┌──────────────────────────────────────┐
│ google_sql_database_instance.main            │◄────────│ outputs: network_id, psa_range_name, │
│   exit-ticket-db  (imported)                 │ remote  │          api_sa_email, …             │
│   ├─ google_sql_database.exitticket (imp.)   │ state   └──────────────────────────────────────┘
│   └─ google_sql_user.api (imported)          │
│        exit-ticket-api@PROJECT_ID.iam        │
└─────────────────────────────────────────────┘
 Not in Terraform: the postgres user + password, the GRANTs inside Postgres

 exit-ticket-db after 5D
   public IP  136.x.x.x   no authorized networks, connector enforcement REQUIRED  ← the Mac's Auth Proxy
   private IP 10.40.0.3   on exit-ticket-vpc via PSA (producer subnet 10.40.0.0/24) ← GKE pods (next)
```

### Why this import was riskier than 5B

In 5B the worst a wrong field could do was rewrite a label. A Cloud SQL instance has fields Google can't change in
place (name, region, disk type, encryption key, replica settings). If the code disagrees on one, Terraform's only
fix is **destroy and recreate**: `-/+` and `# forces replacement` in the plan. That means a new, **empty**
instance, a new IP and server certificate, the database, IAM user and GRANTs gone, and very likely the backups with
it (backups belong to the instance unless `retain_backups_on_delete` is on, [§11](#11-known-gaps)). Rule for the
session: **any `-/+` or "must be replaced" on the instance = stop**, and check the saved plan's `replace_paths` in
JSON, not just the summary.

### Three delete locks

| Lock | Lives in | Stops |
|---|---|---|
| `deletion_protection = true` (instance only) | State; the provider's default is `true` | A Terraform destroy **or replacement** of the instance |
| `deletion_policy = "PREVENT"` (all three) | State | Any Terraform destroy, even if the block is deleted from the code (same as 5B/5C) |
| `settings.deletion_protection_enabled = true` | **The instance itself** (4A's `--deletion-protection`) | Everyone: gcloud, the Console, Terraform |

The database and user only have the middle one. It matters most on the database: deleting its block would
otherwise `DROP DATABASE exitticket`.

### `activation_policy`: a variable, not `ignore_changes`

| | `lifecycle { ignore_changes = [settings[0].activation_policy] }` | **Variable, default `NEVER` (chosen)** |
|---|---|---|
| Start/stop | Stays a gcloud command; Terraform never looks | `terraform apply -var="activation_policy=ALWAYS"` / plain `terraform apply` |
| Pros | Plans never mention it | One tool; every start and stop is a reviewed plan |
| Cons | Not what we want | While running, every plan needs the `-var` |

The default is the point: forgetting the flag produces the **cheap** outcome. A plain `plan` while the instance runs
shows `~ activation_policy = "ALWAYS" -> "NEVER"`, Terraform saying "applying this would stop it".

### What restarts the instance

| Restarts | Doesn't |
|---|---|
| Stop/start; tier or edition change; **first private IP**; version upgrades and maintenance; many (not all) database flags; HA on/off | Labels; backup window and retention; maintenance window; the delete protections; authorized networks; connector enforcement; growing the disk; anything state-only (`deletion_policy`) |

### Reading another layer: `terraform_remote_state`

```hcl
data "terraform_remote_state" "foundation" {
  backend = "gcs"
  config  = { bucket = "${var.project_id}-tfstate", prefix = "foundation" }
}
```
It reads foundation's **outputs** only, read-only: nothing here can change foundation's state. It needs read
access to the state bucket (you, today; Cloud Build's SA in Phase 7). Values are read at plan time, so they can be
used in `import` IDs.

### Not managed here, on purpose

| What | Why |
|---|---|
| The `postgres` user and its password | A password user needs the password in the code, and everything Terraform manages is written to state **in plain text**. It stays only in Secret Manager |
| The `GRANT`s inside Postgres | The Google provider talks to the **Cloud SQL Admin API** (instances, databases, users as Google sees them), not to Postgres. A `postgresql` provider could, but it would need a network path and admin credentials for a one-time grant. They survive because nothing was rebuilt |

---

## 2. What the layer manages

| File | What |
|---|---|
| [`versions.tf`](../infra/terraform/data/versions.tf), [`.terraform.lock.hcl`](../infra/terraform/data/.terraform.lock.hcl) | Copied from foundation: Terraform 1.17.x, `google` 8.11.0, same checksums |
| [`backend.tf`](../infra/terraform/data/backend.tf) | GCS, prefix `data` |
| [`providers.tf`](../infra/terraform/data/providers.tf) | Provider (no `default_labels`: imported), `terraform_remote_state.foundation`, `local.foundation` |
| [`variables.tf`](../infra/terraform/data/variables.tf) | `project_id`, `region`, `activation_policy` (`ALWAYS`/`NEVER`, default `NEVER`) |
| [`cloud_sql.tf`](../infra/terraform/data/cloud_sql.tf) | `google_sql_database_instance.main`, `google_sql_database.exitticket`, `google_sql_user.api` |
| [`outputs.tf`](../infra/terraform/data/outputs.tf) | `connection_name`, `private_ip`, `database_name`, `db_iam_user`, for the cluster layer |

| Resource | Import ID shape |
|---|---|
| `google_sql_database_instance` | `projects/PROJECT_ID/instances/exit-ticket-db` |
| `google_sql_database` | `projects/PROJECT_ID/instances/exit-ticket-db/databases/exitticket` |
| `google_sql_user` (Postgres) | `PROJECT_ID/exit-ticket-db/USER_NAME` (MySQL users add a host; Postgres has none) |

**The IAM user:** name `exit-ticket-api@PROJECT_ID.iam`, the SA email minus `.gserviceaccount.com` (Postgres role
names max out at 63 characters), computed with `trimsuffix()` from foundation's `api_sa_email`. Type
`CLOUD_IAM_SERVICE_ACCOUNT`: logs in with an IAM token for a service account, never a password (the others are
`CLOUD_IAM_USER`, `CLOUD_IAM_GROUP`, `BUILT_IN`).

---

## 3. Safety first: the backup

No backup had ever been taken. Automated backups were configured in 4A (07:00 UTC, keep 7), but a daily backup only
runs if the instance is **running** in its window, and it had been stopped at 07:00 every day.

```bash
gcloud sql instances describe exit-ticket-db --format="value(state,settings.activationPolicy)"
gcloud sql backups create --instance=exit-ticket-db --description="before-terraform-import-phase-5d"
gcloud sql backups list --instance=exit-ticket-db --format="table(id,windowStartTime,type,status,description)"
```

The first attempt failed: state `MAINTENANCE`, `HTTPError 409: … another operation was already in progress`. The
"maintenance" was the **start itself** (`UPDATE`, 01:15 → 01:26 UTC, 11 minutes; versions unchanged at
`POSTGRES_16_15` / `R20261013`). Cloud SQL runs one operation at a time per instance, and reports a starting
instance as `MAINTENANCE`. Waiting on the operation, then retrying:

```bash
gcloud sql operations list --instance=exit-ticket-db --limit=5 --format="table(name,operationType,status,startTime,endTime)"
gcloud sql operations wait OPERATION_ID --timeout=unlimited
```

Result: one `ON_DEMAND` backup, **`SUCCESSFUL`**, at 2026-11-18 01:26 UTC. On-demand backups aren't rotated by the
retention count; it stays (a few cents a month) until deleted.

---

## 4. How the import was done

Same method as [5B §3](phase-5b-foundation.md#3-how-the-import-was-done):

1. **Read reality**, read-only:
   ```bash
   gcloud sql instances describe exit-ticket-db --format="yaml(name,databaseVersion,region,gceZone,instanceType,backendType,connectionName,ipAddresses,settings)"
   gcloud sql databases list --instance=exit-ticket-db --format="table(name,charset,collation)"
   gcloud sql users list --instance=exit-ticket-db --format="table(name,type)"
   ```
   and the provider's own schema (`terraform providers schema -json`) for 8.11.0's field names.
2. **Write the code field by field** from that output, plus three `import` blocks. No `backend.tf` yet: local, empty
   state, so `plan` couldn't import anything by accident.
3. `terraform init`, `fmt`, `validate`, `plan -var="activation_policy=ALWAYS"` → `Plan: 3 to import, 0 to add, 0 to
   change, 0 to destroy.` on the **first** try.
4. **Connect the backend** (no local state, so nothing to migrate; prefix `data` was empty):
   ```bash
   terraform init -backend-config="bucket=$(gcloud config get project)-tfstate"
   terraform plan -var="activation_policy=ALWAYS" -out=tfplan
   terraform show -json tfplan
   ```
   All three `no-op` + `importing`, no `replace_paths`.
5. **Apply and prove it:**
   ```bash
   terraform apply tfplan
   rm tfplan
   terraform plan -var="activation_policy=ALWAYS"
   terraform state list
   ```
   `3 imported, 0 added, 0 changed, 0 destroyed`, then **`No changes.`** 4 state entries (3 + the remote state data
   source). Nothing in Google Cloud changed and the instance wasn't restarted.
6. Deleted `imports.tf`.

---

## 5. Every diff, and what was done about it

**There were none.** Writing the code straight from `describe` paid off. The fields worth knowing:

| API (`describe`) | Terraform | Note |
|---|---|---|
| `enableDataplexIntegration: true` | `enable_dataplex_integration = true` | **Turned on by Google, not us.** Dataplex catalogs metadata (names, schemas), not data; free. Kept as found, else the plan would set it to `false` |
| `edition: ENTERPRISE` | `edition = "ENTERPRISE"` | Postgres 16 defaults to Enterprise Plus (no `db-f1-micro`) |
| `activationPolicy: ALWAYS` | `activation_policy = var.activation_policy` | Why every plan today had the `-var` |
| `dataDiskType`, `dataDiskSizeGb: '10'`, `storageAutoResize: false` | `disk_type`, `disk_size = 10`, `disk_autoresize = false` | Disk type is one of the replacement fields |
| `maintenanceWindow: day 7, hour 8` | `maintenance_window { day = 7, hour = 8 }` | Sunday (Monday = 1), UTC |
| `backupRetentionSettings`, `transactionLogRetentionDays: 7` | `backup_configuration { … }` | 7 days is a default that only matters with PITR |
| `ipConfiguration.sslMode`, `serverCaMode` | `ssl_mode`, `server_ca_mode` | Kept as found ([§11](#11-known-gaps)) |
| `authorizedNetworks` absent | no `authorized_networks` block | No block = none |
| — | no `root_password` | Never in state |

The import plan showed `deletion_protection = true` (provider default) and `deletion_policy = "DELETE"` on all
three: the next section.

---

## 6. Delete protection

A second apply, state only, like [5B §5](phase-5b-foundation.md#5-delete-protection): `deletion_policy = "PREVENT"`
on all three.

```
~ deletion_policy = "DELETE" -> "PREVENT"      (instance, database, user)
Plan: 0 to add, 3 to change, 0 to destroy.
```
Each update `Modifications complete after 0s` (no API call, no restart), then `No changes`. Kept out of the import
plan so that one showed **only** imports.

---

## 7. Private IP

Two lines in `ip_configuration`, both from foundation's state:

```hcl
private_network    = local.foundation.network_id       # projects/PROJECT_ID/global/networks/exit-ticket-vpc
allocated_ip_range = local.foundation.psa_range_name   # exit-ticket-psa (10.40.0.0/20)
```
`ipv4_enabled = true`, no authorized networks and `connector_enforcement = "REQUIRED"` unchanged. With one range on
the peering Google would pick it anyway; `allocated_ip_range` makes it explicit and keeps working if a second range
(Memorystore) is added.

**The plan:**
```
~ resource "google_sql_database_instance" "main" {
    ~ settings {
        ~ ip_configuration {
            + allocated_ip_range = "exit-ticket-psa"
            + private_network    = "projects/PROJECT_ID/global/networks/exit-ticket-vpc"
Plan: 0 to add, 1 to change, 0 to destroy.
```
`~ update in-place`, two `+` lines inside one block; the JSON plan had empty `replace_paths`.

**Before applying, know that:**
- **It restarts the instance** (a new interface in Google's producer network). Nothing was connected.
- **It's irreversible.** Provider schema: *"This setting can be updated, but it cannot be removed after it is set."*
  Moving to another VPC isn't a simple switch either; that's why the network came first in 5C.
- **No extra cost.** Private IPs and the peering are free; the public IPv4 is still billed.
- **The public IP and connection name don't change**, so the Mac's proxy keeps working.

**Apply:** `Modifications complete after 15m39s`. Longer than a plain start (~11 min): the first instance on a
peering also makes Google create a **producer subnet** from the range (here `10.40.0.0/24`). Don't Ctrl+C a long
apply: the operation continues at Google and state can fall behind.

---

## 8. Verification checklist

```bash
echo "--- ip addresses"; gcloud sql instances describe exit-ticket-db --flatten="ipAddresses" --format="table(ipAddresses.type,ipAddresses.ipAddress)"
echo "--- ip configuration"; gcloud sql instances describe exit-ticket-db --format="yaml(state,settings.ipConfiguration.ipv4Enabled,settings.ipConfiguration.privateNetwork,settings.ipConfiguration.allocatedIpRange,settings.ipConfiguration.authorizedNetworks,settings.connectorEnforcement)"
echo "--- psa range"; gcloud compute addresses describe exit-ticket-psa --global --format="yaml(address,prefixLength,purpose,status,users)"
echo "--- producer routes"; gcloud compute networks peerings list-routes servicenetworking-googleapis-com --network=exit-ticket-vpc --region=us-east4 --direction=INCOMING
echo "--- terraform"; terraform plan -var="activation_policy=ALWAYS"
terraform output
```

| Section | Healthy (verified 2026-11-17) |
|---|---|
| ip addresses | `PRIMARY` (public, unchanged), `OUTGOING`, **`PRIVATE 10.40.0.3`** |
| ip configuration | `RUNNABLE`, `ipv4Enabled: true`, `privateNetwork: …/exit-ticket-vpc`, `allocatedIpRange: exit-ticket-psa`, **no** `authorizedNetworks`, `connectorEnforcement: REQUIRED` |
| psa range | `10.40.0.0`, `20`, `VPC_PEERING`, **`status: RESERVED`**, no `users` (see below) |
| producer routes | `10.40.0.0/24 SUBNET_PEERING_ROUTE us-east4 0 accepted` |
| terraform | `No changes.`; outputs `connection_name`, `private_ip = "10.40.0.3"`, `database_name`, `db_iam_user` |

**`RESERVED`, not `IN_USE`:** the expectation was wrong. `status` is Compute Engine's: whether a Compute resource
(VM, forwarding rule) holds the address. A PSA range is handed to Service Networking and carved up inside Google's
project, so Compute has no user to record. The proof of use is the instance's `10.40.x` address and the imported
`/24` route.

**The Mac path** ([Phase 4 §3.7](phase-4-cloud-sql.md#37-seed-as-the-app--4a-8), [4A §9](phase-4a-cloud-sql.md#9-read-only-checks)),
tab 1:
```bash
cloud-sql-proxy --auto-iam-authn --impersonate-service-account=exit-ticket-api@$(gcloud config get project).iam.gserviceaccount.com --port=15432 $(gcloud config get project):us-east4:exit-ticket-db
```
tab 2:
```bash
PGOPTIONS="-c default_transaction_read_only=on" /opt/homebrew/opt/libpq/bin/psql -P pager=off "host=127.0.0.1 port=15432 dbname=exitticket user=exit-ticket-api@$(gcloud config get project).iam" -c "\conninfo" -c "SELECT current_user, version();" -c "SELECT (SELECT count(*) FROM standards) AS standards, (SELECT count(*) FROM questions) AS questions, (SELECT count(*) FROM exit_tickets) AS exit_tickets, (SELECT count(*) FROM students) AS students;" -c "CREATE TABLE should_fail (id int);"
```

| Check | Result |
|---|---|
| Proxy | `Impersonating service account …`, ready, one connection accepted and closed |
| `\conninfo` | `exit-ticket-api@…iam`, **`Password Used: false`**, `Superuser: off` |
| `version()` | PostgreSQL 16.15 |
| Counts | **9 standards, 45 questions, 6 exit tickets, 24 students**: same as 4A, through import and restart |
| `CREATE TABLE` | `cannot execute CREATE TABLE in a read-only transaction` (intended) |

The proxy dials the **public** IP unless given `--private-ip`; the private IP is unreachable from the Mac anyway.

---

## 9. Starting and stopping through Terraform

```bash
cd ~/projects/exit-ticket-5th-grade-math-science/infra/terraform/data
terraform apply -var="activation_policy=ALWAYS"
terraform apply
gcloud sql instances describe exit-ticket-db --format="value(state,settings.activationPolicy)"
```
(Start; stop; check. A start takes ~11 minutes.) Stopped = `STOPPED NEVER`. These replace the `gcloud sql instances
patch --activation-policy` lines in the [Phase 4 runbook](phase-4-cloud-sql.md#4-starting-a-session): a gcloud stop
still works in an emergency, but the next plan would show it as drift. 5D left the instance **running** for 5E, so
the Terraform stop is first exercised at the end of 5E.

---

## 10. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `HTTPError 409: … another operation was already in progress` | One operation at a time per instance (often a start still running) | `gcloud sql operations list`, `operations wait OPERATION_ID --timeout=unlimited`, retry |
| State `MAINTENANCE` right after a start | A starting instance is reported that way | Wait for the start's `UPDATE` operation |
| A plain `terraform plan` wants `"ALWAYS" -> "NEVER"` | The variable's default stops it | Pass `-var="activation_policy=ALWAYS"` while it runs, or apply it to stop |
| `-/+` / `# forces replacement` on the instance | The code differs on a replacement field | **Don't apply.** Fix the code to match `describe` |
| `~ enable_dataplex_integration = true -> false` | Google enabled it; code omits it | Set it `true` (or turn it off deliberately) |
| Plan wants to remove `private_network` | Lines deleted from code | Not possible: private IP can't be removed. Put them back |
| `Instance is deletion protected` / `deletion_policy is PREVENT` | The locks ([§6](#6-delete-protection)) | Intended; lift them deliberately, in code, first |
| `Error: Unsupported attribute` on `local.foundation.…` | Foundation output missing or not applied | `terraform output` in `foundation/` |
| gcloud `table(ipAddresses[].type, …)` with `--flatten` prints `None` | `--flatten="ipAddresses"` makes each row one address, so `[]` no longer applies | `--flatten="ipAddresses" --format="table(ipAddresses.type,ipAddresses.ipAddress)"` |
| Proxy fails after the change | ADC expired, or `--private-ip` used from the Mac | `gcloud auth application-default login`; no `--private-ip` outside the VPC |

---

## 11. Known gaps

| Gap | Why it's acceptable now | Later |
|---|---|---|
| Public IP still exists (and is billed) | Mac admin path; no authorized networks + connector enforcement | Private only once seeding runs as a Job or through a bastion (decision d, options 2–3) |
| `retain_backups_on_delete = false` | Delete is triple-locked | Turn on (in place, no restart): backups would survive an instance deletion |
| `ssl_mode = ALLOW_UNENCRYPTED_AND_ENCRYPTED` | Connector enforcement means every connection is the proxy's mutual TLS anyway | `ENCRYPTED_ONLY` as defence in depth, after checking it doesn't restart |
| Dataplex integration on (Google's default) | Metadata only, free | Decide deliberately |
| `postgres` user, password and GRANTs outside Terraform | [§1](#not-managed-here-on-purpose) | Documented in Phase 4A |
| Stop through Terraform not yet exercised | Instance kept running for 5E | End of 5E |
| The on-demand backup stays until deleted | Cents a month; a restore point | `gcloud sql backups delete BACKUP_ID --instance=exit-ticket-db` when no longer wanted |

---

## 12. Lessons learned

| Session | What happened | Lesson |
|---|---|---|
| 5D | Backup failed with 409 while state said `MAINTENANCE` | It was the start (11 min). One operation at a time: `operations wait`, then retry |
| 5D | First import plan clean on the first try | Write the code from `describe` and the provider schema, field by field, before planning |
| 5D | `enable_dataplex_integration` was `true` though we never set it | Google changes defaults; `describe` shows what's real |
| 5D | The schema had a third lock (`deletion_policy`) and `retain_backups_on_delete` | Read the pinned provider's schema, not memory |
| 5D | Private IP apply took 15m39s | First instance on a peering also creates the producer subnet; never Ctrl+C a long apply |
| 5D | The PSA range stayed `RESERVED` | Compute's status doesn't track Service Networking use; check the IP and the imported route |
| 5D | A gcloud format with `--flatten` and `[]` printed `None` | After flattening, address the field without `[]` |

---

## 13. Interview talking points

**1. "I put a live database under Terraform without touching it."** Backup first, code written from the API's view,
the saved plan checked as JSON for replacements: 3 imports, zero diffs, no restart.

**2. "Defence in depth against deletion."** A Terraform lock, a state lock that survives deleting the block, and the
API's own deletion protection.

**3. "Stop/start as code with a safe default."** `activation_policy` defaults to stopped, so forgetting a flag costs
nothing; every start and stop is a reviewed plan.

**4. "Private IP without breaking anything."** A separate, reviewed in-place change I knew would restart the
instance and be irreversible; the public path stayed proxy-only and was re-verified as the app's identity.

**5. "Layers that share outputs, not state."** The data layer reads the network and the SA from foundation through
`terraform_remote_state`, read-only.

---

## ACE exam notes

**Cloud SQL networking**
- **Public IP**: reachable from the internet only through **authorized networks** (CIDR allowlist) or the **Auth
  Proxy / connectors** (IAM + mutual TLS, no allowlist). Connector enforcement rejects direct connections.
- **Private IP**: through **private services access** (reserved range + `servicenetworking` peering) or **Private
  Service Connect**. Can't be removed once set; enabling it restarts the instance. Reachable only from the VPC (and
  networks connected by VPN/Interconnect with custom route export), not through another peering (**non-transitive**).
- An instance can have **both**; the proxy picks public unless `--private-ip`.
- Only one Cloud SQL operation runs at a time per instance (`gcloud sql operations list|wait`).

**Backups and recovery**
- **Automated** (daily window, retention count) only run while the instance is running; **on-demand** stay until
  deleted. Both are deleted with the instance unless backups are retained on delete / a final backup is configured.
- **Restore** overwrites a target instance; **PITR** needs backups + transaction logs; **export** to Cloud Storage
  (SQL dump/CSV) is portable and survives the instance.
- **Deletion protection** on the instance blocks deletes from every client.

**Operations**
- Stop/start: `activation_policy` `ALWAYS`/`NEVER`; stopped still bills storage, the public IP and backups.
- Restarts: tier/edition changes, many flags, private IP, maintenance. Not: labels, backup/maintenance settings,
  authorized networks, disk growth.
- **Terraform**: `import` blocks adopt existing resources; `-/+` = replacement; `terraform_remote_state` shares
  outputs between configurations; `lifecycle { ignore_changes }` hands a field to something else; state can hold
  secrets, so passwords stay out.
