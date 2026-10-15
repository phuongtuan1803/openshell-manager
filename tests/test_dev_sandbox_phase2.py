"""Offline boundary and operator tests. No OpenShell, Docker or model calls."""

import base64
import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1] / "src/openshell_manager"
sys.path.insert(0, str(HERE.parent))
spec = importlib.util.spec_from_file_location("openshell_manager.phase2_control", HERE / "control.py")
control = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = control
spec.loader.exec_module(control)
guest_spec = importlib.util.spec_from_file_location("phase2_guest", HERE / "guest.py")
guest = importlib.util.module_from_spec(guest_spec)
guest_spec.loader.exec_module(guest)
sys.path.pop(0)


@pytest.fixture(autouse=True)
def legacy_project_scopes(monkeypatch):
    # These pre-profile core tests bypass configure. Scope fixtures belong to tests,
    # never to an installed Manager's default source tree.
    fixtures = HERE.parents[1] / 'tests/fixtures/legacy'
    monkeypatch.setattr(control, 'SOURCE_MANIFEST', fixtures / 'source-manifest.txt')
    monkeypatch.setattr(control, 'EDIT_SCOPE', fixtures / 'edit-scope.txt')


def encoded(value):
    return base64.b64encode(value).decode()


@pytest.mark.parametrize(
    "name",
    [
        "../outside.py",
        "/etc/passwd",
        ".codex/auth.json",
        "infra/secrets/source.py",
        "docs/html/source.py",
    ],
)
def test_patch_rejects_sensitive_paths(name):
    with pytest.raises(ValueError):
        control.patch_from_files({name: b"old\n"}, {name: encoded(b"new\n")})


@pytest.mark.parametrize(
    "content",
    [
        b"\0binary\n",
        b"ghp_" + b"a" * 30 + b"\n",
        b"-----BEGIN PRIVATE KEY-----\n",
        b"no final newline",
    ],
)
def test_unsafe_patch_content(content):
    with pytest.raises(ValueError):
        control.patch_from_files({"source.py": b"old\n"}, {"source.py": encoded(content)})


def test_patch_rejects_scope_and_unexpected_file():
    with pytest.raises(ValueError, match="scope"):
        control.patch_from_files({"AGENTS.md": b"old\n"}, {"AGENTS.md": encoded(b"new\n")}, [])
    with pytest.raises(ValueError, match="scope"):
        control.patch_from_files({"a.py": b"a\n"}, {"a.py": encoded(b"b\n"), "b.py": None})


@pytest.mark.parametrize("changed", [b"new\n", None])
def test_generated_patch_is_valid_and_never_runs_guest_content(tmp_path, changed):
    (tmp_path / "a.py").write_bytes(b"old\n")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    patch = control.patch_from_files(
        {"a.py": b"old\n"}, {"a.py": None if changed is None else encoded(changed)}
    )
    subprocess.run(["git", "-C", str(tmp_path), "apply", "--check", "-"], input=patch, check=True)
    assert (tmp_path / "a.py").read_bytes() == b"old\n"


def test_patch_preserves_text_that_looks_like_commands(tmp_path):
    value = b"$(touch /tmp/SHOULD_NEVER_RUN)\n"
    patch = control.patch_from_files({"a.py": b"old\n"}, {"a.py": encoded(value)})
    assert value in patch


def test_guest_rejects_symlink_and_outside_source(tmp_path):
    (tmp_path / "a.py").symlink_to("/etc/passwd")
    with pytest.raises(ValueError, match="symlink"):
        guest.collect(tmp_path, ["a.py"])
    (tmp_path / "a.py").unlink()
    (tmp_path / "a.py").write_text("safe\n")
    (tmp_path / "outside.py").write_text("not approved\n")
    with pytest.raises(ValueError, match="Unexpected"):
        guest.collect(tmp_path, ["a.py"])


def test_guest_rejects_symlink_parent_and_special_file(tmp_path):
    (tmp_path / "dir").symlink_to(tmp_path)
    with pytest.raises(ValueError):
        guest.collect(tmp_path, ["dir/a.py"])


def test_host_state_symlink_rejected(tmp_path):
    (tmp_path / "link").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="Symlink"):
        control.safe_dir(tmp_path / "link/child")


@pytest.mark.parametrize("phase", ["Stopped", "Error", "Provisioning", "Pending"])
def test_not_ready(phase):
    with pytest.raises(ValueError, match="not Ready"):
        control.ready({"name": control.NAME, "phase": phase})


