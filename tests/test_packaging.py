"""Resource hashes captured from reviewed main; no native operations."""
import hashlib
import json
import tomllib
from pathlib import Path

from openshell_manager.assets import ROOT, RESOURCES

REPO = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((REPO / 'tests/fixtures/packaging/resources.json').read_text())


def packaged_path(source):
    return ROOT / Path(source).name if source.startswith('src/') else RESOURCES / source


def test_canonical_resources_and_payloads_match_reviewed_main():
    for source, expected in MANIFEST.items():
        assert hashlib.sha256(packaged_path(source).read_bytes()).hexdigest() == expected, source
        if not source.startswith('src/'):
            assert (REPO / source).resolve() == packaged_path(source)


def test_package_metadata_and_tool_pins():
    project = tomllib.loads((REPO / 'pyproject.toml').read_text())['project']
    assert project['requires-python'] == '>=3.11'
    assert project['scripts'] == {'openshell-manager': 'openshell_manager.cli:main'}
    assert project['dependencies'] == ['PyYAML==6.0.3']
    defaults = json.loads((RESOURCES / 'config/defaults/phase2.json').read_text())
    assert defaults['cli_version'] == 'openshell 0.1.2'
    assert defaults['codex_version'] == 'codex-cli 0.162.1'
