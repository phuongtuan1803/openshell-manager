"""Native provider lifecycle and failure tests; no network, credentials or models."""

import copy
import importlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

HERE = Path(__file__).resolve().parents[2] / "infra/dev-sandbox"
sys.path.insert(0, str(HERE))
provider = importlib.import_module("github_provider")
control = importlib.import_module("control")
sys.path.pop(0)


@pytest.fixture
def native(monkeypatch, tmp_path):
    wanted = provider.profile(control)
    state = {
        "profile": {**copy.deepcopy(wanted), "resource_version": 1},
        "exists": True,
        "attached": True,
        "ready": True,
        "stale": False,
    }
    calls = []
    fake = SimpleNamespace(
        HERE=HERE,
        STATE=tmp_path,
        NAME=control.NAME,
        require=control.require,
        normalize=control.normalize,
        save=control.save,
        safe_dir=control.safe_dir,
    )

    def api(*args):
        calls.append(args)
        if args[:3] == ("provider", "profile", "list"):
            return [{"id": provider.PROVIDER, "scope": "workspace"}] if state["profile"] else []
        if args[:3] == ("provider", "profile", "describe"):
            return copy.deepcopy(state["profile"])
        if args[:2] == ("provider", "list"):
            return {
                "providers": [
                    {
                        "name": provider.PROVIDER,
                        "type": provider.PROVIDER,
                        "credential_keys": state.get("keys", ["GITHUB_TOKEN"]),
                    }
                ]
                if state["exists"]
                else [],
                "next_page_token": "",
            }
        if args[:3] == ("sandbox", "provider", "list"):
            return {"providers": [{"name": provider.PROVIDER}] if state["attached"] else []}
        if args[:2] == ("policy", "get"):
            return {
                "policy": {
                    "network_policies": {
                        provider.RULE: None if state["stale"] else provider.injected_rule(wanted)
                    }
                }
            }
        pytest.fail("Unexpected native API operation")

    def oshell(*args, **kwargs):
        calls.append(args)
        if args[:3] == ("provider", "profile", "update"):
            value = yaml.safe_load(Path(args[args.index("-f") + 1]).read_text())
            assert value["resource_version"] == state["profile"]["resource_version"]
            assert provider.comparable(value) == provider.comparable(wanted)
            state["profile"] = {**value, "resource_version": value["resource_version"] + 1}
        if args[:3] == ("provider", "profile", "import"):
            assert Path(args[args.index("-f") + 1]) == HERE / "policies/github.yaml"
            state["profile"] = {**copy.deepcopy(wanted), "resource_version": 1}
        if args[:2] == ("provider", "create"):
            assert not state["exists"] and "--from-existing" in args
            state["exists"] = True
        if args[:2] == ("provider", "update"):
            assert "--credential" not in args and "--from-existing" not in args
            state["stale"] = False
        if args[:3] == ("sandbox", "provider", "attach"):
            state["attached"] = True
        return subprocess.CompletedProcess(args, 0, "", "")

    def verify(*args):
        if not state["ready"]:
            raise ValueError("Provider reconciliation did not activate")
        return {"ready": True, "policy_hash": "mock", "provider_resource_version": "1"}

    fake.api = api
    fake.oshell = oshell
    fake.effective = lambda *a, **k: {"hash": "mock"}
    monkeypatch.setattr(provider, "tool", lambda *a, **k: None)
    monkeypatch.setattr(provider, "verify", verify)
    return fake, state, calls


def test_first_setup_imports_creates_attaches_without_credential_arguments(native, monkeypatch):
    fake, state, calls = native
    state.update(profile=None, exists=False, attached=False)
    monkeypatch.setenv("GITHUB_TOKEN", "DUMMY_CREDENTIAL_NEVER_LOG")
    result = provider.reconcile(fake, {"policy": {}})
    assert result["ready"] and not result["credentials_reused"]
    assert any(c[:3] == ("provider", "profile", "import") for c in calls)
    assert any(c[:2] == ("provider", "create") for c in calls)
    assert any(c[:3] == ("sandbox", "provider", "attach") for c in calls)
    assert "DUMMY_CREDENTIAL_NEVER_LOG" not in json.dumps(calls)


def test_existing_provider_is_idempotent_and_never_changes_credentials(native):
    fake, state, calls = native
    before = copy.deepcopy(state)
    provider.reconcile(fake, {"policy": {}})
    provider.reconcile(fake, {"policy": {}})
    assert state == before
    assert not any(
        c[:2] in {("provider", "update"), ("provider", "create")}
        or c[:3] in {("provider", "profile", "update"), ("sandbox", "provider", "attach")}
        for c in calls
    )


def test_check_reuses_provider_without_writing_receipt(native):
    fake, _, _ = native
    fake.save = lambda *a: pytest.fail("Read-only check cannot save a receipt")
    assert provider.reconcile(fake, {"policy": {}}, "check")["ready"]


def test_profile_changes_use_registry_revision_and_config_only_refresh(native):
    fake, state, calls = native
    state["profile"]["description"] = "Old profile"
    provider.reconcile(fake, {"policy": {}})
    assert state["profile"]["resource_version"] == 2
    assert any(c[:2] == ("provider", "update") and "--config" in c and "--wait" in c for c in calls)
    assert not any("--credential" in c or "--from-existing" in c for c in calls)


def test_interrupted_profile_change_reconciles_stale_attachment(native):
    fake, state, calls = native
    state["stale"] = True
    provider.reconcile(fake, {"policy": {}})
    assert not state["stale"]
    assert not any(c[:3] == ("provider", "profile", "update") for c in calls)
    assert any(c[:2] == ("provider", "update") for c in calls)


