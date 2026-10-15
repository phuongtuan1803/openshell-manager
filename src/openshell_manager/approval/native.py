"""Version-specific authenticated Gateway adapter. No CLI text or log authority.

The approval RPC always carries one explicit chunk/token pair (never empty).
See docs/native-api-v0.1.2.md for the pinned contract and rejection race limits.
"""
import base64
import fnmatch
import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import grpc
from google.protobuf.json_format import MessageToDict

from .. import configuration, control
from . import schema, diagnostics
from .store import private

BLOCKER = 'BLOCKED — native interface unavailable; Telegram cannot grant permissions. Manual host review required.'


class NativeUnavailable(ValueError):
    pass


class NativeVerification(NativeUnavailable):
    """Only fixed invariant codes escape native verification; no server prose."""
    def __init__(self, code, retryable=False):
        self.code, self.retryable = code, retryable
        super().__init__(f'Native verification: {code} ({"converging" if retryable else "mismatch"})')


class NativeStale(NativeUnavailable):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def wire(message):
    return base64.b64encode(message.SerializeToString(deterministic=True)).decode()


def no_unknown(message):
    copy = type(message)()
    copy.CopyFrom(message)
    copy.DiscardUnknownFields()
    return wire(copy) == wire(message)


def checked(condition):
    if not condition:
        raise NativeUnavailable(BLOCKER)


def redacted(value):
    text = json.dumps(value, sort_keys=True, ensure_ascii=True)
    # Never emit token-like strings, query parameters or free-form native rationale.
    text = re.sub(r'(?i)(?:bearer\s+\S+|(?:sk-|gh[pousr]_|AKIA)[A-Za-z0-9_-]{8,}|\d{5,20}:[A-Za-z0-9_-]{20,})',
                  '[redacted]', text)
    return text


