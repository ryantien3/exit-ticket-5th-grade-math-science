# Phase 6A — Design: Pub/Sub → BigQuery → Looker Studio

Phase 6 sends every graded answer to an analytics pipeline. Teachers then see mastery by standard in a Looker Studio
report, without any query touching Cloud SQL. Phase 6A is **design only**: this doc and the Avro schema file.
Nothing is applied, no app code changes. The parts after it are a proposal.

| Part | What | PR |
|---|---|---|
| **6A** | Design: architecture, decisions, event contract, Avro schema | (this PR) |
| 6B | `analytics` Terraform layer: schema, topics, subscriptions, dataset, table, view, IAM | |
| 6C | App: real `publish_answer_event()` (non-blocking, no-op without `PUBSUB_TOPIC`), contract v2, tests | |
| 6D | End to end on GKE; dead-letter drill; error-state drill; duplicate check | |
| 6E | Looker Studio report on the view | |

> **About the commands in this doc.** Code blocks contain commands only, no `#` comments: in zsh, `#` is not a
> comment unless `setopt interactivecomments` is on. Run them from the repo root. No project ID or project number is
> committed.

> **Placeholders**
>
> | Placeholder | Meaning |
> |---|---|
> | `PROJECT_ID` | Your GCP project ID (`gcloud config get project`) |
> | `PROJECT_NUMBER` | Your project number (`gcloud projects describe PROJECT_ID --format="value(projectNumber)"`) |

**Contents**