@pytest.fixture
def fake_effective(monkeypatch):
    monkeypatch.setattr(
        control,
        "oshell",
        lambda *args: subprocess.CompletedProcess(
            args, 0, json.dumps({"settings": {"proposal_approval_mode": {"value": "manual"}}}), ""
        ),
    )
    policy = control.policy_for({"binaries": ["/usr/bin/codex"]})
    info = {
        "name": control.NAME,
        "phase": "Ready",
        "configuration_admission": {
            "state": "accepted",
            "policy_version": 4,
            "policy_hash": "hash",
        },
    }
    payload = {
        "version": 4,
        "active_version": 4,
        "status": "effective",
        "hash": "hash",
        "policy_source": "sandbox",
        "policy": policy,
    }
    history = {"revisions": [{"version": 4, "status": "loaded", "hash": "hash"}]}

    def api(*args):
        if args[:2] == ("sandbox", "get"):
            return info
        if args[:2] == ("policy", "get"):
            return payload
        return history

    monkeypatch.setattr(control, "api", api)
    return info, payload, history


def test_loaded_and_effective(fake_effective):
    assert control.effective()["version"] == 4


@pytest.mark.parametrize("kind", ["pending", "hash", "provider", "admission", "revision", "drift"])
def test_incompatible_policy_fails_closed(fake_effective, kind):
    info, payload, history = fake_effective
    expected = copy.deepcopy(payload["policy"])
    if kind == "pending":
        history["revisions"][0]["status"] = "pending"
    elif kind == "hash":
        history["revisions"][0]["hash"] = "other"
    elif kind == "provider":
        payload["policy_source"] = "global"
    elif kind == "admission":
        info["configuration_admission"]["policy_hash"] = "other"
    elif kind == "revision":
        payload["active_version"] = 3
    else:
        payload["policy"]["filesystem_policy"]["read_write"].append("/host")
    with pytest.raises(ValueError):
        control.effective(expected)


def test_credential_status_never_prints_output(monkeypatch, capsys):
    monkeypatch.setattr(
        control,
        "execute",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 1, "SECRET_API_KEY", "SECRET_COOKIE"
        ),
    )
    assert not control.login_status({"tools": {"launcher": "/codex"}})
    output = capsys.readouterr().out
    assert "LOGIN REQUIRED" in output and "SECRET" not in output


def test_missing_dependency_has_recovery(monkeypatch):
    monkeypatch.setattr(control.shutil, "which", lambda _: None)
    with pytest.raises(ValueError, match="Missing dependency: openshell"):
        control.preflight()


def test_denial_parser_redacts_unrelated_logs():
    raw = "SECRET_COOKIE=xxx\nDENIED /usr/bin/curl(1000) -> example.com:443 [reason:policy_denied]"
    rows = control.denials(raw)
    assert len(rows) == 1
    row = next(iter(rows.values()))
    assert row["host"] == "example.com" and row["binaries"] == ["/usr/bin/curl"]
    assert "SECRET" not in json.dumps(rows)
    assert row["path"] == "unavailable"


@pytest.mark.parametrize("host", ["*", "*.example.com", "127.0.0.1", "evil;echo", "a..b"])
def test_no_wildcards_or_shell_in_rules(host):
    with pytest.raises(ValueError):
        control.update_args(
            {
                "id": "review_test",
                "host": host,
                "port": 443,
                "access": "full",
                "binaries": ["/usr/bin/curl"],
            }
        )


def test_update_requires_confirmation_before_apply(monkeypatch, tmp_path):
    data = {"policy": {"network_policies": {}}}
    calls = []
    monkeypatch.setattr(control, "effective", lambda _: {"version": 1})
    monkeypatch.setattr(control, "oshell", lambda *args: calls.append(args))
    monkeypatch.setattr(control, "confirm", lambda _: (_ for _ in ()).throw(ValueError("Declined")))
    rule = {
        "id": "review_test",
        "host": "example.com",
        "port": 443,
        "access": "full",
        "binaries": ["/usr/bin/curl"],
    }
    with pytest.raises(ValueError, match="Declined"):
        control.update(data, rule)
    assert len(calls) == 1 and "--dry-run" in calls[0]


def test_approval_waits_verifies_and_records(monkeypatch, tmp_path):
    monkeypatch.setattr(control, "STATE", tmp_path)
    data = {"policy": {"network_policies": {}}}
    revisions = iter(
        [
            {"version": 1, "hash": "before", "policy": data["policy"]},
            {"version": 1},
            {"version": 2, "hash": "after", "policy": {}},
        ]
    )
    monkeypatch.setattr(control, "effective", lambda _: next(revisions))
    calls = []
    monkeypatch.setattr(control, "oshell", lambda *args: calls.append(args))
    monkeypatch.setattr(control, "confirm", lambda _: None)
    rule = {
        "id": "review_test",
        "host": "example.com",
        "port": 443,
        "access": "full",
        "binaries": ["/usr/bin/curl"],
    }
    control.update(data, rule)
    assert "--dry-run" in calls[0] and "--wait" in calls[1]
    assert list((tmp_path / "history").glob("*.json"))
    assert control.read(tmp_path / "state.json")["policy"]["network_policies"][rule["id"]]


