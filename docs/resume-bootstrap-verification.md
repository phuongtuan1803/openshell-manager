# Resume/bootstrap patch verification

Date: 2026-10-15. Repository: `phuongtuan1803/openshell-manager`.
Branch: `fix/resume-bootstrap-missing-network-policies`.
Baseline and reviewed remote head: `caaab70851986dfea84c2ef61430879a53c30e92`.
Patch range reviewed: `c564578..caaab708` plus this commit's changes.
The final commit containing this report identifies the verified source revision.

## Scope and safety

Verification used an isolated checkout, Python 3.12.3, pytest 8.4.2 and the locked
runtime dependencies, with a writable copy of the existing dependency cache.
No live sandbox/Gateway/image/workspace/policy/credential operation, OAuth login,
model evaluation, Gateway database edit, process termination, OMA pin update,
PR creation or merge was performed. The original workspace branch was preserved.

Historical evaluation evidence was reviewed, not rerun. The existing operator
B15 report in `docs/pr3b-acceptance.md` describes revision `8f25e0d` and explicitly
limits its independently verified status. It does not prove the changed bootstrap,
Codex preflight or session payload. No unchanged paid/model evaluation was repeated
or promoted to current acceptance. The changed inputs require new offline tests.

## Confirmed defects and minimal fixes

1. Interrupted-bootstrap setup postponed rejecting absent ownership receipts until
   after native inspection. Restore an early requirement for a bound project and
   both binding/bootstrap receipts; retain the later exact binding, UUID, image,
   ownership and deny-all policy checks. The existing unbound-sandbox regression
   failed at baseline and now passes.
2. `session.unmanaged_agent()` missed the npm `node .../codex.js` launcher,
   including device login, and treated permission/read errors as an idle guest.
   Recognize the launcher and block on unreadable metadata; tolerate only processes
   that disappeared during inspection. Synthetic baseline-source reproduction
   confirmed both false negatives (exit 0). Regressions cover native login, Node
   login, daemon, Claude/OpenCode, unrelated Node/sleep processes, disappearing
   processes and unreadable metadata. Setup regression ensures an active guest
   blocks recovery/provider reconciliation and state publication. No process is killed.
3. The recovery regression expected two effective-policy checks although recovery,
   initial adoption and final verification correctly perform three. Correct the
   assertion; retain all runtime verification.
4. The reviewed packaging manifest still contained the pre-CA-fix session digest
   `23f4e728c321631f2ec4835574fb2734946d77f779d2c28c93c1135c3c6161ce`.
   Baseline source digest was already
   `39ce8b7f01ae94e95342ff3734467d3847a16213e3a3bb78fdce75e5d832dd3c`.
   Update only the session digest to the reviewed final payload
   `f15461775adcccc3a3eb85cc51150d2b5294d515584658d270f6ddec44fa2759`.

## Review coverage

- Codex requires exact `auth.openai.com:443` before device login and both that
  endpoint and `chatgpt.com:443` before startup. Every discovered pinned native
  and daemon executable needs REST/enforce/full access. Provider rules cannot
  satisfy this check. Added endpoint mutation regressions cover host, port,
  protocol, enforcement and insufficient access; existing regressions cover
  missing executable coverage and actionable Founder-review instructions.
- Checks fail before guest auth preparation/login/startup. No preflight grants
  permissions. Existing exact effective-policy checks retain default deny/manual
  approval and reviewed project configuration; reconciliation remains explicit.
- Git retains `GIT_SSL_CAINFO`, strips inherited Git tracing/config/TLS-bypass
  variables, uses the scoped stateless credential helper, suppresses failing
  transport output and preserves dirty/diverged work. Existing credential/TLS
  and local Git regressions are included in the focused run.
- Native missing `network_policies` is handled as deny-all during effective-policy
  and Provider reconciliation. Recovery still refuses altered ownership, receipts,
  identity or policy. Project configuration and revision migration regressions
  cover existing profiles, exact pins, locks and state preservation.

## Commands and results

All pytest commands below use `python -m pytest -q`; paths are repository-relative.
The initial focused run used the existing locked environment with `PYTHONPATH=src`.
Subsequent commands use `uv run --offline --locked` with a writable cache copy.

| Check | Exit | PASS / FAIL / SKIP |
| --- | --- | --- |
| Initial focused: `tests/test_dev_sandbox_phase2.py tests/test_dev_sandbox_session.py tests/test_dev_sandbox_github.py tests/test_project_configuration.py` | 1 | 180 / 2 / 0 |
| Focused after initial fixes and process regressions | 0 | 193 / 0 / 0 |
| Final focused, including setup blocking and endpoint mutations (same four files) | 0 | 199 / 0 / 0; 26.20 s |
| Packaging regression before manifest correction: `tests/test_packaging.py` | 1 | 1 / 1 / 0 |
| Packaging regression after manifest correction: `tests/test_packaging.py` | 0 | 2 / 0 / 0 |

Final required offline gates (executed once on the final runtime/test changes):

| Command | Exit | Result |
| --- | --- | --- |
| `uv run --offline --locked python -m pytest -q` | 0 | **554 PASS / 0 FAIL / 16 SKIP**, 149.80 s |
| `uv lock --offline --check` | 0 | PASS; 11 packages resolved, lock unchanged |
| `uv build --offline` | 0 | PASS; wheel and sdist |
| `uv run --offline --locked python tools/verify_packaging.py` | 0 | PASS; 39 wheel files, 91 sdist files, 12 reviewed hashes; isolated local Git install, dependencies, provenance failures and native-operation tripwires |
| `uv pip check --python .venv/bin/python` | 0 | PASS; 10 packages compatible |
| AST parsing of the four changed Python files | 0 | PASS |
| `git diff --check` and final diff/disclosure review | 0 | PASS; only six relevant source/test/evidence files; no secrets or machine-private data |

All required README offline gates passed. Evidence text was finalized after these
checks; runtime code, tests and resource hashes did not change afterward. Packaging
fixture commit IDs are disposable test data, not production revisions. The final
commit is a descendant of the supplied baseline; no history rewrite is required.

**Overall verdict: PARTIAL.** All identified offline defects are fixed and the full
offline suite passes; sensitive live behaviors below remain explicitly unverified.
There is no repository pre-commit configuration or installed pre-commit hook;
the README offline verification commands are the repository gates.

## Live limitations

**BLOCKED / unverified:** real device login, Codex network/startup behavior,
OpenShell VM permissions and active-process visibility, actual Provider receipts
and interrupted setup against the existing sandbox. These require sensitive
live operations and were intentionally excluded. Offline tests are not live PASS.
The 16 explicitly gated Claude/OpenCode live cases remain SKIP. No current B15
or paid-model PASS is claimed. Unreadable guest metadata now conservatively blocks
setup and may require operator investigation of guest permissions.
