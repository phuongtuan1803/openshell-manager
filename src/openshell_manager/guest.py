"""Fixed guest operations. The host executes this source, never downloaded code."""

import base64
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path


def regular(path):
    if path.resolve() != path.absolute() or not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("Non-regular file or symlink escape")


def inspect_tools():
    launcher = shutil.which("codex")
    if not launcher:
        raise ValueError("Codex launcher missing")
    version = subprocess.check_output([launcher, "--version"], text=True).strip()
    # Discover the npm platform binary rather than granting network to node/the launcher.
    package = Path(launcher).resolve().parent.parent
    candidates = list(package.glob("node_modules/@openai/codex-*/vendor/*/bin/codex"))
    if len(candidates) != 1:
        raise ValueError("Ambiguous Codex native executable; inspect image")
    native = candidates[0]
    regular(native)
    if subprocess.check_output([str(native), "--version"], text=True).strip() != version:
        raise ValueError("Codex executable versions differ")
    triple = native.parent.parent.name
    release = version.split()[-1]
    daemon = Path("/sandbox/.codex/packages/app-server-daemon/releases")
    daemon = daemon / f"{release}-{triple}" / "bin/codex"
    if daemon.exists():
        regular(daemon)
        if (
            hashlib.sha256(daemon.read_bytes()).digest()
            != hashlib.sha256(native.read_bytes()).digest()
        ):
            raise ValueError("Daemon executable differs from pinned native binary")
    # Codex can unpack this exact version later. Reserve only the version-derived path.
    return {
        "version": version,
        "launcher": launcher,
        "binaries": [str(native), str(daemon)],
        "native_sha256": hashlib.sha256(native.read_bytes()).hexdigest(),
        "daemon_present": daemon.exists(),
        "uid": os.getuid(),
        "git": subprocess.check_output(["git", "--version"], text=True).strip(),
        "python": sys.version.split()[0],
        "pytest": subprocess.check_output(
            ["/usr/bin/python3", "-I", "-m", "pytest", "--version"], text=True
        ).strip(),
    }


def collect(root, manifest):
    if root.resolve() != root.absolute() or not root.is_dir():
        raise ValueError("Missing workspace or symlink workspace")
    files = {}
    for name in manifest:
        path = root / name
        if not path.exists() and not path.is_symlink():
            files[name] = None
            continue
        regular(path)
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("File exceeds 1 MiB transfer limit")
        files[name] = base64.b64encode(path.read_bytes()).decode()
    # Reject unexpected source files; allow only known test/Git cache directories.
    for parent, dirs, names in os.walk(root, followlinks=False):
        for directory in list(dirs):
            path = Path(parent) / directory
            if path.is_symlink():
                raise ValueError("Symlink directory rejected")
            if directory in {".git", "__pycache__", ".pytest_cache"}:
                dirs.remove(directory)
        for name in names:
            rel = (Path(parent) / name).relative_to(root).as_posix()
            if rel not in manifest and rel != "snapshot-provenance.json":
                raise ValueError("Unexpected file outside manifest")
    return files


def prepare_auth(home):
    if not home.exists():
        home.mkdir(mode=0o700)
    if home.resolve() != home.absolute() or not home.is_dir():
        raise ValueError("Invalid guest authentication directory")
    return {"auth_directory_ready": True}


def main():
    request = json.loads(base64.b64decode(sys.argv[1]))
    action = request["action"]
    if action == "tools":
        result = inspect_tools()
    elif action == "prepare_auth":
        result = prepare_auth(Path("/sandbox/.codex"))
    else:
        root = Path(request["root"])
        regular(root / "snapshot-provenance.json")
        provenance = json.loads((root / "snapshot-provenance.json").read_text())
        if provenance != request["provenance"]:
            raise ValueError("Snapshot provenance changed")
        files = collect(root, provenance["manifest"])
        if action == "initialize":
            for entry in provenance["files"]:
                value = files[entry["path"]]
                if (
                    value is None
                    or hashlib.sha256(base64.b64decode(value)).hexdigest() != entry["sha256"]
                ):
                    raise ValueError("Uploaded content checksum mismatch")
            if (root / ".git").exists():
                raise ValueError("Refusing to replace guest Git data")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "add", "--", *files], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-c",
                    "user.name=Snapshot",
                    "-c",
                    "user.email=snapshot@example.invalid",
                    "commit",
                    "-qm",
                    "Approved source snapshot",
                ],
                check=True,
            )
        result = {"provenance": provenance, "files": files}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