def test_rejection_never_changes_native_policy(monkeypatch, tmp_path):
    monkeypatch.setattr(control, "STATE", tmp_path)
    control.save(tmp_path / "inbox.json", {"review_test": {"status": "pending"}})
    monkeypatch.setattr(control, "state", lambda **_: {"policy": {}})
    monkeypatch.setattr(
        control, "effective", lambda _: {"version": 3, "hash": "hash", "policy": {}}
    )
    monkeypatch.setattr(control, "confirm", lambda _: None)
    monkeypatch.setattr(control, "oshell", lambda *_: pytest.fail("No policy command allowed"))
    control.rules("reject", "review_test")
    assert control.read(tmp_path / "inbox.json")["review_test"]["status"] == "rejected"


def test_rollback_removes_only_selected_rule(monkeypatch, tmp_path):
    monkeypatch.setattr(control, "STATE", tmp_path)
    policy = {"network_policies": {"review_test": {}, "codex_auth": {}}}
    monkeypatch.setattr(control, "state", lambda **_: {"policy": copy.deepcopy(policy)})
    revisions = iter(
        [
            {"version": 2, "hash": "before", "policy": policy},
            {"version": 2},
            {"version": 3, "hash": "after", "policy": {}},
        ]
    )
    monkeypatch.setattr(control, "effective", lambda _: next(revisions))
    monkeypatch.setattr(control, "confirm", lambda _: None)
    calls = []
    monkeypatch.setattr(control, "oshell", lambda *args: calls.append(args))
    control.rules("rollback", "review_test")
    stored = control.read(tmp_path / "state.json")
    assert stored["policy"]["network_policies"] == {"codex_auth": {}}
    assert all("--remove-rule" in args and "review_test" in args for args in calls)


def test_default_rules_exclude_unreviewed_hosts():
    hosts = {r["host"] for r in control.settings()["rules"]}
    assert hosts == {"auth.openai.com", "chatgpt.com", "api.github.com"}


def test_repeat_setup_reuses_resources(monkeypatch, tmp_path):
    monkeypatch.setattr(control.github_provider, "reconcile", lambda *a, **k: None)
    monkeypatch.setattr(control, "STATE", tmp_path)
    tools = {"binaries": ["/usr/bin/codex"], "launcher": "/usr/bin/codex"}
    data = {"tools": tools, "policy": control.policy_for(tools), "snapshot": {}}
    control.save(tmp_path / "state.json", data)
    monkeypatch.setattr(control, "preflight", lambda: None)
    monkeypatch.setattr(control, "image", lambda **_: control.settings()["image_id"])
    monkeypatch.setattr(
        control,
        "oshell",
        lambda *args, **kwargs: (
            subprocess.CompletedProcess(args, 0, control.NAME, "")
            if args[:2] == ("sandbox", "list")
            else pytest.fail("No mutation allowed")
        ),
    )
    monkeypatch.setattr(
        control,
        "api",
        lambda *_: {
            "name": control.NAME,
            "phase": "Ready",
            "configuration_admission": {"state": "accepted"},
            "labels": {
                "manager.phase": "2",
                "manager.image": control.settings()["image_id"].split(":")[1][:32],
            },
        },
    )
    monkeypatch.setattr(control, "state", lambda **_: data)
    monkeypatch.setattr(control, "effective", lambda _: {})
    monkeypatch.setattr(control, "verify_tools", lambda _: tools)
    monkeypatch.setattr(control, "current", lambda _: ({"root": "/source"}, {}))
    monkeypatch.setattr(control, "guest", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        control, "execute", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "", "")
    )
    monkeypatch.setattr(control, "login_status", lambda _: True)
    data["snapshot"] = {"root": "/source"}
    monkeypatch.setattr(control, "sync_checkout", lambda **_: {"state": "current"})
    monkeypatch.setattr(
        control,
        "execute",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, "--sandbox --ask-for-approval", ""),
    )
    control.setup("apply")


def test_auth_directory_initialization_preserves_existing_data(tmp_path):
    home = tmp_path / ".codex"
    guest.prepare_auth(home)
    assert home.stat().st_mode & 0o777 == 0o700
    (home / "auth.json").write_text("DUMMY_ONLY")
    guest.prepare_auth(home)
    assert (home / "auth.json").read_text() == "DUMMY_ONLY"
    link = tmp_path / "alias"
    link.symlink_to(home, target_is_directory=True)
    with pytest.raises(ValueError):
        guest.prepare_auth(link)


