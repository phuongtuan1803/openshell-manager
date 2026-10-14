"""Run reviewed guest probes with fresh host identifiers and dummy-only fixtures."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

REQUIRED_TESTS = {
    "guest_identity",
    "host_home_ssh",
    "docker_socket",
    "host_dummy",
    "external_symlink",
    "filesystem_traversal",
    "private_runtime",
    "non_root_capabilities",
    "setuid_root",
    "protected_filesystem",
    "mount_privilege",
    "python",
    "git",
    "raw_ip_egress",
    "allowed_endpoint",
    "unauthorized_endpoint",
    "binary_network_scope",
    "credential_locations_env",
    "codex_startup",
    "paid_agent_workload",
}


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=True, timeout=120)


def validate(sandbox: str) -> dict:
    state = json.loads(run("openshell", "sandbox", "get", sandbox, "-o", "json").stdout)
    if state["phase"] != "Ready" or state["configuration_admission"]["state"] != "accepted":
        raise ValueError("Sandbox must be Ready with accepted configuration; inspect status/logs")
    probes = Path(__file__).with_name("validate_guest.py")
    destination = "/sandbox/manager-probes-" + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="manager-host-dummy-") as temp:
        directory = Path(temp)
        directory.chmod(0o755)
        dummy = directory / "dummy.txt"
        dummy.write_text("OMA_DUMMY_ONLY_NO_CREDENTIALS\n")
        dummy.chmod(0o644)
        run("openshell", "sandbox", "upload", sandbox, str(probes), destination)
        command = [
            "openshell",
            "sandbox",
            "exec",
            "--name",
            sandbox,
            "--no-login-shell",
            "--no-tty",
            "--workdir",
            "/sandbox",
            "--timeout",
            "90",
            "--",
            "/usr/bin/python3",
            destination + "/validate_guest.py",
            "--host-kernel",
            platform.release(),
            "--host-boot-id",
            Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "--host-home",
            str(Path.home()),
            "--host-dummy",
            str(dummy),
        ]
        # A nonzero probe result still contains evidence; transport/parse errors fail closed.
        result = subprocess.run(command, text=True, capture_output=True, check=False, timeout=120)
        guest = json.loads(result.stdout)
        rows = guest.get("results")
        if not rows or any(row.get("status") not in {"PASS", "FAIL", "SKIP"} for row in rows):
            raise ValueError("Guest returned missing or invalid test evidence")
        names = [row["test"] for row in rows]
        if len(names) != len(set(names)) or not set(names).issubset(REQUIRED_TESTS):
            raise ValueError("Guest returned duplicate or unexpected test evidence")
        if result.returncode == 0 and set(names) != REQUIRED_TESTS:
            raise ValueError("Guest success omitted required test evidence")
        for missing in sorted(REQUIRED_TESTS - set(names)):
            rows.append(
                {"test": missing, "status": "SKIP", "evidence": "Guest aborted before test"}
            )
        if result.returncode and not any(row["status"] == "FAIL" for row in rows):
            raise ValueError("Guest command failed without a recorded FAIL result")
        return {
            "date": datetime.now(UTC).isoformat(),
            "sandbox": sandbox,
            "sandbox_id": state["id"],
            "policy_hash": state["configuration_admission"]["policy_hash"],
            "validator_sha256": hashlib.sha256(probes.read_bytes()).hexdigest(),
            "host_kernel": platform.release(),
            "command": command,
            "host_fixture": "world-readable dummy; removed after probes; never uploaded",
            "results": rows,
            "exit_code": result.returncode,
            "model_calls": 0,
            "model_cost_usd": 0,
            "safe_to_enable_noninteractive_agents": False,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sandbox", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    # Reserve output first; never overwrite prior evidence or start probes on collision.
    fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        try:
            report = validate(args.sandbox)
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            report = {
                "results": [
                    {
                        "test": "validation_launcher",
                        "status": "FAIL",
                        "evidence": f"{type(exc).__name__}: incomplete evidence; inspect stage",
                    }
                ],
                "safe_to_enable_noninteractive_agents": False,
            }
        output.write(json.dumps(report, indent=2) + "\n")
    counts = {
        s: sum(row["status"] == s for row in report["results"]) for s in ("PASS", "FAIL", "SKIP")
    }
    print(json.dumps({"evidence": str(args.out), **counts, "noninteractive": "disabled"}))
    return int(bool(counts["FAIL"]))


if __name__ == "__main__":
    raise SystemExit(main())
