# Phase 3A — Containers, Locally

Phase 3A packages the application into two container images, the API and the web front end, and runs the whole
stack in containers on the laptop. Nothing touches Google Cloud yet. The images are built for `linux/amd64`, the CPU
type of GKE nodes, so the exact images tested here are the ones Phase 3B pushes to Artifact Registry.

| Part | What was built | PR |
|---|---|---|
| **3A** | `app/api/Dockerfile`, `app/web/Dockerfile`, both `.dockerignore` files, a startup check for `API_HOST`, nginx DNS re-resolution, and compose profiles for dev mode and full-container mode | (this PR) |

> **About the commands in this doc.** Each code block contains commands only, no `#` comments. In zsh, `#` is not
> a comment unless `setopt interactivecomments` is on, so a pasted `# note` becomes extra arguments (see
> [Phase 2 lessons learned](phase-2-local-app.md#15-lessons-learned)). Run every command from the repo root unless
> a block starts with `cd`.

> **Placeholders used below.**
>
> | Placeholder | Meaning |
> |---|---|
> | `PROJECT_ID` | Your GCP project ID (Artifact Registry preview only) |

**Contents**

1. [Architecture](#1-architecture)
2. [The two images](#2-the-two-images)
3. [Compose profiles: two modes, one file](#3-compose-profiles-two-modes-one-file)
4. [Migrations and seed as one-off containers](#4-migrations-and-seed-as-one-off-containers)
5. [nginx, DNS and a recreated API](#5-nginx-dns-and-a-recreated-api)
6. [How this maps to GKE](#6-how-this-maps-to-gke)
7. [Verification checklist](#7-verification-checklist)
8. [Interview talking points](#8-interview-talking-points)
9. [Lessons learned](#9-lessons-learned)
10. [ACE exam notes](#ace-exam-notes)

---

## 1. Architecture

**Full-container mode:**

```
Browser
  │  http://localhost:8080   (the only published port)
  ▼
web-full container: nginx-unprivileged, UID 101, port 8080
  │  serves the HTML/CSS/JS baked into the image
  │  /api/... → strips /api → http://api:8000/...   (compose network DNS)
  ▼
api container: uvicorn + FastAPI, UID 10001, port 8000 (not published)
  │  postgresql+psycopg://...@db:5432
  ▼
db container: Postgres 16, data in the named volume "pgdata"
```

**Dev mode** is Phase 2's setup, unchanged in how you use it: nginx (now the same web image, with your files
mounted over the baked-in ones) forwards `/api/` to `host.docker.internal:8000`, the uvicorn `--reload` running in a
terminal on your Mac.

---

## 2. The two images

### The build context and `.dockerignore`

`docker buildx build ... app/api` sends the **build context**, the whole `app/api` folder, to the builder. A
`COPY` can only see what's in the context. Without a `.dockerignore`, that would include `.env` (real secrets),
`.venv` (hundreds of MB of macOS binaries), tests and caches.

- **An image is a stack of tar files.** Anyone who can pull it can unpack every layer. A file deleted in a later
  layer is still readable in the earlier one.
- **This repo is public, and images travel:** Artifact Registry, every GKE node, anyone with
  `roles/artifactregistry.reader`. Secrets only ever arrive as environment variables at run time.
- **`.dockerignore` patterns are not `.gitignore` patterns.** A pattern matches only from the top of the context:
  `__pycache__/` missed `app/__pycache__`. Use `**/__pycache__`, `**/.env`, and so on.
- **The web image uses an allowlist** (`*`, then `!public`, `!nginx.conf`, `!docker-entrypoint.d`): with three
  inputs, listing what's allowed is safer than guessing what isn't.

### API image ([`app/api/Dockerfile`](../app/api/Dockerfile))

| Choice | Why |
|---|---|
| `FROM python:3.12.15-slim-trixie` | Exact Python patch + Debian release, so rebuilds are repeatable |
| `requirements.txt` copied and installed **before** the code | Layer caching: a code edit reuses the 7-second pip layer |
| No `apt-get install` | `psycopg[binary]` bundles `libpq`; every dependency ships a prebuilt amd64 wheel |
| `PYTHONUNBUFFERED=1` | Log lines reach stdout, and Cloud Logging, immediately instead of sitting in a buffer |
| `PYTHONDONTWRITEBYTECODE=1` | No `.pyc` writes: the code is read-only to the app user anyway |
| `alembic.ini` + `alembic/` in the image | The same image can run migrations as a Job. It **never** migrates on startup |
| `USER 10001`, code owned by root | Non-root, can't modify its own code. Numeric, so Kubernetes `runAsNonRoot` can verify it |
| Exec-form `CMD ["uvicorn", ..., "--host", "0.0.0.0", ...]` | uvicorn is PID 1 and gets SIGTERM; `0.0.0.0` is reachable from outside the container |
| No `HEALTHCHECK` | Kubernetes ignores it and uses its own probes on `/healthz` and `/readyz` |

Result: **71 MB compressed** (what's pushed and pulled), 306 MB on disk. Your code is about 150 KB of that.

### Web image ([`app/web/Dockerfile`](../app/web/Dockerfile))

In Phase 2, compose mounted `public/` and `nginx.conf` from the laptop. **A GKE pod has no laptop to mount from**,
so the files are baked into the image.

| Choice | Why |
|---|---|
| `FROM nginxinc/nginx-unprivileged:1.30.6-alpine` | Runs entirely as UID 101; works with `runAsNonRoot` |
| `listen 8080` in `nginx.conf` | Unprivileged processes traditionally can't bind ports below 1024 |
| Sample pages deleted, then `COPY public /usr/share/nginx/html` | The web root holds exactly `public/` |
| `nginx.conf` → `/etc/nginx/templates/default.conf.template` | Outside the web root, so it can never be served |
| `05-require-api-host.sh` in `/docker-entrypoint.d/` | No default for `API_HOST`: a missing value stops the container with a clear message |
| `NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1` | Lets nginx re-check the API's IP at run time (see [§5](#5-nginx-dns-and-a-recreated-api)) |

Result: **25.6 MB**.

`nginx` vs `nginxinc/nginx-unprivileged`: the official image's master process runs as root and listens on 80. The
unprivileged one (published by NGINX Inc.) never runs as root, at the cost of a different port (8080) and a
different publisher than the Docker Official Image.

### Building

```bash
docker buildx build --platform linux/amd64 -t exit-ticket-api:dev --load app/api
docker buildx build --platform linux/amd64 -t exit-ticket-web:dev --load app/web
docker image inspect exit-ticket-api:dev --format '{{.Os}}/{{.Architecture}} user={{.Config.User}}'
docker image ls exit-ticket-api:dev
```

- **Why amd64:** GKE nodes are x86-64. An arm64 image from an M2 fails there with `exec format error`.
- **Why it's slower:** Docker Desktop's Linux VM is arm64, so amd64 `RUN` steps run under **QEMU emulation**, often
  3–10× slower. `COPY` and image downloads aren't emulated, and nothing here compiles, so a full API build takes
  about 12 seconds.
- **`--load`** puts the result into local Docker so `docker run` can use it.

---

## 3. Compose profiles: two modes, one file

[`compose.yaml`](../compose.yaml) uses **profiles**. A service with `profiles: [...]` only starts when one of its
profiles is active, **or when you name it on the command line**.

| Service | Profile | What it is |
|---|---|---|
| `db` | (none: always) | Postgres 16, published on `127.0.0.1:5432` for uvicorn, pytest and the seed script |
| `web` | `dev` | The web image with `public/` and `nginx.conf` mounted read-only; `API_HOST=host.docker.internal:8000` |
| `api` | `full` | The API image; secrets from `env_file: app/api/.env`; `DATABASE_URL` → `db:5432`; waits for db to be healthy; **no published port** |
| `web-full` | `full` | The web image exactly as built; `API_HOST=api:8000`; published on `127.0.0.1:8080` |

Both web services use port 8080, so run **one mode at a time**. Only `api` needs the name it will have on GKE,
because it's the one being called by name.

**Options considered:** two compose files (`-f compose.yaml -f compose.full.yaml` on every command, and orphans if
you forget one), or an environment variable to switch `API_HOST` (can't turn off the bind mounts, so full mode
wouldn't test the real image). Profiles keep one file and short commands.

### Dev mode (fastest for editing code)

In one terminal tab:
```bash
cd ~/projects/exit-ticket-5th-grade-math-science/app/api
.venv/bin/uvicorn app.main:app --reload
```
In another:
```bash
cd ~/projects/exit-ticket-5th-grade-math-science
docker compose up -d db web
```

Python edits reload automatically; HTML/CSS/JS edits show up on refresh. API docs: http://localhost:8000/docs.

### Full-container mode (what GKE will run)

```bash
docker compose --profile full up -d --build
docker compose --profile full ps
```

After a code change, run the same `up -d --build` again: only the changed layers rebuild, and only containers whose
image changed are recreated. App: http://localhost:8080 and http://localhost:8080/teacher.html.

Logs:
```bash
docker compose logs api --tail 20
docker compose logs web-full --tail 20
```

**API docs in full mode.** The api has no published port, on purpose. To use `/docs` briefly, start a temporary
second API container with a port, open http://localhost:8001/docs, and press Ctrl+C when done:
```bash
docker compose run --rm -p 127.0.0.1:8001:8000 api
```
This is the compose version of `kubectl port-forward`: a temporary tunnel that only your laptop can use.

### Switching modes and stopping

Plain `docker compose stop` / `down` **only see services without a profile**, so they skip `web`, `api` and
`web-full`. Use `--profile "*"` (every profile) to cover both modes:

```bash
docker compose --profile "*" stop
```
```bash
docker compose --profile "*" down
```

| Command | Containers | Database data |
|---|---|---|
| `docker compose --profile "*" stop` | stopped, kept | kept |
| `docker compose --profile "*" down` | removed | kept (in the `pgdata` volume) |
| `docker compose --profile "*" down -v` | removed | **deleted** |

Switching from full to dev: `docker compose --profile "*" down`, then the dev-mode commands. Both modes share the
same database volume.

---

## 4. Migrations and seed as one-off containers

The API container **never migrates on startup**. With 3 replicas, 3 pods would start together and race to change
the schema; during a rollout, new pods would migrate while old ones still serve traffic. Migrations are one
deliberate step instead.

```bash
docker compose run --rm api alembic upgrade head
docker compose run --rm api alembic current
```

`run --rm` starts a temporary container from the **same image**, runs one command instead of uvicorn, honors
`depends_on` (waits for db), and deletes the container afterwards. That's what a **Kubernetes Job** is: run an image
to completion, once.

The seed script lives in `scripts/`, outside the API image. Either mount it into a one-off container:
```bash
docker compose run --rm -v ./scripts:/scripts:ro -e PYTHONPATH=/app api python /scripts/seed.py
```
or run it from your venv against the published db port:
```bash
cd app/api
.venv/bin/python ../../scripts/seed.py
```

Both migrations and seed are **idempotent**: running them again changes nothing.

Note for the migration Job later: `alembic/env.py` loads the app settings, so the Job needs `SESSION_SECRET` and
`TEACHER_PASSCODE` as well as `DATABASE_URL`.

---

## 5. nginx, DNS and a recreated API

**The trap.** nginx normally looks up `api` **once, at startup**, and keeps that IP forever. A *restarted* container
keeps its IP, but a *recreated* one (after `up -d --build`) can get a new one. When it does, nginx keeps calling the
old address: 502/504 on every request until nginx itself is restarted. This was reproduced by holding the old IP
with a placeholder container.

**A fix that didn't work:** `depends_on: api: {restart: true}` in compose. It only restarts dependents included in
the same compose command, so `docker compose up -d api` left nginx stale.

**The working fix,** in [`app/web/nginx.conf`](../app/web/nginx.conf):
```nginx
resolver ${NGINX_LOCAL_RESOLVERS} valid=5s ipv6=off;

upstream api {
    zone api 64k;
    server ${API_HOST} resolve max_fails=0;
}
```

- **`resolve`** (open-source nginx since 1.27.3) keeps re-checking the name while nginx runs. It needs a shared
  memory `zone`.
- **`resolver`** says which DNS server to ask. The image fills in `NGINX_LOCAL_RESOLVERS` from `/etc/resolv.conf`:
  Docker's DNS (`127.0.0.11`) in compose, kube-dns on GKE. `valid=5s` re-checks at least every 5 seconds.
- **`ipv6=off`** ignores IPv6 answers. That also removes the root cause of Phase 2's `host.docker.internal` bug.
- **Tested:** the api moved from `.4` to `.5` while a placeholder held `.4`; nginx followed in about 1.5 s, without
  a restart.

**A side effect, caught by testing:** with `resolve`, nginx starts even when `API_HOST` is missing (it just logs
`could not be resolved` forever). Fail-fast was restored with
[`05-require-api-host.sh`](../app/web/docker-entrypoint.d/05-require-api-host.sh), which exits 1 with
`API_HOST is not set` before nginx starts.

**On GKE,** nginx's resolver doesn't use Kubernetes' DNS search domains, so `API_HOST` must be the full name:
`api.<namespace>.svc.cluster.local:8000`.

**Does `max_fails=0` still matter?** When a name resolves to **one** address (a compose container, a Kubernetes
ClusterIP), nginx never marks the only server down, so it's redundant there. It's kept for dev mode and for any case
where a name resolves to several addresses (`--scale api=2`).

---

## 6. How this maps to GKE

| Compose (Phase 3A) | Kubernetes / GCP (Phase 3B+) |
|---|---|
| `api` and `web-full` services | **Deployments** (one per image), each running 1+ pods |
| `image: exit-ticket-api:dev`, built locally | Image in **Artifact Registry**, `us-east4-docker.pkg.dev/PROJECT_ID/...`, referenced by tag or digest |
| `api` with no `ports:`, reached as `api:8000` | **ClusterIP Service**: a stable internal IP and DNS name, not reachable from outside the cluster |
| `web-full` published on `127.0.0.1:8080` | Service behind an **Ingress** (external Application Load Balancer) |
| `env_file: app/api/.env` | **Secret** (ideally synced from **Secret Manager**) + env vars in the Deployment |
| `environment:` (`DATABASE_URL`, `API_HOST`) | `env:` in the pod spec, or a **ConfigMap** |
| `docker compose run --rm api alembic upgrade head` | A **Job** using the same image, run before the rollout |
| `docker compose run -p 8001:8000 api` for `/docs` | `kubectl port-forward` |
| `depends_on: db: condition: service_healthy` | Readiness probe on `/readyz` (Kubernetes has no start order) |
| `docker stop` sends SIGTERM | Pod termination sends SIGTERM, then SIGKILL after the grace period (30 s default) |
| Docker's DNS at `127.0.0.11` | kube-dns / Cloud DNS for GKE |
| `pgdata` volume | **Cloud SQL** (Phase 4) |

---

## 7. Verification checklist

| Check | Result | What it proves |
|---|---|---|
| `docker image inspect` | `linux/amd64` for both images | They'll run on GKE nodes |
| `id` inside each container | `uid=10001(app)`, `uid=101(nginx)` | Neither runs as root |
| `find` inside the API image | no `.env`, tests, `.venv`, `__pycache__` | `.dockerignore` works, nothing secret is baked in |
| Web root listing | exactly the files in `public/` | nothing extra served |
| `/nginx.conf`, `/default.conf.template`, `/../nginx.conf` | 404 / 404 / 400 | the config is never served |
| Web image without `API_HOST` | exits 1, `API_HOST is not set` | fails fast |
| Through `localhost:8080`: `/api/healthz`, `/api/readyz`, `/api/standards` | 200 | full path browser → nginx → api → Postgres |
| Teacher sign-in, student sign-in, open ticket, submit, retake | 201, 201, 200 (no answer key), 201, 409 | the product works in containers |
| `curl 127.0.0.1:8000` / `:8001` from the Mac | connection refused | the api is internal only, like ClusterIP |
| `docker compose restart api` | about 1 s of 502 while uvicorn boots, then 200 | no 10-second lockout |
| api recreated on a new IP | 200 after about 1.5 s, nginx not restarted | runtime DNS works |
| `docker compose logs api` | every line JSON with `severity` | Cloud Logging will parse levels |
| `docker stop` on the API | 0.45 s, exit 0, "shutting down" logged | SIGTERM reaches uvicorn |
| Dev mode + uvicorn restart | 502 while down, 200 on the first request back | Phase 2 workflow still works |
| `pytest -q`, `alembic check` | 87 passed, no changes | nothing regressed |

**In the browser (full mode):**
1. http://localhost:8080/teacher.html: sign in with your teacher passcode, click **Release** on a ticket, then
   **Show student codes** and copy one code.
2. In a private window, http://localhost:8080: type that code, open the ticket, answer each question with **Next**,
   and submit.
3. Back on the dashboard: the submission appears under recent activity and in the heatmap within 15 seconds.

---

## 8. Interview talking points

**1. "The image I test is the image I ship."** Built once for `linux/amd64`, configured only by environment variables
at run time, so the same image runs in compose and on GKE. No secrets in any layer; `.dockerignore` plus explicit
`COPY`s as two lines of defense.

**2. "Migrations are a deployment step, not a startup side effect."** The image carries the migrations, but a
one-off container (a Job on GKE) runs them, so replicas never race each other.

**3. A debugging story (STAR).**
- *Situation:* after the API container was recreated, every request through nginx returned 502/504.
- *Task:* find out why, and make recovery automatic.
- *Action:* nginx's error log showed it calling the API's *old* IP. I reproduced it on purpose by holding the old
  IP with a placeholder container. A compose-only fix (`restart: true`) didn't cover all commands, and I said so.
  The working fix was nginx's runtime `resolve` with the container's own DNS server. Testing then showed that
  fix had broken fail-fast for a missing `API_HOST`, so I added an explicit startup check.
- *Result:* nginx follows a new API IP in about 1.5 seconds with no restart, and a misconfigured container still
  fails immediately with a clear message.
- *Lesson:* test the failure and recovery paths, and re-test what a fix might have broken.

**4. "Least privilege inside the container, too."** Both images run as non-root numeric UIDs, the API can't modify
its own code, and only the web container is reachable from outside.

---

## 9. Lessons learned

| What happened | Cause | Lesson |
|---|---|---|
| `__pycache__` ended up in the API image | `.dockerignore` patterns only match at the context root | Use `**/` for anything that can appear at any depth; check the image with `find` |
| 502/504 forever after recreating the api | nginx resolved `api` once at startup and kept the old IP | `resolve` + `resolver` in the upstream; test with the old IP held |
| `depends_on: restart: true` didn't fix it | Compose only restarts dependents included in the same command | Test the fix the way you'll actually use it |
| nginx started without `API_HOST` after adding `resolve` | `resolve` tolerates unresolvable names at startup | A fix can remove an old safety net; re-run the earlier tests |
| Plain `docker compose stop` left nginx running | Services in profiles are skipped unless a profile is active | `docker compose --profile "*" stop` |
| A test command never ran | macOS has no `timeout` command | Run long-lived containers with `-d`, then check `docker inspect` |
| The editor flagged a valid Dockerfile `ENV` | VS Code's linter misread a `\` line continuation | The real build is the source of truth |

---

## ACE exam notes

**Images and registries**
- **An image is layers + metadata** (user, command, ports). A container is a running instance. Anything written into
  a layer stays readable, even if a later layer deletes it.
- **Tag vs digest:** a tag (`:3.12-slim`, `:dev`, `:v1`) is a movable label; a digest (`@sha256:...`) identifies the
  exact content and never changes. Deploy by digest when you need certainty about what's running.
- **Architecture:** `linux/amd64` for standard GKE nodes. A wrong-architecture image fails with `exec format error`.
  **Cloud Build** builds on amd64 natively.
- **Artifact Registry** (successor to Container Registry, `gcr.io`, which has been shut down):
  - Image path format: `REGION-docker.pkg.dev/PROJECT_ID/REPOSITORY/IMAGE:TAG`.
  - Docker authenticates with `gcloud auth configure-docker REGION-docker.pkg.dev`.
  - IAM roles: `roles/artifactregistry.reader` to pull (GKE nodes), `roles/artifactregistry.writer` to push
    (Cloud Build).
  - Put the repository in the **same region** as the cluster (`us-east4`) to avoid cross-region transfer.
  - Cleanup policies delete old images automatically, which keeps storage costs down.

**Kubernetes concepts previewed by compose**
- **Deployment** keeps N replicas of a pod running; **Service** gives them one stable name and IP. **ClusterIP** is
  internal only; **LoadBalancer** / **Ingress** expose a Service externally.
- **Job** runs a pod to completion (migrations, one-off tasks).
- **ConfigMap** for non-secret config, **Secret** for secrets; on GCP, keep the source of truth in **Secret Manager**
  and read it with **Workload Identity** (no key files).
- **Probes:** liveness restarts the container; readiness removes it from Service endpoints. Docker `HEALTHCHECK` is
  ignored by Kubernetes.
- **Shutdown:** Kubernetes sends **SIGTERM** to PID 1, waits `terminationGracePeriodSeconds` (30 s default), then
  SIGKILL. Exec-form `CMD` makes your app PID 1. (nginx's graceful signal is SIGQUIT, which Kubernetes doesn't send;
  a `preStop` hook can run `nginx -s quit`. That's a Phase 3B detail.)
- **`runAsNonRoot: true`** in a pod's `securityContext` requires a **numeric** non-root `USER` in the image.

**Cost reminders**
- Phase 3A runs locally and **costs nothing**.
- Phase 3B creates billable resources: Artifact Registry storage (small; a free tier covers 0.5 GB) and, later, GKE
  Autopilot pods. Delete what you're not using.
