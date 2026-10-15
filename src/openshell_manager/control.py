"""Deterministic OpenShell 0.1.2 operator workflow; no host credential transfer."""

from __future__ import annotations

import argparse
import base64
import difflib
import fcntl
import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid
from contextlib import contextmanager, redirect_stdout
from pathlib import Path

from . import bootstrap
from . import github_provider
from . import snapshot
from . import workflow

from . import agents

from . import provenance
from .assets import ROOT as HERE, RESOURCES as PROJECT_ROOT
REPO = None  # Assigned only from a validated project profile.
STATE = Path.home() / ".local/state/openshell-manager/unconfigured"
CONFIG = PROJECT_ROOT / "config/defaults/phase2.json"
NAME = "example-dev-phase2"  # Never accept arbitrary resource names from guest data/environment.
PROFILE = None
WORKSPACE = "/sandbox/project/checkout"
SOURCE_MANIFEST = None  # External project-owned scopes, assigned by configure.
EDIT_SCOPE = None
PROVIDER_PROFILE = PROJECT_ROOT / "config/policies/github.yaml"
MAX_TRANSFER = 8 * 1024 * 1024


def configure(project, host=None, install_dependencies=False, base_branch=None):
    global PROFILE, REPO, STATE, CONFIG, NAME, WORKSPACE, SOURCE_MANIFEST, EDIT_SCOPE, PROVIDER_PROFILE
    from . import configuration
    pin = configuration.file(project).get('manager_revision')
    provenance.verify(pin)
    cfg = configuration.load(project, host)
    require(base_branch is None or base_branch == cfg["base_branch"], "Base branch differs from project profile")
    for rule in cfg['rules']:
        validate_rule({**rule, 'binaries': [rule['binary']] if 'binary' in rule else ['/usr/bin/codex']})
    target = cfg['state']
    safe_dir(target)
    binding = {k: str(cfg[k]) for k in ('id', 'root', 'repository', 'sandbox', 'workspace', 'manager_revision')}
    # A project ID permanently binds its resource and credential namespace.
    lock_path = target / 'binding.lock'
    require(not lock_path.is_symlink(), 'Symlink binding lock')
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = target / 'project-binding.json'
        if path.exists():
            require(read(path) == binding, 'Conflicting project identity or Manager pin; existing state preserved')
        else:
            require(not (target / 'state.json').exists(), 'Unbound existing state; migration review required')
            save(path, binding)
    PROFILE, REPO, STATE = cfg, Path(cfg['root']), target
    CONFIG, NAME, WORKSPACE = Path(project).absolute(), cfg['sandbox'], cfg['workspace']
    SOURCE_MANIFEST, EDIT_SCOPE = cfg['source_manifest'], cfg['edit_scope']
    PROVIDER_PROFILE = STATE / 'github-profile.yaml'
    require(not PROVIDER_PROFILE.is_symlink(), 'Symlink provider profile')
    if install_dependencies:
        with setup_lock("apply"), idle_lease():
            yaml = bootstrap.yaml_module(command, install=True)
    else:
        yaml = bootstrap.yaml_module(command)
    content = yaml.safe_dump(configuration.provider_profile(cfg), sort_keys=False)
    require(not PROVIDER_PROFILE.is_symlink(), 'Symlink provider profile')
    if PROVIDER_PROFILE.exists():
        require(PROVIDER_PROFILE.read_text() == content, 'Provider profile drift; review before host operations')
    else:
        with PROVIDER_PROFILE.open('x') as stream:
            stream.write(content)
    github_provider.PROVIDER = workflow.PROVIDER = cfg['github']['provider']
    github_provider.REPOSITORY = workflow.REPOSITORY = cfg['repository']
    github_provider.RULE = '_provider_' + cfg['github']['provider'].replace('-', '_')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def safe_dir(path):
    require(path.resolve() == path.absolute(), "Symlink in local state/output path")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)


def save(path, value):
    safe_dir(path.parent)
    require(not path.is_symlink(), "Symlink state file")
    temp = path.with_name(path.name + "." + uuid.uuid4().hex)
    with temp.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    temp.chmod(0o600)
    temp.replace(path)


def read(path):
    require(path.resolve() == path.absolute(), "Symlink state input")
    return json.loads(path.read_text())


def command(args, *, check=True, interactive=False, timeout=120):
    from .approval import diagnostics
    # Never print arbitrary stdout/stderr: CLI/guest logs can contain auth material.
    result = diagnostics.command(subprocess.run,
        args, text=True, capture_output=not interactive, check=False, timeout=timeout
    )
    if check and result.returncode:
        raise ValueError(
            f"Command failed ({result.returncode}): {args[0:3]}; "
            "run setup.sh --check or rules.sh diagnose"
        )
    return result


def oshell(*args, **kwargs):
    return command(["openshell", *args], **kwargs)


def api(*args):
    return json.loads(oshell(*args, "-o", "json").stdout)


def settings():
    data = PROFILE if PROFILE is not None else read(CONFIG)
    require(data["sandbox"] == NAME, "Phase 2 resource name drift")
    return data


def config_hash():
    return digest(
        (json.dumps(PROFILE, sort_keys=True, default=str).encode() if PROFILE else CONFIG.read_bytes())
        + SOURCE_MANIFEST.read_bytes()
        + EDIT_SCOPE.read_bytes()
    )


def execute(*args, interactive=False, workdir="/sandbox", check=True, timeout=120):
    return oshell(
        "sandbox",
        "exec",
        "--name",
        NAME,
        "--no-login-shell",
        "--tty" if interactive else "--no-tty",
        "--workdir",
        workdir,
        "--env",
        "CODEX_HOME=/sandbox/.codex",
        "--",
        *args,
        interactive=interactive,
        check=check,
        timeout=None if interactive else timeout,
    )


def session(action, *, interactive=False, check=True, timeout=300, **fields):
    project = {"repository": PROFILE["repository"], "workspace": WORKSPACE,
               "project_id": PROFILE["id"], "base": PROFILE["base_branch"]} if PROFILE else {}
    payload = base64.b64encode(json.dumps({"action": action, **project, **fields}).encode()).decode()
    return execute(
        "/usr/bin/python3",
        "-I",
        "-c",
        (HERE / "session.py").read_text(),
        payload,
        interactive=interactive,
        check=check,
        timeout=timeout,
    )


def sync_checkout(check=False, base="main"):
    if task_active():
        value = {
            "status": "SKIP",
            "state": "active-agent",
            "fix": "Wait for the bounded task to finish, then retry.",
        }
        print("SKIP: active agent task; no checkout synchronization")
        return value
    result = session("sync", read_only=check, base=base)
    value = json.loads(result.stdout)
    print(f"{value['status']}: {WORKSPACE} {value['state']}")
    if value.get("fix"):
        print(value["fix"])
    return value


def guest(action, **fields):
    payload = base64.b64encode(json.dumps({"action": action, **fields}).encode()).decode()
    result = execute("/usr/bin/python3", "-I", "-c", (HERE / "guest.py").read_text(), payload)
    require(len(result.stdout) <= MAX_TRANSFER * 2, "Guest transfer exceeds limit")
    return json.loads(result.stdout)


