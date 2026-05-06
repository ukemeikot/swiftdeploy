"""Live terminal dashboard for `swiftdeploy status`. Scrapes /metrics
every refresh, queries OPA for both decision domains, and appends one
JSON line per refresh to history.jsonl."""
from __future__ import annotations

import time
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import history, metrics, opa


def _metrics_panel(stats: metrics.WindowStats | None, snap: metrics.Snapshot) -> Panel:
    table = Table.grid(padding=(0, 2))
    table.add_column(justify="right", style="bold")
    table.add_column()

    mode_label = "canary" if snap.app_mode == 1 else "stable"
    chaos_map = {0: "none", 1: "slow", 2: "error"}
    chaos_label = chaos_map.get(int(snap.chaos_active), "unknown")

    table.add_row("mode", mode_label)
    table.add_row("chaos", chaos_label)
    table.add_row("uptime", f"{snap.uptime_seconds:.1f}s")

    if stats is None:
        table.add_row("req/s", "(collecting baseline…)")
        table.add_row("error rate", "—")
        table.add_row("p99 latency", "—")
        table.add_row("samples", "0")
    else:
        table.add_row("req/s", f"{stats.request_rate:.2f}")
        table.add_row("error rate", f"{stats.error_rate * 100:.2f}%")
        table.add_row("p99 latency", f"{stats.p99_latency_ms:.0f} ms")
        table.add_row("samples", str(stats.sample_requests))

    return Panel(table, title="Live metrics", border_style="cyan")


def _policy_panel(decisions: dict[str, opa.Decision | str]) -> Panel:
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Domain")
    table.add_column("Status")
    table.add_column("Detail")

    for domain, decision in decisions.items():
        if isinstance(decision, str):
            table.add_row(domain, Text("UNKNOWN", style="yellow"), decision)
            continue
        if decision.allow:
            table.add_row(domain, Text("PASS", style="green"), "no violations")
            continue
        for v in decision.violations:
            table.add_row(domain, Text("FAIL", style="red"), f"{v.rule}: {v.message}")

    return Panel(table, title="Policy compliance", border_style="magenta")


def run(
    metrics_url: str,
    opa_url: str,
    history_path: Path,
    refresh_seconds: float = 2.0,
) -> int:
    console = Console()
    console.print(
        f"[dim]Scraping {metrics_url} and querying {opa_url} every {refresh_seconds}s. "
        f"Ctrl-C to exit.[/dim]"
    )

    prev_snap: metrics.Snapshot | None = None

    with Live(console=console, refresh_per_second=2, screen=False) as live:
        try:
            while True:
                tick_started = time.time()
                try:
                    snap = metrics.scrape(metrics_url)
                except metrics.MetricsScrapeError as exc:
                    live.update(Panel(f"[red]metrics scrape failed:[/red] {exc}"))
                    history.append(
                        history_path,
                        {"event": "scrape_error", "error": str(exc)},
                    )
                    time.sleep(refresh_seconds)
                    continue

                stats = (
                    metrics.compute_window(prev_snap, snap)
                    if prev_snap is not None
                    else None
                )

                decisions: dict[str, opa.Decision | str] = {}
                infra_input = {
                    "host": {
                        "disk_free_gb": 999.0,
                        "cpu_load_1m": 0.0,
                        "mem_used_pct": 0.0,
                    }
                }
                # status only displays canary_safety; infra is a deploy-time
                # gate. Querying it here would flip-flop with system load
                # noise unrelated to the running stack.

                if stats is not None:
                    canary_input = metrics.to_input(
                        stats,
                        current_mode="canary" if snap.app_mode == 1 else "stable",
                        target_mode="canary" if snap.app_mode == 1 else "stable",
                    )
                    try:
                        decisions["canary_safety"] = opa.query(
                            "canary_safety", canary_input, opa_url
                        )
                    except opa.OpaError as exc:
                        decisions["canary_safety"] = f"OPA error: {exc}"
                else:
                    decisions["canary_safety"] = "(awaiting first window)"

                event = {
                    "event": "scrape",
                    "metrics": {
                        "uptime_seconds": snap.uptime_seconds,
                        "app_mode": snap.app_mode,
                        "chaos_active": snap.chaos_active,
                    },
                    "window": stats.__dict__ if stats else None,
                    "decisions": {
                        domain: (
                            {"allow": d.allow, "violations": [v.__dict__ for v in d.violations]}
                            if isinstance(d, opa.Decision)
                            else {"error": d}
                        )
                        for domain, d in decisions.items()
                    },
                }
                history.append(history_path, event)

                live.update(
                    Panel(
                        Group(
                            _metrics_panel(stats, snap),
                            _policy_panel(decisions),
                        ),
                        title="swiftdeploy status",
                        border_style="bold",
                    )
                )

                prev_snap = snap
                elapsed = time.time() - tick_started
                time.sleep(max(0.0, refresh_seconds - elapsed))
        except KeyboardInterrupt:
            console.print("\n[dim]exited.[/dim]")
            return 0
