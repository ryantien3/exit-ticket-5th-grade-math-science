# Project: Exit Ticket — NYC 5th-Grade Math & Science Learning Platform

## Purpose
Portfolio project for a Google Strategic Cloud Customer Engineer / TAM / gtech consultant role and hands-on practice
for the GCP Associate Cloud Engineer exam. Students answer short quizzes ("exit tickets");
every question is tagged with a NY standard so teachers see mastery by standard.

## Standards in scope
- Math (NYS Next Generation Mathematics Learning Standards): NY-5.NF (fractions), NY-5.NBT (place value/decimals)
- Science (NYS P-12 Science Learning Standards): 5-PS1 (matter and its interactions)

## Architecture (target)
GKE Autopilot (api + web) → Cloud SQL Postgres; answer events → Pub/Sub → BigQuery → Looker Studio.
Artifact Registry, Secret Manager, Workload Identity, Cloud Build CI/CD, Terraform with remote state in GCS.
Region: us-east4 (zone us-east4-a).
Roadmap: see README.md, Roadmap section (phases 0-8 mapped to ACE exam domains).

## Repo layout
app/api (Python 3.12, FastAPI) · app/web (frontend) · infra/terraform · k8s · scripts · docs

## Rules for Claude
1. I am a beginner learning for the ACE exam. Explain what you're doing and why in plain language. Go through the steps like a teacher, anticipating questions and quirks that a beginner may ask. Make sure theory is understood.
2. NEVER run gcloud, terraform apply/destroy, or kubectl apply/delete yourself. Write the command,
   explain it, and let me run it. You may run terraform fmt/validate/plan and local Docker/tests.
3. Never commit secrets, keys, .env files, tfstate, or tfvars. No service account key files at all;
   use Workload Identity and Application Default Credentials.
4. Never commit directly to main. One branch per task, named like feature/..., infra/..., docs/...
5. Use only synthetic student data. No real student names or information, ever.
6. Docker images must be built for linux/amd64 (my laptop is an M2 Mac).
7. Keep costs low: smallest viable tiers; remind me to tear down infrastructure when idle.
