# PR1 — Import & inventory

This implements PR1 only from the user-supplied `.migration-reference/` snapshot.
The plan identifies OMA commit `f23b8926bf4adc663e5baca936c80a5844cec6ce` as
its source; the snapshot does not include upstream Git provenance to independently
verify that identity. No source history or unrelated OMA files were imported.

All 40 supplied files were read before selection. Thirty-nine reviewed files were
imported; one historical verification record was excluded. The existing paths
are deliberately retained: no packaging/profile framework or Telegram code is
introduced. [The audit manifest](pr1-file-audit.json) records individual source and
imported hashes, permissions, and review decisions. 28 files are
byte-identical; 11 have only the disclosed import adaptations below.

## Old-to-new mapping and individual disclosure review

Old paths are relative to the reference OMA repository; new paths are relative
to this repository. Keeping the paths preserves `HERE.parent.parent` and existing
script/test loading without restructuring.

| Old path | New path | Review and adaptation |
| --- | --- | --- |
| `infra/dev-sandbox/.dockerignore` | `infra/dev-sandbox/.dockerignore` | Byte-identical; deny-all build context, only Dockerfile allowed. |
| `infra/dev-sandbox/Dockerfile` | `infra/dev-sandbox/Dockerfile` | Byte-identical; public upstream base digest and Codex pin; no COPY, credentials or mounts. APT/npm transitive inputs were already not fully locked. |
| `infra/dev-sandbox/Dockerfile.agent` | `infra/dev-sandbox/Dockerfile.agent` | Private local image tag prefix replaced by zeros; agent package/version pins retained. Not built. |
| `infra/dev-sandbox/agents.py` | `infra/dev-sandbox/agents.py` | Byte-identical; CLI argument validation, live gate and numeric-only accounting retained; no auth values. |
| `infra/dev-sandbox/bootstrap.py` | `infra/dev-sandbox/bootstrap.py` | Byte-identical; public release URLs/checksums, no-replacement configuration and pinned binary delivery; privileged calls mocked only. |
| `infra/dev-sandbox/cleanup.sh` | `infra/dev-sandbox/cleanup.sh` | Byte-identical; fixed controller command, never executed. |
| `infra/dev-sandbox/control.py` | `infra/dev-sandbox/control.py` | Exact sandbox/provider identity literals sanitized; policy admission/revision/hash checks, journaling, terminal confirmation and deny boundary unchanged. |
| `infra/dev-sandbox/edit-scope.txt` | `infra/dev-sandbox/edit-scope.txt` | Byte-identical; two generic pilot Python files only; instructions are not writable. |
| `infra/dev-sandbox/export.sh` | `infra/dev-sandbox/export.sh` | Byte-identical; fixed controller export command. |
| `infra/dev-sandbox/gateway.vm.example.toml` | `infra/dev-sandbox/gateway.vm.example.toml` | OMA service comment made generic; loopback VM settings and version preserved; no TLS or host identifiers. |
| `infra/dev-sandbox/github.sh` | `infra/dev-sandbox/github.sh` | Byte-identical; existing explicit provider/credential operator entrypoint. |
| `infra/dev-sandbox/github_provider.py` | `infra/dev-sandbox/github_provider.py` | Repository/provider literals sanitized; exact receipt/composition checks and secret handling retained; public gh checksum unchanged. |
| `infra/dev-sandbox/guest.py` | `infra/dev-sandbox/guest.py` | Byte-identical; trusted guest operations, file/provenance checks, non-root/tool identity discovery; no downloaded source. |
| `infra/dev-sandbox/phase2.json` | `infra/dev-sandbox/phase2.json` | Example sandbox/image/provider/repository, zero private image digest, cleared historical Codex verification list and generic review text; all versions/rule endpoints retained as examples. |
| `infra/dev-sandbox/policy.yaml` | `infra/dev-sandbox/policy.yaml` | Byte-identical; generic curl-only GitHub read access, hard-required Landlock and original filesystem scope. |
| `infra/dev-sandbox/refresh.sh` | `infra/dev-sandbox/refresh.sh` | Byte-identical; existing controller forwarding. |
| `infra/dev-sandbox/rules.sh` | `infra/dev-sandbox/rules.sh` | Byte-identical; operator approval wrapper; no Telegram. |
| `infra/dev-sandbox/session.py` | `infra/dev-sandbox/session.py` | Exact repository/credential-helper URL literals sanitized; locking, HTTPS-only helper, redaction and conservative sync logic retained. |
| `infra/dev-sandbox/setup.sh` | `infra/dev-sandbox/setup.sh` | Byte-identical; existing bootstrap/check/dry-run routing; installs never executed. |
| `infra/dev-sandbox/snapshot.py` | `infra/dev-sandbox/snapshot.py` | Byte-identical; committed exact blobs, private-path/content screening, symlink/size checks and fresh outputs. |
| `infra/dev-sandbox/source-manifest.txt` | `infra/dev-sandbox/source-manifest.txt` | Byte-identical; only the three generic pilot files; no private project source. |
| `infra/dev-sandbox/start.sh` | `infra/dev-sandbox/start.sh` | Byte-identical; existing interactive/autonomous routing. |
| `infra/dev-sandbox/validate.py` | `infra/dev-sandbox/validate.py` | Byte-identical; dummy-only live-probe launcher imported because mocked launcher tests require it; never run against a VM. |
| `infra/dev-sandbox/validate_guest.py` | `infra/dev-sandbox/validate_guest.py` | Byte-identical; generic isolation/network probe source, no recorded host/guest results; never executed. |
| `infra/dev-sandbox/verify.sh` | `infra/dev-sandbox/verify.sh` | Host readiness route retained; private runbook renderer and missing OMA tooling replaced by explicit unsupported --reports error. |
| `infra/dev-sandbox/workflow.py` | `infra/dev-sandbox/workflow.py` | Exact repository/provider literals sanitized; scope filtering, retries, locks, accounting and publication guard logic retained. |
| `infra/dev-sandbox/policies/claude.json` | `infra/dev-sandbox/policies/claude.json` | Byte-identical; public API endpoint template; disabled, manual, default deny; no credential values. |
| `infra/dev-sandbox/policies/github.yaml` | `infra/dev-sandbox/policies/github.yaml` | Exact repository/provider and display strings made examples; methods/paths/binaries remain equally scoped, no added endpoints. |
| `infra/dev-sandbox/policies/opencode.json` | `infra/dev-sandbox/policies/opencode.json` | Byte-identical; separate public provider templates; no provider selected and no credentials. |
| `infra/dev-sandbox/pilot/AGENTS.md` | `infra/dev-sandbox/pilot/AGENTS.md` | Byte-identical; isolated generic calculator task instructions, no business data. |
| `infra/dev-sandbox/pilot/calculator.py` | `infra/dev-sandbox/pilot/calculator.py` | Byte-identical; generic integer multiplication fixture; intentionally preserves existing pilot behavior. |
| `infra/dev-sandbox/pilot/test_calculator.py` | `infra/dev-sandbox/pilot/test_calculator.py` | Byte-identical; local arithmetic fixture assertions. |
| `tools/tests/test_dev_sandbox.py` | `tools/tests/test_dev_sandbox.py` | Byte-identical; temporary Git source export, synthetic credential detection, path/symlink/submodule rejection and gateway template. |
| `tools/tests/test_dev_sandbox_agents.py` | `tools/tests/test_dev_sandbox_agents.py` | Byte-identical; inert adapters/workflows, scope and branch checks, accounting redaction; 16 explicit live skips. |
| `tools/tests/test_dev_sandbox_bootstrap.py` | `tools/tests/test_dev_sandbox_bootstrap.py` | Byte-identical; inert sudo/systemd/Docker/download doubles and temporary config; no active host operations. |
| `tools/tests/test_dev_sandbox_github.py` | `tools/tests/test_dev_sandbox_github.py` | Literal provider/repository matching sanitized; mocked native lifecycle, credential redaction and receipt verification. |
| `tools/tests/test_dev_sandbox_launcher.py` | `tools/tests/test_dev_sandbox_launcher.py` | Byte-identical; mocked VM upload/exec and temporary dummy fixture; no actual guest probes. |
| `tools/tests/test_dev_sandbox_phase2.py` | `tools/tests/test_dev_sandbox_phase2.py` | Byte-identical; mocked policy operations and real local Git apply checks; preserves boundary assertions. |
| `tools/tests/test_dev_sandbox_session.py` | `tools/tests/test_dev_sandbox_session.py` | Repository literals sanitized; temporary local bare repositories, synthetic credentials through local Git helper; no remote network. |
| `infra/dev-sandbox/github-verification.json` | Excluded | Historical live evidence; private account/repository limitations; no runtime/test dependency. |

