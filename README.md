# SwiftDeploy

> A declarative CLI that builds, runs, validates, and promotes a containerised web stack from a single YAML manifest.

SwiftDeploy is the answer to a simple question: **what if the only file you had to write to deploy an app was a description of what you wanted?** No hand-edited Compose files. No copy-pasted Nginx snippets. You write a manifest, and the CLI generates everything else.

This is the HNG DevOps Stage 4A submission. It's small enough to read end-to-end in an afternoon and beginner-friendly enough that you can clone it, run two commands, and have a working reverse-proxied API on your laptop.

---

## Table of Contents

1. [What it does](#what-it-does)
2. [How it works (the big picture)](#how-it-works-the-big-picture)
3. [The stack at a glance](#the-stack-at-a-glance)
4. [Project layout](#project-layout)
5. [Prerequisites](#prerequisites)
6. [Installation](#installation)
7. [Build the container images](#build-the-container-images)
8. [The manifest explained](#the-manifest-explained)
9. [The CLI subcommands](#the-cli-subcommands)
10. [The API service](#the-api-service)
11. [Architecture & design choices](#architecture--design-choices)
12. [End-to-end walkthrough](#end-to-end-walkthrough)
13. [Viewing logs](#viewing-logs)
14. [Troubleshooting](#troubleshooting)
15. [What you can edit vs. what's generated](#what-you-can-edit-vs-whats-generated)

---

## What it does

You write this:

```yaml
services:
  image: swift-deploy-1-node:latest
  port: 3000

nginx:
  image: swiftdeploy-nginx:latest
  port: 8080

network:
  name: swiftdeploy-net
  driver_type: bridge
```

You run this:

```bash
swiftdeploy deploy
```

And SwiftDeploy:

1. Reads your manifest.
2. Generates a `docker-compose.yml` and an `nginx.conf` that match it exactly.
3. Validates the result (image present, port free, nginx config syntactically valid).
4. Brings the stack up.
5. Waits until `/healthz` returns 200.
6. Tells you it's healthy and exits.

You can promote between **stable** and **canary** modes with one command, run a chaos endpoint to simulate degraded behaviour, and tear everything down again — all driven from the same manifest.

---

## How it works (the big picture)

```
                 manifest.yaml
                      │
                      │  (you edit this — the single source of truth)
                      ▼
          ┌────────────────────────┐
          │       swiftdeploy      │   the CLI (Python)
          └───────────┬────────────┘
                      │ Jinja2 renders
                      │ templates with
                      │ values from manifest
        ┌─────────────┼─────────────┐
        ▼                           ▼
  docker-compose.yml          nginx.conf
        │                           │
        │   docker compose up       │   mounted into the nginx container
        ▼                           ▼
  ┌──────────────┐            ┌──────────────┐
  │ swiftdeploy- │  upstream  │ swiftdeploy- │
  │     api      │◄───────────│    nginx     │◄──── localhost:8080
  │ (Flask app)  │   :3000    │ (reverse     │
  │              │            │  proxy)      │
  └──────────────┘            └──────────────┘
        │                           │
        └────── docker network ─────┘
              (swiftdeploy-net)
```

**Key idea:** `docker-compose.yml` and `nginx.conf` are _derived artifacts_. They are deleted and regenerated on every `swiftdeploy init`. If you find yourself editing them directly, stop — change the manifest or the template instead.

---

## The stack at a glance

| Layer           | Tech                                     |
| --------------- | ---------------------------------------- |
| API service     | Python 3.12 + Flask + gunicorn           |
| Reverse proxy   | Custom Nginx image (`FROM nginx:latest`) |
| Orchestration   | Docker + Docker Compose v2               |
| Templating      | Jinja2                                   |
| CLI             | Python 3.10+ (argparse, urllib, PyYAML)  |
| Manifest format | YAML                                     |

---

## Project layout

```
.
├── app/                       <- Flask API source
│   ├── main.py                   the /, /healthz, /chaos endpoints
│   └── requirements.txt
├── templates/                 <- Jinja2 templates (rendered by the CLI)
│   ├── docker-compose.yml.j2
│   └── nginx.conf.j2
├── swiftdeploy_cli/           <- the CLI package
│   ├── __init__.py
│   └── core.py                   all subcommand logic lives here
├── scripts/                   <- Windows installer / uninstaller helpers
├── Dockerfile                 <- builds the API image
├── nginx.Dockerfile           <- builds a non-root nginx image
├── manifest.yaml              <- the only deployment file you edit by hand
├── pyproject.toml             <- packages the CLI as `swiftdeploy`
├── requirements.txt           <- runtime deps for the CLI
├── swiftdeploy                <- shebang-style launcher (Linux/macOS)
├── swiftdeploy.cmd            <- launcher (Windows)
└── README.md
```

These two files are **generated**, not committed:

```
docker-compose.yml
nginx.conf
```

`swiftdeploy init` writes them. `swiftdeploy teardown --clean` deletes them. They're gitignored.

---

## Prerequisites

Before you do anything else, make sure you have:

| Tool           | Version         | Verify                   |
| -------------- | --------------- | ------------------------ |
| Docker Engine  | 20.10 or newer  | `docker --version`       |
| Docker Compose | v2 (the plugin) | `docker compose version` |
| Python         | 3.10 or newer   | `python --version`       |
| pip            | recent          | `pip --version`          |

On Windows, **Docker Desktop** ships both Docker and Compose v2. On Linux you may need to install the `docker-compose-plugin` package separately.

You do **not** need a Linux machine — everything works on Windows (PowerShell), macOS, and Linux. Examples below show both `bash` and PowerShell syntax where they differ.

---

## Installation

There are three ways to use the CLI. Pick the one that fits your situation.

### Option 1 — No install (use the launcher)

The repo ships with a launcher script in the project root. From inside the project directory:

**Linux / macOS**

```bash
chmod +x swiftdeploy
./swiftdeploy --help
```

**Windows (PowerShell)**

```powershell
.\swiftdeploy.cmd --help
```

This works without installing anything globally — the launcher just runs the bundled Python module.

### Option 2 — Editable install (recommended for development)

From the project root:

```bash
pip install -e .
swiftdeploy --help
```

You can now run `swiftdeploy` from anywhere on the system. Code changes inside `swiftdeploy_cli/` take effect immediately, with no reinstall.

If `swiftdeploy` is not found after install on Windows, the entry-point script lives in `%APPDATA%\Python\Python3xx\Scripts`. Add that to your PATH or run it by absolute path:

```powershell
& "$env:APPDATA\Python\Python314\Scripts\swiftdeploy.exe" --help
```

### Option 3 — Global install on Windows (PowerShell helper)

```powershell
.\scripts\install-swiftdeploy.ps1
swiftdeploy --help
```

To remove later:

```powershell
.\scripts\uninstall-swiftdeploy.ps1
```

### Option 4 — Install straight from GitHub

After pushing your repo:

```bash
pip install "git+https://github.com/<your-username>/<your-repo>.git"
```

---

## Build the container images

The manifest references two images that don't exist on Docker Hub — you have to build them locally first.

```bash
docker build -t swift-deploy-1-node:latest .
docker build -t swiftdeploy-nginx:latest -f nginx.Dockerfile .
```

**Why a custom Nginx image?** The base `nginx:latest` image ships `/var/log/nginx/access.log` and `/var/log/nginx/error.log` as symlinks to `/dev/stdout` and `/dev/stderr`, and the directory itself is owned by root. We want to:

- Run the container as a non-root user (uid 101) for security.
- Have access logs land as **real files** inside the named `nginx-logs` volume so they persist between restarts.

Both at once is impossible with the stock image. Our `nginx.Dockerfile` is a four-line layer on top of `nginx:latest` that strips the symlinks and chowns the log directory to uid 101. That's it.

Verify the images are present and small enough:

```bash
docker image ls swift-deploy-1-node swiftdeploy-nginx
```

Both should be well under 300 MB.

---

## The manifest explained

`manifest.yaml` is the **only** file you should edit by hand. Every value below maps to something in the generated configs. If you change a value here and rerun `swiftdeploy init`, the corresponding line in `docker-compose.yml` or `nginx.conf` changes too.

```yaml
services:
  image: swift-deploy-1-node:latest # what to run for the API
  port: 3000 # the port the API listens on inside the container
  mode: stable # "stable" or "canary" — drives the MODE env var
  version: 1.0.0 # surfaced via APP_VERSION and the / endpoint
  restart_policy: unless-stopped # docker-compose restart policy
  container_name: swiftdeploy-api # name of the running container

nginx:
  image: swiftdeploy-nginx:latest # the custom hardened nginx image
  port: 8080 # the host port users hit (only port exposed)
  proxy_timeout: 30 # connect/read/send timeout in seconds
  contact: ukemeetim2222@gmail.com # printed in JSON 5xx error bodies
  container_name: swiftdeploy-nginx

network:
  name: swiftdeploy-net # the docker bridge network the containers share
  driver_type: bridge # the docker network driver
```

**Required fields** (`swiftdeploy validate` will fail if any are missing or empty): `services.image`, `services.port`, `nginx.image`, `nginx.port`, `network.name`, `network.driver_type`. `services.mode` must be either `stable` or `canary`.

---

## The CLI subcommands

```
swiftdeploy <command> [options]
```

### `init`

Renders the templates against the current manifest and writes `docker-compose.yml` and `nginx.conf` to the project root. Idempotent — running it twice is the same as running it once.

```bash
swiftdeploy init
```

Use this when you change the manifest or pull new template changes. The generated files **always** reflect the manifest at the moment you ran `init`.

### `validate`

Runs five pre-flight checks and exits non-zero if any fails. This is what you run before `deploy` (and what the grader runs).

```bash
swiftdeploy validate
```

| #   | Check                                                                                                     |
| --- | --------------------------------------------------------------------------------------------------------- |
| 1   | `manifest.yaml` exists and is valid YAML                                                                  |
| 2   | All required fields are present and non-empty                                                             |
| 3   | Both Docker images referenced in the manifest exist locally                                               |
| 4   | The Nginx port is not already bound on the host                                                           |
| 5   | The generated `nginx.conf` is syntactically valid (verified by running `nginx -t` inside the nginx image) |

Each check prints a `PASS` or `FAIL` line. Exit code is 0 if all pass, 1 otherwise.

### `deploy`

Runs `init`, brings the stack up with `docker compose up -d`, and **blocks** until `GET /healthz` returns 200 (or 60 seconds elapse, whichever comes first).

```bash
swiftdeploy deploy
```

If the health check times out, the command exits with a non-zero status and prints the last error it saw.

### `promote`

Switches the API between `stable` and `canary` mode without touching Nginx.

```bash
swiftdeploy promote canary
swiftdeploy promote stable
```

In one command, `promote`:

1. Updates `services.mode` in `manifest.yaml` in place.
2. Regenerates `docker-compose.yml` so the container's `MODE` env var matches.
3. Restarts **only** the API container (`docker compose up -d --no-deps --force-recreate api`).
4. Polls `/healthz` until it sees the new mode (or until 60 seconds elapse).

Nginx and the named volume are untouched, so existing access logs aren't lost.

### `teardown`

Removes all containers, the named volume, and the network created by `docker compose`.

```bash
swiftdeploy teardown            # remove the stack
swiftdeploy teardown --clean    # also delete the generated docker-compose.yml and nginx.conf
```

---

## The API service

A single Flask app in [app/main.py](app/main.py). The same image runs in both stable and canary mode — behaviour changes based on the `MODE` env var injected by Compose.

### Endpoints

| Method | Path       | Purpose                                                                 |
| ------ | ---------- | ----------------------------------------------------------------------- |
| GET    | `/`        | Welcome message with current mode, version, and ISO-8601 timestamp.     |
| GET    | `/healthz` | Liveness probe — returns `status: ok` and process uptime in seconds.    |
| POST   | `/chaos`   | Inject failure for testing. **Only enabled in canary mode** (else 403). |

### Modes

- **stable** — Normal behaviour. `/chaos` returns 403. No `X-Mode` header.
- **canary** — Same code paths, but every response carries `X-Mode: canary`, and `/chaos` is live.

### Chaos endpoint

`POST /chaos` accepts JSON. Use it (in canary only) to simulate degraded conditions:

```bash
# Make every subsequent request sleep N seconds before responding.
curl -X POST http://localhost:8080/chaos \
  -H "Content-Type: application/json" \
  -d '{"mode":"slow","duration":2}'

# Return HTTP 500 on roughly RATE fraction of subsequent requests.
curl -X POST http://localhost:8080/chaos \
  -H "Content-Type: application/json" \
  -d '{"mode":"error","rate":0.5}'

# Cancel any active chaos.
curl -X POST http://localhost:8080/chaos \
  -H "Content-Type: application/json" \
  -d '{"mode":"recover"}'
```

> **PowerShell tip:** when sending JSON via `curl.exe` on Windows, escape the inner quotes:
> `-d '{\"mode\":\"recover\"}'`

---

## Architecture & design choices

### One port in, one port out

Only the Nginx host port (default `8080`) is published. The API's port is `expose`d on the internal Docker network but never bound to the host. All traffic into the API goes **through** Nginx. This is how a real production deployment is shaped.

### Non-root containers

Both containers drop all Linux capabilities and run as a non-root user:

- **API** — uid `10001`, declared in the Dockerfile (`USER 10001:10001`).
- **Nginx** — uid `101`, declared in the Compose template (`user: "101:101"`).

Both also set `security_opt: no-new-privileges:true`, so even a successful exploit can't gain capabilities.

### Custom Nginx image

`nginx.Dockerfile` exists for one reason: the official `nginx:latest` image cannot simultaneously **(a)** run as uid 101 and **(b)** write logs to a mounted named volume, because `/var/log/nginx` is root-owned. We chown it at build time and replace the symlink trick with real log files.

### Health-gated startup

The Compose template defines a Docker-level healthcheck on the API (curl-equivalent against `/healthz` every 10s). The Nginx service uses `depends_on: api: condition: service_healthy`, so Nginx never starts proxying to a not-yet-ready upstream.

### Nginx behaviour

The rendered `nginx.conf` does:

- `listen <nginx.port>` (from manifest)
- All proxy timeouts driven by `nginx.proxy_timeout`
- `add_header X-Deployed-By swiftdeploy always` on every response
- `proxy_pass_header X-Mode` so the canary-mode header reaches the client
- JSON bodies on 502 / 503 / 504, e.g.
  `{"error":"bad gateway","code":"502","service":"swiftdeploy-api","contact":"devops@example.com"}`
- Access logs in the format
  `$time_iso8601 | $status | ${request_time}s | $upstream_addr | $request`

---

## End-to-end walkthrough

This is the path from a fresh clone to a tested running stack. Run it once to convince yourself everything works.

### 1. Build the images

```bash
docker build -t swift-deploy-1-node:latest .
docker build -t swiftdeploy-nginx:latest -f nginx.Dockerfile .
```

### 2. Validate the workspace

```bash
swiftdeploy validate
```

You should see five `PASS` lines.

### 3. Deploy

```bash
swiftdeploy deploy
```

The command exits within ~10–15 seconds and prints `Deployment is healthy`.

### 4. Smoke-test through Nginx

```bash
curl -i http://localhost:8080/
curl -i http://localhost:8080/healthz
```

Look for:

- `HTTP/1.1 200 OK`
- `X-Deployed-By: swiftdeploy` header on every response
- `mode: "stable"` in the JSON body
- **No** `X-Mode` header (we're in stable mode)

Confirm the API port is **not** reachable from the host:

```bash
curl http://localhost:3000/healthz   # connection refused — exactly what we want
```

### 5. Promote to canary

```bash
swiftdeploy promote canary
curl -i http://localhost:8080/healthz
```

The response now includes `X-Mode: canary`, and `/chaos` is live.

### 6. Try the chaos endpoint

```bash
curl -X POST http://localhost:8080/chaos \
  -H "Content-Type: application/json" \
  -d '{"mode":"error","rate":0.5}'

# Send 100 requests and count by status — expect ~50/50.
for i in $(seq 1 100); do curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/; done | sort | uniq -c

# Recover.
curl -X POST http://localhost:8080/chaos \
  -H "Content-Type: application/json" \
  -d '{"mode":"recover"}'
```

### 7. Promote back to stable

```bash
swiftdeploy promote stable
curl -i http://localhost:8080/healthz   # X-Mode header is gone again
```

### 8. View access logs

```bash
docker exec swiftdeploy-nginx cat /var/log/nginx/access.log
```

Each line should look like:

```
2026-05-03T12:14:02+00:00 | 200 | 0.004s | 172.18.0.2:3000 | GET /healthz HTTP/1.1
```

### 9. Tear it all down

```bash
swiftdeploy teardown            # stop containers, remove volume + network
swiftdeploy teardown --clean    # also delete the generated configs
```

The grader's flow is essentially this sequence — start fresh, build, validate, deploy, promote, teardown.

---

## Viewing logs

Three different log streams are useful while debugging:

```bash
# Nginx access log (the spec-formatted access log inside the named volume)
docker exec swiftdeploy-nginx cat /var/log/nginx/access.log

# Nginx error log + entrypoint stderr
docker logs swiftdeploy-nginx

# API stdout (gunicorn access log + Flask exceptions)
docker logs swiftdeploy-api

# All containers managed by Compose, follow mode
docker compose logs -f
```

The named Docker volume `nginx-logs` persists access logs across container restarts. It is removed by `swiftdeploy teardown`.

---

## Troubleshooting

### `validate` says "Docker images missing locally"

You haven't built one of the images. Re-run the build commands in [Build the container images](#build-the-container-images).

### `validate` says "Nginx port is already bound"

Something on your machine is using port 8080. Either stop it or change `nginx.port` in `manifest.yaml` to a free port and re-run `swiftdeploy init`.

### `deploy` hangs or times out at the health check

The API container probably crashed. Check its logs:

```bash
docker logs swiftdeploy-api
```

Common causes:

- Image was rebuilt with a syntax error in `app/main.py`.
- The `MODE` value isn't one of `stable` or `canary`.

### The chaos `error` rate is wildly off (e.g. 18% instead of 50%)

The CHAOS state lives in process memory. If gunicorn is running multiple workers, each worker has its own copy and only the one that received the `POST /chaos` knows about it. The Dockerfile pins gunicorn to a single worker with multiple threads (`--workers 1 --threads 4`) precisely to avoid this. If you've edited it back to multiple workers, the chaos rate becomes a fraction of what you set.

### Nginx restarts in a loop with "permission denied" on the access log

This means the named volume was created against the **stock** `nginx:latest` image (when the directory is still root-owned). Tear down with `--clean` so the volume is recreated against the custom image:

```bash
swiftdeploy teardown --clean
swiftdeploy deploy
```

### `docker compose exec` hangs in PowerShell

Use `-T` to disable TTY allocation, or skip Compose entirely:

```powershell
docker compose exec -T nginx cat /var/log/nginx/access.log
docker exec swiftdeploy-nginx cat /var/log/nginx/access.log
```

### Anything else

Run `swiftdeploy validate` first — it surfaces most preventable issues before they bite you.

---

## What you can edit vs. what's generated

| File                             | Edit by hand?                  | Notes                                              |
| -------------------------------- | ------------------------------ | -------------------------------------------------- |
| `manifest.yaml`                  | **Yes**                        | The single source of truth.                        |
| `templates/*.j2`                 | Yes (advanced)                 | Edit if you need to change rendered shape.         |
| `app/main.py`                    | Yes                            | Application code. Rebuild the image after editing. |
| `Dockerfile`, `nginx.Dockerfile` | Yes                            | Rebuild affected image after editing.              |
| `swiftdeploy_cli/core.py`        | Yes                            | Editable install picks changes up live.            |
| `docker-compose.yml`             | **No** — overwritten by `init` | Generated.                                         |
| `nginx.conf`                     | **No** — overwritten by `init` | Generated.                                         |

If the generated config doesn't match what you want, fix the **manifest** or the **template** — never the generated file. Otherwise the next `swiftdeploy init` (or the grader running it) will silently undo your edit.

---

## Submission notes (HNG Stage 4A)

- Public GitHub repo: includes `manifest.yaml`, `swiftdeploy`, `app/`, `templates/`, `Dockerfile`, `nginx.Dockerfile`, `README.md`.
- Screenshots in Google Drive: `validate` output, `deploy` output, `promote` + `/healthz` confirmation, generated `nginx.conf` and `docker-compose.yml`, and the Nginx access log output.
- Generated files (`docker-compose.yml`, `nginx.conf`) are gitignored. The grader regenerates them with `swiftdeploy init`.