def ready(info):
    require(
        info["name"] == NAME and info["phase"] == "Ready",
        "Development sandbox is not Ready. Run start.sh resume, then setup.sh --check",
    )
    require(
        info["configuration_admission"]["state"] == "accepted",
        "Configuration not accepted; run rules.sh diagnose",
    )


def verify_interrupted_bootstrap(info, image_id):
    """Resume only a project-owned, unchanged deny-all bootstrap sandbox."""
    require(PROFILE is not None, "Unbound existing sandbox; refusing adoption")
    binding_path = STATE / "project-binding.json"
    bootstrap_path = STATE / "bootstrap-policy.json"
    require(
        binding_path.is_file() and bootstrap_path.is_file(),
        "Existing sandbox lacks Manager bootstrap evidence; refusing adoption",
    )
    binding = {
        key: str(PROFILE[key])
        for key in ("id", "root", "repository", "sandbox", "workspace", "manager_revision")
    }
    require(
        read(binding_path) == binding,
        "Project binding differs from interrupted setup; refusing adoption",
    )
    require(
        read(bootstrap_path) == base_policy(),
        "Bootstrap policy receipt differs; refusing adoption",
    )
    sandbox_id = info.get("id")
    require(
        isinstance(sandbox_id, str)
        and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", sandbox_id),
        "Sandbox identity unavailable; refusing bootstrap recovery",
    )
    labels = info.get("labels", {})
    require(
        labels.get("manager.phase") == "2"
        and labels.get("manager.project") == PROFILE["id"]
        and labels.get("manager.image") == image_id.split(":")[1][:32],
        "Existing sandbox ownership/image differs; refusing adoption",
    )
    # Native policy must still be the exact deny-all bootstrap, with manual approval.
    # This also rejects added provider rules, changed filesystem access and drift.
    effective(base_policy())
    print("PASS: interrupted bootstrap verified; resuming existing sandbox")


def normalize(policy):
    result = json.loads(json.dumps(policy))
    result.setdefault("network_policies", {})
    for rule in result["network_policies"].values():
        rule["binaries"] = sorted(
            [{"path": b} if isinstance(b, str) else b for b in rule["binaries"]],
            key=lambda b: b["path"],
        )
    return result


def effective(expected=None, github_profile=None):
    info = api("sandbox", "get", NAME)
    ready(info)
    policy = api("policy", "get", NAME, "--full")
    history = api("policy", "list", NAME)
    composed = any(k.startswith("_provider_") for k in policy["policy"].get("network_policies", {}))
    base = github_provider.base_policy(sys.modules[__name__], policy["policy"], github_profile)
    loaded = any(
        row["version"] == policy["version"]
        and row["status"] == "loaded"
        and (composed or row["hash"] == policy["hash"])
        for row in history["revisions"]
    )
    require(
        loaded
        and policy["status"] == "effective"
        and policy["active_version"] == policy["version"]
        and policy["policy_source"] == "sandbox"
        and info["configuration_admission"]["policy_version"] == policy["version"]
        and info["configuration_admission"]["policy_hash"] == policy["hash"],
        "Policy revision is not Loaded and effective; run rules.sh status",
    )
    mode = json.loads(oshell("settings", "get", NAME, "--json").stdout)
    require(
        mode["settings"]["proposal_approval_mode"]["value"] in {"<unset>", "manual"},
        "Native proposal approval is not manual; review settings before use",
    )
    if expected is not None:
        require(
            normalize(base) == normalize(expected),
            "Effective policy drift. No replacement performed. Run rules.sh status; "
            "use rules.sh recover only for an interrupted wrapper change",
        )
    if composed:
        github_provider.status(sys.modules[__name__], info, policy["hash"])
    return policy


def state(allow_config_drift=False):
    data = read(STATE / "state.json")
    require(
        allow_config_drift or data["config_hash"] == config_hash(),
        "Reviewed configuration drift; review PR then run rules.sh reconcile",
    )
    info = api("sandbox", "get", NAME)
    require(info["id"] == data["sandbox_id"], "Sandbox identity changed; setup review required")
    return data


def preflight():
    require(
        platform.system() == "Linux" and platform.machine() == "x86_64",
        "Validated host requires Linux x86_64",
    )
    for binary in ("openshell", "openshell-gateway", "docker", "git", "python3"):
        require(
            shutil.which(binary), f"Missing dependency: {binary}. Install it, then run setup.sh"
        )
    require(
        oshell("--version").stdout.strip() == settings()["cli_version"],
        "Unsupported OpenShell version; no upgrade performed",
    )
    require(
        os.access("/dev/kvm", os.R_OK | os.W_OK),
        "KVM denied. Check id and /dev/kvm permissions; sign in again after approved group change",
    )
    overrides = [key for key in os.environ if key.startswith("OPENSHELL_")]
    require(
        not overrides, "OpenShell environment override detected; use the reviewed local gateway"
    )
    cfg = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    cfg = cfg / "openshell/gateway.toml"
    require(
        cfg.is_file(),
        "Gateway config missing. Follow Phase 1 gateway installation; no host changes",
    )
    data = tomllib.loads(cfg.read_text())["openshell"]
    require(
        data["version"] == 2
        and data["gateway"]["compute_driver"] == "vm"
        and data["gateway"]["bind_address"] == "127.0.0.1:17670",
        "Gateway config drift: schema v2 loopback VM required",
    )
    driver = Path(data["drivers"]["vm"]["driver_dir"]) / "openshell-driver-vm"
    require(driver.is_file() and os.access(driver, os.X_OK), "VM driver executable missing")
    for binary in ("openshell-gateway", str(driver)):
        require(
            "0.1.2" in command([binary, "--version"]).stdout,
            "Gateway/VM driver version mismatch; install OpenShell 0.1.2",
        )
    command(["openshell-gateway", "config", "preflight", "--path", str(cfg)])
    # Establish that the active process uses the reviewed default configuration.
    pid = command(
        ["systemctl", "--user", "show", "openshell-gateway", "--property=MainPID", "--value"]
    ).stdout.strip()
    require(
        pid.isdigit() and pid != "0",
        "User gateway service not active; run systemctl --user status openshell-gateway",
    )
    argv = (Path("/proc") / pid / "cmdline").read_bytes().split(b"\0")
    env = (Path("/proc") / pid / "environ").read_bytes().split(b"\0")
    entries = dict(item.split(b"=", 1) for item in env if b"=" in item)
    require(
        argv == [b"/usr/bin/openshell-gateway", b""]
        and entries.get(b"HOME") == str(Path.home()).encode()
        and not any(
            k.startswith(b"OPENSHELL_") and k != b"OPENSHELL_LOCAL_TLS_DIR" for k in entries
        )
        and entries.get(b"XDG_CONFIG_HOME", str(Path.home() / ".config").encode())
        == str(cfg.parent.parent).encode(),
        "Running gateway config cannot be established; inspect service overrides manually",
    )
    # Contents are used only for configuration selection and are never printed or stored.
    oshell("status")
    command(["docker", "info", "--format", "{{.ServerVersion}}"])
    print("PASS: Linux, Docker, KVM, CLI 0.1.2, gateway, VM driver and config")


