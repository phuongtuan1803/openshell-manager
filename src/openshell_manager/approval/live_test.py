"""Bounded real protocol test using the existing bridge, never an approval engine."""
import base64
import fcntl
import getpass
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
import tomllib
import warnings
from pathlib import Path

from .. import configuration, control, provenance
from ..assets import ROOT, RESOURCES
from .native import GatewayRPC, NativeAdapter, NativeUnavailable, NativeStale, digest
from .store import private
from .telegram import Telegram, credentials
from .service import bridge_lock


class Blocked(ValueError):
    pass


class Failed(ValueError):
    pass


def need(condition, message):
    if not condition:
        raise Blocked(message)


def say(status, text):
    print(f'[{status}] {text}', flush=True)


def run(args, timeout=60, *, env=None):
    """No child stdin, shell, arbitrary output or inherited terminal recording."""
    result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                            check=False, timeout=timeout, env=env)
    need(result.returncode == 0, 'Host command failed; check the pinned tools and existing local Gateway. No output disclosed.')
    return result.stdout


def wait_for(check, seconds, message, clock=time.monotonic, sleep=time.sleep):
    end = clock() + seconds
    while True:
        result = check()
        if result:
            return result
        if clock() >= end:
            raise Failed(message)
        sleep(1)


def select_gateway(requested=None):
    base = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'openshell/gateways'
    if requested:
        names = [requested]
    elif (base / 'openshell').exists():
        names = ['openshell']
    else:
        names = [p.name for p in base.iterdir() if p.is_dir()] if base.is_dir() else []
        need(len(names) == 1, 'No unique existing local Gateway. Use --gateway NAME; no Gateway will be created.')
    rpc = None
    try:
        rpc = GatewayRPC(names[0])
        health = rpc.call('Health')
        need(health.version == '0.1.2' and health.status == 1, 'Gateway must be healthy OpenShell 0.1.2; no upgrade performed.')
        return names[0], rpc
    except (NativeUnavailable, OSError, ValueError):
        if rpc is not None:
            rpc.close()
        raise Blocked('Existing Gateway metadata/mTLS/health failed validation. Require local HTTPS, owned 0700 mtls and 0600 certificates/key; no credentials changed.') from None


def reviewed_image(state_root):
    """Require existing review receipt AND the shipped recipe label AND actual digest."""
    recipe = control.digest((RESOURCES / 'docker/Dockerfile').read_bytes())
    version = configuration.file(configuration.DEFAULTS)['codex_version']
    candidates = [state_root / 'built-image-review.json', *sorted(state_root.glob('*/built-image-review.json'))]
    matches = set()
    for path in candidates:
        if not path.exists():
            continue
        try:
            receipt = configuration.file(private(path))
            image = receipt.get('image_id', '')
            if (not re.fullmatch(r'sha256:[a-f0-9]{64}', image) or image == 'sha256:' + '0' * 64
                    or receipt.get('dockerfile_sha256') != recipe or receipt.get('codex_version') != version):
                continue
            info = json.loads(run(['docker', 'image', 'inspect', image]))
            if (len(info) == 1 and info[0]['Id'] == image
                    and info[0]['Config'].get('Labels', {}).get('manager.dockerfile') == recipe):
                matches.add(image)
        except (ValueError, OSError, KeyError, TypeError):
            continue
    need(len(matches) == 1, 'Require one locally available reviewed image: private built-image-review.json under ~/.local/state/openshell-manager[/PROJECT], matching the shipped Dockerfile, pinned Codex and Docker digest/label. No image is built or guessed.')
    return matches.pop()


