# PR #5 — Phase 1 offline verification

Date: 2026-10-15. Role: Offline Tester. Handoff: ChatGPT independent review.

Tested commit: `66d4458b8c68a33701001264a11cfdec26de01f7` on
`feat/pr3b-telegram-approval-p1`.
Fetched main/base: `ccc95485a237c3272a62193fd93805dcc44acba4` (also merge base).
GitHub metadata confirmed PR #5 OPEN and Draft with that head before testing
and again during verification. `statusCheckRollup` was empty.

Only GitHub metadata/fetch used remote access. No B15, real approval command,
live agent evaluation, Gateway/VM/sandbox operation, Telegram request or real
credential inspection was performed. Tests named `test_approval_live.py` are
offline orchestration doubles, not live acceptance. Successful checks were not
repeated. This report does not authorize resuming the operator's live-test hold.

## Gates and reproduction

Run from the checkout at the tested SHA. Cached dependencies are required;
the cache used here was `/tmp/pr3b-uv-cache`. Python 3.12.3; uv 0.12.23;
pytest 8.4.2. Runtime versions: PyYAML 6.0.3, grpcio 1.76.0,
protobuf 6.33.6, typing-extensions 4.16.0.

```sh
gh pr view 5 --repo phuongtuan1803/openshell-manager --json headRefOid,headRefName,baseRefName,isDraft,state
git fetch origin main feat/pr3b-telegram-approval-p1
git rev-parse HEAD origin/main
git diff --stat origin/main...HEAD
git diff --check origin/main...HEAD
export UV_CACHE_DIR=/tmp/pr3b-uv-cache
uv run --offline --locked python -m pytest -q
uv lock --offline --check
uv build --offline
uv run --offline --locked python tools/verify_packaging.py
uv pip check --python .venv/bin/python
gh pr view 5 --repo phuongtuan1803/openshell-manager --json headRefOid,isDraft,statusCheckRollup
```

| Gate | Result | Evidence |
| --- | --- | --- |
| Latest head / diff against main | PASS | Exact remote/local SHA above; 33 files, 6,537 insertions, 14 deletions; whitespace check exit 0. Policy/config/image resources unchanged. |
| Complete offline suite | PASS | Exit 0; **512 passed, 16 skipped in 111.94 seconds**. All skips are the explicitly gated Claude/OpenCode live evaluations (`tests/test_dev_sandbox_agents.py:344`). |
| Dependency lock / compatibility | PASS | Lock check exit 0, 11 resolved packages; pip check exit 0, 10 installed packages compatible. No lock changes. |
| Wheel and sdist build | PASS | Both `openshell_manager-0.1.0` archives built offline; exit 0. |
| Packaging / installed CLI | PASS | Exit 0; 39 wheel files, 89 sdist files, 12 reviewed hashes; isolated local Git fixture install with original fixture source hidden, dependencies, wrappers, provenance rejection and native tripwires passed. |
| Telegram authorization | PASS (offline) | Source review and suite cases described below. |
| Callback replay / concurrency | PASS (offline) | Source review and suite cases described below. |
| Exact native approval | PASS (offline) | Source review and typed protobuf fixtures; no real native contract claim. |
| Uncertain-result recovery | PASS (offline) | Source review and crash/load/status fault cases; no repeated mutation. |
| Secret redaction | PASS (offline) | Source review, synthetic secret assertions and package disclosure boundary checks. |
| Sandbox isolation / cleanup | PASS (offline) | Source review and ownership/cleanup doubles; no real isolation/deletion proof. |
| B15 / live acceptance | BLOCKED | Explicitly excluded; existing operator hold remains. Historical interrupted/partial runs are not PASS. |
| Hosted CI | BLOCKED | GitHub reports no status checks for the tested head; no hosted CI evidence available. |
| Independent review / merge readiness | BLOCKED | Phase 1 handoff only; ChatGPT independent review and outstanding acceptance still required. |

The packaging fixture commit `25cf170b9ff46d993b53fd2fc771ac64a897ba67`
is disposable local test data, not the PR SHA. Archive counts describe the tested
head before adding this documentation; the report adds one public sdist document.