@pytest.mark.parametrize("existing", [False, True])
def test_missing_credentials_stop_without_replacement(native, monkeypatch, existing, capsys):
    fake, state, calls = native
    state.update(exists=existing, keys=[])
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    with pytest.raises(ValueError, match="credential"):
        provider.reconcile(fake, {"policy": {}})
    assert not any(c[:2] in {("provider", "create"), ("provider", "update")} for c in calls)
    assert "PASS" not in capsys.readouterr().out


def test_failed_activation_does_not_record_readiness(native):
    fake, state, _ = native
    state["ready"] = False
    with pytest.raises(ValueError, match="did not activate"):
        provider.reconcile(fake, {"policy": {}})
    assert not (fake.STATE / "github-ready.json").exists()


def test_check_does_not_repair_profile_or_attach(native):
    fake, state, calls = native
    state["profile"]["description"] = "Old"
    with pytest.raises(ValueError, match="run setup.sh"):
        provider.reconcile(fake, {"policy": {}}, "check")
    assert not any(c[:3] == ("provider", "profile", "update") for c in calls)


def test_missing_write_permissions_report_instructions_without_api_output(capsys, monkeypatch):
    fake = SimpleNamespace(
        require=control.require,
        execute=lambda *a, **k: subprocess.CompletedProcess(
            a,
            0,
            json.dumps(
                {
                    "full_name": provider.REPOSITORY,
                    "permissions": {"push": False},
                    "unrelated": "DUMMY_CREDENTIAL_NEVER_LOG",
                }
            ),
            "",
        ),
    )
    monkeypatch.setattr(provider, "api", lambda c, *a, **k: c.execute(*a, **k))
    presence = provider.credential_presence
    try:
        provider.credential_presence = lambda _: None
        with pytest.raises(ValueError, match="Contents: read/write"):
            provider.permissions(fake)
    finally:
        provider.credential_presence = presence
    assert "DUMMY_CREDENTIAL_NEVER_LOG" not in capsys.readouterr().out


def test_native_lint_duplicate_id_uses_temporary_id_only(native):
    fake, _, _ = native
    calls = []

    def native_lint(*args, **kwargs):
        path = Path(args[-1])
        calls.append(path)
        value = yaml.safe_load(path.read_text())
        if path == HERE / "policies/github.yaml":
            return subprocess.CompletedProcess(args, 1, "already exists", "")
        assert value["id"].startswith("oma-lint-")
        value["id"] = provider.PROVIDER
        assert value == provider.profile(fake)
        return subprocess.CompletedProcess(args, 0, "", "")

    fake.oshell = native_lint
    provider.lint(fake, HERE / "policies/github.yaml")
    assert len(calls) == 2


def test_explicit_credential_entry_keeps_values_out_of_argv_and_output(monkeypatch, capsys):
    secret = "DUMMY_CREDENTIAL_NEVER_LOG"
    monkeypatch.setattr(provider.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(provider.getpass, "getpass", lambda _: secret)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        assert secret not in json.dumps(args)
        assert kwargs["env"]["GITHUB_TOKEN"] == secret
        assert args[:4] == ["openshell", "provider", "update", provider.PROVIDER]
        return subprocess.CompletedProcess(args, 0, secret, secret)

    monkeypatch.setattr(provider.subprocess, "run", run)
    fake = SimpleNamespace(
        require=control.require,
        api=lambda *a: {
            "providers": [
                {
                    "name": provider.PROVIDER,
                    "type": provider.PROVIDER,
                    "credential_keys": ["GITHUB_TOKEN"],
                }
            ]
        },
    )
    provider.credentials(fake)
    assert calls and secret not in capsys.readouterr().out


def test_revoked_token_returns_specific_refresh_instruction(monkeypatch):
    monkeypatch.setattr(provider, "credential_presence", lambda _: None)
    monkeypatch.setattr(
        provider,
        "api",
        lambda *a, **k: subprocess.CompletedProcess(
            a, 1, '{"message":"Bad credentials"}', "HTTP 401"
        ),
    )
    with pytest.raises(ValueError, match="github.sh credential"):
        provider.permissions(SimpleNamespace(require=control.require))


@pytest.mark.parametrize(
    "field", ["policy_active", "credentials_installed", "launch_environment_installed"]
)
def test_ready_requires_policy_environment_and_credentials(field):
    desired = {
        "sandbox": control.NAME,
        "sandbox_id": "mock",
        "policy_hash": "hash",
        "config_revision": "1",
        "provider_env_revision": "2",
        "attachment_epoch": "3",
        "provider_resource_version": "1",
    }
    observed = {
        **desired,
        "policy_active": True,
        "credentials_installed": True,
        "launch_environment_installed": True,
    }
    observed[field] = False
    result = {
        "targets": [
            {
                "state": "ready",
                "wait_outcome": "complete",
                "receipt": {"provider": provider.PROVIDER, "desired": desired},
                "observed": observed,
            }
        ]
    }
    with pytest.raises(ValueError, match="did not activate"):
        provider.receipt(control, {"id": "mock"}, result, "hash")


def test_git_uses_shared_guest_session_without_credentials_in_host_arguments():
    calls = []
    fake = SimpleNamespace(
        session=lambda *a, **k: (
            calls.append((a, k)) or subprocess.CompletedProcess(a, 0, "refs", "")
        ),
        require=control.require,
    )
    assert (
        provider.git(
            fake, "ls-remote", "https://github.com/example/project.git"
        ).stdout
        == "refs"
    )
    action, fields = calls[0]
    assert action == ("git",)
    assert fields["argv"] == ["ls-remote", f"https://github.com/{provider.REPOSITORY}.git"]
    assert fields["root"] == "/sandbox" and fields["check"] is False
