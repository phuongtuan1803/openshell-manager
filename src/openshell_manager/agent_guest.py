"""Fixed native-agent operations inside the existing project sandbox."""

import base64
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path


def require(value, message):
    if not value:
        raise ValueError(message)


def safe_path(path):
    require(path.resolve() == path.absolute(), "Symlink in agent installation or home")
    return path


def directory(path):
    safe_path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(path.is_dir() and path.stat().st_uid == os.getuid(), "Unsafe agent directory owner")


def file_hash(path):
    safe_path(path)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid(), "Unsafe agent executable")
    require(not info.st_mode & 0o022, "Agent executable is writable by another user")
    with path.open('rb') as source:
        header = source.read(20)
        require(header[:4] == b'\x7fELF' and header[18:20] == b'\x3e\x00',
                "A Linux x86_64 native executable is required")
        source.seek(0)
        return hashlib.file_digest(source, 'sha256').hexdigest()


def environment(name):
    env = dict(os.environ)
    # Agent account files remain in their own guest homes. Never fall back to
    # an injected API key, a host session, or a different model gateway.
    for key in list(env):
        if key.startswith(('ANTHROPIC_', 'CLAUDE_', 'OPENCODE_', 'OPENAI_', 'AZURE_',
                           'AWS_', 'GOOGLE_', 'GEMINI_')):
            del env[key]
    home = f'/sandbox/{name}-home'
    env.update(HOME=home, XDG_CONFIG_HOME=home + '/.config',
               XDG_DATA_HOME=home + '/.local/share', XDG_STATE_HOME=home + '/.local/state',
               XDG_CACHE_HOME=home + '/.cache', HISTFILE='/dev/null')
    if name == 'claude':
        env.update(CLAUDE_CONFIG_DIR=home + '/.claude', DISABLE_AUTOUPDATER='1',
                   CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1', ENABLE_CLAUDEAI_MCP_SERVERS='false')
        if env.get('SSL_CERT_FILE'):
            env['NODE_EXTRA_CA_CERTS'] = env['SSL_CERT_FILE']
    else:
        env.update(OPENCODE_DISABLE_AUTOUPDATE='true', OPENCODE_DISABLE_MODELS_FETCH='true',
                   OPENCODE_DISABLE_DEFAULT_PLUGINS='true')
    return env


def paths(request):
    name, version = request['agent'], request['version']
    require(name in {'claude', 'opencode'} and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version),
            "Invalid pinned agent")
    require(re.fullmatch(r'[0-9a-f]{64}', request['sha256']), "Invalid executable digest")
    binary = Path(f'/sandbox/tools/{name}/{version}/{name}')
    return name, version, binary, Path(f'/sandbox/{name}-home')


def verified(request):
    name, version, binary, home = paths(request)
    require(os.getuid() != 0, "Agent must run as the non-root sandbox user")
    require(file_hash(binary) == request['sha256'], "Agent executable checksum drift")
    safe_path(home)
    result = subprocess.run([str(binary), '--version'], env=environment(name), text=True,
                            capture_output=True, check=False, timeout=30)
    require(result.returncode == 0 and result.stdout.strip() in {
        version, f'{version} (Claude Code)' if name == 'claude' else version,
    }, "Agent executable version differs from pin")
    return {'agent': name, 'version': version, 'binary': str(binary),
            'sha256': request['sha256'], 'home': str(home)}


def main(request):
    os.umask(0o077)
    name, version, binary, home = paths(request)
    action = request['action']
    if action == 'directories':
        directory(binary.parent)
        for path in (home, home / '.config', home / '.local/share', home / '.local/state',
                     home / '.cache', home / '.claude' if name == 'claude' else home / '.config/opencode'):
            directory(path)
        if binary.exists() or binary.is_symlink():
            verified(request)
        return {'ready': True}
    if action == 'install':
        upload = Path(request['upload'])
        require(upload.parent == binary.parent and re.fullmatch(r'\.upload-[0-9a-f]{32}', upload.name),
                "Unexpected agent upload path")
        try:
            # Upload transport may create a group-readable file; never execute
            # it until it is private and checked against the reviewed digest.
            safe_path(upload)
            require(upload.is_file() and upload.stat().st_uid == os.getuid(), "Unsafe upload")
            upload.chmod(0o700)
            require(file_hash(upload) == request['sha256'], "Uploaded agent checksum mismatch")
            if binary.exists() or binary.is_symlink():
                verified(request)
            else:
                upload.rename(binary)
            return verified(request)
        finally:
            if upload.exists() and not upload.is_symlink():
                upload.unlink()
    result = verified(request)
    if action == 'inspect':
        return result
    env = environment(name)
    env['PATH'] = str(binary.parent) + os.pathsep + env.get('PATH', '/usr/bin:/bin')
    if action == 'status':
        if name == 'opencode':
            # Its auth-list exit code does not prove that a provider is logged in.
            return {**result, 'authenticated': None}
        status = subprocess.run([str(binary), 'auth', 'status'], env=env,
                                capture_output=True, timeout=30, check=False)
        return {**result, 'authenticated': status.returncode == 0}
    if action == 'login':
        args = ['auth', 'login', '--claudeai'] if name == 'claude' else ['auth', 'login']
    elif action == 'start':
        args = request.get('model_args', [])
        require(not args or (len(args) == 2 and args[0] == '--model'
                and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:-]{0,160}', args[1])),
                "Invalid model arguments")
    else:
        raise ValueError('Unknown native agent operation')
    os.execve(str(binary), [str(binary), *args], env)


if __name__ == '__main__':
    try:
        print(json.dumps(main(json.loads(base64.b64decode(sys.argv[1])))))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error) if isinstance(error, ValueError) else type(error).__name__, file=sys.stderr)
        sys.exit(1)
