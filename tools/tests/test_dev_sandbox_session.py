"""Offline behavior tests with real Git histories; no external credentials or models."""

import base64
import importlib.util
import json
import subprocess
from contextlib import contextmanager, nullcontext
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[2] / "infra/dev-sandbox"
spec = importlib.util.spec_from_file_location("oma_session", HERE / "session.py")
session = importlib.util.module_from_spec(spec)
spec.loader.exec_module(session)


def local_git(root, *args):
    return subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=True)


@pytest.fixture
def repository(tmp_path):
    bare, upstream, root = (tmp_path / name for name in ("origin.git", "upstream", "oma"))
    local_git(tmp_path, "init", "--bare", str(bare))
    local_git(tmp_path, "init", "-b", "main", str(upstream))
    local_git(upstream, "config", "user.name", "Offline test")
    local_git(upstream, "config", "user.email", "test@example.invalid")
    (upstream / "file.txt").write_text("initial\n")
    local_git(upstream, "add", ".")
    local_git(upstream, "commit", "-m", "initial")
    local_git(upstream, "remote", "add", "origin", str(bare))
    local_git(upstream, "push", "origin", "main")
    calls = []

    def run(args, root=root, check=True):
        calls.append(list(args))
        actual = list(args)
        if actual[0] == "clone":
            actual[actual.index(session.REMOTE)] = str(bare)
        elif actual[0] in {"fetch", "ls-remote"}:
            actual[actual.index("origin")] = str(bare)
        result = subprocess.run(
            ["git", *actual], cwd=root, text=True, capture_output=True, check=check
        )
        if actual[0] == "clone" and result.returncode == 0:
            local_git(Path(actual[-1]), "remote", "set-url", "origin", session.REMOTE)
        return result

    def sync(check=False, active=False):
        return session.synchronize(root=root, run=run, check=check, active=active)

    return root, upstream, bare, calls, sync


def test_first_clone_and_repeat_reuses_checkout(repository):
    root, _, _, calls, sync = repository
    assert sync()["state"] == "cloned"
    head = local_git(root, "rev-parse", "HEAD").stdout
    assert sync()["state"] == "current"
    assert local_git(root, "rev-parse", "HEAD").stdout == head
    assert sum(c[0] == "clone" for c in calls) == 1
    assert local_git(root, "remote", "get-url", "origin").stdout.strip() == session.REMOTE


def test_fast_forward(repository):
    root, upstream, _, _, sync = repository
    sync()
    (upstream / "file.txt").write_text("remote update\n")
    local_git(upstream, "commit", "-am", "remote")
    local_git(upstream, "push", "origin", "main")
    assert sync()["state"] == "fast-forwarded"
    assert (root / "file.txt").read_text() == "remote update\n"


@pytest.mark.parametrize("untracked", [False, True])
def test_dirty_work_never_fetches_or_pulls(repository, untracked):
    root, _, _, calls, sync = repository
    sync()
    path = root / ("untracked.txt" if untracked else "file.txt")
    path.write_text("precious work\n")
    calls.clear()
    assert sync()["state"] == "dirty"
    assert path.read_text() == "precious work\n"
    assert not any(c[0] in {"fetch", "merge", "pull", "reset", "clean"} for c in calls)


def test_diverged_history_preserved(repository):
    root, upstream, _, calls, sync = repository
    sync()
    (root / "local.txt").write_text("local\n")
    local_git(root, "add", ".")
    local_git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "local",
    )
    head = local_git(root, "rev-parse", "HEAD").stdout
    (upstream / "remote.txt").write_text("remote\n")
    local_git(upstream, "add", ".")
    local_git(upstream, "commit", "-m", "remote")
    local_git(upstream, "push", "origin", "main")
    calls.clear()
    assert sync()["state"] == "diverged"
    assert local_git(root, "rev-parse", "HEAD").stdout == head
    assert not any(c[0] in {"merge", "reset", "clean"} for c in calls)


def test_feature_branch_preserved(repository):
    root, _, _, calls, sync = repository
    sync()
    local_git(root, "checkout", "-b", "feature/existing-work")
    calls.clear()
    assert sync()["state"] == "feature-or-detached"
    assert not any(c[0] in {"fetch", "checkout", "merge"} for c in calls)


@pytest.mark.parametrize("remote", sorted(session.SSH_REMOTES))
def test_ssh_migration_only_changes_oma_origin(repository, remote):
    root, _, _, _, sync = repository
    sync()
    local_git(root, "remote", "set-url", "origin", remote)
    local_git(root, "config", "remote.origin.pushurl", remote)
    unrelated = "git@github.com:example/another-repo.git"
    local_git(root, "remote", "add", "unrelated", unrelated)
    assert sync()["state"] == "current"
    assert local_git(root, "remote", "get-url", "origin").stdout.strip() == session.REMOTE
    assert local_git(root, "remote", "get-url", "--push", "origin").stdout.strip() == session.REMOTE
    assert local_git(root, "remote", "get-url", "unrelated").stdout.strip() == unrelated


