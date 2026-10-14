# Installing OpenShell Manager with uv

Manager requires Python 3.11+ and uv. Native operations still require the supported
Linux host and reviewed external OpenShell/Gateway/Docker tooling. Installation
alone never starts services or provisions a sandbox. There is no Telegram code in
this package.

Use a Founder-reviewed **full 40-character commit SHA**, replacing the placeholder:

```bash
uv tool install "git+https://github.com/phuongtuan1803/openshell-manager.git@<FULL_MANAGER_COMMIT_SHA>"
openshell-manager version
```

Run from any directory. Keep project/host JSON, manifests, source, credentials and
state outside the installed package. Existing profile schema and permissions apply:

```bash
export MANAGER_PROJECT=/absolute/path/project.json
# Optional private host settings:
export MANAGER_HOST=/absolute/private/path/host.json
openshell-manager setup --dry-run
openshell-manager verify
openshell-manager start codex
```

Explicit `--project FILE` and `--host FILE` override the environment. Other commands
are `setup`, `run`, `rules`, `github`, `refresh`, `export`, and `cleanup`; existing
arguments, agents, confirmations and failure codes remain in effect. `verify`
means `setup check`. `setup` means explicit apply and can provision approved host
tools/resources; use `--dry-run` to inspect the plan. Dry-run validates the profile
and writes the existing local project binding/provider template, but invokes no
native tools, installers or models. `verify` is a native host readiness check, not
an offline test. Interactive `start` can authenticate and invoke models.

## Revision identity and expected errors

`project.json.manager_revision` must equal the installed PEP 610
`direct_url.json.vcs_info.commit_id`. The receipt must identify Git and the approved
Manager repository; the running module must belong to that installed distribution.
Neither package version, environment variables, tags nor branch names prove a
revision. Unknown provenance and mismatches fail before checkout/native commands
or state creation. Install only immutable reviewed commits even though a VCS
installer can also accept branch names.

Plain wheels/sdists and local directory installations lack approved VCS provenance.
They can display `version` and help, but operational commands refuse them. Reinstall
from the approved full Git SHA to fix missing provenance or a mismatch. Do not
edit receipts, change a project pin to bypass review, or delete state to suppress an
error. PEP 610 is installer metadata, not a cryptographic signature or protection
against an attacker controlling the host/tool environment.

Source development is explicitly reported as `development` and requires an actual
Manager Git checkout with the approved origin, tracked package/provenance files,
no uncommitted tracked changes, and HEAD equal to the project pin. An editable
installation of that same checkout is permitted. Conflicting installed metadata is
rejected; use a separate development environment rather than falling back from
missing installed provenance to an unrelated Git directory.

## Dependencies and development

PyYAML 6.0.3 is the only runtime Python dependency and is resolved inside uv's tool
environment. Manager never installs `python3-yaml` with apt. Inspect actual installed
dependencies with `uv tool list` and the tool environment's interpreter:

```bash
"$(uv tool dir)/openshell-manager/bin/python" -c 'import importlib.metadata as m; print(m.version("PyYAML"))'
```

`uv.lock` locks repository development dependencies. **It does not lock a Git-based
`uv tool install`.** Runtime PyYAML has an explicit exact constraint in
`pyproject.toml`; inspect the resolved environment after installation. The pinned
build backend has its own build requirements. Existing OpenShell, agent and gh pins
are unchanged; native apply-mode bootstrapping retains its prior behavior.

For development, including compatibility shell scripts, use the package environment:

```bash
uv sync --locked
uv run --locked openshell-manager version
uv run --locked ./scripts/setup.sh --dry-run --project /absolute/path/project.json
uv lock --check
uv build
uv run --locked python -m pytest -q
uv run --locked python tools/verify_packaging.py
```

The last command is a separate offline integration harness. Pre-cache PyYAML 6.0.3
and hatchling 1.27.0 plus its build dependencies (for example, `uv pip install --target
/tmp/manager-build-deps PyYAML==6.0.3 hatchling==1.27.0` using the same `UV_CACHE_DIR`).
It inspects both archives, verifies reviewed resource hashes, installs a full-SHA
local Git fixture through uv in private temporary HOME/tool directories, hides the
fixture checkout, and exercises the installed executable with native-tool
tripwires. Git transport is rewritten to the local fixture and HTTPS/SSH transports
are disabled. It also compares legacy wrappers and verifies wheel/mismatch refusal.
This is offline fixture evidence, not live native acceptance. The pytest suite
blocks network and non-Git subprocesses. No CI workflow deploys or creates a PR.

## Update and rollback

Record the current approved SHA/profile and actual dependency versions before an
update. Review a new immutable commit, then replace only the uv tool:

```bash
uv tool install --reinstall "git+https://github.com/phuongtuan1803/openshell-manager.git@<NEW_APPROVED_SHA>"
openshell-manager version
```

The consuming project's reviewed pin must match before use. Existing state binds
both project identity and Manager revision: a new pin can produce `Conflicting
project identity or Manager pin; existing state preserved`. This intentionally
requires a separately reviewed migration/cutover; packaging does not rewrite state
bindings. Qualify a new version with a separate disposable profile and private
`state_root`, without accessing an active sandbox. Do not reconcile active resources
or credentials as part of an installation test.

To roll back, reinstall the earlier reviewed SHA and use its matching project/host
profile and preserved state namespace:

```bash
uv tool install --reinstall "git+https://github.com/phuongtuan1803/openshell-manager.git@<PREVIOUS_APPROVED_SHA>"
openshell-manager version
```

`uv tool uninstall openshell-manager` removes the Python tool only. Neither update,
rollback nor uninstall should delete runtime state, credentials, policies, VMs,
exports or user work. Resolve inconsistent state/pins through Founder review.