New repository support files are `.gitignore`, `README.md`, `pytest.ini`,
`tools/tests/conftest.py`, and these migration documents. They contain only generic
instructions, test isolation and audit metadata. The existing plan is unchanged.
No license or attribution claim was invented; ownership/publication approval
remains with the user at review before committing or publishing the import.

## Import adaptations and behavior boundary

Sanitization replaces the exact repository `phuongtuan1803/one-man-agency` with
`example/project`, sandbox `oma-dev-phase2` with `example-dev-phase2`, provider
`oma-github` with `example-github`, injected rule `_provider_oma_github` with
`_provider_example_github`, and image tag `oma-dev-sandbox:openshell-0.1.2` with
`example-dev-sandbox:openshell-0.1.2`. Corresponding code/test literals are changed
together: credential-helper, origin validation, provider checks and policy paths
still bind to one exact identity. This is public-example sanitization, not PR2
configuration decoupling, and must not be used as OMA deployment configuration.

The host-local image digest/tag prefix is replaced with 64/12 zero digits in
`phase2.json`/`Dockerfile.agent`. It is an inert placeholder, not a release pin.
No original host-local digest, live results, logs, tokens, account details or private payloads
are copied. The historical Codex `live_verified` list is empty in the example;
`agents.py` retains the original Codex live capability gate. Offline equivalence
does not prove live behavior for these example identities or a rebuilt image.

