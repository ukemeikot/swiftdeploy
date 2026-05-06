#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from . import audit, dashboard, history, host_stats, metrics, opa


class SwiftDeployError(Exception):
    pass


def find_project_root(start=None):
    current = Path(start or os.getenv("SWIFTDEPLOY_ROOT") or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        if (directory / "manifest.yaml").exists() and (directory / "templates").is_dir():
            return directory
    raise SwiftDeployError(
        "could not find a SwiftDeploy project. "
        "Run this command from the project root or set SWIFTDEPLOY_ROOT."
    )


ROOT = find_project_root()
MANIFEST = ROOT / "manifest.yaml"
COMPOSE = ROOT / "docker-compose.yml"
NGINX_CONF = ROOT / "nginx.conf"
TEMPLATES = ROOT / "templates"
POLICIES_DIR = ROOT / "policies"
GENERATED_DIR = ROOT / "generated"
OPA_DATA = GENERATED_DIR / "opa-data.json"

REQUIRED_FIELDS = (
    ("services", "image"),
    ("services", "port"),
    ("nginx", "image"),
    ("nginx", "port"),
    ("network", "name"),
    ("network", "driver_type"),
    ("opa", "image"),
    ("opa", "port"),
    ("policies", "infrastructure"),
    ("policies", "canary_safety"),
)


def run(command, check=True, capture=False):
    kwargs = {"cwd": ROOT, "text": True}
    if capture:
        kwargs.update({"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT})

    result = subprocess.run(command, **kwargs)
    if check and result.returncode != 0:
        output = result.stdout.strip() if capture and result.stdout else ""
        raise SwiftDeployError(output or f"command failed: {' '.join(command)}")
    return result


def docker_compose_command():
    if shutil.which("docker") is None:
        raise SwiftDeployError("Docker is not installed or not available in PATH")

    compose = subprocess.run(
        ["docker", "compose", "version"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if compose.returncode == 0:
        return ["docker", "compose"]

    if shutil.which("docker-compose"):
        return ["docker-compose"]

    raise SwiftDeployError("Docker Compose is not installed")


def load_manifest():
    if not MANIFEST.exists():
        raise SwiftDeployError("manifest.yaml does not exist")

    try:
        data = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SwiftDeployError(f"manifest.yaml is not valid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise SwiftDeployError("manifest.yaml must contain a YAML mapping")

    apply_defaults(data)
    return data


def apply_defaults(data):
    service = data.setdefault("services", {})
    nginx = data.setdefault("nginx", {})
    network = data.setdefault("network", {})
    opa_cfg = data.setdefault("opa", {})
    policies = data.setdefault("policies", {})
    audit_cfg = data.setdefault("audit", {})

    service.setdefault("mode", "stable")
    service.setdefault("version", "1.0.0")
    service.setdefault("restart_policy", "unless-stopped")
    service.setdefault("container_name", "swiftdeploy-api")

    nginx.setdefault("proxy_timeout", 30)
    nginx.setdefault("contact", "devops@example.com")
    nginx.setdefault("container_name", "swiftdeploy-nginx")

    network.setdefault("driver_type", "bridge")

    opa_cfg.setdefault("image", "openpolicyagent/opa:1.16.1-static")
    opa_cfg.setdefault("port", 8181)
    opa_cfg.setdefault("container_name", "swiftdeploy-opa")
    opa_cfg.setdefault("policy_network", "swiftdeploy-policy-net")

    infra = policies.setdefault("infrastructure", {})
    infra.setdefault("min_disk_free_gb", 10)
    infra.setdefault("max_cpu_load", 2.0)
    infra.setdefault("max_mem_used_pct", 0.90)
    infra.setdefault("disk_path", "/")

    canary = policies.setdefault("canary_safety", {})
    canary.setdefault("max_error_rate", 0.01)
    canary.setdefault("max_p99_latency_ms", 500)
    canary.setdefault("window_seconds", 30)
    canary.setdefault("min_sample_requests", 5)

    audit_cfg.setdefault("history_path", "history.jsonl")
    audit_cfg.setdefault("report_path", "audit_report.md")


def validate_required_fields(data):
    missing = []
    for section, field in REQUIRED_FIELDS:
        value = data.get(section, {}).get(field)
        if value is None or value == "":
            missing.append(f"{section}.{field}")

    mode = data.get("services", {}).get("mode")
    if mode not in ("stable", "canary"):
        missing.append("services.mode must be stable or canary")

    if missing:
        raise SwiftDeployError("missing or invalid fields: " + ", ".join(missing))


def render_templates():
    data = load_manifest()
    validate_required_fields(data)

    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )

    context = {
        "service": data["services"],
        "nginx": data["nginx"],
        "network": data["network"],
        "opa": data["opa"],
    }

    COMPOSE.write_text(env.get_template("docker-compose.yml.j2").render(context), encoding="utf-8")
    NGINX_CONF.write_text(env.get_template("nginx.conf.j2").render(context), encoding="utf-8")
    print(f"Generated {COMPOSE.name}")
    print(f"Generated {NGINX_CONF.name}")

    write_opa_data(data)


def write_opa_data(data):
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"thresholds": data["policies"]}
    OPA_DATA.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Generated {OPA_DATA.relative_to(ROOT).as_posix()}")


def image_exists(image):
    result = run(["docker", "image", "inspect", image], check=False, capture=True)
    return result.returncode == 0


def port_available(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", int(port))) != 0


def nginx_syntax_valid():
    if shutil.which("docker") is None:
        raise SwiftDeployError("Docker is not installed or not available in PATH")

    image = load_manifest()["nginx"]["image"]
    result = run(
        [
            "docker",
            "run",
            "--rm",
            "--add-host",
            "api:127.0.0.1",
            "-v",
            f"{NGINX_CONF}:/etc/nginx/nginx.conf:ro",
            image,
            "nginx",
            "-t",
        ],
        check=False,
        capture=True,
    )
    if result.returncode != 0:
        raise SwiftDeployError(result.stdout.strip())


def print_check(ok, message):
    status = "PASS" if ok else "FAIL"
    print(f"{status} {message}")


def opa_base_url(data):
    return f"http://127.0.0.1:{data['opa']['port']}"


def metrics_url(data):
    return f"http://127.0.0.1:{data['nginx']['port']}/metrics"


def history_path(data):
    return ROOT / data["audit"]["history_path"]


def report_path(data):
    return ROOT / data["audit"]["report_path"]


def command_init(_args):
    render_templates()


def command_validate(_args):
    failures = 0

    try:
        data = load_manifest()
        print_check(True, "manifest.yaml exists and is valid YAML")
    except SwiftDeployError as exc:
        print_check(False, str(exc))
        return 1

    try:
        validate_required_fields(data)
        print_check(True, "all required fields are present and non-empty")
    except SwiftDeployError as exc:
        failures += 1
        print_check(False, str(exc))

    try:
        images = [data["services"]["image"], data["nginx"]["image"]]
        missing = [image for image in images if not image_exists(image)]
        if not missing:
            print_check(True, f"Docker images exist locally: {', '.join(images)}")
        else:
            failures += 1
            print_check(False, f"Docker images missing locally: {', '.join(missing)}")
    except SwiftDeployError as exc:
        failures += 1
        print_check(False, str(exc))

    if port_available(data["nginx"]["port"]):
        print_check(True, f"Nginx port {data['nginx']['port']} is available")
    else:
        failures += 1
        print_check(False, f"Nginx port {data['nginx']['port']} is already bound")

    try:
        render_templates()
        nginx_syntax_valid()
        print_check(True, "generated nginx.conf is syntactically valid")
    except SwiftDeployError as exc:
        failures += 1
        print_check(False, f"generated nginx.conf is invalid: {exc}")

    return 1 if failures else 0


def wait_for_health(port, expected_mode=None, timeout=60):
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/healthz"
    last_error = ""

    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                body = response.read().decode("utf-8")
                header_mode = response.headers.get("X-Mode")
                if response.status == 200 and (
                    expected_mode is None
                    or expected_mode in body
                    or header_mode == expected_mode
                ):
                    print(f"Health check passed: {url}")
                    print(body)
                    return True
                last_error = f"unexpected health response: {body}"
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = str(exc)
        time.sleep(2)

    raise SwiftDeployError(f"health checks did not pass within {timeout}s: {last_error}")


def wait_for_opa(base_url, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if opa.is_healthy(base_url, timeout=2):
            return True
        time.sleep(1)
    raise SwiftDeployError(f"OPA did not become healthy within {timeout}s at {base_url}")


def report_decision(decision: opa.Decision):
    print(opa.render_decision(decision))


def gate_pre_deploy(data, force: bool):
    threshold_path = data["policies"]["infrastructure"]["disk_path"]
    stats = host_stats.collect(disk_path=threshold_path)
    decision_input = stats.to_input()

    history.append(
        history_path(data),
        {
            "event": "pre_deploy_check",
            "input": decision_input,
        },
    )

    try:
        decision = opa.query("infrastructure", decision_input, opa_base_url(data))
    except opa.OpaUnreachable as exc:
        if force:
            history.append(history_path(data), {"event": "force_override", "command": "deploy", "reason": f"OPA unreachable: {exc}"})
            print(f"WARNING: {exc}. Proceeding because --force was supplied.")
            return
        raise SwiftDeployError(
            f"policy engine offline (no container?). Start the stack first or pass --force. Detail: {exc}"
        )
    except opa.OpaTimeout as exc:
        raise SwiftDeployError(f"policy engine timeout: {exc}. Try again or use --force.")
    except opa.OpaUnhealthy as exc:
        raise SwiftDeployError(f"policy engine unhealthy: {exc}. Check 'docker logs swiftdeploy-opa'.")
    except opa.OpaPolicyError as exc:
        raise SwiftDeployError(f"policy error: {exc}. Inspect policies/ for syntax issues.")
    except opa.OpaBadResponse as exc:
        raise SwiftDeployError(f"policy engine returned an unparseable body: {exc}.")

    history.append(
        history_path(data),
        {
            "event": "pre_deploy_decision",
            "decisions": {
                decision.domain: {
                    "allow": decision.allow,
                    "violations": [v.__dict__ for v in decision.violations],
                }
            },
        },
    )

    report_decision(decision)
    if not decision.allow:
        if force:
            history.append(history_path(data), {"event": "force_override", "command": "deploy", "reason": "infrastructure policy denied"})
            print("WARNING: infrastructure policy denied but --force was supplied. Proceeding.")
            return
        raise SwiftDeployError("deploy blocked by infrastructure policy. Use --force to override (logged).")


def gate_pre_promote(data, target_mode: str, force: bool):
    if target_mode == "stable":
        # Rollback path. Skip the gate entirely so a failing canary can
        # always be reverted to stable.
        print("[canary_safety] target=stable — rollback path is always permitted; skipping gate.")
        return

    canary_cfg = data["policies"]["canary_safety"]
    window = float(canary_cfg["window_seconds"])

    print(f"Sampling /metrics for {window:.0f}s window…")
    base_url = metrics_url(data)
    try:
        prev = metrics.scrape(base_url)
        time.sleep(window)
        curr = metrics.scrape(base_url)
    except metrics.MetricsScrapeError as exc:
        if force:
            history.append(history_path(data), {"event": "force_override", "command": "promote", "reason": f"metrics unreachable: {exc}"})
            print(f"WARNING: {exc}. Proceeding because --force was supplied.")
            return
        raise SwiftDeployError(f"cannot scrape /metrics: {exc}. Use --force to bypass (logged).")

    stats = metrics.compute_window(prev, curr)
    print(
        f"Window stats: rate={stats.request_rate}/s, error_rate={stats.error_rate}, "
        f"p99={stats.p99_latency_ms}ms, samples={stats.sample_requests}"
    )

    decision_input = metrics.to_input(
        stats,
        current_mode=data["services"]["mode"],
        target_mode=target_mode,
    )
    history.append(history_path(data), {"event": "pre_promote_check", "input": decision_input})

    try:
        decision = opa.query("canary_safety", decision_input, opa_base_url(data))
    except opa.OpaUnreachable as exc:
        if force:
            history.append(history_path(data), {"event": "force_override", "command": "promote", "reason": f"OPA unreachable: {exc}"})
            print(f"WARNING: {exc}. Proceeding because --force was supplied.")
            return
        raise SwiftDeployError(f"policy engine offline: {exc}. Use --force to override.")
    except opa.OpaTimeout as exc:
        raise SwiftDeployError(f"policy engine timeout: {exc}.")
    except opa.OpaUnhealthy as exc:
        raise SwiftDeployError(f"policy engine unhealthy: {exc}.")
    except opa.OpaPolicyError as exc:
        raise SwiftDeployError(f"policy error: {exc}.")
    except opa.OpaBadResponse as exc:
        raise SwiftDeployError(f"policy engine returned an unparseable body: {exc}.")

    history.append(
        history_path(data),
        {
            "event": "pre_promote_decision",
            "decisions": {
                decision.domain: {
                    "allow": decision.allow,
                    "violations": [v.__dict__ for v in decision.violations],
                }
            },
        },
    )

    report_decision(decision)
    if not decision.allow:
        if force:
            history.append(history_path(data), {"event": "force_override", "command": "promote", "reason": "canary_safety denied"})
            print("WARNING: canary_safety policy denied but --force was supplied. Proceeding.")
            return
        raise SwiftDeployError("promotion blocked by canary_safety policy. Use --force to override (logged).")


def command_deploy(args):
    render_templates()
    compose = docker_compose_command()
    data = load_manifest()

    # Bring OPA up first so the gate can query it. The api+nginx services
    # will be (re)started after the gate passes.
    run(compose + ["up", "-d", "opa"], check=True)
    wait_for_opa(opa_base_url(data), timeout=30)

    gate_pre_deploy(data, force=args.force)

    run(compose + ["up", "-d"], check=True)
    wait_for_health(data["nginx"]["port"], timeout=60)
    print("Deployment is healthy")


def write_manifest(data):
    MANIFEST.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def command_promote(args):
    mode = args.mode
    data = load_manifest()
    validate_required_fields(data)

    gate_pre_promote(data, target_mode=mode, force=args.force)

    data["services"]["mode"] = mode
    write_manifest(data)
    print(f"Updated manifest.yaml: services.mode={mode}")

    render_templates()
    compose = docker_compose_command()
    run(compose + ["up", "-d", "--no-deps", "--force-recreate", "api"], check=True)
    wait_for_health(data["nginx"]["port"], expected_mode=mode, timeout=60)
    print(f"Promotion to {mode} confirmed")


def command_teardown(args):
    compose = docker_compose_command()
    if COMPOSE.exists():
        run(compose + ["down", "-v", "--remove-orphans"], check=True)
        print("Removed containers, networks, and volumes")
    else:
        print("docker-compose.yml does not exist; nothing to tear down")

    if args.clean:
        for path in (COMPOSE, NGINX_CONF, OPA_DATA):
            if path.exists():
                path.unlink()
                print(f"Deleted {path.relative_to(ROOT).as_posix()}")
        if GENERATED_DIR.exists() and not any(GENERATED_DIR.iterdir()):
            GENERATED_DIR.rmdir()


def command_status(args):
    data = load_manifest()
    return dashboard.run(
        metrics_url=metrics_url(data),
        opa_url=opa_base_url(data),
        history_path=history_path(data),
        refresh_seconds=float(args.interval),
    )


def command_audit(args):
    data = load_manifest()
    out_path = Path(args.output) if args.output else report_path(data)
    audit.render(history_path(data), out_path)
    print(f"Wrote {out_path.relative_to(ROOT).as_posix() if out_path.is_relative_to(ROOT) else out_path}")


def build_parser():
    parser = argparse.ArgumentParser(prog="swiftdeploy", description="Declarative deployment lifecycle manager with policy gates")
    subcommands = parser.add_subparsers(dest="command", required=True)

    init = subcommands.add_parser("init", help="generate docker-compose.yml, nginx.conf, and opa-data.json")
    init.set_defaults(func=command_init)

    validate = subcommands.add_parser("validate", help="run pre-flight validation checks")
    validate.set_defaults(func=command_validate)

    deploy = subcommands.add_parser("deploy", help="generate configs, start stack, gate on infrastructure policy, wait for health")
    deploy.add_argument("--force", action="store_true", help="proceed even if the infrastructure gate denies (recorded in history.jsonl)")
    deploy.set_defaults(func=command_deploy)

    promote = subcommands.add_parser("promote", help="switch deployment mode (gated by canary_safety policy when promoting to canary)")
    promote.add_argument("mode", choices=("canary", "stable"))
    promote.add_argument("--force", action="store_true", help="proceed even if the canary_safety gate denies (recorded in history.jsonl)")
    promote.set_defaults(func=command_promote)

    teardown = subcommands.add_parser("teardown", help="remove stack resources")
    teardown.add_argument("--clean", action="store_true", help="delete generated config files")
    teardown.set_defaults(func=command_teardown)

    status = subcommands.add_parser("status", help="live dashboard of metrics and policy compliance")
    status.add_argument("--interval", default=2.0, type=float, help="refresh interval in seconds (default: 2)")
    status.set_defaults(func=command_status)

    audit_cmd = subcommands.add_parser("audit", help="render history.jsonl into audit_report.md")
    audit_cmd.add_argument("--output", help="override the report output path")
    audit_cmd.set_defaults(func=command_audit)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    try:
        return args.func(args) or 0
    except SwiftDeployError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2 if "blocked by" in str(exc) else 1
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