def test_automatic_native_approvals_are_rejected(fake_effective, monkeypatch):
    monkeypatch.setattr(
        control,
        "oshell",
        lambda *args: subprocess.CompletedProcess(
            args, 0, json.dumps({"settings": {"proposal_approval_mode": {"value": "auto"}}}), ""
        ),
    )
    with pytest.raises(ValueError, match="not manual"):
        control.effective()


def test_launcher_uses_requested_permissions_only_inside_guest(monkeypatch):
    data = {"tools": {"launcher": "/codex"}, "policy": {}}
    monkeypatch.setattr(control, "state", lambda **_: data)
    monkeypatch.setattr(control, "effective", lambda _: {})
    monkeypatch.setattr(control, "verify_tools", lambda _: {})
    monkeypatch.setattr(control, "current", lambda _: ({"root": "/source"}, {}))
    monkeypatch.setattr(control, "guest", lambda *args, **kwargs: {})
    monkeypatch.setattr(control, "login_status", lambda _: True)
    monkeypatch.setattr(control.github_provider, "verify", lambda *a: {})
    calls = []
    monkeypatch.setattr(control, "session", lambda action, **kwargs: calls.append((action, kwargs)))
    control.start("codex")
    assert calls[0][0] == "launch" and calls[0][1]["interactive"]
    argv = calls[0][1]["argv"]
    assert "never" in argv and "danger-full-access" in argv
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv


def test_recovery_never_adopts_unknown_policy(monkeypatch, tmp_path):
    monkeypatch.setattr(control, "STATE", tmp_path)
    base = control.policy_for({"binaries": ["/usr/bin/codex"]})
    control.save(
        tmp_path / "pending-change.json",
        {"sandbox_id": "known", "before": {"policy": base}, "expected": base},
    )
    other = copy.deepcopy(base)
    other["filesystem_policy"]["read_write"].append("/host")
    monkeypatch.setattr(control, "effective", lambda: {"policy": other})
    monkeypatch.setattr(control, "confirm", lambda _: pytest.fail("Cannot confirm unknown drift"))
    with pytest.raises(ValueError, match="Unknown policy drift"):
        control.recover({"sandbox_id": "known"})


@pytest.mark.parametrize("composed", [False, True])
def test_recovery_of_loaded_approved_change_is_local_only(monkeypatch, tmp_path, composed):
    monkeypatch.setattr(control, "STATE", tmp_path)
    base = control.policy_for({"binaries": ["/usr/bin/codex"]})
    before = {"version": 1, "hash": "before", "policy": base}
    control.save(
        tmp_path / "pending-change.json",
        {
            "sandbox_id": "known",
            "before": before,
            "expected": base,
            "action": "approve",
            "rule": {"id": "review_test"},
        },
    )
    actual = copy.deepcopy(base)
    if composed:
        actual["network_policies"][control.github_provider.RULE] = (
            control.github_provider.injected_rule(control.github_provider.profile(control))
        )
    monkeypatch.setattr(
        control, "effective", lambda: {"version": 2, "hash": "after", "policy": actual}
    )
    monkeypatch.setattr(control, "confirm", lambda _: None)
    monkeypatch.setattr(control, "oshell", lambda *_: pytest.fail("No native policy mutation"))
    control.recover({"sandbox_id": "known", "policy": {}})
    assert not (tmp_path / "pending-change.json").exists()
    assert control.read(tmp_path / "state.json")["policy"] == base


def test_persist_writes_only_exact_active_approved_rule(monkeypatch, tmp_path):
    cfg = control.settings()
    monkeypatch.setattr(control, "STATE", tmp_path)
    monkeypatch.setattr(control, "CONFIG", tmp_path / "phase2.json")
    control.save(control.CONFIG, cfg)
    proposal = {
        "id": "review_test",
        "host": "example.com",
        "port": 443,
        "access": "full",
        "binaries": ["/usr/bin/curl"],
        "status": "approved",
    }
    control.save(tmp_path / "inbox.json", {"review_test": proposal})
    data = {"policy": {"network_policies": {"review_test": control.network_rule(proposal)}}}
    monkeypatch.setattr(control, "state", lambda **_: data)
    monkeypatch.setattr(control, "effective", lambda _: {})
    monkeypatch.setattr(control, "confirm", lambda _: None)
    monkeypatch.setattr(control, "oshell", lambda *_: pytest.fail("No native mutation"))
    control.rules("persist", "review_test")
    saved = control.read(control.CONFIG)
    assert saved["rules"][:-1] == cfg["rules"]
    assert saved["rules"][-1]["binary"] == "/usr/bin/curl"
    assert saved["rules"][-1]["host"] == "example.com"


