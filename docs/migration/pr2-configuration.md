> **Layout update:** After extraction, current commands live under `scripts/`, Python sources under `src/openshell_manager/`, Manager configuration under `config/`, Docker recipes under `docker/`, and tests under `tests/`. The historical PR2 notes below describe the original layout.

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
standalone Git checkout; linked worktrees and symlinks are rejected. Its origin
must identify the configured GitHub repository using exact HTTPS, SSH URL or
Git SCP syntax, with optional `.git`. Validation reads the raw origin and Git’s
effective fetch/push URLs, so missing/multiple origins, foreign push targets,
URL rewrites to another repository, HTTP and userinfo URLs fail closed. These
checks never contact GitHub or alter remotes. Identity is rechecked immediately
before snapshot creation, patch export, autonomous execution and publication.

Project IDs use lowercase letters, numbers and hyphens, start with a letter,
and contain 2–15 characters when combined with the required `-dev` suffix.
Sandbox names are limited to OpenShell’s 19 characters; 20 or more fail before
host changes. Sandbox names are `<id>-dev`, provider names are
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
`--test-path` values must be both configured and within the active source scope.
Module runs automatically intersect configured test paths with the selected
module file lists; multiple modules use their union. If no configured tests are
in scope, execution fails before native checks, snapshot upload or model calls.
The same scope checks apply to the workflow API and publication test runner. The base branch is project-owned;
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
./scripts/setup.sh --dry-run
./scripts/verify.sh
./scripts/start.sh codex
./scripts/refresh.sh --scope module --module src
```

Alternatively pass `--project FILE --host FILE` to wrappers or `control.py`.
`github.sh` now forwards options after its action; `verify.sh` accepts profile
options. Setup requires Python 3.11+ and Git to validate the JSON profile and
checkout identity. On supported Linux Lite/Ubuntu x86_64 hosts, one-command
`setup.sh --project FILE` can then install the existing `python3-yaml` dependency
if missing. It validates the profile, base override, checkout origin, Manager pin
and state binding, and takes operation/task leases before dependency installation.
Invalid profiles and active sessions cannot reach the installer. Existing PyYAML
is reused. Check/dry-run/offline-agent modes never install dependencies; missing
PyYAML produces an actionable setup instruction. Hosts without usable Python/Git
or with an unsupported interpreter/bootstrap platform need explicit prerequisites.
Explicit operational setup retains the pinned host bootstrap after validation. Profiled setup
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


## PR2 review corrections

Before editing, the clean current branch was `review/pr1-import-inventory` at
`53d5448`, directly following PR1 `3649226`. Fixes were applied in place; the
combined branch was not rebased or recreated, and no existing commits were
reapplied. This review adds native sandbox-name boundaries, read-only checkout
origin checks at load/operation boundaries, automatic module test selection and
validated one-command PyYAML provisioning. It does not change version pins,
security policies, existing state bindings or credential handling.

Regression coverage includes 18/19/20-character sandbox boundaries; supported
Git URL forms, absent/foreign/multiple origins, foreign push URLs and `insteadOf`
rewrites; origin drift before snapshot/export/publish; module/multi-module test
selection, explicit out-of-scope tests and early no-tests failure; supported and
unsupported dependency provisioning, existing PyYAML reuse, invalid base/profile
rejection, and active-session exclusion. All installation and native calls remain
offline doubles.

| Review verification | Result |
| --- | --- |
| Focused project/agent/bootstrap regressions | 109 passed, 16 skipped |
| One final full offline suite | **255 passed, 16 skipped in 5.49s** |
| Python AST, JSON/YAML/TOML parsing, Bash syntax | Passed |
| `git diff --check` | Passed |

Changed files are `configuration.py`, `control.py`, `workflow.py`, `bootstrap.py`,
`setup.sh`, `test_project_configuration.py`, `test_dev_sandbox_bootstrap.py`,
README and these migration notes. Earlier successful paid/live evidence was not
rerun or claimed as new validation. The skipped cases remain the existing
Claude/OpenCode live acceptance cases. No host tools or installers were run
outside mocks; OMA, Gateway/VM resources, credentials and pins remain untouched.

No implementation/test blocker remains. Python 3.11+ and Git must be available
before profile validation; non-system interpreters may not see the distro
`python3-yaml` package and then require explicit dependency installation. Native
host acceptance and OMA cutover remain PR4, and Telegram remains PR3. Review and verification took place on the existing combined branch without
rebasing, recreating the branch, creating a PR or merging. The user subsequently
authorized committing these verified corrections to the PR1 branch.
