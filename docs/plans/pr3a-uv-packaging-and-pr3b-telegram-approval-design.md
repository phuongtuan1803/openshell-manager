# PR3a + PR3b — OpenShell Manager Detailed Architecture

> Status: APPROVED DIRECTION / IMPLEMENTATION PROPOSAL — not implemented.
> Date: 2026-10-15. Owner/decision maker: Founder.
> Repository: phuongtuan1803/openshell-manager.
> Baseline reviewed: main at 2711ffb5aa8f79217997d7a8d070ff714d88042b.
> Decision: split existing PR3 into **PR3a (uv package/CLI)** and **PR3b (Telegram Approval P1)**.
> This document is design-only. Do not assume native/live acceptance or deploy automatically.

## 1. Goals, boundaries and dependencies

Deliver a reusable host-side Manager installed by uv, followed by a narrowly scoped, human-operated Telegram approval bridge. Preserve existing sandbox isolation and review requirements. The tool must remain usable without an OMA checkout; OMA integration/cutover is PR4 and removal of legacy OMA code is PR5.

| Phase | Depends on | Deliverable | Excluded |
| --- | --- | --- | --- |
| PR3a | PR1/PR2 main | Installable Python package, unified CLI, verified immutable revision, packaged assets, compatible wrappers/tests | Telegram, host upgrades, changed policy, OMA edits |
| PR3b | merged PR3a | Host native-proposal adapter, SQLite queue, Telegram bridge, security tests, service lifecycle/runbook | Generic authorizer, filesystem/process grants, auto-approve, dashboard, OMA M11 |
| PR4 | approved Manager version | OMA-owned profile/version pin/integration and live cutover checks | Manager feature implementation |
| PR5 | accepted cutover | Remove duplicate implementation from OMA | Premature cleanup |

### Non-negotiable principles

1. OpenShell CLI/Gateway/MicroVM remain external native resources; a Python package does not replace their isolation boundary.
2. Installed Manager version must match the consuming project's approved immutable Manager revision before any host, policy, VM or credential mutation.
3. Manager public source/resources must not contain private project code, host credentials, real source snapshots, tokens or unredacted logs.
4. Existing OpenShell 0.1.2, Codex 0.162.1, Claude Code 2.1.296, OpenCode 1.19.0 and gh 2.101.0 pins are not changed by PR3.
5. An approval never silently widens source, filesystem, GitHub or network scope. All unexpected information and unavailable native evidence fail closed.
6. No automatic model inference, paid evaluations, remote host operations or OMA changes during offline implementation/testing.
7. Explicit founder authorization is required before opening a PR or merging; implementation changes use a feature branch and are not bundled with the design-only branch.

### Architectural overview

~~~mermaid
flowchart LR
    P["Project-owned profile<br/>+ manager_revision"] --> CLI["Installed Manager CLI"]
    UV["Pinned Git commit"] --> INSTALL["uv tool install"] --> CLI
    CLI --> CORE["Existing sandbox core"]
    CORE --> OS["OpenShell CLI / Gateway"] --> VM["MicroVM Agent"]
    OS --> NATIVE["PR3b Native Adapter"]
    NATIVE --> QUEUE[("SQLite Approval Store")]
    QUEUE <--> TG["Host Telegram Bridge"]
    TG <--> BOT["Telegram Bot API"]
~~~

## 2. Verified starting point / technical debt

At the reviewed main, Python sources are in src/openshell_manager; wrappers in scripts; defaults and policies in config; recipes in docker; tests in tests. There is no pyproject.toml or distribution CLI entrypoint at the reviewed baseline. Existing scripts execute the source control.py using python3.

Observed package blockers:
- Existing modules use sibling absolute imports (for example import snapshot, import bootstrap); these are not reliable as installed package imports.
- Several files derive config/defaults, policies or Dockerfile using Path(__file__).parents[2]. Those paths assume source checkout layout.
- control.py reads guest.py and session.py as source text for execution inside the VM. Installed wheel must retain exact access to these code resources.
- PR2 validates that manager_revision resembles a forty-character Git SHA, but verification of the *installed running artifact's* revision is a separate requirement for PR3a.
- bootstrap.py may install distro python3-yaml after profile validation; this is inappropriate for the isolated uv package interpreter.
- control.py compatibility inbox currently parses structured TCP DENIED logs; this is NOT a complete native proposal protocol and cannot authorize missing method/path/scope.

