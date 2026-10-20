# exit-ticket-5th-grade-math-science

**Exit Ticket** is a learning platform for NYC 5th graders. Students answer short math and science quizzes ("exit
tickets"). Every question is tagged with a New York State learning standard, so teachers can see mastery by
standard:

- **Math:** NY-5.NF (fractions), NY-5.NBT (place value and decimals)
- **Science:** 5-PS1 (matter and its interactions)

The platform runs on Google Cloud: GKE Autopilot, Cloud SQL Postgres, Pub/Sub, BigQuery and Looker Studio,
provisioned with Terraform and deployed through Cloud Build. It is a cloud engineering portfolio project and
hands-on practice for the GCP Associate Cloud Engineer exam. All student data is synthetic.

## Progress

- [x] **Phase 0:** Repo setup: project folder structure, secure `.gitignore`, CLAUDE.md
- [x] **Phase 1:** GCP foundations: gcloud configuration, APIs, IAM, service accounts, audit logs ([notes](docs/phase-1-foundations.md))
- [x] **Phase 2:** The application, running locally: FastAPI + Postgres API, student and teacher web apps, sign-in, nginx reverse proxy ([notes](docs/phase-2-local-app.md))
