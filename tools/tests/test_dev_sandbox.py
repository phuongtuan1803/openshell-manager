"""Offline regression tests for reviewed source transfer and VM configuration."""

import hashlib
import importlib.util
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SANDBOX = ROOT / "infra/dev-sandbox"
spec = importlib.util.spec_from_file_location("sandbox_snapshot", SANDBOX / "snapshot.py")
snapshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshot)


@pytest.mark.parametrize(
    "path",
    [
        "../secret",
        "/etc/passwd",
        ".git/config",
        ".ssh/id_rsa",
        "infra/secrets/key",
        ".env.local",
        "docs/html/index.html",
        "docs/pdf/report.pdf",
        "keys/token.key",
        ".npmrc",
        ".netrc",
        ".kube/config",
        "keys/ID_ED25519",
        "settings/auth.json",
        "keys/certificate.PFX",
        "snapshot-provenance.json",
        "source/../../outside",
    ],
)
def test_snapshot_rejects_private_or_outside_paths(path):
    with pytest.raises(ValueError):
        snapshot.validate_path(path)


def test_snapshot_exports_committed_blobs_only(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "source.py").write_text("reviewed = True\n")
    (repo / "outside-link").symlink_to("/etc/passwd")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Dummy",
            "-c",
            "user.email=dummy@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    (repo / "source.py").write_text("unreviewed = True\n")
    (repo / ".env").write_text("DUMMY_ONLY=1\n")
    out = tmp_path / "snapshot"
    evidence = snapshot.export(repo, "HEAD", ["source.py"], out)
    assert (out / "source.py").read_text() == "reviewed = True\n"
    assert not (out / ".git").exists()
    assert not (out / ".env").exists()
    assert evidence["files"][0]["path"] == "source.py"
    with pytest.raises(FileExistsError):
        snapshot.export(repo, "HEAD", ["source.py"], out)
    with pytest.raises(ValueError, match="Symlinks"):
        snapshot.export(repo, "HEAD", ["outside-link"], tmp_path / "bad")
    assert not (tmp_path / "bad").exists()
    with pytest.raises(ValueError):
        snapshot.export(repo, "HEAD", ["missing.py"], tmp_path / "missing")


def test_gateway_template_selects_vm_and_valid_default():
    settings = tomllib.loads((SANDBOX / "gateway.vm.example.toml").read_text())["openshell"]
    assert settings["version"] == 2
    assert settings["gateway"]["compute_driver"] == "vm"
    assert settings["gateway"]["bind_address"].startswith("127.0.0.1:")
    assert settings["drivers"]["vm"]["default_image"] == "nvcr.io/nvidia/base/ubuntu:24.04"


def committed_repo(tmp_path, files):
    repo = tmp_path / "source-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for name, content in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    subprocess.run(["git", "-C", str(repo), "add", "-f", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Dummy",
            "-c",
            "user.email=dummy@example.invalid",
            "commit",
            "-qm",
            "dummy fixtures",
        ],
        check=True,
    )
    return repo


# Construct dummy credentials at runtime so this reviewed test source itself
# remains exportable. No real credential is used or printed.
def dummy_credentials():
    return [
        ("private-key", b"-----BEGIN " + b"OPENSSH PRIVATE KEY-----\ndummy"),
        ("openai-token", b"sk-" + b"x" * 24),
        ("anthropic-token", b"sk-ant-" + b"x" * 24),
        ("github-token", b"ghp_" + b"x" * 24),
        ("github-token", b"github_pat_" + b"x" * 30),
        ("aws-access-key", b"AKIA" + b"A" * 16),
        ("google-api-key", b"AIza" + b"x" * 34),
        ("slack-token", b"xoxb-" + b"x" * 24),
        ("credential-url", b"https://" + b"dummy:dummy-password@example.invalid"),
        ("jwt", b"eyJ" + b"x" * 12 + b"." + b"y" * 16 + b"." + b"z" * 16),
        ("secret-assignment", b"OPENAI_API_KEY" + b" = 'dummy-opaque-value-123'"),
        ("secret-assignment", b'"password"' + b': "dummy-password-123"'),
        ("quoted-secret-assignment", b"password" + b" = 'tiny'"),
        ("quoted-secret-assignment", b'"token"' + b': "dummy with spaces"'),
        ("quoted-secret-assignment", b"password" + b" = '!dummy secret!'"),
    ]


@pytest.mark.parametrize("rule,secret", dummy_credentials())
def test_snapshot_fails_before_export_and_redacts_error(tmp_path, rule, secret):
    repo = committed_repo(tmp_path, {"clean.py": b"pass\n", "settings.txt": secret})
    out = tmp_path / "export"
    with pytest.raises(ValueError, match="Suspicious credential") as error:
        snapshot.export(repo, "HEAD", ["clean.py", "settings.txt"], out)
    assert not out.exists()
    assert secret.decode() not in str(error.value)
    assert "settings.txt" in str(error.value)


def test_binary_secret_patterns_are_detected():
    with pytest.raises(ValueError):
        snapshot.validate_content("asset.bin", b"\x00\xff" + b"ghp_" + b"x" * 24)
    snapshot.validate_content("asset.bin", b"\x00\xff\x01")


def test_environment_lookup_is_not_a_literal_credential():
    snapshot.validate_content("source.py", b"api_key = os.getenv('DUMMY_VAR')\n")


def test_committed_but_ignored_files_are_rejected(tmp_path):
    repo = committed_repo(tmp_path, {".gitignore": b"private.txt\n", "private.txt": b"dummy"})
    with pytest.raises(ValueError, match="Ignored path"):
        snapshot.export(repo, "HEAD", ["private.txt"], tmp_path / "export")


def test_explicit_manifest_provenance_and_selection(tmp_path):
    repo = committed_repo(tmp_path, {"source.py": b"pass\n", "other.py": b"pass\n"})
    out = tmp_path / "export"
    evidence = snapshot.export(repo, "HEAD", ["source.py"], out)
    assert evidence["manifest"] == ["source.py"]
    assert evidence["files"][0]["sha256"] == hashlib.sha256(b"pass\n").hexdigest()
    assert evidence["content_screening"] == "common-credential-patterns-v1"
    assert not (out / "other.py").exists()
    for names in ([], ["source.py", "source.py"]):
        with pytest.raises(ValueError):
            snapshot.export(repo, "HEAD", names, tmp_path / "invalid")


def test_submodule_and_symlink_output_are_rejected(tmp_path):
    repo = committed_repo(tmp_path, {"source.py": b"pass\n"})
    oid = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"]).decode().strip()
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{oid},vendor/module",
        ],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Dummy",
            "-c",
            "user.email=dummy@example.invalid",
            "commit",
            "-qm",
            "dummy submodule",
        ],
        check=True,
    )
    with pytest.raises(ValueError, match="submodules"):
        snapshot.export(repo, "HEAD", ["vendor/module"], tmp_path / "export")
    target = tmp_path / "target"
    target.mkdir()
    (tmp_path / "link").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="Symlink in snapshot output"):
        snapshot.export(repo, "HEAD", ["source.py"], tmp_path / "link/export")
    assert not (target / "export").exists()
