"""Trusted guest session: Provider-only Git authentication and conservative checkout sync.

Executed as source by the host wrapper inside the MicroVM. No host credentials,
imports from the checkout, persistent credential helper, or downloaded code.
"""

import base64
import fcntl
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

REMOTE = "https://github.com/example/project.git"
SSH_REMOTES = {
    "git@github.com:example/project.git",
    "ssh://git@github.com/example/project.git",
}
ROOT = Path("/sandbox/project/checkout")
LOCK = Path("/sandbox/.manager-source.lock")
AUTH_FIX = "Run ./scripts/github.sh credential in a private host terminal, then setup.sh."
CREDENTIAL_SCRIPT = """import os,sys
if sys.argv[-1] != 'get': sys.exit(0)
fields = dict(line.rstrip('\\n').split('=',1) for line in sys.stdin if '=' in line)
if (fields.get('protocol') != 'https' or fields.get('host') != 'github.com'
    or fields.get('path') not in {'example/project.git',
                                 'example/project'}): sys.exit(0)
token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
if not token: sys.exit(0)
sys.stdout.write('username=x-access-token\\npassword='+token+'\\n\\n')
"""


def environment(source=None):
    env = dict(os.environ if source is None else source)
    token = env.get("GITHUB_TOKEN") or env.get("GH_TOKEN")
    if not token:
        raise ValueError("GitHub Provider credential missing. " + AUTH_FIX)
    # A stateless helper passes the native Provider placeholder through a private
    # pipe. Git constructs the same Basic header already accepted by OpenShell.
    # Config inspection exposes only helper code, never the credential or header.
    # OpenShell supplies its proxy trust bundle through GIT_SSL_CAINFO. Keep
    # that path while removing inherited Git config, tracing and TLS bypasses.
    for key in list(env):
        if key.startswith(("GIT_", "GCM_")) and key != "GIT_SSL_CAINFO":
            del env[key]
    values = [
        ("credential.helper", ""),
        (
            "credential." + REMOTE + ".helper",
            "!/usr/bin/python3 -I -c " + shlex.quote(CREDENTIAL_SCRIPT),
        ),
        ("http.followRedirects", "false"),
        ("credential.useHttpPath", "true"),
        ("core.hooksPath", "/dev/null"),
    ]
    env["GIT_CONFIG_COUNT"] = str(len(values))
    for i, (key, value) in enumerate(values):
        env[f"GIT_CONFIG_KEY_{i}"] = key
        env[f"GIT_CONFIG_VALUE_{i}"] = value
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GH_TOKEN"] = token
    env["HISTFILE"] = "/dev/null"
    env["PATH"] = "/sandbox/tools:" + env.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    return env


def redact(text, env):
    for key in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = env.get(key)
        if value:
            text = text.replace(value, "[redacted]")
            encoded = base64.b64encode(("x-access-token:" + value).encode()).decode()
            text = text.replace(encoded, "[redacted]")
    return text


def git(args, root=ROOT, env=None, check=True):
    env = environment() if env is None else env
    result = subprocess.run(
        ["/usr/bin/git", *args],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        timeout=300,
        check=False,
    )
    if result.returncode and check:
        # Never return raw transport output: remote responses can echo credentials.
        if any(
            word in (result.stdout + result.stderr).lower()
            for word in ("401", "403", "authentication", "username", "permission", "credential")
        ):
            raise ValueError(
                "GitHub credential expired or lacks repository permission. " + AUTH_FIX
            )
        raise ValueError("Git operation failed; work preserved. Run setup.sh --check.")
    result.stdout = redact(result.stdout, env)
    result.stderr = "" if result.returncode else redact(result.stderr, env)
    return result


