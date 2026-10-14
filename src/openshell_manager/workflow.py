"""Shared scope, offline preparation, execution accounting and GitHub workflow."""

import base64
import fcntl
import json
import re
import time
import uuid

from . import github_provider
from . import snapshot

from . import agents

REPOSITORY = "example/project"
PROVIDER = "example-github"


def scope_paths(control, scope="pilot", modules=()):
    cfg = getattr(control, 'PROFILE', None)
    if cfg:
        if scope in {'pilot', 'full'}:
            if modules:
                raise ValueError('This scope does not accept modules')
            return control.manifest()
        if scope != 'module' or not modules:
            raise ValueError('Module scope needs configured module names')
        mapping = cfg.get('modules', {})
        if any(m not in mapping for m in modules):
            raise ValueError('Unknown configured module')
        return list(dict.fromkeys(p for m in modules for p in mapping[m]))
    if scope == "pilot":
        if modules:
            raise ValueError("Pilot scope does not accept modules")
        return control.manifest()
    if scope not in {"module", "full"}:
        raise ValueError("Scope must be pilot, module or full")
    if scope == "module" and not modules:
        raise ValueError("Module scope needs --module modules/<directory>")
    if scope == "full" and modules:
        raise ValueError("Full scope does not accept modules")
    for module in modules:
        if not re.fullmatch(r"modules/[a-z0-9][a-z0-9-]+", module):
            raise ValueError("Exact module directory required")
        if not (control.REPO / module / "pyproject.toml").is_file():
            raise ValueError("Unknown module")
    paths = snapshot.git(control.REPO, "ls-tree", "-rz", "--name-only", "HEAD").decode()
    selected = []
    for name in paths.split("\0"):
        if not name:
            continue
        # Generated reports are Founder outputs, never search/read/transfer them.
        if name.startswith(("docs/", ".work/", ".claude/", ".agents/", ".codex/")):
            continue
        if scope == "module" and not any(name.startswith(m + "/") for m in modules):
            continue
        try:
            snapshot.validate_path(name)
        except ValueError:
            continue
        if re.fullmatch(r"[A-Za-z0-9_./-]+", name) and name.endswith(
            (".py", ".md", ".toml", ".yaml", ".yml", ".json", ".sh", ".sql", ".txt")
        ):
            selected.append(name)
    if not selected:
        raise ValueError("Empty source scope")
    # Actual export refuses ignored, symlink and credential-bearing selected blobs.
    return selected


def test_command(control, paths, requested=(), scope='pilot'):
    """Choose configured tests that were actually exported into the active scope."""
    configured = control.PROFILE['test_paths']
    if requested:
        if not set(requested) <= set(configured) or not set(requested) <= set(paths):
            raise ValueError('Test paths exceed configured active scope')
        selected = list(dict.fromkeys(requested))
    else:
        selected = [p for p in configured if p in paths]
    if not selected:
        raise ValueError('No configured tests within active scope')
    command = ('/usr/bin/python3', '-m', 'pytest', '-q')
    if scope == 'pilot':
        command += ('--import-mode=prepend',)
    return command + tuple(selected)


def scoped_tests(control, paths, tests=None, scope='pilot'):
    if not getattr(control, 'PROFILE', None):
        return tests if tests is not None else ('/usr/bin/python3', '-m', 'pytest', '-q')
    if tests is None:
        return test_command(control, paths, scope=scope)
    prefix = ('/usr/bin/python3', '-m', 'pytest', '-q')
    tests = tuple(tests)
    if tests[:4] != prefix:
        raise ValueError('Tests must use the pinned pytest runner')
    requested = tests[4:]
    if requested[:1] == ('--import-mode=prepend',):
        requested = requested[1:]
    return test_command(control, paths, requested, scope)