def image(check=False):
    cfg = settings()
    result = command(
        ["docker", "image", "inspect", cfg["image_id"], "--format", "{{.Id}}"], check=False
    )
    if result.returncode:
        require(PROFILE is None, "Reviewed project image digest missing; provision explicitly before setup")
        recipe = digest((PROJECT_ROOT / "docker/Dockerfile").read_bytes())
        built = command(["docker", "image", "inspect", cfg["image"]], check=False)
        info = json.loads(built.stdout)[0] if built.returncode == 0 else None
        if info:
            require(
                info["Config"].get("Labels", {}).get("manager.dockerfile") == recipe,
                "Existing image tag has an incompatible recipe; preserved for review",
            )
        require(info or not check, "Pinned image missing; run setup.sh to build the image")
        # Build in an empty context: never send repository/secrets to Docker.
        if not info:
            with tempfile.TemporaryDirectory(prefix="manager-build-") as temp:
                shutil.copyfile(PROJECT_ROOT / "docker/Dockerfile", Path(temp) / "Dockerfile")
                command(
                    [
                        "docker",
                        "build",
                        "--label",
                        "manager.dockerfile=" + recipe,
                        "-t",
                        cfg["image"],
                        temp,
                    ],
                    timeout=1800,
                )
        actual = command(
            ["docker", "image", "inspect", cfg["image"], "--format", "{{.Id}}"]
        ).stdout.strip()
        require(re.fullmatch(r"sha256:[a-f0-9]{64}", actual), "Invalid Docker image ID")
        if check:
            inventory = read(STATE / "built-image-review.json")
            require(
                inventory["image_id"] == actual
                and inventory["dockerfile_sha256"] == recipe
                and inventory["codex_version"] == cfg["codex_version"],
                "Local image inventory differs; run setup.sh to verify it",
            )
            return actual
        version = command(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--entrypoint",
                "/usr/local/bin/codex",
                actual,
                "--version",
            ]
        ).stdout.strip()
        require(version == cfg["codex_version"], "Built image Codex version mismatch")
        inventory = {
            "image_id": actual,
            "dockerfile_sha256": recipe,
            "codex_version": version,
            "inventory": command(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--read-only",
                    "--entrypoint",
                    "/usr/bin/dpkg-query",
                    actual,
                    "-W",
                ]
            ).stdout.splitlines(),
        }
        if not check:
            save(STATE / "built-image-review.json", inventory)
        print("PASS: recipe, pinned Codex and local image digest verified; inventory recorded")
        return actual
    require(result.stdout.strip() == cfg["image_id"], "Image identity mismatch")
    print("PASS: compatible pinned development image reused")
    return cfg["image_id"]


def validate_rule(rule):
    require(re.fullmatch(r"[a-z][a-z0-9_]{1,60}", rule["id"]), "Invalid rule ID")
    require(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", rule["host"])
        and "." in rule["host"]
        and ".." not in rule["host"]
        and not re.fullmatch(r"[0-9.]+", rule["host"]),
        "Exact DNS hostname required",
    )
    require(rule["port"] == 443, "Compatibility wrapper allows HTTPS 443 only")
    require(rule["access"] in {"read-only", "full"}, "Invalid endpoint access")
    for binary in rule["binaries"]:
        require(
            re.fullmatch(r"/(?:[A-Za-z0-9_@.+-]+/)*[A-Za-z0-9_@.+-]+", binary),
            "Exact executable path required",
        )


def base_policy():
    # Preserve the Phase 1 filesystem boundary; this is a separate sandbox policy.
    return {
        "version": 1,
        "filesystem_policy": {
            "include_workdir": True,
            "read_only": [
                "/bin",
                "/usr",
                "/lib",
                "/proc",
                "/dev/urandom",
                "/app",
                "/etc",
                "/var/log",
            ],
            "read_write": ["/sandbox", "/tmp", "/dev/null"],
        },
        "landlock": {"compatibility": "hard_requirement"},
        "network_policies": {},
    }


def policy_for(tools):
    policy = base_policy()
    for default in settings()["rules"]:
        rule = {
            **default,
            "binaries": tools["binaries"]
            if default.get("agent") == "codex"
            else [default["binary"]],
        }
        require(
            all(b in tools["binaries"] + ["/usr/bin/curl"] for b in rule["binaries"]),
            "Persistent rule executable no longer matches discovered pinned tools",
        )
        validate_rule(rule)
        policy["network_policies"][rule["id"]] = network_rule(rule)
    return policy


def network_rule(rule):
    return {
        "name": rule["id"],
        "binaries": [{"path": b} for b in rule["binaries"]],
        "endpoints": [
            {
                "host": rule["host"],
                "port": rule["port"],
                "protocol": "rest",
                "enforcement": "enforce",
                "access": rule["access"],
            }
        ],
    }


def verify_tools(data=None):
    tools = guest("tools")
    require(
        tools["uid"] != 0 and tools["version"] == settings()["codex_version"],
        "Guest UID or Codex version incompatible",
    )
    if data:
        require(
            tools["binaries"] == data["tools"]["binaries"]
            and tools["native_sha256"] == data["tools"]["native_sha256"],
            "Executable identity drift. No policy expanded. Run setup.sh --check",
        )
    print("PASS: non-root Git, Python, pytest, Codex and native executable discovery")
    return tools


def login_status(data):
    result = execute(data["tools"]["launcher"], "login", "status", check=False)
    # Ignore all output: it may include account identifiers or API-key fragments.
    print(
        "PASS: Codex authenticated"
        if result.returncode == 0
        else "LOGIN REQUIRED: ./scripts/start.sh login"
    )
    return result.returncode == 0


