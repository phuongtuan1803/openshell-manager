"""Offline artifact + installed-CLI acceptance using disposable local Git fixtures.

Run after `uv build`, with the build/runtime dependencies already cached:
UV_CACHE_DIR=/tmp/pr3a-uv-cache uv run --locked python tools/verify_packaging.py
No host tools, credentials, original Git metadata or runtime state are modified.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
URL = 'https://github.com/phuongtuan1803/openshell-manager.git'
MANIFEST = json.loads((REPO / 'tests/fixtures/packaging/resources.json').read_text())


def inventory():
    wheel = next((REPO / 'dist').glob('*.whl'))
    sdist = next((REPO / 'dist').glob('*.tar.gz'))
    with zipfile.ZipFile(wheel) as archive:
        wheel_files = {n: archive.read(n) for n in archive.namelist() if not n.endswith('/')}
    with tarfile.open(sdist) as archive:
        sdist_files = {m.name.split('/', 1)[1]: archive.extractfile(m).read()
                       for m in archive.getmembers() if m.isfile()}
    for source, expected in MANIFEST.items():
        packaged = ('openshell_manager/' + Path(source).name if source.startswith('src/')
                    else 'openshell_manager/resources/' + source)
        assert hashlib.sha256(wheel_files[packaged]).hexdigest() == expected, packaged
        assert wheel_files[packaged] == sdist_files['src/' + packaged], packaged
    modules = {str(p.relative_to(REPO / 'src')) for p in (REPO / 'src/openshell_manager').rglob('*.py')}
    resources = {('openshell_manager/resources/' + n) for n in MANIFEST if not n.startswith('src/')}
    assert {n for n in wheel_files if '.dist-info/' not in n} == modules | resources
    # Explicit public sdist boundary: no profiles from the host/project, state, logs, or reference tree.
    allowed_roots = {'src', 'scripts', 'tests', 'docs', 'tools'}
    allowed_files = {'.gitignore', 'README.md', 'pyproject.toml', 'uv.lock', 'pytest.ini', 'PKG-INFO'}
    for name in sdist_files:
        assert name.split('/')[0] in allowed_roots or name in allowed_files, name
        assert not set(Path(name).parts) & {'.git', '.aws', '.codex', '.agents', '.venv', '__pycache__', 'evidence', 'logs'}, name
    forbidden = (b'-----BEGIN ' + b'OPENSSH PRIVATE KEY-----', b'-----BEGIN ' + b'RSA PRIVATE KEY-----',
                 str(REPO).encode())
    for name, data in (wheel_files | sdist_files).items():
        assert not any(value in data for value in forbidden), name
    print(f'PASS archives: {len(wheel_files)} wheel files; {len(sdist_files)} sdist files; {len(MANIFEST)} reviewed hashes')
    return wheel


def smoke(wheel):
    with tempfile.TemporaryDirectory(prefix='pr3a-installed-') as temporary:
        base = Path(temporary)
        home, fixture, outside = base / 'home', base / 'manager', base / 'outside'
        for path in (home, fixture, outside):
            path.mkdir()
        shutil.copytree(REPO / 'src', fixture / 'src', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(REPO / 'scripts', fixture / 'scripts')
        for name in ('pyproject.toml', 'README.md', 'uv.lock'):
            shutil.copyfile(REPO / name, fixture / name)
        env = {k: v for k, v in os.environ.items() if not k.startswith(('OPENSHELL_', 'GIT_CONFIG_'))
               and k not in {'GH_TOKEN', 'GITHUB_TOKEN', 'MANAGER_PROJECT', 'MANAGER_HOST', 'PYTHONPATH', 'VIRTUAL_ENV'}}
        uv = shutil.which('uv')
        env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / '.config'),
                   GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=str(base / 'gitconfig'), GIT_TERMINAL_PROMPT='0',
                   UV_TOOL_DIR=str(base / 'tools'), UV_TOOL_BIN_DIR=str(base / 'bin'),
                   UV_PYTHON_DOWNLOADS='never', UV_NO_MANAGED_PYTHON='1')
        # The approved HTTPS identity resolves only to this local fixture. No Git network transport exists.
        (base / 'gitconfig').write_text(f'[url "{fixture.as_uri()}"]\n\tinsteadOf = {URL}\n[protocol "https"]\n\tallow = never\n[protocol "ssh"]\n\tallow = never\n[protocol "file"]\n\tallow = always\n')
        def run(argv, expected=0, extra=None):
            result = subprocess.run([str(a) for a in argv], cwd=outside, env=env | (extra or {}),
                                    text=True, capture_output=True, timeout=120)
            assert result.returncode == expected, (argv, result.returncode, result.stdout, result.stderr)
            return result
        def git(root, *args):
            return run(['git', '-C', root, *args]).stdout.strip()
        git(fixture, 'init', '-q')
        git(fixture, 'remote', 'add', 'origin', URL)
        git(fixture, 'add', '.')
        git(fixture, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture')
        pin = git(fixture, 'rev-parse', 'HEAD')
        run([uv, 'tool', 'install', '--offline', '--python', sys.executable, f'git+{URL}@{pin}'])
        hidden_source = base / 'source-hidden'
        fixture.rename(hidden_source)
        executable = base / 'bin/openshell-manager'
        version = run([executable, 'version']).stdout
        assert 'installed Git: ' + pin in version, version
        python = base / 'tools/openshell-manager/bin/python'
        resource_probe = 'from importlib.resources import files; import hashlib,json; r=files("openshell_manager"); print(json.dumps({str(p.relative_to(r)): hashlib.sha256(p.read_bytes()).hexdigest() for p in r.rglob("*") if p.is_file() and ("resources" in p.parts or p.name in {"guest.py","session.py","validate_guest.py","agent_guest.py"})}))'
        installed_hashes = json.loads(run([python, '-c', resource_probe]).stdout)
        for source, expected in MANIFEST.items():
            name = Path(source).name if source.startswith('src/') else 'resources/' + source
            assert installed_hashes[name] == expected, name
        dependencies = run([python, '-c', 'import importlib.metadata as m; print(m.version("PyYAML"))']).stdout.strip()
        assert dependencies == '6.0.3', dependencies
        native_dependencies = json.loads(run([python, '-c', 'import importlib.metadata as m,json; print(json.dumps({n:m.version(n) for n in ("grpcio","protobuf")}))']).stdout)
        assert native_dependencies == {'grpcio': '1.76.0', 'protobuf': '6.33.6'}
        project = base / 'project'
        project.mkdir()
        git(project, 'init', '-q')
        git(project, 'remote', 'add', 'origin', 'https://github.com/synthetic/fixture.git')
        (project / 'app.py').write_text('pass\n')
        (base / 'scope.txt').write_text('app.py\n')
        profile = dict(id='fixture', repository='synthetic/fixture', root=str(project), sandbox='fixture-dev',
                       workspace='/sandbox/fixture/checkout', source_manifest='scope.txt', edit_scope='scope.txt',
                       base_branch='main', test_paths=['app.py'], image='reviewed/image:fixture',
                       image_id='sha256:' + 'a' * 64, manager_revision=pin)
        profile_path, host = base / 'project.json', base / 'host.json'
        profile_path.write_text(json.dumps(profile))
        host.write_text(json.dumps({'state_root': str(base / 'state')}))
        # Every host/native binary is an inert tripwire, including all model launchers.
        native = base / 'native'
        native.mkdir()
        sentinel = base / 'native-called'
        for name in ('sudo', 'apt-get', 'docker', 'openshell', 'openshell-gateway', 'systemctl', 'codex', 'claude', 'opencode', 'gh', 'curl'):
            path = native / name
            path.write_text(f'#!/bin/sh\necho called >> "{sentinel}"\nexit 99\n')
            path.chmod(0o755)
        # Legacy source wrappers use a Python environment with the declared runtime dependency.
        # It must have no installed Manager metadata, which would conflict with source invocation.
        run([uv, 'venv', '--offline', '--python', sys.executable, base / 'source-python'])
        run([uv, 'pip', 'install', '--offline', '--python', base / 'source-python/bin/python', 'PyYAML==6.0.3'])
        (native / 'python3').symlink_to(base / 'source-python/bin/python')
        env['PATH'] = str(native) + ':' + env['PATH']
        options = ['--project', profile_path, '--host', host]
        assert 'SKIP' in run([executable, 'setup', '--dry-run', *options]).stdout
        assert not sentinel.exists()
        assert (base / 'state/fixture/project-binding.json').exists()
        assert 'approve-network' in run([executable, 'agent', '--help']).stdout
        assert 'Supply --project' in run([executable, 'agent', 'status', '--agent', 'claude'], expected=1).stderr
        approval_help = run([executable, 'approval', '--help']).stdout
        assert 'approval' in approval_help and 'test' in approval_help
        # Help for the real-run command remains harmless in the installed artifact.
        assert 'test' in run([executable, 'approval', 'test', '--help']).stdout
        assert 'test' in run([python, '-I', '-m', 'openshell_manager', 'approval', 'test', '--help']).stdout
        assert 'disabled' in run([executable, 'approval', 'status', *options]).stdout
        assert 'disabled' in run([executable, 'approval', 'pending', *options]).stdout
        assert not (base / 'state/fixture/approval').exists()
        run([executable, 'approval', 'serve', *options], expected=1)  # no private bot credentials
        run([executable, 'approval', 'stop', *options], expected=1)  # no bridge state to stop
        assert not sentinel.exists()
        hidden_source.rename(fixture)
        # Source wrappers exercise the same handlers and exit codes; only development diagnostics differ.
        assert 'SKIP' in run(['bash', fixture / 'scripts/setup.sh', '--dry-run', *options]).stdout
        for public, wrapper in [(['start', 'codex'], ['start.sh', 'codex']),
                                (['run', 'codex'], ['start.sh', 'codex', '--autonomous']),
                                (['verify'], ['verify.sh']),
                                (['rules', 'pending'], ['rules.sh', 'pending']),
                                (['github', 'check'], ['github.sh', 'check']),
                                (['refresh'], ['refresh.sh']), (['export'], ['export.sh']), (['cleanup'], ['cleanup.sh'])]:
            # Missing project is a guaranteed pre-host failure for every legacy command.
            new = run([executable, *public], expected=1)
            old = run(['bash', fixture / 'scripts' / wrapper[0], *wrapper[1:]], expected=1)
            assert new.stderr == old.stderr, (public, new.stderr, old.stderr)
        for flags in (['--scope', 'module'], ['--scope', 'module', '--module', 'outside'],
                      ['--test-path', 'outside.py']):
            new = run([executable, 'run', 'codex', *options, *flags], expected=1)
            old = run(['bash', fixture / 'scripts/start.sh', 'codex', '--autonomous', *options, *flags], expected=1)
            assert new.stderr.splitlines()[-1] == old.stderr.splitlines()[-1]
        overrides = {'OPENSHELL_SANDBOX': 'forbidden-override'}
        new = run([executable, 'setup', '--dry-run', *options], expected=1, extra=overrides)
        old = run(['bash', fixture / 'scripts/setup.sh', '--dry-run', *options], expected=1, extra=overrides)
        assert new.stderr == old.stderr
        payload = fixture / 'src/openshell_manager/guest.py'
        payload.write_text(payload.read_text() + '\n# dirty fixture\n')
        dirty = run(['bash', fixture / 'scripts/setup.sh', '--dry-run', *options], expected=1)
        assert 'uncommitted tracked changes' in dirty.stderr
        git(fixture, 'checkout', '--', 'src/openshell_manager/guest.py')
        # Unknown/mismatched provenance must not create state and must never reach tripwires.
        negative_host = base / 'negative-host.json'
        negative_host.write_text(json.dumps({'state_root': str(base / 'negative-state')}))
        profile['manager_revision'] = 'b' * 40
        profile_path.write_text(json.dumps(profile))
        assert 'revision mismatch' in run([executable, 'setup', '--dry-run', '--project', profile_path, '--host', negative_host], expected=1).stderr
        assert not (base / 'negative-state').exists()
        # A plain wheel has no VCS receipt, even though its version/name match.
        run([uv, 'tool', 'install', '--offline', '--force', '--python', sys.executable, wheel])
        assert 'provenance' in run([executable, 'setup', '--dry-run', '--project', profile_path, '--host', negative_host], expected=1).stderr
        assert not (base / 'negative-state').exists()
        assert not sentinel.exists()
        print(f'PASS isolated uv Git install at local fixture {pin}; PyYAML {dependencies}; outside checkout')
        print('PASS dry-run tripwires, legacy wrappers, mismatch and missing VCS provenance; no native operations')


if __name__ == '__main__':
    smoke(inventory())
