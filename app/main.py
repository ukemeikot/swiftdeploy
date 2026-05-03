import os
import random
import time
from datetime import datetime, timezone

from flask import Flask, jsonify, request


app = Flask(__name__)

STARTED_AT = time.monotonic()
CHAOS = {
    "mode": None,
    "duration": 0,
    "rate": 0,
}


def app_mode():
    return os.getenv("MODE", "stable").strip().lower() or "stable"


def app_version():
    return os.getenv("APP_VERSION", "1.0.0")


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


@app.after_request
def add_mode_header(response):
    if app_mode() == "canary":
        response.headers["X-Mode"] = "canary"
    return response


@app.before_request
def before_each_request():
    if request.path == "/chaos":
        return None
    return apply_chaos()


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
    return jsonify({
        "status": "ok",
        "mode": app_mode(),
        "version": app_version(),
        "uptime": round(time.monotonic() - STARTED_AT, 3),
    })


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

    return jsonify({
        "status": "ok",
        "chaos": CHAOS,
        "mode": app_mode(),
    })


if __name__ == "__main__":
    port = int(os.getenv("APP_PORT", "3000"))
    app.run(host="0.0.0.0", port=port)
