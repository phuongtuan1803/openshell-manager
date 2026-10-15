"""Explicit native Claude/OpenCode preparation in the existing project sandbox.

No image build, sandbox creation, account copying or global live-gate changes.
"""

import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import agents, control


def receipt_path(c, name):
    return c.STATE / 'prepared' / f'{name}-installed.json'


def guest(c, receipt, action, *, interactive=False, **fields):
    payload = base64.b64encode(json.dumps({**receipt, **fields, 'action': action}).encode()).decode()
    argv = ['/usr/bin/python3', '-I', '-c', (c.HERE / 'agent_guest.py').read_text(), payload]
    if action == 'start':
        return c.session('launch', argv=argv, interactive=True)
    result = c.execute(*argv, interactive=interactive, timeout=60)
    return result if interactive else json.loads(result.stdout)


def idle_guest(c):
    probe = (c.HERE / 'session.py').read_text().split('if __name__ == "__main__":')[0]
    result = c.execute('/usr/bin/python3', '-I', '-c', probe + '\nprint(int(unmanaged_agent()))')
    c.require(result.stdout.strip() == '0', 'Active or unreadable guest agent; operation refused')


def load(c, name, data):
    path = receipt_path(c, name)
    c.require(path.is_file(), 'Agent is not prepared. Founder approval required: run agent prepare first')
    receipt = c.read(path)
    version = agents.adapter(name).version
    c.require(set(receipt) == {'agent', 'version', 'binary', 'sha256', 'home', 'sandbox_id'},
              'Invalid native agent receipt')
    c.require(receipt['agent'] == name and receipt['version'] == version
              and receipt['sandbox_id'] == data['sandbox_id']
              and receipt['binary'] == f'/sandbox/tools/{name}/{version}/{name}'
              and receipt['home'] == f'/sandbox/{name}-home'
              and re.fullmatch(r'[0-9a-f]{64}', receipt['sha256']),
              'Agent pin or sandbox identity drift; preparation review required')
    actual = guest(c, receipt, 'inspect')
    c.require(actual == {key: value for key, value in receipt.items() if key != 'sandbox_id'},
              'Guest executable differs from installation receipt')
    return receipt


def prepare(c, name, binary, expected_hash, data):
    c.require(binary is not None and re.fullmatch(r'[0-9a-f]{64}', expected_hash or ''),
              'prepare requires --binary and the reviewed --sha256')
    binary = Path(binary).absolute()
    c.require(binary.resolve() == binary and binary.is_file(), 'Use a regular native binary without symlinks')
    info = binary.stat()
    c.require(stat.S_ISREG(info.st_mode) and 20 <= info.st_size <= 512 * 1024 * 1024,
              'Invalid native executable size or type')
    with binary.open('rb') as source:
        header = source.read(20)
        c.require(header[:4] == b'\x7fELF' and header[18:20] == b'\x3e\x00',
                  'Supply only a Linux x86_64 native executable, never account configuration')
        source.seek(0)
        c.require(hashlib.file_digest(source, 'sha256').hexdigest() == expected_hash,
                  'Reviewed host executable checksum mismatch')
    version = agents.adapter(name).version
    receipt = {'agent': name, 'version': version, 'binary': f'/sandbox/tools/{name}/{version}/{name}',
               'sha256': expected_hash, 'home': f'/sandbox/{name}-home', 'sandbox_id': data['sandbox_id']}
    path = receipt_path(c, name)
    if path.exists():
        c.require(c.read(path) == receipt, 'Existing agent receipt differs; preserve it for migration review')
        return load(c, name, data)
    idle_guest(c)
    guest(c, receipt, 'directories')
    upload = str(Path(receipt['binary']).parent / ('.upload-' + uuid.uuid4().hex))
    c.oshell('sandbox', 'upload', c.NAME, str(binary), upload, timeout=300)
    installed = guest(c, receipt, 'install', upload=upload)
    c.require(installed == {key: value for key, value in receipt.items() if key != 'sandbox_id'},
              'Installed agent verification failed')
    c.save(path, receipt)
    print(f'PASS: {name} {version} prepared in existing sandbox {c.NAME}; no model call or network grant')
    return receipt