def manifest():
    names = [
        line.strip()
        for line in SOURCE_MANIFEST.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    for name in names:
        snapshot.validate_path(name)
        require(
            re.fullmatch(r"[A-Za-z0-9_./-]+", name) and name.endswith((".py", ".md", ".toml", ".yaml", ".yml", ".json", ".sh", ".sql", ".txt")),
            "Source manifest permits reviewed plain source files only",
        )
    require(names and len(set(names)) == len(names), "Manifest must be nonempty and unique")
    return names


def current(data):
    value = data.get("snapshot")
    require(value is not None, "Repository source missing; run refresh.sh")
    expected = read(STATE / value["local"] / "snapshot-provenance.json")
    require(
        digest(json.dumps(expected, sort_keys=True).encode()) == value["provenance_sha256"],
        "Host snapshot provenance changed",
    )
    require(
        expected["manifest"] == data.get("scope_manifest", manifest()),
        "Manifest scope drift; review then refresh",
    )
    for row in expected["files"]:
        path = STATE / value["local"] / row["path"]
        require(
            path.resolve() == path.absolute() and digest(path.read_bytes()) == row["sha256"],
            "Host baseline checksum mismatch",
        )
    return value, expected


def refresh(data, names=None):
    if PROFILE:
        from . import configuration
        configuration.verify_checkout(PROFILE)
    # Every refresh uses a new destination; old work is preserved.
    names = manifest() if names is None else names
    token = uuid.uuid4().hex
    local = f"snapshots/{token}/source"
    safe_dir((STATE / local).parent)
    provenance = snapshot.export(REPO, "HEAD", names, STATE / local)
    destination = f"/sandbox/{PROFILE['id'] if PROFILE else 'project'}/snapshots/{token}"
    oshell("sandbox", "upload", "--no-git-ignore", NAME, str(STATE / local), destination)
    root = destination + "/source"  # CLI preserves the uploaded directory basename.
    guest("initialize", root=root, provenance=provenance)
    data["snapshot"] = {
        "root": root,
        "local": local,
        "provenance_sha256": digest(json.dumps(provenance, sort_keys=True).encode()),
    }
    data["scope_manifest"] = names
    data.pop("writable", None)
    save(STATE / "state.json", data)
    print(f"PASS: committed snapshot {provenance['commit'][:12]} ready; prior work preserved")


def migrate_revision(project, host, previous_revision):
    """Explicit state-preserving migration between verified immutable Manager revisions.

    No sandbox, provider, policy, credential or workspace mutations occur here.
    """
    from . import configuration

    require(provenance.sha(previous_revision), "Invalid previous Manager revision")
    target_revision, _ = provenance.identity()
    provenance.verify(target_revision)
    require(previous_revision != target_revision, "Manager already pinned to this revision")
    cfg = configuration.load(project, host)
    require(cfg["manager_revision"] in {previous_revision, target_revision},
            "Unexpected project revision; migration refused")
    root = Path(cfg["state"])
    require(root.is_dir() and root.resolve() == root.absolute(),
            "Missing or unsafe Manager state directory")
    project_path = Path(project).absolute()
    binding_path = root / "project-binding.json"
    state_path = root / "state.json"
    bootstrap_path = root / "bootstrap-policy.json"
    require(bootstrap_path.is_file() and not bootstrap_path.is_symlink(),
            "Missing Manager bootstrap receipt; migration refused")
    require(read(bootstrap_path) == base_policy(),
            "Bootstrap receipt drift; migration refused")

    def config_digest(revision):
        normalized = dict(cfg)
        normalized["manager_revision"] = revision
        return digest(json.dumps(normalized, sort_keys=True, default=str).encode()
                      + Path(cfg["source_manifest"]).read_bytes()
                      + Path(cfg["edit_scope"]).read_bytes())

    old_hash, new_hash = config_digest(previous_revision), config_digest(target_revision)
    expected_binding = {
        key: str(cfg[key])
        for key in ("id", "root", "repository", "sandbox", "workspace", "manager_revision")
    }
    expected_binding["manager_revision"] = previous_revision
    next_binding = {**expected_binding, "manager_revision": target_revision}
    require(not any(p.is_symlink() for p in (project_path, binding_path, state_path)),
            "Symlink in revision migration input; refused")
    require(project_path.is_file() and binding_path.is_file(),
            "Project profile or binding missing; migration refused")
    lock_path = root / "operation.lock"
    task_path = root / "task.lock"
    require(not lock_path.is_symlink() and not task_path.is_symlink(),
            "Unsafe Manager operation/task lock")
    with lock_path.open("a") as operation:
        try:
            fcntl.flock(operation, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Active Manager operation; pin migration refused") from None
        with task_path.open("a") as task:
            try:
                fcntl.flock(task, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Active Manager task; pin migration refused") from None

            profile = configuration.file(project_path)
            require(profile.get("manager_revision") in {previous_revision, target_revision},
                    "Project pin changed concurrently; migration refused")
            binding = read(binding_path)
            require(binding in (expected_binding, next_binding),
                    "Manager project binding drift; migration refused")
            state_data = read(state_path) if state_path.exists() else None
            if state_data is not None:
                require(state_data.get("config_hash") in {old_hash, new_hash}
                        and isinstance(state_data.get("sandbox_id"), str),
                        "State configuration/identity drift; migration refused")

            info = api("sandbox", "get", cfg["sandbox"])
            require(
                info.get("name") == cfg["sandbox"]
                and info.get("phase") == "Ready"
                and info.get("configuration_admission", {}).get("state") == "accepted"
                and info.get("labels", {}).get("manager.phase") == "2"
                and info.get("labels", {}).get("manager.project") == cfg["id"]
                and info.get("labels", {}).get("manager.image") == cfg["image_id"][7:39]
                and (state_data is None or state_data["sandbox_id"] == info.get("id")),
                "Sandbox identity/image/ownership differs; migration refused",
            )

            next_profile = {**profile, "manager_revision": target_revision}
            if state_data is not None:
                next_state = {**state_data, "config_hash": new_hash}
            else:
                next_state = None

            for path, current, wanted in (
                (project_path, profile, next_profile),
                (binding_path, binding, next_binding),
                (state_path, state_data, next_state),
            ):
                if current is None or current == wanted:
                    continue
                # Verify backup content against the *old* revision, including
                # retries after a partially completed migration.
                old = dict(current)
                if path == project_path:
                    old["manager_revision"] = previous_revision
                elif path == binding_path:
                    old = expected_binding
                else:
                    old["config_hash"] = old_hash
                backup = path.with_name(path.name + ".before-" + previous_revision[:12])
                require(not backup.is_symlink(), "Unsafe migration backup")
                if backup.exists():
                    require(read(backup) == old, "Existing migration backup mismatch")
                else:
                    with backup.open("x") as output:
                        json.dump(old, output, indent=2)
                        output.write("\n")
                    backup.chmod(0o600)
                save(path, wanted)

    print("PASS: verified Manager revision migrated; sandbox/state/policy preserved")


def setup(mode):
    if mode == "dry-run":
        print("STATUS  CHECK                 FIX / PLAN")
        for label in (
            "host tools + permissions",
            "gateway (reuse or start)",
            "image (verify or build)",
            "MicroVM (reuse or create)",
            "GitHub Provider + gh",
            "Project HTTPS clone / safe sync",
            "Codex login + directory",
        ):
            print(f"SKIP    {label}: setup.sh applies this step; no changes in dry-run")
        return
    require(not task_active(), "Active session/task; setup refused")
    preflight()
    image_id = image(check=mode != "apply")
    listing = oshell("sandbox", "list").stdout
    exists = any(line.split() and line.split()[0] == NAME for line in listing.splitlines())
    recovering = exists and not (STATE / "state.json").is_file()
    if recovering:
        require(mode == "apply", "Setup incomplete; run setup.sh to resume verified bootstrap")
    if not exists:
        require(mode != "check", "Development sandbox missing; run setup.sh")
        image_ref = NAME + ":" + image_id.split(":")[1][:12]
        command(["docker", "tag", image_id, image_ref])
        empty_policy = base_policy()
        safe_dir(STATE)
        save(STATE / "bootstrap-policy.json", empty_policy)
        oshell(
            "sandbox",
            "create",
            "--name",
            NAME,
            "--from",
            image_ref,
            "--policy",
            str(STATE / "bootstrap-policy.json"),
            "--no-auto-providers",
            "--approval-mode",
            "manual",
            "--label",
            "manager.phase=2",
            "--label",
            "manager.image=" + image_id.split(":")[1][:32],
            *(["--label", "manager.project=" + PROFILE["id"]] if PROFILE else []),
            "--detach",
            "--no-tty",
            "--",
            "/bin/sleep",
            "infinity",
            timeout=600,
        )
    info = api("sandbox", "get", NAME)
    ready(info)
    require(
        info["labels"].get("manager.phase") == "2"
        and info["labels"].get("manager.image") == image_id.split(":")[1][:32],
        "Existing resource ownership/image drift; refusing adoption",
    )
    if PROFILE:
        require(info["labels"].get("manager.project") == PROFILE["id"], "Project ownership label mismatch")
        probe = (HERE / "session.py").read_text().split('if __name__ == "__main__":')[0]
        active = execute("/usr/bin/python3", "-I", "-c", probe + "\nprint(int(unmanaged_agent()))").stdout.strip()
        require(active == "0", "Unmanaged active guest agent; setup refused")
    if recovering:
        verify_interrupted_bootstrap(info, image_id)
    if (STATE / "state.json").exists():
        data = state()
        verify_tools(data)
        github_provider.reconcile(sys.modules[__name__], data, mode)
    else:
        require(mode != "check", "Setup incomplete; rerun setup.sh")
        baseline = base_policy()
        effective(baseline)  # Adopt only our own deny-all bootstrap sandbox.
        tools = verify_tools()
        data = {
            "sandbox_id": info["id"],
            "config_hash": config_hash(),
            "tools": tools,
            "policy": baseline,
            "snapshot": None,
        }
        save(STATE / "state.json", data)
    reviewed = policy_for(data["tools"])
    # Add only absent reviewed default rules. Never replace a policy during setup.
    for key, rule in reviewed["network_policies"].items():
        if key in data["policy"]["network_policies"]:
            require(data["policy"]["network_policies"][key] == rule, "Default rule drift")
            continue
        require(mode != "check", "Reviewed rules missing; rerun setup.sh")
        endpoint = rule["endpoints"][0]
        change = {"id": key, **endpoint, "binaries": [b["path"] for b in rule["binaries"]]}
        update(data, change, confirmation=False)
    effective(data["policy"])
    print("PASS: reviewed policy Loaded and effective; default deny; manual approval")
    if mode == "apply":
        guest("prepare_auth")
    if not (STATE / "github-ready.json").exists():
        github_provider.reconcile(sys.modules[__name__], data, mode)
    synced = sync_checkout(check=mode == "check")
    if synced["state"] != "active-agent":
        help_result = execute(
            *agents.adapter("codex").interactive(data["tools"]["launcher"]),
            "--help",
            workdir=WORKSPACE,
        )
        require(
            "--ask-for-approval" in help_result.stdout and "--sandbox" in help_result.stdout,
            "Pinned Codex CLI flags unavailable; rerun setup.sh",
        )
        print("PASS: Codex launches in the configured workspace; requested CLI flags supported (no model call)")
    else:
        print("SKIP: Codex directory probe while another agent is active")
    login_status(data)
    if mode == "apply":
        data["config_hash"] = config_hash()
        save(STATE / "state.json", data)
    print("PASS: setup checks complete. Start: ./scripts/start.sh codex")


def setup_report(mode):
    """Keep operator output concise. Never expose native CLI or guest transport logs."""
    require(not task_active(), "Active session/task; setup refused")
    output = io.StringIO()
    print("STATUS | CHECK | NEXT STEP")
    try:
        with redirect_stdout(output), setup_lock(mode), idle_lease(mode == "apply" or STATE.exists()):
            if mode == "apply":
                bootstrap.host(sys.modules[__name__])
                print("PASS: Host bootstrap / gateway reuse")
            setup(mode)
        require(
            "LOGIN REQUIRED:" not in output.getvalue(),
            "Codex login required. Run ./scripts/start.sh login; retry setup.sh --check.",
        )
    finally:
        for line in output.getvalue().splitlines():
            status, separator, text = line.partition(": ")
            if separator and status in {"PASS", "SKIP", "LOGIN REQUIRED"}:
                state = "FAIL" if status == "LOGIN REQUIRED" else status
                print(f"{state} | {text} | " + ("start.sh login" if state == "FAIL" else "—"))


@contextmanager
def idle_lease(enabled=True):
    """Hold a shared task lease so a new interactive/agent task cannot race mutations."""
    if not enabled:
        yield
        return
    safe_dir(STATE)
    require(not (STATE / 'task.lock').is_symlink(), 'Symlink task lock')
    with (STATE / 'task.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Active session/task; operation refused') from None
        yield


@contextmanager
def setup_lock(mode):
    if mode != "apply":
        yield
        return
    safe_dir(STATE)
    require(not (STATE / "operation.lock").is_symlink(), "Symlink operation lock")
    with (STATE / "operation.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def task_active():
    path = STATE / "task.lock"
    require(not path.is_symlink(), "Symlink task lock")
    if not path.exists():
        return False
    with path.open("r") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
    return False


def patch_from_files(baseline, files, scope=None):
    require(set(files) == set(baseline), "Guest file scope mismatch")
    chunks = []
    total = 0
    for name, original in baseline.items():
        snapshot.validate_path(name)
        encoded = files[name]
        changed = b"" if encoded is None else base64.b64decode(encoded, validate=True)
        total += len(changed)
        require(len(changed) <= 1024 * 1024 and total <= MAX_TRANSFER, "Patch size limit")
        require(b"\0" not in changed, "Binary patch rejected")
        snapshot.validate_content(name, changed)
        old, new = original.decode("utf-8"), changed.decode("utf-8")
        require(not old or old.endswith("\n"), "Baseline needs final newline")
        require(not new or new.endswith("\n"), "Changed file needs final newline")
        if original == changed and encoded is not None:
            continue
        require(scope is None or name in scope, "Edit outside approved writable task scope")
        # Generate on host from validated content. Guest-supplied diff headers/commands ignored.
        chunks.append(f"diff --git a/{name} b/{name}\n")
        if encoded is None:
            chunks.append("deleted file mode 100644\n")
        chunks.extend(
            difflib.unified_diff(
                old.splitlines(True),
                new.splitlines(True),
                fromfile="a/" + name,
                tofile="/dev/null" if encoded is None else "b/" + name,
            )
        )
    return "".join(chunks).encode()


def export_patch(data):
    if PROFILE:
        from . import configuration
        configuration.verify_checkout(PROFILE)
    value, provenance = current(data)
    result = guest("collect", root=value["root"], provenance=provenance)
    baseline = {
        name: (STATE / value["local"] / name).read_bytes() for name in provenance["manifest"]
    }
    scope = data["writable"] if "writable" in data else [
        line.strip()
        for line in EDIT_SCOPE.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    require(set(scope).issubset(baseline), "Edit scope must be inside source manifest")
    patch = patch_from_files(baseline, result["files"], scope)
    require(patch, "No changes to export")
    output = STATE / "exports" / uuid.uuid4().hex
    safe_dir(output)
    (output / "changes.patch").write_bytes(patch)
    save(
        output / "provenance.json",
        {
            "snapshot": provenance,
            "patch_sha256": digest(patch),
            "sandbox_id": data["sandbox_id"],
            "policy_revision": effective(data["policy"])["version"],
            "applied_to_host": False,
            "review_required": True,
        },
    )
    print("PASS: safe patch exported; no host application")
    print(f"Review: git apply --stat {output / 'changes.patch'}")
    print(f"Check: git apply --check {output / 'changes.patch'}")
    print(f"SHA-256: {digest(patch)}")
    return output / "changes.patch"


DENIAL = re.compile(
    r"DENIED (?P<binary>/[A-Za-z0-9_@./+-]+)\(\d+\) -> "
    r"(?P<host>[a-z0-9.-]+):(?P<port>\d+) \[reason:(?P<reason>[a-z0-9_]+)\]"
)


def denials(logs):
    found = {}
    for match in DENIAL.finditer(logs):
        fields = match.groupdict()
        rule = {
            "host": fields["host"],
            "port": int(fields["port"]),
            "binaries": [fields["binary"]],
            "access": "full",
        }
        rule["id"] = "review_" + digest(json.dumps(rule, sort_keys=True).encode())[:12]
        validate_rule(rule)
        found[rule["id"]] = {
            **rule,
            "reason": fields["reason"],
            "method": "unavailable",
            "path": "unavailable",
        }
    return found


def pending(data):
    raw = oshell("logs", NAME, "--source", "sandbox", "-n", "1000").stdout
    proposals = denials(raw)
    path = STATE / "inbox.json"
    inbox = read(path) if path.exists() else {}
    revision = effective(data["policy"])["version"]
    for key, proposal in proposals.items():
        if key not in inbox:
            inbox[key] = {**proposal, "status": "pending", "observed_revision": revision}
    save(path, inbox)
    for key, proposal in inbox.items():
        if proposal["status"] == "pending":
            print(json.dumps(proposal, indent=2))
            print(
                "Risk: full HTTPS access for this executable can send source and credentials "
                "to this host. HTTP method/path were unavailable; this is an endpoint grant."
            )
            print(f"Approve: ./scripts/rules.sh approve {key}")
            print(f"Reject: ./scripts/rules.sh reject {key}")
    print(
        "Compatibility inbox: recent structured TCP denials only; not a native proposal API. "
        "DNS-only/older denials may have no executable and cannot be approved here."
    )


def update_args(rule):
    validate_rule(rule)
    args = [
        "policy",
        "update",
        NAME,
        "--rule-name",
        rule["id"],
        "--add-endpoint",
        f"{rule['host']}:{rule['port']}:{rule['access']}:rest:enforce",
    ]
    for binary in rule["binaries"]:
        args += ["--binary", binary]
    return args


def confirm(text):
    require(sys.stdin.isatty(), "Founder confirmation requires a terminal")
    require(input(f"Type {text} to confirm: ") == text, "Confirmation declined; no change")


def record_change(before, after, action, rule):
    save(
        STATE / "history" / (str(time.time_ns()) + ".json"),
        {
            "action": action,
            "rule": rule,
            "before_revision": before["version"],
            "after_revision": after["version"],
            "before_hash": before["hash"],
            "after_hash": after["hash"],
            "before_policy": before["policy"],
            "after_policy": after["policy"],
        },
    )


def journal(data, before, expected, action, rule):
    require(
        not (STATE / "pending-change.json").exists(),
        "Interrupted policy change exists; run rules.sh recover",
    )
    save(
        STATE / "pending-change.json",
        {
            "sandbox_id": data.get("sandbox_id"),
            "before": before,
            "expected": expected,
            "action": action,
            "rule": rule,
        },
    )


def recover(data):
    pending = read(STATE / "pending-change.json")
    require(pending["sandbox_id"] == data["sandbox_id"], "Recovery sandbox identity mismatch")
    actual = effective()
    actual_base = github_provider.base_policy(sys.modules[__name__], actual["policy"])
    before_base = github_provider.base_policy(sys.modules[__name__], pending["before"]["policy"])
    applied = normalize(actual_base) == normalize(pending["expected"])
    unchanged = normalize(actual_base) == normalize(before_base)
    require(applied or unchanged, "Unknown policy drift; recovery cannot adopt external changes")
    print(
        json.dumps(
            {
                "action": pending["action"],
                "rule": pending["rule"],
                "before_revision": pending["before"]["version"],
                "current_revision": actual["version"],
                "applied_and_loaded": applied,
            },
            indent=2,
        )
    )
    confirm("recover")
    # Recover local review state only; never send a policy replacement.
    if applied:
        data["policy"] = pending["expected"]
        save(STATE / "state.json", data)
        inbox_path = STATE / "inbox.json"
        if inbox_path.exists():
            inbox = read(inbox_path)
            identifier = pending["rule"].get("id", pending["rule"].get("name"))
            if identifier in inbox:
                inbox[identifier]["status"] = (
                    "approved" if pending["action"] == "approve" else "rolled_back"
                )
                save(inbox_path, inbox)
    record_change(pending["before"], actual, "recover", pending["rule"])
    (STATE / "pending-change.json").unlink()
    print("PASS: exact interrupted change reconciled; no native policy changed")


def update(data, rule, confirmation=True):
    before = effective(data["policy"])
    require(rule["id"] not in data["policy"]["network_policies"], "Rule already exists")
    args = update_args(rule)
    # Suppress native dry-run output, which includes the full policy, then display the narrow delta.
    oshell(*args, "--dry-run")
    if confirmation:
        print(json.dumps(rule, indent=2))
        print(f"Before revision: {before['version']}. Adds one exact endpoint/executable rule.")
        print("Risk: HTTPS requests may transmit source or credentials; endpoint-level access.")
        confirm(rule["id"])
    require(effective(data["policy"])["version"] == before["version"], "Concurrent policy change")
    expected = json.loads(json.dumps(data["policy"]))
    expected["network_policies"][rule["id"]] = network_rule(rule)
    journal(data, before, expected, "approve", rule)
    oshell(*args, "--wait")
    after = effective(expected)
    record_change(before, after, "approve", rule)
    data["policy"] = expected
    save(STATE / "state.json", data)
    (STATE / "pending-change.json").unlink()
    print(f"PASS: revision {before['version']} -> {after['version']} Loaded")
    if rule["id"].startswith("review_"):
        print(f"Undo: ./scripts/rules.sh rollback {rule['id']}")


def rules(action, identifier):
    data = state(allow_config_drift=action in {"reconcile", "status", "diagnose"})
    if action == "recover":
        recover(data)
        return
    if action in {"status", "diagnose"}:
        try:
            require(data["config_hash"] == config_hash(), "Reviewed configuration drift")
            effective(data["policy"])
        except ValueError as exc:
            native = api("policy", "get", NAME)
            revisions = api("policy", "list", NAME)
            print(
                json.dumps(
                    {
                        "sandbox": NAME,
                        "verified": False,
                        "reason": str(exc),
                        "version": native["version"],
                        "active_version": native["active_version"],
                        "revisions": revisions["revisions"],
                    },
                    indent=2,
                )
            )
            print(
                "Interrupted wrapper change: rules.sh recover. Reviewed config change: "
                "rules.sh reconcile. Unknown drift needs separate policy review."
            )
            return
    before = effective(data["policy"])
    if action == "reconcile":
        preflight()
        image(check=True)
        verify_tools(data)
        reviewed = policy_for(data["tools"])
        for key, rule in reviewed["network_policies"].items():
            if key in data["policy"]["network_policies"]:
                require(
                    data["policy"]["network_policies"][key] == rule,
                    "Existing default differs; reconcile cannot replace policy",
                )
        print(
            "Accept reviewed phase2.json change; preserves existing effective policy. "
            "Missing reviewed defaults will be installed by setup.sh."
        )
        confirm("reconcile")
        data["config_hash"] = config_hash()
        save(STATE / "state.json", data)
    elif action == "status":
        print(
            json.dumps(
                {
                    "sandbox": NAME,
                    "revision": before["version"],
                    "status": "Loaded",
                    "hash": before["hash"],
                    "policy": before["policy"],
                },
                indent=2,
            )
        )
    elif action in {"pending", "diagnose"}:
        pending(data)
        if action == "diagnose":
            verify_tools(data)
            login_status(data)
            print("For gateway/KVM failures: openshell doctor check; setup.sh --check")
    elif action == "rollback":
        require(
            identifier.startswith("review_") and identifier in data["policy"]["network_policies"],
            "Rollback removes only a compatibility-approved rule; defaults preserved",
        )
        print(json.dumps(data["policy"]["network_policies"][identifier], indent=2))
        oshell("policy", "update", NAME, "--remove-rule", identifier, "--dry-run")
        confirm(identifier)
        require(
            effective(data["policy"])["version"] == before["version"], "Concurrent policy change"
        )
        expected = json.loads(json.dumps(data["policy"]))
        removed = expected["network_policies"].pop(identifier)
        journal(data, before, expected, "rollback", removed)
        oshell("policy", "update", NAME, "--remove-rule", identifier, "--wait")
        after = effective(expected)
        record_change(before, after, "rollback", removed)
        data["policy"] = expected
        save(STATE / "state.json", data)
        (STATE / "pending-change.json").unlink()
        inbox_path = STATE / "inbox.json"
        if inbox_path.exists():
            inbox = read(inbox_path)
            if identifier in inbox:
                inbox[identifier]["status"] = "rolled_back"
                save(inbox_path, inbox)
        print(f"PASS: revision {before['version']} -> {after['version']} Loaded; rule removed")
    elif action in {"approve", "reject", "persist"}:
        inbox = read(STATE / "inbox.json")
        require(identifier in inbox, "Unknown inbox ID; run rules.sh pending")
        proposal = inbox[identifier]
        if action == "persist":
            require(
                proposal["status"] == "approved", "Approve and review a rule before persistence"
            )
            require(len(proposal["binaries"]) == 1, "Persist accepts one exact executable")
            require(
                data["policy"]["network_policies"].get(identifier) == network_rule(proposal),
                "Only the exact currently approved rule can be persisted",
            )
            cfg = settings()
            require(
                not any(row["id"] == identifier for row in cfg["rules"]),
                "Persistent rule already exists",
            )
            entry = {
                "id": identifier,
                "host": proposal["host"],
                "port": proposal["port"],
                "access": proposal["access"],
                "binary": proposal["binaries"][0],
                "review": "Founder-confirmed compatibility rule; review this configuration in PR",
            }
            validate_rule(proposal)
            print(json.dumps(entry, indent=2))
            print(
                "Preview: append this exact approved rule to phase2.json; no native policy change."
            )
            confirm(identifier)
            require(PROFILE is None, "Persist profile rules through a reviewed project config edit")
            cfg["rules"].append(entry)
            save(CONFIG, cfg)
            print(
                "PASS: exact rule saved to phase2.json. Review and commit through a PR. "
                "After merge run rules.sh reconcile, then setup.sh."
            )
            return
        require(proposal["status"] == "pending", "Proposal already resolved")
        if action == "approve":
            # Do not accept fabricated executable names from denial logs.
            require(
                all(
                    b in data["tools"]["binaries"] + ["/usr/bin/curl"] for b in proposal["binaries"]
                ),
                "Unreviewed executable; separate PR required",
            )
            update(data, proposal)
            proposal["status"] = "approved"
        else:
            confirm(identifier)
            proposal["status"] = "rejected"
            record_change(before, before, "reject", proposal)
            print(f"PASS: rejected; revision {before['version']} unchanged; default deny retained")
        save(STATE / "inbox.json", inbox)


def start(action, model=None, base="main", locked=False):
    if action in {"claude", "opencode"}:
        agents.require_live(action)
    data = state()
    if action in {"stop", "resume"}:
        oshell("sandbox", "stop" if action == "stop" else "start", NAME)
        print(f"PASS: {action}; workspace preserved. Run setup.sh --check after resume.")
        return
    effective(data["policy"])
    verify_tools(data)
    require(
        locked or not task_active(),
        "Concurrent active agent task. Wait for it to finish, then retry start.sh codex.",
    )
    if action == "shell":
        github_provider.verify(sys.modules[__name__], data)
        session("launch", argv=["/bin/bash", "--noprofile", "--norc"], base=base, interactive=True)
    elif action == "login":
        guest("prepare_auth")
        print(
            "Open the displayed official login URL in the host browser. Enter the device code. "
            "Do not paste codes or credentials into chat/evidence."
        )
        execute(data["tools"]["launcher"], "login", "--device-auth", interactive=True)
        require(login_status(data), "Login incomplete; run rules.sh diagnose")
    else:
        require(
            action in settings()["agents"] and settings()["agents"][action]["enabled"],
            "Agent adapter not enabled; Claude Code/OpenCode need reviewed pins and policies",
        )
        if not login_status(data):
            start("login", locked=locked)
        github_provider.verify(sys.modules[__name__], data)
        session(
            "launch",
            argv=agents.require_live(action).interactive(data["tools"]["launcher"], model),
            base=base,
            interactive=True,
        )


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=["setup", "start", "run", "github", "refresh", "export", "rules", "cleanup", "migrate"],
    )
    parser.add_argument("action", nargs="?", default="")
    parser.add_argument("id", nargs="?", default="")
    parser.add_argument("--agent", choices=list(agents.AGENTS))
    parser.add_argument("--scope", choices=["pilot", "module", "full"], default="pilot")
    parser.add_argument("--module", action="append", default=[])
    parser.add_argument("--model")
    parser.add_argument("--auth-mode", choices=["api", "subscription"], default="api")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--publish", metavar="FEATURE_BRANCH")
    parser.add_argument("--project", type=Path, default=os.environ.get("MANAGER_PROJECT"))
    parser.add_argument("--host", type=Path, default=os.environ.get("MANAGER_HOST"))
    parser.add_argument("--from-revision")
    parser.add_argument("--base-branch")
    parser.add_argument("--test-path", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        require(args.project is not None, "Example configuration is inert; supply --project or MANAGER_PROJECT")
        require(not any(k.startswith("OPENSHELL_") for k in os.environ),
                "OpenShell environment overrides are not allowed")
        if args.command == "migrate":
            require(args.action == "revision" and args.from_revision is not None,
                    "Use migrate revision --from-revision FULL_OLD_SHA")
            migrate_revision(args.project, args.host, args.from_revision)
            return 0
        install_dependencies = args.command == "setup" and args.action == "apply" and args.agent not in {"claude", "opencode"}
        if args.command == "setup":
            require(args.action in {"apply", "check", "dry-run"}, "Invalid setup action")
        configure(args.project, args.host, install_dependencies=install_dependencies, base_branch=args.base_branch)
        args.base_branch = args.base_branch or PROFILE["base_branch"]
        require(args.base_branch == PROFILE["base_branch"], "Base branch differs from project profile")
        if args.command == "run":
            tests = workflow.test_command(sys.modules[__name__],
                                          workflow.scope_paths(sys.modules[__name__], args.scope, args.module),
                                          args.test_path, args.scope)
        if args.command == "setup" and args.agent in {"claude", "opencode"}:
            require(args.action in {"apply", "check", "dry-run"}, "Invalid setup action")
            workflow.prepare(
                sys.modules[__name__], args.agent, args.action, args.model, args.auth_mode
            )
            return 0
        if args.command == "start" and args.action in {"claude", "opencode"}:
            agents.require_live(args.action)
        if args.command == "run":
            agents.require_live(args.action)
        if args.command == "setup":
            require(args.action in {"apply", "check", "dry-run"}, "Invalid setup action")
            if args.action == "dry-run":
                setup("dry-run")
                return 0
            require(
                not os.environ.get("OPENSHELL_SANDBOX"),
                "This is a MicroVM session. Run the command from the Linux Lite host terminal.",
            )
            require(
                not any(k.startswith("OPENSHELL_") for k in os.environ),
                "OpenShell environment overrides are not allowed",
            )
            setup_report(args.action)
            return 0
        require(
            not any(k.startswith("OPENSHELL_") for k in os.environ),
            "OpenShell environment overrides are not allowed",
        )
        require(shutil.which("openshell"), "Missing dependency: openshell. Install CLI 0.1.2 first")
        require(
            oshell("--version").stdout.strip() == settings()["cli_version"], "CLI version drift"
        )
        safe_dir(STATE)
        require(not (STATE / "operation.lock").is_symlink(), "Symlink operation lock")
        with (STATE / "operation.lock").open("a") as lock, idle_lease(args.command in {"refresh", "cleanup"} or (args.command == "start" and args.action in {"stop", "resume"})):
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.command == "start":
                require(
                    args.scope == "pilot" and not args.module,
                    "Use --autonomous for bounded scopes; interactive startup uses the configured workspace.",
                )
                if args.action not in {"stop", "resume"}:
                    # A long session must not block Founder approval/refresh in another terminal.
                    fcntl.flock(lock, fcntl.LOCK_UN)
                require(
                    not args.publish and not args.prompt_file,
                    "Publication and prompt files require --autonomous",
                )
                if args.action in {'stop', 'resume'}:
                    require(not task_active(), 'Active session/task; lifecycle change refused')
                    start(args.action, args.model, args.base_branch)
                else:
                    require(not (STATE / 'task.lock').is_symlink(), 'Symlink task lock')
                    with (STATE / 'task.lock').open('a') as task_lock:
                        fcntl.flock(task_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        start(args.action, args.model, args.base_branch, locked=True)
            elif args.command == "run":
                require(args.prompt_file is not None, "run requires --prompt-file")
                require(args.prompt_file.stat().st_size <= 32768, "Prompt file too large")
                result = workflow.run(
                    sys.modules[__name__],
                    args.action,
                    args.prompt_file.read_text(),
                    args.scope,
                    args.module,
                    args.model,
                    args.attempts,
                    args.publish,
                    tests,
                    release_lock=lambda: fcntl.flock(lock, fcntl.LOCK_UN),
                    base=args.base_branch,
                )
                print(json.dumps(result, indent=2))
            elif args.command == "github":
                require(
                    args.action in {"setup", "check", "credential"},
                    "Use github.sh setup|check|credential",
                )
                preflight()
                if args.action == "credential":
                    github_provider.credentials(sys.modules[__name__])
                    return 0
                data = state(allow_config_drift=args.action == "setup")
                github_provider.reconcile(
                    sys.modules[__name__], data, "apply" if args.action == "setup" else "check"
                )
                data["config_hash"] = config_hash()
                if args.action == "setup":
                    save(STATE / "state.json", data)
            elif args.command == "rules":
                require(
                    args.action
                    in {
                        "status",
                        "pending",
                        "approve",
                        "reject",
                        "rollback",
                        "persist",
                        "diagnose",
                        "reconcile",
                        "recover",
                    },
                    "Unknown rules action",
                )
                rules(args.action, args.id)
            elif args.command == "cleanup":
                require(not task_active(), "Active session/task; cleanup refused")
                data = state()
                print(
                    "Export work first. Deletes only the configured project sandbox and its guest credentials/data."
                )
                confirm(NAME)
                oshell("sandbox", "delete", NAME)
                (STATE / "state.json").rename(STATE / f"retired-{uuid.uuid4().hex}.json")
                print("PASS: named development VM removed; local exports/history preserved")
            else:
                data = state()
                effective(data["policy"])
                if args.command == "refresh":
                    require(not task_active(), "Active session/task; refresh refused")
                    names = workflow.scope_paths(sys.modules[__name__], args.scope, args.module)
                    refresh(data, names)
                    from . import configuration
                    data["writable"] = [p for p in names if p in configuration.paths(EDIT_SCOPE)]
                    save(STATE / "state.json", data)
                else:
                    export_patch(data)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        # Only controlled errors are exposed; never echo guest/API payloads.
        message = (
            str(exc)
            if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError)
            else type(exc).__name__
        )
        print(f"FAIL: {message}", file=sys.stderr)
        if args.command == "setup":
            print("FAIL | Setup readiness | " + message)
            print("SKIP | Dependent checks | Resolve the failure above, then retry setup.sh.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
