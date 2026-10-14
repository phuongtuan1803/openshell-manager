"""Credential-free probes; run only inside an actual OpenShell VM via sandbox exec."""

from __future__ import annotations

import argparse
import errno
import json
import os
import platform
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path


def command(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=20, check=False)


def denied_read(path: Path, *, directory: bool = False) -> bool:
    try:
        if directory:
            with os.scandir(path) as entries:
                next(entries, None)
        else:
            with path.open("rb") as stream:
                stream.read(1)
    except OSError as exc:
        return exc.errno in {errno.EACCES, errno.EPERM, errno.ENOENT, errno.ENOTDIR}
    return False


def curl(url: str) -> tuple[int, str]:
    result = command(
        "/usr/bin/curl", "-sS", "--max-time", "10", "-o", "/dev/null", "-w", "%{http_code}", url
    )
    return result.returncode, result.stdout


def permission_denied_connection(host: str) -> bool:
    try:
        with socket.create_connection((host, 443), timeout=5):
            return False
    except OSError as exc:
        return exc.errno in {errno.EACCES, errno.EPERM}


def network_checks(record) -> None:
    if not shutil.which("curl"):
        record("allowed_endpoint", None, "curl absent")
        record("unauthorized_endpoint", None, "curl absent")
        record("binary_network_scope", None, "curl absent")
        return
    # /rate_limit remains available when unauthenticated /zen exhausts GitHub's quota.
    code, http = curl("https://api.github.com/rate_limit")
    record(
        "allowed_endpoint",
        code == 0 and http == "200",
        f"curl GET api.github.com/rate_limit exit={code}; HTTP={http}",
    )
    denied = permission_denied_connection("example.com")
    denied_code, denied_http = curl("https://example.com/")
    record(
        "unauthorized_endpoint",
        denied and denied_code != 0,
        f"example.com TCP permission denied={denied}; curl exit={denied_code}; HTTP={denied_http}",
    )
    wrong_binary = permission_denied_connection("api.github.com")
    record(
        "binary_network_scope",
        wrong_binary,
        f"Python TCP to curl-only api.github.com permission denied={wrong_binary}",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-kernel", required=True)
    parser.add_argument("--host-boot-id", required=True)
    parser.add_argument("--host-home", required=True)
    parser.add_argument("--host-dummy", required=True)
    parser.add_argument("--network-only", action="store_true")
    args = parser.parse_args()
    results: list[dict[str, str]] = []

    def record(name: str, ok: bool | None, detail: str) -> None:
        results.append(
            {
                "test": name,
                "status": "SKIP" if ok is None else "PASS" if ok else "FAIL",
                "evidence": detail,
            }
        )

    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    vm = platform.release() != args.host_kernel and boot_id != args.host_boot_id
    vm = vm and Path.cwd() == Path("/sandbox")
    record(
        "guest_identity",
        vm,
        f"kernel={platform.release()}; distinct boot ID={boot_id != args.host_boot_id}; "
        f"cwd={Path.cwd()}",
    )
    if not vm:
        print(json.dumps({"results": results, "aborted": "Guest identity failed"}, indent=2))
        return 1
    if args.network_only:
        network_checks(record)
        print(json.dumps({"results": results, "model_calls": 0, "model_cost_usd": 0}, indent=2))
        return int(any(item["status"] == "FAIL" for item in results))

    home = Path(args.host_home)
    record(
        "host_home_ssh",
        denied_read(home, directory=True) and denied_read(home / ".ssh", directory=True),
        "Actual directory opens denied or absent",
    )
    record(
        "docker_socket",
        all(not Path(p).exists() for p in ("/var/run/docker.sock", "/run/docker.sock")),
        "Both socket paths absent",
    )
    record(
        "host_dummy",
        denied_read(Path(args.host_dummy)),
        "Host-confirmed world-readable dummy fixture cannot be opened",
    )
    with tempfile.TemporaryDirectory(prefix="oma-probes-", dir="/sandbox") as temp:
        work = Path(temp)
        external = work / "external-link"
        external.symlink_to(args.host_dummy)
        private = work / "private-link"
        private.symlink_to("/.openshell", target_is_directory=True)
        record(
            "external_symlink",
            denied_read(external) and denied_read(private, directory=True),
            "Host dummy symlink open and private-directory symlink listing denied",
        )
        traversal = work / ".." / ".." / args.host_dummy.lstrip("/")
        record(
            "filesystem_traversal",
            denied_read(traversal),
            "Traversal from guest workspace cannot reach host dummy",
        )
        record(
            "private_runtime",
            denied_read(Path("/.openshell"), directory=True),
            "Actual private runtime directory listing denied; stat alone is insufficient",
        )
        status = dict(
            line.split(":", 1)
            for line in Path("/proc/self/status").read_text().splitlines()
            if ":" in line
        )
        caps = all(
            int(status[key].strip(), 16) == 0
            for key in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
        )
        record(
            "non_root_capabilities",
            os.getuid() != 0
            and caps
            and status["NoNewPrivs"].strip() == "1"
            and status["Seccomp"].strip() == "2",
            f"uid={os.getuid()}; gid={os.getgid()}; capabilities zero={caps}; "
            f"no_new_privs={status['NoNewPrivs'].strip()}; seccomp={status['Seccomp'].strip()}",
        )
        try:
            os.setuid(0)
            escalation_denied = False
        except OSError as exc:
            escalation_denied = exc.errno == errno.EPERM
        record(
            "setuid_root",
            escalation_denied,
            "setuid(0) returned EPERM" if escalation_denied else "Unexpected escalation",
        )
        if not escalation_denied:
            print(json.dumps({"results": results, "aborted": "Privilege failure"}, indent=2))
            return 1
        try:
            Path("/etc/oma-negative-dummy").write_text("dummy")
            protected = False
        except OSError as exc:
            protected = exc.errno in {errno.EACCES, errno.EPERM, errno.EROFS}
        record(
            "protected_filesystem",
            protected,
            "Dummy /etc write denied" if protected else "Unexpected /etc write",
        )
        mountpoint = work / "mount"
        mountpoint.mkdir()
        mount = command("/usr/bin/mount", "-t", "tmpfs", "none", str(mountpoint))
        record(
            "mount_privilege",
            mount.returncode != 0
            and any(
                s in mount.stderr.lower()
                for s in ("permission denied", "must be superuser", "operation not permitted")
            ),
            f"Unprivileged tmpfs mount exit={mount.returncode}",
        )
        sample = work / "sample.py"
        sample.write_text("assert sum([1, 2, 3]) == 6\n")
        python = command("/usr/bin/python3", str(sample))
        record(
            "python",
            python.returncode == 0,
            f"Python {platform.python_version()}; calculation exit={python.returncode}",
        )
        git_version = command("/usr/bin/git", "--version").stdout.strip()
        git = command("/usr/bin/git", "init", "-q", str(work))
        add = command("/usr/bin/git", "-C", str(work), "add", "sample.py")
        commit = command(
            "/usr/bin/git",
            "-C",
            str(work),
            "-c",
            "user.name=Dummy",
            "-c",
            "user.email=dummy@example.invalid",
            "commit",
            "-qm",
            "dummy",
        )
        sample.write_text("assert sum([1, 2, 3]) == 6\n# reviewed dummy change\n")
        diff = command("/usr/bin/git", "-C", str(work), "diff", "--exit-code")
        record(
            "git",
            git.returncode == add.returncode == commit.returncode == 0 and diff.returncode == 1,
            f"{git_version}; init/add/commit successful; "
            f"diff detects change={diff.returncode == 1}; no remote",
        )

    try:
        with socket.create_connection(("1.1.1.1", 443), timeout=5):
            raw_denied = False
    except OSError as exc:
        raw_denied = exc.errno in {errno.EACCES, errno.EPERM}
    record(
        "raw_ip_egress",
        raw_denied,
        "TCP to 1.1.1.1:443 denied by permission"
        if raw_denied
        else "No deterministic permission-denial evidence",
    )
    network_checks(record)

    credential_paths = [
        home / ".codex/auth.json",
        home / ".aws/credentials",
        Path("/home/ubuntu/.codex/auth.json"),
        Path("/home/ubuntu/.ssh"),
        Path("/home/ubuntu/.aws/credentials"),
        Path("/root/.ssh"),
    ]
    absent = all(denied_read(p, directory=p.name == ".ssh") for p in credential_paths)
    names = [
        name
        for name in os.environ
        if any(
            marker in name.upper()
            for marker in (
                "API_KEY",
                "ACCESS_TOKEN",
                "AUTH_TOKEN",
                "PASSWORD",
                "SECRET",
                "CREDENTIAL",
            )
        )
    ]
    record(
        "credential_locations_env",
        absent and not names,
        f"Credential locations inaccessible; suspect environment variable count={len(names)}; "
        "values never printed",
    )
    codex = shutil.which("codex")
    if codex:
        version = command(codex, "--version")
        help_result = command(codex, "--help")
        record(
            "codex_startup",
            version.returncode == help_result.returncode == 0,
            f"{version.stdout.strip()}; --help exit={help_result.returncode}; "
            "no authentication or model invocation",
        )
    else:
        record("codex_startup", None, "Codex absent")
    record(
        "paid_agent_workload",
        None,
        "No paid calls approved; authentication/provider and model workflow remain unverified",
    )
    print(json.dumps({"results": results, "model_calls": 0, "model_cost_usd": 0}, indent=2))
    return int(any(item["status"] == "FAIL" for item in results))


if __name__ == "__main__":
    raise SystemExit(main())
