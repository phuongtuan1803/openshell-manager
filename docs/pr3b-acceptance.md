# PR3b acceptance: native Telegram approval correction

Branch: `feat/pr3b-telegram-approval-p1`; correction follows `c981873`.
Contract: Section 4 and B01–B15 of
[the design](plans/pr3a-uv-packaging-and-pr3b-telegram-approval-design.md).
The earlier notification-only implementation was insufficient for the requested
workflow. This correction implements automatic native discovery and real native
approval/rejection through the version-specific authenticated Gateway API.

**Status: offline implementation verified; live end-to-end acceptance SKIP.**
No service was deployed or enabled. No real Telegram/native credential, Gateway,
active sandbox or paid agent/model call was accessed. Runtime missing/untrusted
native evidence reports **BLOCKED — end-to-end approval unavailable**, not success.

## Resolved source/API investigation

Public OpenShell v0.1.2 tag resolves to immutable commit
`6648bd0c290efbc41ba131ee9831ee45cd431f94` (read-only `git ls-remote` evidence).
Pinned source was downloaded to a disposable directory. The CLI's human-readable
output is not used as authority. Native Gateway `GetDraftPolicy` supplies exact
chunk IDs, complete rule scope, creation identity, risk fields, current/candidate
effective policies/hashes and a review token. Supported approval verifies that
token and fences persistence against changed effective policy. Native rejection
addresses the exact chunk and records a reason.

The bridge uses `ApproveAllDraftChunks` with exactly **one explicit pending
chunk/token pair**, never an empty list. This avoids the single-chunk approval
RPC's ability to reapprove rejected chunks. No blanket approval, generic policy
update, auto mode or permanent project-policy persistence exists here.

[Exact immutable source references, schema hashes, auth/decision behavior and
race limits](native-api-v0.1.2.md) are recorded separately. Real gRPC/protobuf
serialization is exercised by synthetic versioned fixtures. There is no invented
JSON endpoint, hand-parsed terminal output or log-derived native identity.

## B01–B15

PASS entries below are **offline source/fixture evidence**, not host/live proof.

| ID | Status | Actual evidence and limits |
| --- | --- | --- |
| B01 | PASS | Exact pinned protobuf, CLI, Gateway handlers, review-token/persistence code, mTLS resolution and agent wait lifecycle inspected. Generated descriptor/source digests and wire field/method tests. Runtime exact version plus complete evidence required. Installed/live probe remains SKIP. |
| B02 | PASS | Serialized request fixtures address exact workspace, sandbox and chunk. One explicit reviewed pending pair for approval; exact native reject with fixed reason. Empty/blanket list, raw-ID-only mutation and UpdateConfig unavailable. Candidate must add only the exact proposed rule and preserve existing authority. |
| B03 | PASS | Native manual default or explicit MANUAL required. AUTO/global policy/unadmitted configuration refused. Manager phase/image, Ready/admission/load and reviewed filesystem/Landlock/process guards enforced. No native settings/policy/pin changes or host installation steps added. |
| B04 | PASS | Bridge scans native RPCs with absent inbox; duplicate active proposal delivered once. Native ID/creation/sandbox/review generation dedup; same endpoint new chunk after rejection receives new notice. Changed review and expired pending requests receive fresh tokens/generations. Complete sanitized scope/risk, identity, expiry and buttons tested. |
| B05 | PASS | Unauthorized numeric/string/bool user/chat, wrong message, forged/repurposed token rejected before mutation. Authorized actor recorded. Compatibility callback can never acquire native authority. |
| B06 | PASS | SQLite concurrent atomic claim and callback tests; approve/reject sibling tokens consumed together; durable offset and replay protection. Expiry checked before claim AND immediately before native dispatch. Old expiry tokens cannot authorize a fresh generation. |
| B07 | PASS | Sandbox UUID, instance, project pin/config, existing Manager tool-binding fingerprint, creation identity, native token, full rule, policy/provider fingerprints and risk drift refused. Discovery races and native precondition/zero-grant refusal tested. Stale generation never grants; native rejection lacks server-side CAS, so external-admin races require needs_review rather than exact success. |
| B08 | PASS | Mocked native approved/rejected result checked against exact proposal and policy. Approval success requires candidate effective state, exact native revision/hash and LOADED/active version. Unconfirmed/pending load and rejection rule race never report success. Read-only later confirmation tested. Actual native host acceptance SKIP. |
| B09 | PASS | Send/answer/status/native outages injected. Unknown sends/results held; no implicit grant or blind resend. New native IDs notify after recovery. Failed Telegram edit retries only the status, never the decision. |
| B10 | PASS | Crash after atomic claim before RPC, lost response before/after mutation, delayed load and restart tests. Applying/needs_review reconciles only by reads; no mutation replay even with durable native request_id. Legacy fallback recovery retained. |
| B11 | PASS | Existing inbox remains read-only notification fallback with Details only. Forged chunk/method/path/rationale ignored; missing native interface cannot grant through compatibility. Native normal operation needs no manual rules-pending/log refresh. |
| B12 | PASS | Private 0700/0600 state and existing key files; owner/link checks, numeric identity validation, TLS verification and secret-bearing error suppression. Native rationale/credential values omitted, token-like scope redacted and approval disabled. Bot credentials never enter RPCs/guest arguments/public artifacts. Disclosure scan required below. |
| B13 | PASS | Existing host-wide poller lock and operation lock reused, cooperative bounded stop, signal/DB cleanup, explicit service template/lifecycle. Native Gateway selection bound; no installer/setup auto enable. gRPC native sockets explicitly blocked in offline test guards. |
| B14 | PASS | Full inherited offline suite and packaging/resource regression checks required below. PR3a provenance/source guards and native/model/tool pins unchanged. Added exact Python grpcio/protobuf dependencies for the host adapter; artifact/installed dependency checks updated. |
| B15 | SKIP | No separately authorized isolated native sandbox/Telegram host acceptance ran. No live sandbox UUID/revision, actual agent retry trace or production deployment evidence exists. No paid model calls. |

