# Dead-letter drill (Phase 6D). OFF by default: nothing here exists unless you apply with
#   terraform apply -var="dlq_drill=true"
# and a plain `terraform apply` removes it all again.
#
# Why a separate subscription and table: a message is dead-lettered only when BigQuery rejects
# that ONE message while the subscription stays healthy. Breaking the real table or its grant
# instead puts the subscription in an error state, and messages wait in the backlog (never the
# DLQ). So the drill uses its own table where chosen_choice may be 1 character: a message with
# "chosen_choice":"AB" is valid Avro but too long for that column.
#
# The filter means only messages published with the attribute drill=dlq reach this subscription;
# real answers never touch the drill table.

variable "dlq_drill" {
  description = "true = create the dead-letter drill subscription and table (temporary). Default false."
  type        = bool
  default     = false
}

locals {
  drill_count = var.dlq_drill ? 1 : 0

  # The real table's columns, with chosen_choice limited to 1 character (STRING(1)).
  drill_schema = jsonencode([
    for column in jsondecode(file("${path.module}/schemas/answer_events_table.json")) :
    column.name == "chosen_choice" ? merge(column, { maxLength = "1" }) : column
  ])
}

resource "google_bigquery_table" "dlq_drill" {
  count       = local.drill_count
  dataset_id  = google_bigquery_dataset.analytics.dataset_id
  table_id    = "answer_events_dlq_drill"
  description = "TEMPORARY: dead-letter drill target (chosen_choice STRING(1)). Removed by a plain apply."
  schema      = local.drill_schema

  deletion_protection = false # temporary by design
}

resource "google_bigquery_table_iam_member" "dlq_drill_writer" {
  count      = local.drill_count
  dataset_id = google_bigquery_table.dlq_drill[0].dataset_id
  table_id   = google_bigquery_table.dlq_drill[0].table_id
  role       = "roles/bigquery.dataEditor"
  member     = local.pubsub_service_agent
}

resource "google_pubsub_subscription" "dlq_drill" {
  count  = local.drill_count
  name   = "answer-events-dlq-drill"
  topic  = google_pubsub_topic.answer_events.id
  filter = "attributes.drill = \"dlq\""

  bigquery_config {
    table               = "${google_bigquery_table.dlq_drill[0].project}.${google_bigquery_table.dlq_drill[0].dataset_id}.${google_bigquery_table.dlq_drill[0].table_id}"
    use_topic_schema    = true
    write_metadata      = true
    drop_unknown_fields = false
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.answer_events_dlq.id
    max_delivery_attempts = 5
  }

  # Shorter than the real subscription's 600 s cap, so 5 attempts finish within a few minutes.
  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "60s"
  }

  depends_on = [google_bigquery_table_iam_member.dlq_drill_writer]
}

# Dead-lettering from this subscription: the service agent must be able to ack the original here
# (it can already publish to the DLQ topic, from pubsub.tf).
resource "google_pubsub_subscription_iam_member" "dlq_drill_agent_subscriber" {
  count        = local.drill_count
  subscription = google_pubsub_subscription.dlq_drill[0].name
  role         = "roles/pubsub.subscriber"
  member       = local.pubsub_service_agent
}
