# Phase 6B — The analytics layer: Pub/Sub → BigQuery in Terraform

Phase 6B builds the pipeline designed in [6A](phase-6a-design.md) as a new long-lived Terraform layer,
**`infra/terraform/analytics/`**. It has 12 resources:

- an Avro schema,
- two topics,
- a BigQuery subscription with dead-lettering,
- a pull subscription to inspect the dead letters,
- a dataset, a partitioned table and a dedup view,
- four resource-level IAM grants.

Foundation gained one API. Both applies worked first time, and a smoke test sent a real message end to end. The app
doesn't publish yet: that's 6C.

| Part | What | PR |
|---|---|---|
| 6A | Design, event contract, Avro schema | #28 |
| **6B** | `analytics` layer; `bigquerystorage` API in foundation; smoke test | (this PR) |
| 6C | App publisher (`PUBSUB_TOPIC`, non-blocking), contract v2, contract test | |
| 6D | End to end on GKE; DLQ and error-state drills; smoke-row cleanup | |
| 6E | Looker Studio | |

> **About the commands in this doc.** Code blocks contain commands only, no `#` comments: in zsh, `#` is not a
> comment unless `setopt interactivecomments` is on. Run them from the repo root unless a block starts with `cd`.
> No project ID or project number is committed.

> **Placeholders**
>
> | Placeholder | Meaning |
> |---|---|
> | `PROJECT_ID` | Your GCP project ID (`gcloud config get project`) |
> | `PROJECT_NUMBER` | Your project number (part of the Pub/Sub service agent's email) |

**Contents**

1. [Concepts](#1-concepts)
2. [The code](#2-the-code)
3. [Foundation: one more API](#3-foundation-one-more-api)
4. [Creating the analytics layer](#4-creating-the-analytics-layer)
5. [Smoke test](#5-smoke-test)
6. [What 6A's open questions turned out to be](#6-what-6as-open-questions-turned-out-to-be)
7. [Troubleshooting](#7-troubleshooting)
8. [Known gaps](#8-known-gaps)
9. [Lessons learned](#9-lessons-learned)
10. [Interview talking points](#10-interview-talking-points)
11. [ACE exam notes](#ace-exam-notes)

---

## 1. Concepts

### Why a fifth layer, and why it stays up

The layers are split by **lifetime**:

| Layer | Lifetime |
|---|---|
| `cluster` | Destroyed every session |
| `data` | Stopped every session |
| `analytics` | Stays up |

`analytics` stays up because nothing in it bills by the hour, and because the history in BigQuery has to survive. It
reads only foundation's state, for the `exit-ticket-api` email. Nothing reads it, so it can be planned and applied on
its own.

### Three kinds of delete lock

The table and the dataset hold the data teachers rely on, so each gets three locks. They fail in different ways, which
is why there are three:

| Lock | Where it lives | What it stops | How it could be bypassed |
|---|---|---|---|
| `lifecycle { prevent_destroy = true }` | The **code** | Any plan that would destroy the resource errors out | Someone deletes the resource block, and the next plan destroys it |
| `deletion_protection = true` (table), `deletion_policy = "PREVENT"` (dataset) | **State** | The destroy fails even if the block is gone from the code | An apply that first sets it to false (a visible, reviewable change) |
| `delete_contents_on_destroy = false` (dataset) | The **API call** | Google refuses to delete a dataset that still has tables | Deleting the tables first |

The view, topics and subscriptions get none:

- **The view** is a saved query, so it can be recreated in a second.
- **Recreating a topic or subscription** loses only messages in flight.

### Resource-level IAM

Every grant in this layer is on **one resource**, never on the project. For example, `exit-ticket-api` can publish to
`answer-events` and to nothing else. Each topic, subscription and table has its own small **IAM policy**, visible with
`get-iam-policy` on that resource. The smoke test (§5) prints all of them.

### The Pub/Sub service agent

`service-PROJECT_NUMBER@gcp-sa-pubsub.iam.gserviceaccount.com` is a **Google-managed** account, one per project. Pub/Sub
uses it to do work on your behalf: writing to BigQuery, and forwarding dead letters. You grant it roles, but you never
create it or hold keys for it.

Its email contains the project **number**, not the ID. So the layer reads the number from a `google_project` data source
rather than hard-coding it.

The google 8.11 provider can't create the agent: `google_project_service_identity` exists only in the beta provider. So
§4 checks that it exists before the apply.

### Subscriptions expire by default

A subscription with no activity for **31 days is deleted**, unless `expiration_policy.ttl = ""`, which the provider
documents as "never expires". This project can pause for weeks, so both subscriptions set it. Without it, a quiet
month would silently delete the pipeline.

---

## 2. The code

| File | What |
|---|---|
| [versions.tf](../infra/terraform/analytics/versions.tf), [.terraform.lock.hcl](../infra/terraform/analytics/.terraform.lock.hcl) | Copied from `data/`; identical in every layer. `cmp` confirmed the lock file matches |
| [backend.tf](../infra/terraform/analytics/backend.tf) | GCS backend, prefix `analytics`; bucket passed at `init` |
| [variables.tf](../infra/terraform/analytics/variables.tf), [terraform.tfvars.example](../infra/terraform/analytics/terraform.tfvars.example) | `project_id` (validated), `region` (default `us-east4`) |
| [providers.tf](../infra/terraform/analytics/providers.tf) | `default_labels` (app, env, layer); read-only `terraform_remote_state.foundation`; `data.google_project` for the number; the service agent built in a `local` |
| [bigquery.tf](../infra/terraform/analytics/bigquery.tf) | Dataset, table, view, the service agent's `dataEditor` on the table |
| [pubsub.tf](../infra/terraform/analytics/pubsub.tf) | Schema, two topics, two subscriptions, three IAM grants |
| [outputs.tf](../infra/terraform/analytics/outputs.tf) | `topic_id` (the future `PUBSUB_TOPIC`), `table_id`, `view_id`, `subscription_name`, `dlq_subscription_name` |
| [schemas/answer_event.avsc](../infra/terraform/analytics/schemas/answer_event.avsc) | The contract (6A, unchanged) |
| [schemas/answer_events_table.json](../infra/terraform/analytics/schemas/answer_events_table.json) | The BigQuery schema: 14 REQUIRED contract columns + 4 NULLABLE metadata columns (`attributes` is `JSON`) |

Every argument was checked against the provider's own schema (`terraform providers schema -json`, google 8.11.0), as in
5E:

| Setting | Value | Why |
|---|---|---|
| `google_pubsub_schema.definition` | `file("schemas/answer_event.avsc")` | The provider docs: "Changes to the definition commit new schema revisions". So editing the file is **not** a replace. A schema allows up to 20 revisions |
| `schema_settings.encoding` | `JSON` | Readable messages; no Avro library in the app |
| `message_storage_policy.allowed_persistence_regions` | `[us-east4]` | Both topics store messages in the region where the student data lives |
| `bigquery_config.table` | `PROJECT.DATASET.TABLE`, built from the table resource | The format the provider documents (`{projectId}.{datasetId}.{tableId}`), and the reference makes the table come first |
| `use_topic_schema`, `write_metadata`, `drop_unknown_fields` | `true`, `true`, `false` | 6A §3.3–3.4 |
| `dead_letter_policy` | DLQ topic, `max_delivery_attempts = 5` | 5 is the minimum and the default; per-message rejections are permanent, so more tries wouldn't help |
| `retry_policy` | `10s` to `600s` | Pub/Sub's defaults, written down so the plan shows them |
| `message_retention_duration` | `604800s` (7 days) | How long a message waits in the backlog, e.g. while the subscription is in an error state |
| `expiration_policy.ttl` | `""` | Never expire (§1) |
| `depends_on` on the BigQuery subscription | The table IAM grant | Pub/Sub checks it can write to the table **when it creates the subscription** |
| `time_partitioning` | `DAY` on `answered_at`, no `expiration_ms` | Event time, keep history (6A §3.6) |
| `clustering` | `classroom_id`, `standard_code` | Up to four columns, "in descending priority order" (provider docs) |
| `require_partition_filter` | `false` (top-level) | So a Looker chart without a date filter still works |

A side note on the plan output: `google_bigquery_table` shows `deletion_policy = "DELETE"` even with
`deletion_protection = true`. For tables, `deletion_protection` is the lock, and it's the one that blocks destroys.

---

## 3. Foundation: one more API

The 6A design (§3.1) found that BigQuery subscriptions write through the **BigQuery Storage Write API**: the
[troubleshooting page](https://cloud.google.com/pubsub/docs/bigquery-troubleshooting) names its quota,
`bigquerystorage.googleapis.com/write/append_bytes`. The API was already on, enabled automatically as a dependency of
BigQuery. Listing it in [foundation/apis.tf](../infra/terraform/foundation/apis.tf) makes the dependency explicit. No
other API is needed: Pub/Sub and BigQuery were already listed, and Looker Studio isn't a project API.

```bash
cd infra/terraform/foundation
terraform plan -out=tfplan
terraform apply tfplan
rm tfplan
terraform plan -detailed-exitcode
cd ../../..
```

| Step | Result |
|---|---|
| Plan | `google_project_service.this["bigquerystorage.googleapis.com"] will be created`, `Plan: 1 to add, 0 to change, 0 to destroy` |
| Apply | `Creation complete after 4s`, `Apply complete! Resources: 1 added` |
| Re-plan | `No changes.`, exit 0 |

**Why "created" for an API that was already on, and no import?** For `google_project_service`, "create" means
"make sure it's enabled". Enabling an enabled API is a no-op, so Terraform simply takes it over.

---

## 4. Creating the analytics layer

```bash
gcloud projects get-iam-policy "$(gcloud config get project)" --flatten="bindings[].members" --filter="bindings.members~gcp-sa-pubsub" --format="value(bindings.role)"
cd infra/terraform/analytics
cp terraform.tfvars.example terraform.tfvars
terraform init -backend-config="bucket=$(gcloud config get project)-tfstate"
terraform plan -out=tfplan
terraform apply tfplan
rm tfplan
terraform state list
terraform plan -detailed-exitcode
cd ../../..
```

(`cp` only on a fresh checkout; then fill in `project_id`.)

| Step | Result |
|---|---|
| Service agent check | `roles/pubsub.serviceAgent`: the agent exists |
| Plan | `Plan: 12 to add, 0 to change, 0 to destroy` |
| Apply | All 12 in about 15 seconds, in dependency order (below). `Apply complete! Resources: 12 added` |
| State | 12 resources + 2 data sources |
| Re-plan | `No changes.`, exit 0 |

The order Terraform chose, from the references between resources:

1. **Schema, dataset and DLQ topic together.** None depends on another.
2. The main topic (needs the schema), then the table (needs the dataset).
3. The view and the table grant (both need the table). At the same time: the DLQ grant and the DLQ inspection
   subscription.
4. **`answer-events-to-bq`, only after the table grant**, thanks to `depends_on`. Pub/Sub's create-time check found
   it could write to the table.
5. The service agent's subscriber grant (needs the subscription).

No IAM propagation delay showed up, though one is possible. If the subscription fails with a permission error right
after its grant succeeded, run the plan and apply again without changing anything.

**Outputs:**

| Output | Value |
|---|---|
| `topic_id` | `projects/PROJECT_ID/topics/answer-events` |
| `table_id` | `PROJECT_ID.exit_ticket_analytics.answer_events` |
| `view_id` | `PROJECT_ID.exit_ticket_analytics.answer_events_clean` |
| `subscription_name` | `answer-events-to-bq` |
| `dlq_subscription_name` | `answer-events-dlq-inspect` |

---

## 5. Smoke test

The app doesn't publish until 6C, so the test publishes by hand with gcloud.

Some details of how the test is built:

- **The test event is marked.** It uses `event_type = "smoke_test"`, so its row is easy to find and to delete. The
  schema only requires a string there.
- **`bq` is given `--dataset_id`.** Then the SQL can say `FROM answer_events` and stay in single quotes. In zsh,
  backticks around a full table name would be run as a command.
- **The DLQ pull has no `--auto-ack`,** so it only looks and removes nothing.

```bash
PROJECT="$(gcloud config get project)"
gcloud pubsub topics publish answer-events --message='{"event_id":"7d1f3a52-9c4e-4b8a-a1f6-2e5b8c9d0a11","event_type":"smoke_test","schema_version":2,"answered_at":1795010730123456,"answer_id":"b4b4e164-3866-4de5-a365-a44d2a8360ee","student_id":"49eab8ac-be38-4554-97ab-c48f6d5dd371","classroom_id":"0f3eca60-0172-41a5-8641-037e16698014","ticket_id":"59ce99e2-6827-43fe-bd8a-df0bc78198bb","question_id":"caed3c50-7512-4255-a948-b84a67755685","standard_code":"NY-5.NF.4","standard_domain":"NY-5.NF","subject":"math","chosen_choice":"C","is_correct":true}'
gcloud pubsub topics publish answer-events --message='{"event_id":"7d1f3a52-9c4e-4b8a-a1f6-2e5b8c9d0a11","is_correct":"yes"}'
sleep 30
bq --project_id="$PROJECT" --dataset_id=exit_ticket_analytics query --use_legacy_sql=false --format=prettyjson 'SELECT event_id, event_type, answered_at, standard_code, is_correct, subscription_name, message_id, publish_time, JSON_VALUE(attributes, "$.googclient_schemarevisionid") AS schema_revision, JSON_VALUE(attributes, "$.googclient_schemaencoding") AS encoding FROM answer_events WHERE event_type = "smoke_test"'
bq --project_id="$PROJECT" --dataset_id=exit_ticket_analytics query --use_legacy_sql=false 'SELECT COUNT(*) AS rows_in_view FROM answer_events_clean WHERE event_type = "smoke_test"'
gcloud pubsub subscriptions describe answer-events-to-bq --format="value(state)"
gcloud pubsub subscriptions pull answer-events-dlq-inspect --limit=5
gcloud pubsub topics get-iam-policy answer-events --format=yaml
gcloud pubsub topics get-iam-policy answer-events-dlq --format=yaml
gcloud pubsub subscriptions get-iam-policy answer-events-to-bq --format=yaml
bq --project_id="$PROJECT" get-iam-policy --format=prettyjson exit_ticket_analytics.answer_events
gcloud projects get-iam-policy "$PROJECT" --flatten="bindings[].members" --filter="bindings.role~pubsub" --format="table(bindings.role,bindings.members)"
terraform -chdir=infra/terraform/analytics plan -detailed-exitcode > /dev/null; echo "analytics exit code: $?"
terraform -chdir=infra/terraform/foundation plan -detailed-exitcode > /dev/null; echo "foundation exit code: $?"
```

Results (2026-11-18):

| # | Check | Result | OK? |
|---|---|---|---|
| 1 | Publish the valid event | `messageIds: '22637063468810153'` | ✅ |
| 2 | Publish the invalid event | `INVALID_ARGUMENT: … Message failed schema validation`; `revisionInfo: … last checked revision: revision_id=cb8202da failed … Field was not found in JSON object: event_type`; reason `INVALID_JSON_AVRO_MESSAGE` | ✅ |
| 3 | Row in `answer_events` | One row: `message_id` matches #1, `subscription_name` = `projects/PROJECT_NUMBER/subscriptions/answer-events-to-bq`, `publish_time` set, `schema_revision = cb8202da`, `encoding = JSON` | ✅ |
| 4 | The view | `rows_in_view = 1` | ✅ |
| 5 | Subscription state | `ACTIVE` | ✅ |
| 6 | DLQ | `Listed 0 items.` | ✅ |
| 7a | Topic `answer-events` IAM | Only `exit-ticket-api` → `roles/pubsub.publisher` | ✅ |
| 7b | Topic `answer-events-dlq` IAM | Only the service agent → `roles/pubsub.publisher` | ✅ |
| 7c | Subscription IAM | Only the service agent → `roles/pubsub.subscriber` | ✅ |
| 7d | Table IAM | Only the service agent → `roles/bigquery.dataEditor` | ✅ (second try, see §7) |
| 7e | Project-level Pub/Sub roles | Only `roles/pubsub.serviceAgent` for the service agent: **nothing project-wide was granted** | ✅ |
| 8–9 | Re-plans | analytics exit 0, foundation exit 0 | ✅ |

What the results show:

- **Why the rejection in #2 is good.** The schema stops a broken message **at the door**. It never enters the topic,
  never reaches the subscription, BigQuery or the DLQ, and the publisher learns about it at once. That's the cheapest
  possible place to catch it.
- **The schema revision is traced end to end.** The rejection names revision `cb8202da`, and the row that got in
  records the same revision in `attributes`. Every row says which version of the contract it passed. That's the
  `write_metadata` decision paying off.
- **Where your Owner role is.** You (Owner) don't appear in 7a–7d, because Owner is granted on the project and applies
  to every resource without being listed on each one. The resource policies hold only what this layer granted.
- **Display rounding.** `answered_at` displayed as `2026-11-18 14:05:30`: `bq`'s output drops the microseconds, but
  the stored value keeps `.123456`.
- **The time gap is meaningless here.** `publish_time` is about 11 hours after `answered_at` only because the test
  reused 6A's example timestamp. Real events will show the real delay.

### The smoke-test row: kept until 6D

Rows written through the streaming path (the Storage Write API) may not be deletable with DML (`DELETE`/`UPDATE`) right
away: BigQuery can refuse while they're still settling, often for up to about an hour. A refused `DELETE` is harmless,
just early. 6D will add more test events (the DLQ and error-state drills), so **all test rows are deleted together at
the end of 6D**:

```sql
DELETE FROM answer_events WHERE event_type = "smoke_test"
```

Until then the row is clearly labelled, and Looker Studio (6E) will only chart `answer_submitted`.

---

## 6. What 6A's open questions turned out to be

| 6A §7 question | Answer | Evidence |
|---|---|---|
| REQUIRED columns accept the required Avro fields | **Yes** | Pub/Sub checks compatibility when it creates the subscription, and the create succeeded; the row landed |
| A day-partitioned, clustered table works | **Yes** | Same |
| `attributes` as `JSON` | **Yes** | `JSON_VALUE(attributes, …)` returned the revision and the encoding |
| Does the service agent exist before the grants? | **Yes** | `roles/pubsub.serviceAgent` binding present before the apply |
| How does `google_pubsub_schema` change? (6A §9) | **A new revision, in place** | Provider docs for `definition`. Still to see in practice with the first real schema change |
| Unknown keys dropped, not written | Still open | Moved to the 6D drill list |

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `bq get-iam-policy … "PROJECT:dataset.table"` → `Not found: Table PROJECT:xit_ticket_analytics…` (first letter of the dataset gone) | `bq` misread the colon form for this command | Name the project with the global flag instead: `bq --project_id=PROJECT get-iam-policy exit_ticket_analytics.answer_events` |
| Subscription create fails with permission denied right after the table grant | IAM propagation | Re-run plan and apply unchanged |
| Subscription create fails with `table not found` or `schema mismatch` | The table doesn't match the Avro schema, or doesn't exist yet | Pub/Sub's create-time check, so nothing was half-built. Fix the table JSON or the `.avsc`, then apply |
| Subscription state not `ACTIVE` | Table deleted, the service agent lost its grant, or the table no longer matches the schema | Messages wait in the backlog for 7 days and are **not** dead-lettered. `gcloud pubsub subscriptions describe answer-events-to-bq` and the [troubleshooting page](https://cloud.google.com/pubsub/docs/bigquery-troubleshooting); fix, and it drains |
| Row doesn't appear after 30 s | Normal delay, or the state above | Wait a minute and query again; then check the state |
| `DELETE` on a fresh row refused | Recently streamed rows can't be modified yet | Try again later (§5) |
| A grant fails with "service account does not exist" | The Pub/Sub service agent hasn't been created | 📖 `gcloud beta services identity create --service=pubsub.googleapis.com`, then apply again |

---

## 8. Known gaps

- **The app doesn't publish yet.** 6C: `PUBSUB_TOPIC` (= the `topic_id` output), a non-blocking publisher, the closed
  event model and the model-vs-schema contract test.
- **The subscription state is checked by hand.** It's now in the end-of-session checklist
  ([phase-5-terraform.md §6](phase-5-terraform.md#6-verification-checklist)). An alert on state or backlog age is
  Phase 7/8.
- **No drill yet for either failure path.** 6D: a per-message rejection into the DLQ, and a removed grant for the error
  state.
- **The smoke-test row** stays until the end of 6D (§5).
- **Looker Studio runs on your Owner access.** Phase 8.

---

## 9. Lessons learned

| Lesson | Where |
|---|---|
| **The plan is the review.** Twelve resources were checked one at a time before the apply, and the apply did exactly that | §4 |
| **Check arguments against the provider schema, not memory.** It turned up that `google_project_service_identity` is beta-only, that schema edits are revisions, and that `ttl = ""` means never | §1, §2 |
| **Defaults can delete things.** Without `ttl = ""`, a quiet month would have deleted both subscriptions | §1 |
| **Encode ordering as dependencies.** `depends_on` from the subscription to the table grant matches Pub/Sub's create-time check | §4 |
| **A schema rejection is a success.** Test the "no" path as deliberately as the "yes" path | §5 |
| **Resource-level IAM is easy to prove.** Five `get-iam-policy` calls show the whole access story | §5 |
| **When a CLI mangles an argument, read the error closely.** `xit_ticket_analytics` gave it away | §7 |

---

## 10. Interview talking points

- *"How did you make sure the subscription wouldn't be created before it could write?"* `depends_on` on the table IAM
  grant. Pub/Sub verifies write access and schema compatibility at create time, so the order matters, and a mismatch
  fails the apply instead of breaking quietly later.
- *"How do you protect analytics data from a bad `terraform destroy`?"* Three locks of different kinds: in the code
  (`prevent_destroy`), in the state (`deletion_protection` / `deletion_policy = PREVENT`), and in the API
  (`delete_contents_on_destroy = false`). Each covers a way the others can be bypassed.
- *"Least privilege, concretely?"* Four grants, all on single resources. The app's account can publish to one topic.
  Google's own service agent can write one table and handle dead letters. `get-iam-policy` on each resource proves it,
  and the project policy shows nothing new.
- *"What's a service agent?"* A Google-managed identity that acts for a service inside your project, here Pub/Sub
  writing to BigQuery. You grant it roles but never create it or hold keys for it.
- *"What would page you about this pipeline?"* A subscription that isn't `ACTIVE`, or a growing backlog. Those failures
  skip the dead-letter topic, so a DLQ alone isn't monitoring.

---

## ACE exam notes

- `gcloud pubsub topics publish TOPIC --message=…` returns `messageIds`. On a topic with a schema, a bad message
  fails with `INVALID_ARGUMENT`.
- `gcloud pubsub subscriptions pull SUB --limit=N` looks at messages; `--auto-ack` also removes them.
- Topic, subscription and BigQuery table IAM are separate policies: `get-iam-policy` on each.
- `bq query --use_legacy_sql=false` for GoogleSQL. `--project_id` and `--dataset_id` are global flags that set defaults.
- Subscriptions expire after **31 days** of inactivity by default. Retention of unacked messages defaults to
  **7 days**.
- Service agent email format: `service-PROJECT_NUMBER@gcp-sa-SERVICE.iam.gserviceaccount.com`.
- BigQuery partitioning (by a `DATE`/`TIMESTAMP` column, ingestion time or integer range) and clustering (up to four
  columns) are both set when the table is created.