## Verification commands and evidence

Focused suites cover the native API, automatic discovery/repeated identity,
exact decisions/result confirmation, drift, authorization/replay/concurrency,
outages/recovery, transport/privacy, SQLite migration and existing fallback.
All fixtures are explicitly synthetic; no captured private native/Telegram data.
Offline guards cover both Python sockets/urllib and gRPC's native sockets.

Final offline verification (2026-10-15), using
`UV_CACHE_DIR=/tmp/pr3b-uv-cache` with cached public dependencies:

| Command / check | Result |
| --- | --- |
| `uv run --offline --locked python -m pytest -q tests/test_approval_native.py` | 73 passed in 25.15 seconds after final code changes. |
| `uv run --offline --locked python -m pytest -q` | **399 passed, 16 skipped** in 69.20 seconds. Skips are the existing explicitly gated Claude/OpenCode live evaluations. |
| `uv lock --offline --check` | PASS; 11 resolved packages. |
| `uv build --offline` | PASS; wheel and sdist built. |
| `uv run --offline --locked python tools/verify_packaging.py` | PASS; 36 wheel files, 83 sdist files, 12 reviewed asset hashes; isolated local Git fixture installation, installed dependency assertions, legacy wrappers, provenance failures and native-operation tripwires. |
| Final diff / disclosure review | PASS; whitespace, private-key/token/workstation-path patterns and runbook local links checked across all 23 changed/new public files; only synthetic fixtures included. |

The fixture install commit is a disposable local packaging fixture, not the
Manager release revision or live-host evidence. The full suite includes the
existing fallback, packaging and Manager security regressions. Neither native nor
Telegram sockets are permitted in the offline tests.

Use [the first real-run procedure](runbooks/telegram-approval-live.md) for explicit
isolated acceptance, exact native proposal submission, Telegram decisions,
agent-side wait/retry/rejection, sanitized evidence and the separate service
enablement decision. Writing that procedure does not authorize or execute it. A disposable protobuf compiler environment was used only
for schema generation; grpcio-tools is not a runtime dependency or native upgrade.
Runtime direct dependencies are PyYAML 6.0.3, grpcio 1.76.0 and protobuf 6.33.6.
No real host installation is performed by the local packaging fixture harness.

## Remaining limits / operator stop conditions

- Live evidence is SKIP. An explicitly authorized isolated dummy sandbox must
  prove native proposal discovery, exact enforcement, load and normal agent
  wait/retry/rejection before deployment acceptance; no active VM may be modified.
- Existing local HTTPS mTLS only. Missing credentials, required broader role,
  unsupported auth/host/version or missing structured evidence means BLOCKED;
  request an operator decision rather than create credentials/change Gateway.
- P1 approval is narrower than all native proposals: clean explicit HTTPS REST
  rules for curl/Manager-discovered pinned Codex paths, with existing authority
  unchanged. Credentialed destination hosts are refused even with clean prover
  output; native executable selectors are path-only, not fresh binary attestation.
  GitHub/credential grants and unsupported/risky scope cannot be approved.
- RejectDraftChunk has no review-token/CAS parameter. Exact pre-dispatch and
  post-result checks preserve default deny and refuse false success; an
  independent native administrator can race and native rejection may deny/remove
  access. If atomic conditional rejection is required, stop deployment and seek
  a separate decision. Manager's operation lock does not lock external admins.
