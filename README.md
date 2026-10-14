# OpenShell Manager

Independent host-side tooling for OpenShell MicroVM development sandboxes, agents,
GitHub Provider and scoped project workflows. This repository is public; project
configuration, credentials and local state remain owned by the consuming project
and the Linux host.

## Repository layout

```text
src/openshell_manager/   Python runtime, configuration, agent and GitHub adapters
scripts/                 Host CLI entrypoints (setup/start/verify/rules/etc.)
config/defaults/         Pinned Manager defaults; example identities are inert
config/policies/         Sandbox and GitHub Provider policy templates
config/examples/         Generic project/host profile and scope examples
config/gateway.vm.example.toml  Host Gateway example (not active host config)
docker/                  Reviewed VM/agent image recipes
tests/                   Offline regression suite and fixtures
docs/                    Plans, migration evidence and decisions
```

Legacy `config/defaults`, `config/policies`, Gateway example and Docker files link
to canonical assets in `src/openshell_manager/resources`. Guest/session payloads
remain canonical Python modules and are read through `importlib.resources`.

## Usage

Install the CLI with uv from an approved full Git SHA; see the
[installation, update and rollback runbook](docs/packaging.md). PyYAML is provided
by the package environment. Require Python 3.11+ and Git. Fill in the reviewed image
digest and immutable Manager commit; the provided examples cannot operate as-is.

```bash
uv tool install "git+https://github.com/phuongtuan1803/openshell-manager.git@<FULL_MANAGER_COMMIT_SHA>"
openshell-manager version
export MANAGER_PROJECT=/absolute/path/to/project/project.json
# Optional: export MANAGER_HOST=/absolute/private/path/host.json
openshell-manager setup --dry-run
openshell-manager setup --check
openshell-manager setup            # Explicit host operation; may install/start resources
openshell-manager start codex
openshell-manager verify
```

The scripts are **not** ordinary offline checks: setup can install host software,
create a VM and reconcile policies; interactive start can log in and invoke models.
Nothing in the folder reorganization changes effective policy, native permissions,
state or tool pins. Never put host tokens or runtime state in this public repository.

## Offline verification

```bash
uv lock --check
uv build
uv run --locked python -m pytest -q
uv run --locked python tools/verify_packaging.py
```

Offline tests use mocked host tools and local Git fixtures. Claude/OpenCode live
acceptance cases are skipped and must not be presented as verified. Historical
pins remain OpenShell 0.1.2, Codex 0.162.1, Claude Code 2.1.296, OpenCode
1.19.0 and GitHub CLI 2.101.0.

See [migration notes](docs/migration/pr2-configuration.md) and the
[extraction/Telegram plan](docs/plans/openshell-extraction-and-telegram-approval.md).
Telegram Approval is a later implementation step, not part of this reorganization.
