# Phase 1 — GCP Foundations

Phase 1 sets up the base that every later phase builds on. That means a dedicated gcloud configuration, the APIs the
platform needs, a working understanding of IAM, and a habit of checking the audit logs.

> **Placeholders used below.** Replace these with your own values when you run a command. Never commit the real values.
>
> | Placeholder | Meaning |
> |---|---|
> | `PROJECT_ID` | Your GCP project ID |
> | `PROJECT_NUMBER` | Your numeric project number (`gcloud projects describe PROJECT_ID --format="value(projectNumber)"`) |
> | `YOUR_EMAIL` | The Google account you log in with |
> | `SA_NAME` | Short name of a service account, e.g. `practice-sa` |

---

## 1. Named gcloud configuration: `exit-ticket`

A **gcloud configuration** is a named set of defaults: account, project, region and zone. Keeping a separate one per
project means a command is far less likely to run against the wrong project by accident.

| Setting | Value |
|---|---|
| Configuration name | `exit-ticket` |
| Default region | `us-east4` |
| Default zone | `us-east4-a` |

```bash
# Create the configuration and switch to it (activation happens automatically on create)
gcloud config configurations create exit-ticket

# Set the defaults stored in this configuration
gcloud config set account YOUR_EMAIL
gcloud config set project PROJECT_ID
gcloud config set compute/region us-east4
gcloud config set compute/zone us-east4-a

# Log in. The second command creates Application Default Credentials (ADC) for local code and Terraform.
gcloud auth login
gcloud auth application-default login

# Check which configuration is active and what it contains
gcloud config configurations list
gcloud config list

# Switch between configurations later
gcloud config configurations activate exit-ticket
```

**Quirks worth knowing**
- `gcloud auth login` authorizes the **gcloud CLI**. `gcloud auth application-default login` authorizes **your code and
  tools** such as Terraform and client libraries. These are two separate credentials.
- Configurations are stored on your laptop in `~/.config/gcloud/configurations/`, not in the cloud.
- A `--project`, `--region` or `--zone` flag on a single command overrides the configuration for that command only.

---

## 2. APIs enabled (13)

A new project has most services switched **off**. A service's API must be enabled before you can use it, and enabling
an API is free. You pay only for the resources you create.

```bash
gcloud services enable \
  compute.googleapis.com \
  container.googleapis.com \
  sqladmin.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  cloudbuild.googleapis.com \
  pubsub.googleapis.com \
  bigquery.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  cloudresourcemanager.googleapis.com \
  logging.googleapis.com \
  monitoring.googleapis.com

# Verify
gcloud services list --enabled
```

| API | Why we need it |
|---|---|
| `compute.googleapis.com` | VPC networks, subnets, firewalls and the VMs that run under GKE nodes. |
| `container.googleapis.com` | GKE, which runs the GKE Autopilot cluster for the api and web apps. |
| `sqladmin.googleapis.com` | Cloud SQL Admin, which creates and manages the Postgres instance. |
| `artifactregistry.googleapis.com` | Stores the Docker images that GKE pulls. |
| `secretmanager.googleapis.com` | Stores the database password and other secrets, so they never sit in code. |
| `cloudbuild.googleapis.com` | Runs CI/CD: builds images and deploys on each push. |
| `pubsub.googleapis.com` | Carries answer events from the API to analytics. |
| `bigquery.googleapis.com` | The analytics warehouse for answer events, which Looker Studio reads. |
| `iam.googleapis.com` | Creates and manages service accounts and custom roles. |
| `iamcredentials.googleapis.com` | Issues short-lived tokens, which Workload Identity and impersonation depend on (and which replace keys). |
| `cloudresourcemanager.googleapis.com` | Reads and changes project IAM policies; Terraform needs it. |
| `logging.googleapis.com` | Cloud Logging: app logs and audit logs. |
| `monitoring.googleapis.com` | Cloud Monitoring: metrics, dashboards and alerts. |

---

## 3. IAM concepts

IAM answers one question: **who** can do **what** on **which resource**.

