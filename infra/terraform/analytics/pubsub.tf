# Pub/Sub: the contract (schema), the main topic, the BigQuery subscription and the dead-letter path.
# See docs/phase-6a-design.md §3.2–§3.5.

resource "google_pubsub_schema" "answer_event" {
  name       = "answer-event"
  type       = "AVRO"
  definition = file("${path.module}/schemas/answer_event.avsc")
}

resource "google_pubsub_topic" "answer_events" {
  name = "answer-events"

  schema_settings {
    schema   = google_pubsub_schema.answer_event.id
    encoding = "JSON" # readable in logs and in the DLQ; no Avro library needed in the app
  }

  # Keep student events stored in the same region as the rest of their data.
  message_storage_policy {
    allowed_persistence_regions = [var.region]
  }
}

resource "google_pubsub_topic" "answer_events_dlq" {
  name = "answer-events-dlq"

  message_storage_policy {
    allowed_persistence_regions = [var.region]
  }
}

resource "google_pubsub_subscription" "answer_events_to_bq" {
  name  = "answer-events-to-bq"
  topic = google_pubsub_topic.answer_events.id

  bigquery_config {
    table               = "${google_bigquery_table.answer_events.project}.${google_bigquery_table.answer_events.dataset_id}.${google_bigquery_table.answer_events.table_id}"
    use_topic_schema    = true  # Avro fields → same-named columns
    write_metadata      = true  # subscription_name, message_id, publish_time, attributes
    drop_unknown_fields = false # a schema/table mismatch should be loud, not silently lose a column
  }

  # Messages BigQuery rejects one by one go to the DLQ after 5 attempts (the minimum).
  # Subscription-level errors (table gone, grant lost) are NOT dead-lettered: they stay in the backlog.
  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.answer_events_dlq.id
    max_delivery_attempts = 5
  }

  # Wait between redeliveries (exponential, 10 s up to 10 min). These are Pub/Sub's own defaults,
  # written down so the plan shows them.
  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  # How long an unwritten message waits in the backlog (7 days, the default).
  message_retention_duration = "604800s"

  # By default a subscription is DELETED after 31 days of inactivity. This project can pause for
  # weeks, so never expire.
  expiration_policy {
    ttl = ""
  }

  # Pub/Sub checks it can write to the table when it creates the subscription, so the grant comes first.
  depends_on = [google_bigquery_table_iam_member.pubsub_agent_writer]
}

# Without a subscription, messages sent to the DLQ topic would be dropped. You read this one with gcloud.
resource "google_pubsub_subscription" "answer_events_dlq_inspect" {
  name  = "answer-events-dlq-inspect"
  topic = google_pubsub_topic.answer_events_dlq.id

  ack_deadline_seconds       = 60
  message_retention_duration = "604800s"

  expiration_policy {
    ttl = ""
  }
}

# --- IAM: who may do what, on these resources only ---

# The app publishes to the main topic. Nothing else: not the DLQ, not project-wide.
resource "google_pubsub_topic_iam_member" "api_publisher" {
  topic  = google_pubsub_topic.answer_events.id
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${local.foundation.api_sa_email}"
}

# Dead-lettering: the service agent publishes the failed message to the DLQ...
resource "google_pubsub_topic_iam_member" "pubsub_agent_dlq_publisher" {
  topic  = google_pubsub_topic.answer_events_dlq.id
  role   = "roles/pubsub.publisher"
  member = local.pubsub_service_agent
}

# ...and acks the original, which removes it from the source subscription.
resource "google_pubsub_subscription_iam_member" "pubsub_agent_source_subscriber" {
  subscription = google_pubsub_subscription.answer_events_to_bq.name
  role         = "roles/pubsub.subscriber"
  member       = local.pubsub_service_agent
}
