"""Host environment readings for the pre-deploy gate. Cross-platform via
psutil so the same code works on Windows dev and Linux graders."""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

import psutil


@dataclass
class HostStats:
    disk_free_gb: float
    cpu_load_1m: float
    mem_used_pct: float
    disk_path: str

    def to_input(self) -> dict:
        return {
            "host": {
                "disk_free_gb": round(self.disk_free_gb, 3),
                "cpu_load_1m": round(self.cpu_load_1m, 3),
                "mem_used_pct": round(self.mem_used_pct, 3),
                "disk_path": self.disk_path,
            }
        }


def collect(disk_path: str = "/") -> HostStats:
    """On Windows, `/` resolves to the current drive root via shutil."""
    target = disk_path
    if os.name == "nt" and disk_path == "/":
        target = os.path.splitdrive(os.getcwd())[0] + os.sep

    usage = shutil.disk_usage(target)
    free_gb = usage.free / (1024 ** 3)

    try:
        load_1m, _, _ = psutil.getloadavg()
    except (AttributeError, OSError):
        load_1m = psutil.cpu_percent(interval=0.1) / 100.0 * psutil.cpu_count()

    mem_used_pct = psutil.virtual_memory().percent / 100.0

    return HostStats(
        disk_free_gb=free_gb,
        cpu_load_1m=load_1m,
        mem_used_pct=mem_used_pct,
        disk_path=target,
    )