def test_fresh_setup_creates_only_named_vm_with_manual_approval(monkeypatch, tmp_path):
    monkeypatch.setattr(control.github_provider, "reconcile", lambda *a, **k: None)
    monkeypatch.setattr(control, "STATE", tmp_path)
    monkeypatch.setattr(control, "preflight", lambda: None)
    pinned = control.settings()["image_id"]
    monkeypatch.setattr(control, "image", lambda **_: pinned)
    tools = {"binaries": ["/usr/bin/codex"], "launcher": "/usr/bin/codex"}
    calls = []

    def native(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "oma-dev-validation\n", "")

    monkeypatch.setattr(control, "oshell", native)
    monkeypatch.setattr(control, "command", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        control,
        "api",
        lambda *_: {
            "id": "new",
            "name": control.NAME,
            "phase": "Ready",
            "configuration_admission": {"state": "accepted"},
            "labels": {"manager.phase": "2", "manager.image": pinned.split(":")[1][:32]},
        },
    )
    monkeypatch.setattr(control, "effective", lambda _: {})
    monkeypatch.setattr(control, "verify_tools", lambda: tools)
    monkeypatch.setattr(
        control,
        "update",
        lambda data, rule, **kwargs: data["policy"]["network_policies"].update(
            {rule["id"]: control.network_rule(rule)}
        ),
    )
    monkeypatch.setattr(
        control, "refresh", lambda data: data.update({"snapshot": {"root": "/source"}})
    )
    monkeypatch.setattr(control, "current", lambda _: ({"root": "/source"}, {}))
    monkeypatch.setattr(control, "guest", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        control, "execute", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "", "")
    )
    monkeypatch.setattr(control, "login_status", lambda _: False)
    monkeypatch.setattr(control, "sync_checkout", lambda **_: {"state": "current"})
    monkeypatch.setattr(
        control,
        "execute",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, "--sandbox --ask-for-approval", ""),
    )
    control.setup("apply")
    create = [args for args in calls if args[:2] == ("sandbox", "create")]
    assert len(create) == 1 and create[0][create[0].index("--name") + 1] == control.NAME
    assert "--no-auto-providers" in create[0]
    assert create[0][create[0].index("--approval-mode") + 1] == "manual"
    assert all("delete" not in args and "--global" not in args for args in calls)


def test_executable_drift_never_expands_policy(monkeypatch):
    monkeypatch.setattr(
        control,
        "guest",
        lambda *_: {
            "uid": 1000,
            "version": control.settings()["codex_version"],
            "binaries": ["/changed/codex"],
            "native_sha256": "new",
        },
    )
    with pytest.raises(ValueError, match="Executable identity drift"):
        control.verify_tools({"tools": {"binaries": ["/pinned/codex"], "native_sha256": "old"}})


def test_corrupted_snapshot_baseline_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(control, "STATE", tmp_path)
    monkeypatch.setattr(control, "manifest", lambda: ["a.py"])
    provenance = {
        "manifest": ["a.py"],
        "files": [{"path": "a.py", "sha256": control.digest(b"ok\n")}],
    }
    control.save(tmp_path / "source/snapshot-provenance.json", provenance)
    (tmp_path / "source/a.py").write_bytes(b"changed\n")
    data = {
        "snapshot": {
            "local": "source",
            "provenance_sha256": control.digest(json.dumps(provenance, sort_keys=True).encode()),
        }
    }
    with pytest.raises(ValueError, match="baseline checksum"):
        control.current(data)


def test_kvm_denial_has_actionable_recovery(monkeypatch):
    monkeypatch.setattr(control.shutil, "which", lambda _: "/dummy")
    monkeypatch.setattr(
        control,
        "oshell",
        lambda *args: subprocess.CompletedProcess(args, 0, "openshell 0.1.2\n", ""),
    )
    monkeypatch.setattr(control.os, "access", lambda *_: False)
    with pytest.raises(ValueError, match="KVM denied"):
        control.preflight()


def test_missing_image_check_never_builds(monkeypatch):
    calls = []

    def cmd(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1, "", "")

    monkeypatch.setattr(control, "command", cmd)
    with pytest.raises(ValueError, match="Pinned image missing"):
        control.image(check=True)
    assert len(calls) == 2 and all(c[1:3] == ["image", "inspect"] for c in calls)


def test_provider_composition_checks_receipt_when_history_has_base_hash(
    fake_effective, monkeypatch
):
    info, payload, history = fake_effective
    base = copy.deepcopy(payload["policy"])
    payload["policy"]["network_policies"][control.github_provider.RULE] = (
        control.github_provider.injected_rule(control.github_provider.profile(control))
    )
    history["revisions"][0]["hash"] = "base-hash"
    calls = []
    monkeypatch.setattr(control.github_provider, "status", lambda *a: calls.append(a))
    assert control.effective(base)["hash"] == "hash"
    assert calls and calls[0][-1] == info["configuration_admission"]["policy_hash"]
    payload["policy"]["network_policies"][control.github_provider.RULE]["endpoints"][0]["host"] = (
        "unexpected.example"
    )
    with pytest.raises(ValueError, match="canonical profile"):
        control.effective(base)


