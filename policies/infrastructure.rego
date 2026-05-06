# Infrastructure pre-deploy policy.
#
# Asks: is the host environment safe to deploy onto right now?
#
# Reads thresholds from data.thresholds.infrastructure (rendered from
# manifest.yaml by `swiftdeploy init`). No values are hardcoded here.
package infrastructure

import rego.v1

default decision := {
	"allow": false,
	"violations": [{
		"rule": "policy_evaluation_failed",
		"message": "infrastructure.decision did not produce a result",
		"observed": null,
		"threshold": null,
	}],
	"domain": "infrastructure",
}

thresholds := data.thresholds.infrastructure

violations contains v if {
	disk_free := input.host.disk_free_gb
	disk_free < thresholds.min_disk_free_gb
	v := {
		"rule": "min_disk_free_gb",
		"message": sprintf("Disk free %vGB is below the %vGB minimum required to deploy.", [disk_free, thresholds.min_disk_free_gb]),
		"observed": disk_free,
		"threshold": thresholds.min_disk_free_gb,
	}
}

violations contains v if {
	load := input.host.cpu_load_1m
	load > thresholds.max_cpu_load
	v := {
		"rule": "max_cpu_load",
		"message": sprintf("1-minute CPU load %v exceeds the maximum allowed %v.", [load, thresholds.max_cpu_load]),
		"observed": load,
		"threshold": thresholds.max_cpu_load,
	}
}

violations contains v if {
	mem := input.host.mem_used_pct
	mem > thresholds.max_mem_used_pct
	v := {
		"rule": "max_mem_used_pct",
		"message": sprintf("Memory utilisation %v exceeds the maximum allowed %v.", [mem, thresholds.max_mem_used_pct]),
		"observed": mem,
		"threshold": thresholds.max_mem_used_pct,
	}
}

decision := {
	"allow": count(violations) == 0,
	"violations": [v | some v in violations],
	"domain": "infrastructure",
}
