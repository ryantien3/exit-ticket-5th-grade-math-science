# Kubernetes manifests (GKE Autopilot)

The app on GKE: a temporary Postgres, the api, nginx (web), a migration Job, and an Ingress. Full explanation in
[docs/phase-3c-gke.md](../docs/phase-3c-gke.md).

| File | Creates |
|---|---|
| `namespace.yaml` | Namespace `exit-ticket` |
| `configmap.yaml` | ConfigMap `exit-ticket-config`: `LOG_LEVEL`, `ENVIRONMENT`, `API_HOST` |
| `db.yaml` | **Temporary** Postgres Deployment + ClusterIP Service `db` (data lost when its pod goes; Cloud SQL in Phase 4) |
| `migrate-job.yaml` | Job `migrate`: `alembic upgrade head` with the api image |
| `api.yaml` | Deployment + ClusterIP Service `api` (port 8000) |
| `web.yaml` | Deployment + ClusterIP Service `web` (port 80 → nginx on 8080) |
| `ingress.yaml` | Ingress `web` → external Application Load Balancer (**bills hourly**) |

**Secrets are never in this folder.** `scripts/create-k8s-secrets.sh` creates `db-secrets` and `api-secrets`
directly in the cluster.

**Images are referenced by digest, with the literal word `PROJECT_ID` in the path.** Fill it in while applying, so
the real project ID is never written to a file:
```bash
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/api.yaml | kubectl apply -f -
```

## Apply order

Run from the repo root, after `gcloud container clusters get-credentials`.

1. Namespace and settings:
```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml
scripts/create-k8s-secrets.sh
```
2. Database, and wait until it accepts connections:
```bash
kubectl apply -f k8s/db.yaml
kubectl -n exit-ticket rollout status deployment/db --timeout=180s
```
3. Migrations, and check they ran:
```bash
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/migrate-job.yaml | kubectl apply -f -
kubectl -n exit-ticket wait --for=condition=complete job/migrate --timeout=300s
kubectl -n exit-ticket logs job/migrate
```
4. The app:
```bash
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/api.yaml | kubectl apply -f -
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/web.yaml | kubectl apply -f -
kubectl -n exit-ticket rollout status deployment/api --timeout=180s
kubectl -n exit-ticket rollout status deployment/web --timeout=180s
```
5. Only when you want a public URL (bills hourly):
```bash
kubectl apply -f k8s/ingress.yaml
```

## Teardown

Ingress first, so GKE removes the load balancer, then the cluster:
```bash
kubectl delete -f k8s/ingress.yaml --wait=true
gcloud container clusters delete exit-ticket --region=us-east4
```