def test_unexpected_provider_is_rejected_even_during_reconciliation(fake_effective):
    _, payload, _ = fake_effective
    payload["policy"]["network_policies"]["_provider_unreviewed"] = {}
    with pytest.raises(ValueError, match="Unexpected provider"):
        control.effective(github_profile=False)


def test_effective_accepts_native_deny_all_without_network_policies(fake_effective):
    """OpenShell 0.1.2 omits empty network_policies in the returned JSON."""
    _, payload, _ = fake_effective
    payload["policy"].pop("network_policies")
    expected = copy.deepcopy(payload["policy"])
    assert control.effective(expected)["status"] == "effective"


@pytest.fixture
def interrupted_project_bootstrap(monkeypatch, tmp_path):
    image_id = "sha256:" + "a" * 64
    profile = {
        "id": "oma",
        "root": str(tmp_path / "oma"),
        "repository": "phuongtuan1803/one-man-agency",
        "sandbox": "oma-dev",
        "workspace": "/sandbox/oma/checkout",
        "manager_revision": "c" * 40,
    }
    monkeypatch.setattr(control, "PROFILE", profile)
    monkeypatch.setattr(control, "NAME", profile["sandbox"])
    monkeypatch.setattr(control, "STATE", tmp_path)
    binding = {
        key: str(profile[key])
        for key in ("id", "root", "repository", "sandbox", "workspace", "manager_revision")
    }
    control.save(tmp_path / "project-binding.json", binding)
    control.save(tmp_path / "bootstrap-policy.json", control.base_policy())
    info = {
        "id": "11d945ec-15a5-460b-b58c-7c32e8468a83",
        "name": "oma-dev",
        "phase": "Ready",
        "configuration_admission": {"state": "accepted"},
        "labels": {
            "manager.phase": "2",
            "manager.project": "oma",
            "manager.image": image_id[7:39],
        },
    }
    return image_id, info


def test_interrupted_bootstrap_receipt_and_identity_verified(
    interrupted_project_bootstrap, monkeypatch
):
    image_id, info = interrupted_project_bootstrap
    checked = []
    monkeypatch.setattr(control, "effective", lambda policy: checked.append(policy))
    control.verify_interrupted_bootstrap(info, image_id)
    assert checked == [control.base_policy()]


@pytest.mark.parametrize(
    "drift",
    ["binding", "bootstrap_policy", "missing_receipt", "sandbox_id", "image", "project"],
)
def test_interrupted_bootstrap_refuses_unverified_resources(
    interrupted_project_bootstrap, monkeypatch, drift
):
    image_id, info = interrupted_project_bootstrap
    if drift == "binding":
        binding = control.read(control.STATE / "project-binding.json")
        binding["repository"] = "other/repo"
        control.save(control.STATE / "project-binding.json", binding)
    elif drift == "bootstrap_policy":
        policy = control.base_policy()
        policy["filesystem_policy"]["read_write"].append("/host")
        control.save(control.STATE / "bootstrap-policy.json", policy)
    elif drift == "missing_receipt":
        (control.STATE / "bootstrap-policy.json").unlink()
    elif drift == "sandbox_id":
        info["id"] = "not-a-native-sandbox-id"
    elif drift == "image":
        info["labels"]["manager.image"] = "bad"
    elif drift == "project":
        info["labels"]["manager.project"] = "foreign"

    monkeypatch.setattr(
        control, "effective", lambda *_: pytest.fail("Unverified policy must not be used")
    )
    with pytest.raises(ValueError, match="refusing|unavailable"):
        control.verify_interrupted_bootstrap(info, image_id)
    assert not (control.STATE / "state.json").exists()


def test_interrupted_bootstrap_rejects_policy_drift_without_writing_state(
    interrupted_project_bootstrap, monkeypatch
):
    image_id, info = interrupted_project_bootstrap
    def deny_policy(_):
        raise ValueError("Effective policy drift")
    monkeypatch.setattr(control, "effective", deny_policy)
    with pytest.raises(ValueError, match="policy drift"):
        control.verify_interrupted_bootstrap(info, image_id)
    assert not (control.STATE / "state.json").exists()