`verify.sh` keeps its default `setup.sh --check` behavior. `--reports` returns
status 2 and explains that the OMA renderer was not imported. All other shell
entrypoints retain their arguments and byte content. No security logic was
refactored. Placeholder changes intentionally prevent targeting the original
named sandbox/provider by default, but are not a new runtime safety mechanism.

## Dependency graph and ownership

```text
setup/start/rules/refresh/export/github/cleanup.sh -> control.py
verify.sh -> setup.sh --check
control.py -> bootstrap.py, github_provider.py, snapshot.py, workflow.py, agents.py
workflow.py -> agents.py, snapshot.py, github_provider.py
github_provider.py -> PyYAML (lazy), bootstrap.py (pinned gh provisioning)
control.py -> guest.py/session.py (trusted source executed in guest with python -I)
validate.py -> validate_guest.py (uploaded live probe source; only mocks used here)
control/bootstrap/workflow -> adjacent configs, policies, image recipes, scope files
tools/tests -> modules above; pilot tests -> calculator.py
```

Core code uses the Python standard library plus PyYAML for provider profiles.
The offline suite needs Python 3.11+, pytest, PyYAML and Git. Operational dependencies
are Linux x86_64, Ubuntu/Linux Lite, KVM, Docker, user systemd, native OpenShell
CLI/Gateway/VM driver, guest Python/Git/pytest, Node/npm and pinned agent tools.
Host bootstrap/download/image build paths remain opt-in operator operations.
They were imported as source and exercised only with offline doubles.

