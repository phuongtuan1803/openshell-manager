"""Minimal Linux Lite/Ubuntu host bootstrap; never replaces existing configuration."""

import getpass
import hashlib
import os
import platform
import shutil
import tempfile
import time
import tomllib
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEB_URL = "https://github.com/NVIDIA/OpenShell/releases/download/v0.1.2/openshell_0.1.2-1_amd64.deb"
DEB_SHA = "1f5416ea08f32fdc621f20a2cc0324298e60aba8bbe996459d9e3b44195f23df"
GH_URL = "https://github.com/cli/cli/releases/download/v2.101.0/gh_2.101.0_linux_amd64.tar.gz"
GH_ARCHIVE_SHA = "9bca2d1c16825f109907a23307628a2f0698fbf99662b73a5cf0b020293072b8"
SERVICE = """[Unit]
Description=OpenShell development gateway
After=default.target

[Service]
ExecStart=/usr/bin/openshell-gateway
Restart=on-failure

[Install]
WantedBy=default.target
"""


def download(url, path, expected):
    with urllib.request.urlopen(url, timeout=60) as response, path.open("wb") as output:
        shutil.copyfileobj(response, output)
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        path.unlink()
        raise ValueError("Release checksum mismatch; nothing installed. Retry setup.sh.")


def exclusive_file(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise ValueError("Existing configuration preserved; inspect " + str(path))
    # Atomic write makes a retry safe after an interrupted setup.
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content.encode())
    temporary.chmod(0o600)
    try:
        os.link(temporary, path)
    finally:
        temporary.unlink()


def host(control):
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise ValueError("Supported host: Linux Lite/Ubuntu x86_64 with KVM.")
    if not Path("/etc/debian_version").exists():
        raise ValueError(
            "Automatic bootstrap supports Ubuntu/Linux Lite; install pinned tools manually."
        )
    run = control.command
    required = {"git": "git", "curl": "curl", "docker": "docker.io"}
    packages = [package for binary, package in required.items() if not shutil.which(binary)]
    # control imports PyYAML; setup.sh provisions it before reaching this function.
    if packages:
        run(["sudo", "apt-get", "update"], interactive=True, timeout=600)
        run(
            ["sudo", "apt-get", "install", "-y", "ca-certificates", *packages],
            interactive=True,
            timeout=1200,
        )
    tools = ("openshell", "openshell-gateway", "/usr/libexec/openshell/openshell-driver-vm")
    if not all(shutil.which(tool) for tool in tools):
        # Refuse an upgrade/downgrade of installed incompatible binaries.
        for tool in tools:
            if shutil.which(tool):
                result = run([tool, "--version"])
                if "0.1.2" not in result.stdout:
                    raise ValueError("Incompatible OpenShell preserved; install release 0.1.2.")
        with tempfile.TemporaryDirectory(prefix="manager-openshell-install-") as temporary:
            path = Path(temporary) / "openshell.deb"
            download(DEB_URL, path, DEB_SHA)
            run(["sudo", "dpkg", "--unpack", str(path)], interactive=True, timeout=600)
            run(["sudo", "dpkg", "--configure", "openshell"], interactive=True, timeout=600)
    for tool in tools:
        if "0.1.2" not in run([tool, "--version"]).stdout:
            raise ValueError("OpenShell CLI/Gateway/VM driver version mismatch; install 0.1.2.")
    docker = run(["docker", "info", "--format", "{{.ServerVersion}}"], check=False)
    if docker.returncode:
        if run(["systemctl", "is-active", "docker"], check=False).returncode:
            run(["sudo", "systemctl", "start", "docker"], interactive=True)
        if run(["docker", "info", "--format", "{{.ServerVersion}}"], check=False).returncode:
            run(["sudo", "usermod", "-aG", "docker", getpass.getuser()], interactive=True)
            raise ValueError("Docker access added. Sign out and back in, then rerun setup.sh.")
    if not Path("/dev/kvm").exists():
        raise ValueError("KVM missing. Enable virtualization in firmware, then rerun setup.sh.")
    if not os.access("/dev/kvm", os.R_OK | os.W_OK):
        run(["sudo", "usermod", "-aG", "kvm", getpass.getuser()], interactive=True)
        raise ValueError("KVM group access added. Sign out and back in, then rerun setup.sh.")
    config_root = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    cfg = config_root / "openshell/gateway.toml"
    if cfg.is_symlink():
        raise ValueError("Symlink gateway configuration preserved; review the configured path.")
    if cfg.exists():
        data = tomllib.loads(cfg.read_text())["openshell"]
        if (
            data.get("version") != 2
            or data["gateway"].get("compute_driver") != "vm"
            or data["gateway"].get("bind_address") != "127.0.0.1:17670"
        ):
            raise ValueError(
                "Incompatible gateway.toml preserved; select the reviewed VM configuration."
            )
    else:
        # Validate before starting the service; never touch local TLS material.
        exclusive_file(cfg, (HERE / "gateway.vm.example.toml").read_text())
    run(["openshell-gateway", "config", "preflight", "--path", str(cfg)])
    active = run(["systemctl", "--user", "is-active", "openshell-gateway"], check=False)
    if active.returncode:
        unit = config_root / "systemd/user/openshell-gateway.service"
        existing = run(["systemctl", "--user", "cat", "openshell-gateway"], check=False)
        if existing.returncode:
            if not unit.exists():
                exclusive_file(unit, SERVICE)
            run(["systemctl", "--user", "daemon-reload"])
        elif "ExecStart=/usr/bin/openshell-gateway\n" not in existing.stdout:
            raise ValueError("Incompatible gateway service preserved; review its ExecStart.")
        run(["systemctl", "--user", "enable", "--now", "openshell-gateway"])
    # Native gateway creates/reuses its own TLS configuration; wait for real response.
    for _ in range(30):
        if control.oshell("status", check=False).returncode == 0:
            return
        time.sleep(1)
    raise ValueError(
        "Gateway not responding. Run systemctl --user start openshell-gateway; retry setup.sh."
    )


def gh_binary(control):
    """Provision only the pinned executable; never install/copy GitHub account config."""
    import tarfile

    local = control.STATE / "tools/gh"
    with tempfile.TemporaryDirectory(prefix="manager-gh-install-") as temporary:
        archive = Path(temporary) / "gh.tar.gz"
        download(GH_URL, archive, GH_ARCHIVE_SHA)
        with tarfile.open(archive) as tar:
            member = tar.getmember("gh_2.101.0_linux_amd64/bin/gh")
            if not member.isfile():
                raise ValueError("Invalid gh release archive")
            content = tar.extractfile(member).read()
        if hashlib.sha256(content).hexdigest() != control.github_provider.GH_SHA256:
            raise ValueError("GitHub CLI checksum mismatch; nothing installed.")
        control.safe_dir(local.parent)
        if local.is_symlink():
            raise ValueError("Symlink in gh installation path")
        local.write_bytes(content)
        local.chmod(0o755)
    return local
