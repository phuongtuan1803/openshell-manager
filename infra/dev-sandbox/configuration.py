"""Strict JSON profiles. Host settings never merge into project permissions."""
import json
import re
from pathlib import Path

import snapshot

DEFAULTS = Path(__file__).resolve().parent / 'phase2.json'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def file(path):
    path = Path(path).absolute()
    require(path.resolve() == path and path.is_file(), 'Missing or symlink configuration input')
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, 'Duplicate configuration key')
            value[key] = item
        return value
    return json.loads(path.read_text(), object_pairs_hook=unique)


def paths(path):
    names = [p.strip() for p in path.read_text().splitlines() if p.strip() and not p.startswith('#')]
    require(names and len(names) == len(set(names)), 'Scope must be nonempty and unique')
    for name in names:
        snapshot.validate_path(name)
        require(re.fullmatch(r'[A-Za-z0-9_./-]+', name) and name.endswith(('.py', '.md', '.toml', '.yaml', '.yml', '.json', '.sh', '.sql', '.txt')), 'Invalid scope path')
    return names


def branch(value):
    require(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_/-]{0,100}', value)
            and '..' not in value and '//' not in value and not value.endswith('/'), 'Invalid base branch')
    return value


def load(project_path, host_path=None):
    project_path = Path(project_path).absolute()
    p = file(project_path)
    required = {'id', 'repository', 'root', 'sandbox', 'workspace', 'source_manifest',
                'edit_scope', 'base_branch', 'test_paths', 'image', 'image_id', 'manager_revision'}
    require(required <= p.keys() and not p.keys() - required - {'rules', 'modules'}, 'Unknown or missing project fields')
    require(isinstance(p['id'], str) and re.fullmatch(r'[a-z][a-z0-9-]{1,30}', p['id']), 'Invalid project ID')
    require(p['sandbox'] == p['id'] + '-dev', 'Sandbox must be <project-id>-dev; conflicting identity')
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9_.-]*', p['repository'])
            and p['repository'].lower() not in {'example/project', 'owner/repository', 'your-org/your-repo'}, 'Placeholder or invalid repository')
    require(re.fullmatch(r'sha256:[0-9a-f]{64}', p['image_id']) and set(p['image_id'][7:]) != {'0'}, 'Placeholder or invalid image digest')
    require(re.fullmatch(r'[0-9a-f]{40}', p['manager_revision']) and set(p['manager_revision']) != {'0'}, 'Placeholder or invalid Manager revision')
    require(isinstance(p['image'], str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9./:_-]+', p['image'])
            and not p['image'].startswith(('example', 'placeholder')), 'Placeholder or invalid image')
    require(p['workspace'] == '/sandbox/' + p['id'] + '/checkout', 'Workspace must be /sandbox/<project-id>/checkout')
    root = Path(p['root'])
    require(root.is_absolute() and root.resolve() == root and (root / '.git').is_dir()
            and not (root / '.git').is_symlink(), 'Project root must be a standalone checkout without symlinks')
    for key in ('source_manifest', 'edit_scope'):
        value = Path(p[key])
        value = value if value.is_absolute() else project_path.parent / value
        require(value.resolve() == value.absolute() and value.is_file(), 'Missing or symlink scope file')
        p[key] = value.absolute()
    source, editable = paths(p['source_manifest']), paths(p['edit_scope'])
    require(set(editable) <= set(source) and all(Path(n).name != 'AGENTS.md' for n in editable), 'Edit scope exceeds source manifest')
    for name in source:
        require((root / name).resolve() == root / name and (root / name).is_file(), 'Missing or symlink source path')
    branch(p['base_branch'])
    require(isinstance(p['test_paths'], list) and p['test_paths'], 'Test paths required')
    for name in p['test_paths']:
        snapshot.validate_path(name)
        require(name in source, 'Test paths must be in source manifest')
    require(isinstance(p.get('modules', {}), dict), 'Modules must map names to source path lists')
    for name, members in p.get('modules', {}).items():
        snapshot.validate_path(name)
        require(isinstance(members, list) and members and set(members) <= set(source), 'Module exceeds manifest')
    rules = p.get('rules', [])
    require(isinstance(rules, list), 'Rules must be a list')
    ids = []
    for rule in rules:
        require(isinstance(rule, dict) and {'id', 'host', 'port', 'access', 'review'} <= rule.keys()
                and not rule.keys() - {'id', 'host', 'port', 'access', 'review', 'binary', 'agent'}, 'Unknown or missing rule fields')
        require((rule.get('agent') == 'codex' and 'binary' not in rule)
                or (rule.get('binary') == '/usr/bin/curl' and 'agent' not in rule), 'Rule executable must be pinned Codex or curl')
        require(isinstance(rule['review'], str) and rule['review'].strip(), 'Rule review required')
        ids.append(rule['id'])
    require(len(ids) == len(set(ids)), 'Duplicate rule IDs')
    defaults = file(DEFAULTS)
    # Same pinned tools; no project or host version override, and no inherited grants.
    result = {k: defaults[k] for k in ('cli_version', 'codex_version', 'agents')}
    result.update(p)
    result['rules'] = rules
    result['github'] = {'repository': p['repository'], 'provider': p['id'] + '-github'}
    h = file(host_path) if host_path else {}
    require(not h.keys() - {'state_root'}, 'Host settings cannot override project/security settings')
    state_root = Path(h.get('state_root', str(Path.home() / '.local/state/openshell-manager')))
    require(state_root.is_absolute() and state_root != Path('/') and state_root.resolve() == state_root, 'Unsafe state root')
    require(not state_root.is_relative_to(root), 'Host state must be outside project checkout')
    result['state'] = state_root / p['id']
    return result


def provider_profile(cfg):
    """Generate only the established exact repository endpoint template."""
    import yaml
    template = yaml.safe_load((DEFAULTS.parent / 'policies/github.yaml').read_text())
    template['id'] = cfg['github']['provider']
    template['display_name'] = cfg['id'] + ' GitHub'
    template['description'] = 'Scoped Git and draft PR operations; no merge endpoint'
    for endpoint in template['endpoints']:
        for rule in endpoint['rules']:
            rule['allow']['path'] = rule['allow']['path'].replace('example/project', cfg['repository'])
    return template