def prepare(control, name, mode="apply", model=None, auth_mode="api"):
    agent = agents.adapter(name)
    if agent.live:
        control.setup(mode)
        return
    # No gateway call, authentication, installation or inference in offline preparation.
    model = model or ("anthropic/claude-sonnet-4-5" if name == "opencode" else None)
    plan = {
        "agent": name,
        "cli_version": agent.version,
        "model": model,
        "status": agents.UNVERIFIED,
        "installation": agent.install(),
        "image_recipe": "docker/Dockerfile.agent",
        "build_args": {"AGENT_PACKAGE": agent.package, "AGENT_VERSION": agent.version},
        "authentication": agent.auth_configuration(auth_mode),
        "environment": agent.environment(),
        "interactive": agent.interactive(model=model),
        "autonomous": agent.autonomous("OFFLINE COMMAND CHECK ONLY", model=model),
        "policy_template": f"config/policies/{name}.json",
        "github_provider": PROVIDER,
        "network_applied": False,
        "cli_installed": False,
        "model_calls": 0,
    }
    if name == "opencode":
        provider = model.split("/", 1)[0]
        templates = control.read(control.PROJECT_ROOT / "config/policies/opencode.json")["providers"]
        if provider not in templates:
            raise ValueError("Selected provider needs a reviewed endpoint template in a PR")
        plan["provider"] = provider
        plan["opencode_configuration"] = {"model": model}
        plan["selected_network_template"] = templates[provider]
        plan["authentication"]["provider"] = provider
    if mode == "apply":
        control.save(control.STATE / "prepared" / f"{name}.json", plan)
    print(agents.UNVERIFIED)
    print(json.dumps(plan, indent=2))


def branch_name(branch):
    if not re.fullmatch(r"feat/openshell-[a-z0-9][a-z0-9-]{1,70}", branch):
        raise ValueError("Disposable branch must be feat/openshell-<name>; main prohibited")
    return branch


def publication_preflight(control, data):
    """Verify native readiness; Founder accepts application-only Git ref restrictions."""
    cfg = control.settings().get("github", {})
    if cfg.get("provider") != PROVIDER or cfg.get("repository") != REPOSITORY:
        raise ValueError("GitHub provider/repository drift")
    return github_provider.verify(control, data)