@pytest.mark.parametrize("push", [False, True])
def test_unsupported_identity_preserved(repository, push):
    root, _, _, calls, sync = repository
    sync()
    key = "remote.origin.pushurl" if push else "remote.origin.url"
    local_git(root, "config", key, "https://github.com/example/other.git")
    config = (root / ".git/config").read_bytes()
    calls.clear()
    with pytest.raises(ValueError, match="Unsupported Project"):
        sync()
    assert (root / ".git/config").read_bytes() == config
    assert not any(c[0] in {"fetch", "merge", "remote", "ls-remote"} for c in calls)


def test_check_does_not_clone_fetch_or_migrate(repository):
    root, _, _, calls, sync = repository
    with pytest.raises(ValueError, match="checkout missing"):
        sync(check=True)
    assert not root.exists() and not calls
    sync()
    config = (root / ".git/config").read_bytes()
    calls.clear()
    assert sync(check=True)["state"] == "clean"
    assert (root / ".git/config").read_bytes() == config
    assert not any(c[0] in {"fetch", "merge", "clone", "remote"} for c in calls)


def test_active_task_blocks_all_git_operations(repository):
    root, _, _, calls, sync = repository
    assert sync(active=True)["state"] == "active-agent"
    assert not calls and not root.exists()


def test_checkout_lock_blocks_sync_and_releases_after_failure(tmp_path):
    path = tmp_path / "source.lock"
    with session.lease(path, shared=True):
        with pytest.raises(ValueError, match="Concurrent active agent"):
            with session.lease(path):
                pytest.fail("Cannot acquire exclusive lock during task")
    with pytest.raises(RuntimeError), session.lease(path):
        raise RuntimeError("interrupted")
    with session.lease(path):
        pass


def test_interrupted_clone_retry_preserves_existing_source(tmp_path):
    root = tmp_path / "oma"

    def failed(args, root, **kwargs):
        partial = Path(args[-1])
        partial.mkdir()
        (partial / "partial").write_text("incomplete")
        raise ValueError("interrupted clone")

    with pytest.raises(ValueError, match="interrupted clone"):
        session.synchronize(root=root, run=failed, active=False)
    assert not root.exists()
    assert not list(tmp_path.glob(".manager-clone-*"))
    root.mkdir()
    (root / "precious.txt").write_text("keep")
    with pytest.raises(ValueError, match="standalone Git checkout"):
        session.synchronize(root=root, run=failed, active=False)
    assert (root / "precious.txt").read_text() == "keep"


def test_missing_credential_is_actionable_without_logging(capsys):
    with pytest.raises(ValueError, match="github.sh credential"):
        session.environment({"PATH": "/usr/bin"})
    assert capsys.readouterr().out == ""


