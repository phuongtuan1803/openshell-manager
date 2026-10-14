"""Export explicitly reviewed Git blobs without host Git metadata or symlinks."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath

# Deliberately conservative. No bypass flag: suspicious files must be redacted in
# a reviewed commit before transfer. Never include the matched value in errors.
CONTENT_RULES = {
    "private-key": rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----",
    "openai-token": rb"\bsk-(?!ant-)(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}",
    "anthropic-token": rb"\bsk-ant-[A-Za-z0-9_-]{20,}",
    "github-token": rb"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})",
    "aws-access-key": rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
    "google-api-key": rb"\bAIza[A-Za-z0-9_-]{30,}",
    "slack-token": rb"\bxox[baprs]-[A-Za-z0-9-]{20,}",
    "credential-url": rb"(?i)\b(?:https?|postgres(?:ql)?|redis|amqps?)://[^\s/:]+:[^\s/@]+@",
    "jwt": rb"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
    "quoted-secret-assignment": (
        rb"(?im)[\"']?\b(?:[a-z0-9]+_)*(?:api[_-]?key|(?:access[_-]?|auth[_-]?)?token|"
        rb"secret(?:_access_key)?|password|passwd|private[_-]?key)[\"']?"
        rb"\s*[:=]\s*(?:\"[^\"\r\n]+\"|'[^'\r\n]+')"
    ),
    "secret-assignment": (
        rb"(?im)(?:[\"']?\b(?:[a-z0-9]+_)*(?:api[_-]?key|access[_-]?token|"
        rb"auth[_-]?token|secret(?:_access_key)?|password|passwd|private[_-]?key)[\"']?)"
        rb"\s*[:=]\s*[\"']?([A-Za-z0-9_+/.=-]{8,})(?=$|[\s,}])"
    ),
}


def validate_content(name: str, content: bytes) -> None:
    for rule, pattern in CONTENT_RULES.items():
        if re.search(pattern, content):
            raise ValueError(f"Suspicious credential content ({rule}) in {name}; export refused")


def validate_path(name: str) -> None:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or str(path) != name:
        raise ValueError("Manifest entries must be exact repository-relative file paths")
    if name == "snapshot-provenance.json":
        raise ValueError("Reserved snapshot metadata filename")
    forbidden = {
        ".git",
        ".ssh",
        ".aws",
        ".codex",
        ".opencode",
        ".local",
        ".env",
        "secrets",
        "credentials",
        ".gnupg",
        ".kube",
        ".docker",
        ".npmrc",
        ".netrc",
        ".pgpass",
        ".git-credentials",
        ".bash_history",
        ".zsh_history",
        ".pypirc",
        ".claude.json",
        "auth.json",
        "id_rsa",
        "id_ed25519",
        "id_ecdsa",
    }
    if any(
        part.lower() in forbidden
        or part.lower().startswith(".env.")
        or part.lower().endswith((".pem", ".key", ".p12", ".pfx"))
        for part in path.parts
    ):
        raise ValueError(f"Credential or private path rejected: {name}")
    if tuple(part.lower() for part in path.parts[:2]) in {
        ("docs", "html"),
        ("docs", "pdf"),
        ("docs", "md"),
    }:
        raise ValueError("Generated document outputs are not source transfer inputs")


def git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(repo), *args])


def export(repo: Path, revision: str, names: list[str], output: Path) -> dict:
    if not names or len(names) != len(set(names)):
        raise ValueError("Manifest must contain a nonempty unique list of files")
    if output.resolve() != output.absolute():
        raise ValueError("Symlink in snapshot output path; export refused")
    commit = git(repo, "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}")
    commit = commit.decode().strip()
    blobs = {}
    for name in names:
        validate_path(name)
        ignored = subprocess.run(
            ["git", "-C", str(repo), "check-ignore", "--no-index", "--quiet", "--", name],
            capture_output=True,
            check=False,
        )
        if ignored.returncode != 1:
            raise ValueError(f"Ignored path or ignore-check failure; export refused: {name}")
        entry = git(repo, "ls-tree", "-z", commit, "--", name).split(b"\0")
        if len(entry) != 2 or not entry[0]:
            raise ValueError(f"Exact tracked file required: {name}")
        info, tracked = entry[0].split(b"\t", 1)
        mode, kind, oid = info.decode().split()
        if tracked.decode() != name or kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError(f"Symlinks, directories and submodules rejected: {name}")
        size = int(git(repo, "cat-file", "-s", oid))
        if size > 1024 * 1024:
            raise ValueError(f"Source file exceeds 1 MiB transfer limit: {name}")
        content = git(repo, "cat-file", "blob", oid)
        if b"\0" in content:
            raise ValueError(f"Binary source transfer refused: {name}")
        validate_content(name, content)
        blobs[name] = (mode, content)
    if sum(len(content) for _, content in blobs.values()) > 8 * 1024 * 1024:
        raise ValueError("Source scope exceeds 8 MiB transfer limit; select modules")
    # Exclusively create a fresh directory; never replace an existing checkout.
    output.mkdir(parents=False, exist_ok=False)
    files = []
    for name, (mode, content) in blobs.items():
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(0o755 if mode == "100755" else 0o644)
        files.append({"path": name, "sha256": hashlib.sha256(content).hexdigest()})
    provenance = {
        "commit": commit,
        "manifest": names,
        "content_screening": "common-credential-patterns-v1",
        "files": files,
        "review": "Pattern screening is not exhaustive; explicit content review remains required",
    }
    (output / "snapshot-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    return provenance


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--revision", default="HEAD")
    parser.add_argument("--paths", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    names = [
        line.strip()
        for line in args.paths.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    print(json.dumps(export(args.repo, args.revision, names, args.out), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
