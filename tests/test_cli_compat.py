"""Parser dispatch is shared with the legacy control interface."""
import pytest
from openshell_manager import cli, control


@pytest.mark.parametrize('public,legacy', [
    (['setup'], ['setup', 'apply']),
    (['setup', '--dry-run', '--project', '/fixture'], ['setup', 'dry-run', '--project', '/fixture']),
    (['setup', '--check'], ['setup', 'check']),
    (['verify', '--host', '/fixture'], ['setup', 'check', '--host', '/fixture']),
    (['start', 'codex', '--scope', 'pilot'], ['start', 'codex', '--scope', 'pilot']),
    (['run', 'claude'], ['run', 'claude']),
    (['rules', 'pending'], ['rules', 'pending']),
])
def test_shared_dispatch(monkeypatch, public, legacy):
    calls = []
    monkeypatch.setattr(control, 'main', lambda argv: calls.append(argv) or 17)
    assert cli.main(public) == 17
    assert calls == [legacy]


def test_version_requires_no_profile_or_host(monkeypatch, capsys):
    monkeypatch.setattr(cli.provenance, 'identity', lambda: ('a' * 40, 'installed Git'))
    monkeypatch.setattr(control, 'configure', lambda *a, **k: pytest.fail('version configured host'))
    assert cli.main(['version']) == 0
    assert 'a' * 40 in capsys.readouterr().out


def test_missing_project_and_parser_errors(monkeypatch):
    monkeypatch.delenv('MANAGER_PROJECT', raising=False)
    monkeypatch.setattr(control, 'configure', lambda *a, **k: pytest.fail('No profile reached configure'))
    assert cli.main(['setup', '--dry-run']) == 1
    assert cli.main(['setup', '--dry-run', '--check']) == 2
    with pytest.raises(SystemExit) as exc:
        cli.main(['start', 'codex', '--scope', 'outside'])
    assert exc.value.code == 2


@pytest.mark.parametrize('explicit', [False, True])
def test_project_and_host_precedence(monkeypatch, explicit):
    monkeypatch.setenv('MANAGER_PROJECT', '/environment-project')
    monkeypatch.setenv('MANAGER_HOST', '/environment-host')
    captured = []
    def configure(project, host, **kwargs):
        captured.append((str(project), str(host)))
        raise ValueError('stop before state or host operations')
    monkeypatch.setattr(control, 'configure', configure)
    args = ['setup', '--dry-run']
    if explicit:
        args += ['--project', '/explicit-project', '--host', '/explicit-host']
    assert cli.main(args) == 1
    source = 'explicit' if explicit else 'environment'
    assert captured == [(f'/{source}-project', f'/{source}-host')]
