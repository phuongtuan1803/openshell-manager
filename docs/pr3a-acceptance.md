# PR3a acceptance report

Implementation branch: `feat/pr3a-uv-packaging`, based on fetched `main`
`2711ffb5aa8f79217997d7a8d070ff714d88042b`. Source of truth: Section 3 and
A01–A12 of the design document read with `git show` from documentation commit
`d06e8b243a0d7632ca619478824688fe59c4a418`. The documentation branch was not merged.

## Results

All acceptance below concerns offline packaging and fixture verification. No
production installation, native host acceptance, credentials, active VM, model
call, PR creation, push, merge, OMA change, permission expansion or tool upgrade
was performed.

| ID | Status | Evidence |
| --- | --- | --- |
| A01 | PASS | PEP 621 metadata; `uv lock --check`; `uv build` created wheel and sdist. Both archive inventories checked by `tools/verify_packaging.py`. |
| A02 | PASS | Harness performs real `uv tool install --offline git+https://…@<full fixture SHA>` with an approved-URL Git rewrite to a disposable local repository; private HOME/tool directories, external working directory, source fixture hidden. Installed CLI reports the exact installed Git SHA. No GitHub installation/live verification claimed. |
| A03 | PASS | 12 SHA-256 comparisons to reviewed-main originals: nine config/policy/recipe assets and three Python payloads. Wheel/sdist bytes and installed `importlib.resources` bytes agree. Old source asset paths are file symlinks to canonical package resources. |
| A04 | PASS | Unit exact-commit fixture and real isolated Git-install dry-run match `manager_revision`; only then are the fixture project binding/provider template created. Distribution file ownership is verified. |
| A05 | PASS | Mismatch, missing distribution/file ownership, absent/malformed provenance, foreign URL, wrong VCS and non-SHA receipts fail closed. Unit guards assert profile/native code is not reached and directories do not change; installed mismatch/plain-wheel tests leave the negative state root absent. |
| A06 | PASS | Shared parser/dispatcher tests; actual shell wrapper vs installed CLI return codes/security failures; positive dry-run, run/start, verify, rules, GitHub, refresh, export, cleanup, source scope/test rejection and OpenShell-environment refusal. Existing agent, lock and argument regressions retained. |
| A07 | PASS | Installed dry-run with all native/install/service/model tools replaced by tripwires; no tripwire executes. Pytest globally blocks network and non-local-Git subprocesses. Missing PyYAML fails with reinstall guidance in both apply/check modes, with zero apt calls. Existing dry-run local binding behavior is preserved and documented. |
| A08 | PASS | Full offline run: 277 passed, 16 expected live skips, two legacy fixture failures. Removed production source-fixture assumptions required explicit test scope fixtures; affected module rerun: 55 passed, including both failures. No other implementation change followed that run. Existing policy, sandbox, source/edit scope, GitHub, locks and dirty-tree checks remain covered. |
| A09 | PASS | Canonical asset hashes and inherited pin assertions unchanged: OpenShell 0.1.2, Codex 0.162.1, Claude Code 2.1.296, OpenCode 1.19.0, gh 2.101.0. Native bootstrap release URLs/checksums unchanged. |
| A10 | PASS | Wheel strict allowlist contains package Python modules, reviewed resources and distribution metadata only (29 files). Sdist public-root allowlist and disclosure scan exclude host state, private profiles/credentials, evidence/logs, reference repositories and caches. Resource contents match reviewed-main hashes. |
| A11 | PASS | `docs/packaging.md`: installation, version/provenance, errors, dependency inspection, source wrappers, update, state-binding constraint and rollback without deleting state/resources. |
| A12 | PASS | No CI workflows exist or were introduced; no deployment/publication steps. Offline guards and the harness enforce local Git/no native effects. Live/paid acceptance remains explicitly skipped. |

## Commands and actual outcomes

Commands used `UV_CACHE_DIR=/tmp/pr3a-uv-cache` because the default user cache is
outside writable roots. Dependency download/cache preparation was separately
network-enabled; the test suite and installed integration harness ran offline.

- `uv lock --check`: exit 0, eight packages resolved.
- `uv run --locked python -m pytest -q`: initial final full run exited 1:
  **277 passed, 2 failed, 16 skipped in 2.32s**. Both failures were legacy setup
  tests expecting production variables to point at test scope files.
- After moving those scope defaults into the inherited test fixture,
  `uv run --locked python -m pytest -q tests/test_dev_sandbox_phase2.py`:
  **55 passed in 0.10s**, exit 0. Only affected checks were rerun as requested;
  all 279 offline cases have passing coverage across the full run and correction.
- Focused configuration/bootstrap/GitHub checks: **90 passed**. Focused
  provenance/parser/package checks also passed before the full run.
- `uv build`: exit 0, `dist/openshell_manager-0.1.0-py3-none-any.whl` and
  `dist/openshell_manager-0.1.0.tar.gz`; wheel built from the sdist.
- `uv run --locked python tools/verify_packaging.py`: exit 0; archive allowlists,
  12 reviewed hashes, installed hashes, actual PyYAML **6.0.3**, full-SHA local Git
  installation outside source, wrapper/security/dirty-source comparisons, native
  tripwires, mismatch and plain-wheel refusal.
- `git diff --check`: exit 0.

## Limits and remaining risks

The 16 inherited Claude/OpenCode live cases remain skipped by design. These results
are not native/live acceptance and do not verify a remote GitHub installation.
The final implementation commit has not been published or deployed.

PEP 610 metadata is trusted local installer evidence, not a signature against a
compromised host. Plain wheels and sdists intentionally cannot run operational
commands without approved VCS provenance. Development mode refuses dirty tracked
files or conflicting installed metadata. `uv.lock` locks repository development,
not Git tool-install resolution; the only runtime dependency is constrained exactly
to PyYAML 6.0.3 and was inspected in the installed fixture environment.

Existing project state binds the Manager revision. An upgrade requires a reviewed
project pin and separate state migration/cutover decision; no bindings or active
resources were rewritten. Resource compatibility links require normal Git symlink
support on the supported Linux development host. No PR3b work is included.
