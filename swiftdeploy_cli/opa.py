"""OPA client. The CLI's only job here is shipping inputs and surfacing
decisions. No allow/deny logic lives in this module."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field


class OpaError(Exception):
    """Base class for every OPA failure mode. Each subclass produces a
    distinct human-readable message in the CLI."""


class OpaUnreachable(OpaError):
    """No TCP connection — likely the container isn't running."""


class OpaTimeout(OpaError):
    """Connection or read timeout."""


class OpaUnhealthy(OpaError):
    """OPA responded but with a non-2xx status code."""


class OpaPolicyError(OpaError):
    """OPA returned 200 but the body has no usable decision."""


class OpaBadResponse(OpaError):
    """OPA returned 200 but the body wasn't valid JSON."""


@dataclass
class Violation:
    rule: str
    message: str
    observed: object = None
    threshold: object = None


@dataclass
class Decision:
    domain: str
    allow: bool
    violations: list[Violation] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


def query(domain: str, decision_input: dict, base_url: str, timeout: float = 3.0) -> Decision:
    """Hit POST /v1/data/<domain>/decision with {"input": ...}.

    Raises one of the OpaError subclasses for every distinct failure mode
    so the caller can render different messages."""
    url = f"{base_url.rstrip('/')}/v1/data/{domain}/decision"
    payload = json.dumps({"input": decision_input}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raise OpaUnhealthy(
            f"policy engine returned HTTP {exc.code} for domain '{domain}': {exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
            raise OpaTimeout(
                f"policy engine timeout after {timeout}s at {base_url}"
            ) from exc
        raise OpaUnreachable(
            f"policy engine unreachable at {base_url}: {reason}"
        ) from exc
    except TimeoutError as exc:
        raise OpaTimeout(
            f"policy engine timeout after {timeout}s at {base_url}"
        ) from exc

    if status != 200:
        raise OpaUnhealthy(f"policy engine returned HTTP {status} for domain '{domain}'")

    try:
        envelope = json.loads(body)
    except json.JSONDecodeError as exc:
        raise OpaBadResponse(
            f"policy engine returned non-JSON for domain '{domain}': {body[:200]}"
        ) from exc

    if "result" not in envelope:
        raise OpaPolicyError(
            f"policy '{domain}' returned no decision (is the package present in policies/?)"
        )

    result = envelope["result"]
    if not isinstance(result, dict) or "allow" not in result:
        raise OpaPolicyError(
            f"policy '{domain}' returned a malformed decision (no 'allow' field)"
        )

    violations = [
        Violation(
            rule=v.get("rule", "unknown"),
            message=v.get("message", ""),
            observed=v.get("observed"),
            threshold=v.get("threshold"),
        )
        for v in result.get("violations", [])
    ]

    return Decision(
        domain=domain,
        allow=bool(result["allow"]),
        violations=violations,
        raw=result,
    )


def is_healthy(base_url: str, timeout: float = 2.0) -> bool:
    """Lightweight liveness probe used by the status dashboard."""
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/health", timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError):
        return False


def render_decision(decision: Decision) -> str:
    """Format a decision for human consumption. Used by deploy/promote
    when the gate fails."""
    if decision.allow:
        return f"[{decision.domain}] ALLOW — no violations"
    lines = [f"[{decision.domain}] DENY — {len(decision.violations)} violation(s):"]
    for v in decision.violations:
        lines.append(f"  - {v.rule}: {v.message}")
        if v.observed is not None or v.threshold is not None:
            lines.append(f"      observed={v.observed!r} threshold={v.threshold!r}")
    return "\n".join(lines)
