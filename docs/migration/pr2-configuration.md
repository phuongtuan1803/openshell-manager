# PR2 configuration and CLI migration

PR2 retains `infra/dev-sandbox/` and the existing shell entrypoints. No OMA
checkout, Gateway, VM or credentials were modified during implementation.

## Configuration ownership

`configuration.py` loads strict JSON without new dependencies. Unknown and
duplicate keys fail closed. `phase2.json` supplies Manager tool/agent defaults
only: OpenShell 0.1.2 and existing agent pins remain unchanged. Its example
resource identities, image digest and network rules are never inherited by a
project. Direct CLI invocation without a project fails before host commands.

Copy `config/examples/project.json` and its scope files into a project-owned
configuration directory. Replace the repository, checkout root, image tag,
image digest and immutable Manager Git revision. Examples intentionally fail
validation until filled in. Scope file paths resolve relative to the profile;
source paths resolve relative to the project checkout. The root must be a
standalone Git checkout; linked worktrees and symlinks are rejected.

Project IDs use lowercase letters, numbers and hyphens, start with a letter,
and contain 2–31 characters. Sandbox names are `<id>-dev`, provider names are
`<id>-github`, and checkout paths are `/sandbox/<id>/checkout`. These conservative
names avoid ambiguous native identifiers and cannot target imported OMA resources.
Provider endpoints are generated from the existing exact repository template;
custom provider IDs, endpoint policies and filesystem overrides are rejected.
Each ID is bound to its root, repository, sandbox, workspace and Manager pin in
host state. Conflicting reuse fails before OpenShell operations.

`source_manifest` is the complete readable export allowlist; `edit_scope` is a
nonempty subset, excluding `AGENTS.md`. Both accept plain source formats already
supported by snapshot filtering. Snapshot checks still reject private paths,
secrets, ignored paths, symlinks and noncommitted blobs. `pilot` and `full` now
select the configured manifest. `module` selects named lists in `modules`, each
strictly contained in that manifest. No directory discovery expands permissions.
Tests use the pinned Python pytest runner and configured `test_paths`; explicit
`--test-path` values can only select those paths. Module runs should select test
paths included in that module's source list. The base branch is project-owned;
a CLI override must match it. Publish heads still require `feat/openshell-*`.
No automatic main push, force-push or PR merge is added.

`rules` defaults to empty. Explicit reviewed rules retain exact DNS/HTTPS 443
validation and can target discovered pinned Codex binaries or `/usr/bin/curl`.
Rule fields are `id`, `host`, `port`, `access`, `review`, and exactly one of
`agent: "codex"` or `binary: "/usr/bin/curl"`. Configuration never merges grants
from defaults or host settings. Default deny, hard-required Landlock, manual
approval, executable validation and native Loaded revision/hash checks remain.
Existing policy changes require the existing review/reconcile procedure; setup
refuses configuration drift. For profiles, `rules persist` requires a reviewed
manual project-file edit rather than rewriting the resolved configuration.

Host JSON accepts only `state_root`. Its default is
`~/.local/state/openshell-manager`; state must be outside the project checkout
and have no symlink components. State, snapshots, journals, operation/task locks
and generated provider profiles live under `<state_root>/<id>`. Native credentials
remain in distinct gateway providers; no token is stored in project/host JSON.
Gateway configuration stays in the existing host OpenShell config location;
loopback, VM driver and native version checks are unchanged. No host setting can
override permissions or pins. First invocation initializes private local binding
and provider-profile files, including `--check` and `--dry-run`; those modes do
not mutate Gateway/VM resources. Choose a private state root owned by the operator.

## CLI

```bash
export MANAGER_PROJECT=/absolute/path/to/project/project.json
export MANAGER_HOST=/absolute/path/to/private/host.json  # optional
./infra/dev-sandbox/setup.sh --dry-run
./infra/dev-sandbox/verify.sh
./infra/dev-sandbox/start.sh codex
./infra/dev-sandbox/refresh.sh --scope module --module src
```

Alternatively pass `--project FILE --host FILE` to wrappers or `control.py`.
`github.sh` now forwards options after its action; `verify.sh` accepts profile
options. Setup requires Python 3.11+ and PyYAML already installed rather than
installing prerequisites before validating a profile. Explicit operational setup
retains the original pinned bootstrap behavior after validation. Profiled setup
requires the reviewed image digest to exist locally; it refuses automatic builds
that would substitute a different digest. The standalone probe launcher now
requires an explicit `--sandbox` instead of an OMA resource default. These commands
are migration instructions, not operations performed by PR2.

