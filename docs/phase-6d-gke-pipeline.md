# Phase 6D — The pipeline on GKE: deploy, prove, break, backfill

Phase 6D runs the [6C](phase-6c-publisher.md) publisher on GKE, where it publishes as **`exit-ticket-api`** through
Workload Identity. It proves the whole path, from a browser submission to a BigQuery row, then breaks it four ways on
purpose. It also adds a month of **synthetic** history so the Looker Studio report (6E) has something to show.
Everything was torn down at the end, except the long-lived analytics layer.

| Part | What | PR |
|---|---|---|
| 6A | Design, contract, Avro schema | #28 |
| 6B | `analytics` Terraform layer | #29 |
| 6C | Publisher in the API | #30 |
| **6D** | New image on GKE; `PUBSUB_TOPIC`; end to end; four drills; synthetic backfill | (this PR) |
| 6E | Looker Studio | |

> **About the commands in this doc.** Code blocks contain commands only, no `#` comments: in zsh, `#` is not a
> comment unless `setopt interactivecomments` is on. No project ID or project number is committed. Manifests use the
> `PROJECT_ID` placeholder that `sed` fills in at apply time.

**Contents**

1. [Concepts](#1-concepts)
2. [The changes](#2-the-changes)
3. [Session and deploy](#3-session-and-deploy)
4. [End to end on GKE](#4-end-to-end-on-gke)
5. [Drills](#5-drills)
6. [Synthetic backfill](#6-synthetic-backfill)
7. [Teardown and verification](#7-teardown-and-verification)
8. [Open items](#8-open-items)
9. [Lessons learned](#9-lessons-learned)
10. [Interview talking points](#10-interview-talking-points)
11. [ACE exam notes](#ace-exam-notes)

---

## 1. Concepts

### Why no new IAM, network or NAT

| Needed | Already there | Since |
|---|---|---|
| An identity for the pod | KSA `api` → GSA `exit-ticket-api`, through Workload Identity | 4B/4C |
| Permission to publish | `roles/pubsub.publisher` on `answer-events` only | 6B |
| A route to `pubsub.googleapis.com` from private nodes | **Private Google Access** on the subnet. No NAT, no public IP | 5C |

The code has no credentials in it (ADC, [6C §1](phase-6c-publisher.md#1-concepts)). So deploying meant only a new
image plus one environment variable.

### ConfigMap values are read at container start

`PUBSUB_TOPIC` comes from the ConfigMap as an **environment variable**. Environment variables are fixed when a
container starts. Changing the ConfigMap changes nothing in a running pod until `kubectl rollout restart` replaces it.
Drill 5.1 relies on this.

### Rolling updates keep the old pod serving

During `rollout restart`, the old pod keeps serving traffic until the new one is **Ready**. That's the point of a
rolling update: no downtime. It also means a request sent mid-rollout can reach the **old** version (§5.1 shows it
happening).

---

## 2. The changes

| File | Change |
|---|---|
| [k8s/api.yaml](../k8s/api.yaml) | api image `@sha256:451e6ff5…` (tag `3f77bc1`, the 6C merge); `PUBSUB_TOPIC` env from the ConfigMap |
| [k8s/migrate-job.yaml](../k8s/migrate-job.yaml) | Same api digest, so migrations and the app run one build |
| [k8s/configmap.yaml](../k8s/configmap.yaml) | `PUBSUB_TOPIC: projects/PROJECT_ID/topics/answer-events` |
| [k8s/web.yaml](../k8s/web.yaml) | **Unchanged**: `app/web/` was last changed on 2026-10-22, before its current digest (2026-10-27) |
| [infra/terraform/analytics/drill.tf](../infra/terraform/analytics/drill.tf) | The dead-letter drill (§5.3). **Off by default**: `-var="dlq_drill=true"` creates it, a plain apply removes it |
| [scripts/backfill-answer-events.py](../scripts/backfill-answer-events.py) | Synthetic backfill (§6). Dry run by default |

`scripts/push-images.sh` always builds both images under the new commit tag. So a web image `web:3f77bc1` was pushed
too, but not deployed: harmless, and the repository's cleanup policy limits how many are kept.

---

## 3. Session and deploy

The tab plan:

| Tab | Job |
|---|---|
| Tab 1 | Cloud SQL `apply -var="activation_policy=ALWAYS"`, then the port-forward |
| Tab 2 | Cluster `apply` → `get-credentials` → deploy → teardown |
| Tab 3 | `push-images.sh` (in parallel with the infra), then `bq`, `gcloud pubsub`, Terraform drills, backfill |

The images were built while Cloud SQL and the cluster were being created.

```bash
scripts/push-images.sh
```

| Image | Result |
|---|---|
| api | Built for `linux/amd64` in 17 s. The `pip install` layer (10.9 s) is new because of `google-cloud-pubsub`; everything else came from the cache |
| web | Every layer cached |

The deploy order was the runbook's ([phase-5-terraform.md §4](phase-5-terraform.md#4-starting-a-session)), checked
at the end:

```bash
kubectl -n exit-ticket get deployment api -o jsonpath='{.spec.template.spec.containers[0].image}'
kubectl -n exit-ticket logs deployment/api -c api | grep -E "Publishing answer events"
```

| Check | Result |
|---|---|
| Pods | `api 2/2`, `web 1/1`, `migrate 0/2 Completed`, no restarts |
| Image | `…/api@sha256:451e6ff57875…` |
| Publisher | `Publishing answer events to projects/PROJECT_ID/topics/answer-events`, then `Application startup complete.` |

---

## 4. End to end on GKE

A student answered a 3-question NY-5.NF.4 ticket through `kubectl port-forward svc/web 8080:80`. The same answers
were then read from three places.

```bash
kubectl -n exit-ticket logs deployment/api -c api | grep -E "answer_event_publish"
kubectl -n exit-ticket exec deployment/api -c api -- python -c '
from sqlalchemy import text
from app.db import get_engine
with get_engine().connect() as c:
    rows = c.execute(text("SELECT id, ticket_id, standard_code, chosen_choice, is_correct, answered_at FROM answers WHERE answered_at > now() - make_interval(mins => 20) ORDER BY id")).all()
for r in rows:
    print(r.id, r.standard_code, r.chosen_choice, r.is_correct, r.answered_at.isoformat())
'
bq --project_id="$PROJECT" --dataset_id=exit_ticket_analytics query --use_legacy_sql=false 'SELECT answer_id, standard_code, chosen_choice, is_correct, answered_at, message_id, TIMESTAMP_DIFF(publish_time, answered_at, MILLISECOND) AS latency_ms FROM answer_events WHERE event_type = "answer_submitted" AND JSON_VALUE(attributes, "$.source") IS NULL AND answered_at > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 20 MINUTE) ORDER BY answer_id'
```

How the Postgres check works:

- **`kubectl exec` uses the app's own database path.** It runs Python inside the `api` container, through the Cloud
  SQL proxy sidecar, logged in as `exit-ticket-api`. No password and no tunnel from the Mac.
- **`make_interval(mins => 20)` instead of `interval '20 minutes'`.** The Python sits inside single quotes in zsh, and
  an inner `'` would end it.

| `answer_id` (Postgres = BigQuery) | Choice, correct | `message_id` (log = BigQuery) |
|---|---|---|
| `04bc4c18…` | A, true | `…4777` |
| `6adb0db1…` | A, false | `…4776` |
| `8d387251…` | C, true | `…4778` |

**Result:** 3 = 3 = 3. Identical answer IDs, message IDs, values and time (02:19:14.432204). This is the first proof
that **`exit-ticket-api` can publish**, which the local Owner run in 6C couldn't show.

**`latency_ms = 667`, higher than the local 380.** This was the **first** publish from a new pod, so it paid one-time
costs:

- **The connection:** opening the gRPC connection to `pubsub.googleapis.com`.
- **The token:** the Workload Identity exchange (the pod's Kubernetes identity → a short-lived `exit-ticket-api` access
  token). Locally, the ADC token was already on disk.

Later publishes reuse both.

---

## 5. Drills

### 5.1 The publisher can't publish (missing topic)

**Break:** point the ConfigMap at a topic that doesn't exist, without editing any file:

```bash
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/configmap.yaml | sed "s#topics/answer-events#topics/answer-events-missing#" | kubectl apply -f -
kubectl -n exit-ticket rollout restart deployment/api
kubectl -n exit-ticket rollout status deployment/api --timeout=300s
```

**The first attempt tested nothing,** and it's the most useful lesson of the session:

| Time (UTC) | Event |
|---|---|
| 02:21:38.65 | Submission saved |
| 02:21:50.09 | The **new** pod (missing topic) logs its startup |

The request went to the **old** pod during the rollout, which still had the good topic, so it published normally. Its
logs were gone, too: `kubectl logs deployment/api` reads **one** pod, the new one. That's the rolling update doing its
job (§1).

**Second attempt** (after the rollout, one pod running):

| Expected | Seen |
|---|---|
| Submission succeeds | `POST …/submissions 201` at 02:23:34.60 |
| Answers saved | 3 rows in Postgres (`4472c513…`, `606d2505…`, `9b5868fc…`) |
| Failure logged per answer | 3 × `answer_event_publish_failed`, `error_type: NotFound`, `404 Resource not found (resource=answer-events-missing).` |
| Fast | All 3 failures 0.49 s after the response: NotFound isn't retried |
| No student data in the log | Only `event_id`, `error_type`, `error` |
| Nothing in BigQuery | `SELECT COUNT(*) … WHERE answer_id IN (…)` = `0` |

**Restore:** a plain ConfigMap apply, `rollout restart`, and wait until there's one pod. Startup then named
`…/topics/answer-events` again.

**Those 3 answers are a real, permanent gap.** They're in Cloud SQL and never in BigQuery, because nothing retries
them. That's the case the Cloud-SQL-is-the-truth backfill exists for (6C §4). It's recorded here rather than repaired.

### 5.2 The subscription breaks (error state, drift)

**Break:** remove the service agent's write grant on the table **by hand**, outside Terraform:

```bash
bq --project_id="$PROJECT" remove-iam-policy-binding --member="serviceAccount:service-${NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com" --role=roles/bigquery.dataEditor exit_ticket_analytics.answer_events
```

**Why not through Terraform:** a `terraform destroy -target` of the grant would also destroy everything that depends on
it, including the subscription (its `depends_on`). Removing it by hand and letting Terraform restore it is the safe
version, and it doubles as a **drift** drill.

The block waited 3 minutes for IAM to take effect, published one message marked `event_type = "error_state_drill"`,
waited 90 s, then checked:

| Expected (6A §3.5 path 3) | Seen |
|---|---|
| Publish still accepted | `messageIds: 22637192818951005` |
| Subscription in error | **`RESOURCE_ERROR`** |
| Not written | `drill_rows = 0` |
| **Not** dead-lettered | DLQ: `Listed 0 items.` |
| Terraform sees the drift | `google_bigquery_table_iam_member.pubsub_agent_writer will be created`, `1 to add` |

**Restore:** `terraform apply` (1 added, the grant only), then a 2-minute wait:

| Check | Result |
|---|---|
| State | Back to **`ACTIVE`** on its own, with no restart |
| The held message | Landed: **the same `message_id` `22637192818951005`**, 301 s after publish |
| DLQ | Still empty |
| Re-plan | exit 0 |

### 5.3 A message BigQuery rejects (dead-letter)

**Why breaking the real table can't reach the DLQ:** in 5.2 the whole subscription broke, so messages were held in the
backlog. A message is dead-lettered only when BigQuery rejects **that one message** while the subscription stays
healthy. With matching schemas that can't happen on the real table. So [drill.tf](../infra/terraform/analytics/drill.tf)
adds a temporary setup:

| Resource | Notes |
|---|---|
| `answer_events_dlq_drill` table | The real columns, but `chosen_choice` is `STRING(1)` |
| Its `dataEditor` grant | For the service agent |
| `answer-events-dlq-drill` subscription | **`filter = attributes.drill = "dlq"`**, so real answers never reach it. Same DLQ, `max_delivery_attempts = 5`, backoff 10–60 s |
| A subscriber grant on it | Lets the service agent ack forwarded messages |

```bash
terraform -chdir=infra/terraform/analytics apply -var="dlq_drill=true"
gcloud pubsub topics publish answer-events --attribute=drill=dlq --message='{… "event_type":"dlq_drill", … "chosen_choice":"AB", …}'
sleep 240
gcloud pubsub subscriptions pull answer-events-dlq-inspect --limit=5 --format="yaml(message.messageId,message.attributes)"
```

| Expected | Seen |
|---|---|
| Pub/Sub accepts `STRING(1)` for an Avro `string` at create time | Yes: 4 added (a 6A open question, now answered) |
| Topic accepts "AB" (valid Avro) | `messageIds: 22636716494682962` |
| Drill subscription stays healthy | **`ACTIVE`** |
| Dead-lettered after 5 tries | `CloudPubSubDeadLetterSourceDeliveryCount: '5'` |
| The reason | `CloudPubSubDeadLetterSourceDeliveryErrorMessage: 'Field chosen_choice: STRING(1) has maximum length 1 but got a value with length 2 on field chosen_choice.'` |
| The source | `…SourceSubscription: answer-events-dlq-drill`, `…SourceTopicPublishTime`; original attributes kept (`drill: dlq`, `googclient_schemarevisionid: cb8202da`) |
| Copies | Real table 1 (no length limit there), drill table 0 |

**The DLQ copy has a new `messageId`** (`…4433035` vs the original `…4682962`). Dead-lettering publishes a new
message. The link back is the source attributes and the unchanged `event_id` in the body.

**Restore:** a plain `terraform apply` (`0 to add, 0 to change, 4 to destroy`, all `dlq_drill`), then the DLQ message
acked with `pull --auto-ack`. Afterwards: 2 subscriptions left, DLQ empty, re-plan exit 0.

**The two failure paths side by side:**

| | 5.2 subscription broken | 5.3 one bad message |
|---|---|---|
| Subscription state | `RESOURCE_ERROR` | `ACTIVE` |
| The message | Held in the backlog (≤ 7 days), then written once fixed | Moved to the DLQ after 5 attempts |
| What detects it | State or backlog monitoring | Reading the DLQ |
| Fix | Repair the cause; it drains by itself | Inspect, fix the data or the schema, republish if needed |

### 5.4 Duplicate delivery

The body of real event `f5a43102…` (answer `04bc4c18…`) was rebuilt from BigQuery and published again, the way a
publisher retry would send it:

```bash
MSG="$(bq … --format=json 'SELECT TO_JSON_STRING(STRUCT(event_id, event_type, schema_version, UNIX_MICROS(answered_at) AS answered_at, answer_id, student_id, classroom_id, ticket_id, question_id, standard_code, standard_domain, subject, chosen_choice, is_correct)) AS msg FROM answer_events WHERE answer_id = "04bc4c18-…" LIMIT 1' | python3.13 -c 'import json, sys; print(json.load(sys.stdin)[0]["msg"])')"
gcloud pubsub topics publish answer-events --attribute=drill=duplicate --message="$MSG"
```

`TO_JSON_STRING(STRUCT(…))` gives the fields in schema order, and `UNIX_MICROS` turns the timestamp back into Avro's
integer microseconds. The rebuilt message matched the original to the microsecond (`1795054754432204`).

| Where | Rows for `event_id f5a43102…` |
|---|---|
| Raw `answer_events` | **2**: `message_id …4777` (02:19:15, original) and `…6045` (02:46:30, `drill = duplicate`) |
| View `answer_events_clean` | **1**: `…4777`, the earliest `publish_time` |

This is why the view dedups on **`event_id`**. The duplicate got a **new** `message_id`, so deduplicating by
`message_id` would have kept both.

---

## 6. Synthetic backfill

[scripts/backfill-answer-events.py](../scripts/backfill-answer-events.py) publishes contract-v2 events so the report
has history.

| Property | How |
|---|---|
| Real students, tickets, questions | Built with `AnswerEvent` from `app/events.py` and `stable_id` from `seed.py`: the 24 seeded students and 6 tickets with their **database IDs** |
| Simulated answers | Per-student ability, per-standard difficulty, a little growth each week. **No Cloud SQL rows** |
| Dates | School days over the last 4 weeks, 9:00–15:00 New York time |
| **Marked** | The Pub/Sub attribute **`source=synthetic-backfill`**. It lands in the `attributes` JSON column through `write_metadata`, so **no schema change**. Filter with `JSON_VALUE(attributes, "$.source")` |
| Safe to rerun | Deterministic `uuid5` event IDs: a second run only creates duplicates the view removes |
| Safe by default | Dry run unless `--publish --topic projects/…/topics/answer-events`; every message is checked against the `.avsc` with fastavro first |
| Who publishes | Your user's ADC, from the Mac: an operator tool, not the app |

```bash
app/api/.venv/bin/python scripts/backfill-answer-events.py
app/api/.venv/bin/python scripts/backfill-answer-events.py --publish --topic "projects/$PROJECT/topics/answer-events"
```

| Check | Result |
|---|---|
| Publish | `published 490, failed 0, unconfirmed 0` |
| Landed | 490 rows = 490 events, 24 students, 14 days, 2026-10-21 → 2026-11-13 |
| Through the view, % correct | 5-PS1-1 89.5 · 5-PS1-2 84.2 · 5-PS1-3 94.7 · 5-PS1-4 76.3 · NY-5.NBT.3 65.8 · NY-5.NBT.4 78.9 · NY-5.NBT.7 81.5 · NY-5.NF.1 71.6 · NY-5.NF.4 58.7: identical to the dry run |

---

## 7. Teardown and verification

The runbook's §5 sequence:

1. Ctrl+C the port-forward.
2. `kubectl delete namespace exit-ticket`.
3. `terraform destroy` in `cluster/`: `1 to destroy`, 4m53s.
4. A plain `terraform apply` in `data/`: only `ALWAYS -> NEVER`, 51 s.

No Ingress was used.

The verify block (runbook §6, plus the analytics checks):

| Check | Result |
|---|---|
| Clusters, forwarding rules, backend services, NEGs, disks | None |
| Addresses | Only `exit-ticket-psa` |
| Cloud SQL | `STOPPED NEVER True` |
| Firewall | Only the four `default-allow-*` |
| BigQuery subscription | `ACTIVE` |
| Topics / subscriptions | `answer-events`, `answer-events-dlq` / `answer-events-to-bq`, `answer-events-dlq-inspect` (long-lived, by design) |
| DLQ | `Listed 0 items.` (the drill message was acked) |
| Cluster state | `0` |
| Drift | bootstrap 0, foundation 0, data 0, **cluster 2** (destroyed by design), analytics 0 |
| Processes | None |

---

## 8. Open items

**Test rows in `answer_events`, still to delete.** Three of the four were streamed minutes before the end of the session,
and BigQuery can refuse DML on recently streamed rows. It needs only BigQuery (no cluster, no Cloud SQL), so it can run
any time later:

```bash
bq --project_id="$(gcloud config get project)" --dataset_id=exit_ticket_analytics query --use_legacy_sql=false 'DELETE FROM answer_events WHERE event_type IN ("smoke_test", "error_state_drill", "dlq_drill") OR JSON_VALUE(attributes, "$.drill") = "duplicate"'
```

It should affect 4 rows. A "streaming buffer" error means it's too early: run it again later.

**Other open items:**

- **The 3 answers missing from BigQuery** (§5.1): known, kept as evidence.
- **The 4 answers from the 6C local run, and the 3 + 3 real GKE answers:** valid events, kept.

---

## 9. Lessons learned

| Lesson | Where |
|---|---|
| **Wait for the rollout to finish before testing.** A rolling update keeps the old version serving, so the first missing-topic test hit the old pod | §5.1 |
| **`kubectl logs deployment/X` shows one pod.** The old pod's logs went with it; use `-l app=api --prefix` while both exist, or Cloud Logging afterwards | §5.1 |
| **A DLQ doesn't catch a broken subscription.** A lost grant gave `RESOURCE_ERROR` and a held backlog, never a dead letter | §5.2 |
| **Drift is detectable and repairable.** A by-hand IAM change showed up as `1 to add`, and `apply` fixed it | §5.2 |
| **A backlog is a safety net, with a deadline.** The held message landed with its original ID after the fix; after 7 days it would have expired | §5.2 |
| **To drill a failure, isolate it.** A filtered drill subscription and table, behind a variable that defaults to off, so the real path is never at risk | §5.3 |
| **Dedup on the business key, not the transport key.** The duplicate had a new `message_id`; `event_id` caught it | §5.4 |
| **Mark synthetic data without changing the contract.** A message attribute plus `write_metadata` | §6 |
| **First calls are slow.** 667 ms for the first publish from a cold pod (connection + token exchange) | §4 |

---

## 10. Interview talking points

- *"How did you prove the service account can publish, not just you?"* The local run used my Owner credentials, which
  proves nothing about the app's identity. On GKE the pod publishes as `exit-ticket-api` through Workload Identity, and
  that account holds publisher on one topic. I matched the same 3 answers across the api log, Postgres (read from
  inside the pod) and BigQuery.
- *"What happens if Pub/Sub is misconfigured?"* Drilled it: the student still got 201 and the answers were saved. The
  api logged `NotFound` per answer with only an event ID, and fast, because NotFound isn't retried. The gap is
  recoverable from Cloud SQL.
- *"Does a dead-letter topic cover you?"* Only for bad messages. I showed both paths: a lost table grant puts the
  subscription in `RESOURCE_ERROR` and holds messages in the backlog, and nothing is dead-lettered. A message that's
  too long for a column goes to the DLQ after 5 attempts, with BigQuery's reason in an attribute. So you monitor
  subscription state *and* the DLQ.
- *"How do you handle at-least-once?"* An idempotent consumer: I republished a real event, the raw table had two rows
  with different message IDs, and the view returned one, keyed on `event_id`.
- *"How did you test failure without risking production data?"* Isolated drill resources behind a Terraform variable
  that defaults to off, a subscription filter so real traffic never touches them, and marked test events that are
  deleted afterwards.
- *"Where did the demo data come from?"* A backfill script built on the app's own event model and the seed IDs, marked
  with a Pub/Sub attribute, deterministic so reruns are deduplicated, and dry run by default.

---

## ACE exam notes

- **ConfigMap env vars need a pod restart** (`kubectl rollout restart deployment/X`); mounted ConfigMap *files* update
  in place.
- **Rolling updates:** `maxSurge` / `maxUnavailable`; old pods serve until new ones are Ready.
  `kubectl rollout status` waits for that.
- **`kubectl exec deployment/X -c container -- cmd`** runs inside a pod of the Deployment; `-c` picks the container
  (here `api`, not the proxy sidecar).
- **Pub/Sub subscription filters** (`attributes.KEY = "value"`) are set at creation and can't be changed later.
- **Dead-letter messages** carry `CloudPubSubDeadLetterSourceDeliveryCount`, `…SourceSubscription` and, for BigQuery
  subscriptions, `…SourceDeliveryErrorMessage`.
- **Subscription `state`:** `ACTIVE` or `RESOURCE_ERROR` (for BigQuery and Cloud Storage subscriptions, when the
  destination can't be written).
- **`bq remove-iam-policy-binding` / `add-iam-policy-binding`** manage table IAM; `bq get-iam-policy` shows it.