def publish(
    control,
    data,
    branch,
    patch_file,
    tests=None,
    base="main",
):
    """Apply a validated patch to a real repository clone; never push snapshot history."""
    if getattr(control, "PROFILE", None):
        from . import configuration
        configuration.verify_checkout(control.PROFILE)
    if getattr(control, 'PROFILE', None):
        paths = data['scope_manifest'] if 'scope_manifest' in data else control.manifest()
    else:
        paths = []
    tests = scoped_tests(control, paths, tests, scope='full')
    branch_name(branch)
    if getattr(control, "PROFILE", None):
        if base != control.PROFILE["base_branch"]:
            raise ValueError("Base branch drift")
    elif base != "main":
        branch_name(base)
    if base == branch:
        raise ValueError("Publication head and base must differ")
    publication_preflight(control, data)
    root = f"/sandbox/publications/{uuid.uuid4().hex}"
    remote = github_provider.git(
        control,
        "ls-remote",
        "--heads",
        f"https://github.com/{REPOSITORY}.git",
        "refs/heads/" + branch,
    )
    exists = bool(remote.stdout.strip())
    github_provider.git(
        control,
        "clone",
        "--branch",
        branch if exists else base,
        "--single-branch",
        f"https://github.com/{REPOSITORY}.git",
        root,
    )
    control.oshell("sandbox", "upload", control.NAME, str(patch_file), root + "/changes.patch")
    git = ["/usr/bin/git", "-c", "core.hooksPath=/dev/null"]
    if not exists:
        control.execute(*git, "checkout", "-b", branch, workdir=root)
    control.execute(*git, "apply", "--check", "changes.patch", workdir=root)
    control.execute(*git, "apply", "changes.patch", workdir=root)
    names = control.execute(*git, "diff", "--name-only", workdir=root).stdout.splitlines()
    allowed = data.get("writable", control.manifest())
    if not names or not set(names).issubset(allowed):
        raise ValueError("Publication patch exceeds approved scope")
    for name in names:
        snapshot.validate_path(name)
    control.execute(*tests, workdir=root, timeout=300)
    control.execute(*git, "add", "--", *names, workdir=root)
    control.execute(
        *git,
        "-c",
        "user.name=OpenShell Sandbox",
        "-c",
        "user.email=sandbox@example.invalid",
        "commit",
        "-m",
        "Bounded OpenShell coding task",
        workdir=root,
    )
    commit = control.execute(*git, "rev-parse", "HEAD", workdir=root).stdout.strip()
    github_provider.git(
        control, "push", "--dry-run", "origin", f"HEAD:refs/heads/{branch}", workdir=root
    )
    github_provider.git(control, "push", "origin", f"HEAD:refs/heads/{branch}", workdir=root)
    # REST-only gh operations fit narrow method/path policies; no GraphQL mutations.
    pulls = f"repos/{REPOSITORY}/pulls"
    query = pulls + f"?head={REPOSITORY.split('/')[0]}:{branch}&state=open"
    existing = github_provider.api(control, query, "--method", "GET", check=False, workdir=root)
    if existing.returncode:
        raise ValueError(github_provider.PERMISSIONS)
    rows = json.loads(existing.stdout)
    body = "Bounded Codex task. Focused tests passed. Founder review and merge required."
    if rows:
        number = rows[0]["number"]
        updated = github_provider.api(
            control,
            f"{pulls}/{number}",
            "--method",
            "PATCH",
            "-f",
            "body=" + body,
            workdir=root,
            check=False,
        )
        if updated.returncode:
            raise ValueError(github_provider.PERMISSIONS)
    else:
        created = github_provider.api(
            control,
            pulls,
            "--method",
            "POST",
            "-f",
            "base=" + base,
            "-f",
            "head=" + branch,
            "-f",
            "title=OpenShell Codex acceptance",
            "-f",
            "body=" + body,
            "-F",
            "draft=true",
            workdir=root,
            check=False,
        )
        if created.returncode:
            raise ValueError(github_provider.PERMISSIONS)
        number = json.loads(created.stdout)["number"]

    return {
        "commit": commit,
        "branch": branch,
        "base": base,
        "pr": number,
        "repository": REPOSITORY,
    }


def run(control, name, *args, **kwargs):
    agents.require_live(name)
    if (control.STATE / "task.lock").is_symlink():
        raise ValueError("Symlink task lock")
    with (control.STATE / "task.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError(
                "Concurrent active agent task; wait for it to finish and retry."
            ) from None
        return _run(control, name, *args, **kwargs)


