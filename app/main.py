import os
import random
import time
from datetime import datetime, timezone

from flask import Flask, Response, jsonify, request
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)


app = Flask(__name__)

STARTED_AT = time.monotonic()
CHAOS = {
    "mode": None,
    "duration": 0,
    "rate": 0,
}

REGISTRY = CollectorRegistry()

REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests by method, path, and status code",
    ["method", "path", "status_code"],
    registry=REGISTRY,
)

REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "path"],
    registry=REGISTRY,
)

UPTIME_GAUGE = Gauge(
    "app_uptime_seconds",
    "Process uptime in seconds",
    registry=REGISTRY,
)

MODE_GAUGE = Gauge(
    "app_mode",
    "Deployment mode (0=stable, 1=canary)",
    registry=REGISTRY,
)

CHAOS_GAUGE = Gauge(
    "chaos_active",
    "Active chaos injection (0=none, 1=slow, 2=error)",
    registry=REGISTRY,
)

CHAOS_CODES = {None: 0, "slow": 1, "error": 2}

# Routes excluded from request counting/latency to avoid scraper-induced noise.
EXCLUDED_PATHS = {"/metrics", "/healthz"}

# Routes never affected by chaos. /metrics and /healthz are infrastructure
# endpoints — gating them on chaos would break the gate's ability to read
# real signal during a degraded canary.
CHAOS_EXEMPT_PATHS = {"/metrics", "/healthz", "/chaos"}


def app_mode():
    return os.getenv("MODE", "stable").strip().lower() or "stable"


def app_version():
    return os.getenv("APP_VERSION", "1.0.0")


def refresh_state_gauges():
    UPTIME_GAUGE.set(time.monotonic() - STARTED_AT)
    MODE_GAUGE.set(1 if app_mode() == "canary" else 0)
    CHAOS_GAUGE.set(CHAOS_CODES.get(CHAOS["mode"], 0))


def apply_chaos():
    if app_mode() != "canary":
        return None

    if CHAOS["mode"] == "slow":
        time.sleep(max(0, float(CHAOS["duration"])))
        return None

    if CHAOS["mode"] == "error" and random.random() < float(CHAOS["rate"]):
        return jsonify({
            "error": "chaos error injected",
            "mode": app_mode(),
            "version": app_version(),
        }), 500

    return None


@app.before_request
def before_each_request():
    request._start_time = time.perf_counter()
    if request.path in CHAOS_EXEMPT_PATHS:
        return None
    return apply_chaos()


@app.after_request
def after_each_request(response):
    if app_mode() == "canary":
        response.headers["X-Mode"] = "canary"

    path = request.path or "/"
    if path not in EXCLUDED_PATHS:
        start = getattr(request, "_start_time", None)
        if start is not None:
            REQUEST_DURATION.labels(method=request.method, path=path).observe(
                time.perf_counter() - start
            )
        REQUESTS_TOTAL.labels(
            method=request.method, path=path, status_code=str(response.status_code)
        ).inc()

    return response


@app.get("/")
def index():
    return jsonify({
        "message": "Welcome to SwiftDeploy",
        "mode": app_mode(),
        "version": app_version(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@app.get("/healthz")
def healthz():
    refresh_state_gauges()
    return jsonify({
        "status": "ok",
        "mode": app_mode(),
        "version": app_version(),
        "uptime": round(time.monotonic() - STARTED_AT, 3),
    })


@app.get("/metrics")
def metrics():
    refresh_state_gauges()
    return Response(generate_latest(REGISTRY), mimetype=CONTENT_TYPE_LATEST)


@app.post("/chaos")
def chaos():
    if app_mode() != "canary":
        return jsonify({
            "error": "chaos endpoint is only active in canary mode",
            "mode": app_mode(),
        }), 403

    payload = request.get_json(silent=True) or {}
    requested_mode = payload.get("mode")

    if requested_mode == "slow":
        duration = payload.get("duration")
        if not isinstance(duration, (int, float)) or duration < 0:
            return jsonify({"error": "duration must be a non-negative number"}), 400
        CHAOS.update({"mode": "slow", "duration": duration, "rate": 0})

    elif requested_mode == "error":
        rate = payload.get("rate")
        if not isinstance(rate, (int, float)) or not 0 <= rate <= 1:
            return jsonify({"error": "rate must be a number between 0 and 1"}), 400
        CHAOS.update({"mode": "error", "duration": 0, "rate": rate})

    elif requested_mode == "recover":
        CHAOS.update({"mode": None, "duration": 0, "rate": 0})

    else:
        return jsonify({"error": "mode must be slow, error, or recover"}), 400

    refresh_state_gauges()
    return jsonify({
        "status": "ok",
        "chaos": CHAOS,
        "mode": app_mode(),
    })


if __name__ == "__main__":
    port = int(os.getenv("APP_PORT", "3000"))
    app.run(host="0.0.0.0", port=port)