## Security review evidence

No confirmed new security defect was identified within this offline review.
References below refer to the tested commit; all paths are relative to the repo.

- **Authorization:** `src/openshell_manager/approval/service.py:242–275`
  requires exact integer user and chat IDs before callback resolution/native
  reads; unauthorized callbacks do not consume valid authority.
  Evidence: `tests/test_approval_native.py:199–206` and
  `tests/test_approval.py:170–178` (wrong IDs/types).
- **Replay:** `approval/store.py:113–129,180–205` (under the same source package)
  stores hashes of random per-action tokens, binds message/request/generation,
  checks TTL and commits the decision claim with `BEGIN IMMEDIATE` before RPC.
  `approval/service.py:270–275` advances handled updates before acknowledgement.
  Evidence: `tests/test_approval_native.py:209,300,353,464` covers expiry,
  concurrent callbacks, failed acknowledgement and message/action binding.
- **Exact approval:** `approval/native.py:163–255,272–368,377–411` binds
  Gateway/workspace/sandbox UUID, reviewed static policy, provider/config
  revisions, proposal/risk/review identity and exact candidate bytes. Dispatch
  carries one explicit chunk/token, security flags disabled and a request UUID;
  no blanket approval. `approval/native.py:445–504` checks native decision,
  candidate hash, exact active rule, receipt and LOADED active revision.
  Evidence: `tests/test_approval_native.py:227–297,574–612` and
  `tests/test_approval_diagnostics.py:127–211` cover drift, unsafe scope,
  credential destinations and bounded convergence.
- **Uncertainty:** `approval/service.py:171–231` persists applying/receipt state,
  moves unknown outcomes to needs_review, and reconciles using confirmation
  reads only, with bounded reads and permanent-drift stop. No mutation replay.
  Evidence: `tests/test_approval_native.py:317–349,407–417`.
- **Secrets:** `approval/telegram.py:36–65` suppresses token-bearing URL/error
  prose and disallows redirects. `approval/native.py:280–314` omits native
  rationale and emits bounded risk categories; `approval/diagnostics.py:118–164`
  admits only typed fields, excluding payloads/arguments/environment.
  `approval/store.py:15–32` requires private owned paths and rejects symlinks
  and hardlinks. Evidence: `tests/test_approval.py:107,241,280`,
  `tests/test_approval_native.py:379,488`, and
  `tests/test_approval_diagnostics.py:25,227,289`.
- **Isolation/cleanup:** `approval/live_test.py:264–291,306–320,368–445,635–669`
  isolates the CLI registry, uses a nonce-labelled dedicated test name and UUID,
  refuses ownership drift before exec/delete, and confirms absence before
  claiming cleanup. `approval/live_test.py:785–790` removes temporary copied
  TLS material. Evidence: `tests/test_approval_live.py:237–297,555`.
  Offline guards in `tests/conftest.py:12–55` replace HOME/config, block Python
  and gRPC network entry points, and permit only local Git fixture subprocesses.

**Known native limitation:** rejection lacks a server-side review-token
precondition (`approval/native.py:407–411`; `docs/native-api-v0.1.2.md:72–82`).
The Manager lock cannot coordinate an independent native administrator. A race
can reject changed authority; post-checks refuse exact-success claims but cannot
make the mutation conditional. Evidence: `tests/test_approval_native.py:371–375`.
Independent review must assess whether this documented P1 limitation is acceptable;
atomic conditional rejection would require a separate native/operator decision.

## Remaining blockers / handoff

1. ChatGPT independent review of this evidence and the final security/diff review.
2. Operator-authorized clean B15 PASS with actual approval/load/retry, separate
   rejection/deny and verified owned cleanup. Keep the existing live hold.
3. Hosted CI evidence for the eventual final branch head, where required/available.

Stop after Phase 1. No merge, Ready transition or deployment is authorized by
this report. Only this report is added to the existing PR branch; runtime code,
tests and dependencies remain at the tested commit's contents.