Do not refactor modules into a new layered framework just to satisfy packaging.

## 3. PR3a — uv packaging and unified CLI

### 3.1 Target behavior

Support both source development (uv run) and installed operation (uv tool install from a full immutable Git SHA). Once installed, running the CLI must not require the Manager Git checkout or the consuming repository to have Python packages installed. It still requires supported Linux/native tools for operational commands.

Proposed invocation examples (commands below are future CLI contract, not current commands):

~~~bash
uv tool install "git+https://github.com/phuongtuan1803/openshell-manager.git@<FULL_MANAGER_COMMIT_SHA>"
openshell-manager version
openshell-manager setup --project /absolute/path/project.json --dry-run
openshell-manager verify --project /absolute/path/project.json
openshell-manager start codex --project /absolute/path/project.json
~~~

Keep MANAGER_PROJECT and MANAGER_HOST environment support, with explicit --project/--host taking precedence according to existing policy. Missing/invalid profiles must fail before native host mutations. Keep the existing source scripts as compatibility wrappers through PR4; do not claim CLI and old wrappers to be equivalent until regression-tested.

### 3.2 Proposed modules and ownership

~~~text
pyproject.toml                              NEW - PEP 621 metadata, console script, build backend
uv.lock                                     NEW - development resolution lock (not an implicit tool-install runtime lock)
src/openshell_manager/__main__.py           NEW - python -m entrypoint
src/openshell_manager/cli.py                NEW - argparse dispatcher, no business logic duplication
src/openshell_manager/provenance.py         NEW - active installed revision verification
src/openshell_manager/resources/            NEW - bundled immutable defaults/policies/recipes
src/openshell_manager/control.py            MODIFY - package imports and delegated CLI
src/openshell_manager/configuration.py      MODIFY - resource resolver + pin verification
src/openshell_manager/bootstrap.py          MODIFY - package resource resolver, no system-PyYAML bootstrap for uv
src/openshell_manager/github_provider.py    MODIFY - resource resolver/import compatibility if needed
src/openshell_manager/{guest,session}.py    MODIFY ONLY IF required by package resource handling
scripts/*.sh                                MODIFY - thin compatibility shims, no duplicated logic
tests/test_packaging.py                     NEW
tests/test_cli_compat.py                    NEW
tests/test_provenance.py                    NEW
docs/packaging.md                           NEW - user-facing install/version/rollback runbook
~~~

Actual filenames may vary slightly only if a simpler equivalent preserves the same contracts. Keep one canonical copy of every packaged default/policy/Docker recipe. Prefer importlib.resources and a safe extraction to a private temporary file only when a native CLI requires a filesystem path; clean temporary files and never write a shipped resource back into the public package. Project/host JSON and actual state remain external, not bundled.

Package resources needed for parity include config/defaults/phase2.json, config/policies (including github.yaml), reviewed Docker recipes, gateway example where referenced, and the guest/session Python payloads. Build tests must enumerate expected files and validate their bytes/hashes against canonical sources before retiring source-only paths.

### 3.3 CLI and backward compatibility contract

Root executable: openshell-manager. Proposed subcommands: version, setup, start, verify, rules, refresh, github, export, cleanup, and the existing run functionality. Preserve existing argument constraints, permitted agents, --scope/--module/--test-path validation, project binding, all safety confirmations and return-code semantics.

Do not introduce a default project, remote auto-discovery, automatic credentials, or a new policy source. Avoid adding aliases only for aesthetics. Wrappers may invoke the installed CLI when available, but tests must also prove a developer source checkout works without prior global installation.

Read-only commands (version and static package introspection) should not require a project. Operational commands continue to require a validated project profile; verify/setup --dry-run must preserve the existing host-interaction contract rather than secretly installing tooling.

### 3.4 Immutable provenance/version contract

**Project authority:** project.json manager_revision = the reviewed full Git commit SHA of Manager. This is a source pin and is separate from package semantic version.

**Installed VCS authority:** read installed distribution metadata for the actual running package, including PEP 610 direct_url.json vcs_info.commit_id when available. Validate that the VCS URL is the approved Manager source and full commit ID matches manager_revision exactly. Never infer a revision merely from package name, version, a user's environment string or a branch/tag name.

**Source development authority:** permit source-tree developer invocation only if detected as a real Manager Git checkout with no uncommitted tracked changes, HEAD matches manager_revision and no conflicting installed package metadata. Explicitly report this as a development mode. Do not use Git probing to silently replace a missing installed-distribution provenance.

**Wheel/sdist from another source:** if trustworthy VCS provenance cannot be established, return an actionable error and prohibit state/host-changing operations. A future signed release receipt may solve this separately; do not invent an unsigned embedded SHA and treat it as proof.

**Safety order:** parser -> package revision proof -> strict profile/checkout validation -> state binding/locks -> native operations. Keep the existing harmless read-only diagnostics available where provably safe. On mismatch, fail before native OpenShell/Docker/Git network commands and before creating/modifying state.

**Lock caveat:** uv.lock governs this repository's reproducible development workflow. uv tool install from Git resolves tool dependencies in its own environment and does not automatically enforce the repository's uv.lock. PR3a must document and verify the actual resolved tool dependencies or explicit constraints, and must not claim uv.lock alone locks uv tool installations.

### 3.5 Packaging and dependency behavior

Use a minimal PEP 621 pyproject with requires-python >=3.11, an appropriate build backend, PyYAML as declared runtime dependency and an entrypoint under [project.scripts]. Keep test-only tools in a development dependency group. Prefer no optional production dependencies beyond what already exists. Do not add Telegram library in PR3a.

Replace system python3-yaml installation as the import path for the installed uv interpreter with the packaged dependency; preserve the current host bootstrap of *other* approved external native dependencies in explicit apply mode only. No shell-based dependency install in tests, checks, version, dry-run or package import.

### 3.6 PR3a acceptance criteria (must have objective evidence)

| ID | Criterion | Verification/evidence |
| --- | --- | --- |
| A01 | Valid PEP 621 package, successful uv lock check/build, wheel/sdist created | uv lock --check; uv build; inspect both archives |
| A02 | Isolated uv tool install from a full commit SHA, CLI works outside checkout | temporary HOME/uv tool dir + subprocess smoke test |
| A03 | Required resources available through installed package, byte-identical to reviewed canonical originals | extracted wheel inventory + resource hash assertions |
| A04 | Installed source revision proven equal to project manager_revision before mutations | positive exact-commit fixture |
| A05 | SHA mismatch/unknown provenance rejected before native commands/state modifications | negative mock-native and directory assertions |
| A06 | Old shell wrappers and new CLI preserve argument, behavior, security failures and meaningful exit codes | golden parser/runner fixtures |
| A07 | setup --dry-run and offline tests never install/start Gateway/VM/Docker or call models | subprocess/network guard assertions |
| A08 | Existing policy, sandbox, GitHub, source/edit scope, task locks and dirty-tree guards unchanged | full inherited offline regression suite |
| A09 | Existing CLI/agent/policy pins unchanged, no automatic upgrade | configuration snapshot assertion |
| A10 | Public package/wheel/sdist excludes secrets, private project profiles, host state, evidence, logs | manifest and disclosure scan |
| A11 | docs/packaging.md shows install, version check, expected errors, upgrade procedure and safe rollback | docs review |
| A12 | CI is read-only, no host side effects or automatic PR/merge, no unapproved paid/live validation | workflow review and tests |

Suggested check commands after implementation (these are not evidence of current execution):

~~~bash
uv lock --check
uv run --locked python -m pytest -q
uv build
# Then test installed CLI in an isolated temporary environment with mocked host tools.
git diff --check
~~~

Avoid adding a dependency on report rendering, browser tooling or OMA CI. Only refresh generated project indexes if this repository actually has a canonical generator requiring it.

### 3.7 PR3a rollback and stop points

Rollback means uninstalling/reinstalling a previously approved pinned Manager build in an isolated tool environment; never delete host state, native credentials, active sandbox or user work. If local version/receipt is inconsistent, refuse setup/start and show the exact safe remediation.

STOP for Founder decision on: inability to prove installed commit identity; package resource divergence; changes to active native permissions or version pins; request to rewrite project config schema; host credential operations; OMA changes; broad refactor; or unexpected public data.

## 4. PR3b — Telegram Approval P1

PR3b starts only after acceptance and approval of PR3a. This section is a detailed contract, not permission to implement PR3b during PR3a.

### 4.1 Authority, native API and capability gate

The native OpenShell 0.1.2 policy-advisor documentation describes:
- openshell rule get SANDBOX --status pending
- openshell rule approve SANDBOX --chunk-id CHUNK_ID
- openshell rule reject SANDBOX --chunk-id CHUNK_ID --reason REASON

These grant/reject **network-rule proposals**, not arbitrary filesystem/process/Git/admin privileges. Approved rules can take effect without a sandbox restart and can remain effective beyond a single agent request; therefore P1 UI says "Approve rule", not "Approve once" or "Approve session".

Before implementing a writable native adapter, interrogate the pinned installed CLI --help/output on an explicitly authorized, isolated test host or safe CLI fixture. The human-readable pending output alone is not a trustworthy stable machine interface for automated approval. Prefer a structured, versioned native API/output. If the exact proposal identity, scoped rule contents, risk details, sandbox and freshness cannot be retrieved unambiguously, native approve through Telegram is disabled, and notifications remain review-only. This gate is mandatory even if the docs show approval commands.

Native proposal remains human-operated/manual. Do not enable global auto approvals, relax Landlock/default-deny, or accept provider/policy drift. A compatibility path may consume the existing structured TCP DENIED logs only to notify and direct the Founder to manual host review. Fallback logs are NEVER sufficient to auto-grant or synthesize native IDs.

### 4.2 Components

~~~text
src/openshell_manager/approval/
  models.py          immutable typed proposal/decision metadata; sanitized risk fields
  native.py          pinned native discovery/get/approve/reject adapter
  compatibility.py   read-only DENIED notification fallback, no authorization
  store.py           sqlite3 queue, offset, journal and atomic state transitions
  verifier.py        project/sandbox/identity/TTL/fingerprint/authorization/replay checks
  telegram.py        outbound Telegram getUpdates, inline keyboard, safe rendering
  service.py         single-profile host loop + reconciliation + shutdown
tests/test_approval_native.py
tests/test_approval_security.py
tests/test_approval_store.py
tests/test_approval_telegram.py
docs/runbooks/telegram-approval-p1.md
~~~

No new API server, webhook listener, Redis, n8n, LangGraph, dashboard, secrets inside VM, or extra agent. Prefer standard-library sqlite3 and a minimal HTTP transport. One active project/one bridge/one dedicated Telegram bot in P1, with an explicit process lock to prevent competing pollers. Other project/bot configurations are future work.

### 4.3 End-to-end flow

~~~mermaid
flowchart LR
    S["Sandbox network request"] --> GW["Native pending proposal"]
    GW --> AD["Read + validate native identity"]
    AD --> Q[("Host SQLite queue")]
    Q --> TG["Telegram notice / buttons"]
    TG --> F["Founder callback"]
    F --> V{"Authorized and fresh?"}
    V -->|"No"| DENY["No permission change"]
    V -->|"Yes"| R["Re-read native proposal"]
    R -->|"Identity/scope unchanged"| ACT["Native approve or reject"]
    R -->|"Drift or unknown"| DENY
    ACT --> E["Verify native result + audit"]
~~~

Process rules:
1. Discover exact pending native proposals. Sanitize descriptions before posting (untrusted strings can include source/secrets/markup).
2. Store only a minimal metadata snapshot/fingerprint tied to project ID, immutable Manager pin, native sandbox identity, chunk ID, exact rule scope, policy/provider revision or hash when available, observed time, expiry and decision state.
3. Send redacted Telegram notification with sandbox, action, exact endpoint/executable, scope, risks and a short-lived opaque callback token. Never put a complete policy or credentials in callback payload.
4. Verify allowed numeric Telegram user_id AND chat_id, message/callback binding, token, request state, expiry, project and sandbox identity, and native effective policy/provider.
5. Atomically claim the pending request to prevent concurrent callbacks from causing multiple native changes. Re-read pending native proposal and compare exact scope/fingerprint before invoking native approve/reject.
6. Call native action only after verified approval. Re-read native effective state, then mark approved/rejected and write a sanitized durable audit. Native conflicts or uncertain results require review rather than automatic retry.
7. Update Telegram message status; failure to update Telegram never causes an implicit grant.

### 4.4 Queue/data model and transitions

Store in private state_root/<project-id>/approval (mode 0700, DB/files 0600 where applicable); never in public repo or project source tree.

Minimum tables: requests (request_id, project_id, sandbox_id, native_chunk_id, rule_fingerprint, native_revision, expires_at, status, telegram_message_id), callbacks (hashed opaque token, action, processed time), bot_cursor (update offset), and sanitized audit (who, what, when, before/after revision/hash and native result). Separate bridge state from existing compatibility inbox and reuse existing project-bound file/operation lock conventions to prevent races.

~~~mermaid
stateDiagram-v2
    [*] --> Pending
    Pending --> Applying: verified callback
    Pending --> Rejected: explicit native reject
    Pending --> Expired: time window elapsed
    Pending --> Stale: changed identity or rule
    Applying --> Approved: native confirmation
    Applying --> Rejected: native reject confirmed
    Applying --> NeedsReview: outcome unknown
    NeedsReview --> [*]
    Approved --> [*]
    Rejected --> [*]
    Expired --> [*]
    Stale --> [*]
~~~

Rules:
- TTL default proposed 15 minutes; expires *Telegram authority*, not necessarily native proposal or already-approved rule.
- Duplicate events are idempotent; never run native mutation twice just because a callback was replayed.
- After crash/restart during Applying, reconcile native state; never blindly repeat approve.
- Unauthorized/stale/expired/error/offline preserve default deny; no automatic approval or escalation.
- Never auto-persist a reviewed runtime rule in the project profile. Durable policy changes require separate reviewed config/PR.
- Bound polling rate, request queue, backoff and message length; do not block interactive development when Telegram unavailable.

### 4.5 Telegram and host trust boundary

Use Telegram Bot API outbound long polling; no inbound webhook or exposed host port. Separate bot from OMA M11 Gate to avoid competing getUpdates consumers. Credentials are in private host configuration/environment (0600, never logged, never passed to guests); project public profile does not contain token. Verify both user_id and chat_id on each update. Use a random opaque callback token of bounded length; bind server-side to the exact proposal and expected action.

Proposed operator commands (not yet present):
- openshell-manager approval setup --project ... : validate config, describe private credentials; never auto-grant.
- openshell-manager approval serve --project ... : run outbound host bridge under an explicit single-instance lock.
- openshell-manager approval status --project ... : read-only health/status.
- openshell-manager approval pending --project ... : sanitized list.
- openshell-manager approval stop --project ... : safely stop bridge; no native rule deletion.

Optional P1 operational integration: a reviewed systemd user service template. It must be explicitly enabled by the operator; tool installation must not start/enable it. Document online operation, offline fallback and disable/rollback.

### 4.6 PR3b acceptance criteria

| ID | Criterion | Required regression/evidence |
| --- | --- | --- |
| B01 | Pinned native 0.1.2 capability and structured proposal contract positively identified | native adapter contract fixture/probe; otherwise review-only mode |
| B02 | Get/list/approve/reject address exact sandbox and chunk ID; no synthetic scope widening | mocked native calls and refusal cases |
| B03 | Native manual default-deny preserved; no global auto approval | policy/config diff assertions |
| B04 | Pending proposal delivered once with sanitized scope/risk and correct TTL | duplicate polling and payload tests |
| B05 | Unauthorized user/chat/callback never changes native policy | negative authorization tests |
| B06 | Expired, replayed, duplicate and concurrent callback transitions are safe | atomic store/race tests |
| B07 | Drift in project, sandbox, proposal rule, provider or effective policy invalidates prior Telegram approval | stale fingerprint tests |
| B08 | Native approve/reject verified against actual resulting state | success, refusal and inconsistent-state tests |
| B09 | Telegram failure or bot offline leaves sandbox denied and queue recoverable | network/timeout fault injection |
| B10 | Restart during Applying does not blindly repeat native approve | crash/recovery tests |
| B11 | Compatibility DENIED fallback is read-only notification; no log-derived authorization | missing method/path and forged log tests |
| B12 | Telegram token never enters logs, Git, guest arguments or public artifacts | leakage/disclosure tests |
| B13 | Single active poller, explicit service lifecycle, safe stop/rollback | process lock and mock service tests |
| B14 | Legacy core/sandbox/Git/source scope/pins preserved | full inherited offline regressions |
| B15 | Separately approved dummy-only native host acceptance is documented without claiming it ran | live evidence status PASS/FAIL/SKIP with sandbox/revision |

## 5. Test matrix, release gates and rollback

| Test class | PR3a | PR3b |
| --- | --- | --- |
| Unit and existing offline regressions | Required | Required |
| Installed-package isolated smoke | Required | Required on pinned PR3a |
| Resource archive/disclosure scan | Required | Required if package contents change |
| Host commands/network in offline CI | Forbidden | Forbidden |
| GitHub publication, sandbox restart, policy mutation from CI | Forbidden | Forbidden |
| Paid coding-agent test call | Not required | Not required |
| Live native host/sandbox test | Not required | Requires explicit later approval for evidence |
| PR creation/merge | Never automatic | Never automatic |

Use one final focused/full local suite after all edits rather than repetitive paid or full live checks. Commit only sanitized evidence and small deterministic test fixtures; track total runtime and skip reasons. Distinguish offline tests from native acceptance. CI must be read-only.

Rollback PR3a: reinstall previous approved Git-pinned tool without modifying state/VM/policy. Rollback PR3b: disable bridge/service, retain audit, let existing native/manual approval continue; do not delete/rewrite sandbox/policy, leave pending approvals ungranted, and inspect uncertain in-flight changes on host.

## 6. Delivery sequence and ownership

1. Commit this design proposal on a documentation-only branch; do not create an implementation PR as a side effect.
2. PR3a implementation uses a separate feature branch based on up-to-date main. Implement only Section 3 and A01-A12. Review native behavior equivalence and package install/distribution. No Telegram code.
3. Founder reviews PR3a; only after approval/merge does PR3b begin, limited to Section 4 and B01-B15.
4. PR4 migrates OMA to reviewed immutable Manager commit; PR5 then removes old OMA implementation after independent acceptance.

Before changing any permission, credential, systemd service, OpenShell native version, OMA file, active VM, or publication state, stop and request explicit Founder approval.

## 7. Source of truth and relevant references

- Existing plan: docs/plans/openshell-extraction-and-telegram-approval.md
- PR2 configuration/migration: docs/migration/pr2-configuration.md
- uv tool install: https://docs.astral.sh/uv/concepts/tools/
- uv command reference: https://docs.astral.sh/uv/reference/cli/
- Direct URL / VCS provenance (PEP 610): https://packaging.python.org/en/latest/specifications/direct-url/
- OpenShell policy advisor 0.1.2: https://docs.nvidia.com/openshell/v0.1.2/how-it-works/policies/advisor
- Telegram Bot API: https://core.telegram.org/bots/api

All CLI/file examples marked proposed are design contracts, not claims of current implemented behavior.
