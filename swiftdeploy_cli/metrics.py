"""Scrape and parse the app's /metrics endpoint, then compute windowed
stats (req/s, error rate, p99 latency) for the canary safety gate.

Histograms are cumulative — to compute "p99 over the last N seconds" we
take a delta between two snapshots. The first call populates a baseline,
subsequent calls compare against it."""
from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from prometheus_client.parser import text_string_to_metric_families


class MetricsScrapeError(Exception):
    pass


@dataclass
class Snapshot:
    taken_at: float
    requests_total: dict[tuple[str, str, str], float] = field(default_factory=dict)
    duration_buckets: dict[tuple[str, str, float], float] = field(default_factory=dict)
    duration_count: dict[tuple[str, str], float] = field(default_factory=dict)
    duration_sum: dict[tuple[str, str], float] = field(default_factory=dict)
    bucket_bounds: dict[tuple[str, str], list[float]] = field(default_factory=dict)
    app_mode: float = 0.0
    chaos_active: float = 0.0
    uptime_seconds: float = 0.0


@dataclass
class WindowStats:
    window_seconds: float
    request_rate: float
    error_rate: float
    p99_latency_ms: float
    sample_requests: int


def scrape(metrics_url: str, timeout: float = 3.0) -> Snapshot:
    try:
        with urllib.request.urlopen(metrics_url, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise MetricsScrapeError(f"failed to scrape {metrics_url}: {exc}") from exc

    snap = Snapshot(taken_at=time.time())

    for family in text_string_to_metric_families(body):
        if family.name == "http_requests":
            for sample in family.samples:
                if sample.name == "http_requests_total":
                    key = (
                        sample.labels.get("method", ""),
                        sample.labels.get("path", ""),
                        sample.labels.get("status_code", ""),
                    )
                    snap.requests_total[key] = sample.value
        elif family.name == "http_request_duration_seconds":
            for sample in family.samples:
                method = sample.labels.get("method", "")
                path = sample.labels.get("path", "")
                if sample.name == "http_request_duration_seconds_bucket":
                    le = sample.labels.get("le", "")
                    if le in ("", "+Inf"):
                        bound = float("inf")
                    else:
                        try:
                            bound = float(le)
                        except ValueError:
                            continue
                    snap.duration_buckets[(method, path, bound)] = sample.value
                    bounds = snap.bucket_bounds.setdefault((method, path), [])
                    if bound not in bounds:
                        bounds.append(bound)
                elif sample.name == "http_request_duration_seconds_count":
                    snap.duration_count[(method, path)] = sample.value
                elif sample.name == "http_request_duration_seconds_sum":
                    snap.duration_sum[(method, path)] = sample.value
        elif family.name == "app_mode":
            for sample in family.samples:
                snap.app_mode = sample.value
        elif family.name == "chaos_active":
            for sample in family.samples:
                snap.chaos_active = sample.value
        elif family.name == "app_uptime_seconds":
            for sample in family.samples:
                snap.uptime_seconds = sample.value

    for key in snap.bucket_bounds:
        snap.bucket_bounds[key].sort()

    return snap


def _sum_requests(snap: Snapshot) -> float:
    return sum(snap.requests_total.values())


def _sum_errors(snap: Snapshot) -> float:
    return sum(
        v for (_, _, status), v in snap.requests_total.items() if status.startswith("5")
    )


def _p99_from_buckets(prev: Snapshot, curr: Snapshot) -> float:
    """Compute p99 from histogram bucket deltas across all method/path pairs.

    Prometheus bucket values are already cumulative — bucket{le="0.025"}
    is the *total* number of requests that completed in ≤ 25 ms, not
    just the ones in the (0.01, 0.025] range. So we sum bucket counts
    across (method, path) pairs but DO NOT re-accumulate them within a
    single pair; the +Inf bucket is the total observation count.

    We treat the entire request population as one — sufficient for the
    canary safety gate which cares about overall tail latency."""
    bucket_totals: dict[float, float] = {}
    keys = set(curr.bucket_bounds.keys())
    for key in keys:
        bounds = curr.bucket_bounds[key]
        for bound in bounds:
            curr_v = curr.duration_buckets.get((key[0], key[1], bound), 0.0)
            prev_v = prev.duration_buckets.get((key[0], key[1], bound), 0.0)
            delta = max(0.0, curr_v - prev_v)
            bucket_totals[bound] = bucket_totals.get(bound, 0.0) + delta

    if not bucket_totals:
        return 0.0

    sorted_bounds = sorted(bucket_totals.keys())
    total = bucket_totals[sorted_bounds[-1]]
    if total <= 0:
        return 0.0

    target = 0.99 * total
    for bound in sorted_bounds:
        if bucket_totals[bound] >= target:
            if bound == float("inf"):
                finite = [b for b in sorted_bounds if b != float("inf")]
                bound = max(finite) if finite else 0.0
            return bound * 1000.0  # seconds → milliseconds

    return sorted_bounds[-1] * 1000.0


def compute_window(prev: Snapshot, curr: Snapshot) -> WindowStats:
    window = max(0.001, curr.taken_at - prev.taken_at)
    delta_total = max(0.0, _sum_requests(curr) - _sum_requests(prev))
    delta_errors = max(0.0, _sum_errors(curr) - _sum_errors(prev))
    error_rate = (delta_errors / delta_total) if delta_total > 0 else 0.0
    p99_ms = _p99_from_buckets(prev, curr)

    return WindowStats(
        window_seconds=round(window, 3),
        request_rate=round(delta_total / window, 3),
        error_rate=round(error_rate, 4),
        p99_latency_ms=round(p99_ms, 1),
        sample_requests=int(delta_total),
    )


def to_input(stats: WindowStats, current_mode: str, target_mode: str) -> dict:
    return {
        "metrics": {
            "window_seconds": stats.window_seconds,
            "request_rate": stats.request_rate,
            "error_rate": stats.error_rate,
            "p99_latency_ms": stats.p99_latency_ms,
            "sample_requests": stats.sample_requests,
        },
        "current_mode": current_mode,
        "target_mode": target_mode,
    }
