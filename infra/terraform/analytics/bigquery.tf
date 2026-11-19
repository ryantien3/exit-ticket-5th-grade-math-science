# BigQuery: where answer events land. Long-lived and locked: this is the history teachers use.
# See docs/phase-6a-design.md §3.6 and §5.

resource "google_bigquery_dataset" "analytics" {
  dataset_id  = "exit_ticket_analytics"
  location    = var.region # permanent: a dataset can't move
  description = "Exit-ticket answer events (synthetic students only). Written by the answer-events-to-bq subscription."

  # Delete lock 1 (API): deleting the dataset fails while it still contains tables.
  delete_contents_on_destroy = false
  # Delete lock 2 (state): Terraform refuses to delete it, even if this block is removed from the code.
  deletion_policy = "PREVENT"

  lifecycle {
    # Delete lock 3 (code): any plan that would destroy it errors out.
    prevent_destroy = true
  }
}

resource "google_bigquery_table" "answer_events" {
  dataset_id  = google_bigquery_dataset.analytics.dataset_id
  table_id    = "answer_events"
  description = "One row per answer event, as written by Pub/Sub. Query the answer_events_clean view instead (deduplicated)."
  schema      = file("${path.module}/schemas/answer_events_table.json")

  time_partitioning {
    type  = "DAY"
    field = "answered_at" # event time, not arrival time
    # No expiration_ms: history across the school year is the point (design §3.6).
  }
  clustering = ["classroom_id", "standard_code"] # teachers filter by class, then standard

  # false: a Looker Studio chart without a date filter would otherwise fail.
  require_partition_filter = false

  # Delete lock 2 (state). It's also the provider default; set so it's visible.
  deletion_protection = true

  lifecycle {
    # Delete lock 3 (code).
    prevent_destroy = true
  }
}

# One row per event_id (Pub/Sub is at-least-once). Looker Studio reads this, never the raw table.
resource "google_bigquery_table" "answer_events_clean" {
  dataset_id  = google_bigquery_dataset.analytics.dataset_id
  table_id    = "answer_events_clean"
  description = "answer_events without duplicates: the earliest copy of each event_id."

  view {
    use_legacy_sql = false
    query          = <<-SQL
      SELECT * EXCEPT (copy_number)
      FROM (
        SELECT
          *,
          ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY publish_time) AS copy_number
        FROM `${google_bigquery_table.answer_events.project}.${google_bigquery_dataset.analytics.dataset_id}.${google_bigquery_table.answer_events.table_id}`
      )
      WHERE copy_number = 1
    SQL
  }

  # A view holds no data: it's a saved query that can be recreated any time. No lock.
  deletion_protection = false
}

# The Pub/Sub service agent writes rows. Data Editor on this table only, not the dataset or project.
resource "google_bigquery_table_iam_member" "pubsub_agent_writer" {
  dataset_id = google_bigquery_table.answer_events.dataset_id
  table_id   = google_bigquery_table.answer_events.table_id
  role       = "roles/bigquery.dataEditor"
  member     = local.pubsub_service_agent
}
