"""PR2 profiles use only synthetic local Git checkouts and mocked host calls."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parents[2] / 'infra/dev-sandbox'
sys.path.insert(0, str(HERE))
import configuration
import workflow


def controller():
    spec = importlib.util.spec_from_file_location('project_controller', HERE / 'control.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def profile(tmp_path):
    def create(name='generic', oma=False):
        root = tmp_path / name
        root.mkdir()
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
        subprocess.run(['git', '-C', str(root), 'remote', 'add', 'origin', 'https://github.com/synthetic/' + name + '.git'], check=True)
        source = ['modules/m01-synthetic/src/app.py', 'modules/m01-synthetic/tests/test_app.py'] if oma else ['src/app.py', 'tests/test_app.py']
        for path in source:
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('pass\n')
        (root / 'source.txt').write_text('\n'.join(source))
        (root / 'edit.txt').write_text(source[0])
        cfg = dict(id=name, repository='synthetic/' + name, root=str(root), sandbox=name + '-dev',
                   workspace='/sandbox/' + name + '/checkout', source_manifest='source.txt', edit_scope='edit.txt',
                   base_branch='develop', test_paths=[source[1]], image='synthetic-image:fixed',
                   image_id='sha256:' + 'a' * 64, manager_revision='b' * 40)
        if oma:
            cfg['modules'] = {'modules/m01-synthetic': source}
        path = root / 'project.json'
        path.write_text(json.dumps(cfg))
        host = tmp_path / 'host.json'
        host.write_text(json.dumps({'state_root': str(tmp_path / 'state')}))
        return path, host, cfg
    return create


@pytest.mark.parametrize('oma', [False, True])
def test_generic_and_synthetic_oma_profiles(profile, oma):
    path, host, _ = profile('synthetic-oma' if oma else 'generic', oma)
    cfg = configuration.load(path, host)
    assert cfg['rules'] == []  # defaults cannot inject network grants
    c = controller()
    c.PROFILE, c.REPO, c.SOURCE_MANIFEST = cfg, Path(cfg['root']), cfg['source_manifest']
    assert workflow.scope_paths(c, 'full') == c.manifest()
    if oma:
        assert workflow.scope_paths(c, 'module', ['modules/m01-synthetic']) == c.manifest()
    with pytest.raises(ValueError):
        workflow.scope_paths(c, 'module', ['../other'])
    native = configuration.provider_profile(cfg)
    assert native['id'] == cfg['id'] + '-github'
    for endpoint in native['endpoints']:
        for rule in endpoint['rules']:
            assert cfg['repository'] in rule['allow']['path']
            assert 'merge' not in rule['allow']['path']
    assert c.base_policy()['landlock']['compatibility'] == 'hard_requirement'
    assert c.base_policy()['network_policies'] == {}


@pytest.mark.parametrize('field,value', [
    ('repository', 'example/project'), ('repository', 'synthetic/*'), ('sandbox', 'other-dev'),
    ('workspace', '/sandbox/other/checkout'), ('image_id', 'sha256:' + '0' * 64),
    ('manager_revision', '0' * 40), ('base_branch', '../main'), ('test_paths', ['../secret.py']),
    ('rules', [{'id': 'all', 'host': '*'}]), ('provider', 'shared'),
    ('modules', {'foreign': ['outside.py']}), ('cli_version', 'openshell 99'),
])
def test_invalid_config_before_host_calls(profile, field, value, monkeypatch):
    path, host, cfg = profile()
    cfg[field] = value
    path.write_text(json.dumps(cfg))
    c = controller()
    monkeypatch.setattr(c, 'command', lambda *a, **k: pytest.fail('Invalid config reached host commands'))
    with pytest.raises((ValueError, TypeError)):
        c.configure(path, host)
    assert not (host.parent / 'state').exists()


def test_host_cannot_merge_permissions(profile):
    path, host, _ = profile()
    host.write_text(json.dumps({'rules': [], 'workspace': '/tmp'}))
    with pytest.raises(ValueError, match='cannot override'):
        configuration.load(path, host)


def test_edit_scope_symlink_and_duplicates_rejected(profile):
    path, host, cfg = profile()
    root = Path(cfg['root'])
    for content in ['outside.py', 'src/app.py\nsrc/app.py', '../outside.py']:
        (root / 'edit.txt').write_text(content)
        with pytest.raises(ValueError):
            configuration.load(path, host)
    (root / 'edit.txt').write_text('src/app.py')
    (root / 'src/app.py').unlink()
    (root / 'src/app.py').symlink_to(root / 'tests/test_app.py')
    with pytest.raises(ValueError, match='symlink source'):
        configuration.load(path, host)


def test_state_credentials_and_lock_isolation(profile, monkeypatch):
    a, host, acfg = profile('alpha')
    b, _, _ = profile('bravo')
    c = controller()
    # configure intentionally updates legacy adapters for a single CLI process.
    for module in (c.github_provider, c.workflow):
        for key in ('PROVIDER', 'REPOSITORY'):
            monkeypatch.setattr(module, key, getattr(module, key))
    monkeypatch.setattr(c.github_provider, 'RULE', c.github_provider.RULE)
    monkeypatch.setattr(c, 'command', lambda *a, **k: SimpleNamespace(stdout='b' * 40))
    c.configure(a, host)
    first = c.STATE
    assert c.github_provider.PROVIDER == 'alpha-github'
    (first / 'operation.lock').write_text('alpha')
    c.configure(b, host)
    assert c.STATE != first and c.github_provider.PROVIDER == 'bravo-github'
    assert not (c.STATE / 'operation.lock').exists()
    assert 'synthetic/alpha' not in c.PROVIDER_PROFILE.read_text()
    acfg['repository'] = 'synthetic/changed'
    subprocess.run(['git', '-C', acfg['root'], 'remote', 'set-url', 'origin', 'https://github.com/synthetic/changed.git'], check=True)
    a.write_text(json.dumps(acfg))
    with pytest.raises(ValueError, match='Conflicting project'):
        c.configure(a, host)
    assert (first / 'operation.lock').read_text() == 'alpha'


def test_example_cli_guard_before_host(monkeypatch):
    c = controller()
    monkeypatch.delenv('MANAGER_PROJECT', raising=False)
    monkeypatch.setattr(sys, 'argv', ['control.py', 'setup', 'apply'])
    monkeypatch.setattr(c, 'command', lambda *a, **k: pytest.fail('Example reached a host command'))
    assert c.main() == 1


def test_active_session_blocks_setup_before_bootstrap(monkeypatch, tmp_path):
    import fcntl
    c = controller()
    c.STATE = tmp_path
    monkeypatch.setattr(c.bootstrap, 'host', lambda *a: pytest.fail('Active session reached bootstrap'))
    with (tmp_path / 'task.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match='Active session'):
            c.setup_report('apply')


def test_existing_unbound_sandbox_not_adopted(monkeypatch, tmp_path):
    c = controller()
    c.STATE = tmp_path
    monkeypatch.setattr(c, 'preflight', lambda: None)
    monkeypatch.setattr(c, 'image', lambda **k: 'sha256:' + 'a' * 64)
    monkeypatch.setattr(c, 'oshell', lambda *a, **k: SimpleNamespace(stdout=c.NAME))
    monkeypatch.setattr(c, 'api', lambda *a: pytest.fail('Unbound sandbox was adopted'))
    with pytest.raises(ValueError, match='refusing adoption'):
        c.setup('apply')


def test_manager_pin_mismatch(profile, monkeypatch):
    path, host, _ = profile()
    c = controller()
    monkeypatch.setattr(c, 'command', lambda *a, **k: SimpleNamespace(stdout='c' * 40))
    with pytest.raises(ValueError, match='revision mismatch'):
        c.configure(path, host)
    assert not (host.parent / 'state').exists()


def test_idle_lease_prevents_new_session_race(tmp_path):
    import fcntl
    c = controller()
    c.STATE = tmp_path
    with c.idle_lease():
        with (tmp_path / 'task.lock').open('a') as task:
            with pytest.raises(BlockingIOError):
                fcntl.flock(task, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_symlink_host_state_and_lock_rejected(profile, tmp_path):
    path, host, _ = profile()
    real = tmp_path / 'real-state'
    real.mkdir()
    (tmp_path / 'state').symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match='Unsafe state'):
        configuration.load(path, host)
    c = controller()
    c.STATE = real
    (real / 'task.lock').symlink_to(host)
    with pytest.raises(ValueError, match='Symlink task lock'):
        c.task_active()


def test_duplicate_json_fields_rejected(profile):
    path, host, _ = profile()
    path.write_text('{"id":"alpha","id":"bravo"}')
    with pytest.raises(ValueError, match='Duplicate configuration'):
        configuration.load(path, host)


def test_profile_image_pin_cannot_fall_back_to_build(monkeypatch):
    c = controller()
    c.PROFILE = {'sandbox': c.NAME, 'image_id': 'sha256:' + 'a' * 64}
    calls = []
    def command(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(c, 'command', command)
    with pytest.raises(ValueError, match='digest missing'):
        c.image()
    assert len(calls) == 1 and 'build' not in calls[0]


def test_profile_hash_drift_rejected_before_native_lookup(profile, monkeypatch):
    path, host, _ = profile()
    cfg = configuration.load(path, host)
    c = controller()
    c.PROFILE, c.STATE = cfg, cfg['state']
    c.SOURCE_MANIFEST, c.EDIT_SCOPE = cfg['source_manifest'], cfg['edit_scope']
    c.save(c.STATE / 'state.json', {'config_hash': c.config_hash()})
    cfg['test_paths'] = ['src/app.py']
    monkeypatch.setattr(c, 'api', lambda *a: pytest.fail('Config drift reached native lookup'))
    with pytest.raises(ValueError, match='configuration drift'):
        c.state()


def test_empty_writable_scope_does_not_expand():
    c = controller()
    import base64
    with pytest.raises(ValueError, match='outside approved'):
        c.patch_from_files({'app.py': b'pass\n'}, {'app.py': base64.b64encode(b'print(1)\n').decode()}, scope=[])


@pytest.mark.parametrize('oma', [False, True])
def test_project_export_and_patch_boundaries(profile, tmp_path, oma):
    import base64
    import snapshot
    path, host, _ = profile('synthetic-oma' if oma else 'generic', oma)
    cfg = configuration.load(path, host)
    root = Path(cfg['root'])
    for args in (['add', '.'], ['-c', 'user.name=Offline Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture']):
        subprocess.run(['git', '-C', str(root), *args], check=True)
    source = configuration.paths(cfg['source_manifest'])
    result = snapshot.export(root, 'HEAD', source, tmp_path / 'export')
    assert result['manifest'] == source
    c = controller()
    baseline = {name: (tmp_path / 'export' / name).read_bytes() for name in source}
    changed = {name: base64.b64encode(content).decode() for name, content in baseline.items()}
    changed[source[0]] = base64.b64encode(b'print(1)\n').decode()
    editable = configuration.paths(cfg['edit_scope'])
    assert c.patch_from_files(baseline, changed, editable)
    changed[source[1]] = base64.b64encode(b'print(2)\n').decode()
    with pytest.raises(ValueError, match='outside approved'):
        c.patch_from_files(baseline, changed, editable)


@pytest.mark.parametrize('length,valid', [(2, True), (14, True), (15, True), (16, False), (31, False)])
def test_sandbox_native_length_boundary(profile, length, valid, monkeypatch):
    path, host, _ = profile('a' * length)
    if valid:
        cfg = configuration.load(path, host)
        assert len(cfg['sandbox']) == length + 4 <= 19
    else:
        c = controller()
        monkeypatch.setattr(c, 'command', lambda *a, **k: pytest.fail('Overlong sandbox reached host'))
        with pytest.raises(ValueError, match='at most 19'):
            c.configure(path, host, install_dependencies=True)
        assert not (host.parent / 'state').exists()


@pytest.mark.parametrize('url', [
    'https://github.com/synthetic/generic.git', 'https://github.com/synthetic/generic',
    'git@github.com:synthetic/generic.git', 'git@github.com:synthetic/generic',
    'ssh://git@github.com/synthetic/generic.git', 'ssh://git@github.com/synthetic/generic',
])
def test_checkout_origin_supported_formats(profile, url):
    path, host, cfg = profile()
    subprocess.run(['git', '-C', cfg['root'], 'remote', 'set-url', 'origin', url], check=True)
    before = (Path(cfg['root']) / '.git/config').read_bytes()
    configuration.load(path, host)
    assert (Path(cfg['root']) / '.git/config').read_bytes() == before


@pytest.mark.parametrize('case', ['missing', 'wrong', 'multiple', 'push', 'rewrite', 'http', 'userinfo'])
def test_checkout_identity_rejects_ambiguous_and_foreign_origins(profile, case, monkeypatch):
    path, host, cfg = profile()
    git = ['git', '-C', cfg['root']]
    commands = {
        'missing': ['remote', 'remove', 'origin'],
        'wrong': ['remote', 'set-url', 'origin', 'https://github.com/foreign/project.git'],
        'multiple': ['config', '--add', 'remote.origin.url', 'https://github.com/foreign/project.git'],
        'push': ['config', 'remote.origin.pushurl', 'https://github.com/foreign/project.git'],
        'rewrite': ['config', 'url.https://github.com/foreign/.insteadOf', 'https://github.com/synthetic/'],
        'http': ['remote', 'set-url', 'origin', 'http://github.com/synthetic/generic.git'],
        'userinfo': ['remote', 'set-url', 'origin', 'https://user@github.com/synthetic/generic.git'],
    }
    subprocess.run(git + commands[case], check=True)
    before = (Path(cfg['root']) / '.git/config').read_bytes()
    c = controller()
    monkeypatch.setattr(c, 'command', lambda *a, **k: pytest.fail('Invalid origin reached host'))
    monkeypatch.setattr(c.bootstrap, 'yaml_module', lambda *a, **k: pytest.fail('Invalid origin reached dependency install'))
    with pytest.raises(ValueError, match='Git origin'):
        c.configure(path, host, install_dependencies=True)
    assert not (host.parent / 'state').exists()
    assert (Path(cfg['root']) / '.git/config').read_bytes() == before


@pytest.mark.parametrize('operation', ['snapshot', 'export', 'publish'])
def test_origin_drift_rechecked_at_operation_boundary(profile, operation, monkeypatch):
    path, host, _ = profile()
    cfg = configuration.load(path, host)
    c = controller()
    c.PROFILE, c.REPO, c.STATE = cfg, Path(cfg['root']), cfg['state']
    subprocess.run(['git', '-C', cfg['root'], 'remote', 'set-url', 'origin', 'https://github.com/foreign/project.git'], check=True)
    monkeypatch.setattr(c, 'guest', lambda *a, **k: pytest.fail('Origin drift reached guest'))
    monkeypatch.setattr(c, 'current', lambda *a: pytest.fail('Origin drift read snapshot'))
    monkeypatch.setattr(c.snapshot, 'export', lambda *a, **k: pytest.fail('Origin drift exported source'))
    monkeypatch.setattr(workflow, 'publication_preflight', lambda *a: pytest.fail('Origin drift reached provider'))
    with pytest.raises(ValueError, match='Git origin'):
        if operation == 'snapshot':
            c.refresh({}, names=['src/app.py'])
        elif operation == 'export':
            c.export_patch({})
        else:
            workflow.publish(c, {}, 'feat/openshell-review', Path('unused'), base='develop')
    assert not c.STATE.exists()


def test_module_test_selection_intersects_source_scope():
    c = SimpleNamespace(PROFILE={
        'test_paths': ['core/test_app.py', 'other/test_app.py', 'shared/test_all.py'],
        'modules': {'core': ['core/app.py', 'core/test_app.py'], 'other': ['other/test_app.py']},
    })
    paths = workflow.scope_paths(c, 'module', ['core'])
    command = workflow.test_command(c, paths, scope='module')
    assert command == ('/usr/bin/python3', '-m', 'pytest', '-q', 'core/test_app.py')
    with pytest.raises(ValueError, match='active scope'):
        workflow.test_command(c, paths, ['other/test_app.py'], 'module')
    multiple = workflow.scope_paths(c, 'module', ['core', 'other'])
    assert workflow.test_command(c, multiple, scope='module')[4:] == ('core/test_app.py', 'other/test_app.py')


@pytest.mark.parametrize('tests', [None, ('/usr/bin/python3', '-m', 'pytest', '-q', 'other/test_app.py')])
def test_module_with_no_valid_tests_fails_before_native_or_refresh(tests):
    c = SimpleNamespace(PROFILE={
        'test_paths': ['other/test_app.py'], 'modules': {'core': ['core/app.py']},
    }, state=lambda: pytest.fail('No module tests reached native state'),
       refresh=lambda *a, **k: pytest.fail('No module tests refreshed source'))
    with pytest.raises(ValueError, match='active scope'):
        workflow._run(c, 'codex', 'fix task', scope='module', modules=['core'], tests=tests)


def test_profiled_workflow_defaults_choose_module_tests_before_native():
    class StopBeforeNative(Exception):
        pass
    c = SimpleNamespace(PROFILE={
        'test_paths': ['core/test_app.py', 'other/test_app.py'],
        'modules': {'core': ['core/app.py', 'core/test_app.py']},
    }, state=lambda: pytest.fail('Test selection must happen first'))
    original = workflow.scoped_tests
    selected = []
    def check_tests(control, paths, tests, scope):
        selected.append(original(control, paths, tests, scope))
        raise StopBeforeNative
    from unittest.mock import patch
    with patch.object(workflow, 'scoped_tests', check_tests), pytest.raises(StopBeforeNative):
        workflow._run(c, 'codex', 'fix task', scope='module', modules=['core'])
    assert selected == [('/usr/bin/python3', '-m', 'pytest', '-q', 'core/test_app.py')]


@pytest.mark.parametrize('install', [False, True])
def test_validated_setup_can_provision_yaml_without_extra_command(profile, monkeypatch, install):
    import yaml
    path, host, _ = profile()
    c = controller()
    calls, imports = [], []
    for module in (c.github_provider, c.workflow):
        for key in ('PROVIDER', 'REPOSITORY'):
            monkeypatch.setattr(module, key, getattr(module, key))
    monkeypatch.setattr(c.github_provider, 'RULE', c.github_provider.RULE)
    def command(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(stdout='b' * 40)
    def import_yaml(name):
        assert name == 'yaml'
        imports.append(name)
        if len(imports) == 1:
            raise ModuleNotFoundError(name='yaml')
        return yaml
    monkeypatch.setattr(c, 'command', command)
    monkeypatch.setattr(c.bootstrap.importlib, 'import_module', import_yaml)
    monkeypatch.setattr(c.bootstrap.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(c.bootstrap.platform, 'machine', lambda: 'x86_64')
    if install:
        c.configure(path, host, install_dependencies=True)
        assert [argv for argv in calls if argv[0] == 'sudo'] == [
            ['sudo', 'apt-get', 'update'], ['sudo', 'apt-get', 'install', '-y', 'python3-yaml']]
        assert c.PROVIDER_PROFILE.is_file()
    else:
        with pytest.raises(ValueError, match='PyYAML missing'):
            c.configure(path, host)
        assert not any(argv[0] == 'sudo' for argv in calls)


def test_invalid_base_override_cannot_bootstrap_dependencies(profile, monkeypatch):
    path, host, _ = profile()
    c = controller()
    monkeypatch.setattr(c, 'command', lambda *a, **k: pytest.fail('Invalid base reached bootstrap'))
    with pytest.raises(ValueError, match='Base branch differs'):
        c.configure(path, host, install_dependencies=True, base_branch='other')
    assert not (host.parent / 'state').exists()


def test_active_session_cannot_bootstrap_dependencies(profile, monkeypatch):
    import fcntl
    path, host, _ = profile()
    cfg = configuration.load(path, host)
    cfg['state'].mkdir(parents=True)
    c = controller()
    monkeypatch.setattr(c, 'command', lambda *a, **k: SimpleNamespace(stdout='b' * 40))
    monkeypatch.setattr(c.bootstrap, 'yaml_module', lambda *a, **k: pytest.fail('Active session reached dependency install'))
    with (cfg['state'] / 'task.lock').open('a') as task:
        fcntl.flock(task, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match='Active session'):
            c.configure(path, host, install_dependencies=True)
