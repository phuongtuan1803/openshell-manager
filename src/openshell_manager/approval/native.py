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
from . import schema
from .store import private

BLOCKER = 'BLOCKED — native interface unavailable; Telegram cannot grant permissions. Manual host review required.'


class NativeUnavailable(ValueError):
    pass


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
        try:
            invoke = self.channel.unary_unary('/openshell.v1.OpenShell/' + method,
                request_serializer=lambda value: value.SerializeToString(deterministic=True),
                response_deserializer=response.FromString)
            return invoke(request(**fields), timeout=10, wait_for_ready=False)
        except grpc.RpcError as error:
            if error.code() == grpc.StatusCode.FAILED_PRECONDITION:
                raise NativeStale('Native precondition refused; fresh review required') from None
            raise NativeUnavailable(BLOCKER) from None
        except Exception:
            raise NativeUnavailable(BLOCKER) from None


class NativeAdapter:
    def __init__(self, cfg=None, rpc=None, workspace='default', clock=time.time):
        self.cfg, self.rpc, self.workspace, self.clock = cfg, rpc, workspace, clock
        checked(re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}', workspace))

    def call(self, method, **fields):
        checked(self.rpc is not None and self.cfg is not None)
        return self.rpc.call(method, **fields)

    @property
    def selector(self):
        return {'workspace': self.workspace}

    def context(self):
        health = self.call('Health')
        checked(health.version == '0.1.2' and health.status == 1)
        resource = self.call('GetSandbox', name=self.cfg['sandbox'], workspace_scope=self.selector).sandbox
        meta = resource.metadata
        local = control.read(private(self.cfg['state'] / 'state.json'))
        tools = local.get('tools', {})
        checked(type(tools.get('uid')) is int and tools['uid'] > 0
                and tools.get('version') == self.cfg['codex_version']
                and isinstance(tools.get('binaries'), list) and tools['binaries']
                and all(isinstance(b, str) and re.fullmatch(r'/(?:[A-Za-z0-9_@.+-]+/)*[A-Za-z0-9_@.+-]+', b) for b in tools['binaries'])
                and isinstance(tools.get('native_sha256'), str) and re.fullmatch(r'[a-f0-9]{64}', tools['native_sha256']))
        expected_config = control.digest(json.dumps(self.cfg, sort_keys=True, default=str).encode()
            + self.cfg['source_manifest'].read_bytes() + self.cfg['edit_scope'].read_bytes())
        checked(local.get('config_hash') == expected_config and meta.id == local.get('sandbox_id')
                and meta.name == self.cfg['sandbox'] and meta.workspace == self.workspace
                and meta.labels.get('manager.project') == self.cfg['id']
                and meta.labels.get('manager.phase') == '2'
                and meta.labels.get('manager.image') == self.cfg['image_id'].split(':')[1][:32]
                and resource.status.phase == 2 and not meta.HasField('deletion_time'))
        config = self.call('GetSandboxConfig', name=self.cfg['sandbox'], workspace_scope=self.selector)
        checked(no_unknown(config) and config.HasField('policy') and config.configuration_admitted
                and config.workspace == self.workspace and config.policy_source == 1
                and config.global_policy_version == 0 and config.config_revision > 0
                and config.provider_env_revision > 0 and config.configuration_instance_id
                and config.provider_attachment_epoch and re.fullmatch(r'[a-f0-9]{64}', config.policy_hash))
        admission = resource.status.configuration_admission
        checked(admission.state == 2 and admission.instance_id == config.configuration_instance_id
                and admission.policy_version == config.version and admission.policy_hash == config.policy_hash
                and admission.config_revision == config.config_revision and admission.provider_env_revision == config.provider_env_revision)
        reviewed = local.get('policy', {})
        filesystem = reviewed.get('filesystem_policy', {})
        checked(filesystem and set(config.policy.filesystem.read_only) == set(filesystem.get('read_only', []))
                and set(config.policy.filesystem.read_write) == set(filesystem.get('read_write', []))
                and config.policy.filesystem.include_workdir == filesystem.get('include_workdir', False)
                and config.policy.landlock.compatibility == reviewed.get('landlock', {}).get('compatibility') == 'hard_requirement'
                and MessageToDict(config.policy.process, preserving_proto_field_name=True) == (reviewed.get('process') or {'run_as_user': 'sandbox', 'run_as_group': 'sandbox'})
                and not config.policy.network_middlewares)  # Existing Manager P1 declares no middleware authority.
        loaded = self.call('GetSandboxPolicyStatus', sandbox=self.cfg['sandbox'], workspace_scope=self.selector,
                           version=config.version)
        checked(no_unknown(loaded) and loaded.revision.version == config.version and loaded.revision.status == 2
                and loaded.active_version == config.version and re.fullmatch(r'[a-f0-9]{64}', loaded.revision.policy_hash))
        mode = config.settings.get('proposal_approval_mode')
        checked(mode is not None and (not mode.value.WhichOneof('value') or mode.value.string_value == 'manual'))
        return config, {'gateway': getattr(self.rpc, 'identity', 'fixture'), 'project': self.cfg['id'],
            'manager_revision': self.cfg['manager_revision'], 'sandbox': meta.name, 'sandbox_id': meta.id,
            'workspace': self.workspace, 'binaries': sorted(set(tools['binaries']) | {'/usr/bin/curl'}),
            'tools_fingerprint': digest({k: tools[k] for k in ('uid', 'version', 'binaries', 'native_sha256')}),
            'config_revision': config.config_revision,
            'provider_revision': config.provider_env_revision, 'attachment': config.provider_attachment_epoch,
            'instance': config.configuration_instance_id, 'version': config.version,
            'policy_hash': config.policy_hash, 'policy_bytes_hash': hashlib.sha256(config.policy.SerializeToString(deterministic=True)).hexdigest()}

    def discover(self):
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
            checked(result.chunks_approved == 1 and result.chunks_skipped == 0 and result.policy_version > 0
                    and re.fullmatch(r'[a-f0-9]{64}', result.policy_hash))
            receipt = {'version': result.policy_version, 'hash': result.policy_hash}
        else:
            self.call('RejectDraftChunk', sandbox=snapshot['sandbox'], workspace_scope=self.selector,
                chunk_id=snapshot['chunk_id'], reason='Rejected by the authorized project operator via Telegram; submit a narrower network rule.',
                request_id=request_id)
            receipt = None
        return receipt

    def confirm(self, snapshot, action, receipt=None):
        config, context = self.context()
        checked(all(context[k] == snapshot[k] for k in ('gateway', 'project', 'manager_revision', 'sandbox', 'sandbox_id', 'workspace', 'instance', 'attachment')))
        result = self.call('GetDraftPolicy', sandbox=snapshot['sandbox'], workspace_scope=self.selector, status_filter='')
        matches = [c for c in result.chunks if c.id == snapshot['chunk_id']]
        checked(len(matches) == 1 and no_unknown(result))
        chunk = matches[0]
        checked(chunk.status == ('approved' if action == 'approve' else 'rejected')
                and wire(chunk.proposed_rule) == snapshot['rule'] and wire(chunk.created_time) == snapshot['created']
                and chunk.rule_name == snapshot['rule_name'] and chunk.review_token == snapshot['review_token']
                and digest({'security': chunk.security_notes, 'validation': chunk.validation_result,
                            'application': chunk.application_error}) == snapshot['risk_hash'])
        if action == 'approve':
            checked(context['provider_revision'] == snapshot['provider_revision']
                    and config.policy_hash == snapshot['candidate_hash']
                    and snapshot['rule_name'] in config.policy.network_policies)
            status = self.call('GetSandboxPolicyStatus', sandbox=snapshot['sandbox'], workspace_scope=self.selector,
                               version=config.version)
            checked(status.revision.version == config.version
                    and (receipt is None or (status.revision.version == receipt['version'] and status.revision.policy_hash == receipt['hash']))
                    and status.revision.status == 2 and status.active_version == config.version
                    and wire(config.policy.network_policies.get(snapshot['rule_name'])) == snapshot['rule'])
        else:
            checked(context['policy_hash'] == snapshot['policy_hash'] and context['provider_revision'] == snapshot['provider_revision']
                    and context['config_revision'] == snapshot['config_revision'] and chunk.rejection_reason)
        return {'status': chunk.status, 'policy_hash': context['policy_hash'], 'version': context['version'],
                'loaded': action == 'approve'}

    def approve(self, snapshot, request_id, expires_at=None):
        checked(isinstance(snapshot, dict) and isinstance(expires_at, (int, float)))
        return self.decide(snapshot, 'approve', request_id, expires_at)

    def reject(self, snapshot, request_id, expires_at=None):
        checked(isinstance(snapshot, dict) and isinstance(expires_at, (int, float)))
        return self.decide(snapshot, 'reject', request_id, expires_at)
