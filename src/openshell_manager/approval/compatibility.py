"""Read existing inbox only. Ignore descriptions, paths, logs and native-looking IDs."""
import hashlib
import json
import re

from .. import control


def notifications(path):
    if not path.exists():
        return []
    control.require(path.stat().st_size <= 1024 * 1024, 'Compatibility inbox too large')
    inbox = control.read(path)
    control.require(isinstance(inbox, dict), 'Invalid compatibility inbox')
    result = []
    for value in list(inbox.values())[:1000]:
        if not isinstance(value, dict) or value.get('status') != 'pending':
            continue
        host, port, binaries = value.get('host'), value.get('port'), value.get('binaries')
        if (not isinstance(host, str) or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,252}', host)
                or type(port) is not int or not 1 <= port <= 65535
                or not isinstance(binaries, list) or len(binaries) != 1
                or not isinstance(binaries[0], str)
                or binaries[0] not in {'/usr/bin/curl', '/usr/bin/codex'}):
            continue
        # Minimal fields only; no log-derived authorization or invented method/path.
        scope = {'host': host, 'port': port, 'binary': binaries[0]}
        payload = json.dumps(scope, sort_keys=True)
        result.append((hashlib.sha256(payload.encode()).hexdigest(), payload))
    return result
