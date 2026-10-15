"""Fixed guest protocol probe; no host credentials, models or arbitrary commands."""
import json
import re
import subprocess
import sys


def main(request):
    action = request['action']
    if action == 'submit':
        assert re.fullmatch(r'telegram_probe_[ab]', request['name'])
        assert re.fullmatch(r'/(?:pr3b-[a-f0-9]{32})?', request['path'])
        body = {'intent_summary': 'Isolated Manager Telegram approval protocol test', 'operations': [
            {'addRule': {'ruleName': request['name'], 'rule': {'name': request['name'],
                'endpoints': [{'host': 'example.com', 'port': 443, 'protocol': 'rest',
                    'enforcement': 'enforce', 'rules': [{'allow': {'method': 'GET', 'path': request['path']}}]}],
                'binaries': [{'path': '/usr/bin/curl'}]}}}]}
        url = 'http://policy.local/v1/proposals'
    elif action == 'wait':
        assert re.fullmatch(r'[A-Za-z0-9_-]{1,100}', request['chunk'])
        url = 'http://policy.local/v1/proposals/' + request['chunk'] + '/wait?timeout=1'
        body = None
    elif action == 'probe':
        assert re.fullmatch(r'/(?:pr3b-[a-f0-9]{32})?', request['path'])
        url = 'https://example.com' + request['path']
        body = None
    else:
        raise ValueError('Unsupported probe')
    args = ['/usr/bin/curl', '--silent', '--show-error', '--max-time', '20',
            '--max-filesize', '65536', '--write-out', '\n%{http_code}', url]
    if body is not None:
        args += ['-H', 'Content-Type: application/json', '--data-binary', '@-']
    result = subprocess.run(args, input=json.dumps(body) if body else None, text=True,
                            capture_output=True, timeout=25, check=False)
    text, _, code = result.stdout.rpartition('\n')
    try:
        payload = json.loads(text)
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if action == 'probe':
        return {'http_status': code, 'exit_code': result.returncode,
                'policy_denied': payload.get('error') == 'policy_denied'}
    if result.returncode or code not in {'200', '202'}:
        return {'error': 'native_protocol_unavailable', 'http_status': code}
    if action == 'submit':
        return {'accepted_chunk_ids': payload.get('accepted_chunk_ids'),
                'refused': bool(payload.get('rejection_reasons'))}
    return {'status': payload.get('status'), 'policy_reloaded': payload.get('policy_reloaded'),
            'has_rejection_reason': bool(payload.get('rejection_reason'))}


if __name__ == '__main__':
    try:
        print(json.dumps(main(json.loads(sys.argv[1]))))
    except Exception:
        print(json.dumps({'error': 'guest_probe_failed'}))
        sys.exit(1)
