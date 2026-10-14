# OpenShell Manager

PR1 imports the reviewed OpenShell manager and offline regression tests from the
reference snapshot described in the [migration plan](docs/plans/openshell-extraction-and-telegram-approval.md).
The existing `infra/dev-sandbox/` layout is retained to keep the extraction small.
This import is awaiting review and is not ready for deployment or OMA cutover.

Run the offline checks with Python 3.11+ and already-installed pytest and PyYAML:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
```

The suite uses mocked host tools and temporary local Git repositories. Its test
guard blocks host-tool execution and network calls and isolates Git configuration.
Sixteen Claude/OpenCode live acceptance cases are explicitly skipped.
No dependency installation is needed in an environment that already has the test tools.

The imported entrypoints remain `setup.sh`, `start.sh`, `verify.sh`, `rules.sh`,
`refresh.sh`, `export.sh`, `github.sh`, and `cleanup.sh` under `infra/dev-sandbox/`.
Their original host operations are preserved: setup can install software, start a
gateway, build images, create a VM and reconcile policies; start can authenticate
and invoke models; workflow publication can push and create draft PRs. They are
not part of the offline checks. `verify.sh` without arguments invokes host readiness
checks; the OMA-only `--reports` option now exits with an explanation.

PR2 adds validated project profiles and isolated host state. Example profiles are
inert: operational commands require `--project FILE` or `MANAGER_PROJECT`.
See [configuration ownership, CLI migration and limitations](docs/migration/pr2-configuration.md)
for the schema, per-project scope and safe migration procedure. Setup now requires
Python/PyYAML prerequisites to be installed explicitly before profile validation.

Pins are unchanged: OpenShell 0.1.2, Codex 0.162.1, Claude Code 2.1.296,
OpenCode 1.19.0 and GitHub CLI 2.101.0. Upstream release checksums and the base
image digest are retained. The Docker recipe already resolves APT/npm dependencies
at build time; a rebuilt image is not guaranteed to reproduce the private host image.
Codex's historical live gate is preserved in code; this import makes no new live
verification claim. Claude and OpenCode remain disabled for live execution.

See the [PR1 inventory, file mapping, security review and PR2 handoff](docs/migration/pr1-import-inventory.md).
Telegram approval belongs to PR3; OMA integration and cutover belong to PR4.
