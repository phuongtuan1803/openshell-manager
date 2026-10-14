"""Native provider reconciliation. Captured CLI output and credentials are never logged."""

import base64
import getpass
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

PROVIDER = "example-github"
REPOSITORY = "example/project"
RULE = "_provider_example_github"
GH = "/sandbox/tools/gh"
GH_VERSION = "2.101.0"
GH_SHA256 = "ea857a3f0f7d4276cf5848b236542c5048e2eaa7bdd1b6ddec238f8793e74bff"
PERMISSIONS = (
    "GitHub credential cannot perform this operation. Grant the existing credential "
    "Contents: read/write and Pull requests: read/write for "
    + REPOSITORY
    + " only. If replacement is necessary, run ./infra/dev-sandbox/github.sh credential "
    "in a private host terminal, then rerun setup.sh. No credential was replaced."
)


def profile(control):
    import yaml

    return yaml.safe_load(getattr(control, "PROVIDER_PROFILE", control.HERE / "policies/github.yaml").read_text())


def lint(control, path):
    result = control.oshell("provider", "profile", "lint", "-f", str(path), check=False)
    if result.returncode == 0:
        return
    control.require(
        "already exists" in result.stdout + result.stderr,
        "Canonical GitHub profile failed native validation",
    )
    # 0.1.2 lint also checks registry uniqueness. Validate identical content under
    # a temporary, unregistered ID; import/update still reads only the canonical file.
    with tempfile.TemporaryDirectory(prefix="manager-profile-lint-") as temporary:
        candidate = Path(temporary) / "github.yaml"
        content = path.read_text()
        control.require(("\n" + content).count("\nid: " + PROVIDER + "\n") == 1, "Canonical profile ID drift")
        candidate.write_text(
            ("\n" + content).replace("\nid: " + PROVIDER + "\n", "\nid: manager-lint-" + uuid.uuid4().hex + "\n")
        )
        control.oshell("provider", "profile", "lint", "-f", str(candidate))


def comparable(value):
    # Native JSON contains default values and registry metadata absent from YAML.
    result = {
        k: value[k]
        for k in (
            "id",
            "display_name",
            "description",
            "category",
            "credentials",
            "discovery",
            "endpoints",
            "binaries",
        )
    }
    result = json.loads(json.dumps(result))
    for credential in result["credentials"]:
        if credential.get("query_param") == "":
            credential.pop("query_param")
    result["binaries"] = sorted(result["binaries"])
    return result


def injected_rule(value):
    return {
        "name": RULE,
        "binaries": [{"path": p} for p in value["binaries"]],
        "endpoints": value["endpoints"],
    }


def base_policy(control, policy, imported=None):
    result = json.loads(json.dumps(policy))
    rules = result.setdefault("network_policies", {})
    injected = [k for k in rules if k.startswith("_provider_")]
    control.require(not set(injected) - {RULE}, "Unexpected provider policy; no adoption")
    if RULE in rules:
        if imported is not False:
            wanted = injected_rule(imported or profile(control))
            control.require(
                control.normalize({"network_policies": {RULE: rules[RULE]}})
                == control.normalize({"network_policies": {RULE: wanted}}),
                "Native GitHub policy differs from the canonical profile; run setup.sh",
            )
        rules.pop(RULE)
    return result


def receipt(control, info, result, policy_hash):
    targets = result.get("targets", [])
    control.require(len(targets) == 1, "Provider activation returned no unique target")
    target = targets[0]
    desired = target.get("receipt", {}).get("desired", {})
    observed = target.get("observed", {})
    control.require(
        target.get("state") == "ready"
        and target.get("wait_outcome") == "complete"
        and target.get("receipt", {}).get("provider") == PROVIDER
        and desired.get("sandbox") == control.NAME
        and desired.get("sandbox_id") == info["id"]
        and desired.get("policy_hash") == observed.get("policy_hash") == policy_hash
        and desired.get("config_revision") == observed.get("config_revision")
        and desired.get("provider_env_revision") == observed.get("provider_env_revision")
        and desired.get("attachment_epoch") == observed.get("attachment_epoch")
        and all(
            observed.get(k) is True
            for k in ("credentials_installed", "launch_environment_installed", "policy_active")
        ),
        "Provider reconciliation did not activate credentials, environment and exact policy",
    )
    return {
        "provider": PROVIDER,
        "ready": True,
        "policy_hash": policy_hash,
        "provider_resource_version": desired["provider_resource_version"],
    }


def status(control, info, policy_hash):
    result = control.api(
        "sandbox", "provider", "status", control.NAME, PROVIDER, "--wait", "--timeout", "30"
    )
    return receipt(control, info, result, policy_hash)


