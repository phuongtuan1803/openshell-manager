"""Three adapters and shared workflow with inert commands; only Codex live is permitted."""

import base64
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parents[1] / "src/openshell_manager"
sys.path.insert(0, str(HERE))
workflow = importlib.import_module("workflow")
agents = importlib.import_module("agents")

spec = importlib.util.spec_from_file_location("multi_control", HERE / "control.py")
control = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = control
spec.loader.exec_module(control)
sys.path.pop(0)


@pytest.mark.parametrize("name", agents.AGENTS)
def test_pinned_cli_commands(name):
    adapter = agents.adapter(name)
    assert adapter.install()[-1] == f"{adapter.package}@{adapter.version}"
    interactive = adapter.interactive(model="anthropic/model" if name == "opencode" else None)
    autonomous = adapter.autonomous(
        "fix $(echo inert)", model="anthropic/model" if name == "opencode" else None
    )
    assert interactive[0] == autonomous[0] == adapter.executable
    assert autonomous[-2:] == ["--", "fix $(echo inert)"]
    assert not any("bypass" in arg for arg in autonomous + interactive)
    if name == "codex":
        assert "--ephemeral" in autonomous and "never" in interactive
    elif name == "claude":
        assert "--print" in autonomous and "stream-json" in autonomous
    else:
        assert "run" in autonomous and "--model" in autonomous


@pytest.mark.parametrize("model", ["-evil", "x;echo", "$(curl bad)", "https://user@host", ""])
def test_model_cannot_inject_options(model):
    with pytest.raises(ValueError):
        agents.adapter("opencode").model_args(model)


def test_explicit_opencode_provider_required():
    with pytest.raises(ValueError, match="provider/model"):
        agents.adapter("opencode").autonomous("task")
    with pytest.raises(ValueError, match="provider/model"):
        agents.adapter("opencode").interactive(model="model")


def test_selected_opencode_provider_does_not_inherit_another_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(control, "STATE", tmp_path)
    workflow.prepare(control, "opencode", model="openai/custom-model")
    plan = control.read(tmp_path / "prepared/opencode.json")
    assert plan["provider"] == "openai"
    assert plan["opencode_configuration"] == {"model": "openai/custom-model"}
    assert "api.anthropic.com" not in json.dumps(plan["selected_network_template"])
    with pytest.raises(ValueError, match="reviewed endpoint template"):
        workflow.prepare(control, "opencode", model="unknown/model")


@pytest.mark.parametrize(
    "mode,key", [("api", "ANTHROPIC_API_KEY"), ("subscription", "CLAUDE_CODE_OAUTH_TOKEN")]
)
def test_claude_auth_configuration_is_inert(mode, key):
    config = agents.adapter("claude").auth_configuration(mode)
    assert config["credential_env"] == key and config["enabled"] is False


@pytest.mark.parametrize("name", ["claude", "opencode"])
def test_preparation_and_launch_never_touch_gateway(name, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(control, "STATE", tmp_path)
    monkeypatch.setattr(control, "oshell", lambda *a, **k: pytest.fail("No gateway calls"))
    workflow.prepare(control, name)
    plan = control.read(tmp_path / "prepared" / f"{name}.json")
    assert plan["model_calls"] == 0 and not plan["network_applied"]
    assert not plan["cli_installed"]
    with pytest.raises(ValueError, match="Founder approval"):
        control.start(name)
    with pytest.raises(ValueError, match="Founder approval"):
        workflow.run(control, name, "task", branch="feat/openshell-test")
    assert agents.UNVERIFIED in capsys.readouterr().out


@pytest.mark.parametrize(
    "name,event",
    [
        (
            "codex",
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 3, "output_tokens": 2, "secret": "SENSITIVE"},
            },
        ),
        (
            "claude",
            {
                "type": "result",
                "usage": {"input_tokens": 4, "output_tokens": 2},
                "total_cost_usd": 0.01,
                "result": "SENSITIVE",
            },
        ),
        (
            "opencode",
            {
                "type": "step_finish",
                "part": {"tokens": {"input": 3, "output": 2}, "cost": 0.02, "text": "SENSITIVE"},
            },
        ),
    ],
)
def test_accounting_whitelists_numeric_usage(name, event):
    output = 'SENSITIVE\n{"type":"tool","text":"SENSITIVE"}\n' + json.dumps(event)
    records = agents.usage(name, output)
    assert records and "SENSITIVE" not in json.dumps(records)
    if name == "codex":
        assert records[0]["cost_usd"] is None


