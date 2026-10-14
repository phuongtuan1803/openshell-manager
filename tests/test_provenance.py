"""Provenance unit checks use synthetic metadata and local Git only."""
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from openshell_manager import configuration, control, provenance

PIN = 'a' * 40
URL = 'https://github.com/phuongtuan1803/openshell-manager.git'


def installed(monkeypatch, tmp_path, receipt):
    running = tmp_path / 'site-packages/openshell_manager/provenance.py'
    monkeypatch.setattr(provenance, 'RUNNING', running)
    dist = SimpleNamespace(read_text=lambda name: json.dumps(receipt), files=['openshell_manager/provenance.py'],
                           locate_file=lambda name: running.parent.parent / name)
    monkeypatch.setattr(provenance.metadata, 'distribution', lambda name: dist)
    return dist


def test_installed_exact_pin(monkeypatch, tmp_path):
    installed(monkeypatch, tmp_path, {'url': URL, 'vcs_info': {'vcs': 'git', 'commit_id': PIN}})
    monkeypatch.setattr(provenance, 'git', lambda *a: pytest.fail('Installed packages cannot probe Git'))
    assert provenance.verify(PIN) == PIN


@pytest.mark.parametrize('requested,commit,accepted', [
    (None, PIN, True),
    ('main', PIN, True),
    ('v0.1.0', PIN, True),
    ('b' * 40, PIN, True),
    (PIN, PIN, True),
    (PIN, None, False),
    (PIN, 'main', False),
    (PIN, 'b' * 40, False),
])
def test_requested_revision_never_overrides_resolved_commit(monkeypatch, tmp_path, requested, commit, accepted):
    vcs = {'vcs': 'git'}
    if requested is not None:
        vcs['requested_revision'] = requested
    if commit is not None:
        vcs['commit_id'] = commit
    installed(monkeypatch, tmp_path, {'url': URL, 'vcs_info': vcs})
    monkeypatch.setattr(provenance, 'git', lambda *a: pytest.fail('No mutable-ref lookup allowed'))
    if accepted:
        assert provenance.verify(PIN) == PIN
    else:
        with pytest.raises(ValueError, match='full Manager Git commit identity|revision mismatch'):
            provenance.verify(PIN)


@pytest.mark.parametrize('receipt', [{}, {'url': URL}, {'url': 'file:///tmp/unknown'},
    {'url': URL, 'vcs_info': {'vcs': 'git', 'commit_id': 'main'}},
    {'url': URL + '/evil', 'vcs_info': {'vcs': 'git', 'commit_id': PIN}},
    {'url': URL, 'vcs_info': {'vcs': 'hg', 'commit_id': PIN}},
    [], {'url': URL, 'vcs_info': []}])
def test_untrusted_receipts_fail_before_state_or_native(monkeypatch, tmp_path, receipt):
    installed(monkeypatch, tmp_path, receipt)
    project = tmp_path / 'project.json'
    project.write_text(json.dumps({'manager_revision': PIN}))
    monkeypatch.setattr(control, 'command', lambda *a, **k: pytest.fail('Native tool reached'))
    monkeypatch.setattr(configuration, 'load', lambda *a: pytest.fail('Checkout validation reached'))
    before = sorted(tmp_path.rglob('*'))
    with pytest.raises(ValueError):
        control.configure(project)
    assert sorted(tmp_path.rglob('*')) == before


def test_mismatch_and_missing_distribution(monkeypatch, tmp_path):
    dist = installed(monkeypatch, tmp_path, {'url': URL, 'vcs_info': {'vcs': 'git', 'commit_id': PIN}})
    with pytest.raises(ValueError, match='revision mismatch'):
        provenance.verify('b' * 40)
    dist.files = []
    with pytest.raises(ValueError, match='running Manager'):
        provenance.verify(PIN)
    def missing(name):
        raise provenance.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(provenance.metadata, 'distribution', missing)
    with pytest.raises(ValueError, match='Missing provenance'):
        provenance.verify(PIN)


def test_development_clean_dirty_and_conflicting_metadata(monkeypatch, tmp_path, capsys):
    root = tmp_path / 'manager'
    running = root / 'src/openshell_manager/provenance.py'
    running.parent.mkdir(parents=True)
    running.write_text('# local fixture\n')
    (root / 'pyproject.toml').write_text('[project]\nname = "openshell-manager"\n')
    def git(*args):
        return subprocess.run(['git', '-C', str(root), *args], check=True, text=True, capture_output=True).stdout.strip()
    git('init', '-q')
    git('remote', 'add', 'origin', URL)
    git('add', '.')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture')
    pin = git('rev-parse', 'HEAD')
    monkeypatch.setattr(provenance, 'RUNNING', running)
    def missing(name):
        raise provenance.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(provenance.metadata, 'distribution', missing)
    assert provenance.verify(pin) == pin
    assert 'development mode' in capsys.readouterr().err
    running.write_text('# dirty\n')
    with pytest.raises(ValueError, match='uncommitted tracked'):
        provenance.verify(pin)
    git('checkout', '--', '.')
    monkeypatch.setattr(provenance.metadata, 'distribution', lambda name: SimpleNamespace(read_text=lambda name: '{}'))
    with pytest.raises(ValueError, match='Conflicting installed'):
        provenance.verify(pin)