def _run(
    control,
    name,
    prompt,
    scope="pilot",
    modules=(),
    model=None,
    attempts=1,
    branch=None,
    tests=None,
    release_lock=None,
    base="main",
):
    agent = agents.require_live(name)  # Gate before setup, login, policy or other side effects.
    if not 1 <= attempts <= 3:
        raise ValueError("Attempts must be between 1 and 3")
    if not prompt.strip() or len(prompt.encode()) > 32768:
        raise ValueError("Prompt must be nonempty and bounded to 32 KiB")
    snapshot.validate_content("task prompt", prompt.encode())
    paths = scope_paths(control, scope, modules)
    tests = scoped_tests(control, paths, tests, scope)
    if getattr(control, "PROFILE", None):
        from . import configuration
        configuration.verify_checkout(control.PROFILE)
    data = control.state()
    revision = control.effective(data["policy"])
    control.verify_tools(data)
    if not control.login_status(data):
        raise ValueError("Codex login required; run start.sh login")
    if branch:
        branch_name(branch)
        if getattr(control, "PROFILE", None):
            if base != control.PROFILE["base_branch"]:
                raise ValueError("Base branch drift")
        elif base != "main":
            branch_name(base)
        if base == branch:
            raise ValueError("Publication head and base must differ")
        publication_preflight(control, data)  # Avoid paid work for a known blocked publication.
    control.refresh(data, names=paths)
    if getattr(control, "PROFILE", None):
        from . import configuration
        data["writable"] = [p for p in paths if p in configuration.paths(control.EDIT_SCOPE)]
    else:
        data["writable"] = [p for p in paths if p.rsplit("/", 1)[-1] != "AGENTS.md"]
    if scope == "pilot" and not getattr(control, "PROFILE", None):
        data["writable"] = [p for p in paths if p.endswith(".py")]
    control.save(control.STATE / "state.json", data)
    value, provenance = control.current(data)
    report = {
        "agent": name,
        "cli_version": agent.version,
        "model": model,
        "mode": "autonomous",
        "scope": scope,
        "modules": list(modules),
        "snapshot_commit": provenance["commit"],
        "sandbox_id": data["sandbox_id"],
        "policy_revision": revision["version"],
        "policy_hash": revision["hash"],
        "prompt_sha256": control.digest(prompt.encode()),
        "attempts": [],
        "status": "FAIL",
        "github": "SKIP",
        "cost_usd": None,
        "raw_output_saved": False,
    }
    started = time.monotonic()
    if release_lock is not None:
        # Founder rule review must remain available while the CLI is running.
        release_lock()

    def sync_policy():
        active = control.state()
        if active["sandbox_id"] != data["sandbox_id"] or active["snapshot"] != data["snapshot"]:
            raise ValueError("Active snapshot changed during execution; prior workspace preserved")
        data["policy"] = active["policy"]
        loaded = control.effective(data["policy"])
        report["policy_revision"] = loaded["version"]
        report["policy_hash"] = loaded["hash"]

    try:
        for attempt in range(attempts):
            sync_policy()
            task = prompt if attempt == 0 else prompt + "\nFocused tests failed; fix and retry."
            result = control.session(
                "task",
                argv=agent.autonomous(task, data["tools"]["launcher"], model),
                root=value["root"],
                check=False,
                timeout=600,
            )
            check = control.execute(*tests, workdir=value["root"], check=False, timeout=300)
            report["attempts"].append(
                {
                    "exit_code": result.returncode,
                    "tests_exit_code": check.returncode,
                    "usage": agents.usage(name, result.stdout),
                    "failure_category": agents.failure_category(result.stdout + result.stderr)
                    if result.returncode
                    else None,
                }
            )
            if result.returncode == check.returncode == 0:
                break
        else:
            raise ValueError("Autonomous task or focused tests failed; evidence retained")
        sync_policy()
        patch = control.export_patch(data)
        # Validate all edits on the host before any local commit or publication.
        changed = control.guest("collect", root=value["root"], provenance=provenance)["files"]
        names = [
            p
            for p in paths
            if changed[p] is None
            or control.digest(base64.b64decode(changed[p]))
            != control.digest((control.STATE / value["local"] / p).read_bytes())
        ]
        git = ["/usr/bin/git", "-c", "core.hooksPath=/dev/null"]
        control.execute(*git, "add", "--", *names, workdir=value["root"])
        control.execute(
            *git,
            "-c",
            "user.name=OpenShell Sandbox",
            "-c",
            "user.email=sandbox@example.invalid",
            "commit",
            "-m",
            "Bounded autonomous coding task",
            workdir=value["root"],
        )
        report["local_commit"] = control.execute(
            *git, "rev-parse", "HEAD", workdir=value["root"]
        ).stdout.strip()
        if branch:
            report["github"] = publish(control, data, branch, patch, tests, base)
        report["status"] = "PASS"
        return report
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        control.save(control.STATE / "executions" / f"{uuid.uuid4().hex}.json", report)