def test_setup_resumes_verified_bootstrap_without_creating_sandbox(
    interrupted_project_bootstrap, monkeypatch
):
    image_id, info = interrupted_project_bootstrap
    monkeypatch.setattr(control, "preflight", lambda: None)
    monkeypatch.setattr(control, "image", lambda **_: image_id)
    monkeypatch.setattr(control, "config_hash", lambda: "reviewed-config")
    monkeypatch.setattr(control, "policy_for", lambda tools: {"network_policies": {}})
    monkeypatch.setattr(control, "verify_tools", lambda data=None: {
        "binaries": ["/usr/local/bin/codex"],
        "launcher": "/usr/local/bin/codex",
    })
    monkeypatch.setattr(control.github_provider, "reconcile", lambda *a, **k: None)
    monkeypatch.setattr(control, "guest", lambda *a, **k: {})
    monkeypatch.setattr(control, "login_status", lambda _: True)
    monkeypatch.setattr(control, "sync_checkout", lambda **_: {"state": "current"})
    monkeypatch.setattr(control, "api", lambda *a: info)
    checked = []
    monkeypatch.setattr(control, "effective", lambda policy: checked.append(policy))
    native_calls = []

    def native(*args, **kwargs):
        native_calls.append(args)
        if args[:2] == ("sandbox", "list"):
            return subprocess.CompletedProcess(args, 0, "NAME CREATED PHASE\noma-dev today Ready\n", "")
        pytest.fail("Resume must not mutate native sandbox state")
    monkeypatch.setattr(control, "oshell", native)

    def execute(*args, **kwargs):
        source = " ".join(map(str, args))
        stdout = "0\n" if "unmanaged_agent()" in source else "--sandbox --ask-for-approval\n"
        return subprocess.CompletedProcess(args, 0, stdout, "")
    monkeypatch.setattr(control, "execute", execute)

    control.setup("apply")
    assert control.read(control.STATE / "state.json")["sandbox_id"] == info["id"]
    assert checked == [control.base_policy(), control.base_policy()]
    assert all(args[:2] != ("sandbox", "create") for args in native_calls)


def test_provider_reconcile_treats_missing_native_network_rules_as_deny_all(
    monkeypatch, tmp_path
):
    """A fresh OpenShell VM omits network_policies until first provider attach."""
    provider = control.github_provider
    monkeypatch.setattr(control, "STATE", tmp_path)
    monkeypatch.setattr(control, "PROVIDER_PROFILE", tmp_path / "github.yaml")
    control.PROVIDER_PROFILE.write_text("id: reviewed-only\n")
    monkeypatch.setattr(provider, "lint", lambda *a: None)
    monkeypatch.setattr(provider, "profile", lambda *_: {"id": provider.PROVIDER})
    monkeypatch.setattr(provider, "comparable", lambda _: {})
    monkeypatch.setattr(provider, "tool", lambda *a, **kw: None)
    monkeypatch.setattr(provider, "verify", lambda *a: {"ready": True})
    monkeypatch.setattr(control, "effective", lambda *a, **kw: None)
    native = []

    def api(*parts):
        if parts[:3] == ("provider", "profile", "list"):
            return [{"id": provider.PROVIDER}]
        if parts[:3] == ("provider", "profile", "describe"):
            return {"id": provider.PROVIDER}
        if parts[:2] == ("provider", "list"):
            return {"providers": [{"name": provider.PROVIDER,
                                   "type": provider.PROVIDER,
                                   "credential_keys": ["GITHUB_TOKEN"]}]}
        if parts[:3] == ("sandbox", "provider", "list"):
            return {"providers": []}
        if parts[:2] == ("policy", "get"):
            return {"policy": {"version": 1, "filesystem_policy": {},
                               "landlock": {}}}
        pytest.fail(f"Unexpected native API call: {parts[:2]}")

    monkeypatch.setattr(control, "api", api)
    monkeypatch.setattr(
        control, "oshell",
        lambda *args, **kwargs: native.append(args),
    )
    result = provider.reconcile(control, {"policy": control.base_policy()}, mode="apply")
    assert result["ready"] is True
    assert any(args[:3] == ("sandbox", "provider", "attach") for args in native)
    assert not any("delete" in args for args in native)


def test_provider_verify_missing_network_rules_fails_closed(monkeypatch):
    provider = control.github_provider
    monkeypatch.setattr(
        control, "oshell",
        lambda *args: subprocess.CompletedProcess(args, 0, "openshell 0.1.2\n", ""),
    )
    monkeypatch.setattr(provider, "tool", lambda *a, **kw: None)
    monkeypatch.setattr(control, "effective", lambda policy: {"policy": {"version": 1}})
    with pytest.raises(ValueError, match="GitHub provider not attached"):
        provider.verify(control, {"policy": control.base_policy()})