- Uncertain send/mutation/load requires read-only confirmation or manual review,
  never automatic retry. A held uncertain identity cannot acquire a replacement
  approval generation. Independent native changes can prevent later exact proof.
- Legacy core policy-drift checks remain unchanged. Native runtime grants can
  require separately reviewed reconciliation for future core CLI operations;
  state.json/project policy is not silently rewritten to bypass those guards.
- One canonical state root/project/bot, bounded queue and reviewed retention;
  other roots/consumers and archival automation are outside P1. Disabling the
  service never revokes an already-approved runtime native rule automatically.

No OMA/PR4/PR5 work, broader generic authorizer, native version upgrade, service
activation, active host mutation, real credential creation or PR/merge occurred.

## One-command live-test addition

`openshell-manager approval test` now orchestrates the existing bridge and adapter.
It proves the running revision before state/native actions, validates the pinned
CLI exec contract and existing local Gateway/mTLS/workspace, verifies or prepares a
reviewed local image receipt/digest/recipe, generates strict isolated profiles and
a standalone local Git fixture, and creates a fresh owned `tg-<hex>-dev` sandbox.
No existing sandbox is adopted. Every exec, sandbox-local proposal setting and
delete rechecks native ownership/UUID. The four named OMA sandboxes are excluded
by construction and tested as forbidden lifecycle targets.

The only added Telegram methods are read-only `getMe` and `getWebhookInfo` for
first-run validation. Enrollment uses hidden token entry and a random `/start`
challenge, private numeric identity confirmation and a private host JSON. Existing
private configuration is reused. No credential reaches the guest, project files,
reports or command arguments. A host-wide onboarding check and the existing bridge
lock prevent normal Manager poller overlap; unrelated pollers remain an operator
prerequisite.

The managed child runs the existing `approval serve`. The parent never approves or
rejects a native request. It submits fixed genuine native protocol requests through
the supported versioned noninteractive CLI exec, waits for Telegram decisions,
revalidates native result/policy through the existing adapter and checks guest
wait/load plus actual HTTP 200 retry or explicit `policy_denied` / HTTP 403.
The test can enable the registered proposal surface only on its new owned sandbox;
it never enables AUTO, adds generic policy grants or changes Gateway settings.

Timeouts, interruption, uncertain creation/mutation and ownership drift keep a
private durable record and cannot produce a fake PASS. Cleanup stops only its child
and deletes only its owned sandbox, requiring confirmed absence in the native
inventory. An uncertain runtime grant is reported. Reviewed images, existing VMs,
services and credentials are not removed; private audit/config fixtures are retained.
Native dedup and live callback replay checks can be explicit SKIP; no identifiers
or callback data are invented to manufacture evidence.

The [one-command runbook](runbooks/telegram-approval-test.md) replaces manual setup
as the normal test procedure and documents the genuine first-run prerequisites.
B01–B14 retain their qualified offline status; the new orchestration has offline
coverage with mocked external services, **not live success evidence**. B15 remains
**SKIP**: this implementation session did not execute the real command against
Telegram, a Gateway, or any VM. No paid model calls occurred.

The native CLI also writes its local `last_sandbox` selection on exec. Lifecycle
commands therefore use a private per-run CLI registry, bound to the original
Gateway name/endpoint/CA with private copies of existing mTLS materials. The
original registry is never rewritten. Copies are removed even after a failed or
interrupted test; hard-crash remnants require private operator review. No keys
are generated or copied into guest/project data. Offline tests check private
modes, credential cleanup and preservation of the original sandbox selection.

Final verification for this addition (2026-10-15), with
`UV_CACHE_DIR=/tmp/pr3b-uv-cache`:

| Check | Actual result |
| --- | --- |
| `uv run --offline --locked python -m pytest -q tests/test_approval_live.py` | **59 passed** in 1.35 seconds. First-run/reuse/identity, exact proposal orchestration, actual existing engine with typed mocked RPCs, deadline/expiry/Ctrl-C, fast decisions, ownership isolation and cleanup, missing prerequisites, secret suppression and private CLI registry preservation. |
| `uv run --offline --locked python -m pytest -q` | **458 passed, 16 skipped** in 43.47 seconds after final code changes. The 16 skips remain the explicitly gated live agent evaluations. |
| `uv lock --offline --check` | PASS; 11 resolved packages, no dependency or native-tool upgrade. |
| `uv build --offline` | PASS; wheel and sdist. |
| `uv run --offline --locked python tools/verify_packaging.py` | PASS; 38 wheel files, 87 sdist files, 12 reviewed asset hashes; isolated installed Git fixture with original source hidden, exact runtime dependencies, one-command help and isolated Python child import, legacy wrappers and native tripwires. |
| Diff / disclosure / documentation | PASS; whitespace, Python syntax, local runbook links/fences and private key/token/workstation-path scan across all 11 changed/new public files. Only synthetic test data. |
| Real `approval test` / B15 | **SKIP — not executed**. No real credentials or Telegram/Gateway/VM operations; no paid calls. |