class GatewayRPC:
    """Reuse existing local mTLS materials; no credential creation or settings writes."""
    def __init__(self, gateway):
        checked(isinstance(gateway, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', gateway))
        base = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'openshell/gateways' / gateway
        meta = configuration.file(base / 'metadata.json')
        checked(meta.get('name') == gateway and meta.get('auth_mode') in {None, 'mtls'} and not meta.get('is_remote'))
        endpoint = urlsplit(meta.get('gateway_endpoint', ''))
        checked(endpoint.scheme == 'https' and endpoint.hostname in {'localhost', '127.0.0.1', '::1'}
                and not endpoint.username and not endpoint.password and endpoint.path in {'', '/'}
                and not endpoint.query and not endpoint.fragment and endpoint.port is not None)
        tls = base / 'mtls'
        private(tls, directory=True)
        certs = {name: private(tls / name).read_bytes() for name in ('ca.crt', 'tls.crt', 'tls.key')}
        credentials = grpc.ssl_channel_credentials(certs['ca.crt'], certs['tls.key'], certs['tls.crt'])
        self.channel = grpc.secure_channel(endpoint.netloc, credentials, options=[
            ('grpc.enable_retries', 0), ('grpc.enable_http_proxy', 0),
            ('grpc.max_receive_message_length', 4 * 1024 * 1024)])
        # Server name verification remains enabled. No plaintext/auth override.
        self.stopping = lambda: False
        self.identity = digest({'gateway': gateway, 'endpoint': meta['gateway_endpoint'],
                                'ca': hashlib.sha256(certs['ca.crt']).hexdigest()})

    def close(self):
        self.channel.close()

    def call(self, method, **fields):
        checked(method in schema.METHODS and not self.stopping())
        if method == 'ApproveAllDraftChunks':
            approvals = fields.get('approvals')
            checked(isinstance(approvals, list) and len(approvals) == 1 and isinstance(approvals[0], dict)
                    and fields.get('include_security_flagged') is False
                    and set(approvals[0]) == {'chunk_id', 'review_token'})
        request, response = schema.METHODS[method]
        started, rpc_ref = time.monotonic(), diagnostics.reference(uuid.uuid4())
        diagnostics.emit('native', 'transport_start', method=method.lower(), rpc_ref=rpc_ref, deadline_seconds=10)
        try:
            invoke = self.channel.unary_unary('/openshell.v1.OpenShell/' + method,
                request_serializer=lambda value: value.SerializeToString(deterministic=True),
                response_deserializer=response.FromString)
            result = invoke(request(**fields), timeout=10, wait_for_ready=False)
            diagnostics.emit('native', 'transport_response', method=method.lower(), rpc_ref=rpc_ref,
                             status='pass', code='ok', duration_ms=(time.monotonic() - started) * 1000)
            return result
        except grpc.RpcError as error:
            diagnostics.emit('native', 'transport_response', method=method.lower(), rpc_ref=rpc_ref,
                             status='fail', code='rpc_' + method.lower() + '_' + error.code().name.lower(),
                             duration_ms=(time.monotonic() - started) * 1000)
            if error.code() == grpc.StatusCode.FAILED_PRECONDITION:
                raise NativeStale('Native precondition refused; fresh review required') from None
            raise NativeVerification('rpc_' + method.lower() + '_' + error.code().name.lower(),
                error.code() in {grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED,
                                 grpc.StatusCode.RESOURCE_EXHAUSTED, grpc.StatusCode.ABORTED}) from None
        except Exception:
            diagnostics.emit('native', 'transport_response', method=method.lower(), rpc_ref=rpc_ref,
                             status='unknown', code='native_transport_unknown',
                             duration_ms=(time.monotonic() - started) * 1000)
            raise NativeUnavailable(BLOCKER) from None


class NativeAdapter:
    def __init__(self, cfg=None, rpc=None, workspace='default', clock=time.time, confirmation_attempts=4, sleep=time.sleep):
        checked(type(confirmation_attempts) is int and 1 <= confirmation_attempts <= 4)
        self.confirmation_attempts, self.sleep = confirmation_attempts, sleep
        self.correlation = {}
        self.cfg, self.rpc, self.workspace, self.clock = cfg, rpc, workspace, clock
        checked(re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}', workspace))

    def call(self, method, **fields):
        checked(self.rpc is not None and self.cfg is not None)
        started = time.monotonic()
        ref = diagnostics.reference(fields.get('request_id') or uuid.uuid4())
        mutation = method in {'ApproveAllDraftChunks', 'RejectDraftChunk'}
        diagnostics.emit('native', 'rpc_start', method=method.lower(), rpc_ref=ref,
                         mutation_attempted=mutation, grant_possible=method == 'ApproveAllDraftChunks',
                         **self.correlation)
        try:
            response = self.rpc.call(method, **fields)
        except NativeUnavailable as error:
            diagnostics.emit('native', 'rpc_response', method=method.lower(), rpc_ref=ref,
                             status='unknown' if mutation else 'fail',
                             code=getattr(error, 'code', 'rpc_' + method.lower() + '_failed_precondition' if isinstance(error, NativeStale) else 'native_transport_unknown'),
                             duration_ms=(time.monotonic() - started) * 1000, **self.correlation)
            raise
        diagnostics.emit('native', 'rpc_response', method=method.lower(), rpc_ref=ref, status='pass',
                         code='ok', duration_ms=(time.monotonic() - started) * 1000,
                         **self.correlation)
        return response

    @property
    def selector(self):
        return {'workspace': self.workspace}

    def context(self, checkpoint=None, transient=None):
        def check(code, condition, retryable=False):
            diagnostics.emit('native', 'predicate', code=code, status='pass' if condition else 'wait' if retryable else 'fail', **self.correlation)
            if checkpoint:
                checkpoint(code, 'pass' if condition else 'wait' if retryable else 'fail')
            if not condition:
                if transient is not None and retryable:
                    transient.append(NativeVerification(code, True))
                else:
                    raise NativeVerification(code, retryable)

        def read(method, **fields):
            try:
                return self.call(method, **fields)
            except NativeVerification as error:
                diagnostics.emit('native', 'predicate', code=error.code, status='wait' if error.retryable else 'fail', **self.correlation)
                raise
            except NativeStale:
                raise NativeVerification('rpc_' + method.lower() + '_precondition') from None
            except NativeUnavailable:
                raise NativeVerification('rpc_' + method.lower() + '_unknown') from None

        health = read('Health')
        check('gateway_version', health.version == '0.1.2')
        check('gateway_health', health.status == 1, True)
        resource = read('GetSandbox', name=self.cfg['sandbox'], workspace_scope=self.selector).sandbox
        meta = resource.metadata
        try:
            local = control.read(private(self.cfg['state'] / 'state.json'))
        except (ValueError, OSError):
            check('local_state_private', False)
        tools = local.get('tools', {})
        check('guest_tools', type(tools.get('uid')) is int and tools['uid'] > 0
                and tools.get('version') == self.cfg['codex_version']
                and isinstance(tools.get('binaries'), list) and tools['binaries']
                and all(isinstance(b, str) and re.fullmatch(r'/(?:[A-Za-z0-9_@.+-]+/)*[A-Za-z0-9_@.+-]+', b) for b in tools['binaries'])
                and isinstance(tools.get('native_sha256'), str) and re.fullmatch(r'[a-f0-9]{64}', tools['native_sha256']))
        expected_config = control.digest(json.dumps(self.cfg, sort_keys=True, default=str).encode()
            + self.cfg['source_manifest'].read_bytes() + self.cfg['edit_scope'].read_bytes())
        check('config_binding', local.get('config_hash') == expected_config)
        check('sandbox_identity', meta.id == local.get('sandbox_id')
                and meta.name == self.cfg['sandbox'] and meta.workspace == self.workspace
                and meta.labels.get('manager.project') == self.cfg['id']
                and meta.labels.get('manager.phase') == '2'
                and meta.labels.get('manager.image') == self.cfg['image_id'].split(':')[1][:32]
                and not meta.HasField('deletion_time'))
        check('sandbox_ready', resource.status.phase == 2, resource.status.phase in {0, 1})
        config = read('GetSandboxConfig', name=self.cfg['sandbox'], workspace_scope=self.selector)
        check('config_schema', no_unknown(config) and config.HasField('policy'))
        check('config_admitted', config.configuration_admitted, transient is not None)
        check('policy_source', config.workspace == self.workspace and config.policy_source == 1
                and config.global_policy_version == 0)
        check('config_revision', config.config_revision > 0 and bool(re.fullmatch(r'[a-f0-9]{64}', config.policy_hash)))
        check('provider_revision_attachment', config.provider_env_revision > 0 and bool(config.provider_attachment_epoch))
        diagnostics.emit('native', 'configuration_observed', version=config.version, policy_hash=config.policy_hash,
                         provider_revision=config.provider_env_revision, **self.correlation)
        admission = resource.status.configuration_admission
        check('native_admission', admission.state == 2, admission.state in {0, 1})
        check('admission_instance', bool(config.configuration_instance_id)
                and admission.instance_id == config.configuration_instance_id, True)
        check('admission_policy_revision', admission.policy_version == config.version
                and admission.policy_hash == config.policy_hash, True)
        check('admission_config_revision', admission.config_revision == config.config_revision, True)
        check('admission_provider_revision', admission.provider_env_revision == config.provider_env_revision, True)
        reviewed = local.get('policy', {})
        filesystem = reviewed.get('filesystem_policy', {})
        check('filesystem_guard', filesystem and set(config.policy.filesystem.read_only) == set(filesystem.get('read_only', []))
                and set(config.policy.filesystem.read_write) == set(filesystem.get('read_write', []))
                and config.policy.filesystem.include_workdir == filesystem.get('include_workdir', False))
        check('landlock_guard', config.policy.landlock.compatibility == reviewed.get('landlock', {}).get('compatibility') == 'hard_requirement')
        check('process_guard', MessageToDict(config.policy.process, preserving_proto_field_name=True)
                == (reviewed.get('process') or {'run_as_user': 'sandbox', 'run_as_group': 'sandbox'}))
        check('middleware_guard', not config.policy.network_middlewares)
        loaded = read('GetSandboxPolicyStatus', sandbox=self.cfg['sandbox'], workspace_scope=self.selector, version=config.version)
        check('loaded_schema', no_unknown(loaded))
        diagnostics.emit('native', 'load_observed', version=loaded.revision.version, active_version=loaded.active_version,
                         status={0: 'unspecified', 1: 'pending', 2: 'loaded', 3: 'failed', 4: 'superseded'}.get(loaded.revision.status, 'unknown'),
                         **self.correlation)
        check('loaded_schema_hash', bool(re.fullmatch(r'[a-f0-9]{64}', loaded.revision.policy_hash)))
        check('loaded_revision_version', loaded.revision.version == config.version)
        check('loaded_state', loaded.revision.status == 2, loaded.revision.status in {0, 1})
        check('active_policy_version', loaded.active_version == config.version,
              loaded.revision.status in {0, 1, 2} and loaded.active_version < config.version)
        mode = config.settings.get('proposal_approval_mode')
        check('manual_approval_mode', mode is not None and (not mode.value.WhichOneof('value') or mode.value.string_value == 'manual'))
        return config, {'gateway': getattr(self.rpc, 'identity', 'fixture'), 'project': self.cfg['id'],
            'manager_revision': self.cfg['manager_revision'], 'sandbox': meta.name, 'sandbox_id': meta.id,
            'workspace': self.workspace, 'binaries': sorted(set(tools['binaries']) | {'/usr/bin/curl'}),
            'tools_fingerprint': digest({k: tools[k] for k in ('uid', 'version', 'binaries', 'native_sha256')}),
            'config_revision': config.config_revision,
            'provider_revision': config.provider_env_revision, 'attachment': config.provider_attachment_epoch,
            'instance': config.configuration_instance_id, 'version': config.version,
            'policy_hash': config.policy_hash, 'policy_bytes_hash': hashlib.sha256(config.policy.SerializeToString(deterministic=True)).hexdigest()}

    def discover(self):
        self.correlation = {}
        config, before = self.context()
        result = self.call('GetDraftPolicy', sandbox=self.cfg['sandbox'], workspace_scope=self.selector, status_filter='pending')
        _, after = self.context()
        if before != after:
            raise NativeStale('Native state changed during discovery')
        checked(no_unknown(result) and len(result.chunks) <= 1000)
        checked(len({c.id for c in result.chunks}) == len(result.chunks))
        proposals = []
        for chunk in result.chunks:
            checked(chunk.status == 'pending')
            proposals.append(self.snapshot(chunk, config, before))
        return proposals

    def snapshot(self, chunk, config, context):
        checked(no_unknown(chunk) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', chunk.id)
                and chunk.HasField('proposed_rule') and chunk.HasField('created_time')
                and chunk.created_time.seconds > 0 and re.fullmatch(r'[a-f0-9]{64}', chunk.review_token)
                and re.fullmatch(r'[a-f0-9]{64}', chunk.current_effective_policy_hash)
                and re.fullmatch(r'[a-f0-9]{64}', chunk.candidate_effective_policy_hash)
                and chunk.HasField('current_effective_policy') and chunk.HasField('candidate_effective_policy'))
        rule = chunk.proposed_rule
        scope = MessageToDict(rule, preserving_proto_field_name=True)
        scope_text = redacted(scope)
        safe = self.safe_rule(chunk, config, context['binaries'])
        if scope_text != json.dumps(scope, sort_keys=True, ensure_ascii=True) or len(scope_text) > 2400:
            safe = False
        # Do not persist/free-text native explanations or complete effective policies.
        snapshot = {**context, 'chunk_id': chunk.id, 'created': wire(chunk.created_time),
            'rule_name': chunk.rule_name, 'rule': wire(rule), 'review_token': chunk.review_token,
            'current_hash': chunk.current_effective_policy_hash, 'candidate_hash': chunk.candidate_effective_policy_hash,
            'risk_hash': digest({'security': chunk.security_notes, 'validation': chunk.validation_result,
                                 'application': chunk.application_error}),
            'eligible': safe, 'scope': scope_text if len(scope_text) <= 2400 else 'Complete scope too large; manual host review required',
            'risk': 'No new prover findings; no security flags' if safe else self.findings(chunk),
            'status': chunk.status}
        snapshot['identity'] = digest({k: snapshot[k] for k in ('gateway', 'workspace', 'sandbox_id', 'chunk_id', 'created')})
        snapshot['fingerprint'] = digest(snapshot)
        return snapshot

    def findings(self, chunk):
        # Render bounded native finding categories, never arbitrary native prose.
        codes = {'link_local_reach', 'l7_bypass_credentialed', 'credential_reach_expansion', 'capability_expansion'}
        findings = sorted(code for code in codes if code in chunk.validation_result)
        notes = chunk.security_notes.lower()
        for phrase, category in {'internal/private': 'private_destination', 'private/internal': 'private_allowed_ips',
                'wildcard': 'wildcard_destination', 'hostless': 'hostless_allowlist', 'ephemeral': 'ephemeral_port',
                'database/service': 'database_or_service_port', 'cannot inspect': 'uninspected_credentials'}.items():
            if phrase in notes:
                findings.append(category)
        if notes and not findings:
            findings.append('native_security_flags')
        if chunk.validation_result != 'prover: no new findings' and not any(code in findings for code in codes):
            findings.append('native_prover_unavailable_or_findings')
        if chunk.application_error:
            findings.append('native_application_error')
        return 'Approval disabled: ' + ', '.join(findings or ['scope_outside_P1'])

    def safe_rule(self, chunk, config, allowed_binaries):
        rule, current, candidate = chunk.proposed_rule, chunk.current_effective_policy, chunk.candidate_effective_policy
        if (chunk.security_notes or chunk.application_error or chunk.validation_result != 'prover: no new findings'
                or chunk.current_effective_policy_hash != config.policy_hash
                or wire(current) != wire(config.policy) or rule.name != chunk.rule_name
                or not re.fullmatch(r'[a-z][a-z0-9_]{1,60}', rule.name) or rule.name.startswith('_provider_')
                or len(rule.binaries) != 1 or rule.binaries[0].path not in allowed_binaries
                or chunk.binary != rule.binaries[0].path or not rule.endpoints):
            return False
        for endpoint in rule.endpoints:
            fields = MessageToDict(endpoint, preserving_proto_field_name=True)
            allowed = {'host', 'port', 'ports', 'protocol', 'enforcement', 'access', 'rules', 'deny_rules',
                       'advisor_proposed', 'path', 'allow_encoded_slash'}
            if (set(fields) - allowed or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', endpoint.host)
                    or '.' not in endpoint.host or '..' in endpoint.host or re.fullmatch(r'[0-9.]+', endpoint.host)
                    or endpoint.host == 'github.com' or endpoint.host.endswith('.github.com')
                    or endpoint.host == 'githubusercontent.com' or endpoint.host.endswith('.githubusercontent.com')
                    or (list(endpoint.ports) if endpoint.ports else [endpoint.port]) != [443]
                    or endpoint.port not in {0, 443} or endpoint.protocol != 'rest' or endpoint.enforcement != 1
                    or endpoint.tls != 0 or endpoint.access != 0 or not endpoint.rules
                    or (endpoint.path and not re.fullmatch(r'/[A-Za-z0-9_./*{}:@%+-]{0,300}', endpoint.path))):
                return False
            for existing in current.network_policies.values():
                for bound in existing.endpoints:
                    credentialed = (bound.provider_credentialed or bound.HasField('credential_binding')
                                    or bound.credential_signing or bound.request_body_credential_rewrite
                                    or bound.websocket_credential_rewrite or bound.allow_uninspected_credentials)
                    # Conservative glob match: overblocking is preferable to credential path expansion.
                    if credentialed and (not bound.host or fnmatch.fnmatchcase(endpoint.host, bound.host.lower())):
                        return False
            for deny in endpoint.deny_rules:
                if set(MessageToDict(deny, preserving_proto_field_name=True)) - {'method', 'path'}:
                    return False
            for match in endpoint.rules:
                if (not match.HasField('allow') or set(MessageToDict(match, preserving_proto_field_name=True)) != {'allow'}
                        or set(MessageToDict(match.allow, preserving_proto_field_name=True)) != {'method', 'path'}
                        or match.allow.method not in {'GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'}
                        or not re.fullmatch(r'/[A-Za-z0-9_./*{}:@%+-]{0,300}', match.allow.path)):
                    return False
        # Native candidate must add exactly this rule, preserving every existing authority.
        if rule.name in current.network_policies:
            return False
        if set(candidate.network_policies) != set(current.network_policies) | {rule.name}:
            return False
        if wire(candidate.network_policies[rule.name]) != wire(rule):
            return False
        for name in current.network_policies:
            if wire(current.network_policies[name]) != wire(candidate.network_policies[name]):
                return False
        baseline, proposed = type(current)(), type(candidate)()
        baseline.CopyFrom(current); proposed.CopyFrom(candidate)
        baseline.ClearField('network_policies'); proposed.ClearField('network_policies')
        return wire(baseline) == wire(proposed)

    def get(self, sandbox, chunk_id):
        checked(self.cfg is not None and sandbox == self.cfg['sandbox'])
        matches = [p for p in self.discover() if p['chunk_id'] == chunk_id]
        if len(matches) != 1:
            raise NativeStale('Native proposal is no longer pending')
        return matches[0]

    def verify(self, snapshot):
        current = self.get(snapshot['sandbox'], snapshot['chunk_id'])
        if current != snapshot:
            raise NativeStale('Native proposal or policy changed')
        return current

    def decide(self, snapshot, action, request_id, expires_at):
        checked(action in {'approve', 'reject'} and str(uuid.UUID(request_id)) == request_id)
        self.verify(snapshot)
        self.correlation = {'sandbox_ref': diagnostics.reference(snapshot['sandbox_id']),
                            'proposal_ref': diagnostics.reference(snapshot['chunk_id']),
                            'decision_ref': diagnostics.reference(request_id)}
        if self.clock() >= expires_at:
            raise NativeStale('Review expired before native dispatch')
        if action == 'approve':
            checked(snapshot['eligible'])
            # Empty approvals means blanket approval in this API. Never emit it.
            result = self.call('ApproveAllDraftChunks', sandbox=snapshot['sandbox'], workspace_scope=self.selector,
                approvals=[{'chunk_id': snapshot['chunk_id'], 'review_token': snapshot['review_token']}],
                include_security_flagged=False, request_id=request_id)
            if result.chunks_approved == 0 and result.chunks_skipped == 1 and result.policy_version == 0 and not result.policy_hash:
                raise NativeStale('Native proposal skipped; no grant applied. Fresh review required')
            valid = bool(result.chunks_approved == 1 and result.chunks_skipped == 0 and result.policy_version > 0
                         and re.fullmatch(r'[a-f0-9]{64}', result.policy_hash))
            diagnostics.emit('native', 'predicate', code='approval_receipt_shape', status='pass' if valid else 'fail', **self.correlation)
            if not valid:
                raise NativeVerification('approval_receipt_shape')
            receipt = {'version': result.policy_version, 'hash': result.policy_hash}
            diagnostics.emit('native', 'approval_receipt', receipt_received=True, receipt_version=result.policy_version,
                             receipt_hash=result.policy_hash, receipt_bytes=len(result.SerializeToString()), **self.correlation)
        else:
            self.call('RejectDraftChunk', sandbox=snapshot['sandbox'], workspace_scope=self.selector,
                chunk_id=snapshot['chunk_id'], reason='Rejected by the authorized project operator via Telegram; submit a narrower network rule.',
                request_id=request_id)
            receipt = None
        return receipt

    def confirm(self, snapshot, action, receipt=None):
        """Bounded reads only. Drift and unclassified transport errors never retry."""
        self.correlation.update(sandbox_ref=diagnostics.reference(snapshot['sandbox_id']),
                                proposal_ref=diagnostics.reference(snapshot['chunk_id']))
        predicates = ('proposal_schema', 'proposal_identity_unique', 'proposal_status', 'proposal_rule_equality',
                      'proposal_created_identity', 'proposal_rule_name', 'proposal_review_identity', 'proposal_risk_hash',
                      'provider_revision', 'candidate_policy_hash', 'candidate_policy_version', 'active_rule_present',
                      'active_rule_equality', 'confirmation_loaded_schema', 'receipt_version', 'receipt_hash',
                      'receipt_config_version', 'confirmation_revision_version', 'confirmation_loaded_state',
                      'confirmation_active_version')
        for attempt in range(self.confirmation_attempts):
            self._confirmation_observed = set()
            diagnostics.emit('native', 'confirmation_start', retry=attempt, action=action, **self.correlation)
            try:
                return self._confirm_once(snapshot, action, receipt)
            except NativeUnavailable as error:
                for predicate in predicates:
                    if predicate not in self._confirmation_observed:
                        diagnostics.emit('native', 'predicate', code=predicate, status='not_evaluated', **self.correlation)
                if not isinstance(error, NativeVerification):
                    diagnostics.emit('native', 'confirmation_end', status='unknown', code='native_outcome_unknown', **self.correlation)
                    raise
                diagnostics.emit('native', 'confirmation_end', retry=attempt, code=error.code, retryable=error.retryable,
                                 status='wait' if error.retryable and attempt + 1 < self.confirmation_attempts else 'fail', **self.correlation)
                if not error.retryable or attempt + 1 == self.confirmation_attempts:
                    raise
                # v0.1.2's supervisor polls policy every ten seconds. Four
                # one-second reads can all precede its next poll; allow three
                # poll intervals while retaining the bounded read count.
                self.sleep(10)

    def _confirm_once(self, snapshot, action, receipt):
        failures = []
        def check(code, condition, retryable=False):
            self._confirmation_observed.add(code)
            diagnostics.emit('native', 'predicate', code=code,
                             status='unknown' if condition is None else 'pass' if condition else 'wait' if retryable else 'fail', **self.correlation)
            if condition is False:
                failures.append(NativeVerification(code, retryable))

        config, context = self.context(transient=failures)
        for key in ('gateway', 'project', 'manager_revision', 'sandbox', 'sandbox_id', 'workspace', 'instance', 'attachment'):
            check('confirmation_identity_' + key, context[key] == snapshot[key])
        result = self.call('GetDraftPolicy', sandbox=snapshot['sandbox'], workspace_scope=self.selector, status_filter='')
        matches = [c for c in result.chunks if c.id == snapshot['chunk_id']]
        check('proposal_schema', no_unknown(result))
        check('proposal_identity_unique', len(matches) == 1)
        if len(matches) != 1:
            raise NativeVerification('proposal_identity_unique')
        chunk = matches[0]
        check('proposal_status', chunk.status == ('approved' if action == 'approve' else 'rejected'),
              chunk.status == 'pending' and receipt is not None)
        check('proposal_rule_equality', wire(chunk.proposed_rule) == snapshot['rule'])
        check('proposal_created_identity', wire(chunk.created_time) == snapshot['created'])
        check('proposal_rule_name', chunk.rule_name == snapshot['rule_name'])
        check('proposal_review_identity', chunk.review_token == snapshot['review_token'])
        check('proposal_risk_hash', digest({'security': chunk.security_notes, 'validation': chunk.validation_result,
                                         'application': chunk.application_error}) == snapshot['risk_hash'])
        check('provider_revision', context['provider_revision'] == snapshot['provider_revision'])
        if action == 'approve':
            check('candidate_policy_hash', config.policy_hash == snapshot['candidate_hash'],
                  receipt is not None and config.policy_hash == snapshot['policy_hash'] and config.version == snapshot['version'])
            check('candidate_policy_version', config.version > snapshot['version'],
                  receipt is not None and config.version == snapshot['version'])
            rule_present = snapshot['rule_name'] in config.policy.network_policies
            check('active_rule_present', rule_present, receipt is not None and config.version == snapshot['version'])
            check('active_rule_equality', rule_present and wire(config.policy.network_policies[snapshot['rule_name']]) == snapshot['rule'],
                  not rule_present and receipt is not None and config.version == snapshot['version'])
            status = self.call('GetSandboxPolicyStatus', sandbox=snapshot['sandbox'], workspace_scope=self.selector,
                               version=receipt['version'] if receipt else config.version)
            check('confirmation_loaded_schema', no_unknown(status))
            check('receipt_version', status.revision.version == receipt['version'] if receipt else None)
            check('receipt_hash', status.revision.policy_hash == receipt['hash'] if receipt else None)
            check('receipt_config_version', receipt is None or config.version == receipt['version'],
                  receipt is not None and config.version == snapshot['version'])
            check('confirmation_revision_version', status.revision.version == config.version,
                  receipt is not None and config.version == snapshot['version'])
            check('confirmation_loaded_state', status.revision.status == 2, status.revision.status in {0, 1})
            check('confirmation_active_version', status.active_version == status.revision.version,
                  status.revision.status in {0, 1, 2} and status.active_version < status.revision.version)
        else:
            check('rejection_policy_hash', context['policy_hash'] == snapshot['policy_hash'])
            check('rejection_config_revision', context['config_revision'] == snapshot['config_revision'])
            check('rejection_reason_present', bool(chunk.rejection_reason))
        if failures:
            # Permanent drift wins over simultaneously observed convergence.
            raise next((error for error in failures if not error.retryable), failures[0])
        diagnostics.emit('native', 'confirmation_end', action=action, status='pass', version=config.version,
                         policy_hash=context['policy_hash'], **self.correlation)
        return {'status': chunk.status, 'policy_hash': context['policy_hash'], 'version': context['version'],
                'loaded': action == 'approve'}

    def approve(self, snapshot, request_id, expires_at=None):
        checked(isinstance(snapshot, dict) and isinstance(expires_at, (int, float)))
        return self.decide(snapshot, 'approve', request_id, expires_at)

    def reject(self, snapshot, request_id, expires_at=None):
        checked(isinstance(snapshot, dict) and isinstance(expires_at, (int, float)))
        return self.decide(snapshot, 'reject', request_id, expires_at)
