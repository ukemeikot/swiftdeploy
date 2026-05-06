# SwiftDeploy

> A declarative CLI that builds, runs, validates, promotes, and **policy-gates** a containerised web stack from a single YAML manifest.

SwiftDeploy is the answer to a simple question: **what if the only file you had to write to deploy an app was a description of what you wanted?** No hand-edited Compose files. No copy-pasted Nginx snippets. The CLI templates everything else and refuses to deploy or promote when policy says no.

This is the HNG DevOps Stage 4 submission (parts **A** and **B**). Stage 4A built the engine — manifest → templates → lifecycle. Stage 4B added the **eyes** (`/metrics` + a live status dashboard), the **brain** (an OPA sidecar that owns every allow/deny call), and the **memory** (`history.jsonl` + `audit_report.md`).

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
11. [Observability & metrics](#observability--metrics)
12. [Policy gates with OPA](#policy-gates-with-opa)
13. [Architecture & design choices](#architecture--design-choices)
14. [End-to-end walkthrough](#end-to-end-walkthrough)
15. [Viewing logs](#viewing-logs)
16. [Troubleshooting](#troubleshooting)
17. [What you can edit vs. what's generated](#what-you-can-edit-vs-whats-generated)

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
                      │ Jinja2 + JSON renders
                      ▼
   ┌────────────────────┬────────────────────────┐
   ▼                    ▼                        ▼
docker-compose.yml   nginx.conf       generated/opa-data.json
   │                    │                        │
   │  docker compose up │     mounted into       │  mounted into
   ▼                    ▼     nginx container    ▼     opa container
                                       ┌────────────────┐
 ┌──────────────┐    ┌──────────────┐  │ swiftdeploy-   │
 │ swiftdeploy- │ ←──│ swiftdeploy- │  │     opa        │
 │     api      │    │    nginx     │  │ (policy engine)│◄── 127.0.0.1:8181
 │ (Flask)      │    │ (proxy)      │  └────────────────┘    (host-only)
 └──────────────┘    └──────────────┘            ▲
        │                    │                   │ POST /v1/data/<domain>/decision
        └─── public-net ─────┘                   │
                            │                    │
                            └─── policy-net ─────┘
                                  (internal)
                                  ▲
                              localhost:8080  ◄── users
```

**Key idea:** every generated file is a *derived artifact*. The CLI deletes and regenerates them on every `swiftdeploy init`. If you find yourself editing them directly, stop — change the manifest or the template instead. **All allow/deny decisions are made by OPA, never by Python.**

---

## The stack at a glance

| Layer           | Tech                                                       |
| --------------- | ---------------------------------------------------------- |
| API service     | Python 3.12 + Flask + gunicorn + prometheus-client         |
| Reverse proxy   | Custom Nginx image (`FROM nginx:latest`)                   |
| Policy engine   | Open Policy Agent (`openpolicyagent/opa:1.16.1-static`)  |
| Orchestration   | Docker + Docker Compose v2 (two networks: public + policy) |
| Templating      | Jinja2 (configs) + JSON (OPA data)                         |
| CLI             | Python 3.10+ (argparse, urllib, PyYAML, psutil, rich)      |
| Manifest format | YAML                                                       |

---

## Project layout

```
.
├── app/                       <- Flask API source
│   ├── main.py                   /, /healthz, /chaos, /metrics
│   └── requirements.txt
├── templates/                 <- Jinja2 templates (rendered by init)
│   ├── docker-compose.yml.j2
│   └── nginx.conf.j2
├── policies/                  <- Rego policies (loaded into OPA)
│   ├── infrastructure.rego       pre-deploy gate (disk/CPU/mem)
│   └── canary_safety.rego        pre-promote gate (error rate, p99)
├── swiftdeploy_cli/           <- the CLI package
│   ├── core.py                   subcommands + lifecycle
│   ├── opa.py                    OPA HTTP client + decision dataclass
│   ├── host_stats.py             pre-deploy host readings (psutil)
│   ├── metrics.py                /metrics scrape + windowed stats
│   ├── history.py                append-only audit log
│   ├── dashboard.py              live `swiftdeploy status` view
│   └── audit.py                  `audit_report.md` renderer
├── scripts/                   <- Windows installer / uninstaller helpers
├── Dockerfile                 <- builds the API image
├── nginx.Dockerfile           <- builds the non-root nginx image
├── manifest.yaml              <- the only deployment file you edit by hand
├── pyproject.toml             <- packages the CLI as `swiftdeploy`
├── requirements.txt
├── swiftdeploy                <- launcher (Linux/macOS)
├── swiftdeploy.cmd            <- launcher (Windows)
├── TEST.md                    <- full test plan (see file)
└── README.md
```

These files are **generated** at runtime, not committed:

```
docker-compose.yml      ← swiftdeploy init
nginx.conf              ← swiftdeploy init
generated/opa-data.json ← swiftdeploy init  (OPA's data document)
history.jsonl           ← swiftdeploy status / deploy / promote
audit_report.md         ← swiftdeploy audit
```

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
docker image ls --filter "reference=swift-deploy-1-node" --filter "reference=swiftdeploy-nginx"
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

**Required fields** (`swiftdeploy validate` will fail if any are missing or empty): `services.image`, `services.port`, `nginx.image`, `nginx.port`, `network.name`, `network.driver_type`, `opa.image`, `opa.port`, `policies.infrastructure`, `policies.canary_safety`. `services.mode` must be either `stable` or `canary`.

### Stage 4B additions

```yaml
opa:
  image: openpolicyagent/opa:1.16.1-static # pinned for reproducibility
  port: 8181 # bound to 127.0.0.1 only — never exposed publicly
  container_name: swiftdeploy-opa
  policy_network: swiftdeploy-policy-net # internal docker network

policies:
  infrastructure: # pre-deploy gate
    min_disk_free_gb: 10
    max_cpu_load: 2.0
    max_mem_used_pct: 0.90
    disk_path: /
  canary_safety: # pre-promote gate
    max_error_rate: 0.01 # 1%
    max_p99_latency_ms: 500
    window_seconds: 30
    min_sample_requests: 5

audit:
  history_path: history.jsonl
  report_path: audit_report.md
```

Threshold values live **only** in the manifest. They are rendered to `generated/opa-data.json` by `swiftdeploy init` and loaded by OPA as `data.thresholds.*`. Rego files reference them — they never hardcode numbers.

---

## The CLI subcommands

```
swiftdeploy <command> [options]
```

### `init`

Renders templates and the OPA data file against the current manifest, writing `docker-compose.yml`, `nginx.conf`, and `generated/opa-data.json` to the project root. Idempotent — running it twice is the same as running it once.

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

Runs `init`, brings up **OPA first**, runs the **infrastructure pre-deploy gate** (host disk / CPU / memory readings → OPA), and only on `allow` brings up the rest of the stack and blocks until `GET /healthz` returns 200 (or 60 seconds elapse).

```bash
swiftdeploy deploy            # gated
swiftdeploy deploy --force    # bypass denials, recorded in history.jsonl
```

| Outcome                   | Exit code | What you see                                                |
| ------------------------- | --------- | ----------------------------------------------------------- |
| Allowed and healthy       | 0         | `[infrastructure] ALLOW` then `Deployment is healthy`       |
| Denied by policy          | 2         | `[infrastructure] DENY` with rule, observed value, threshold |
| OPA unreachable           | 1         | `policy engine offline (no container?)`                     |
| OPA timeout               | 1         | `policy engine timeout: ...`                                |
| OPA bad/malformed reply   | 1         | `policy '<x>' returned a malformed decision (no 'allow')`   |
| Health check timeout      | 1         | `health checks did not pass within 60s`                     |

### `promote`

Switches the API between `stable` and `canary` mode without touching Nginx.

```bash
swiftdeploy promote canary    # gated by canary_safety
swiftdeploy promote stable    # always allowed (rollback path)
swiftdeploy promote canary --force   # bypass denial, recorded
```

In one command, `promote`:

1. **(canary only)** Scrapes `/metrics` twice with a `policies.canary_safety.window_seconds` gap, computes error rate / p99 / sample count, and queries OPA's `canary_safety` policy. On deny, exits 2.
2. Updates `services.mode` in `manifest.yaml` in place.
3. Regenerates `docker-compose.yml` so the container's `MODE` env var matches.
4. Restarts **only** the API container (`docker compose up -d --no-deps --force-recreate api`).
5. Polls `/healthz` until it sees the new mode (or until 60 seconds elapse).

`promote stable` skips the gate entirely so a failing canary can always be rolled back. Nginx and the named volume are untouched.

### `teardown`

Removes all containers, both networks, and the named volume created by `docker compose`.

```bash
swiftdeploy teardown            # remove the stack
swiftdeploy teardown --clean    # also delete generated docker-compose.yml, nginx.conf, generated/opa-data.json
```

### `status`

Live terminal dashboard. Scrapes `/metrics` and queries OPA every `--interval` seconds (default 2s), and **appends every scrape to `history.jsonl`** as the audit trail. Ctrl-C to exit.

```bash
swiftdeploy status
swiftdeploy status --interval 1
```

The dashboard shows two panels:

- **Live metrics** — current mode, active chaos, uptime, req/s, error rate, p99 latency, sample count.
- **Policy compliance** — per-domain `PASS`/`FAIL` rows with rule names and reasons.

### `audit`

Reads `history.jsonl` and writes a GitHub-Flavored Markdown report.

```bash
swiftdeploy audit
swiftdeploy audit --output reports/run-2026-05-06.md
```

The report has three sections:

- **Timeline** — consecutive same-state scrapes collapsed into rows of `(start, end, mode + chaos)`.
- **Policy violations** — every denial recorded during the run.
- **Forced overrides** — any time `--force` was used (with command and reason).

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

## Observability & metrics

The Flask app exposes Prometheus metrics at `GET /metrics` (text format `0.0.4`). Every request is wrapped in `before_request` / `after_request` middleware that records:

| Metric                              | Type      | Labels                       | What it tracks                       |
| ----------------------------------- | --------- | ---------------------------- | ------------------------------------ |
| `http_requests_total`               | Counter   | `method`, `path`, `status_code` | Throughput and per-status counts     |
| `http_request_duration_seconds`     | Histogram | `method`, `path`             | Latency, default Prometheus buckets  |
| `app_uptime_seconds`                | Gauge     | —                            | Process uptime in seconds            |
| `app_mode`                          | Gauge     | —                            | `0` = stable, `1` = canary           |
| `chaos_active`                      | Gauge     | —                            | `0` = none, `1` = slow, `2` = error  |

`/metrics` and `/healthz` are excluded from the request counter so scrape volume doesn't drown the canary-safety window.

```bash
curl http://localhost:8080/metrics | grep -E '^(http_|app_|chaos_)'
```

The CLI scrapes this same endpoint to compute windowed stats for `swiftdeploy status` and `swiftdeploy promote canary` (the canary gate). Histograms are cumulative — windowed p99/error rate is computed from a delta between two snapshots taken `policies.canary_safety.window_seconds` apart.

---

## Policy gates with OPA

SwiftDeploy ships with an Open Policy Agent sidecar. **The CLI never decides allow/deny on its own** — it ships an input document to OPA, OPA evaluates the relevant policy package, and the CLI surfaces the decision and (on deny) blocks the lifecycle action. This gives you:

- **Auditability** — every decision is a structured object with a `rule`, a `message`, an `observed` value, and a `threshold`. No bare booleans.
- **Hot-reloadable rules** — OPA reloads `policies/` automatically. Edit a `.rego` file, save, and the next gate query uses the new logic without restarting anything.
- **Domain isolation** — each policy package owns one question. `infrastructure` answers "is this host safe to deploy onto?". `canary_safety` answers "is this canary healthy enough to promote?". Changing one never touches the other.

### How a gate query works

```
swiftdeploy deploy
   │
   ├─ scrape host stats (psutil)         ← input.host = {disk_free_gb, cpu_load_1m, mem_used_pct}
   ├─ POST http://127.0.0.1:8181/v1/data/infrastructure/decision
   │      with {"input": {...}}
   │
   ├─ OPA loads /policies/*.rego + /data/opa-data.json
   ├─ evaluates `data.infrastructure.decision`
   └─ returns {"result": {"allow": false, "violations": [...], "domain": "infrastructure"}}
   │
   └─ on allow → docker compose up -d → /healthz → done
      on deny  → render violations → exit 2 (deploy did NOT happen)
```

### The two policies shipped

| File | Package | Asks | Inputs | Thresholds (from manifest) |
| ---- | ------- | ---- | ------ | --------------------------- |
| `policies/infrastructure.rego` | `infrastructure` | "Is the host safe to deploy onto?" | `host.{disk_free_gb, cpu_load_1m, mem_used_pct}` | `min_disk_free_gb`, `max_cpu_load`, `max_mem_used_pct` |
| `policies/canary_safety.rego` | `canary_safety` | "Is the canary healthy enough to promote?" | `metrics.{error_rate, p99_latency_ms, sample_requests}`, `target_mode` | `max_error_rate`, `max_p99_latency_ms`, `min_sample_requests`, `window_seconds` |

### Where the thresholds come from

`generated/opa-data.json` is rendered by `swiftdeploy init` from `manifest.yaml`:

```json
{
  "thresholds": {
    "infrastructure": { "min_disk_free_gb": 10, "max_cpu_load": 2.0, "max_mem_used_pct": 0.9, "disk_path": "/" },
    "canary_safety":  { "max_error_rate": 0.01, "max_p99_latency_ms": 500, "window_seconds": 30, "min_sample_requests": 5 }
  }
}
```

Inside Rego, every threshold is read with `data.thresholds.<domain>.<field>`. There are **no numeric literals** in `.rego` files — change a number in the manifest, re-run `swiftdeploy init`, and OPA picks up the new value on its next reload.

### Network isolation

OPA runs on a **separate** Docker network (`policy-net`); nginx is only on `public-net`. Since nginx cannot route to a container it shares no network with, OPA is unreachable through the public ingress regardless of any flag. The OPA port is also host-bound to `127.0.0.1:8181` only — never `0.0.0.0` — so the CLI on the host can reach it but nothing on a remote machine can. Two consequences worth checking yourself:

```bash
# OPA reachable from the host (CLI uses this)
curl http://127.0.0.1:8181/health   # → 200 OK

# OPA NOT reachable through nginx (the public ingress)
curl http://localhost:8080/v1/data/infrastructure/decision   # → 404 Not Found
```

If those don't behave as above, the topology is broken and the "no leakage" submission requirement is failing.

### Failure handling

The CLI distinguishes every OPA failure mode and prints a different message — never a stack trace:

| Failure                       | Message                                                       |
| ----------------------------- | ------------------------------------------------------------- |
| Container not running         | `policy engine offline (no container?). Start the stack first or pass --force.` |
| Connection timeout            | `policy engine timeout: ... after 3s`                          |
| HTTP 5xx from OPA             | `policy engine unhealthy: ...`                                |
| Rego compile or runtime error | `policy '<x>' returned a malformed decision...`               |
| Non-JSON response             | `policy engine returned an unparseable body...`               |

`--force` (on `deploy` and `promote`) lets an operator bypass the gate in an emergency. Every override is appended to `history.jsonl` with the reason for the audit trail.

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

### 9. Watch the live dashboard during chaos

In one terminal:

```bash
swiftdeploy promote canary
swiftdeploy status
```

In a second terminal, induce errors:

```bash
curl -X POST http://localhost:8080/chaos -H "Content-Type: application/json" \
  -d '{"mode":"error","rate":0.5}'

# Generate load
for i in $(seq 1 200); do curl -s -o /dev/null http://localhost:8080/; done
```

Watch the dashboard's `error rate` and `chaos_active` rows climb in real time. Then try to promote back to canary — wait, you're already canary. Try the **inverse** — recover, then attempt to promote canary→canary again with chaos still injected:

```bash
# In terminal #2, try to promote while error rate is high (use a fresh attempt while chaos is still active):
swiftdeploy promote canary       # → DENY: max_error_rate
```

`promote stable` will always succeed (rollback path):

```bash
swiftdeploy promote stable
```

### 10. Render the audit report

```bash
swiftdeploy audit
cat audit_report.md
```

You should see a Timeline table covering the run, a Violations table for any policy denials, and a Forced overrides section if you used `--force` anywhere.

### 11. Tear it all down

```bash
swiftdeploy teardown            # stop containers, remove volumes + networks
swiftdeploy teardown --clean    # also delete the generated configs
```

The grader's flow is essentially this sequence — start fresh, build, validate, deploy (gated), promote (gated), exercise chaos, audit, teardown.

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

## Submission notes (HNG Stage 4)

**Stage 4A**

- Public GitHub repo: includes `manifest.yaml`, `swiftdeploy`, `app/`, `templates/`, `Dockerfile`, `nginx.Dockerfile`, `README.md`.
- Screenshots in Google Drive: `validate` output, `deploy` output, `promote` + `/healthz` confirmation, generated `nginx.conf` and `docker-compose.yml`, and the Nginx access log output.
- Generated files (`docker-compose.yml`, `nginx.conf`) are gitignored. The grader regenerates them with `swiftdeploy init`.

**Stage 4B**

- Adds: `policies/`, `swiftdeploy_cli/{opa,host_stats,metrics,history,dashboard,audit}.py`, OPA service in compose template, `policies` and `opa` blocks in manifest.
- Hard gate: filling host disk causes `swiftdeploy deploy` to refuse with `[infrastructure] DENY — min_disk_free_gb: ...` and exit code 2.
- No leakage: OPA's `:8181` is bound to `127.0.0.1` only, sits on `policy-net` (`internal: true`), and is unreachable through the public nginx port.
- Audit: `history.jsonl` is appended on every gate query, scrape, decision, and force-override; `swiftdeploy audit` renders it as GFM `audit_report.md`.
- Blog post: see [BLOG.md placeholder] — published walkthrough with architecture diagram, OPA rationale, chaos demo, and lessons learned.
- See [TEST.md](TEST.md) for the full installation + test plan.