def tool(control, check=False):
    code = """import pathlib,hashlib,json
p=pathlib.Path('/sandbox/tools/gh')
print(json.dumps({'sha256':hashlib.sha256(p.read_bytes()).hexdigest()
 if p.is_file() and p.resolve()==p.absolute() else None}))
"""
    actual = json.loads(control.execute("/usr/bin/python3", "-I", "-c", code).stdout)
    if actual["sha256"] != GH_SHA256:
        control.require(not check, "Pinned guest gh missing or changed; run setup.sh")
        binary = shutil.which("gh")
        if binary is None or hashlib.sha256(Path(binary).read_bytes()).hexdigest() != GH_SHA256:
            import bootstrap

            binary = str(bootstrap.gh_binary(control))
        content = Path(binary).read_bytes()
        control.require(
            hashlib.sha256(content).hexdigest() == GH_SHA256,
            "Host gh differs from pinned " + GH_VERSION + "; no binary transferred",
        )
        local = control.STATE / "tools"
        control.safe_dir(local)
        copied = local / "gh"
        control.require(not copied.is_symlink(), "Symlink in local gh tool path")
        copied.write_bytes(content)
        copied.chmod(0o755)
        control.execute("/bin/mkdir", "-p", "/sandbox/tools")
        # Copy the executable only. Never copy gh config, Git config, SSH or auth files.
        control.oshell("sandbox", "upload", control.NAME, str(copied), GH)
        actual = json.loads(control.execute("/usr/bin/python3", "-I", "-c", code).stdout)
        control.require(actual["sha256"] == GH_SHA256, "Guest gh checksum verification failed")
    version = control.execute(GH, "--version").stdout.splitlines()[0]
    control.require(version.startswith("gh version " + GH_VERSION + " "), "Guest gh version drift")


def credential_presence(control):
    result = control.execute(
        "/usr/bin/python3",
        "-I",
        "-c",
        "import os,json; print(json.dumps({'present':bool(os.environ.get('GITHUB_TOKEN') "
        "or os.environ.get('GH_TOKEN'))}))",
    )
    control.require(
        json.loads(result.stdout).get("present") is True,
        "Provider credential missing in guest; rerun setup.sh; never copy host auth files",
    )


def permissions(control):
    credential_presence(control)
    result = api(control, f"repos/{REPOSITORY}", check=False)
    if result.returncode and ("HTTP 401" in result.stderr or "Bad credentials" in result.stdout):
        raise ValueError(
            "GitHub rejected the existing credential (HTTP 401). Run "
            "./infra/dev-sandbox/github.sh credential to explicitly refresh it, "
            "then rerun setup.sh. No credential was replaced."
        )
    control.require(result.returncode == 0, PERMISSIONS)
    data = json.loads(result.stdout)
    control.require(data.get("full_name") == REPOSITORY, "GitHub repository response mismatch")
    control.require(data.get("permissions", {}).get("push") is True, PERMISSIONS)
    # Account push permission is not proof of PAT Contents/PR write scopes.
    # Actual Git push/PR writes remain authoritative and fail without replacing credentials.
    return {
        "repository": REPOSITORY,
        "account_push_permission": True,
        "token_write_permissions": "not proven until Git/PR writes succeed",
    }


def credentials(control):
    """Explicit Founder action only; setup/reconciliation never calls this."""
    control.require(sys.stdin.isatty(), "Credential entry requires a private host terminal")
    providers = control.api("provider", "list")["providers"]
    existing = next((p for p in providers if p["name"] == PROVIDER), None)
    control.require(
        existing is None or existing["type"] == PROVIDER,
        "Existing example-github type mismatch; credentials preserved",
    )
    key = (
        "GH_TOKEN"
        if existing and existing.get("credential_keys") == ["GH_TOKEN"]
        else "GITHUB_TOKEN"
    )
    token = getpass.getpass("Repository-scoped GitHub token (hidden input): ")
    control.require(
        token and "\n" not in token and "\r" not in token,
        "Empty or invalid credential; existing credentials preserved",
    )
    # Lookup-by-name keeps credential values out of argv and all captured output.
    env = os.environ.copy()
    env[key] = token
    args = ["openshell", "provider"]
    args += (
        ["update", PROVIDER, "--wait"]
        if existing
        else ["create", "--name", PROVIDER, "--type", PROVIDER]
    )
    args += ["--credential", key]

    result = subprocess.run(args, env=env, capture_output=True, text=True, check=False, timeout=60)
    control.require(
        result.returncode == 0, "Native credential update failed; retry in the private terminal"
    )
    print("PASS: credential supplied to the native gateway; run setup.sh to verify permissions")


def api(control, *args, workdir="/sandbox", check=True):
    # Select the same injected credential as Git; gh otherwise prefers a GH_TOKEN alias.
    # Credential selection happens in the guest environment, never command arguments.
    # Host arguments and stored command/evidence records never contain token values.
    payload = base64.b64encode(json.dumps(list(args)).encode()).decode()
    code = """import os,subprocess,sys,json,base64
args=json.loads(base64.b64decode(sys.argv[1]))
token=os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
if not token: sys.exit(78)
env=os.environ.copy()
env['GH_TOKEN']=token
r=subprocess.run(['/sandbox/tools/gh','api',*args],capture_output=True,text=True,env=env)
sys.stdout.write(r.stdout.replace(token,'[redacted]'))
sys.stderr.write(r.stderr.replace(token,'[redacted]'))
sys.exit(r.returncode)
"""
    result = control.execute(
        "/usr/bin/python3", "-I", "-c", code, payload, workdir=workdir, check=False
    )
    if check and result.returncode:
        raise ValueError(PERMISSIONS)
    return result