def bot_config(path):
    need(not any((parent / '.git').is_file() or (parent / '.git/HEAD').is_file() for parent in path.parents),
         'Bot configuration must be outside every Git checkout and guest source tree.')
    if path.exists():
        auth = credentials(path)
        bot = Telegram(auth['bot_token'])
        identity = bot.call('getMe')
        need(isinstance(identity, dict) and identity.get('is_bot') is True, 'Configured token does not identify a bot.')
        webhook = bot.call('getWebhookInfo')
        need(isinstance(webhook, dict) and not webhook.get('url'), 'Bot has an existing webhook; use a dedicated long-poll bot.')
        return auth
    need(sys.stdin.isatty(), 'First-run bot setup requires an interactive terminal; existing private bot JSON can be supplied with --bot-config.')
    print('Create a dedicated bot with https://t.me/BotFather (/newbot) if needed. Do not reuse OMA M11 Gate.', flush=True)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            token = getpass.getpass('Dedicated bot token (hidden): ')
    except (getpass.GetPassWarning, EOFError):
        raise Blocked('Secure token entry unavailable; no echoed fallback or credential save is allowed.') from None
    need(bool(re.fullmatch(r'[0-9]{5,20}:[A-Za-z0-9_-]{20,100}', token)), 'Invalid bot token format; nothing saved.')
    bot = Telegram(token)
    identity = bot.call('getMe')
    need(isinstance(identity, dict), 'Invalid Telegram bot identity response.')
    username = identity.get('username', '')
    need(isinstance(identity, dict) and identity.get('is_bot') is True and re.fullmatch(r'[A-Za-z0-9_]{5,64}', username), 'Telegram did not identify a valid bot.')
    webhook = bot.call('getWebhookInfo')
    need(isinstance(webhook, dict) and not webhook.get('url'), 'Bot has an existing webhook; use a dedicated long-poll bot.')
    challenge = secrets.token_hex(16)
    print(f'Open https://t.me/{username} and send: /start {challenge}', flush=True)
    offset = 0

    def receive():
        nonlocal offset
        updates = bot.call('getUpdates', offset=offset, timeout=5, allowed_updates=['message'])
        need(isinstance(updates, list), 'Invalid Telegram enrollment response.')
        for update in updates:
            if not isinstance(update, dict) or type(update.get('update_id')) is not int:
                continue
            offset = max(offset, update['update_id'] + 1)
            message = update.get('message', {})
            if not isinstance(message, dict):
                continue
            sender, chat = message.get('from', {}), message.get('chat', {})
            if not isinstance(sender, dict) or not isinstance(chat, dict):
                continue
            if (message.get('text') == '/start ' + challenge and chat.get('type') == 'private'
                    and sender.get('is_bot') is False and type(sender.get('id')) is int
                    and sender['id'] > 0 and type(chat.get('id')) is int and chat['id'] == sender['id']):
                return {'bot_token': token, 'user_id': sender['id'], 'chat_id': chat['id']}
        return None

    auth = wait_for(receive, 300, 'Telegram /start enrollment timed out after 5 minutes; token was not saved.')
    need(input(f"Confirm your Telegram user_id={auth['user_id']}, chat_id={auth['chat_id']} (yes/no): ").strip().lower() == 'yes',
         'Telegram identity not confirmed; token was not saved.')
    # Confirmed enrollment is acknowledged before the bridge becomes the only poller.
    bot.call('getUpdates', offset=offset, timeout=0, allowed_updates=['callback_query'])
    private(path.parent, directory=True)
    private(path, create=True)
    control.save(path, auth)
    credentials(path)
    return auth


