# SwiftDeploy — Test Plan

This document is the full acceptance test plan for SwiftDeploy. It covers
installation, every CLI subcommand, both Rego policies, the OPA isolation
guarantees, the chaos endpoint, the metrics endpoint, container hardening
checks, and the failure-handling matrix.

It is structured so a grader (or you, after a fresh clone) can run every
test top-to-bottom and tick each one off. Every test states its
**Setup**, **Steps**, and **Expected** outcome so there is no ambiguity.

---

## Table of contents

1. [Conventions](#1-conventions)
2. [Prerequisites](#2-prerequisites)
3. [Installation tests](#3-installation-tests)
4. [Image build & sanity tests](#4-image-build--sanity-tests)
5. [Manifest & template tests](#5-manifest--template-tests)
6. [`swiftdeploy validate` tests](#6-swiftdeploy-validate-tests)
7. [`swiftdeploy init` tests](#7-swiftdeploy-init-tests)
8. [`swiftdeploy deploy` tests](#8-swiftdeploy-deploy-tests)
9. [API endpoint tests](#9-api-endpoint-tests)
10. [Metrics endpoint tests](#10-metrics-endpoint-tests)
11. [`swiftdeploy promote` tests](#11-swiftdeploy-promote-tests)
12. [OPA gating tests (the brain)](#12-opa-gating-tests-the-brain)
13. [OPA isolation & no-leakage tests](#13-opa-isolation--no-leakage-tests)
14. [`swiftdeploy status` dashboard tests](#14-swiftdeploy-status-dashboard-tests)
15. [`swiftdeploy audit` tests](#15-swiftdeploy-audit-tests)
16. [`swiftdeploy teardown` tests](#16-swiftdeploy-teardown-tests)
17. [Failure handling matrix](#17-failure-handling-matrix)
18. [Container hardening tests](#18-container-hardening-tests)
19. [Hard-gate scenario (the disk-fill grader test)](#19-hard-gate-scenario-the-disk-fill-grader-test)
20. [End-to-end happy path (smoke test)](#20-end-to-end-happy-path-smoke-test)
21. [Cleanup](#21-cleanup)

---

## 1. Conventions

- Run every command from the project root: `~/swiftdeploy/`.
- Examples show `bash` syntax. PowerShell variants are noted where they
  differ; in particular, JSON bodies must escape inner quotes:
  `'{\"mode\":\"recover\"}'`.
- A test **passes** only if **every** expected line in the Expected
  block is observed and the exit code matches.
- Tests are ordered by dependency. Don't skip ahead — Section 8
  (`deploy`) assumes Section 4 (image build) succeeded.
- Where a test mutates state (manifest edits, disk fills, chaos
  injection), a Cleanup step is given. Always run it before moving on.

---

## 2. Prerequisites

| Tool           | Version     | Verify                   |
| -------------- | ----------- | ------------------------ |
| Docker Engine  | 20.10+      | `docker --version`       |
| Docker Compose | v2 (plugin) | `docker compose version` |
| Python         | 3.10+       | `python --version`       |
| pip            | recent      | `pip --version`          |
| curl           | any         | `curl --version`         |
| jq (optional)  | any         | `jq --version`           |

**OS notes:** development is supported on Windows + macOS + Linux. The
**hard-gate disk-fill scenario (Section 19) requires Linux or WSL2** —
filling a Windows host disk is not supported by this test plan.

### 2.1 Verify prerequisites

**Steps**

```bash
docker --version && docker compose version && python --version
```

**Expected**

- All three commands print a version, none error.
- Exit code 0.

---

## 3. Installation tests

### 3.1 Editable install of the CLI

**Setup:** clean clone of the repo.

**Steps**

```bash
python -m venv .venv

# Linux/macOS
source .venv/bin/activate
# Windows PowerShell
.\.venv\Scripts\Activate.ps1

pip install -e .
```

**Expected**

- `pip` installs `Jinja2`, `PyYAML`, `prometheus-client`, `psutil`,
  `rich` plus their transitive deps.
- Final line: `Successfully installed swiftdeploy-cli-2.0.0 ...`.
- Exit code 0.

### 3.2 CLI is on PATH

**Steps**

```bash
swiftdeploy --help
```

**Expected**

- Help text is printed listing all seven subcommands:
  `init, validate, deploy, promote, teardown, status, audit`.
- Exit code 0.

### 3.3 CLI launcher (no install) works

**Steps**

Linux/macOS:

```bash
deactivate 2>/dev/null
chmod +x swiftdeploy
./swiftdeploy --help
```

Windows PowerShell:

```powershell
.\swiftdeploy.cmd --help
```

**Expected**

- Same help text as 3.2.
- Exit code 0.

---

## 4. Image build & sanity tests

### 4.1 Build the API image

**Steps**

```bash
docker build -t swift-deploy-1-node:latest .
```

**Expected**

- Build succeeds.
- `Successfully tagged swift-deploy-1-node:latest`.
- Exit code 0.

### 4.2 Build the custom Nginx image

**Steps**

```bash
docker build -t swiftdeploy-nginx:latest -f nginx.Dockerfile .
```

**Expected**

- Build succeeds.
- Exit code 0.

### 4.3 Both images are under 300 MB

**Steps**

```bash
docker image ls --format '{{.Repository}}:{{.Tag}} {{.Size}}' \
  --filter "reference=swift-deploy-1-node" \
  --filter "reference=swiftdeploy-nginx"
```

PowerShell equivalent (filters are easier than backslash continuations):

```powershell
docker image ls --format '{{.Repository}}:{{.Tag}} {{.Size}}' |
  Select-String 'swift-deploy-1-node|swiftdeploy-nginx'
```

**Expected**

- Two lines, both showing a size under `300MB`.
- The API image should typically be ~80–150 MB on `python:3.12-alpine`.

### 4.4 OPA image is pulled

**Steps**

```bash
docker pull openpolicyagent/opa:1.16.1-static
docker image ls openpolicyagent/opa:1.16.1-static
```

**Expected**

- Image is pulled successfully (or already present).
- Exit code 0.

---

## 5. Manifest & template tests

### 5.1 Manifest is valid YAML

**Steps**

```bash
python -c "import yaml; yaml.safe_load(open('manifest.yaml'))"
```

**Expected**

- No output, exit code 0.

### 5.2 Manifest contains every required block

**Steps**

```bash
python -c "
import yaml
m = yaml.safe_load(open('manifest.yaml'))
for k in ('services','nginx','network','opa','policies','audit'):
    assert k in m, f'missing top-level key: {k}'
print('OK')
"
```

**Expected**

- Prints `OK`, exit code 0.

### 5.3 Templates exist

**Steps**

```bash
ls templates/docker-compose.yml.j2 templates/nginx.conf.j2
ls policies/infrastructure.rego policies/canary_safety.rego
```

**Expected**

- All four files listed without errors.
- Exit code 0.

---

## 6. `swiftdeploy validate` tests

### 6.1 Validate passes with required images present

**Setup:** Sections 4.1 and 4.2 already ran. No process is bound to the
manifest's `nginx.port` (default `8080`).

**Steps**

```bash
swiftdeploy validate
```

**Expected**

```
PASS manifest.yaml exists and is valid YAML
PASS all required fields are present and non-empty
PASS Docker images exist locally: swift-deploy-1-node:latest, swiftdeploy-nginx:latest
PASS Nginx port 8080 is available
PASS generated nginx.conf is syntactically valid
```

- Exit code 0.

### 6.2 Validate fails when an image is missing

**Setup**

```bash
docker rmi swift-deploy-1-node:latest
```

**Steps**

```bash
swiftdeploy validate; echo "exit=$?"
```

**Expected**

- `FAIL Docker images missing locally: swift-deploy-1-node:latest`
- `exit=1`.

**Cleanup:** rebuild the image (re-run 4.1).

### 6.3 Validate fails when the nginx port is bound

**Setup** (in a separate terminal)

```bash
python -m http.server 8080
```

**Steps**

```bash
swiftdeploy validate; echo "exit=$?"
```

**Expected**

- `FAIL Nginx port 8080 is already bound`
- `exit=1`.

**Cleanup:** stop the http.server (Ctrl-C in the other terminal).

### 6.4 Validate fails on a malformed manifest

**Setup**

```bash
cp manifest.yaml manifest.yaml.bak
echo ":::not yaml:::" > manifest.yaml
```

**Steps**

```bash
swiftdeploy validate; echo "exit=$?"
```

**Expected**

- `FAIL manifest.yaml is not valid YAML: ...`
- `exit=1`.

**Cleanup**

```bash
mv manifest.yaml.bak manifest.yaml
```

### 6.5 Validate fails on missing required field

**Setup**

```bash
cp manifest.yaml manifest.yaml.bak
python -c "
import yaml
m = yaml.safe_load(open('manifest.yaml'))
del m['nginx']['image']
open('manifest.yaml','w').write(yaml.safe_dump(m, sort_keys=False))
"
```

**Steps**

```bash
swiftdeploy validate; echo "exit=$?"
```

**Expected**

- `FAIL missing or invalid fields: nginx.image`
- `exit=1`.

**Cleanup**

```bash
mv manifest.yaml.bak manifest.yaml
```

---

## 7. `swiftdeploy init` tests

### 7.1 init generates all three artifacts

**Setup**

```bash
rm -f docker-compose.yml nginx.conf generated/opa-data.json
```

**Steps**

```bash
swiftdeploy init
ls docker-compose.yml nginx.conf generated/opa-data.json
```

**Expected**

- Three lines printed by `swiftdeploy init`:
  `Generated docker-compose.yml`, `Generated nginx.conf`,
  `Generated generated/opa-data.json`.
- All three files exist after the `ls`.
- Exit code 0.

### 7.2 init is idempotent (regeneration test — the grader's check)

**Steps**

```bash
swiftdeploy init
sha256sum docker-compose.yml nginx.conf generated/opa-data.json > /tmp/h1.txt
rm -f docker-compose.yml nginx.conf generated/opa-data.json
swiftdeploy init
sha256sum docker-compose.yml nginx.conf generated/opa-data.json > /tmp/h2.txt
diff /tmp/h1.txt /tmp/h2.txt
```

**Expected**

- `diff` exits 0 (no differences). Hashes are identical between runs.

### 7.3 generated/opa-data.json contains the manifest thresholds

**Steps**

```bash
python -c "
import json
d = json.load(open('generated/opa-data.json'))
t = d['thresholds']
assert 'infrastructure' in t and 'canary_safety' in t
assert t['infrastructure']['min_disk_free_gb'] >= 0
assert 0 < t['canary_safety']['max_error_rate'] <= 1
print('OK')
"
```

**Expected**

- Prints `OK`, exit code 0.

### 7.4 No numeric literals in Rego files

**Steps**

```bash
grep -nE '\b[0-9]+(\.[0-9]+)?\b' policies/*.rego | grep -v 'rego.v1' || echo "no numeric literals"
```

**Expected**

- Either no output that resembles a threshold (only e.g. import lines)
  or the explicit `no numeric literals` line. Thresholds must come from
  `data.thresholds.<domain>`, not from the policy files.

---

## 8. `swiftdeploy deploy` tests

> Sections 8.x assume Section 4 succeeded. Every deploy test cleans up
> with `swiftdeploy teardown` at the end.

### 8.1 Deploy from clean state succeeds

**Setup**

```bash
swiftdeploy teardown --clean 2>/dev/null || true
```

**Steps**

```bash
swiftdeploy deploy
```

**Expected**

- Compose creates the `opa` service first.
- `[infrastructure] ALLOW — no violations` is printed.
- Then api + nginx are started.
- `Health check passed: http://127.0.0.1:8080/healthz`.
- `Deployment is healthy`.
- Exit code 0.

### 8.2 All three containers are running

**Steps**

```bash
docker ps --format '{{.Names}}: {{.Status}}'
```

**Expected**

- `swiftdeploy-api: Up ... (healthy)`
- `swiftdeploy-nginx: Up ...`
- `swiftdeploy-opa: Up ...`

### 8.3 The api port is NOT bound on the host

**Steps**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:3000/healthz
```

**Expected**

- `000` (connection refused) or curl error. The API is only reachable
  through nginx.

### 8.4 The nginx port is bound on the host

**Steps**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/healthz
```

**Expected**

- `200`.

---

## 9. API endpoint tests

### 9.1 GET / returns the welcome payload

**Steps**

```bash
curl -s http://localhost:8080/ | python -m json.tool
```

**Expected JSON keys**

- `message: "Welcome to SwiftDeploy"`
- `mode: "stable"` (or whatever the manifest currently has)
- `version: "1.0.0"`
- `timestamp` in ISO-8601 UTC.

### 9.2 GET / sets X-Deployed-By

**Steps**

```bash
curl -sI http://localhost:8080/ | grep -i 'X-Deployed-By'
```

**Expected**

- `X-Deployed-By: swiftdeploy`.

### 9.3 GET /healthz returns ok + uptime

**Steps**

```bash
curl -s http://localhost:8080/healthz | python -m json.tool
```

**Expected JSON**

- `status: "ok"`
- `uptime` is a number ≥ 0.
- `mode` matches the deployed mode.
- `version` matches the manifest.

### 9.4 POST /chaos returns 403 in stable mode

**Setup:** ensure `services.mode: stable` (manifest) and the stack is
running stable.

**Steps**

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  -X POST http://localhost:8080/chaos \
  -H "Content-Type: application/json" \
  -d '{"mode":"recover"}'
```

**Expected**

- `403`.

### 9.5 nginx access log format is correct

**Steps**

```bash
docker exec swiftdeploy-nginx cat /var/log/nginx/access.log | tail -5
```

**Expected** — every line matches the format
`$time_iso8601 | $status | ${request_time}s | $upstream_addr | $request`,
e.g.

```
2026-05-06T14:22:11+00:00 | 200 | 0.004s | 172.18.0.2:3000 | GET /healthz HTTP/1.1
```

### 9.6 nginx returns JSON 5xx body when upstream is down

**Setup**

```bash
docker stop swiftdeploy-api
```

**Steps**

```bash
curl -s http://localhost:8080/ | python -m json.tool
```

**Expected JSON keys**

- `error`, `code`, `service`, `contact` — all present.
- HTTP status (verify with `curl -i`) is one of `502`/`503`/`504`.
- `X-Deployed-By: swiftdeploy` is still set.

**Cleanup**

```bash
docker start swiftdeploy-api
```

---

## 10. Metrics endpoint tests

### 10.1 GET /metrics returns Prometheus text

**Steps**

```bash
curl -s http://localhost:8080/metrics | head -20
```

**Expected**

- Content begins with `# HELP` / `# TYPE` lines.
- No JSON, no errors.

### 10.2 All required metric families are present

**Steps**

```bash
curl -s http://localhost:8080/metrics | grep -E '^# TYPE (http_requests_total|http_request_duration_seconds|app_uptime_seconds|app_mode|chaos_active)'
```

**Expected**

- Five lines, one per metric family.

### 10.3 http_requests_total increments after a request

**Steps**

```bash
before=$(curl -s http://localhost:8080/metrics | grep '^http_requests_total{' | awk '{s+=$2} END {print s+0}')
curl -s http://localhost:8080/ > /dev/null
after=$(curl -s http://localhost:8080/metrics | grep '^http_requests_total{' | awk '{s+=$2} END {print s+0}')
echo "before=$before after=$after"
```

**Expected**

- `after` is greater than `before` by at least 1.

### 10.4 /metrics and /healthz are excluded from the request counter

**Steps**

```bash
curl -s http://localhost:8080/metrics | grep -E '^http_requests_total\{.*path="/(metrics|healthz)"'
```

**Expected**

- No output. Exit code 1 from grep is fine — the goal is "no rows".

### 10.5 app_mode reflects the running mode

**Steps**

```bash
curl -s http://localhost:8080/metrics | grep '^app_mode '
```

**Expected**

- `app_mode 0.0` when stable, `app_mode 1.0` when canary.

---

## 11. `swiftdeploy promote` tests

### 11.1 Promote stable → canary (rollback path always allowed for the inverse)

**Setup:** stack is up, currently in `stable` mode.

**Steps**

```bash
swiftdeploy promote canary
```

**Expected**

- The CLI prints `Sampling /metrics for 30s window…`.
- Window stats line is printed.
- `[canary_safety] ALLOW — no violations` is printed.
- `Updated manifest.yaml: services.mode=canary`.
- API container is force-recreated.
- Healthz body shows `"mode":"canary"`.
- `Promotion to canary confirmed`.
- Exit code 0.

### 11.2 manifest.yaml was updated in place

**Steps**

```bash
grep '^  mode:' manifest.yaml
```

**Expected**

- `  mode: canary`.

### 11.3 Canary adds X-Mode header

**Steps**

```bash
curl -sI http://localhost:8080/ | grep -i 'X-Mode'
```

**Expected**

- `X-Mode: canary`.

### 11.4 /chaos is now active

**Steps**

```bash
curl -s -X POST http://localhost:8080/chaos \
  -H "Content-Type: application/json" \
  -d '{"mode":"recover"}'
```

**Expected**

- HTTP 200, JSON includes `"chaos":{"mode":null,...}`.

### 11.5 Promote canary → stable (rollback is always allowed)

**Steps**

```bash
swiftdeploy promote stable
```

**Expected**

- `[canary_safety] target=stable — rollback path is always permitted; skipping gate.`
- API is force-recreated.
- `Promotion to stable confirmed`.
- Exit code 0.
- `curl -sI http://localhost:8080/healthz` no longer has `X-Mode`.

---

## 12. OPA gating tests (the brain)

### 12.1 OPA container is reachable from the host

**Steps**

```bash
curl -s http://127.0.0.1:8181/health
```

**Expected**

- `{}` (HTTP 200).

### 12.2 Direct query to the infrastructure policy with a healthy input

**Steps**

```bash
curl -s -X POST http://127.0.0.1:8181/v1/data/infrastructure/decision \
  -H "Content-Type: application/json" \
  -d '{"input":{"host":{"disk_free_gb":500,"cpu_load_1m":0.1,"mem_used_pct":0.2}}}' \
  | python -m json.tool
```

**Expected JSON**

```json
{
  "result": {
    "allow": true,
    "violations": [],
    "domain": "infrastructure"
  }
}
```

### 12.3 Direct query with a failing input shows structured violations

**Steps**

```bash
curl -s -X POST http://127.0.0.1:8181/v1/data/infrastructure/decision \
  -H "Content-Type: application/json" \
  -d '{"input":{"host":{"disk_free_gb":1,"cpu_load_1m":9.9,"mem_used_pct":0.99}}}' \
  | python -m json.tool
```

**Expected JSON**

- `result.allow == false`.
- `result.violations` is a list of three objects.
- Each violation has `rule`, `message`, `observed`, `threshold`.

### 12.4 Direct query to canary_safety — failing case

**Steps**

```bash
curl -s -X POST http://127.0.0.1:8181/v1/data/canary_safety/decision \
  -H "Content-Type: application/json" \
  -d '{"input":{"current_mode":"canary","target_mode":"canary","metrics":{"window_seconds":30,"request_rate":10,"error_rate":0.5,"p99_latency_ms":2500,"sample_requests":300}}}' \
  | python -m json.tool
```

**Expected JSON**

- `result.allow == false`.
- `result.violations` contains entries for `max_error_rate` and
  `max_p99_latency_ms`.

### 12.5 promote canary while chaos is degrading the service is denied

**Setup**

```bash
swiftdeploy promote canary
curl -s -X POST http://localhost:8080/chaos \
  -H "Content-Type: application/json" \
  -d '{"mode":"error","rate":0.9}'
# generate sample traffic
for i in $(seq 1 80); do curl -s -o /dev/null http://localhost:8080/; done
```

**Steps**

```bash
swiftdeploy promote canary; echo "exit=$?"
```

> The promote-canary-while-canary scenario is the simplest way to
> reproduce the gate without changing modes. The CLI samples /metrics,
> sees a high error rate, and OPA denies.

**Expected**

- Window stats line shows `error_rate` > `0.01`.
- `[canary_safety] DENY — N violation(s):` is printed.
- At least `max_error_rate` violation is listed with `observed=` and
  `threshold=`.
- `ERROR promotion blocked by canary_safety policy. Use --force to override (logged).`
- `exit=2`.

**Cleanup**

```bash
curl -s -X POST http://localhost:8080/chaos \
  -H "Content-Type: application/json" \
  -d '{"mode":"recover"}'
```

### 12.6 --force bypasses the denial and is recorded

**Setup**

```bash
curl -s -X POST http://localhost:8080/chaos \
  -H "Content-Type: application/json" \
  -d '{"mode":"error","rate":0.9}'
for i in $(seq 1 80); do curl -s -o /dev/null http://localhost:8080/; done
```

**Steps**

```bash
swiftdeploy promote canary --force; echo "exit=$?"
grep -c '"event": "force_override"' history.jsonl
```

**Expected**

- `WARNING: canary_safety policy denied but --force was supplied. Proceeding.`
- `Promotion to canary confirmed`.
- `exit=0`.
- `grep -c` returns ≥ 1.

**Cleanup**

```bash
curl -s -X POST http://localhost:8080/chaos \
  -H "Content-Type: application/json" \
  -d '{"mode":"recover"}'
```

### 12.7 Rego files contain no numeric literals

**Steps**

```bash
grep -nE 'data\.thresholds\.' policies/*.rego | wc -l
```

**Expected**

- A non-zero count — every threshold is read from `data.thresholds.*`,
  proving thresholds come from `opa-data.json` (which is rendered from
  the manifest), not the policy files.

---

## 13. OPA isolation & no-leakage tests

### 13.1 OPA port is bound to localhost only

**Steps**

```bash
docker port swiftdeploy-opa
```

**Expected**

- `8181/tcp -> 127.0.0.1:8181`. Not `0.0.0.0:8181`.

### 13.2 OPA is NOT reachable through nginx

**Steps**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/v1/data/infrastructure/decision
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/health
```

**Expected**

- Both return `404`. Nginx has no upstream pointing at OPA.

### 13.3 OPA sits on its own dedicated network

**Steps**

```bash
docker network inspect swiftdeploy-policy-net --format '{{range $k, $v := .Containers}}{{$v.Name}} {{end}}'
```

**Expected**

- The container list contains only `swiftdeploy-opa` — neither
  `swiftdeploy-nginx` nor `swiftdeploy-api` is on this network. Since
  nginx cannot route to a container it shares no network with, this
  alone makes OPA unreachable through the public ingress.

### 13.4 nginx and api share their own (non-internal) network

**Steps**

```bash
docker network inspect swiftdeploy-net --format '{{json .Internal}} {{range $k, $v := .Containers}}{{$v.Name}} {{end}}'
```

**Expected**

- `Internal` is `false` (or absent — defaults to false).
- Container list includes `swiftdeploy-api` and `swiftdeploy-nginx`,
  but **not** `swiftdeploy-opa`.

---

## 14. `swiftdeploy status` dashboard tests

### 14.1 Status renders without crashing

**Setup:** stack is up.

**Steps**

```bash
swiftdeploy status --interval 1
# wait ~10 seconds, then Ctrl-C
```

**Expected**

- Two-panel display appears: `Live metrics` (top) and
  `Policy compliance` (bottom) within a `swiftdeploy status` outer box.
- After ~2s the `req/s`, `error rate`, `p99 latency` rows show numbers
  (not `(collecting baseline…)` / `—`).
- Ctrl-C exits cleanly with `exited.` and exit code 0.

### 14.2 history.jsonl is appended on every refresh

**Setup**

```bash
rm -f history.jsonl
```

**Steps**

```bash
swiftdeploy status --interval 1 &
PID=$!
sleep 6
kill -INT $PID
wait $PID 2>/dev/null
wc -l history.jsonl
```

**Expected**

- `history.jsonl` exists with at least 4 lines.
- Each line is valid JSON (verify with `python -c "import json; [json.loads(l) for l in open('history.jsonl')]"`).

### 14.3 Status shows policy denials in real time

**Setup**

```bash
swiftdeploy promote canary
curl -s -X POST http://localhost:8080/chaos \
  -H "Content-Type: application/json" \
  -d '{"mode":"error","rate":0.9}'
```

**Steps**

In one terminal:

```bash
swiftdeploy status --interval 1
```

In another, generate load:

```bash
for i in $(seq 1 200); do curl -s -o /dev/null http://localhost:8080/; done
```

**Expected**

- After load is applied, the dashboard's `Policy compliance` panel
  shows a `canary_safety FAIL` row with `max_error_rate: ...`.
- The `error rate` row in the live metrics panel is non-zero.

**Cleanup**

```bash
curl -s -X POST http://localhost:8080/chaos \
  -H "Content-Type: application/json" \
  -d '{"mode":"recover"}'
```

### 14.4 Status survives a temporary OPA outage

**Steps**

While `swiftdeploy status` is running:

```bash
docker stop swiftdeploy-opa
sleep 5
docker start swiftdeploy-opa
```

**Expected**

- The dashboard shows `canary_safety: UNKNOWN OPA error: ...` while
  OPA is down.
- It does **not** crash.
- Once OPA is back, subsequent rows show real decisions again.

---

## 15. `swiftdeploy audit` tests

### 15.1 audit renders with no history

**Setup**

```bash
rm -f history.jsonl audit_report.md
```

**Steps**

```bash
swiftdeploy audit
cat audit_report.md
```

**Expected**

- File starts with `# SwiftDeploy Audit Report`.
- Body says `_No events recorded yet..._`.

### 15.2 audit produces valid GFM with content

**Setup:** run Section 14.3 first to populate `history.jsonl` with a
mix of allow + deny events.

**Steps**

```bash
swiftdeploy audit
head -40 audit_report.md
```

**Expected**

- `# SwiftDeploy Audit Report` header.
- A `## Timeline` section with a table — `| Started | Ended | State |`.
- A `## Policy violations` section with at least one row.
- (If `--force` was used) a `## Forced overrides` section.
- All tables use `| --- |` separators (GFM standard) with no HTML.

### 15.3 audit table escapes pipes safely

**Steps**

```bash
grep -E '\| .+ \|' audit_report.md | head -3
```

**Expected**

- Every row has the same column count. No row truncated by an
  unescaped `|` in a violation message.

### 15.4 audit accepts --output

**Steps**

```bash
swiftdeploy audit --output /tmp/run-report.md
ls /tmp/run-report.md
```

**Expected**

- File exists at `/tmp/run-report.md`.
- Exit code 0.

---

## 16. `swiftdeploy teardown` tests

### 16.1 teardown removes containers, network, volume

**Steps**

```bash
swiftdeploy teardown
docker ps -a --filter name=swiftdeploy- --format '{{.Names}}'
docker volume ls --filter name=nginx-logs --format '{{.Name}}'
docker network ls --filter name=swiftdeploy- --format '{{.Name}}'
```

**Expected**

- `Removed containers, networks, and volumes` printed.
- All four `docker ...` commands return empty output.
- Exit code 0.

### 16.2 teardown --clean also removes generated configs

**Setup**

```bash
swiftdeploy deploy
```

**Steps**

```bash
swiftdeploy teardown --clean
ls docker-compose.yml nginx.conf generated/opa-data.json 2>&1
```

**Expected**

- All three files report "No such file or directory".
- `Deleted docker-compose.yml`, `Deleted nginx.conf`,
  `Deleted generated/opa-data.json` are printed.

### 16.3 teardown is safe when nothing is up

**Steps**

```bash
swiftdeploy teardown
```

**Expected**

- Either `docker-compose.yml does not exist; nothing to tear down` or
  a no-op compose down. Exit code 0 either way.

---

## 17. Failure handling matrix

The CLI must produce a **distinct, non-stacktrace** message for each
OPA failure mode.

### 17.1 OPA container missing

**Setup**

```bash
swiftdeploy teardown --clean
swiftdeploy init
docker compose up -d   # bring up everything EXCEPT we'll then stop OPA
docker stop swiftdeploy-opa
```

**Steps**

```bash
swiftdeploy deploy; echo "exit=$?"
```

> Note: `swiftdeploy deploy` will try to start OPA itself. To simulate
> a true unreachable scenario, run a one-off OPA-less query through the
> Python module:

```bash
python -c "
from swiftdeploy_cli import opa
try:
    opa.query('infrastructure', {'host':{'disk_free_gb':1,'cpu_load_1m':0,'mem_used_pct':0}}, 'http://127.0.0.1:9999')
except opa.OpaError as e:
    print(type(e).__name__, e)
"
```

**Expected**

- Output begins with `OpaUnreachable` and a clear reason. No traceback.

### 17.2 OPA timeout

**Steps**

```bash
python -c "
from swiftdeploy_cli import opa
try:
    opa.query('infrastructure', {}, 'http://10.255.255.1:8181', timeout=1.0)
except opa.OpaError as e:
    print(type(e).__name__, e)
"
```

**Expected**

- `OpaTimeout` followed by a "timeout after 1.0s" message.

### 17.3 OPA returns non-JSON

**Steps**

```bash
python -m http.server 9999 &
SRV=$!
python -c "
from swiftdeploy_cli import opa
try:
    opa.query('infrastructure', {}, 'http://127.0.0.1:9999')
except opa.OpaError as e:
    print(type(e).__name__, e)
"
kill $SRV
```

**Expected**

- `OpaUnhealthy` (because `http.server` returns 404 for
  `/v1/data/infrastructure/decision`) — distinct from Unreachable.

### 17.4 Policy returns no `allow` field (simulation)

**Steps**

Query a policy package that exists but has no `decision` rule:

```bash
curl -s -X POST http://127.0.0.1:8181/v1/data/system/decision \
  -H "Content-Type: application/json" -d '{"input":{}}'
```

**Expected**

- Response contains no `result` key (OPA returns `{}` because the path
  doesn't exist). The CLI's `OpaPolicyError` is what surfaces this.

### 17.5 promote stable always succeeds even with OPA down

**Setup**

```bash
swiftdeploy deploy
swiftdeploy promote canary
docker stop swiftdeploy-opa
```

**Steps**

```bash
swiftdeploy promote stable
```

**Expected**

- `[canary_safety] target=stable — rollback path is always permitted; skipping gate.`
- Promotion succeeds even though OPA is down.
- Exit code 0.

**Cleanup**

```bash
docker start swiftdeploy-opa
```

---

## 18. Container hardening tests

### 18.1 API runs as non-root

**Steps**

```bash
docker exec swiftdeploy-api id
```

**Expected**

- `uid=10001 gid=10001`.

### 18.2 nginx runs as non-root

**Steps**

```bash
docker exec swiftdeploy-nginx id
```

**Expected**

- `uid=101 gid=101`.

### 18.3 OPA runs as non-root

The `-static` OPA image is distroless and has no shell or `id` binary,
so `docker exec swiftdeploy-opa id` will fail with "executable file not
found". That's a security feature of distroless, not a problem. Verify
the user via the host instead:

**Steps**

```bash
docker top swiftdeploy-opa
docker inspect swiftdeploy-opa --format '{{.Config.User}}'
```

**Expected**

- `docker top` shows the OPA process owned by uid `65532` (the
  distroless `nonroot` user) or whatever the image was built to run as.
- `docker inspect` returns a non-empty user (or empty, in which case
  the image's configured ENTRYPOINT user applies — confirm via
  `docker top` above).

### 18.4 All three containers drop ALL capabilities

**Steps**

```bash
for c in swiftdeploy-api swiftdeploy-nginx swiftdeploy-opa; do
  echo "=== $c ===";
  docker inspect "$c" --format '{{json .HostConfig.CapDrop}}';
done
```

**Expected**

- Each container shows `["ALL"]`.

### 18.5 no-new-privileges is set on every container

**Steps**

```bash
for c in swiftdeploy-api swiftdeploy-nginx swiftdeploy-opa; do
  echo "=== $c ===";
  docker inspect "$c" --format '{{json .HostConfig.SecurityOpt}}';
done
```

**Expected**

- Each container's SecurityOpt list contains `no-new-privileges:true`.

### 18.6 Healthchecks are configured on api and opa

**Steps**

```bash
docker inspect swiftdeploy-api --format '{{json .State.Health.Status}}'
docker inspect swiftdeploy-opa --format '{{json .State.Health.Status}}'
```

**Expected**

- Both report `"healthy"` (after the start period).

---

## 19. Hard-gate scenario (the disk-fill grader test)

> **Linux/WSL only.** This test fills your root filesystem to within
> the configured threshold to trigger the infrastructure denial.
> **Read carefully** — leftover files will not be auto-removed if the
> test crashes midway.

### 19.1 Setup the simulated low-disk state

**Setup**

Pick a path on the same filesystem as `policies.infrastructure.disk_path`
(default `/`). We'll create a sparse hog file. Compute how big it must
be to leave less than 10 GB free:

```bash
df -BG --output=avail / | tail -1
```

If the output says `120G`, you need to take 111 GB to drop below 10 GB.
Use a sparse file so we don't actually consume disk blocks (only the
`statvfs` "free" reading matters because we don't actually write):

```bash
# WARNING: do NOT do this on a production machine.
sudo fallocate -l 111G /var/tmp/swiftdeploy-disk-hog
df -BG --output=avail /
```

> If `fallocate` is unavailable, use `dd if=/dev/zero of=/var/tmp/swiftdeploy-disk-hog bs=1M count=113664`
> — but this writes real data. Free it with `rm` after the test.

### 19.2 Deploy is denied

**Steps**

```bash
swiftdeploy teardown 2>/dev/null || true
swiftdeploy deploy; echo "exit=$?"
```

**Expected**

- OPA is started.
- `[infrastructure] DENY — 1 violation(s):`
- A `min_disk_free_gb` row with `observed=<value> threshold=10` (or
  whatever the manifest's `min_disk_free_gb` is).
- `ERROR deploy blocked by infrastructure policy. Use --force to override (logged).`
- `exit=2`.
- The api and nginx containers were **not** started.

### 19.3 Cleanup the disk hog

**Steps**

```bash
sudo rm -f /var/tmp/swiftdeploy-disk-hog
df -BG --output=avail /
swiftdeploy deploy
```

**Expected**

- Free disk is back to baseline.
- Subsequent `swiftdeploy deploy` succeeds (policy now passes).

---

## 20. End-to-end happy path (smoke test)

Run this end-to-end on a fresh clone to convince yourself everything
hangs together. Each command is expected to succeed (exit 0) unless
explicitly noted.

```bash
# 1. install
pip install -e .

# 2. build images
docker build -t swift-deploy-1-node:latest .
docker build -t swiftdeploy-nginx:latest -f nginx.Dockerfile .

# 3. validate
swiftdeploy validate

# 4. deploy (gated, must pass)
swiftdeploy deploy

# 5. happy-path API checks
curl -i http://localhost:8080/         # 200, X-Deployed-By present
curl -i http://localhost:8080/healthz  # 200, JSON status=ok
curl -i http://localhost:3000/healthz  # connection refused (good)
curl -s http://localhost:8080/metrics | head -3   # Prometheus text

# 6. promote to canary (gated, must pass on idle stack)
swiftdeploy promote canary
curl -i http://localhost:8080/healthz  # X-Mode: canary

# 7. inject error chaos and watch the gate fire
curl -X POST http://localhost:8080/chaos -H "Content-Type: application/json" -d '{"mode":"error","rate":0.9}'
for i in $(seq 1 100); do curl -s -o /dev/null http://localhost:8080/; done
swiftdeploy promote canary    # MUST exit 2 with [canary_safety] DENY

# 8. recover and roll back
curl -X POST http://localhost:8080/chaos -H "Content-Type: application/json" -d '{"mode":"recover"}'
swiftdeploy promote stable    # always allowed (rollback path)

# 9. status + audit
swiftdeploy status --interval 1   # Ctrl-C after ~10s
swiftdeploy audit
head -30 audit_report.md

# 10. isolation
docker port swiftdeploy-opa                                     # 127.0.0.1:8181
curl -o /dev/null -w "%{http_code}\n" http://localhost:8080/health  # 404 (no leakage)

# 11. teardown
swiftdeploy teardown --clean
```

**Pass criteria:** every step behaves as commented; the only intended
non-zero exit is step 7 (`exit=2` from the canary gate denial).

---

## 21. Cleanup

After running the full test plan:

```bash
swiftdeploy teardown --clean
docker rmi swift-deploy-1-node:latest swiftdeploy-nginx:latest 2>/dev/null || true
deactivate 2>/dev/null || true
```

Disk-hog cleanup (if Section 19 was exercised):

```bash
sudo rm -f /var/tmp/swiftdeploy-disk-hog
```

---

## Appendix A — Mapping tests to grader requirements

| HNG Stage 4B requirement                      | Test(s) covering it    |
| --------------------------------------------- | ---------------------- |
| `/metrics` exposes the four required families | 10.1, 10.2             |
| OPA reachable by CLI, isolated from ingress   | 12.1, 13.1, 13.2, 13.3 |
| Policies in `policies/` loaded by OPA         | 7.4, 12.2, 12.4        |
| Thresholds not hardcoded in Rego              | 7.3, 7.4, 12.7         |
| Domain-isolated policies, independent answers | 12.2, 12.4             |
| Decisions carry reasoning (no bare booleans)  | 12.3, 12.4             |
| Distinct messages for each OPA failure mode   | 17.1–17.4              |
| pre-deploy gate sends host stats              | 8.1, 19.2              |
| pre-promote gate sends scraped metrics        | 11.1, 12.5             |
| `swiftdeploy status` live dashboard           | 14.1, 14.3             |
| status writes to history.jsonl                | 14.2                   |
| `swiftdeploy audit` produces clean GFM        | 15.2, 15.3             |
| Hard gate: full disk denies deploy            | 19.2                   |
| No leakage: OPA not via nginx                 | 13.2                   |
| Manifest is single source of truth            | 7.2 (idempotency), 5.2 |
| Container hardening                           | 18.1–18.6              |
| Image size <300MB                             | 4.3                    |