Prior PR3b correction results above remain historical. A fixture Git SHA from the
packaging harness is not a release SHA or live evidence. The native CLI registry
side effect is verified in the immutable
[exec handler](https://github.com/NVIDIA/OpenShell/blob/6648bd0c290efbc41ba131ee9831ee45cd431f94/crates/openshell-cli/src/main.rs#L3594);
[test source references](runbooks/telegram-approval-test.md) cover the other native
execution/lifecycle contracts.

## Image-preflight correction (offline evidence)

The one-command test now revalidates existing receipt/digest/recipe-label, pinned
Codex and full package inventory before reuse. If none qualifies, it builds an
untagged dedicated image from only the packaged Dockerfile in an otherwise empty
temporary context. Docker's actual `--iidfile` digest is inspected and the pinned
Codex and required package inventory are checked in network-disabled, read-only
containers before saving the private `approval-live/built-image-review.json`.
No existing OMA image tag, receipt or Gateway configuration is changed. Failed
builds/verifications cannot produce a new receipt; shared Docker storage is never
pruned. This replaces the previous separately established image prerequisite.

- Focused offline orchestration suite: **71 passed** (`tests/test_approval_live.py`).
  Includes missing receipt/first build, verified reuse, incompatible existing
  recipe/digest/label/version/inventory, missing image and build/timeout/validation
  failures. Tests mock Docker and check the Dockerfile-only context and absence
  of tag writes or image removal.
- Complete offline suite: **470 passed, 16 skipped** in 42.84 seconds.
  Skips remain the explicitly gated live agent evaluations.
- `uv build --offline`: wheel and sdist PASS. `tools/verify_packaging.py`: PASS
  (38 wheel files, 87 sdist files, 12 reviewed hashes; isolated local Git install).
- Live Docker build and native/Telegram acceptance: **SKIP**. No host Docker,
  Gateway, sandbox, image or credential operations were executed. **B15 remains
  SKIP**; mocked image preparation is not live approval evidence.

## First native-context live failure correction

Founder-reported live evidence for `c12ef874e7da2f4fb0d99db6dc9e0192d2f3ec2a`:
revision, Gateway/mTLS, dedicated image and Telegram connection passed; isolated
creation succeeded; native context timed out; owned cleanup passed; no permission
was granted. This is **not B15 PASS**. The old exception suppression retained no
failed invariant, so the exact original host failure remains unproven.

Offline source analysis reproduced an incompatibility: the bootstrap omitted
`process`, while the adapter required explicit sandbox user/group. Pinned v0.1.2
`openshell-policy/src/lib.rs` parser (`raw.process.map`, line 621) and
canonicalization (line 1872) preserve omission; server create validates/persists
that policy (`grpc/sandbox.rs`, line 531). The test now includes the exact required
sandbox identity before creation; no existing live process policy is changed and
no guard is relaxed. The runbook links immutable source references.

`NativeAdapter.context()` now reports named static and convergence invariants.
The one-command test persists their safe status codes in private `result.json`
before cleanup, fails immediately on permanent mismatch/rejected admission, and
names the last blocking invariant on bounded convergence timeout. Fixed gRPC
status codes distinguish transient transport errors from permission/authentication
failures. Native error prose, full policies and provider contents are excluded.
Creation, Ready and admission progress are visible. Owned cleanup and uncertain
mutation rules are unchanged; these diagnostic retries are reads only.

Focused offline verification: **158 passed** (`tests/test_approval_live.py` and
`tests/test_approval_native.py`, 108.91 seconds). Includes the omitted-process
regression, exact guard/provider/mode/config/load/admission diagnostics, durable
pre-cleanup reports, retry convergence, timeout and secret suppression.

No new real test, Docker/Gateway operation or paid model call was performed.
A new isolated live run still requires explicit authorization; **B15 remains SKIP**.

Complete offline regression verification after final code changes: **484 passed,
16 skipped** in 111.15 seconds (`uv run --offline --locked python -m pytest -q`).
The 16 skips remain the gated live agent evaluations. Syntax, final diff whitespace
and credential-disclosure checks passed.