Pins retained: OpenShell 0.1.2 (including Debian release SHA), Codex 0.162.1,
Claude Code 2.1.296, OpenCode 1.19.0, gh 2.101.0 (archive and executable SHAs),
and the public upstream Ubuntu base image digest. The pre-existing APT/npm
transitive dependencies are not bit-for-bit archived. No versions were upgraded,
dependencies installed, images built or live verification repeated.

## Security review

Each imported file was reviewed for credential literals, personal/account data,
private business source, auth configuration, raw logs/evidence, host identity,
private URLs and indirect imports. Synthetic credentials in tests are explicit
dummy strings or constructed at runtime. Credential variable names and redaction
code are necessary implementation, not credential material. Reviewed OMA labels,
workspace/error strings and module conventions remain only as documented coupling;
public config/profile identity fields use exact examples.

The preserved tests exercise default deny/manual mode, hard-required Landlock,
exact executables/hostnames, sandbox identity and effective Loaded policy hashes,
provider receipts/composition, configuration/executable drift, confirmation before
policy apply, rollback/recovery, source allowlists, committed blobs, symlink/size/
credential rejection, host-generated patch validation and conservative Git sync.
Codex's broad CLI sandbox flags execute inside the verified MicroVM workflow.
Claude/OpenCode live execution remains gated and their templates unapplied.

Known inherited limits remain visible for PR2/security review:

- Git receive-pack HTTP rules cannot constrain branch refs in request bodies;
  branch restrictions are application checks. Provider credentials/native server
  controls must enforce the project boundary; offline mocks are not proof of it.
- The compatibility inbox parses structured TCP DENIED logs; HTTP method/path
  are unavailable. It is manual endpoint-grant review, not native proposals or
  Telegram approval, and must not become automatic permission granting.
- Session origin HTTPS migration happens before the dirty-work check in the
  original implementation. Tests prove no dirty fetch/merge, but PR2 must review
  this ordering against the plan's stricter no-remote-change-on-dirty requirement.
- Imported operation/task locks and state paths assume a single project. PR2
  must validate isolation, symlink/race behavior and worktree/active-session cases
  before claiming multi-project safety.

`.gitignore` excludes the reference snapshot, local runtime/evidence, environments
and Python caches. Ignore rules prevent accidental inclusion; they do not replace
this individual audit. No active gateway/VM/credential/effective-policy operations
were performed. OMA and the snapshot remain untouched. No commit, push, PR or merge
was made. A heuristic credential scan supplements the content review and cannot
prove absence of every possible secret.

## PR2 dependencies and blockers

| Coupling / blocker | Location | Work required in PR2 |
| --- | --- | --- |
| Manager checkout assumed to be project checkout | `control.py`: `REPO = HERE.parent.parent`; tests' root loading | Separate manager/project roots, validated project schema and package entrypoint. |
| Single local state tree and lock namespace | `control.py`: `.work/openshell-phase2`, operation/task locks; `session.py` source lock | Per-project host state, project identity binding, concurrent isolation and migration/version-lock review. |
| Hardcoded workspace and guest identity | `control.py`, `session.py`: `/sandbox/oma`, `/sandbox/oma-phase2`, `.oma-source.lock`; `oma.phase`, `oma.image`, `oma.dockerfile` labels | Resolve from validated profile; never rename/adopt an active VM implicitly; test valid native sandbox names. |
| Fixed example resource/provider/repository | `control.py`, `session.py`, `workflow.py`, `github_provider.py`, `phase2.json`, `policies/github.yaml` | Exact per-project identities and endpoint/credential target validation; no wildcard expansion. |
| Private image unavailable | `phase2.json`, `Dockerfile.agent` zero digest | Review a project/host image pin and inventory separately, retaining all CLI/base pins; no automatic replacement/build during extraction. |
| Pilot/module scope and tests assume OMA layout | manifests, `workflow.py` module regex/pyproject check/docs filtering; `control.py` pytest path handling | Profile-owned source/edit scope, test commands and exclusions; generic and OMA fixture coverage. |
| Provider writes/branch rules rely on wrapper | `workflow.py`, `policies/github.yaml`, session auth helper | Validate fetch/push/PR scopes, forbid main/merge/force-push by default, verify credential isolation and fail closed. |
| Git sync safety and locking | `session.py`, `control.py`, `workflow.py` | Resolve dirty-origin ordering; verify dirty/diverged/worktree/active sessions and cross-project locking without overwrites. |
| Host defaults and reporting | `bootstrap.py`, gateway template, `verify.sh --reports` | Host-runtime settings separated from project policy; OMA renderer stays in OMA; generic operational documentation. |
| Historical live acceptance cannot transfer | agent gate, empty example verification list; excluded verification record | Preserve existing evidence privately; no live/paid runs until separately approved; integration/cutover is PR4. |
| No upstream snapshot provenance metadata | supplied snapshot, audit manifest | Confirm source commit/ownership before publication; hashes capture exactly the reviewed inputs here. |

