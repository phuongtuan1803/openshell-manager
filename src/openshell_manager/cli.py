"""Public entrypoint; operation parsing and handlers remain in control."""
import sys

from . import control, provenance


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == 'approval':
        from .approval import service
        return service.main(argv[1:])
    if argv == ['version']:
        print('openshell-manager 0.1.0')
        try:
            revision, mode = provenance.identity()
            print(f'{mode}: {revision}')
        except ValueError as exc:
            print(f'provenance unavailable: {exc}')
        return 0
    if argv and argv[0] == 'verify':
        argv = ['setup', 'check', *argv[1:]]
    elif argv and argv[0] == 'setup':
        flags = [flag for flag in ('--check', '--dry-run') if flag in argv]
        if len(flags) > 1:
            print('FAIL: choose --check or --dry-run', file=sys.stderr)
            return 2
        if flags:
            argv.remove(flags[0])
            argv.insert(1, {'--check': 'check', '--dry-run': 'dry-run'}[flags[0]])
        elif len(argv) == 1 or argv[1].startswith('--'):
            argv.insert(1, 'apply')
    return control.main(argv)