def test_malformed_usage_is_not_evidence():
    assert agents.usage("codex", '{"type":"turn.completed","usage":"SECRET"}') == []


@pytest.mark.parametrize(
    "branch", ["main", "master", "refs/heads/main", "feat/x", "feat/openshell-x;curl", "--force"]
)
def test_branch_guard(branch):
    with pytest.raises(ValueError, match="main prohibited"):
        workflow.branch_name(branch)


def test_full_and_multi_module_scope_without_reading_documents(monkeypatch, tmp_path):
    for name in ["m01-a", "m02-b"]:
        module = tmp_path / "modules" / name
        module.mkdir(parents=True)
        (module / "pyproject.toml").write_text("")
    fake = SimpleNamespace(REPO=tmp_path, manifest=lambda: ["pilot.py"])
    paths = [
        "modules/m01-a/src/a.py",
        "modules/m02-b/src/b.py",
        "infra/secrets/key.txt",
        "docs/html/report.html",
        "docs/src/report.json",
        ".codex/auth.json",
        "tools/a.py",
    ]
    monkeypatch.setattr(workflow.snapshot, "git", lambda *a: "\0".join(paths).encode())
    assert workflow.scope_paths(fake, "module", ["modules/m01-a", "modules/m02-b"]) == paths[:2]
    assert workflow.scope_paths(fake, "full") == paths[:2] + ["tools/a.py"]
    with pytest.raises(ValueError):
        workflow.scope_paths(fake, "module", ["../escape"])


def test_publication_requires_native_readiness_before_guest_writes(monkeypatch):
    fake = SimpleNamespace(settings=lambda: control.settings())

    def unavailable(*args):
        raise ValueError("Native provider not ready")

    monkeypatch.setattr(workflow.github_provider, "verify", unavailable)
    with pytest.raises(ValueError, match="not ready"):
        workflow.publish(fake, {}, "feat/openshell-test", Path("unused"))


@pytest.mark.parametrize("name", agents.AGENTS)
@pytest.mark.parametrize("state_changed", [False, True])
def test_shared_mock_execution_tests_retry_commit_accounting(
    name, state_changed, monkeypatch, tmp_path
):
    # Only the offline test replaces the live gate. Every execution below is an inert mock.
    monkeypatch.setattr(agents, "require_live", agents.adapter)
    data = {
        "sandbox_id": "mock",
        "policy": {},
        "tools": {"launcher": agents.adapter(name).executable},
    }
    source = tmp_path / "source"
    source.mkdir()
    (source / "task.py").write_text("old\n")
    calls = []
    moved = [False]
    checks = iter([1, 0])

    def execute(*args, **kwargs):
        calls.append(args)
        if args[0] == agents.adapter(name).executable:
            moved[0] = state_changed
        if "pytest" in args:
            return subprocess.CompletedProcess(args, next(checks), "SECRET", "SECRET")
        return subprocess.CompletedProcess(
            args,
            0,
            '{"type":"turn.completed","usage":{"input_tokens":10}}'
            if "--json" in args
            else "a" * 40,
            "SECRET",
        )

    def refresh(value, names):
        value["snapshot"] = {"local": "source", "root": "/sandbox/mock"}

    saved = []
    fake = SimpleNamespace(
        STATE=tmp_path,
        manifest=lambda: ["task.py"],
        state=lambda: (
            dict(data, snapshot={**data["snapshot"], "root": "/sandbox/replaced"})
            if moved[0]
            else data
        ),
        effective=lambda _: {"version": 4, "hash": "mock"},
        verify_tools=lambda _: {},
        login_status=lambda _: True,
        refresh=refresh,
        save=lambda p, v: saved.append(json.dumps(v)),
        current=lambda _: (data["snapshot"], {"commit": "base"}),
        execute=execute,
        session=lambda action, argv, root, **kwargs: execute(*argv, workdir=root, **kwargs),
        export_patch=lambda _: tmp_path / "safe.patch",
        digest=control.digest,
        guest=lambda *a, **k: {"files": {"task.py": base64.b64encode(b"new\n").decode()}},
    )
    if state_changed:
        with pytest.raises(ValueError, match="snapshot changed"):
            workflow.run(
                fake,
                name,
                "fix task",
                model="provider/model" if name == "opencode" else None,
                attempts=2,
            )
        assert not any("commit" in cmd for cmd in calls)
        assert json.loads(saved[-1])["status"] == "FAIL"
        return
    result = workflow.run(
        fake,
        name,
        "fix task",
        model="provider/model" if name == "opencode" else None,
        attempts=2,
        release_lock=lambda: calls.append(("unlock",)),
    )
    assert result["status"] == "PASS" and len(result["attempts"]) == 2
    assert result["local_commit"] == "a" * 40
    assert any("commit" in cmd for cmd in calls)
    assert calls[0] == ("unlock",)
    assert "SECRET" not in "".join(saved)