@pytest.fixture
def initialized_revision_migration(monkeypatch, tmp_path):
    from openshell_manager import configuration, provenance

    before, after = "a" * 40, "b" * 40
    project = tmp_path / "project.json"
    scope = tmp_path / "source-manifest.txt"
    edit = tmp_path / "edit-scope.txt"
    scope.write_text("AGENTS.md\n")
    edit.write_text("task.py\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    cfg = {
        "id": "oma",
        "root": str(tmp_path / "oma"),
        "repository": "phuongtuan1803/one-man-agency",
        "sandbox": "oma-dev",
        "workspace": "/sandbox/oma/checkout",
        "manager_revision": before,
        "image_id": "sha256:" + "a" * 64,
        "source_manifest": scope,
        "edit_scope": edit,
        "state": state_dir,
        "codex_version": "0.162.1",
        "cli_version": "openshell 0.1.2",
        "agents": {},
        "rules": [],
        "github": {"repository": "phuongtuan1803/one-man-agency", "provider": "oma-github"},
    }
    control.save(project, {"id": "oma", "manager_revision": before})
    keys = ("id", "root", "repository", "sandbox", "workspace", "manager_revision")
    control.save(state_dir / "project-binding.json", {key: str(cfg[key]) for key in keys})
    control.save(state_dir / "bootstrap-policy.json", control.base_policy())
    old_hash = control.digest(
        json.dumps(cfg, sort_keys=True, default=str).encode()
        + scope.read_bytes() + edit.read_bytes()
    )
    control.save(state_dir / "state.json", {
        "sandbox_id": "11d945ec-15a5-460b-b58c-7c32e8468a83",
        "config_hash": old_hash,
        "policy": control.base_policy(),
        "snapshot": None,
        "custom_receipt": {"preserve": True},
    })
    monkeypatch.setattr(configuration, "load", lambda path, host: cfg)
    monkeypatch.setattr(provenance, "identity", lambda: (after, "installed Git"))
    monkeypatch.setattr(provenance, "verify", lambda pin: pin)
    api_calls = []
    def read_native(*args):
        api_calls.append(args)
        return {
            "id": "11d945ec-15a5-460b-b58c-7c32e8468a83",
            "name": "oma-dev",
            "phase": "Ready",
            "configuration_admission": {"state": "accepted"},
            "labels": {
                "manager.phase": "2", "manager.project": "oma",
                "manager.image": cfg["image_id"][7:39],
            },
        }
    monkeypatch.setattr(control, "api", read_native)
    return project, state_dir, cfg, before, after, api_calls


def test_migrate_initialized_state_preserves_sandbox_policy_snapshot(
    initialized_revision_migration
):
    project, state_dir, cfg, before, after, native_calls = initialized_revision_migration
    control.migrate_revision(project, None, before)
    updated = control.read(state_dir / "state.json")
    assert updated["custom_receipt"] == {"preserve": True}
    assert updated["policy"] == control.base_policy()
    assert updated["snapshot"] is None
    assert updated["config_hash"] != control.digest(b"garbage")
    assert control.read(project)["manager_revision"] == after
    assert control.read(state_dir / "project-binding.json")["manager_revision"] == after
    assert native_calls == [("sandbox", "get", "oma-dev")]
    # A retry after a complete migration must not rewrite or reset existing state.
    control.migrate_revision(project, None, before)
    assert control.read(state_dir / "state.json") == updated
    assert native_calls == [("sandbox", "get", "oma-dev")] * 2


def test_migrate_initialized_state_rejects_config_drift(
    initialized_revision_migration
):
    project, state_dir, _, before, _, _ = initialized_revision_migration
    data = control.read(state_dir / "state.json")
    data["config_hash"] = "unreviewed"
    control.save(state_dir / "state.json", data)
    with pytest.raises(ValueError, match="configuration/identity drift"):
        control.migrate_revision(project, None, before)
    assert control.read(project)["manager_revision"] == before


def test_migrate_initialized_state_refuses_sandbox_identity_drift(
    initialized_revision_migration, monkeypatch
):
    project, state_dir, _, before, _, _ = initialized_revision_migration
    monkeypatch.setattr(control, "api", lambda *_: {
        "id": "foreign", "name": "oma-dev", "phase": "Ready",
        "configuration_admission": {"state": "accepted"},
        "labels": {"manager.phase": "2", "manager.project": "oma", "manager.image": "bad"},
    })
    with pytest.raises(ValueError, match="ownership differs"):
        control.migrate_revision(project, None, before)
    assert control.read(project)["manager_revision"] == before


def test_github_stage_errors_are_actionable_not_payloads(monkeypatch):
    monkeypatch.setattr(control.github_provider, "reconcile", lambda *a, **kw: {}["missing"])
    with pytest.raises(ValueError, match="GitHub Provider response schema mismatch"):
        control.reconcile_github({"policy": control.base_policy()}, "apply")
