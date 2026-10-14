"""Offline launcher routing and failure handling; no OpenShell or model requests."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "sandbox_validation", Path(__file__).resolve().parents[1] / "src/openshell_manager/validate.py"
)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_launcher_uses_dummy_fixture_and_exact_uploaded_file(monkeypatch):
    calls = []
    state = {
        "phase": "Ready",
        "id": "dummy-id",
        "configuration_admission": {"state": "accepted", "policy_hash": "dummy-policy"},
    }

    def run(*args):
        calls.append(args)
        return SimpleNamespace(stdout=json.dumps(state))

    def execute(command, **kwargs):
        calls.append(command)
        fixture = Path(command[command.index("--host-dummy") + 1])
        assert fixture.read_text() == "OMA_DUMMY_ONLY_NO_CREDENTIALS\n"
        assert fixture.stat().st_mode & 0o777 == 0o644
        assert fixture.parent.stat().st_mode & 0o777 == 0o755
        upload = calls[-2]
        assert command[command.index("--") + 2] == upload[-1] + "/validate_guest.py"
        assert "--no-login-shell" in command and "--no-tty" in command
        assert "--auto-providers" not in command
        assert command[command.index("--workdir") + 1] == "/sandbox"
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "results": [
                        {"test": name, "status": "PASS", "evidence": "mock only"}
                        for name in launcher.REQUIRED_TESTS
                    ]
                }
            ),
        )

    monkeypatch.setattr(launcher, "run", run)
    monkeypatch.setattr(launcher.subprocess, "run", execute)
    report = launcher.validate("dummy-sandbox")
    assert report["sandbox_id"] == "dummy-id"
    assert report["safe_to_enable_noninteractive_agents"] is False
    fixture = Path(report["command"][report["command"].index("--host-dummy") + 1])
    assert not fixture.exists()


def test_not_ready_does_not_upload_or_execute(monkeypatch):
    calls = []

    def run(*args):
        calls.append(args)
        return SimpleNamespace(stdout=json.dumps({"phase": "Provisioning"}))

    monkeypatch.setattr(launcher, "run", run)
    with pytest.raises(ValueError, match="Ready"):
        launcher.validate("dummy")
    assert len(calls) == 1


@pytest.mark.parametrize(
    "rows",
    [[], [{"test": "guest_identity", "status": "PASS"}], [{"test": "invented", "status": "PASS"}]],
)
def test_missing_or_invented_success_evidence_fails_closed(monkeypatch, rows):
    state = {
        "phase": "Ready",
        "id": "dummy",
        "configuration_admission": {"state": "accepted", "policy_hash": "dummy"},
    }
    monkeypatch.setattr(launcher, "run", lambda *args: SimpleNamespace(stdout=json.dumps(state)))
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda *args, **kw: SimpleNamespace(returncode=0, stdout=json.dumps({"results": rows})),
    )
    with pytest.raises(ValueError):
        launcher.validate("dummy")