1. [Architecture](#1-architecture)
2. [Concepts](#2-concepts)
3. [Decisions](#3-decisions)
4. [The event contract](#4-the-event-contract)
5. [The table and the view](#5-the-table-and-the-view)
6. [Files in this PR](#6-files-in-this-pr)
7. [To verify in 6B–6E](#7-to-verify-in-6b6e)
8. [Cost](#8-cost)
9. [Known gaps](#9-known-gaps)
10. [Interview talking points](#10-interview-talking-points)
11. [ACE exam notes](#ace-exam-notes)
12. [Sources](#sources)

---

## 1. Architecture

```
 GKE Autopilot (private nodes, us-east4)
 ┌──────────────────────────────────────┐
 │ api pod                              │
 │  submit_exit_ticket()                │
 │   1. save answers, COMMIT (Cloud SQL)│
 │   2. publish_answer_event() x N      │  KSA api ──Workload Identity──► GSA exit-ticket-api
 │      (non-blocking, errors logged)   │                                  roles/pubsub.publisher
 └──────────────────┬───────────────────┘                                  on answer-events ONLY
                    │ HTTPS to pubsub.googleapis.com
                    │ via Private Google Access (no public IP, no NAT)
                    ▼
 ┌──────────────────────────────────────┐
 │ topic: answer-events                 │  schema: answer-event (Avro, JSON encoding)
 │                                      │  → a message that doesn't match is REJECTED at publish
 └──────────────────┬───────────────────┘
                    │
                    ▼
 ┌──────────────────────────────────────┐   a message BigQuery rejects,
 │ subscription: answer-events-to-bq    │   after 5 delivery attempts
 │  type: BigQuery                      │──────────────────────────────┐
 │  use_topic_schema, write_metadata    │                              ▼
 │  dead letter → answer-events-dlq     │        ┌────────────────────────────────────┐
 └──────────────────┬───────────────────┘        │ topic: answer-events-dlq           │
                    │ written by the Pub/Sub     └─────────────────┬──────────────────┘
                    │ service agent                                ▼
                    │ service-PROJECT_NUMBER@gcp-sa-pubsub...      ┌────────────────────────────────────┐
                    ▼                                              │ subscription:                      │
 ┌────────────────────────────────────────┐                        │ answer-events-dlq-inspect (pull)   │
 │ BigQuery dataset exit_ticket_analytics │ (us-east4)             │ you read it with gcloud            │
 │  table answer_events                   │                        └────────────────────────────────────┘
 │   partitioned by DAY(answered_at)      │
 │   clustered by classroom_id,           │
 │                standard_code           │
 │  view  answer_events_clean             │ ← one row per event_id (dedup)
 └──────────────────┬─────────────────────┘
                    │ your user credentials (for now)
                    ▼
            Looker Studio report
```

Three identities appear in the diagram:

| Identity | What it does | Gets |
|---|---|---|
| `exit-ticket-api` (GSA, through Workload Identity) | The app publishes | `roles/pubsub.publisher` on `answer-events` |
| Pub/Sub service agent `service-PROJECT_NUMBER@gcp-sa-pubsub.iam.gserviceaccount.com` | Google's own account that runs the BigQuery subscription and moves messages to the dead-letter topic | Data Editor on the table; publisher on the DLQ topic; subscriber on the source subscription |
| You | Read the DLQ, build the report | Already Owner (Phase 8 review) |

**Why the pod needs no new network setup.** The nodes are private (no public IPs), and the subnet has **Private Google
Access** turned on ([foundation/network.tf](../infra/terraform/foundation/network.tf),
`private_ip_google_access = true`). That lets private VMs reach Google APIs like `pubsub.googleapis.com`. It's the same
path the pod already uses for Secret Manager and Logging ([5E](phase-5e-cluster.md#why-no-cloud-nat)).

---

## 2. Concepts

Read this section first if Pub/Sub or BigQuery are new to you.

**Pub/Sub, in one paragraph.** A **topic** is a named mailbox you publish messages to. A **subscription** is a
queue attached to a topic: every message published to the topic gets a copy in every subscription. A subscriber
processes a message and **acks** it ("done, delete it"). If no ack arrives, Pub/Sub delivers it again. That's why
Pub/Sub is **at-least-once**: a message can arrive twice, but it isn't silently dropped. Unacked messages wait in the
subscription's **backlog** for its retention period (7 days by default), then expire. The publisher (our API) doesn't
know or care who reads the topic. This is called **decoupling**. If BigQuery is slow or down, students can still submit.

**Four kinds of subscription.**

| Kind | Who does the work | Our use |
|---|---|---|
| Pull | Your code asks Pub/Sub for messages | DLQ inspection (you, with gcloud) |
| Push | Pub/Sub POSTs each message to your HTTPS endpoint | — |
| **BigQuery** | **Pub/Sub itself writes each message as a row in a BigQuery table** | The main path |
| Cloud Storage | Pub/Sub writes batches of files to a bucket | — |

**A schema on a topic** is a contract that Pub/Sub enforces. When the topic has a schema, Pub/Sub checks every
message at publish time and rejects a message that doesn't match. Schemas are written in Avro or Protocol Buffers. The
**encoding** says how a message is sent: as JSON text (easy to read and debug) or as compact binary.

A schema can have up to 20 **revisions**. The topic says which range of revisions it accepts.

**BigQuery, in one paragraph.** BigQuery is Google's serverless data warehouse. A **dataset** is a folder of tables
with a fixed **location** (region), which can't change after creation. You pay for **storage** and for **bytes
scanned** by queries. Both are tiny for us. That's why two table features matter:

- **Partitioning** splits a table into one chunk per day. A query with `WHERE answered_at >= '2026-11-01'` reads only
  those days' chunks.
- **Clustering** sorts the rows inside each partition by up to four columns, so a filter on those columns skips blocks.

A **view** is a saved query that looks like a table. Our view hides duplicates.

**Looker Studio** is Google's free report builder. It connects to BigQuery and runs queries when someone opens the
report.

---

## 3. Decisions

Each table lists the alternatives considered. The chosen option is in **bold**.

### 3.1 Where the infrastructure lives: a new `analytics` layer

| Option | Pros | Cons |
|---|---|---|
| Add to `foundation` | One fewer layer | Foundation is about project-wide basics (APIs, IAM, network). Mixing data in makes its plans noisy and its blast radius bigger |
| Add to `data` | It's "data" | `data` is Cloud SQL, which you start and stop every session with `-var`. Analytics has a different lifecycle |
| Add to `cluster` | — | **Wrong**: `cluster` is destroyed every session. Pub/Sub and BigQuery must stay, or answers are lost while the cluster is down |
| **New `infra/terraform/analytics`** | Long-lived, own state (prefix `analytics`), own plan | One more layer to plan in the drift check |

How it fits the existing layers:

- It reads **foundation** remote state for `api_sa_email` (the publisher grant) and `project_number` (to build the
  Pub/Sub service agent's email), the same `terraform_remote_state` pattern as
  [cluster/providers.tf](../infra/terraform/cluster/providers.tf). Dependencies still flow one way: foundation never
  reads analytics.
- **APIs stay in foundation.** [foundation/apis.tf](../infra/terraform/foundation/apis.tf) already lists
  `pubsub.googleapis.com` and `bigquery.googleapis.com`, and the Phase 6 start-up check showed both enabled.
  - Looker Studio isn't a project API.
  - `bigquerystorage.googleapis.com` was turned on automatically as a dependency of `bigquery.googleapis.com`. A
    BigQuery subscription writes through the BigQuery Storage Write API: its quota, `bigquerystorage.googleapis.com/write/append_bytes`,
    appears in the [troubleshooting page](https://cloud.google.com/pubsub/docs/bigquery-troubleshooting). So 6B adds it to
    the foundation list explicitly. A dependency the pipeline needs shouldn't rely on an automatic side effect.
- **Delete locks** guard the history teachers rely on:
  - Table: `deletion_protection = true` (Terraform-side; it's the provider default, set explicitly so it's visible).
  - Dataset: `delete_contents_on_destroy = false`, so a destroy fails while tables exist.
  - Both: `lifecycle { prevent_destroy = true }`, so even a plan that would destroy them errors out.

  Deleting the table would also put the subscription into an error state (§3.5). Topics and subscriptions don't get
  locks: recreating them loses only messages in flight.

### 3.2 Delivery: a BigQuery subscription

| Option | How | Pros | Cons |
|---|---|---|---|
| **BigQuery subscription** | Pub/Sub writes rows itself | No code, no servers, nothing to run when the cluster is down, nothing billed while idle | Little transformation: the message shape must match the table. At-least-once only |
| Push / pull subscription + our code | A worker (Cloud Run or a pod) reads and calls the BigQuery Storage Write API | Full control, can enrich | Code to write, test, deploy, scale and monitor. A pod would stop with the cluster |
| Dataflow (Pub/Sub → BigQuery template) | Apache Beam streaming job | Transformations; the docs note the template "enforces exactly once delivery by default" | A streaming job runs workers all the time: dollars per day for a handful of rows |

The BigQuery subscription wins clearly. We control the message shape (the contract is ours), and we need no
transformation that SQL in a view can't do later. Its one weakness, duplicates, is handled in §3.8.

### 3.3 Schema: Avro topic schema with JSON encoding, `use_topic_schema`

**Schema language and encoding.**

| Option | Pros | Cons |
|---|---|---|
| No topic schema | Simplest | Any bytes are accepted. Mistakes show up only as BigQuery write failures, after the fact |
| Protocol Buffers schema | Typed, compact | Needs generated code or descriptors in Python; harder to read |
| **Avro schema, JSON encoding** | Typed, validated at publish; JSON is plain text you can read in logs and in the DLQ; Python needs no extra library | JSON is bigger than binary (irrelevant at our size); Avro's JSON rules for unions are a trap (see below) |
| Avro schema, binary encoding | Smallest messages | Needs an Avro library in the app; messages unreadable when debugging |

**How the subscription maps fields to columns.**

| Option | What it does | Fit |
|---|---|---|
| **`use_topic_schema = true`** | Writes each schema field to the table column with the same name, using the documented Avro→BigQuery type mapping | Our topic has a schema, so this is the natural pairing, and compatibility is checked when the subscription is created |
| `use_table_schema = true` | "Use the schema of the BigQuery table to write the fields of a JSON message to the corresponding columns." Messages must be JSON | Built for messages shaped by the table, not by a topic schema. We'd give up the topic schema as the single contract |
| Neither | Writes the whole message into one `data` column | We'd have to parse JSON in every query |

**What the docs say about types.** The
[BigQuery subscriptions → Schema compatibility](https://cloud.google.com/pubsub/docs/bigquery) tables map Avro to
BigQuery like this:

| Avro | Allowed BigQuery types |
|---|---|
| `boolean` | `BOOLEAN` |
| `int`, `long` | `INTEGER`, `NUMERIC`, `BIGNUMERIC` |
| `string` | `STRING`, `JSON`, `TIMESTAMP`, `DATETIME`, `DATE`, `TIME`, `NUMERIC`, `BIGNUMERIC` |
| `enum` | **`INTEGER`** (not STRING) |
| logical type `timestamp-micros` (and `timestamp-millis`) | `TIMESTAMP` |
| union of `null` and one type `T` | `NULLABLE T`. Any other union is unmappable |
| logical type `uuid` | Not in the table. The docs: "Any logical types not listed only match the equivalent Avro type that they annotate" (so: string) |

The rules on column **modes** come from
[Create BigQuery subscriptions → Use topic schema](https://cloud.google.com/pubsub/docs/create-bigquery-subscription):

- "Required fields in the topic schema don't need to be required in the BigQuery schema."
- "Any optional field in the topic schema must also be optional in the BigQuery schema."
- BigQuery columns that aren't in the topic schema must be NULLABLE.

**Our choices, from those tables.**

| Field kind | Avro type | BigQuery column | Why this one |
|---|---|---|---|
| Timestamp `answered_at` | `long` with `logicalType: timestamp-micros` | `TIMESTAMP` | Documented mapping. Pub/Sub checks it's a number at publish. A `string` is also allowed into TIMESTAMP, but its text then has to match BigQuery's format, and a badly formatted string would pass the schema check and only fail later, at write time |
| `is_correct` | `boolean` | `BOOL` | Documented 1:1 |
| `schema_version` | `int` | `INT64` | Documented |
| UUIDs, codes, `subject`, `chosen_choice` | `string` | `STRING` | BigQuery has no UUID type. **Not `enum`**: an Avro enum lands as an **integer**, so `subject` would become `0`/`1` in BigQuery. Not `uuid`: unlisted, so it adds nothing over `string` |

**No nullable fields, on purpose.** In Avro's JSON encoding, a value in a union like `["null", "string"]` isn't
written as plain `"x"`. It has to be wrapped as `{"string": "x"}`. The
[associate-schema page](https://cloud.google.com/pubsub/docs/associate-schema-topic) warns: "If using JSON with an
Avro schema, pay close attention to the encoding rules for unions", and points to the
[Avro specification](https://avro.apache.org/docs/1.11.1/specification/#json-encoding). Every field we have is always
known at publish time, so none needs to be optional, and the message stays ordinary JSON.

**What `answered_at` looks like on the wire.** `timestamp-micros` annotates an Avro `long`, and Avro's JSON encoding
writes a `long` as a JSON number. So the value is an integer: microseconds since 1970-01-01 UTC.
`2026-11-18T14:05:30.123456Z` is `1795010730123456`. The Google pages don't spell this out; §6's `validate-message`
check confirms it against Pub/Sub itself. In 6C, convert with integer arithmetic,
`(dt - EPOCH) // timedelta(microseconds=1)`, not `dt.timestamp() * 1_000_000`, because a float loses the last digits.

**`drop_unknown_fields = false`** (the default). The docs: without it, "messages with extra fields are not written to
BigQuery and remain in the subscription backlog unless you configure a dead letter topic." Keeping it off means a
schema/table mismatch is loud instead of silently losing a column. §9 gives the evolution order that avoids it.

### 3.4 `write_metadata = true`

| Option | Pros | Cons |
|---|---|---|
| **Yes** | Adds Pub/Sub's own view of each message: lets us measure pipeline latency (`publish_time - answered_at`), spot redeliveries (same `message_id` twice) and see which schema revision each row came from | Four extra columns; a name-clash rule |
| No | Smaller table | Duplicates and delays become invisible |

[Create BigQuery subscriptions → Write metadata](https://cloud.google.com/pubsub/docs/create-bigquery-subscription)
lists these columns:

| Column | BigQuery type | Contents |
|---|---|---|
| `subscription_name` | `STRING` | Which subscription wrote the row |
| `message_id` | `STRING` | Pub/Sub's ID for the message |
| `publish_time` | `TIMESTAMP` | When Pub/Sub accepted it |
| `attributes` | `STRING` or `JSON` | "A JSON object containing all message attributes", plus things like the ordering key |
| `data` | `BYTES`, `STRING` or `JSON` | The raw body. Needed only when neither use-topic-schema nor use-table-schema is set, **so we don't create it** |

Notes on these columns:

- **`attributes` won't be empty.** We set no attributes ourselves, but on a schema topic Pub/Sub adds three to every
  message it accepts ([publish-topics-schema](https://cloud.google.com/pubsub/docs/publish-topics-schema)):
  `googclient_schemaname`, `googclient_schemaencoding` and `googclient_schemarevisionid`. That records the schema
  revision of every row for free. We use **`JSON`** for the column so a query can read it with
  `JSON_VALUE(attributes, '$.googclient_schemarevisionid')`.
- **The name-clash rule.** With write metadata and use topic schema, "the schema of the topic must not contain any
  fields with names that match those of the metadata parameters", camelCase spellings included. None of our 14 names
  clash.

### 3.5 Failure handling and dead-lettering

There are **three** different failure paths, and only one of them goes to the dead-letter topic:

| Failure | Example | What happens | Where you see it |
|---|---|---|---|
| **1. Bad message at publish** | Missing field, wrong type, typo in a key | Pub/Sub "returns an `INVALID_ARGUMENT` error to the publish request". The message never enters the topic | API logs (the done-callback, §3.7) |
| **2. One message BigQuery rejects** | A value Pub/Sub accepts but the table doesn't, e.g. a string longer than a column's max length | Retried. After `max_delivery_attempts`, moved to **`answer-events-dlq`** with an attribute `CloudPubSubDeadLetterSourceDeliveryErrorMessage` saying why | Pull `answer-events-dlq-inspect` |
| **3. The subscription itself is broken** | Table deleted, service agent lost its grant, table schema changed so it's no longer compatible | The subscription goes into an **error state**. Messages "are not written to the BigQuery table and remain in the subscription backlog. … messages are not delivered to an attached dead-letter topic". After the fix it returns to healthy and drains the backlog | Subscription state in the Console or gcloud; a growing backlog |

Sources: [publish-topics-schema](https://cloud.google.com/pubsub/docs/publish-topics-schema),
[BigQuery subscriptions → Handle message failures](https://cloud.google.com/pubsub/docs/bigquery),
[Troubleshoot a BigQuery subscription](https://cloud.google.com/pubsub/docs/bigquery-troubleshooting).

**Why path 3 matters for us.** Backlog messages wait only for the subscription's `message_retention_duration` (7 days by
default), then they're gone. Our safety net is that Cloud SQL holds every answer (§9 backfill). In 6B the
subscription state becomes part of the session-start check, alongside "Cloud SQL stopped".

**Path 2 is rare by design.** The topic schema blocks most bad messages at path 1. The table matches the schema
(which Pub/Sub verifies when it creates the subscription), so very little can still fail per message. The DLQ is a
safety net, not something we expect to fill.

**`max_delivery_attempts = 5`.**

- The allowed range is 5–100, and the default is 5
  ([Dead-letter topics](https://cloud.google.com/pubsub/docs/handling-failures)).
- The per-message rejections that reach the DLQ are permanent: attempt 6 fails exactly like attempt 1.
- The docs add that with a DLQ attached, Pub/Sub "does not back off delivery when message failures are due to schema
  compatibility errors".

**The inspection subscription** `answer-events-dlq-inspect` is a plain pull subscription.

- **Why it's needed:** a topic with no subscription stores nothing. The docs' samples warn that "messages published to
  a topic with no subscriptions are lost".
- **How you read it:** with `gcloud pubsub subscriptions pull` in 6D. The DLQ copy keeps the original JSON body
  (readable, thanks to JSON encoding) plus the error attribute.

**Permissions for dead-lettering.** The Pub/Sub service agent forwards the message, so it needs:

- `roles/pubsub.publisher` on `answer-events-dlq`, to "publish messages to the topic".
- `roles/pubsub.subscriber` on `answer-events-to-bq`, to "acknowledge the messages, which removes them from the
  subscription".

The docs say to grant these "after you create the dead-letter topic". In Terraform, the grants reference the topic and
subscription resources, so Terraform orders them automatically.

### 3.6 The table

| Setting | Choice | Alternatives and why not |
|---|---|---|
| Dataset location | **`us-east4`** (single region) | `US` multi-region: the cluster and Cloud SQL are both in us-east4, so student data stays in one region. Location can't change later. The Pub/Sub pages give no constraint linking topic and dataset location |
| Partitioning | **By `DAY` on `answered_at`** (the event time) | Ingestion-time or `publish_time`: a delayed message would land on the wrong day. Monthly: fine too at our size, but daily matches "this week's tickets" reports |
| Clustering | **`classroom_id`, `standard_code`** | A teacher's report always filters to their classroom first, then by standard, so that's the order. `subject` would be a weak third (only two values) |
| Partition expiration | **None** | See below |
| `require_partition_filter` | **false** | `true` would make every Looker Studio chart without a date filter fail with an error |

**Why no partition expiration.** An expiration deletes old partitions automatically. That's the right tool for
high-volume logs you don't need. Here:

- The data is tiny. A class of 30 doing five 5-question tickets a week is about 750 rows/week. The whole school year is
  a few MiB.
- Teachers need **history across the school year** to see growth on a standard. Silent deletion would destroy exactly
  the part of the data that's valuable.

When synthetic-data cleanup or a real retention policy is needed, it should be a deliberate decision (Phase 8), not a
default.

**Clustering at this size does nothing measurable.** BigQuery only skips blocks once a partition holds a lot of data.
We set it because it costs nothing and it's the correct design for the access pattern.

### 3.7 Publish timing

What happens today ([exit_tickets.py](../app/api/app/routes/exit_tickets.py), `submit_exit_ticket`) stays the same:

- `publish_answer_event()` is called once per answer, **after** `session.commit()` succeeds, so analytics never sees an
  answer that wasn't saved.
- Each call is wrapped in `try/except` and failures are logged, so the student's score is always returned.

Phase 6C changes only the body:

| Rule | How |
|---|---|
| **Non-blocking** | The Pub/Sub client's `publish()` returns a **future** right away and sends in a background thread, batching messages. We **don't** call `future.result()` in the request, which would make a 5-question submission wait for 5 round trips. A **done-callback** logs a failure when it happens |
| **Errors logged, not raised** | The existing `try/except` stays, and the callback logs async failures (including path-1 `INVALID_ARGUMENT` rejections) with the `event_id` |
| **No-op when `PUBSUB_TOPIC` is unset** | `Settings` gets `pubsub_topic: str \| None = None`. If it's unset (local dev, pytest), nothing is published and no client is created, so tests never need credentials. The `answer_event` log line stays in both cases |
| **One client per process** | The `PublisherClient` is created once (lazily) and reused. On shutdown, the FastAPI lifespan calls `publisher.stop()`, which flushes the batch the pod is still holding |
| Config | `PUBSUB_TOPIC` = `projects/PROJECT_ID/topics/answer-events`, added to [k8s/configmap.yaml](../k8s/configmap.yaml) and to the explicit `env:` list in [k8s/api.yaml](../k8s/api.yaml) |

**The known gap.** The answer rows and the event aren't saved together. If the pod dies after `COMMIT` but before
the background thread sends the batch (OOM kill, node preemption, `kill -9`), those events are lost, even though the
answers are safely in Cloud SQL. The graceful-shutdown flush covers normal rollouts, but not crashes.

The standard fix is the **transactional outbox**:

1. Write the event to an `outbox` table **in the same transaction** as the answers.
2. A relay publishes the outbox rows and marks them sent.

It's deferred:

- It needs a relay process.
- A lost event is recoverable, because Cloud SQL holds the truth and a backfill can republish.
- At our volume the window is milliseconds.

See §9.

### 3.8 Duplicates

The docs say it plainly: "the BigQuery subscription only supports at least once delivery. If exact deduplication is
critical for your use case, consider downstream processes in BigQuery to handle potential duplicates"
([BigQuery subscriptions](https://cloud.google.com/pubsub/docs/bigquery)).

Duplicates come from two places:

1. **Redelivery.** The subscription writes the same message again (same `message_id`).
2. **Publisher retries.** If the client times out waiting for Pub/Sub's reply and retries, Pub/Sub may store **two
   messages** with **different** `message_id`s but the same body.

So `message_id` can't dedup case 2. The **`event_id`** we put in the body can:

- It's made once, when the event object is built.
- Every retry and redelivery carries the same value.

The view keeps one row per `event_id` (§5). Looker Studio reads only the view, never the raw table.

(For now, `answer_id` is also one per event and would work. `event_id` is separate so the same rule works for future
event types that aren't about a single answer.)

### 3.9 IAM

Least privilege, granted on the **resource**, not the project:

| Who | Role | On | Why |
|---|---|---|---|
| `exit-ticket-api` GSA | `roles/pubsub.publisher` | Topic `answer-events` only | Publish. Not on the DLQ (the app has no reason to write there), not project-wide |
| Pub/Sub service agent | `roles/bigquery.dataEditor` | Table `answer_events` only | The [docs](https://cloud.google.com/pubsub/docs/create-bigquery-subscription) allow it "either for the project or the table". The table is the smaller blast radius. The permissions the service needs are `bigquery.tables.get` and `bigquery.tables.updateData` ([troubleshooting](https://cloud.google.com/pubsub/docs/bigquery-troubleshooting)), and Data Editor includes both |
| Pub/Sub service agent | `roles/pubsub.publisher` | Topic `answer-events-dlq` | Forward dead letters |
| Pub/Sub service agent | `roles/pubsub.subscriber` | Subscription `answer-events-to-bq` | Ack the original after forwarding |
| You | (existing Owner) | Project | Create the resources (Terraform runs as you), Looker Studio with your user credentials, DLQ pulls |

Notes:

- **The Pub/Sub service agent** is a Google-managed account, one per project, named
  `service-PROJECT_NUMBER@gcp-sa-pubsub.iam.gserviceaccount.com`. It isn't one of our service accounts, so it isn't in
  [foundation/service_accounts.tf](../infra/terraform/foundation/service_accounts.tf). Terraform builds the email from
  foundation's `project_number` output.
- **Alternative: a custom service account** (`service_account_email` on the subscription). It's more explicit, but it
  needs Data Editor itself, `iam.serviceAccounts.getAccessToken` for the Pub/Sub service agent on it, and
  `iam.serviceAccounts.actAs` for whoever creates the subscription. That's three grants and one more account for no
  gain here.
- **Phase 8:** Looker Studio runs on your Owner access. The review should replace that with a dedicated reader:
  `roles/bigquery.dataViewer` on the dataset (or an authorized view) plus `roles/bigquery.jobUser`.
- **Looker Studio credentials, a privacy warning.** With "Owner's credentials" (the default), **everyone you share the
  report with sees the data through your access**. Until Phase 8, don't share the report beyond yourself.

### 3.10 Privacy

Every field in the event, checked against the [Student model](../app/api/app/models.py):

| Field | Personal info? |
|---|---|
| `event_id`, `event_type`, `schema_version` | No. Pipeline bookkeeping |
| `answered_at` | No (a time) |
| `answer_id`, `classroom_id`, `ticket_id`, `question_id` | No. Random UUIDs of non-person records |
| `student_id` | **Pseudonymous.** A random UUID with no name in it, but anyone who can also read Cloud SQL can join it to `students.display_name`. So treat the dataset as student data: restricted access, no public sharing |
| `standard_code`, `standard_domain`, `subject` | No. Curriculum tags |
| `chosen_choice`, `is_correct` | Not identifying. They're the student's performance, which is exactly why `student_id` matters |

**Not in the event, on purpose:**

- `students.display_name`: a name (synthetic in this project, but the design must hold for real data).
- `students.login_code`: a **sign-in secret**. Leaking it would let someone sign in as the student. It must never leave
  Cloud SQL.

**What keeps them out, tested.** The schema alone does **not**. §6 test 4 showed that a JSON message with an extra
`login_code` key is still "valid" to Pub/Sub. The schema checks that every *declared* field is present and well typed,
but it ignores keys it doesn't know.

The extra key wouldn't reach BigQuery, because with `use_topic_schema` only schema fields are written. It would still
sit in Pub/Sub for up to 7 days, though, and in any DLQ copy. So the guarantee has to live in the app (6C):

1. **The message is built only from `AnswerEvent`.** The pydantic model gets `extra="forbid"`, and `publish_answer_event()`
   serializes `event.model_dump(mode="json")` and nothing else. There's no way to add a stray dict key.
2. **A contract test** reads `answer_event.avsc` and asserts that `AnswerEvent`'s field names equal the schema's field
   names, exactly. Adding `display_name` or `login_code` to the model then fails CI unless someone also adds it to the
   schema, which is a deliberate, reviewable change in a file whose `doc` says what belongs there.

The schema is still the best type check (it caught test 3). It just isn't a field filter.

**Where the data lives:**

- The dataset is in us-east4 (§3.6).
- 6B can also pin the topic's **message storage** to us-east4 with
  `message_storage_policy { allowed_persistence_regions = ["us-east4"] }`. Pub/Sub is otherwise global.

As always, the project uses synthetic students only (CLAUDE.md rule 5).

---

## 4. The event contract

The contract comes from the real call site. `submit_exit_ticket` builds one event per answer row after grading, from
values captured before the commit (ORM objects expire after commit). Version 1 (today, log-only) has 10 fields.
**Version 2** adds the fields this design needs:

| # | Field | Avro type | BigQuery | Mode | Meaning | Source in 6C | Example (synthetic) |
|---|---|---|---|---|---|---|---|
| 1 | `event_id` | string | STRING | REQUIRED | Unique ID of this event; the dedup key | **new**: `uuid.uuid4()` when the event is built | `3e2d2ad7-2677-46f8-be62-a605c3a7046b` |
| 2 | `event_type` | string | STRING | REQUIRED | What happened | **new**: constant | `answer_submitted` |
| 3 | `schema_version` | int | INT64 | REQUIRED | Contract version | changed: `1` → `2` | `2` |
| 4 | `answered_at` | long, timestamp-micros | TIMESTAMP | REQUIRED | When the answer was graded (UTC). Partition column | `row.answered_at` | `1795010730123456` (= `2026-11-18 14:05:30.123456 UTC`) |
| 5 | `answer_id` | string | STRING | REQUIRED | The `answers` row | `row.id` | `b4b4e164-3866-4de5-a365-a44d2a8360ee` |
| 6 | `student_id` | string | STRING | REQUIRED | Internal student ID (pseudonymous) | `row.student_id` | `49eab8ac-be38-4554-97ab-c48f6d5dd371` |
| 7 | `classroom_id` | string | STRING | REQUIRED | The student's classroom. Cluster column | `student.classroom_id` | `0f3eca60-0172-41a5-8641-037e16698014` |
| 8 | `ticket_id` | string | STRING | REQUIRED | The exit ticket | `row.ticket_id` | `59ce99e2-6827-43fe-bd8a-df0bc78198bb` |
| 9 | `question_id` | string | STRING | REQUIRED | The question | `row.question_id` | `caed3c50-7512-4255-a948-b84a67755685` |
| 10 | `standard_code` | string | STRING | REQUIRED | NY standard of the question. Cluster column | `row.standard_code` | `NY-5.NF.4` |
| 11 | `standard_domain` | string | STRING | REQUIRED | The standard's domain, for "fractions overall" charts | **new**: `standards.domain` | `NY-5.NF` |
| 12 | `subject` | string | STRING | REQUIRED | `math` or `science` | **new**: `standards.subject` | `math` |
| 13 | `chosen_choice` | string | STRING | REQUIRED | Picked choice, for "most common wrong answer" | `row.chosen_choice` | `C` |
| 14 | `is_correct` | boolean | BOOL | REQUIRED | Graded on the server | `row.is_correct` | `true` |

Notes on the contract:

- **Why bump to version 2.** The docstring in [events.py](../app/api/app/events.py) says to bump `schema_version`
  whenever fields change. Version 1 events exist only in Cloud Logging, and nothing consumes them. Bumping anyway keeps
  the rule honest, and two different shapes never share one version number.
- **Where `subject` and `standard_domain` come from.** They live on `standards`, not `questions`. In 6C,
  `get_ticket_questions()` joins `Standard` so the values come from the same query: no extra round trip, and no
  guessing from the code's prefix. Putting them in the event saves every dashboard query a join. BigQuery has no
  `standards` table.
- **There's no `submission_id`.** The app doesn't have one: each answer is its own row, and the unique rule
  `(student_id, ticket_id, question_id)` plus "no retakes yet" means **`(student_id, ticket_id)` identifies a
  submission**. If retakes arrive, a real `submission_id` column (in Cloud SQL *and* the event, as v3) is the fix, so
  we don't invent an ID that exists only in analytics.
- **Field order** in the Avro file matches this table. With JSON encoding the key order in a message doesn't matter.

The same event as the JSON message the API will publish (one line on the wire; spread out here):

```json
{
  "event_id": "3e2d2ad7-2677-46f8-be62-a605c3a7046b",
  "event_type": "answer_submitted",
  "schema_version": 2,
  "answered_at": 1795010730123456,
  "answer_id": "b4b4e164-3866-4de5-a365-a44d2a8360ee",
  "student_id": "49eab8ac-be38-4554-97ab-c48f6d5dd371",
  "classroom_id": "0f3eca60-0172-41a5-8641-037e16698014",
  "ticket_id": "59ce99e2-6827-43fe-bd8a-df0bc78198bb",
  "question_id": "caed3c50-7512-4255-a948-b84a67755685",
  "standard_code": "NY-5.NF.4",
  "standard_domain": "NY-5.NF",
  "subject": "math",
  "chosen_choice": "C",
  "is_correct": true
}
```

---

## 5. The table and the view

**Table `exit_ticket_analytics.answer_events`.** Terraform creates it in 6B. The docs list "a BigQuery table exists"
under Before you begin, so it comes before the subscription.

| Column | Type | Mode | From |
|---|---|---|---|
| the 14 contract fields in §4 | as in §4 | REQUIRED | Avro body (`use_topic_schema`) |
| `subscription_name` | STRING | NULLABLE | `write_metadata` |
| `message_id` | STRING | NULLABLE | `write_metadata` |
| `publish_time` | TIMESTAMP | NULLABLE | `write_metadata` |
| `attributes` | JSON | NULLABLE | `write_metadata` |

**Why these modes:**

- **REQUIRED for the contract columns.** Our Avro fields are all required, and required fields "don't need to be
  required" in BigQuery. Making them REQUIRED is allowed and is the stricter choice: a row can never be half-empty.
  Pub/Sub checks compatibility when the subscription is created, so if this were wrong, 6B's apply would say so.
- **NULLABLE for the metadata columns.** That's what the rule for "columns not in the topic schema" demands.

**View `exit_ticket_analytics.answer_events_clean`**: one row per `event_id`, the earliest copy wins:

```sql
SELECT * EXCEPT (copy_number)
FROM (
  SELECT
    *,
    ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY publish_time) AS copy_number
  FROM `PROJECT_ID.exit_ticket_analytics.answer_events`
)
WHERE copy_number = 1
```

How this works:

- `ROW_NUMBER() OVER (PARTITION BY event_id ...)` numbers the copies of each event 1, 2, 3…
- Keeping `copy_number = 1` keeps exactly one.
- `EXCEPT (copy_number)` hides the helper column from Looker Studio.

**Trade-off.** A date filter on the view may not prune partitions, because BigQuery has to number copies across the
whole table first. At our size the whole table is kilobytes, so a full scan costs nothing. If it ever grows, the
upgrade is a scheduled query that writes a deduplicated table.

---

## 6. Files in this PR

| File | What |
|---|---|
| `docs/phase-6a-design.md` | This doc |
| [infra/terraform/analytics/schemas/answer_event.avsc](../infra/terraform/analytics/schemas/answer_event.avsc) | The Avro schema (contract v2). 6B passes it to `google_pubsub_schema` with `definition = file("schemas/answer_event.avsc")`. Not applied anywhere yet |

You can check the schema and the example message without creating anything. Pub/Sub has validate-only API calls
that store nothing:

```bash
gcloud pubsub schemas validate-schema --type=avro --definition-file=infra/terraform/analytics/schemas/answer_event.avsc
gcloud pubsub schemas validate-message --type=avro --message-encoding=json --definition-file=infra/terraform/analytics/schemas/answer_event.avsc --message='{"event_id":"3e2d2ad7-2677-46f8-be62-a605c3a7046b","event_type":"answer_submitted","schema_version":2,"answered_at":1795010730123456,"answer_id":"b4b4e164-3866-4de5-a365-a44d2a8360ee","student_id":"49eab8ac-be38-4554-97ab-c48f6d5dd371","classroom_id":"0f3eca60-0172-41a5-8641-037e16698014","ticket_id":"59ce99e2-6827-43fe-bd8a-df0bc78198bb","question_id":"caed3c50-7512-4255-a948-b84a67755685","standard_code":"NY-5.NF.4","standard_domain":"NY-5.NF","subject":"math","chosen_choice":"C","is_correct":true}'
gcloud pubsub schemas validate-message --type=avro --message-encoding=json --definition-file=infra/terraform/analytics/schemas/answer_event.avsc --message='{"event_id":"3e2d2ad7-2677-46f8-be62-a605c3a7046b","is_correct":"yes"}'
gcloud pubsub schemas validate-message --type=avro --message-encoding=json --definition-file=infra/terraform/analytics/schemas/answer_event.avsc --message='{"event_id":"3e2d2ad7-2677-46f8-be62-a605c3a7046b","event_type":"answer_submitted","schema_version":2,"answered_at":1795010730123456,"answer_id":"b4b4e164-3866-4de5-a365-a44d2a8360ee","student_id":"49eab8ac-be38-4554-97ab-c48f6d5dd371","classroom_id":"0f3eca60-0172-41a5-8641-037e16698014","ticket_id":"59ce99e2-6827-43fe-bd8a-df0bc78198bb","question_id":"caed3c50-7512-4255-a948-b84a67755685","standard_code":"NY-5.NF.4","standard_domain":"NY-5.NF","subject":"math","chosen_choice":"C","is_correct":true,"login_code":"ABC234"}'
```

What each command should do:

| # | Command | Expect | Proves |
|---|---|---|---|
| 1 | `validate-schema` | Success | The `.avsc` is valid Avro to Pub/Sub |
| 2 | Full message | Success | The contract, including `answered_at` as integer microseconds |
| 3 | Missing fields, `"yes"` for a boolean | **Failure** | Path 1 of §3.5 |
| 4 | Full message plus `login_code` | Open question when written | Whether the schema filters unknown keys |

**Results (run 2026-11-18):**

| # | Result |
|---|---|
| 1 | `Schema is valid.` |
| 2 | `Message is valid.` This confirms `timestamp-micros` as a JSON integer |
| 3 | `INVALID_ARGUMENT: Invalid data in message: Field was not found in JSON object: event_type.` with reason `INVALID_JSON_AVRO_MESSAGE`. It reports the first problem it finds, not all of them |
| 4 | **`Message is valid.`** Unknown keys are ignored, so the schema does not keep `login_code` out. §3.10 now puts that guarantee in the app |

---

## 7. To verify in 6B–6E

The design rests on the docs, and a few details are confirmed only by running them. Each has an owner step:

| Claim | Status from the docs | Check | When |
|---|---|---|---|
| REQUIRED BigQuery columns accept required Avro fields | Implied ("required fields … don't need to be required"), not stated directly | Subscription creation succeeds; Pub/Sub checks compatibility at create | 6B apply |
| A day-partitioned, clustered table works with a BigQuery subscription | Not stated (only ingestion-time partitioning is mentioned) | Same | 6B apply |
| `attributes` as `JSON` | Allowed ("STRING or JSON") | First row lands; `JSON_VALUE` reads the revision ID | 6D |
| The Pub/Sub service agent exists before the grants | Not stated | `google_project_service_identity` in 6B, or check the project IAM policy | 6B plan |
| ~~Unknown keys (e.g. `login_code`) are rejected at publish~~ | **Tested: false**, they're accepted (§6 test 4) | Replaced by the contract test in §3.10 | 6C |
| Unknown keys are dropped, not written, by the BigQuery subscription | Implied by the drop-unknown-fields text | Publish one test message with an extra key; it must not appear anywhere in the table | 6D |
| Path 2: a per-message rejection reaches the DLQ after 5 attempts | Stated | Drill: see §9 | 6D |
| Path 3: deleting the service agent's table grant puts the subscription in error, messages stay in backlog, restoring it drains them | Stated | Drill: remove the grant with Terraform, publish, check state and backlog, restore | 6D |
| Looker Studio works on the view with only the enabled APIs | Not applicable | Report loads | 6E |

---

## 8. Cost

Nothing in this pipeline bills by the hour. Everything is usage-based, and at our volume (kilobytes) the usage is
effectively zero:

| Item | Charge model | Our use |
|---|---|---|
| Pub/Sub topics and subscriptions | By data volume (publish, delivery, BigQuery subscription writes), with a free tier | Kilobytes |
| Topic schema | No separate charge mentioned in the schema docs | — |
| BigQuery storage | Per GiB-month, with a free tier | Kilobytes |
| BigQuery queries | Per TiB scanned (on-demand), with a monthly free tier | Kilobytes per report load |
| Looker Studio | Free | — |

The exact rates and free-tier sizes are on the [Pub/Sub pricing](https://cloud.google.com/pubsub/pricing) and
[BigQuery pricing](https://cloud.google.com/bigquery/pricing) pages. The Pub/Sub page didn't render for this review,
so they aren't quoted here. Check them before 6B.

The analytics layer can stay up between sessions, unlike the cluster. The teardown policy still applies to the cluster
and Cloud SQL. The session-start check gains one line: the BigQuery subscription's state.

---

## 9. Known gaps

- **Lost events between commit and publish** (§3.7). Fix: transactional outbox. Recovery today: a backfill script
  that republishes from Cloud SQL `answers`, which the view's dedup makes safe to run twice.
- **Error-state backlog expires after 7 days** (§3.5, path 3). If the subscription breaks and nobody notices for a week,
  messages expire. Mitigations: the session-start state check now, an alert on subscription state or backlog age in
  Phase 7/8, and the Cloud SQL backfill.
- **Schema evolution needs an order.** Adding a field, safely:
  1. Add the column to the table as NULLABLE (allowed: required schema fields may map to nullable columns).
  2. Commit a new schema **revision**. A schema allows at most 20.
  3. Update the topic's revision range (`first_revision_id` / `last_revision_id`), so both old and new messages
     validate.
  4. Ship the app.

  Also:
  - **Don't delete old revisions.** Messages that validated against a deleted revision "cannot be written to
    BigQuery". The docs suggest rolling back instead.
  - **Terraform behaviour.** `google_pubsub_schema` changes in place versus replacing the schema; 6B must check which
    one Terraform does. This is probably the first real lesson of 6B.
- **The DLQ drill needs a per-message failure.** Because the schema and table match, path 2 is hard to trigger on
  purpose. A clean drill for 6D: temporarily give one column a max length (e.g. `STRING(1)` on `chosen_choice`) and
  publish a valid-Avro message with `"chosen_choice":"AB"`. Whether Pub/Sub accepts a parameterized column at
  subscription update is itself part of the drill.
- **The view may not prune partitions** (§5). Fine until the table is large.
- **Looker Studio runs on your credentials** (§3.9). Phase 8: a dedicated reader. Don't share the report until then.
- **No `submission_id`** (§4). Becomes a real column if retakes are added.

---

## 10. Interview talking points

- *"Why a BigQuery subscription instead of Dataflow?"* No transformation was needed that a view can't do, so the
  managed path has nothing to run and nothing to operate. The trade-off is at-least-once delivery, handled by dedup in a
  view. Dataflow is for windowing, joins and exactly-once in flight.
- *"How do you handle duplicates in an at-least-once system?"* Make the consumer idempotent: an `event_id` made at
  the source, dedup in the view. `message_id` alone misses publisher-side retries.
- *"What does a dead-letter topic NOT catch?"* Subscription-level failures. A deleted table or a lost IAM grant puts a
  BigQuery subscription in an error state and messages back up in the backlog; they're never dead-lettered. You need
  state or backlog monitoring as well as a DLQ.
- *"What happens if the analytics pipeline is down?"* Students aren't affected: publish is after commit and
  non-blocking, and failures are logged. Messages wait in the subscription. Cloud SQL stays the source of truth, so a
  backfill can repair gaps.
- *"Where can bad data be stopped?"* At three gates: the topic schema (at publish), the table schema with REQUIRED
  columns (compatibility checked at subscription creation), and the view (duplicates).
- *"Is a schema a privacy control?"* Only partly. I tested it: a JSON message with an extra `login_code` key still
  validated. Schemas check declared fields, but they don't filter extras. The real control is a closed event model in
  the app plus a test that keeps the model and the schema identical.
- *"Least privilege?"* Publisher on one topic for the app. The Google-managed service agent gets Data Editor on one
  table plus the two dead-letter grants. Nothing is project-wide.

---

## ACE exam notes

- **Subscription types:** pull, push, BigQuery, Cloud Storage. Know which needs code and which doesn't.
- **Pub/Sub is at-least-once by default.** Consumers must be idempotent. BigQuery subscriptions are at-least-once
  only; the Dataflow template is exactly-once.
- **Dead-letter topics:** `max_delivery_attempts` is 5–100 (default 5). The Pub/Sub **service agent** needs publisher on
  the dead-letter topic and subscriber on the source subscription. Give the dead-letter topic a subscription, or its
  messages are lost.
- **Unacked messages are kept for the subscription's retention** (7 days by default), then deleted.
- **Service agents** (`service-PROJECT_NUMBER@gcp-sa-*.iam.gserviceaccount.com`) are Google-managed accounts that act
  for a service in your project. You grant them roles, but you don't create them or hold keys for them.
- **BigQuery cost levers:** partitioning (prune by date), clustering (skip blocks), `SELECT` only needed columns,
  `--dry_run` to see bytes before running.
- **Dataset location is permanent.** Pick it to match where the data comes from.
- **Private Google Access** lets VMs without external IPs reach Google APIs. It's configured per **subnet**.

---

## Sources

Google Cloud docs, read in full for this review (2026-11-18). The `cloud.google.com/pubsub/docs/...` URLs redirect to
`docs.cloud.google.com`:

- [BigQuery subscriptions](https://cloud.google.com/pubsub/docs/bigquery): schema compatibility tables (Avro types,
  logical types, unions), at-least-once delivery, handling message failures and backoff
- [Create BigQuery subscriptions](https://cloud.google.com/pubsub/docs/create-bigquery-subscription): use topic schema
  and the mode rules, use table schema, write metadata columns and the clash rule, drop unknown fields, Data Editor for
  the service agent, custom service account requirements, "a BigQuery table exists"
- [Troubleshoot a BigQuery subscription](https://cloud.google.com/pubsub/docs/bigquery-troubleshooting): error states
  (table not found, permission denied, schema mismatch) keep messages in the backlog and skip the DLQ; compatibility
  checked at create/update; required permissions `bigquery.tables.get` / `bigquery.tables.updateData`
- [Dead-letter topics](https://cloud.google.com/pubsub/docs/handling-failures): 5–100 attempts, default 5; service agent
  roles and when to grant them
- [Publish messages to a topic with a schema](https://cloud.google.com/pubsub/docs/publish-topics-schema):
  `INVALID_ARGUMENT` on validation failure; `googclient_schema*` attributes
- [Associate a schema with a topic](https://cloud.google.com/pubsub/docs/associate-schema-topic): revision ranges and
  validation order; the warning about Avro unions in JSON
- [Schemas overview](https://cloud.google.com/pubsub/docs/schemas): revisions (max 20 per schema), deletion is permanent
- [Avro specification, JSON encoding](https://avro.apache.org/docs/1.11.1/specification/#json-encoding): union
  wrapping, `long` as a JSON number (not fetched; confirmed in practice by §6)
- [Pub/Sub pricing](https://cloud.google.com/pubsub/pricing) and [BigQuery pricing](https://cloud.google.com/bigquery/pricing):
  linked, not quoted (the Pub/Sub page didn't render)