### Resource hierarchy
```
Organization  (company domain, e.g. example.com; a personal Gmail account has none)
└── Folders   (optional grouping, e.g. by team or environment)
    └── Projects   (the unit for billing, APIs and quotas; ours is PROJECT_ID)
        └── Resources  (buckets, clusters, SQL instances, topics…)
```
- **Policies are inherited downward.** A role granted on a folder applies to every project and resource under it.
- **A lower level can't take away access granted at a higher level.** Effective access is the *union* of all policies
  from the resource up to the org. (IAM *deny policies* are the exception, but they're a separate feature.)

### Role types
| Type | Example | What it is | When to use |
|---|---|---|---|
| **Basic** (formerly "primitive") | `roles/owner`, `roles/editor`, `roles/viewer` | Very broad roles that cover almost every service. | Avoid in production. Fine for a personal sandbox owner. |
| **Predefined** | `roles/storage.objectViewer`, `roles/cloudsql.client` | Curated by Google for one service and job, and updated as services change. | The default choice. |
| **Custom** | `projects/PROJECT_ID/roles/quizReader` | A list of permissions you pick yourself. | Only when no predefined role fits. You have to maintain it. |

- A **permission** looks like `service.resource.verb`, e.g. `storage.objects.get`. You grant **roles**, never single permissions.
- Custom roles can be created at the **project or organization level only**, not on folders.

### Bindings
A **policy** is attached to a resource and holds a list of **bindings**. Each binding links **one role** to **one or
more members (principals)**:
```yaml
bindings:
- role: roles/logging.viewer
  members:
  - user:YOUR_EMAIL
  - serviceAccount:SA_NAME@PROJECT_ID.iam.gserviceaccount.com
```
Member prefixes: `user:`, `serviceAccount:`, `group:`, `domain:`, plus `allUsers` and `allAuthenticatedUsers`, which
are public and should be avoided.

```bash
# View the project's policy
gcloud projects get-iam-policy PROJECT_ID

# Add or remove ONE binding. Use these instead of set-iam-policy, which replaces the whole policy.
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="user:YOUR_EMAIL" --role="roles/logging.viewer"
gcloud projects remove-iam-policy-binding PROJECT_ID \
  --member="user:YOUR_EMAIL" --role="roles/logging.viewer"
```

### Least privilege
Grant the **smallest role**, on the **lowest resource**, for the **shortest time** that still gets the job done. For
example, the API's service account gets `roles/cloudsql.client`, not `roles/editor`. Groups are easier to audit than
individual users.

### Service agents
**Service agents** are Google-managed service accounts that let a Google service act inside your project, e.g.
GKE creating load balancers or Cloud Build pushing images. They are created automatically when you enable an API,
and they look like:
```
service-PROJECT_NUMBER@container-engine-robot.iam.gserviceaccount.com
service-PROJECT_NUMBER@gcp-sa-artifactregistry.iam.gserviceaccount.com
```
- They're hidden from the IAM page unless you tick **"Include Google-provided role grants."**
- **Don't remove their roles.** If you do, the service breaks in confusing ways.
- They are different from **user-managed** service accounts like the one we create below, which you own and control.

---

## 4. Practice service account lifecycle

A **service account (SA)** is an identity for software, not for a person. We created a throwaway SA to practice each
stage of its life.

```bash
# 1. Create
gcloud iam service-accounts create SA_NAME \
  --display-name="Practice SA (Phase 1)"

# 2. List and describe
gcloud iam service-accounts list
gcloud iam service-accounts describe SA_NAME@PROJECT_ID.iam.gserviceaccount.com

# 3. Grant a narrow role (least privilege)
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:SA_NAME@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/logging.viewer"

# 4. Confirm the binding
gcloud projects get-iam-policy PROJECT_ID \
  --flatten="bindings[].members" \
  --filter="bindings.members:SA_NAME@PROJECT_ID.iam.gserviceaccount.com" \
  --format="table(bindings.role)"

# 5. Disable (reversible: the SA can't authenticate, but it still exists)
gcloud iam service-accounts disable SA_NAME@PROJECT_ID.iam.gserviceaccount.com
gcloud iam service-accounts enable  SA_NAME@PROJECT_ID.iam.gserviceaccount.com

# 6. Clean up: remove the binding, then delete the SA
gcloud projects remove-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:SA_NAME@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/logging.viewer"
gcloud iam service-accounts delete SA_NAME@PROJECT_ID.iam.gserviceaccount.com
```

**Why remove the binding before deleting?** Deleting an SA does **not** remove its bindings automatically. The leftover
binding shows up as `deleted:serviceAccount:...?uid=...`. It grants nothing, but it clutters the policy. A new SA
created later with the same name gets a **new unique ID**, so it does *not* inherit the old access.

**Undelete:** a deleted SA can be restored for about 30 days with
`gcloud iam service-accounts undelete ACCOUNT_UNIQUE_ID`.

---

## 5. Why we use no service account keys

A **service account key** is a downloadable JSON file holding a long-lived private key. Anyone who has the file can
act as the service account until the key is deleted.

| Risk of keys | What we use instead |
|---|---|
| A key can leak through git, Slack, laptop backups or a screenshot. | **No key files exist**, so there's nothing to leak. |
| Keys don't expire on their own (the default is no expiry). | **Short-lived tokens** (about 1 hour) from `iamcredentials`. |
| Manual rotation is easy to forget. | Tokens are issued and rotated automatically. |
| It's hard to tell who used a key. | Audit logs show the real caller, including impersonation chains. |

**How code authenticates without keys:**
- **On your laptop:** Application Default Credentials (ADC) via `gcloud auth application-default login`.
  Client libraries and Terraform find these automatically.
- **On GKE:** **Workload Identity Federation for GKE**. A Kubernetes service account is mapped to an IAM identity, and
  the pod gets tokens from the metadata server with no file mounted.
- **Acting as an SA temporarily:** **impersonation**, e.g.
  ```bash
  gcloud storage ls --impersonate-service-account=SA_NAME@PROJECT_ID.iam.gserviceaccount.com
  ```
  This requires `roles/iam.serviceAccountTokenCreator` on that SA.

Organizations can enforce this with the org policy constraint `iam.disableServiceAccountKeyCreation`.

---

## 6. Querying Admin Activity audit logs

**Cloud Audit Logs** record "who did what, where and when." There are four types:

| Log type | Records | On by default? | Cost |
|---|---|---|---|
| **Admin Activity** | Config and metadata changes (create SA, set IAM policy, enable API) | **Always on; can't be disabled** | Free |
| **Data Access** | Reads of config/data and user data writes | Off (except BigQuery) | Billable |
| **System Event** | Changes Google makes to your resources, e.g. live migration | Always on | Free |
| **Policy Denied** | Access denied by a security policy (e.g. VPC Service Controls) | On | Billable (can be excluded) |

Admin Activity logs are kept for **400 days** in the `_Required` bucket.

```bash
# The 10 most recent Admin Activity entries from the last day
gcloud logging read \
  'logName="projects/PROJECT_ID/logs/cloudaudit.googleapis.com%2Factivity"' \
  --freshness=1d --limit=10

# Who created or deleted service accounts?
gcloud logging read \
  'logName="projects/PROJECT_ID/logs/cloudaudit.googleapis.com%2Factivity"
   AND protoPayload.methodName:("CreateServiceAccount" OR "DeleteServiceAccount")' \
  --freshness=7d \
  --format="table(timestamp, protoPayload.authenticationInfo.principalEmail, protoPayload.methodName, protoPayload.resourceName)"

# Who changed the project IAM policy?
gcloud logging read \
  'logName="projects/PROJECT_ID/logs/cloudaudit.googleapis.com%2Factivity"
   AND protoPayload.methodName="SetIamPolicy"' \
  --freshness=7d --limit=10
```

**The same filter in the Console (Logging → Logs Explorer):**
```
logName="projects/PROJECT_ID/logs/cloudaudit.googleapis.com%2Factivity"
protoPayload.methodName:"ServiceAccount"
```

**Fields worth knowing in an entry**
- `protoPayload.authenticationInfo.principalEmail`: **who** made the change
- `protoPayload.methodName`: **what** they did
- `protoPayload.resourceName`: **which** resource was affected
- `timestamp`: **when** it happened

**Quirk:** the `/` in `cloudaudit.googleapis.com/activity` is URL-encoded as `%2F` inside `logName`.

---

## ACE exam notes

- **Configurations:** `gcloud config configurations create | activate | list`. Know that `gcloud config set` writes to
  the *active* configuration, and that per-command flags override it.
- **Two auth commands:** `gcloud auth login` is for the CLI. `gcloud auth application-default login` is ADC, for code
  and Terraform.
- **Enabling APIs:** `gcloud services enable SERVICE`. A "service not enabled" or `SERVICE_DISABLED` error means
  enable the API first.
- **Hierarchy:** Org → Folder → Project → Resource. Policies are inherited downward and are additive, so a child
  can't revoke a parent's grant.
- **Roles:** Prefer predefined over basic. Use custom roles only when nothing predefined fits; they can exist at the
  org or project level, **not folders**.
- **`add-iam-policy-binding` vs `set-iam-policy`:** the first adds one binding. The second **replaces the whole
  policy**, which risks lockout and race conditions (the `etag` field guards against concurrent edits).
- **Least privilege:** the smallest role on the lowest resource. Use groups instead of individual users.
- **Service accounts:** they are both an *identity* (they act on things) and a *resource* (others can be granted
  roles on them, e.g. `roles/iam.serviceAccountUser` lets someone attach the SA to a VM or GKE workload).
- **Service agents** are Google-managed `service-PROJECT_NUMBER@...` accounts. Don't remove their roles.
- **Avoid SA keys.** Use Workload Identity (GKE), attached SAs (Compute Engine, Cloud Run), impersonation, or
  Workload Identity Federation (outside GCP, e.g. GitHub Actions).
- **Roles for impersonation:** `roles/iam.serviceAccountTokenCreator` lets you mint tokens for an SA;
  `roles/iam.serviceAccountUser` lets you attach or run as the SA.
- **Audit logs:** Admin Activity is always on, free, and kept 400 days. Data Access is off by default (except
  BigQuery) and billable. To keep logs longer or analyze them, create a **log sink** to BigQuery, Cloud Storage,
  Pub/Sub, or another log bucket.
- **Viewing logs** requires `roles/logging.viewer`. Viewing **Data Access** logs needs `roles/logging.privateLogViewer`.
