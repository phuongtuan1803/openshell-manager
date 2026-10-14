"""Host bootstrap command tests; sudo/systemd/Docker are inert offline doubles."""

import importlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parents[2] / "infra/dev-sandbox"
sys.path.insert(0, str(HERE))
bootstrap = importlib.import_module("bootstrap")
control = importlib.import_module("control")
sys.path.pop(0)


@pytest.fixture
def host(monkeypatch, tmp_path):
    config = tmp_path / ".config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Linux")
    monkeypatch.setattr(bootstrap.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "/usr/bin/" + name)
    exists = Path.exists
    monkeypatch.setattr(
        Path,
        "exists",
        lambda path: True if str(path) in {"/etc/debian_version", "/dev/kvm"} else exists(path),
    )
    monkeypatch.setattr(bootstrap.os, "access", lambda *a: True)
    calls, state = [], {"gateway": True, "unit": True}

    def run(argv, **kwargs):
        calls.append(argv)
        rc, output = 0, ""
        if "--version" in argv:
            output = "OpenShell 0.1.2"
        if argv[:4] == ["systemctl", "--user", "is-active", "openshell-gateway"]:
            rc = 0 if state["gateway"] else 1
        if argv[:4] == ["systemctl", "--user", "cat", "openshell-gateway"]:
            rc = 0 if state["unit"] else 1
            output = bootstrap.SERVICE if state["unit"] else ""
        if argv[:4] == ["systemctl", "--user", "enable", "--now"]:
            state["gateway"] = True
        return subprocess.CompletedProcess(argv, rc, output, "")

    fake = SimpleNamespace(command=run, oshell=lambda *a, **k: run(["openshell", *a], **k))
    return fake, calls, state, config


def test_healthy_gateway_reused_without_restart_or_sudo(host):
    fake, calls, _, config = host
    cfg = config / "openshell/gateway.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text((HERE / "gateway.vm.example.toml").read_text())
    tls = cfg.parent / "existing-tls"
    tls.write_text("DUMMY_TLS_MARKER")
    before = cfg.read_bytes()
    bootstrap.host(fake)
    bootstrap.host(fake)
    assert cfg.read_bytes() == before and tls.read_text() == "DUMMY_TLS_MARKER"
    assert not any(c[0] == "sudo" or "restart" in c or "enable" in c for c in calls)


def test_fresh_configuration_and_service_are_retryable(host):
    fake, calls, state, config = host
    state.update(gateway=False, unit=False)
    bootstrap.host(fake)
    cfg = config / "openshell/gateway.toml"
    assert cfg.exists() and 'compute_driver = "vm"' in cfg.read_text()
    unit = config / "systemd/user/openshell-gateway.service"
    assert unit.read_text() == bootstrap.SERVICE
    first = cfg.read_bytes(), unit.read_bytes()
    bootstrap.host(fake)
    assert (cfg.read_bytes(), unit.read_bytes()) == first
    assert sum("enable" in c for c in calls) == 1


def test_stopped_gateway_starts_existing_unit(host):
    fake, calls, state, _ = host
    state["gateway"] = False
    bootstrap.host(fake)
    assert any(c[:4] == ["systemctl", "--user", "enable", "--now"] for c in calls)
    assert not any(c[0] == "sudo" for c in calls)


def test_incompatible_configuration_is_preserved(host):
    fake, calls, _, config = host
    cfg = config / "openshell/gateway.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[openshell]\nversion = 2\n[openshell.gateway]\ncompute_driver = "docker"\n')
    before = cfg.read_bytes()
    with pytest.raises(ValueError, match="Incompatible gateway.toml preserved"):
        bootstrap.host(fake)
    assert cfg.read_bytes() == before
    assert not any("enable" in c or "restart" in c for c in calls)


