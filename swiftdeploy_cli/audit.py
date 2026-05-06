"""Generate audit_report.md from history.jsonl. GitHub-Flavored Markdown
only — no HTML, no fancy widgets."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from . import history


CHAOS_LABEL = {0: "none", 1: "slow", 2: "error"}
MODE_LABEL = {0: "stable", 1: "canary"}


def _format_ts(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M:%S UTC")
    except ValueError:
        return ts


def _build_timeline(rows: list[dict]) -> list[tuple[str, str, str]]:
    """Collapse consecutive same-(mode,chaos) rows into spans."""
    timeline: list[tuple[str, str, str]] = []
    current: dict | None = None

    for row in rows:
        if row.get("event") != "scrape":
            continue
        m = row.get("metrics") or {}
        mode = MODE_LABEL.get(int(m.get("app_mode", 0)), "unknown")
        chaos = CHAOS_LABEL.get(int(m.get("chaos_active", 0)), "unknown")
        key = (mode, chaos)

        if current is None or (current["mode"], current["chaos"]) != key:
            if current is not None:
                timeline.append((current["start"], current["end"], f"mode={current['mode']}, chaos={current['chaos']}"))
            current = {"start": row["ts"], "end": row["ts"], "mode": mode, "chaos": chaos}
        else:
            current["end"] = row["ts"]

    if current is not None:
        timeline.append((current["start"], current["end"], f"mode={current['mode']}, chaos={current['chaos']}"))
    return timeline


def _build_violations(rows: list[dict]) -> list[tuple[str, str, str, str]]:
    """One row per violation occurrence: (ts, domain, rule, message)."""
    out = []
    for row in rows:
        decisions = row.get("decisions") or {}
        for domain, decision in decisions.items():
            if not isinstance(decision, dict):
                continue
            for v in decision.get("violations", []) or []:
                out.append((row.get("ts", ""), domain, v.get("rule", ""), v.get("message", "")))
    return out


def render(history_path: Path, report_path: Path) -> str:
    rows = history.read_all(history_path)

    lines: list[str] = []
    lines.append("# SwiftDeploy Audit Report")
    lines.append("")
    lines.append(f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}  ")
    lines.append(f"Source: `{history_path.name}`  ")
    lines.append(f"Total events: {len(rows)}")
    lines.append("")

    if not rows:
        lines.append("_No events recorded yet. Run `swiftdeploy status` to start collecting._")
        report = "\n".join(lines) + "\n"
        report_path.write_text(report, encoding="utf-8")
        return report

    timeline = _build_timeline(rows)
    lines.append("## Timeline")
    lines.append("")
    if timeline:
        lines.append("| Started | Ended | State |")
        lines.append("| --- | --- | --- |")
        for start, end, state in timeline:
            lines.append(f"| {_format_ts(start)} | {_format_ts(end)} | {state} |")
    else:
        lines.append("_No mode/chaos transitions recorded._")
    lines.append("")

    violations = _build_violations(rows)
    lines.append("## Policy violations")
    lines.append("")
    if violations:
        lines.append("| Time | Domain | Rule | Message |")
        lines.append("| --- | --- | --- | --- |")
        for ts, domain, rule, message in violations:
            safe_msg = message.replace("|", "\\|")
            lines.append(f"| {_format_ts(ts)} | {domain} | {rule} | {safe_msg} |")
    else:
        lines.append("_No policy violations recorded._")
    lines.append("")

    forced = [r for r in rows if r.get("event") == "force_override"]
    if forced:
        lines.append("## Forced overrides")
        lines.append("")
        lines.append("| Time | Command | Reason |")
        lines.append("| --- | --- | --- |")
        for r in forced:
            lines.append(f"| {_format_ts(r['ts'])} | {r.get('command', '')} | {r.get('reason', '')} |")
        lines.append("")

    report = "\n".join(lines) + "\n"
    report_path.write_text(report, encoding="utf-8")
    return report
