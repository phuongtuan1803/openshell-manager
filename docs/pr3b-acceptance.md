# PR3b acceptance: notification/manual-review fallback

Branch: `feat/pr3b-telegram-approval-p1`, based on fetched main `ccc9548`.
Main contains PR3a merge #4 and architecture merge #3. Section 4 of
[the design](plans/pr3a-uv-packaging-and-pr3b-telegram-approval-design.md)
is the implementation contract. This release is the explicitly permitted
fallback, **not complete remote native approval acceptance**.

## Mandatory native gate / blocker

The pinned [OpenShell v0.1.2 CLI source](https://github.com/NVIDIA/OpenShell/blob/v0.1.2/crates/openshell-cli/src/main.rs)
shows `DraftCommands::Get` accepts sandbox name/status only (lines 1836–1847).
There is no structured-output or exact-chunk selector on that command. The
[0.1.2 advisor documentation](https://docs.nvidia.com/openshell/v0.1.2/how-it-works/policies/advisor)
documents manual get/approve/reject but does not establish a reliable host-side
structured exact-proposal/freshness/result contract. These sources were inspected;
no installed CLI, active Gateway, sandbox, Telegram or guest service was probed.

Native discovery/get/approve/reject all raise `NativeUnavailable` before any
subprocess or network call. No user flag enables native writes. No invented
structured schema, synthetic chunk identity or log-derived authorization exists.
No Approve rule/Reject buttons are offered while the gate is blocked; only an
expiring authenticated Details callback. Native successful approve/reject tests
cannot be represented honestly by inventing a mock contract. They remain SKIP.

## Criteria

PASS below means offline fallback behavior only where stated. It does not
establish native host acceptance or permission grant capability.

| ID | Status | Evidence and limitation |
| --- | --- | --- |
| B01 | FAIL | Native structured exact-proposal contract not positively identified. Pinned source evidence above; four native adapter refusal cases pass. Mandatory review-only mode enforced. |
| B02 | SKIP | Native get/list/approve/reject unavailable behind gate; no write command emitted. Command correctness/scope approval acceptance blocked by B01. |
| B03 | PASS | No settings/policy/pin changes, native commands or global auto approval added. Adapter unconditionally refuses writes; existing guards/resources retained. |
| B04 | PASS | Fallback only: repeated inbox scans send one bounded sanitized endpoint/risk notice, with default 900-second TTL. Free-form reasons/paths/native IDs omitted, unchanged inbox bytes asserted. Native proposal delivery unavailable. |
| B05 | PASS | Wrong numeric user/chat, string IDs, bool IDs, message mismatch and forged callbacks refused. Unauthorized callbacks do not consume valid tokens. No callback path invokes native mutation. |
| B06 | PASS | SQLite atomic concurrent claims/callbacks, hashed opaque tokens, expiry boundary, duplicate fingerprints, replayed callback/update and durable offset tests. Token bound to Details and Telegram message; it grants no permission. |
| B07 | SKIP | Project/bot journal binding and changed config fields are refused before polling; removed/changed inbox notices become stale. Native sandbox UUID, rule/provider/effective-policy fingerprint verification blocked by B01. |
| B08 | SKIP | Verified native successful approve/reject/refusal/result tests blocked by B01. No claim of native state confirmation. All native methods refuse. |
| B09 | PASS | Injected send/answer failure and recovery: no native action; uncertain delivery held for review without resending; new endpoints still delivered after outage; offset replay safe. Explicit missing-bot configuration fails before Telegram. |
| B10 | PASS | Restart reconciliation turns sending and legacy applying rows into needs_review, records sanitized audit, never retries native action or uncertain send. Native result reconciliation unavailable; conservative manual-review outcome used. |
| B11 | PASS | Inbox read-only; forged native chunk/method/path/reason ignored; invalid scope/binary/port/markup rejected. Compatibility path has no approval capability and no native CLI calls. |
| B12 | PASS | Private host JSON loader enforces ownership/0600/no links, numeric IDs and token format. Telegram errors suppress token-bearing exceptions; payload tests exclude token. Directory/DB 0700/0600 and symlink/hardlink refusal tested. Archive disclosure scan required below. No real credential created/read. |
| B13 | PASS | Host-wide flock excludes second poller sharing canonical state root; cooperative private stop marker, orderly signal restoration/DB closure and drift-before-poll tests. Reviewed optional systemd template, explicit enable/disable runbook. Package/setup never enables service. One canonical host state root is a P1 precondition. |
| B14 | PASS | Full inherited offline suite and resource hashes; legacy CLI handlers unchanged except approval dispatch. Packaging allowlist includes Python subpackage. No agent/native pins, policy assets, source scope or GitHub behavior changed. |
| B15 | SKIP | Separately authorized dummy-only native/Telegram host acceptance was not run. No native sandbox ID, runtime revision or live evidence exists. |

## Verification

Focused tests cover native refusal, queue privacy/binding, expiry, replay,
concurrency, restart, inbox sanitization/drift, Telegram failure/payload/redaction,
identity authorization, long-poll shutdown and CLI/service lifecycle. All native
and Telegram responses are mocks; inherited autouse guards prohibit live network
and non-local-Git subprocesses.

Actual final verification (all commands used `UV_CACHE_DIR=/tmp/pr3a-uv-cache`):

- `uv run --offline --locked python -m pytest -q tests/test_approval.py`:
  **39 passed**, final focused run 3.62 seconds.
- `uv run --offline --locked python -m pytest -q`:
  **326 passed, 16 skipped in 6.54 seconds**, exit 0. The 16 skips are inherited
  Claude/OpenCode live cases requiring separate Founder approval.
- `uv lock --offline --check`: exit 0; eight packages, no dependency changes.
- `uv build --offline`: exit 0; wheel and sdist built successfully.
- `uv run --offline --locked python tools/verify_packaging.py`: exit 0;
  **35 wheel files, 76 sdist files, 12 reviewed resource hashes**. Isolated
  Git-fixture uv installation works outside checkout with PyYAML 6.0.3.
  Installed approval help/status/pending succeed; absent bot/bridge state
  serve/stop refuse. No approval directory or native tripwire is created by
  read-only commands. Legacy wrappers, pin mismatch and plain-wheel refusal pass.
- `git diff --check`: exit 0. Final source/artifact disclosure scans found no
  real credentials, host logs, private project profiles or workstation paths.

Build/install harness uses temporary isolated local Git fixtures and native
tripwires; it never installs to the active host or calls real services.

## Remaining risks and deferred acceptance

B01 blocks reliable remote native decisions and B02/B07/B08 native acceptance.
The fallback monitors existing compatibility inbox entries, not live proposals.
No active inbox means no notices. Uncertain sends deliberately remain needs_review
and may require manual discovery; delivery is not exactly once under HTTP failure.
Details expiry cannot revoke native rules. Queue/audit retention is bounded and
requires reviewed archival at the documented limit. One canonical state root
coordinates pollers; separate host roots/other Telegram consumers are unsupported.

This implementation never persists runtime rules to project policy, changes
native approval mode, upgrades pinned tools, starts a Gateway/VM, modifies OMA,
creates real credentials, changes active host configuration or creates/merges a
PR. Installation and updates never enable the service. The Founder must separately
authorize future native interface work, dummy host acceptance and any deployment.
