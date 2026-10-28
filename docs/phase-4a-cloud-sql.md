# Phase 4A — Cloud SQL for PostgreSQL

Phase 4A creates the Cloud SQL instance from the [Phase 4 design](phase-4-design.md), logs in with **IAM database
authentication** through the **Cloud SQL Auth Proxy** on the Mac, runs the migrations and the seed **as the app's
own identity**, and stops the instance at the end. No cluster yet: the api moves onto Cloud SQL in a later part.

| Part | What was built | PR |
|---|---|---|
| **4A** | Google SA `exit-ticket-api` + Cloud SQL roles; instance `exit-ticket-db`; `postgres` password in Secret Manager; database `exitticket`; IAM database user; grants; migrations and seed through the Auth Proxy; stop; audit log review | (this PR) |

> **About the commands in this doc.** Code blocks contain commands only, no `#` comments (zsh treats `#` as an
> argument). Run them from the repo root unless a block starts with `cd`.

> **Placeholders**
>
> | Placeholder | Meaning |
> |---|---|
> | `PROJECT_ID` | Your GCP project ID (the commands use `$(gcloud config get project)`) |

**Contents**

1. [Concepts](#1-concepts)
2. [The app's Google service account](#2-the-apps-google-service-account)
3. [Creating the instance](#3-creating-the-instance)
4. [The postgres password](#4-the-postgres-password)
5. [Database and IAM user](#5-database-and-iam-user)
6. [The Cloud SQL Auth Proxy](#6-the-cloud-sql-auth-proxy)
7. [Grants, as postgres](#7-grants-as-postgres)
8. [Migrations and seed, as the app](#8-migrations-and-seed-as-the-app)
9. [Read-only checks](#9-read-only-checks)
10. [Stopping and starting](#10-stopping-and-starting)
11. [The audit trail](#11-the-audit-trail)
12. [End-of-session check](#12-end-of-session-check)
13. [Troubleshooting](#13-troubleshooting)
14. [Lessons learned](#14-lessons-learned)
15. [Interview talking points](#15-interview-talking-points)
16. [ACE exam notes](#ace-exam-notes)

---

## 1. Concepts

```
Project
 └─ Instance  exit-ticket-db        a managed VM + disk running Postgres: what bills, backs up, stops
     ├─ Database  exitticket        a namespace for tables (free)
     ├─ Database  postgres          created automatically
     └─ Users (belong to the instance, shared by all its databases)
         ├─ postgres                         built-in admin, password login
         └─ exit-ticket-api@PROJECT_ID.iam   IAM login, no password
```

- **Connection name** `PROJECT_ID:us-east4:exit-ticket-db` (`PROJECT:REGION:INSTANCE`): the instance's stable
  address for the Auth Proxy and connectors, independent of its IP.
- The instance's VM lives in a **Google-managed tenant project**, so it never appears in
  `gcloud compute instances list`.

| | `postgres` (built-in) | `exit-ticket-api@PROJECT_ID.iam` (the app) |
|---|---|---|
| Login | Password, kept in Secret Manager | Short-lived IAM token supplied by the proxy |
| Power | Member of `cloudsqlsuperuser` (create databases and roles, grant). **Not** a true superuser: Google keeps `cloudsqladmin` | Only what's granted |
| Used for | One-time grants; emergencies | Migrations, seed, the app |

---

## 2. The app's Google service account

The IAM database user *is* this service account, so it's created first (free):
```bash
gcloud iam service-accounts create exit-ticket-api --display-name="Exit Ticket API (pods)" --description="Pod identity for the api and migrate Job: Cloud SQL IAM login"
gcloud projects add-iam-policy-binding $(gcloud config get project) --member="serviceAccount:exit-ticket-api@$(gcloud config get project).iam.gserviceaccount.com" --role="roles/cloudsql.client" --condition=None
gcloud projects add-iam-policy-binding $(gcloud config get project) --member="serviceAccount:exit-ticket-api@$(gcloud config get project).iam.gserviceaccount.com" --role="roles/cloudsql.instanceUser" --condition=None
gcloud iam service-accounts add-iam-policy-binding exit-ticket-api@$(gcloud config get project).iam.gserviceaccount.com --member="user:$(gcloud config get account)" --role="roles/iam.serviceAccountTokenCreator"
```

| Grant | Why |
|---|---|
| `cloudsql.client` on the project | The proxy may connect as this account |
| `cloudsql.instanceUser` on the project | IAM database login (`cloudsql.instances.login`) |
| `serviceAccountTokenCreator` for you, **on this SA only** | The Mac's proxy can impersonate it ([§8](#8-migrations-and-seed-as-the-app)) |

Cloud SQL roles go on the **project**: Cloud SQL has no per-instance IAM policy.

The block ran twice; the second `create` failed with "already exists" and the bindings were no-ops. Checked
afterwards:
```bash
gcloud iam service-accounts describe exit-ticket-api@$(gcloud config get project).iam.gserviceaccount.com
gcloud iam service-accounts keys list --iam-account=exit-ticket-api@$(gcloud config get project).iam.gserviceaccount.com --managed-by=user
```
**0 user-managed keys.**

---

## 3. Creating the instance

### Check the flags first

```bash
gcloud sql instances create --help | grep -E -- "--(edition|tier|zone|region|availability-type|storage-type|storage-size|storage-auto-increase|backup-start-time|retained-backups-count|enable-point-in-time-recovery|database-flags|connector-enforcement|deletion-protection|maintenance-window-day|maintenance-window-hour|labels)"
```
It showed two things that mattered:
- `[--region=REGION … | … --zone=ZONE]`: **one or the other**. The zone implies the region.
- **No `--labels` line**: the GA command doesn't have it. The first attempt failed with
  `unrecognized arguments: --labels=…`, rejected by gcloud locally, so nothing was created.

### Create

**💰 Billing starts here** (~$0.011/hour compute, plus storage and IP).
```bash
gcloud sql instances create exit-ticket-db --database-version=POSTGRES_16 --edition=enterprise --tier=db-f1-micro --zone=us-east4-a --availability-type=zonal --storage-type=SSD --storage-size=10GB --no-storage-auto-increase --backup-start-time=07:00 --retained-backups-count=7 --no-enable-point-in-time-recovery --maintenance-window-day=SUN --maintenance-window-hour=8 --database-flags=cloudsql.iam_authentication=on --connector-enforcement=REQUIRED --deletion-protection
```

| Flag | Meaning |
|---|---|
| `--database-version=POSTGRES_16` | Matches compose |
| `--edition=enterprise` | **Required**: Postgres 16+ defaults to Enterprise Plus, which has no `db-f1-micro` |
| `--tier=db-f1-micro` | Shared vCPU, 0.6 GB; **no SLA** |
| `--zone=us-east4-a` | Region us-east4 follows from it |
| `--availability-type=zonal` | No HA standby |
| `--storage-type=SSD --storage-size=10GB` | Minimum size; the type can't change later |
| `--no-storage-auto-increase` | Storage only grows, never shrinks |
| `--backup-start-time=07:00 --retained-backups-count=7` | Daily backup window in **UTC**; 7 kept |
| `--no-enable-point-in-time-recovery` | No WAL archive; synthetic data |
| `--maintenance-window-day=SUN --maintenance-window-hour=8` | Updates Sundays 08:00 UTC |
| `--database-flags=cloudsql.iam_authentication=on` | Allows IAM logins |
| `--connector-enforcement=REQUIRED` | Only Auth Proxy / connector connections |
| `--deletion-protection` | `delete` refuses until it's turned off |

No `--root-password`: a flag value lands in shell history ([§4](#4-the-postgres-password)).

**It took 11 minutes** (the audit log's request and completion entries, [§11](#11-the-audit-trail)): Cloud SQL
provisions a VM and disk in its tenant project, initializes Postgres, sets up the IP and server certificates, and
applies the flags. If gcloud stops waiting, the creation continues: `gcloud sql operations list --instance=exit-ticket-db --limit=3`.

Result: `exit-ticket-db POSTGRES_16 us-east4-a db-f1-micro` with a public IP and `STATUS RUNNABLE`. The public IP is
closed: no authorized networks, and connector enforcement refuses direct connections.

### Labels (beta)

The GA `patch` has no labels flag either; the beta one does (no restart):
```bash
gcloud beta sql instances patch exit-ticket-db --update-labels=app=exit-ticket,env=dev
```

### Verify

```bash
gcloud sql instances describe exit-ticket-db --format="yaml(connectionName,state,settings.edition,settings.tier,settings.availabilityType,settings.dataDiskType,settings.dataDiskSizeGb,settings.storageAutoResize,settings.backupConfiguration,settings.databaseFlags,settings.connectorEnforcement,settings.deletionProtectionEnabled,settings.ipConfiguration.authorizedNetworks,settings.maintenanceWindow,settings.userLabels)"
```

| Field | Value |
|---|---|
| `edition`, `tier`, `availabilityType` | `ENTERPRISE`, `db-f1-micro`, `ZONAL` |
| `dataDiskType`, `dataDiskSizeGb`, `storageAutoResize` | `PD_SSD`, `'10'`, `false` |
| `backupConfiguration` | `enabled: true`, `startTime: 07:00`, `retainedBackups: 7`, `pointInTimeRecoveryEnabled: false` (`transactionLogRetentionDays: 7` is a default that only matters with PITR) |
| `databaseFlags` | `cloudsql.iam_authentication: on` |
| `connectorEnforcement`, `deletionProtectionEnabled` | `REQUIRED`, `true` |
| `maintenanceWindow` | `day: 7` (**Sunday**; the API counts Monday as 1), `hour: 8` |
| `authorizedNetworks` | **Absent** = none |
| `userLabels` | `app: exit-ticket`, `env: dev` |

---

## 4. The postgres password

Generated and stored in one pipe: the value never touches the disk, the screen, the shell history or a command
line (simpler than the temp-file + `trap` pattern in `scripts/create-k8s-secrets.sh`, because gcloud reads
`--data-file=-` from stdin):
```bash
openssl rand -base64 32 | tr -d '\n' | gcloud secrets create postgres-admin-password --replication-policy=user-managed --locations=us-east4 --data-file=-
gcloud secrets versions list postgres-admin-password
```
`tr -d '\n'` drops the trailing newline, which would otherwise be part of the password.

gcloud's password prompt reads the keyboard, not stdin, so the value goes through the clipboard and the clipboard
is cleared afterwards:
```bash
gcloud secrets versions access 1 --secret=postgres-admin-password | pbcopy
gcloud sql users set-password postgres --instance=exit-ticket-db --prompt-for-password
pbcopy < /dev/null
```
(Cmd+V at `New Password:`, Enter; nothing is echoed.)

---

## 5. Database and IAM user

```bash
gcloud sql databases create exitticket --instance=exit-ticket-db
gcloud sql users create exit-ticket-api@$(gcloud config get project).iam --instance=exit-ticket-db --type=cloud_iam_service_account
gcloud sql users list --instance=exit-ticket-db --format="table(name,type)"
```
`postgres` is `BUILT_IN`; `exit-ticket-api@PROJECT_ID.iam` is `CLOUD_IAM_SERVICE_ACCOUNT`. The user name is the SA
email **without `.gserviceaccount.com`** (Postgres role names max out at 63 characters).

---

## 6. The Cloud SQL Auth Proxy

```bash
brew install cloud-sql-proxy libpq
cloud-sql-proxy --version
/opt/homebrew/opt/libpq/bin/psql --version
```
Proxy 2.26.3; psql 18.6 (newer clients work with older servers). `libpq` is **keg-only** (not on `PATH`), hence the
full path. These are native arm64 tools on the Mac; the linux/amd64 rule is for images.

### How it authenticates

```
Mac                                                      Cloud SQL
 psql / alembic ──plain TCP──► cloud-sql-proxy ══TLS 1.3, port 3307══► Postgres
   127.0.0.1:15432             1. ADC token → Cloud SQL Admin API (cloudsql.instances.connect)
                               2. a 1-hour client certificate + the server CA come back
                               3. --auto-iam-authn: an OAuth token is sent as the password
```
- **Identity, not IP**: IAM decides who connects; removing a role revokes access everywhere.
- **Mutual TLS** with short-lived certificates; only the hop inside the Mac is plain.
- **Audited**: every certificate request is an Admin Activity entry ([§11](#11-the-audit-trail)).

Opening authorized networks to `0.0.0.0/0` instead exposes port 5432 to the whole internet: bots try passwords and
probe Postgres vulnerabilities, encryption depends on each client, and the password is the only defense. An
allowlist of one IP is better but breaks on every Wi-Fi change and still means "anyone at that address".

---

## 7. Grants, as postgres

Tab 1, the proxy as **you** (Application Default Credentials):
```bash
cloud-sql-proxy --port=15432 $(gcloud config get project):us-east4:exit-ticket-db
```
Tab 2:
```bash
cd ~/projects/exit-ticket-5th-grade-math-science
APP_USER="exit-ticket-api@$(gcloud config get project).iam"
PGPASSWORD="$(gcloud secrets versions access 1 --secret=postgres-admin-password)" /opt/homebrew/opt/libpq/bin/psql "host=127.0.0.1 port=15432 dbname=exitticket user=postgres" -v ON_ERROR_STOP=1 -c "GRANT CONNECT ON DATABASE exitticket TO \"$APP_USER\";" -c "GRANT USAGE, CREATE ON SCHEMA public TO \"$APP_USER\";" -c "\dn+ public"
```
Names containing `@` or `-` need double quotes in SQL; `\"` keeps the shell from eating them.

`\dn+ public` afterwards:

| Entry | Meaning |
|---|---|
| `pg_database_owner=UC/pg_database_owner` | The schema's owner (whoever owns the database): USAGE + CREATE |
| `=U/pg_database_owner` | **PUBLIC**: USAGE only. Since Postgres 15 nobody else may create tables in `public` |
| `cloudsqlsuperuser=UC/…` | Cloud SQL's admin role (`postgres` is a member) |
| `"exit-ticket-api@…iam"=UC/pg_database_owner` | **Our grant**: the app may create tables |

Tables it creates are **owned by it**, so it has full access to them and nothing else. Ctrl+C the admin proxy.

---

## 8. Migrations and seed, as the app

Tab 1, the proxy **impersonating** the app's SA and logging in with IAM:
```bash
cloud-sql-proxy --auto-iam-authn --impersonate-service-account=exit-ticket-api@$(gcloud config get project).iam.gserviceaccount.com --port=15432 $(gcloud config get project):us-east4:exit-ticket-db
```
It printed `Impersonating service account with Application Default Credentials`, then `ready for new connections`.

Tab 2:
```bash
cd ~/projects/exit-ticket-5th-grade-math-science/app/api
DB_URL="postgresql+psycopg://exit-ticket-api%40$(gcloud config get project).iam@127.0.0.1:15432/exitticket"
DATABASE_URL="$DB_URL" .venv/bin/alembic upgrade head
DATABASE_URL="$DB_URL" .venv/bin/alembic current
DATABASE_URL="$DB_URL" .venv/bin/python ../../scripts/seed.py
cd ~/projects/exit-ticket-5th-grade-math-science
```
- **No password in the URL**: the proxy supplies the token. This is the shape the ConfigMap will use.
- `%40` is a URL-encoded `@` (a raw one would end the user name).
- `DATABASE_URL` on the command line wins over `.env`; `SESSION_SECRET` and `TEACHER_PASSCODE` still come from
  `.env` (alembic loads the full settings).

Result: `Running upgrade  -> 0001, create core tables`, `0001 -> 0002, student login codes and exit ticket releases`,
`0002 (head)`, and `Seeded 9 standards, 45 questions, 6 exit tickets, 1 classroom, 24 students`. The proxy logged
one short connection for each command.

---

## 9. Read-only checks

```bash
PGOPTIONS="-c default_transaction_read_only=on" /opt/homebrew/opt/libpq/bin/psql -P pager=off "host=127.0.0.1 port=15432 dbname=exitticket user=exit-ticket-api@$(gcloud config get project).iam" -c "\conninfo" -c "SELECT current_user, version();" -c "SELECT tablename, tableowner FROM pg_tables WHERE schemaname = 'public' ORDER BY 1;" -c "SELECT (SELECT count(*) FROM standards) AS standards, (SELECT count(*) FROM questions) AS questions, (SELECT count(*) FROM exit_tickets) AS exit_tickets, (SELECT count(*) FROM students) AS students;" -c "CREATE TABLE should_fail (id int);"
```
`PGOPTIONS` makes the whole session read-only; `-P pager=off` stops psql from paging long output (a lone `:` is
`less` waiting for a key).

| Check | Result |
|---|---|
| `\conninfo` | Client user `exit-ticket-api@…iam`; **`Password Used: false`** (the proxy supplied the IAM token); `SSL Connection: false` (only the local hop; proxy → Cloud SQL is TLS); `Superuser: off` |
| `version()` | PostgreSQL **16.15** |
| Table owners | All 9 (`alembic_version` + 8 app tables) owned by `exit-ticket-api@…iam` |
| Counts | 9 standards, 45 questions, 6 exit tickets, 24 students |
| `CREATE TABLE` | `ERROR: cannot execute CREATE TABLE in a read-only transaction` (as intended) |

---

## 10. Stopping and starting

Ctrl+C the proxy, then:
```bash
gcloud sql instances patch exit-ticket-db --activation-policy=NEVER
gcloud sql instances list
```
`list` shows **`STOPPED`**. The API's `state` field stays **`RUNNABLE`** (it means "healthy", not "running"); the
stop switch is `settings.activationPolicy`, and `list` combines the two. Check with `list`.

To start at the beginning of the next session:
```bash
gcloud sql instances patch exit-ticket-db --activation-policy=ALWAYS
```

| Activation policy | Meaning |
|---|---|
| `ALWAYS` | Running; compute bills |
| `NEVER` | Stopped; no compute charge |
| `ON_DEMAND` | Legacy first generation only |

While stopped, **still billed**: the 10 GB SSD, the public IPv4 address, stored backups. Free: the secret, the
service account, IAM. Under the [teardown policy](phase-4-design.md#11-teardown-policy-enforced), the instance is
deleted if no session is planned within 7 days.

---

## 11. The audit trail

```bash
gcloud logging read 'logName:"cloudaudit.googleapis.com%2Factivity" AND (protoPayload.serviceName="cloudsql.googleapis.com" OR protoPayload.serviceName="secretmanager.googleapis.com" OR protoPayload.serviceName="iam.googleapis.com" OR protoPayload.serviceName="cloudresourcemanager.googleapis.com")' --freshness=1d --limit=40 --format="table(timestamp,protoPayload.serviceName,protoPayload.methodName,protoPayload.authenticationInfo.principalEmail)"
```
`logName:"…activity"` (`:` = contains) selects Admin Activity without the project ID. A first attempt with
`log_id(…)`, a grouped `OR` and `--order=asc` printed nothing. Newest entries come first: read bottom-up.

| UTC | Method | What |
|---|---|---|
| 01:31 | `CreateServiceAccount`, 2× `SetIamPolicy` | §2, first run |
| 01:34 | `CreateServiceAccount` (failed: exists), 2× `SetIamPolicy`, `SetIAMPolicy` on the SA | §2, second run + token creator |
| 01:37 → 01:48 | `cloudsql.instances.create` ×2 | **One** create: request, then completion 11 minutes later |
| 01:51 | `instances.update` ×2 | The label patch (request, completion) |
| 01:52 | `CreateSecret`, `AddSecretVersion` | §4 |
| 02:01 | `instances.connect` by **you** | The admin proxy's certificate (§7) |
| 02:02 | `instances.connect` by **`exit-ticket-api@…`** | The app proxy: **proof of impersonation** (§8) |
| 02:11 → 02:12, 02:14 | `instances.update` ×4 | Two stop requests (`NEVER`), each with a completion entry |

- **Long-running operations log twice**: when the request arrives and when it completes. Only the request entry has
  the request body (`protoPayload.request.body.settings.activationPolicy` was `NEVER` on the two requests, blank on
  the completions).
- Failed calls are logged too (the "already exists" create). The `--labels` attempt never reached the API.
- Not here: secret reads and Postgres logins (**Data Access** logs, off by default, billed when on), and SQL inside
  Postgres such as `GRANT` (Postgres logging or the `pgaudit` extension). `databases.create` / `users.create` didn't
  show up under those names.

---

## 12. End-of-session check

```bash
gcloud sql instances list
gcloud container clusters list
gcloud compute forwarding-rules list
pgrep -fl cloud-sql-proxy
```
`exit-ticket-db` `STOPPED`; no clusters; no forwarding rules; no proxy running on the Mac.

---

## 13. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `unrecognized arguments: --labels=…` | GA `sql instances create` / `patch` have no labels flag | `gcloud beta sql instances patch --update-labels` after creating |
| `argument --zone: not allowed with --region` (or similar) | Mutually exclusive | Pass `--zone` only |
| `Operation is taking longer than expected` | gcloud stopped waiting; creation continues | `gcloud sql operations list --instance=exit-ticket-db` |
| `Service account … already exists` | The block ran twice | Harmless; check `describe` and `keys list` |
| Proxy: `invalid_grant` / `reauth` | Application Default Credentials expired | `gcloud auth application-default login` |
| Proxy hangs on dial | Outbound port **3307** blocked by the network | Another network |
| Direct `psql` to the public IP refused | Connector enforcement + no authorized networks | Use the proxy (that's the point) |
| `permission denied for schema public` | Postgres 15+: no `CREATE` for PUBLIC | `GRANT USAGE, CREATE ON SCHEMA public` ([§7](#7-grants-as-postgres)) |
| A role name with `@` rejected in SQL | Needs double quotes | `"exit-ticket-api@…iam"` |
| psql shows `:` and waits | The pager (`less`) | `q`, or `-P pager=off` |
| `gcloud logging read` prints nothing | Zero matches (not an error) | Start with the simplest filter and widen it |
| `describe` says `RUNNABLE` after stopping | `state` ≠ running | `gcloud sql instances list` → `STOPPED` |

---

## 14. Lessons learned

| What happened | Cause | Lesson |
|---|---|---|
| Create failed on `--labels` though the `--help` check had no `labels` line | Missed reading the check's output | A pre-flight check only helps if every expected line is confirmed |
| Two `CreateServiceAccount` entries | The block was run twice | Admin Activity logs failed calls too; it answers "what really happened" |
| Two `instances.create` entries, 11 minutes apart | Long-running operations log request and completion | Don't count entries as operations; look at the request body |
| psql stopped at `:` | Pager on long output | `-P pager=off` for paste-friendly output |
| `log_id()` + grouped `OR` + `--order=asc` returned nothing | Filter or ordering didn't match as expected | The `logName:` form works; build filters up from simple |
| Unclear whether to run the "start next session" command | Reference command next to run commands | Label every block **RUN NOW** or **REFERENCE** |

---

## 15. Interview talking points

**1. "No database password for the app."** The api's database user is its Google service account. The Auth Proxy
trades the pod's identity for a short-lived OAuth token, so there's nothing to store, leak or rotate; access is
granted and revoked in IAM and audited.

**2. "Identity-based access instead of IP allowlists."** The instance has a public IP but no authorized networks,
and connector enforcement accepts only the proxy: mutual TLS with one-hour certificates, authorized per identity.

**3. "Least privilege inside the database too."** The app user can create tables in one schema and owns only what it
created; the `postgres` admin is used once for grants, with its password generated straight into Secret Manager.

**4. "I verify from the audit log."** I could show the proxy connecting *as the app's service account* from my
laptop (impersonation), and read long-running operations' request/completion pairs to reconstruct exactly what ran.

**5. "Cost discipline."** Smallest Enterprise tier, explicit edition (Postgres 16 defaults to Enterprise Plus),
auto-increase off, stopped at the end of every session, deleted after a week idle.

---

## ACE exam notes

**Instances, databases, users**
- Instance = billing, sizing, backups, HA, maintenance; databases and users live inside it.
- Connection name `PROJECT:REGION:INSTANCE`.
- The built-in `postgres` user has `cloudsqlsuperuser`, not true superuser.
- IAM database auth: flag `cloudsql.iam_authentication=on`, users of type `cloud_iam_user` /
  `cloud_iam_service_account` / `cloud_iam_group`, role `roles/cloudsql.instanceUser`.

**Editions and sizing**
- Enterprise vs Enterprise Plus; Postgres 16+ defaults to Enterprise Plus.
- Shared-core tiers have no SLA. Changing the tier restarts the instance. Storage only grows; type fixed.

**Availability and recovery**
- **HA** (`--availability-type=regional`): standby in another zone, synchronous replication on a regional persistent
  disk, automatic failover to the same IP; protects against a zone outage; doubles cost.
- **Read replicas**: asynchronous, read scaling, can be cross-region for DR; **not HA**; promoted manually.
- **Backups**: automated (window, retention count) and on-demand; restoring overwrites the target; **PITR** needs
  backups + transaction logs; exports to Cloud Storage are separate and portable.
- **Maintenance**: window (day, hour), deny periods, short restart.

**Connecting**
- Cloud SQL Auth Proxy / language connectors: IAM-authorized, mutual TLS, port 3307; no allowlist needed.
- Authorized networks for direct public-IP clients; private IP via private services access or Private Service
  Connect.
- Roles: `cloudsql.client` (connect), `cloudsql.instanceUser` (IAM login), `cloudsql.editor`, `cloudsql.admin`; on
  the project.

**Operations**
- Stop/start: `--activation-policy=NEVER|ALWAYS`; stopped still bills storage and IP.
- `gcloud sql operations list`; Admin Activity logs include `cloudsql.instances.connect`.
- gcloud release levels: GA, `beta`, `alpha` (`gcloud components install beta`); some flags are beta-only.

**Choosing a database**
- Cloud SQL: regional relational, standard engines.
- **Spanner**: global, horizontally scaling, strongly consistent, 99.999%.
- **AlloyDB**: PostgreSQL-compatible, high performance and analytics.
- **Bigtable**: wide-column NoSQL, high-throughput writes. **Firestore**: documents. **BigQuery**: analytics.
