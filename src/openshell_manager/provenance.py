"""Prove the running code's immutable Git identity before state or native operations."""
import json
import re
import subprocess
import sys
from importlib import metadata
from pathlib import Path

SOURCES = frozenset({
    'https://github.com/phuongtuan1803/openshell-manager.git',
    'https://github.com/phuongtuan1803/openshell-manager',
    'ssh://git@github.com/phuongtuan1803/openshell-manager.git',
    'ssh://git@github.com/phuongtuan1803/openshell-manager',
    'git@github.com:phuongtuan1803/openshell-manager.git',
    'git@github.com:phuongtuan1803/openshell-manager',
})
RUNNING = Path(__file__).resolve()


def sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{40}', value) and value != '0' * 40


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                            text=True, check=False, timeout=10)
    if result.returncode:
        raise ValueError('Cannot verify Manager development checkout')
    return result.stdout.strip()


def identity():
    try:
        dist = metadata.distribution('openshell-manager')
    except metadata.PackageNotFoundError:
        dist = None
    try:
        receipt = json.loads(dist.read_text('direct_url.json') or '{}') if dist else {}
        if not isinstance(receipt, dict):
            raise ValueError('Invalid Manager provenance receipt')
        # Source development is an explicit, exact layout; installed packages never fall back to Git.
        root = RUNNING.parents[2]
        development = (root / '.git').exists() and RUNNING == root / 'src/openshell_manager/provenance.py'
        if development:
            if dist is not None:
                from urllib.parse import urlsplit, unquote
                url = urlsplit(receipt.get('url', ''))
                if (receipt.get('dir_info') != {'editable': True} or url.scheme != 'file'
                        or url.netloc or Path(unquote(url.path)).resolve() != root):
                    raise ValueError('Conflicting installed Manager metadata; use an isolated development environment')
            if (git(root, 'rev-parse', '--show-toplevel') != str(root)
                    or git(root, 'config', '--get', 'remote.origin.url') not in SOURCES):
                raise ValueError('Unapproved Manager development checkout')
            git(root, 'ls-files', '--error-unmatch', 'src/openshell_manager/provenance.py', 'pyproject.toml')
            if git(root, 'status', '--porcelain', '--untracked-files=no'):
                raise ValueError('Manager development checkout has uncommitted tracked changes')
            revision = git(root, 'rev-parse', 'HEAD')
            mode = 'development'
        else:
            if dist is None or not any(
                Path(dist.locate_file(f)).resolve() == RUNNING for f in (dist.files or [])
            ):
                raise ValueError('Missing provenance for the running Manager package')
            vcs = receipt.get('vcs_info', {})
            if not isinstance(vcs, dict):
                raise ValueError('Invalid Manager VCS provenance')
            if receipt.get('url') not in SOURCES or vcs.get('vcs') != 'git':
                raise ValueError('Missing or unapproved Manager VCS provenance')
            revision = vcs.get('commit_id')
            mode = 'installed Git'
        if not sha(revision):
            raise ValueError('Missing full Manager Git commit identity')
        return revision, mode
    except (OSError, TypeError, KeyError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        raise ValueError('Invalid Manager provenance; reinstall the approved full Git SHA with uv') from exc


def verify(expected):
    if not sha(expected):
        raise ValueError('Placeholder or invalid Manager revision')
    revision, mode = identity()
    if revision != expected:
        raise ValueError('Manager revision mismatch; reinstall the approved full Git SHA; no automatic update')
    if mode == 'development':
        print(f'Manager development mode: {revision}', file=sys.stderr)
    return revision