def required_hosts(c, name, model=None):
    if name == 'claude':
        return ('claude.ai', 'platform.claude.com', 'api.anthropic.com')
    agents.adapter(name).model_args(model)
    c.require(model is not None, 'OpenCode needs --model provider/model before endpoint review or launch')
    provider = model.split('/', 1)[0]
    template = c.read(c.PROJECT_ROOT / 'config/policies/opencode.json')['providers']
    c.require(provider in template, 'Selected OpenCode provider needs a reviewed endpoint template')
    return tuple(endpoint['host'] for endpoint in template[provider]['endpoints'])


def network_rules(c, receipt, data, model=None):
    rules = []
    for host in required_hosts(c, receipt['agent'], model):
        identity = f"{receipt['agent']}:{receipt['binary']}:{host}"
        identifier = 'review_' + hashlib.sha256(identity.encode()).hexdigest()[:12]
        rule = {'id': identifier, 'host': host, 'port': 443, 'access': 'full',
                'binaries': [receipt['binary']]}
        c.validate_rule(rule)
        existing = data['policy'].get('network_policies', {}).get(identifier)
        c.require(existing is None or existing == c.network_rule(rule), 'Existing agent network rule drift')
        if existing is None:
            rules.append(rule)
    return rules


@contextmanager
def task_lease(c):
    path = c.STATE / 'task.lock'
    c.require(not path.is_symlink(), 'Symlink task lock')
    with path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Active session/task; agent launch refused') from None
        yield


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'status', 'network', 'approve-network', 'login', 'start'])
    parser.add_argument('--agent', required=True, choices=['claude', 'opencode'])
    parser.add_argument('--binary', type=Path)
    parser.add_argument('--sha256')
    parser.add_argument('--model')
    parser.add_argument('--project', type=Path, default=os.environ.get('MANAGER_PROJECT'))
    parser.add_argument('--host', type=Path, default=os.environ.get('MANAGER_HOST'))
    args = parser.parse_args(argv)
    c = control
    os.umask(0o077)
    try:
        c.require(args.project is not None, 'Supply --project or MANAGER_PROJECT')
        c.require(not any(key.startswith('OPENSHELL_') for key in os.environ),
                  'OpenShell environment overrides are not allowed')
        c.configure(args.project, args.host)
        interactive = args.action in {'login', 'start'}
        with c.setup_lock('apply' if not interactive else 'check'), c.idle_lease(not interactive):
            data = c.state()
            c.effective(data['policy'])
            if args.action == 'prepare':
                prepare(c, args.agent, args.binary, args.sha256, data)
                return 0
            receipt = load(c, args.agent, data)
            if args.action == 'status':
                print(json.dumps(guest(c, receipt, 'status'), indent=2))
                return 0
            missing = network_rules(c, receipt, data, args.model)
            if args.action == 'network':
                print(json.dumps({'sandbox': c.NAME, 'agent': args.agent, 'missing': missing,
                                  'network_applied': False}, indent=2))
                return 0
            if args.action == 'approve-network':
                idle_guest(c)
                for rule in missing:
                    c.update(data, rule)  # Native dry run, explicit Founder confirmation and journal.
                return 0
            c.require(not missing, 'Missing reviewed agent HTTPS permission. Run agent network, '
                      'then agent approve-network in a Founder terminal before login/start')
            with task_lease(c):
                idle_guest(c)
                if args.action == 'login':
                    print('Complete subscription/provider login in this private terminal; do not paste codes in chat.')
                    guest(c, receipt, 'login', interactive=True)
                else:
                    if args.agent == 'claude':
                        c.require(guest(c, receipt, 'status')['authenticated'],
                                  'Claude subscription login required; run agent login --agent claude')
                    guest(c, receipt, 'start', model_args=agents.adapter(args.agent).model_args(args.model))
            return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        message = str(error) if isinstance(error, ValueError) and not isinstance(error, json.JSONDecodeError) else type(error).__name__
        print('FAIL: ' + message, file=sys.stderr)
        return 1
