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

## External project Codex network onboarding

The Manager **does not inherit** the illustrative Codex network rules in
`config/defaults/phase2.json` for external projects. An external
`project.json` with `"rules": []` is intentionally **default deny**: GitHub
Provider readiness does not authorize Codex device login or workspace discovery.

Before using `start login` or `start codex`, the Founder must review and
explicitly declare the needed endpoints in the **private project profile**
(never in the Manager's default config). For Codex 0.162.1, the currently
observed onboarding endpoints are:

| Purpose | Exact HTTPS endpoint | Pinned executable |
| --- | --- | --- |
| Device login | `auth.openai.com:443` | Discovered Codex native binary/daemon paths |
| Account/workspace bootstrap | `chatgpt.com:443` | Discovered Codex native binary/daemon paths |

Example reviewed profile entries; **do not silently inject these defaults**:

```json
"rules": [
  {
    "id": "codex_auth",
    "host": "auth.openai.com",
    "port": 443,
    "access": "full",
    "agent": "codex",
    "review": "Founder-approved HTTPS for Codex device authentication"
  },
  {
    "id": "codex_workspace",
    "host": "chatgpt.com",
    "port": 443,
    "access": "full",
    "agent": "codex",
    "review": "Founder-approved HTTPS for Codex account/workspace discovery"
  }
]
```

Review the security impact: `full` means endpoint-level HTTPS access for the
exact pinned executable paths, **not** HTTP path-specific permission. Check the
actual denied endpoint and requested executable before changing a policy.
The preflight reports missing `host:443`, binary path and commands without
trying to log in or start a model.

On an **already initialized project** after changing `project.json`:

```bash
openshell-manager rules reconcile  # Founder must type "reconcile" to approve the profile
openshell-manager setup apply      # Installs only reviewed missing rules; verifies effective policy
openshell-manager setup check
openshell-manager start login      # Or: openshell-manager start codex
```

On a **new project**, approve the private profile before the initial
`setup apply` instead. Existing rules explicitly approved and tracked through
`rules pending`/`rules approve` are also accepted if the effective policy
covers the pinned executables. Neither setup checks nor preflight invent
missing network permissions. Preserve manual proposal approval and default deny.

## Claude and OpenCode in the existing project sandbox

`agent prepare` installs an explicitly reviewed Linux x86_64 native executable
under `/sandbox/tools/<agent>/<pinned-version>/`. It reuses the configured
sandbox, image and checkout. It never creates a second sandbox or copies host
account files. A native Claude executable avoids the npm launcher's Node version
requirement. Supply the full path to a regular binary and its reviewed SHA-256;
symbolic links and credential files are rejected.

```bash
openshell-manager agent prepare --agent claude \
  --binary /absolute/path/to/native/claude --sha256 REVIEWED_64_HEX_DIGEST
openshell-manager agent status --agent claude
openshell-manager agent network --agent claude
openshell-manager agent approve-network --agent claude
openshell-manager agent login --agent claude
openshell-manager start claude
```

All commands use `MANAGER_PROJECT`/`--project` and the same verified project
binding as the Codex commands. The installation receipt binds the executable
version and checksum to that sandbox ID. Startup and network approval recheck
the guest executable. Existing binaries with different content are preserved
for explicit migration review.

Preparation grants no network access. `approve-network` uses the existing
native dry run, Founder confirmation, journal and Loaded-policy verification
for each exact executable/host rule. Claude subscription login needs
`claude.ai`, `platform.claude.com` and `api.anthropic.com` on HTTPS 443. This is
endpoint-level access: source and credentials can be sent to those hosts.
Auto-updates and optional Claude connector discovery are disabled. No browser
login is started during preparation; complete login in a private terminal.

Claude account files stay under `/sandbox/claude-home`, OpenCode account files
under `/sandbox/opencode-home`, and Codex keeps `/sandbox/.codex`. Inherited
API keys, OAuth environment tokens and model-gateway overrides are removed
from the new agents' environments. OpenShell's CA trust and the stateless Git
Provider helper are preserved. Interactive startup uses the common checkout
synchronization and source lock; all agents share the existing task lock.

OpenCode uses the same `agent prepare` command with `--agent opencode` and its
pinned native binary. Supply `--model provider/model` to network review,
approval and startup. Only the selected provider's reviewed endpoint template
is used; other provider grants are not inherited. Authenticate using
`agent login --agent opencode --model provider/model` in a private terminal.
`agent status` reports OpenCode installation integrity, not a claim that a
provider is authenticated. Select and verify the intended provider in its CLI.

This explicit installation path enables interactive preparation and startup;
it does not mark all live acceptance cases as verified or enable autonomous
`run claude`/`run opencode` and publication. Those existing live gates remain.

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

Telegram Approval P1 automatically discovers exact OpenShell v0.1.2 network-rule
proposals through an existing authenticated local Gateway. Supported requests
provide **Approve rule**, **Reject** and **Details**, with native result/load
confirmation, private SQLite audit and fail-closed recovery. Compatibility notices
remain read-only fallback. See the [operator runbook](docs/runbooks/telegram-approval-p1.md),
[one-command live test](docs/runbooks/telegram-approval-test.md) (`openshell-manager approval test`),
[manual first real run](docs/runbooks/telegram-approval-live.md),
[versioned native API evidence](docs/native-api-v0.1.2.md) and
[acceptance report](docs/pr3b-acceptance.md). Live acceptance is not yet verified;
package installation never starts or enables the optional user service.