def verify(control, data):
    control.require(
        control.oshell("--version").stdout.strip() == "openshell 0.1.2",
        "Unsupported OpenShell version",
    )
    tool(control, check=True)
    loaded = control.effective(data["policy"])
    control.require(RULE in loaded["policy"]["network_policies"], "GitHub provider not attached")
    result = status(control, control.api("sandbox", "get", control.NAME), loaded["hash"])
    result.update(permissions(control))
    return result


def reconcile(control, data, mode="apply"):
    path = getattr(control, "PROVIDER_PROFILE", control.HERE / "policies/github.yaml")
    lint(control, path)
    wanted = profile(control)
    profiles = control.api("provider", "profile", "list")
    installed = next((p for p in profiles if p["id"] == PROVIDER), None)
    scope = ["--global"] if installed and installed.get("scope") == "platform" else []
    imported = (
        control.api("provider", "profile", "describe", PROVIDER, *scope) if installed else None
    )
    # Validate the already attached composition against the installed old profile before updating.
    control.effective(data["policy"], github_profile=False)
    changed = imported is None or comparable(imported) != comparable(wanted)
    if changed:
        control.require(mode != "check", "Canonical GitHub profile changed; run setup.sh")
        if installed:
            # Registry revision is concurrency metadata, not a second policy source.
            with tempfile.TemporaryDirectory(prefix="manager-profile-update-") as temporary:
                candidate = Path(temporary) / "github.yaml"
                control.require(
                    type(imported.get("resource_version")) is int
                    and imported["resource_version"] > 0,
                    "Native profile revision missing",
                )
                candidate.write_text(
                    path.read_text()
                    + "\nresource_version: "
                    + str(imported["resource_version"])
                    + "\n"
                )
                control.oshell(
                    "provider", "profile", "update", PROVIDER, "-f", str(candidate), *scope
                )
        else:
            control.oshell("provider", "profile", "import", "-f", str(path), *scope)
    imported = control.api("provider", "profile", "describe", PROVIDER, *scope)
    control.require(
        comparable(imported) == comparable(wanted), "Native profile reconciliation failed"
    )
    providers = control.api("provider", "list")
    control.require(not providers.get("next_page_token"), "Provider list incomplete; no mutation")
    existing = next((p for p in providers["providers"] if p["name"] == PROVIDER), None)
    if existing:
        control.require(
            existing["type"] == PROVIDER, "Existing example-github type mismatch; preserved"
        )
        control.require(
            set(existing.get("credential_keys", [])) & {"GH_TOKEN", "GITHUB_TOKEN"},
            "Existing provider lacks a GitHub credential. " + PERMISSIONS,
        )
    else:
        control.require(mode != "check", "GitHub provider missing; run setup.sh")
        control.require(
            bool(os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")),
            "GitHub credential missing. Supply a repository-scoped GITHUB_TOKEN in "
            "the protected host environment and rerun setup.sh. Values are never logged.",
        )
        control.oshell(
            "provider", "create", "--name", PROVIDER, "--type", PROVIDER, "--from-existing"
        )
    tool(control, check=mode == "check")
    attachments = control.api("sandbox", "provider", "list", control.NAME)["providers"]
    attached = any(p["name"] == PROVIDER for p in attachments)
    compiled = control.api("policy", "get", control.NAME, "--full")
    current_rule = compiled["policy"]["network_policies"].get(RULE)
    stale = current_rule is None or control.normalize(
        {"network_policies": {RULE: current_rule}}
    ) != control.normalize({"network_policies": {RULE: injected_rule(wanted)}})
    if not attached:
        control.require(mode != "check", "GitHub provider not attached; run setup.sh")
        control.oshell(
            "sandbox", "provider", "attach", control.NAME, PROVIDER, "--wait", "--timeout", "30"
        )
    elif changed or stale:
        control.require(mode != "check", "Provider policy is stale; run setup.sh")
        # OpenShell 0.1.2 profile updates need a provider mutation to refresh attachments.
        # Only a non-secret config stamp changes; credential values remain untouched.
        control.oshell(
            "provider",
            "update",
            PROVIDER,
            "--config",
            "manager_profile_sha256=" + hashlib.sha256(path.read_bytes()).hexdigest(),
            "--wait",
            "--timeout",
            "30",
        )
    result = verify(control, data)
    result["profile_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    result["credentials_reused"] = existing is not None
    if mode == "apply":
        control.save(control.STATE / "github-ready.json", result)
    print("PASS: native example-github reconciled; exact policy active; credentials preserved")
    return result


def git(control, *args, workdir="/sandbox", check=True):
    # One implementation for publication and interactive sessions.
    result = control.session("git", argv=list(args), root=workdir, check=False, timeout=300)
    if check and result.returncode:
        raise ValueError(PERMISSIONS)
    return result
