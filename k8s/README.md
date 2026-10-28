# Kubernetes manifests (GKE Autopilot)

The app on GKE: the api (with a Cloud SQL Auth Proxy sidecar), nginx (web), a migration Job, and an Ingress. The
database is Cloud SQL; secrets come from Secret Manager. Explanations in
[docs/phase-3c-gke.md](../docs/phase-3c-gke.md) (GKE basics, Ingress) and
[docs/phase-4b-secrets-identity.md](../docs/phase-4b-secrets-identity.md) (secrets, identity, the proxy).

| File | Creates |
|---|---|
| `namespace.yaml` | Namespace `exit-ticket` |
| `configmap.yaml` | ConfigMap `exit-ticket-config`: `LOG_LEVEL`, `ENVIRONMENT`, `API_HOST`, `DATABASE_URL` (no password) |
| `serviceaccount.yaml` | Kubernetes SA `api`, mapped to the Google SA `exit-ticket-api` (Workload Identity) |
| `secretproviderclass.yaml` | `app-secrets`: which Secret Manager versions the add-on mounts as files |
| `migrate-job.yaml` | Job `migrate`: `alembic upgrade head` with the api image + proxy sidecar |
| `api.yaml` | Deployment (api + Cloud SQL Auth Proxy native sidecar + secrets volume) + ClusterIP Service `api` (port 8000) |
| `web.yaml` | Deployment + ClusterIP Service `web` (port 80 → nginx on 8080) |
| `ingress.yaml` | Ingress `web` → external Application Load Balancer (**bills hourly**) |

**No secret is in this folder or in a Kubernetes Secret.** `scripts/create-app-secrets.sh` creates them in Secret
Manager; the GKE Secret Manager add-on mounts them into the api and migrate pods at `/var/secrets/exit-ticket`.

**The literal word `PROJECT_ID` appears in several files.** Fill it in while applying, so the real project ID is
never written to a file:
```bash
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/api.yaml | kubectl apply -f -
```

## Prerequisites

- Cloud SQL `exit-ticket-db` running (`gcloud sql instances patch exit-ticket-db --activation-policy=ALWAYS`).
- The cluster created with `--service-account=exit-ticket-nodes@…` and `--enable-secret-manager`.
- The secrets and IAM bindings from [docs/phase-4b-secrets-identity.md](../docs/phase-4b-secrets-identity.md).

## Apply order

Run from the repo root, after `gcloud container clusters get-credentials`.

1. Namespace, settings, identity, secrets mapping:
```bash
kubectl apply -f k8s/namespace.yaml
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/configmap.yaml | kubectl apply -f -
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/serviceaccount.yaml | kubectl apply -f -
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/secretproviderclass.yaml | kubectl apply -f -
```
2. Migrations, and check they ran:
```bash
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/migrate-job.yaml | kubectl apply -f -
kubectl -n exit-ticket wait --for=condition=complete job/migrate --timeout=300s
kubectl -n exit-ticket logs job/migrate -c migrate
```
3. The app:
```bash
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/api.yaml | kubectl apply -f -
sed "s/PROJECT_ID/$(gcloud config get project)/g" k8s/web.yaml | kubectl apply -f -
kubectl -n exit-ticket rollout status deployment/api --timeout=300s
kubectl -n exit-ticket rollout status deployment/web --timeout=300s
```
4. Only when you want a public URL (bills hourly). Wait for an `ADDRESS`, then expect up to ~10 minutes of
   `000`/502 before a 200:
```bash
kubectl apply -f k8s/ingress.yaml
kubectl -n exit-ticket get ingress web -w
```

## Teardown

Ingress → namespace → cluster, then stop Cloud SQL. The controllers that clean up the load balancer, the NEG and
the health-check firewall rule run inside the cluster, so it must still exist while they finish:
```bash
kubectl delete -f k8s/ingress.yaml --wait=true
kubectl delete namespace exit-ticket
```
Wait 2–3 minutes, then:
```bash
gcloud compute network-endpoint-groups list
gcloud container clusters delete exit-ticket --region=us-east4
gcloud sql instances patch exit-ticket-db --activation-policy=NEVER
```
Full verification list: [docs/phase-4-design.md §11](../docs/phase-4-design.md#11-teardown-policy-enforced).