@contextmanager
def lease(path=None, shared=False):
    path = LOCK if path is None else path
    if path.is_symlink():
        raise ValueError("Unsafe source lock path")
    with path.open("a") as lock:
        try:
            fcntl.flock(lock, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError(
                "Concurrent active agent or Git sync. Exit that session; retry."
            ) from None
        yield


def unmanaged_agent():
    """Fail conservatively for sessions started outside this wrapper (including the daemon)."""
    for item in Path("/proc").iterdir():
        if not item.name.isdigit() or int(item.name) == os.getpid():
            continue
        try:
            argv = (item / "cmdline").read_bytes().split(b"\0")
            if any(
                Path(arg.decode(errors="ignore")).name in {"codex", "codex.js", "claude", "opencode"}
                for arg in argv[:2]
            ):
                return True
        except (FileNotFoundError, ProcessLookupError):
            # A process may exit between listing /proc and reading cmdline.
            continue
        except (OSError, ValueError):
            # Unreadable process metadata cannot establish an idle guest.
            return True
    return False


def synchronize(root=ROOT, base="main", check=False, run=git, active=None):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_/-]{0,100}", base) or ".." in base or "//" in base or base.endswith("/"):
        raise ValueError("Invalid configured base branch.")
    if active is None:
        active = unmanaged_agent()
    if active:
        return {"status": "SKIP", "state": "active-agent", "fix": "Exit the active agent; retry."}
    if root.resolve() != root.absolute():
        raise ValueError("Checkout path contains a symlink; work preserved.")
    if not root.exists():
        if check:
            raise ValueError("Project checkout missing. Run setup.sh.")
        # Clone into a private sibling and publish only after success. Never adopt
        # an interrupted, partially populated destination or overwrite existing work.
        with tempfile.TemporaryDirectory(prefix=".manager-clone-", dir=root.parent) as temporary:
            clone = Path(temporary) / "checkout"
            run(["clone", "--branch", base, "--", REMOTE, str(clone)], root=root.parent)
            if root.exists():
                raise ValueError("Checkout appeared during clone; refusing replacement.")
            clone.rename(root)
        return {"status": "PASS", "state": "cloned", "branch": base}
    if not (root / ".git").is_dir() or (root / ".git").is_symlink():
        raise ValueError("/sandbox/project/checkout is not a standalone Git checkout; work preserved.")

    def read(*args):
        return run(list(args), root=root).stdout.strip()

    if Path(read("rev-parse", "--show-toplevel")) != root:
        raise ValueError("Unexpected repository identity; work preserved.")
    urls = read("config", "--get-all", "remote.origin.url").splitlines()
    push = run(["config", "--get-all", "remote.origin.pushurl"], root=root, check=False)
    pushes = push.stdout.strip().splitlines() if push.returncode == 0 else []
    allowed = {REMOTE, REMOTE.removesuffix(".git"), *SSH_REMOTES}
    if len(urls) != 1 or urls[0] not in allowed or any(u not in allowed for u in pushes):
        raise ValueError("Unsupported Project origin or push URL. Set only Project origin to " + REMOTE)
    if read("status", "--porcelain"):
        return {"status": "SKIP", "state": "dirty", "fix": "Commit or stash when ready; retry."}
    if check and (urls != [REMOTE] or any(u != REMOTE for u in pushes)):
        raise ValueError("Project origin needs HTTPS migration. Run setup.sh; SSH is unnecessary.")
    if not check:
        if urls != [REMOTE]:
            run(["remote", "set-url", "origin", REMOTE], root=root)
        if pushes and pushes != [REMOTE]:
            run(["config", "--replace-all", "remote.origin.pushurl", REMOTE], root=root)
    # Probe only after confirming the checkout is clean and inactive.
    run(["ls-remote", "--exit-code", "origin", "refs/heads/" + base], root=root)
    branch = read("branch", "--show-current")
    if branch != base:
        return {
            "status": "SKIP",
            "state": "feature-or-detached",
            "branch": branch,
            "fix": "Continue existing work; switch to the base branch when ready.",
        }
    if check:
        return {"status": "PASS", "state": "clean", "branch": branch}
    run(
        ["fetch", "--no-tags", "origin", f"refs/heads/{base}:refs/remotes/origin/{base}"],
        root=root,
    )
    counts = read("rev-list", "--left-right", "--count", "HEAD...refs/remotes/origin/" + base)
    ahead, behind = map(int, counts.split())
    if ahead:
        return {
            "status": "SKIP",
            "state": "diverged" if behind else "ahead",
            "fix": "Review local commits; resolve history explicitly. No automatic merge.",
        }
    if behind:
        run(["merge", "--ff-only", "refs/remotes/origin/" + base], root=root)
    return {"status": "PASS", "state": "fast-forwarded" if behind else "current", "branch": branch}


def main():
    global REMOTE, SSH_REMOTES, ROOT, LOCK, CREDENTIAL_SCRIPT
    request = json.loads(base64.b64decode(sys.argv[1]))
    if 'repository' in request:
        repo, project = request['repository'], request['project_id']
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9_.-]*', repo) or not re.fullmatch(r'[a-z][a-z0-9-]{1,30}', project):
            raise ValueError('Invalid host project identity')
        if request['workspace'] != '/sandbox/' + project + '/checkout':
            raise ValueError('Invalid host workspace')
        CREDENTIAL_SCRIPT = CREDENTIAL_SCRIPT.replace('example/project', repo)
        REMOTE = 'https://github.com/' + repo + '.git'
        SSH_REMOTES = {'git@github.com:' + repo + '.git', 'ssh://git@github.com/' + repo + '.git'}
        ROOT = Path(request['workspace'])
        LOCK = Path('/sandbox/' + project + '/source.lock')
        LOCK.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if LOCK.parent.resolve() != LOCK.parent:
            raise ValueError('Symlink project workspace')
    env = environment()
    action = request["action"]
    if action == "git":
        result = git(request["argv"], root=Path(request["root"]), env=env, check=False)
        sys.stdout.write(result.stdout)
        sys.exit(result.returncode)
    if action == "task":
        with lease(shared=True):
            sys.exit(subprocess.call(request["argv"], cwd=request["root"], env=env))
    with lease():
        result = synchronize(
            root=ROOT,
            base=request.get("base", "main"),
            check=request.get("read_only", False),
        )
        if action == "sync":
            print(json.dumps(result))
            return
        if result["state"] == "active-agent":
            raise ValueError("Concurrent active agent; exit that session and retry. Work retained.")
        print("Repository: " + result["state"] + " in " + str(ROOT), flush=True)
        if result.get("fix"):
            print(result["fix"], flush=True)
        # argv is fixed by the host, never loaded from downloaded repository code.
        sys.exit(subprocess.call(request["argv"], cwd=ROOT, env=env))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(
            str(exc)
            if isinstance(exc, ValueError)
            else "Guest session failed; work preserved. Run setup.sh --check.",
            file=sys.stderr,
        )
        sys.exit(1)
