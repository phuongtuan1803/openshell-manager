# PR3a acceptance report

Implementation branch: `feat/pr3a-uv-packaging`, based on fetched `main`
`2711ffb5aa8f79217997d7a8d070ff714d88042b`. Source of truth: Section 3 and
A01–A12 of the design document read with `git show` from documentation commit
`d06e8b243a0d7632ca619478824688fe59c4a418`. The documentation branch was not merged.

## Results

All acceptance below concerns offline packaging and fixture verification. No
production installation, native host acceptance, credential access, active VM,
model call, PR creation, merge, OMA change, permission expansion or native tool
upgrade was performed.

Published branch status was verified with `git ls-remote --heads origin
feat/pr3a-uv-packaging`: the remote branch points to implementation commit
`eb13ea0260541c744d5d71a0d79af6efdc8a1606`, which was pushed at the user's request.
The accompanying finalization commit is local on the same branch and has not been
pushed. No PR or merge was performed as part of this work.

| ID | Status | Evidence |
| --- | --- | --- |
| A01 | PASS | PEP 621 metadata; `uv lock --check`; `uv build` created wheel and sdist. Both archive inventories checked by `tools/verify_packaging.py`. |
| A02 | PASS | Harness performs real `uv tool install --offline git+https://…@<full fixture SHA>` with an approved-URL Git rewrite to a disposable local repository; private HOME/tool directories, external working directory, source fixture hidden. Installed CLI reports the exact installed Git SHA. No GitHub installation/live verification claimed. |
| A03 | PASS | 12 SHA-256 comparisons to reviewed-main originals: nine config/policy/recipe assets and three Python payloads. Wheel/sdist bytes and installed `importlib.resources` bytes agree. Old source asset paths are file symlinks to canonical package resources. |
| A04 | PASS | Unit exact-commit fixture and real isolated Git-install dry-run match `manager_revision`; only then are the fixture project binding/provider template created. Distribution file ownership is verified. Eight new regression cases prove `commit_id` remains authoritative regardless of a missing, mutable or SHA-valued `requested_revision`. |
| A05 | PASS | Mismatch, missing distribution/file ownership, absent/malformed provenance, foreign URL, wrong VCS and non-SHA receipts fail closed. Unit guards assert profile/native code is not reached and directories do not change; installed mismatch/plain-wheel tests leave the negative state root absent. |
| A06 | PASS | Shared parser/dispatcher tests; actual shell wrapper vs installed CLI return codes/security failures; positive dry-run, run/start, verify, rules, GitHub, refresh, export, cleanup, source scope/test rejection and OpenShell-environment refusal. Existing agent, lock and argument regressions retained. |
| A07 | PASS | Installed dry-run with all native/install/service/model tools replaced by tripwires; no tripwire executes. Pytest globally blocks network and non-local-Git subprocesses. Missing PyYAML fails with reinstall guidance in both apply/check modes, with zero apt calls. Existing dry-run local binding behavior is preserved and documented. |
| A08 | PASS | Complete offline suite after the latest fixture correction: 279 passed, 16 expected live skips, zero failures (3.65s). Subsequent focused provenance suite: 19 passed, including eight new requested/resolved revision cases. No runtime code changed during finalization. Existing policy, sandbox, source/edit scope, GitHub, locks and dirty-tree checks remain covered. |
| A09 | PASS | Canonical asset hashes and inherited pin assertions unchanged: OpenShell 0.1.2, Codex 0.162.1, Claude Code 2.1.296, OpenCode 1.19.0, gh 2.101.0. Native bootstrap release URLs/checksums unchanged. |
| A10 | PASS | Wheel strict allowlist contains package Python modules, reviewed resources and distribution metadata only (29 files). Sdist public-root allowlist and disclosure scan exclude host state, private profiles/credentials, evidence/logs, reference repositories and caches. Resource contents match reviewed-main hashes. |
| A11 | PASS | `docs/packaging.md`: installation, version/provenance, errors, dependency inspection, source wrappers, update, state-binding constraint and rollback without deleting state/resources. |
| A12 | PASS | No CI workflows exist or were introduced; no deployment/publication steps. Offline guards and the harness enforce local Git/no native effects. Live/paid acceptance remains explicitly skipped. |

## Commands and actual outcomes

Commands used `UV_CACHE_DIR=/tmp/pr3a-uv-cache` because the default user cache is
outside writable roots. Dependency download/cache preparation was separately
network-enabled during the original implementation; finalization reused that cache.
The test suite and installed integration harness ran offline.

- `uv lock --check`: exit 0, eight packages resolved.
- `uv run --locked python -m pytest -q`: final complete offline run after the
  committed legacy fixture correction, **279 passed, 16 skipped in 3.65s**, exit 0.
  The two failures recorded in the earlier report are resolved. This was the one
  full-suite run during finalization.
- Subsequently added eight focused `requested_revision`/`commit_id` regression
  cases without changing runtime code. `uv run --locked python -m pytest -q
  tests/test_provenance.py`: **19 passed in 0.06s**, exit 0. The full-suite count
  above predates these eight additions; only the affected suite was rerun.
- `uv build --offline`: exit 0, `dist/openshell_manager-0.1.0-py3-none-any.whl`
  and `dist/openshell_manager-0.1.0.tar.gz`; wheel built from the sdist. The initial
  `uv build` attempt failed while revalidating cached hatchling against PyPI
  because sandbox DNS was unavailable; using the existing cache offline succeeded.
  Final archive verification used the successfully rebuilt artifacts.
- `uv run --locked python tools/verify_packaging.py`: exit 0; archive allowlists,
  12 reviewed hashes, installed hashes, actual PyYAML **6.0.3**, full-SHA local Git
  installation outside source, wrapper/security/dirty-source comparisons, native
  tripwires, mismatch and plain-wheel refusal.
- `git diff --check`: exit 0.

## Limits and remaining risks

The 16 inherited Claude/OpenCode live cases remain skipped by design. These results
are not native/live acceptance and do not verify a remote GitHub installation.
Implementation commit `eb13ea0260541c744d5d71a0d79af6efdc8a1606` is published on
`origin/feat/pr3a-uv-packaging`; the local finalization commit is not yet published.
Neither publication nor the local fixture test constitutes deployment or a real
GitHub installation test. Real GitHub installation and native/live verification
remain **SKIP**, outside the offline A02 acceptance evidence.

PEP 610 metadata is trusted local installer evidence, not a signature against a
compromised host. Plain wheels and sdists intentionally cannot run operational
commands without approved VCS provenance. Development mode refuses dirty tracked
files or conflicting installed metadata. `uv.lock` locks repository development,
not Git tool-install resolution; the only runtime dependency is constrained exactly
to PyYAML 6.0.3 and was inspected in the installed fixture environment.

The accepted provenance contract uses only the approved source's resolved full
`vcs_info.commit_id` for comparison with the project's reviewed pin.
`requested_revision` is informational: a branch/tag request resolving to the exact
approved commit can pass; a requested SHA cannot rescue an absent, non-SHA or
mismatching `commit_id`. No branch lookup or automatic update occurs. Installation
instructions still require full-SHA pins. This behavior was preserved and tested,
not refactored.

Existing project state binds the Manager revision. An upgrade requires a reviewed
project pin and separate state migration/cutover decision; no bindings or active
resources were rewritten. Resource compatibility links require normal Git symlink
support on the supported Linux development host. No PR3b work is included.