PR2 should test a generic fixture and an OMA-shaped fixture with no credentials,
validate config precedence without widening permissions, and add immutable manager
pins/update locks. OMA integration and version.lock belong to PR4; deleting old
OMA code belongs to PR5. No PR2 or Telegram implementation is included here.

## Verification results

Checks ran with existing Python 3.12.3, pytest 9.1.1, PyYAML 6.0.1 and Git.
No dependency installation, image build, host readiness check or live/paid run
was performed.

| Check | Result |
| --- | --- |
| Full offline suite, once: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q` | Initially 185 passed, 16 skipped, 5 failed in 2.51s. Failures were caused by the new guard placing its home inside test directories with empty-directory assertions. |
| Required correction | Moved the guard home to a separate `tmp_path_factory` directory; imported implementation/tests unchanged. |
| Targeted verification of the five failed cases, once after correction | 5 passed in 0.08s (pinned gh provisioning; memory-only Git auth; stateless helper fill/approve/reject). |
| Python AST, JSON/YAML/TOML parsing, Bash syntax | Passed for imported source/config/tests and new test guard. |
| Audit hashes and file permissions | All 39 imported entries matched the per-file manifest; original modes retained. |
| Reference integrity | All 40 supplied files retained original bytes and modes. |
| Version/public identity checks | All version pins retained; no private repository URL or host-local image digest in imported implementation/tests/configs. |
| Credential-pattern scan and manual adjudication | Four source-file findings, all dummy test material; no real credential found. |
| Git scope/ignore review | Original tracked plan unchanged; only PR1 files untracked; snapshot/runtime/evidence ignored. |

Across the full run and five necessary targeted rechecks, all 190 executable
offline cases passed. The full suite was not repeated after the guard-only fix.
Sixteen explicit Claude/OpenCode live acceptance cases remain skipped; they are
not passing live evidence. The recheck was limited to failures introduced by the
new isolation fixture, rather than repeating successful or live evaluations.

Credential scan adjudication (no matched values are recorded):

- `test_dev_sandbox_phase2.py`: `private-key` matches an empty private-key header
  used as a rejection fixture; there is no key body.
- `test_dev_sandbox_agents.py`: `quoted-secret-assignment` matches synthetic
  sensitive-field accounting events.
- `test_dev_sandbox_github.py`: `quoted-secret-assignment` matches explicit dummy
  credential markers for redaction/lifecycle tests.
- `test_dev_sandbox_session.py`: `quoted-secret-assignment` matches explicit dummy
  provider markers for stateless helper/redaction tests.

The automated scan uses the inherited conservative `snapshot.CONTENT_RULES`;
manual review of each file and every import diff supplied the public-disclosure
assessment. No claim of exhaustive secret detection or live equivalence is made.