@pytest.mark.parametrize("existing", [False, True])
def test_shared_mock_publication_preserves_repository_and_reuses_pr(existing, monkeypatch):
    monkeypatch.setattr(workflow, "publication_preflight", lambda *a: None)
    calls = []

    def execute(*args, **kwargs):
        calls.append(args)
        output = ""
        if "diff" in args:
            output = "task.py\n"
        if "rev-parse" in args:
            output = "a" * 40
        if args[:2] == (workflow.github_provider.GH, "api") and "GET" in args:
            output = json.dumps(
                [{"number": 777}] if existing or any("POST" in a for a in calls) else []
            )
        if args[:2] == (workflow.github_provider.GH, "api") and "POST" in args:
            output = json.dumps({"number": 777})
        return subprocess.CompletedProcess(args, 0, output, "")

    fake = SimpleNamespace(
        execute=execute,
        oshell=lambda *a: calls.append(a),
        NAME="mock",
        manifest=lambda: ["task.py"],
    )
    monkeypatch.setattr(workflow.github_provider, "git", lambda c, *a, **k: execute(*a, **k))
    monkeypatch.setattr(
        workflow.github_provider,
        "api",
        lambda c, *a, **k: execute(workflow.github_provider.GH, "api", *a, **k),
    )
    report = workflow.publish(
        fake,
        {"writable": ["task.py"]},
        "feat/openshell-acceptance",
        Path("changes.patch"),
        base="feat/openshell-phase2",
    )
    assert report["pr"] == 777
    assert report["base"] == "feat/openshell-phase2"
    assert any("clone" in call and "feat/openshell-phase2" in call for call in calls)
    if not existing:
        assert any("base=feat/openshell-phase2" in call for call in calls)
    clone = next(i for i, call in enumerate(calls) if "clone" in call)
    apply = next(i for i, call in enumerate(calls) if "apply" in call)
    assert clone < apply
    push = next(call for call in calls if "push" in call and "--dry-run" not in call)
    assert push[-1] == "HEAD:refs/heads/feat/openshell-acceptance"
    assert not any("merge" in call for call in calls)
    assert sum("POST" in call for call in calls) == (0 if existing else 1)


def test_founder_acceptance_removes_branch_protection_prerequisite(monkeypatch):
    fake = SimpleNamespace(settings=lambda: control.settings())
    monkeypatch.setattr(workflow.github_provider, "verify", lambda *a: {"ready": True})
    assert workflow.publication_preflight(fake, {}) == {"ready": True}


def test_separate_templates_never_approve_other_agents():
    for name in ["claude", "opencode"]:
        template = json.loads((HERE.parents[1] / "config/policies" / f"{name}.json").read_text())
        assert template["default_deny"] and template["manual_approval"]
        assert template["binaries"] == ["/usr/local/bin/" + name]
        assert "OFFLINE" in template["status"]
        assert "chatgpt.com" not in json.dumps(template)
    profile = (HERE.parents[1] / "config/policies/github.yaml").read_text()
    assert "git-receive-pack" in profile and "method: PATCH" in profile
    assert "method: PUT" not in profile
    assert "graphql" not in profile


@pytest.mark.parametrize("name", ["claude", "opencode"])
@pytest.mark.parametrize(
    "criterion",
    [
        "authentication",
        "model_call",
        "coding",
        "tests",
        "commit",
        "push",
        "pr",
        "credential_isolation",
    ],
)
def test_live_acceptance_explicitly_skipped(name, criterion):
    pytest.skip(f"{name} {criterion}: {agents.UNVERIFIED}")
