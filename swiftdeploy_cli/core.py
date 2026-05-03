#!/usr/bin/env python3
import argparse
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

REQUIRED_FIELDS = (
    ("services", "image"),
    ("services", "port"),
    ("nginx", "image"),
    ("nginx", "port"),
    ("network", "name"),
    ("network", "driver_type"),
)


def run(command, check=True, capture=False):
    kwargs = {
        "cwd": ROOT,
        "text": True,
    }
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

    service.setdefault("mode", "stable")
    service.setdefault("version", "1.0.0")
    service.setdefault("restart_policy", "unless-stopped")
    service.setdefault("container_name", "swiftdeploy-api")

    nginx.setdefault("proxy_timeout", 30)
    nginx.setdefault("contact", "devops@example.com")
    nginx.setdefault("container_name", "swiftdeploy-nginx")

    network.setdefault("driver_type", "bridge")


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
    }

    COMPOSE.write_text(env.get_template("docker-compose.yml.j2").render(context), encoding="utf-8")
    NGINX_CONF.write_text(env.get_template("nginx.conf.j2").render(context), encoding="utf-8")
    print(f"Generated {COMPOSE.name}")
    print(f"Generated {NGINX_CONF.name}")


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
                if response.status == 200 and (expected_mode is None or expected_mode in body or header_mode == expected_mode):
                    print(f"Health check passed: {url}")
                    print(body)
                    return True
                last_error = f"unexpected health response: {body}"
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = str(exc)
        time.sleep(2)

    raise SwiftDeployError(f"health checks did not pass within {timeout}s: {last_error}")


def command_deploy(_args):
    render_templates()
    compose = docker_compose_command()
    run(compose + ["up", "-d"], check=True)
    data = load_manifest()
    wait_for_health(data["nginx"]["port"], timeout=60)
    print("Deployment is healthy")


def write_manifest(data):
    MANIFEST.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def command_promote(args):
    mode = args.mode
    data = load_manifest()
    validate_required_fields(data)
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
        for path in (COMPOSE, NGINX_CONF):
            if path.exists():
                path.unlink()
                print(f"Deleted {path.name}")


def build_parser():
    parser = argparse.ArgumentParser(prog="swiftdeploy", description="Declarative deployment lifecycle manager")
    subcommands = parser.add_subparsers(dest="command", required=True)

    init = subcommands.add_parser("init", help="generate docker-compose.yml and nginx.conf")
    init.set_defaults(func=command_init)

    validate = subcommands.add_parser("validate", help="run pre-flight validation checks")
    validate.set_defaults(func=command_validate)

    deploy = subcommands.add_parser("deploy", help="generate configs, start stack, and wait for health")
    deploy.set_defaults(func=command_deploy)

    promote = subcommands.add_parser("promote", help="switch deployment mode")
    promote.add_argument("mode", choices=("canary", "stable"))
    promote.set_defaults(func=command_promote)

    teardown = subcommands.add_parser("teardown", help="remove stack resources")
    teardown.add_argument("--clean", action="store_true", help="delete generated config files")
    teardown.set_defaults(func=command_teardown)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    try:
        return args.func(args) or 0
    except SwiftDeployError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
