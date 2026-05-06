# Canary safety pre-promote policy.
#
# Asks: is the running canary healthy enough to promote (or to fall back from)?
#
# Reads thresholds from data.thresholds.canary_safety. The CLI scrapes
# /metrics, computes a windowed error_rate and p99_latency_ms, and sends
# them as input.metrics. No values are hardcoded here.
package canary_safety

import rego.v1

default decision := {
	"allow": false,
	"violations": [{
		"rule": "policy_evaluation_failed",
		"message": "canary_safety.decision did not produce a result",
		"observed": null,
		"threshold": null,
	}],
	"domain": "canary_safety",
}

thresholds := data.thresholds.canary_safety

# A promotion to "stable" is the rollback path — it is always allowed,
# regardless of canary health, because rolling back must not be gated by
# the very signals that prove rollback is needed.
violations contains v if {
	input.target_mode != "stable"
	rate := input.metrics.error_rate
	rate > thresholds.max_error_rate
	v := {
		"rule": "max_error_rate",
		"message": sprintf("Error rate %v over %vs exceeds the %v threshold.", [rate, thresholds.window_seconds, thresholds.max_error_rate]),
		"observed": rate,
		"threshold": thresholds.max_error_rate,
	}
}

violations contains v if {
	input.target_mode != "stable"
	p99 := input.metrics.p99_latency_ms
	p99 > thresholds.max_p99_latency_ms
	v := {
		"rule": "max_p99_latency_ms",
		"message": sprintf("p99 latency %vms over %vs exceeds the %vms threshold.", [p99, thresholds.window_seconds, thresholds.max_p99_latency_ms]),
		"observed": p99,
		"threshold": thresholds.max_p99_latency_ms,
	}
}

violations contains v if {
	input.target_mode != "stable"
	samples := input.metrics.sample_requests
	samples < thresholds.min_sample_requests
	v := {
		"rule": "min_sample_requests",
		"message": sprintf("Only %v sample requests in the %vs window — need at least %v before promoting a canary.", [samples, thresholds.window_seconds, thresholds.min_sample_requests]),
		"observed": samples,
		"threshold": thresholds.min_sample_requests,
	}
}

decision := {
	"allow": count(violations) == 0,
	"violations": [v | some v in violations],
	"domain": "canary_safety",
}