class LiveTest:
    def __init__(self, directory, state_root, gateway, workspace, rpc, revision):
        self.directory, self.state_root = directory, state_root
        self.gateway, self.workspace, self.rpc, self.revision = gateway, workspace, rpc, revision
        self.started_at = time.time()
        self.nonce = secrets.token_hex(16)
        self.project_id = 'tg-' + self.nonce[:10]
        self.name = self.project_id + '-dev'
        self.resource_id = None
        self.create_attempted = False
        self.child = None
        self.grant_possible = False
        self.cleaned = False
        self.results = {}
        self.image = None
        self.project = directory / 'project.json'
        self.host = directory / 'host.json'
        self.cfg = None
        self.native_config = None
        self.native_env = None

    def cli(self, *args, timeout=60):
        return run(['openshell', '--gateway', self.gateway, '--workspace', self.workspace, *args], timeout, env=self.native_env)

    def isolate_cli_registry(self):
        # Native CLI create/exec writes last_sandbox. Never change the real registry.
        need(not any((p / '.git').is_file() or (p / '.git/HEAD').is_file() for p in self.directory.parents),
             'Private test state must be outside Git before host TLS materials are reused.')
        source = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'openshell/gateways' / self.gateway
        metadata = configuration.file(source / 'metadata.json')
        private(source / 'mtls', directory=True)
        materials = {name: private(source / 'mtls' / name).read_bytes() for name in ('ca.crt', 'tls.crt', 'tls.key')}
        need(metadata.get('name') == self.gateway and metadata.get('auth_mode') in {None, 'mtls'}
             and not metadata.get('is_remote') and digest({'gateway': self.gateway,
                 'endpoint': metadata.get('gateway_endpoint'),
                 'ca': hashlib.sha256(materials['ca.crt']).hexdigest()}) == self.rpc.identity,
             'Gateway identity changed before private CLI binding; no lifecycle action permitted.')
        self.native_config = private(self.directory / 'native-config', directory=True, create=True)
        target = self.native_config
        for name in ('openshell', 'gateways', self.gateway):
            target = private(target / name, directory=True, create=True)
        control.save(target / 'metadata.json', metadata)
        tls = private(target / 'mtls', directory=True, create=True)
        for name, content in materials.items():
            private(tls / name, create=True).write_bytes(content)
        self.native_env = dict(os.environ, XDG_CONFIG_HOME=str(self.native_config))

    def remove_cli_registry(self):
        if self.native_config is not None and self.native_config.exists():
            private(self.native_config, directory=True)
            need(self.native_config == self.directory / 'native-config', 'Unexpected temporary registry location; retained for review.')
            shutil.rmtree(self.native_config)
        self.native_env = None

    def listing(self):
        entries, token = [], ''
        for _ in range(100):
            page = json.loads(self.cli('sandbox', 'list', '--page-size', '1000', '--page-token', token, '-o', 'json'))
            need(isinstance(page.get('sandboxes'), list), 'Incomplete native sandbox list; isolation cannot be established.')
            entries.extend(page['sandboxes'])
            token = page.get('next_page_token')
            need(isinstance(token, str), 'Incomplete native pagination; refusing lifecycle operations.')
            if not token:
                return entries
        raise Blocked('Native sandbox pagination exceeded the test limit; no adoption performed.')

    def owned(self):
        need(re.fullmatch(r'tg-[a-f0-9]{10}-dev', self.name), 'Target is not a dedicated test sandbox.')
        sandbox = self.rpc.call('GetSandbox', name=self.name, workspace_scope={'workspace': self.workspace}).sandbox
        meta = sandbox.metadata
        need(meta.name == self.name and meta.workspace == self.workspace and meta.id
             and meta.labels.get('manager.approval-test') == self.nonce
             and meta.labels.get('manager.project') == self.project_id
             and meta.labels.get('manager.phase') == '2'
             and meta.labels.get('manager.image') == self.image.split(':')[1][:32]
             and (self.resource_id is None or meta.id == self.resource_id),
             'Sandbox ownership/UUID changed; no exec, settings or deletion permitted.')
        return sandbox

    def journal(self):
        control.save(self.directory / 'result.json', {'sandbox': self.name, 'sandbox_id': self.resource_id,
            'run_marker': self.nonce, 'manager_revision': self.revision, 'gateway': self.gateway,
            'started_at': self.started_at, 'updated_at': time.time(), 'reviewed_image_id': self.image,
            'workspace': self.workspace, 'create_attempted': self.create_attempted,
            'runtime_grant_possible': self.grant_possible, 'native_resource_absent': self.cleaned,
            'checks': self.results})

    def prepare(self, image):
        self.image = image
        self.isolate_cli_registry()
        fixture = private(self.directory / 'fixture', directory=True, create=True)
        (fixture / 'probe.py').write_text('def test_fixture():\n    assert True\n')
        run(['git', 'init', '-q', '-b', 'main', str(fixture)])
        run(['git', '-C', str(fixture), 'remote', 'add', 'origin', 'https://github.com/phuongtuan1803/openshell-manager.git'])
        run(['git', '-C', str(fixture), 'add', 'probe.py'])
        run(['git', '-C', str(fixture), '-c', 'core.hooksPath=/dev/null', '-c', 'user.name=Approval Test',
             '-c', 'user.email=test@example.invalid', '-c', 'commit.gpgsign=false', 'commit', '-qm', 'Isolated protocol fixture'])
        for name in ('source.txt', 'edit.txt'):
            (self.directory / name).write_text('probe.py\n')
        control.save(self.project, {'id': self.project_id, 'repository': 'phuongtuan1803/openshell-manager',
            'root': str(fixture), 'sandbox': self.name, 'workspace': '/sandbox/' + self.project_id + '/checkout',
            'source_manifest': str(self.directory / 'source.txt'), 'edit_scope': str(self.directory / 'edit.txt'),
            'base_branch': 'main', 'test_paths': ['probe.py'], 'image': 'approval-test:reviewed',
            'image_id': image, 'manager_revision': self.revision, 'rules': []})
        control.save(self.host, {'state_root': str(self.state_root)})
        control.configure(self.project, self.host)
        self.cfg = configuration.load(self.project, self.host)
        need(not any(item.get('name') == self.name for item in self.listing()),
             'Dedicated test name already exists; refusing adoption.')
        baseline = control.base_policy()
        policy = self.directory / 'bootstrap-policy.json'
        control.save(policy, baseline)
        control.image(check=True)  # Reuse the existing Manager digest verification.
        self.create_attempted = True
        self.journal()  # Durable ownership intent before any native creation.
        self.cli('sandbox', 'create', '--name', self.name, '--from', image, '--policy', str(policy),
            '--no-auto-providers', '--approval-mode', 'manual', '--label', 'manager.phase=2',
            '--label', 'manager.project=' + self.project_id, '--label', 'manager.image=' + image.split(':')[1][:32],
            '--label', 'manager.approval-test=' + self.nonce, '--detach', '--no-tty', '-o', 'json',
            '--', '/bin/sleep', 'infinity', timeout=600)
        self.resource_id = self.owned().metadata.id
        self.journal()
        wait_for(lambda: self.owned().status.phase == 2, 180, 'Isolated sandbox did not reach Ready within 180 seconds.')
        # Supported sandbox-local opt-in only; no global/Gateway setting is changed.
        self.owned()
        self.cli('settings', 'set', self.name, '--key', 'agent_policy_proposals_enabled', '--value', 'true')
        def configured():
            status = self.owned().status
            return status.phase == 2 and status.configuration_admission.state == 2
        wait_for(configured, 120, 'Owned sandbox proposal configuration was not admitted within 120 seconds.')
        data = json.loads(self.execute('/usr/bin/python3', '-I', '-c', (ROOT / 'guest.py').read_text(),
                                      base64.b64encode(b'{"action":"tools"}').decode()))
        need(data.get('uid', 0) > 0 and data.get('version') == self.cfg['codex_version'], 'Reviewed image guest tools/UID do not match pinned Manager requirements.')
        control.save(self.cfg['state'] / 'state.json', {'sandbox_id': self.resource_id,
            'config_hash': control.config_hash(), 'tools': data, 'policy': baseline, 'snapshot': None})
        self.adapter = NativeAdapter(self.cfg, self.rpc, self.workspace)
        def admitted():
            try:
                config, _ = self.adapter.context()
                need(not config.policy.network_policies, 'Test bootstrap is not deny-all; refusing adoption.')
                return True
            except NativeUnavailable:
                return False
        wait_for(admitted, 120, 'Isolated sandbox policy/admission/MANUAL state could not be verified within 120 seconds.')
        say('PASS', 'Isolated test sandbox ready')

    def execute(self, *command):
        self.owned()
        return self.cli('sandbox', 'exec', '--name', self.name, '--no-login-shell', '--no-tty',
                        '--timeout', '35', '--workdir', '/sandbox', '--', *command, timeout=45)

    def probe(self, action, **fields):
        value = json.loads(self.execute('/usr/bin/python3', '-I', '-c', (ROOT / 'approval/probe.py').read_text(),
                                        json.dumps({'action': action, **fields})))
        if value.get('error'):
            raise Blocked('Guest native proposal API/curl unavailable. Only this owned test sandbox was configured; inspect native proposal support without changing the Gateway.')
        return value

    def start_bridge(self, bot_path):
        self.child = subprocess.Popen([sys.executable, '-I', '-m', 'openshell_manager', 'approval', 'serve',
            '--project', str(self.project), '--host', str(self.host), '--bot-config', str(bot_path),
            '--gateway', self.gateway, '--gateway-workspace', self.workspace],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, cwd=self.directory)
        def healthy():
            self.running()
            metadata = self.queue_metadata()
            health = json.loads(metadata.get('native_health', '{}'))
            return health.get('status') == 'verified'
        wait_for(healthy, 90, 'Native bridge not verified within 90 seconds; check existing mTLS/binding/MANUAL state.')
        say('PASS', 'Native approval bridge verified')

    def running(self):
        if self.child is not None and self.child.poll() is not None:
            raise Failed('Approval bridge exited; check project provenance, bot ownership and private state. Child output suppressed.')

    def db(self):
        path = self.cfg['state'] / 'approval/queue.sqlite3'
        if not path.exists():
            return None
        private(path)
        db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    def queue_metadata(self):
        db = self.db()
        if db is None:
            return {}
        try:
            return dict(db.execute('SELECT key,value FROM metadata'))
        finally:
            db.close()

    def request(self, chunk):
        self.running()
        db = self.db()
        if db is None:
            return None
        try:
            for row in db.execute('SELECT * FROM requests WHERE kind="native" ORDER BY id DESC'):
                if json.loads(row['snapshot'])['chunk_id'] == chunk:
                    return dict(row)
        finally:
            db.close()
        return None

    def verify_probe(self, snapshot, name, path):
        rule = json.loads(snapshot['scope'])
        endpoints = rule.get('endpoints', [])
        need(snapshot['eligible'] and rule.get('name') == name
             and rule.get('binaries') == [{'path': '/usr/bin/curl'}] and len(endpoints) == 1
             and endpoints[0].get('host') == 'example.com'
             and endpoints[0].get('rules') == [{'allow': {'method': 'GET', 'path': path}}],
             'Exact native proposal does not match the submitted test rule or has findings; approval refused.')

    def submit(self, name, path):
        result = self.probe('submit', name=name, path=path)
        ids = result.get('accepted_chunk_ids')
        need(not result.get('refused') and isinstance(ids, list) and len(ids) == 1
             and isinstance(ids[0], str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', ids[0]),
             'Native proposal submission did not accept exactly one chunk; no identity invented.')
        try:
            snapshot = self.adapter.get(self.name, ids[0])
        except NativeStale:
            # A fast Founder may decide from Telegram before this parent reads pending.
            # Accept only the existing bridge's durable, natively reconfirmed decision.
            def terminal():
                row = self.request(ids[0])
                return row if row and row['status'] in {'approved', 'rejected'} else None
            row = wait_for(terminal, 30, 'Proposal left pending before the bridge could confirm an exact decision.')
            snapshot = json.loads(row['snapshot'])
            self.adapter.confirm(snapshot, row['action'])
        self.verify_probe(snapshot, name, path)
        return ids[0]

    def decision(self, chunk, action):
        def notified():
            row = self.request(chunk)
            if row and row['status'] in {'stale', 'expired', 'needs_review'}:
                raise Failed('Proposal expired, changed or needs host review; no automatic decision retry.')
            return row and row['status'] in {'notified', 'applying', 'approved', 'rejected'}
        wait_for(notified, 90, 'No automatic Telegram notification within 90 seconds; check the dedicated bot connection.')
        say('WAIT', 'Proposal sent to Telegram. Open Telegram and click ' + ('APPROVE RULE.' if action == 'approve' else 'REJECT.'))
        if action == 'approve':
            self.grant_possible = True
            self.journal()
        def decided():
            row = self.request(chunk)
            if row and row['status'] in {'stale', 'expired', 'needs_review'}:
                raise Failed('Decision stale/expired/uncertain; no native mutation will be replayed.')
            if row and row['status'] in {'approved', 'rejected'}:
                expected = 'approved' if action == 'approve' else 'rejected'
                if row['status'] != expected:
                    raise Failed('Telegram decision differs from the requested test step; preserving actual native outcome.')
                try:
                    self.adapter.confirm(json.loads(row['snapshot']), action)
                except NativeUnavailable:
                    raise Failed('Native decision/policy changed or cannot be confirmed; no success or mutation retry assumed.') from None
                return row
            return None
        row = wait_for(decided, 840, 'Founder decision timed out after 14 minutes; no approval assumed.')
        waited = self.probe('wait', chunk=chunk)
        if action == 'approve' and waited.get('status') == 'approved' and not waited.get('policy_reloaded'):
            waited = wait_for(lambda: self.loaded(chunk), 30, 'Native approval confirmed but guest policy did not reload within 30 seconds.')
        need(waited.get('status') == row['status'] and (waited.get('policy_reloaded') is True if action == 'approve' else waited.get('has_rejection_reason')),
             'Native guest wait result does not confirm the required decision/load/reason.')
        say('PASS', 'Native approval confirmed' if action == 'approve' else 'Native rejection confirmed')
        if action == 'approve':
            say('PASS', 'Policy loaded')
        self.results[action] = {'chunk_id': chunk, 'status': row['status'],
            'before_policy_hash': json.loads(row['snapshot'])['policy_hash'],
            'native_confirmation': json.loads(row['native_result']), 'guest_wait': waited,
            'confirmed_at': time.time()}
        self.journal()
        return row

    def loaded(self, chunk):
        value = self.probe('wait', chunk=chunk)
        return value if value.get('status') == 'approved' and value.get('policy_reloaded') is True else None

    def workflow(self):
        approved = self.submit('telegram_probe_a', '/')
        self.decision(approved, 'approve')
        retry = self.probe('probe', path='/')
        if retry != {'http_status': '200', 'exit_code': 0, 'policy_denied': False}:
            raise Failed('Approved GET did not return HTTP 200; DNS/TLS/upstream failure is not a successful network retry.')
        say('PASS', 'Network retry permitted')
        self.results['network_retry'] = 'HTTP 200'
        denied_path = '/pr3b-' + self.nonce
        rejected = self.submit('telegram_probe_b', denied_path)
        self.decision(rejected, 'reject')
        denial = self.probe('probe', path=denied_path)
        if denial.get('http_status') != '403' or not denial.get('policy_denied'):
            raise Failed('Rejection probe lacks an explicit native policy_denied response; no denial success inferred from network failure.')
        say('PASS', 'Network request remains denied')
        self.results['network_denial'] = 'native policy_denied / HTTP 403'
        # Never forge Telegram callbacks or recover unhashed tokens to manufacture replay evidence.
        say('SKIP', 'Live callback replay injection; offline replay/concurrency coverage retained')
        self.results['live_replay'] = 'SKIP: no synthetic callback injection'
        result = self.probe('submit', name='telegram_probe_b', path=denied_path)
        ids = result.get('accepted_chunk_ids', [])
        if (not result.get('refused') and isinstance(ids, list) and len(ids) == 1
                and isinstance(ids[0], str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', ids[0])
                and ids[0] not in {approved, rejected}):
            repeated = self.adapter.get(self.name, ids[0])
            self.verify_probe(repeated, 'telegram_probe_b', denied_path)
            wait_for(lambda: (self.request(ids[0]) or {}).get('status') == 'notified', 90,
                     'New native request with identical scope did not receive a fresh notice.')
            say('PASS', 'Repeated scope received a new native identity and notification; no third decision needed')
            self.results['repeated_request'] = {'status': 'PASS', 'chunk_id': ids[0]}
        else:
            say('SKIP', 'Native refused/deduplicated repeated proposal; no new identity invented')
            self.results['repeated_request'] = 'SKIP: native refused/deduplicated'
        self.journal()

    def cleanup(self):
        ok = True
        if self.child is not None:
            try:
                self.child.terminate()
                self.child.wait(timeout=45)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(timeout=10)
                say('WARN', 'Bridge required forced stop; native outcome may need review')
            except OSError:
                ok = False
        if self.create_attempted:
            try:
                entries = self.listing()
                if any(item.get('name') == self.name for item in entries):
                    sandbox = self.owned()
                    self.resource_id = sandbox.metadata.id
                    self.journal()
                    self.cli('sandbox', 'delete', self.name, timeout=120)
                wait_for(lambda: not any(item.get('name') == self.name or item.get('id') == self.resource_id for item in self.listing()),
                         120, 'Native sandbox deletion not confirmed within 120 seconds.')
                self.cleaned = True
                say('PASS', 'Owned test sandbox confirmed absent from native inventory')
            except (ValueError, OSError, subprocess.SubprocessError):
                ok = False
                say('FAIL', 'Test sandbox cleanup unconfirmed; retain the private ownership report. Existing sandboxes were not targeted.')
        self.journal()
        return ok


def main(args):
    print('OpenShell Telegram Approval — E2E Test\n', flush=True)
    test = rpc = lock = None
    result, code = 'BLOCKED', 2
    try:
        need(not any(k.startswith('OPENSHELL_') for k in os.environ), 'Remove OpenShell environment overrides; explicit existing Gateway selection is required.')
        revision, _ = provenance.identity()
        provenance.verify(revision)
        say('PASS', 'Manager revision verified')
        need(platform.system() == 'Linux' and platform.machine() == 'x86_64', 'Require the supported Linux x86_64 host.')
        for tool in ('openshell', 'openshell-gateway', 'docker', 'git', 'python3'):
            need(shutil.which(tool), f'Missing host tool: {tool}; no installer or service change will run.')
        need(run(['openshell', '--version']).strip() == 'openshell 0.1.2', 'Require OpenShell 0.1.2; no upgrade performed.')
        need(re.search(r'(?<![0-9.])0\.1\.2(?![0-9.])', run(['openshell-gateway', '--version'])), 'Gateway binary version must be 0.1.2.')
        exec_help = run(['openshell', 'sandbox', 'exec', '--help'])
        need(all(flag in exec_help for flag in ('--no-login-shell', '--no-tty', '--timeout', '--name')),
             'Pinned CLI lacks supported noninteractive sandbox execution flags; no test sandbox created.')
        need(os.access('/dev/kvm', os.R_OK | os.W_OK), 'Host user requires existing read/write KVM access; permissions will not be changed.')
        run(['docker', 'info', '--format', '{{.ServerVersion}}'])
        gateway_config = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'openshell/gateway.toml'
        need(gateway_config.is_file(), 'Existing VM Gateway config is missing; no provisioning outside the test sandbox.')
        native_config = tomllib.loads(gateway_config.read_text())['openshell']
        need(native_config['version'] == 2 and native_config['gateway']['compute_driver'] == 'vm'
             and native_config['gateway']['bind_address'] == '127.0.0.1:17670', 'Require the existing reviewed loopback VM Gateway configuration; no settings changed.')
        driver = Path(native_config['drivers']['vm']['driver_dir']) / 'openshell-driver-vm'
        need(driver.is_file() and os.access(driver, os.X_OK) and re.search(r'(?<![0-9.])0\.1\.2(?![0-9.])', run([str(driver), '--version'])),
             'Existing executable VM driver must be version 0.1.2.')
        gateway, rpc = select_gateway(args.gateway)
        say('PASS', 'Gateway: ' + gateway)
        say('PASS', 'mTLS credentials verified')
        state_root = Path.home() / '.local/state/openshell-manager'
        state_root.mkdir(parents=True, mode=0o700, exist_ok=True)
        private(state_root, directory=True)
        live_root = private(state_root / 'approval-live', directory=True, create=True)
        lock_path = private(live_root / 'test.lock', create=True)
        lock = lock_path.open('r+')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Blocked('Another one-command approval test is active; no test resources created.') from None
        image = reviewed_image(state_root)
        # Do not consume bot updates while a Manager bridge sharing the canonical root is active.
        with bridge_lock(state_root):
            known_bot_paths = [live_root / 'telegram.json', Path.home() / '.config/openshell-manager/telegram.json',
                               state_root / 'telegram.json']
            auth_path = (args.bot_config or next((p for p in known_bot_paths if p.exists()), known_bot_paths[0])).absolute()
            if auth_path.parent != live_root:
                need(auth_path.exists(), 'An explicit --bot-config must already exist privately; default first-run enrollment needs no path.')
            bot_config(auth_path)
        say('PASS', 'Telegram bot connected')
        cli_prefix = ['openshell', '--gateway', gateway, '--workspace', args.gateway_workspace]
        workspaces = json.loads(run([*cli_prefix, 'workspace', 'list', '-o', 'json']))
        rows = workspaces.get('workspaces', [])
        need(not workspaces.get('next_page_token'), 'Workspace list is paginated; explicit discovery cannot be completed safely.')
        selected = next((w['name'] for w in rows if w.get('name') == args.gateway_workspace), None)
        if selected is None and args.gateway_workspace == 'default' and len(rows) == 1:
            selected = rows[0].get('name')
        need(isinstance(selected, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', selected), 'No unique existing workspace; use --gateway-workspace NAME. No workspace created.')
        directory = private(live_root / secrets.token_hex(16), directory=True, create=True)
        test = LiveTest(directory, state_root, gateway, selected, rpc, revision)
        test.prepare(image)
        test.start_bridge(auth_path)
        test.workflow()
        result, code = 'PASS', 0
    except KeyboardInterrupt:
        result, code = 'FAIL', 130
        say('FAIL', 'Interrupted; stopping the bridge and cleaning only owned test resources')
    except Failed as error:
        result, code = 'FAIL', 1
        say('FAIL', str(error))
    except Blocked as error:
        say('BLOCKED', str(error))
    except (ValueError, OSError, subprocess.SubprocessError, TypeError, KeyError, EOFError):
        say('BLOCKED', 'Private configuration, transport or host command failed validation. No raw exception/credential output disclosed; check the one-command runbook.')
    finally:
        if test is not None:
            if test.grant_possible:
                say('INFO', 'A runtime rule may have been granted in the owned test sandbox; bridge stop alone does not revoke it')
            try:
                if not test.cleanup():
                    result, code = 'FAIL', 1
            except (KeyboardInterrupt, ValueError, OSError, subprocess.SubprocessError):
                result, code = 'FAIL', 1
                say('FAIL', 'Cleanup interrupted/unconfirmed; preserve the private test ownership record')
            print('Private test record: ' + str(test.directory / 'result.json'), flush=True)
        if test is not None:
            try:
                test.remove_cli_registry()
            except (KeyboardInterrupt, ValueError, OSError):
                result, code = 'FAIL', 1
                say('FAIL', 'Temporary private CLI credential cleanup unconfirmed; preserve and review the private run directory')
            test.results['outcome'] = result
            try:
                test.journal()
            except (ValueError, OSError):
                result, code = 'FAIL', 1
                say('FAIL', 'Final private report could not be saved; no cleanup success inferred')
        if rpc is not None:
            rpc.close()
        if lock is not None:
            lock.close()
    print('\nRESULT: ' + result, flush=True)
    return code
