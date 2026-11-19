# For the app config (PUBSUB_TOPIC) and the smoke tests.

output "topic_id" {
  description = "projects/PROJECT_ID/topics/answer-events: the value of PUBSUB_TOPIC for the api."
  value       = google_pubsub_topic.answer_events.id
}

output "table_id" {
  description = "The raw events table (PROJECT.DATASET.TABLE). Query answer_events_clean for reports."
  value       = "${google_bigquery_table.answer_events.project}.${google_bigquery_table.answer_events.dataset_id}.${google_bigquery_table.answer_events.table_id}"
}

output "view_id" {
  description = "The deduplicated view (PROJECT.DATASET.VIEW) that Looker Studio reads."
  value       = "${google_bigquery_table.answer_events_clean.project}.${google_bigquery_table.answer_events_clean.dataset_id}.${google_bigquery_table.answer_events_clean.table_id}"
}

output "subscription_name" {
  description = "The BigQuery subscription. Check its state at session start."
  value       = google_pubsub_subscription.answer_events_to_bq.name
}

output "dlq_subscription_name" {
  description = "Pull this to inspect dead-lettered messages."
  value       = google_pubsub_subscription.answer_events_dlq_inspect.name
}