def test_interrupted_config_write_does_not_replace_file(tmp_path):
    path = tmp_path / "gateway.toml"
    bootstrap.exclusive_file(path, "original")
    with pytest.raises(ValueError, match="Existing configuration preserved"):
        bootstrap.exclusive_file(path, "replacement")
    assert path.read_text() == "original"


def test_missing_gh_provision_uses_pinned_release_only(monkeypatch, tmp_path):
    import hashlib
    import io
    import tarfile

    dummy = b"OFFLINE EXECUTABLE FIXTURE"
    fake = SimpleNamespace(
        STATE=tmp_path,
        safe_dir=lambda p: p.mkdir(parents=True),
        github_provider=SimpleNamespace(GH_SHA256=hashlib.sha256(dummy).hexdigest()),
    )

    def download(url, path, expected):
        assert url == bootstrap.GH_URL and expected == bootstrap.GH_ARCHIVE_SHA
        with tarfile.open(path, "w:gz") as tar:
            entry = tarfile.TarInfo("gh_2.101.0_linux_amd64/bin/gh")
            entry.size = len(dummy)
            tar.addfile(entry, io.BytesIO(dummy))

    monkeypatch.setattr(bootstrap, "download", download)
    path = bootstrap.gh_binary(fake)
    assert path.read_bytes() == dummy and path.stat().st_mode & 0o777 == 0o755
    assert list(tmp_path.iterdir()) == [tmp_path / "tools"]


def test_check_does_not_bootstrap_or_write_state(monkeypatch, tmp_path):
    monkeypatch.setattr(control, "STATE", tmp_path / "absent")
    monkeypatch.setattr(
        control.bootstrap, "host", lambda *a: pytest.fail("No host changes in check")
    )
    monkeypatch.setattr(control, "setup", lambda mode: None)
    control.setup_report("check")
    assert not control.STATE.exists()


def test_dry_run_without_tools_has_no_side_effects(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(control, "STATE", tmp_path / "absent")
    monkeypatch.setattr(control.shutil, "which", lambda _: None)
    control.setup("dry-run")
    output = capsys.readouterr().out
    assert "SKIP" in output and "no changes in dry-run" in output
    assert not control.STATE.exists()


def test_missing_tools_install_pinned_package_and_retry(host, monkeypatch):
    fake, calls, _, _ = host
    monkeypatch.setattr(
        bootstrap.shutil,
        "which",
        lambda name: None if name in {"docker", "openshell"} else "/usr/bin/" + name,
    )
    downloaded = []

    def download(url, path, expected):
        downloaded.append((url, expected))
        path.write_bytes(b"INERT PACKAGE FIXTURE")

    monkeypatch.setattr(bootstrap, "download", download)
    bootstrap.host(fake)
    assert downloaded == [(bootstrap.DEB_URL, bootstrap.DEB_SHA)]
    assert any(c[:3] == ["sudo", "apt-get", "install"] and "docker.io" in c for c in calls)
    assert any(c[:3] == ["sudo", "dpkg", "--unpack"] for c in calls)
    assert not any("restart" in c or "delete" in c for c in calls)


def test_read_only_sync_flag_reaches_guest(monkeypatch):
    import base64
    import json

    requests = []

    def execute(*argv, **kwargs):
        requests.append(json.loads(base64.b64decode(argv[-1])))
        return subprocess.CompletedProcess(argv, 0, '{"status":"PASS","state":"clean"}', "")

    monkeypatch.setattr(control, "execute", execute)
    monkeypatch.setattr(control, "task_active", lambda: False)
    control.sync_checkout(check=True)
    assert requests == [{"action": "sync", "read_only": True, "base": "main"}]


def test_active_host_task_blocks_sync_through_completion(monkeypatch, tmp_path):
    import fcntl

    monkeypatch.setattr(control, "STATE", tmp_path)
    monkeypatch.setattr(control, "session", lambda *a, **k: pytest.fail("Active task blocks Git"))
    with (tmp_path / "task.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert control.sync_checkout()["state"] == "active-agent"
    assert not control.task_active()