def test_git_auth_configuration_is_memory_only_and_redacted(monkeypatch, tmp_path):
    secret = "DUMMY_PROVIDER_PLACEHOLDER_NEVER_LOG"
    env = session.environment({"GITHUB_TOKEN": secret, "GIT_TRACE": "1", "PATH": "/usr/bin"})
    encoded = base64.b64encode(("x-access-token:" + secret).encode()).decode()
    assert env["GIT_CONFIG_KEY_1"] == "credential." + session.REMOTE + ".helper"
    assert secret not in "".join(v for k, v in env.items() if k.startswith("GIT_CONFIG_"))
    assert "GIT_TRACE" not in env and env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_CONFIG_VALUE_2"] == "false" and env["GIT_CONFIG_VALUE_3"] == "true"

    def run(argv, **kwargs):
        assert secret not in repr(argv) and encoded not in repr(argv)
        return subprocess.CompletedProcess(argv, 0, secret + encoded, "")

    monkeypatch.setattr(session.subprocess, "run", run)
    result = session.git(["ls-remote", session.REMOTE], root=tmp_path, env=env)
    assert secret not in result.stdout and encoded not in result.stdout
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("operation", ["fill", "approve", "reject"])
def test_real_git_stateless_helper_never_stores_credential(tmp_path, operation):
    secret = "DUMMY_PROVIDER_PLACEHOLDER_NEVER_LOG"
    env = session.environment({"GITHUB_TOKEN": secret, "PATH": "/usr/bin"})
    request = "protocol=https\nhost=github.com\npath=example/project.git\n\n"
    result = subprocess.run(
        ["git", "credential", operation],
        input=request,
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    if operation == "fill":
        assert "username=x-access-token" in result.stdout and "password=" + secret in result.stdout
    else:
        assert result.stdout == ""
    assert not list(tmp_path.iterdir())
    config = subprocess.run(
        ["git", "config", "--list"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert secret not in config.stdout + config.stderr


@pytest.mark.parametrize(
    "host,path",
    [
        ("github.com", "example/other.git"),
        ("evil.example", "example/project.git"),
    ],
)
def test_helper_never_supplies_provider_credential_to_other_remotes(tmp_path, host, path):
    env = session.environment(
        {"GH_TOKEN": "DUMMY_PROVIDER_PLACEHOLDER_NEVER_LOG", "PATH": "/usr/bin"}
    )
    result = subprocess.run(
        ["git", "credential", "fill"],
        cwd=tmp_path,
        env=env,
        input=f"protocol=https\nhost={host}\npath={path}\n\n",
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "DUMMY_PROVIDER_PLACEHOLDER_NEVER_LOG" not in result.stdout + result.stderr


def test_invalid_credential_never_echoes_remote_error(monkeypatch, tmp_path, capsys):
    secret = "DUMMY_PROVIDER_PLACEHOLDER_NEVER_LOG"
    env = session.environment({"GH_TOKEN": secret})
    monkeypatch.setattr(
        session.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 128, secret, "HTTP 401 " + secret),
    )
    with pytest.raises(ValueError, match="expired or lacks repository permission") as error:
        session.git(["fetch", "origin"], root=tmp_path, env=env)
    assert secret not in str(error.value) + capsys.readouterr().out
    failed = session.git(["fetch", "origin"], root=tmp_path, env=env, check=False)
    assert secret not in failed.stdout + failed.stderr


def test_launch_directory_auth_and_lock_lifetime(monkeypatch, tmp_path):
    payload = {
        "action": "launch",
        "argv": ["codex", "--sandbox", "danger-full-access", "--ask-for-approval", "never"],
    }
    monkeypatch.setattr(
        session.sys, "argv", ["session.py", base64.b64encode(json.dumps(payload).encode()).decode()]
    )
    monkeypatch.setenv("GITHUB_TOKEN", "DUMMY_PROVIDER_PLACEHOLDER_NEVER_LOG")
    monkeypatch.setattr(session, "ROOT", tmp_path)
    held = []

    @contextmanager
    def lease():
        held.append(True)
        try:
            yield
        finally:
            held.pop()

    monkeypatch.setattr(session, "lease", lease)
    monkeypatch.setattr(session, "synchronize", lambda **kwargs: {"state": "dirty"})

    def call(argv, cwd, env):
        assert held and cwd == tmp_path
        assert argv == payload["argv"] and env["GIT_CONFIG_COUNT"] == "5"
        return 0

    monkeypatch.setattr(session.subprocess, "call", call)
    with pytest.raises(SystemExit) as exit_status:
        session.main()
    assert exit_status.value.code == 0 and not held


def test_active_session_cannot_launch(monkeypatch):
    payload = base64.b64encode(json.dumps({"action": "launch", "argv": ["codex"]}).encode())
    monkeypatch.setattr(session.sys, "argv", ["session.py", payload.decode()])
    monkeypatch.setenv("GH_TOKEN", "DUMMY_PROVIDER_PLACEHOLDER_NEVER_LOG")
    monkeypatch.setattr(session, "lease", nullcontext)
    monkeypatch.setattr(session, "synchronize", lambda **kwargs: {"state": "active-agent"})
    monkeypatch.setattr(
        session.subprocess, "call", lambda *a, **k: pytest.fail("Active work preserved")
    )
    with pytest.raises(ValueError, match="Concurrent active agent"):
        session.main()


@pytest.mark.parametrize('check', [False, True])
def test_dirty_ssh_origin_never_migrated(repository, check):
    root, _, _, calls, sync = repository
    sync()
    local_git(root, 'remote', 'set-url', 'origin', next(iter(session.SSH_REMOTES)))
    (root / 'dirty.py').write_text('pass\n')
    before = (root / '.git/config').read_bytes()
    calls.clear()
    assert sync(check=check)['state'] == 'dirty'
    assert (root / '.git/config').read_bytes() == before
    assert not any(c[0] in {'remote', 'fetch', 'merge', 'ls-remote'} for c in calls)


def test_source_lease_uses_runtime_project_lock(monkeypatch, tmp_path):
    path = tmp_path / 'project-source.lock'
    monkeypatch.setattr(session, 'LOCK', path)
    with session.lease(shared=True):
        assert path.is_file()
        with pytest.raises(ValueError, match='Concurrent active agent'):
            with session.lease():
                pytest.fail('Runtime source lock must exclude sync')