Git synchronization skips active agents, dirty trees, feature/detached branches,
and ahead/diverged histories. Dirty trees are checked before HTTPS migration or
any authentication/fetch operation, so their remote config is untouched. Updates
remain fast-forward only, and interrupted clone destinations are never adopted.
Interactive shells/login/agents now hold the host task lock; setup, refresh and
lifecycle operations take shared leases to prevent racing a new managed session.
Existing unbound sandboxes are refused even if labels resemble Manager labels.

## Limits and dependencies

This is a single-profile CLI process, not a multi-project service. Adapters receive
validated identities through process-local bindings; embedded concurrent use is
unsupported. State ownership and OS filesystem access remain host trust boundaries;
static symlink rejection is not a hostile same-user filesystem race sandbox.
GitHub receive-pack still cannot constrain branch refs in HTTP bodies: use a
repository-scoped credential and server controls. Full live equivalence and native
sandbox-name acceptance were not exercised; offline mocks are not live evidence.

Pins bind committed Manager HEAD; working changes are reviewed here before any
commit. Updating a pin with existing state is refused, including during sessions;
explicit state/resource migration is intentionally deferred to PR4. No imported
OMA state, labels, credentials or images are adopted automatically. PR4 must own
OMA profiles, immutable version.lock, image/evidence review and approved cutover.
No historical paid/live evaluation was rerun or promoted to new acceptance.
Claude/OpenCode live gates remain unchanged. Telegram/native proposal transport
and callback security remain PR3; the manual compatibility inbox is unchanged.

## Verification

The PR1 local baseline was commit `3649226` with a clean working tree; its
complete offline suite passed **190 passed, 16 skipped** before implementation.
This resolves PR1's missing final full-suite rerun after its test-home fix. The
explicit no-profile CLI guard also resolves the example-config prerequisite.
The checkout provides no independent evidence of upstream merge status; no
network fetch, commit, push or PR operation was performed.

Focused verification covered the inherited session/provider/policy/bootstrap/
agent regressions and new generic/synthetic OMA-shaped profiles. New tests cover
placeholder/unknown/duplicate keys, scope and host override rejection, immutable
pins, separate state/provider/locks, conflicting IDs, symlinks, shared lease races,
active setup refusal, existing sandbox refusal and dirty SSH remote preservation.
Existing tests retain diverged Git, linked worktree rejection and native policy
revision/hash/drift coverage.

| Check | Result |
| --- | --- |
| PR1 baseline full offline suite | 190 passed, 16 skipped |
| Focused inherited session/provider/policy/bootstrap/agent regressions | 143 passed, 16 skipped |
| New project profile suite, including both committed-export fixtures | 29 passed |
| Full offline suite before final review correction | 221 passed, 16 skipped |
| Focused session checks after correcting runtime guest-lock resolution | 27 passed |
| Final full offline suite on the completed implementation | **222 passed, 16 skipped in 4.59s** |
| Python AST, JSON/YAML/TOML parsing, all Bash syntax | Passed |
| Final Python syntax and `git diff --check` | Passed |

The necessary final rerun followed a newly found default-argument bug: the guest
lease originally captured the unconfigured lock path. It now resolves the
project lock at invocation, with a dedicated regression test. No successful
paid/live evaluation was repeated. All 16 skips are the inherited explicit
Claude/OpenCode live acceptance cases, not passing evidence.

Changed files: new `configuration.py`, four `config/examples/` files and
`test_project_configuration.py`; updated `control.py`, `session.py`, `workflow.py`,
`github_provider.py`, bootstrap temporary labels, validation launcher resource
selection, setup/verify/github/rules/export/cleanup wrappers, three inherited
regression files, README and this migration note. Version pins, Docker recipes,
Landlock/filesystem policy and manual approval transport were not changed.
No implementation/test failure remains. Upstream PR1 merge provenance and actual
host/image/native acceptance remain review/integration prerequisites outside the
offline verification performed here.

Publication follow-up: after the user authorized pushing these changes,
`git fetch origin main` confirmed that remote main still points to `857407a` and
does not contain PR1 commit `3649226`. PR2 is therefore published on a separate
`feat/pr2-generalize-configuration` branch stacked on the PR1 baseline. PR1 must
merge before PR2 can merge; main is not modified and no PR is created here.
