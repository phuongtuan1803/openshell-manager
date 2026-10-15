# One-command Telegram approval live test

From a normal host terminal, run:

```bash
openshell-manager approval test
```

No project files, environment exports, second terminal, manual sandbox setup or
proposal IDs are needed. The command creates a fresh isolated fixture and test
sandbox, starts the existing approval bridge as a child, submits real native
proposals and verifies the decisions. It does not call an AI model or enable a
system service. Offline coverage and the supplied partial live observations are
described below; neither establishes a complete live acceptance pass.

On first use, create a dedicated bot through BotFather if necessary. The command
asks for the token with echo disabled, then asks you to send the displayed
`/start <one-use-challenge>` to that bot. Confirm the displayed numeric user/chat
IDs. Only a matching private-chat challenge is accepted. The bot JSON is saved
outside Git and guest data at
`~/.local/state/openshell-manager/approval-live/telegram.json`, mode 0600.
On subsequent runs the saved configuration is reused without prompts.

The command also discovers existing private bot JSON at
`~/.config/openshell-manager/telegram.json` or
`~/.local/state/openshell-manager/telegram.json`. An existing configuration at a
nonstandard path can be selected once per invocation with `--bot-config FILE`;
it is never copied into project data. Never put a token in command arguments.
Use a dedicated bot, separate from OMA M11 Gate, with no webhook or other poller.

When prompted in the terminal:

1. In Telegram review the exact isolated sandbox, `/usr/bin/curl`, `example.com:443`,
   `GET /`, findings and expiry; click **Approve rule**.
2. Wait for native approval, loaded policy and an actual successful HTTP 200 retry.
3. Review the separate exact `GET /pr3b-<random-run-id>` proposal; click **Reject**.
4. Wait for native rejection with a reason and an explicit HTTP 403
   `policy_denied` response. The first rule does not cover the second path.

A third submission checks repeated rejected scope if OpenShell creates a new native
identity. It may generate another notification, but requires no third decision:
owned sandbox cleanup removes the pending test resource. Native dedup/refusal is
reported as SKIP, never replaced with a made-up identity. Live replay injection is
SKIP; the command does not forge callbacks or recover opaque tokens. Existing
offline replay/concurrency tests remain the evidence for that check.

```mermaid
flowchart TD
    C[approval test] --> P[Verify provenance, tools, existing local mTLS and reviewed image]
    P --> B[Reuse private bot or enroll with hidden token and confirmed identity]
    B --> S[Create owned disposable deny-all sandbox in existing workspace]
    S --> N[Start existing bridge and verify native health]
    N --> A[Submit native GET proposal: Founder approves in Telegram]
    A --> V[Confirm native state, guest reload and HTTP 200 retry]
    V --> R[Submit separate path: Founder rejects in Telegram]
    R --> D[Confirm native rejection and explicit policy denial]
    D --> X[Stop bridge, delete owned sandbox, confirm native inventory absence]
    X --> E[Print PASS / FAIL / BLOCKED and private evidence path]
```

## Existing prerequisites

The managed bridge child uses the same package interpreter in isolated Python
mode (`-I`) and a private working directory; cwd/PYTHONPATH cannot supply its code.
Source development therefore needs the normal `uv` editable package environment,
rather than relying only on a `PYTHONPATH` export.

The command checks and reuses these prerequisites; it never installs, repairs or
upgrades them:

- Linux x86_64, existing KVM access, Docker, Git, Python and pinned OpenShell,
  Gateway and VM driver **0.1.2**. The local reviewed Gateway config must already
  use schema v2, VM compute and `127.0.0.1:17670`.
- A verified installed Manager Git revision, or a clean approved development
  checkout. Profiles created by the test bind to that exact verified revision.
  Uncommitted tracked development changes are refused by the existing provenance
  guard; do not bypass it.
- An existing healthy local HTTPS mTLS Gateway. The verified `openshell` registry
  is preferred; otherwise only a unique registry is selected. The mTLS directory
  requires ownership by the host user, 0700, with 0600 certificates/key and no
  links. Unsupported, invalid or missing credentials are BLOCKED; none are created.
- An existing workspace: `default` is preferred, or a sole discovered workspace.
  Ambiguity requires explicit `--gateway NAME` / `--gateway-workspace NAME` options.
  No workspace or active Gateway configuration is changed.
- **Docker image preparation is automatic.** The command first searches private
  `built-image-review.json` receipts in the Manager state root and its immediate
  project directories, preferring `approval-live/built-image-review.json`. It
  rechecks the actual immutable image digest, packaged Dockerfile hash/label,
  pinned Codex version and matching package inventory before reuse. An OMA image
  with Codex alone, but no recipe label/receipt, is insufficient.
  If no fully verified image is available, it builds a dedicated **untagged** test
  image from the packaged, reviewed Manager Dockerfile. The temporary build context
  contains only that Dockerfile; the OMA checkout and host credentials are never
  sent to Docker. First build requires registry/APT/npm connectivity, Docker disk
  space and up to 30 minutes; progress is shown as a bounded WAIT, with raw build
  output withheld. The base and Codex remain pinned; APT/npm dependencies are not
  snapshot locked, so the actual digest and `dpkg-query -W` inventory are recorded.
  Digest/recipe-label inspection and network-disabled, read-only Codex/inventory
  checks must all pass before a genuine mode-0600 receipt is saved at
  `~/.local/state/openshell-manager/approval-live/built-image-review.json`.
  Subsequent runs reverify and reuse it. No existing OMA image is relabeled,
  overwritten, rebuilt or deleted. The dedicated image and private receipt are
  retained after sandbox cleanup for reuse.
- The public HTTPS endpoint `https://example.com/` must return HTTP 200 through
  the approved exact rule. DNS/TLS/outage/upstream errors are failures, not proof
  of permission. No redirects, credentials, query strings or request bodies are
  used for the external probe.

The command may opt in to `agent_policy_proposals_enabled=true` **on its new owned
test sandbox only**, using the supported sandbox setting. MANUAL remains mandatory;
a conflicting effective AUTO/global policy is refused. The bootstrap policy reuses
Manager's deny-all network and existing filesystem/Landlock boundary. No GitHub
provider, remote clone, agent login, model launch or generic policy update runs.

## Isolation, timing and results

Every invocation uses a new `tg-<10 hex characters>-dev` name (17 characters), a
random ownership label and a recorded native UUID. It never adopts existing
sandboxes. Before exec, settings or deletion it verifies its exact native name,
workspace, UUID and ownership/project/image labels. The four existing OMA
sandboxes are never lifecycle targets. The native CLI normally updates its registry `last_sandbox` selection even for
exec. This command gives lifecycle CLI processes a private per-run registry with
mode 0700 directories and 0600 copies of the **existing** mTLS materials. Gateway
name/endpoint/CA identity is checked against the verified adapter before copying.
Those temporary host-only copies are removed on normal exit/Ctrl-C, including
failed tests; no new keys are generated and the real registry/last selection is
untouched. The bridge and parent adapter continue to use the original registry.
The verified image is referenced by its actual local digest. Only the dedicated
untagged fallback image may be built; existing images/tags are never changed or removed.

Timeouts: host checks generally 60 seconds; first image build 1800 seconds; creation 600 seconds, subsequent Ready
180 seconds, admission 120 seconds, bridge health/notification 90 seconds,
Founder decision 14 minutes within the bridge's 15-minute TTL, guest reload
30 seconds, and deletion confirmation 120 seconds. Guest exec has native 35-second
and host 45-second limits; HTTP probes have 20-second limits. First-run Telegram
challenge enrollment allows 5 minutes. Human token/identity input waits for the
operator; Ctrl-C is supported at any point. A running native/HTTP call can finish
up to its bounded deadline after cancellation.

Ctrl-C stops the managed bridge, then attempts deletion of only this run's owned
sandbox. The child gets SIGTERM and up to 45 seconds to exit; a stuck child may
be killed without repeating any native decision. No locks are unlinked. Cleanup
requires confirmed absence from the native sandbox inventory before reporting
success. Ownership drift, transport failure or interruption produces an explicit
unconfirmed-cleanup failure, never a successful cleanup claim.

If approval may already have happened, the command says so. Stopping the bridge
alone does not revoke a runtime rule; deletion of the owned test sandbox must be
confirmed. Existing native-admin rejection race limits still apply; see the
[operator runbook](telegram-approval-p1.md). No uncertain mutation is retried.

`RESULT: PASS` requires both confirmed decisions, policy reload, HTTP 200 retry,
explicit continued denial and confirmed cleanup. `FAIL` means an attempted test
or cleanup did not meet its checks. `BLOCKED` identifies a missing/untrusted
prerequisite. Exit codes are 0 / 1 / 2, or 130 for Ctrl-C when cleanup completes.
An interruption with failed cleanup returns 1.

If image preparation reports BLOCKED, check Docker daemon access, free disk space
and base-registry/APT/npm connectivity, then rerun the same command. Failed builds
or verification never create a new review receipt or fall back to the unverified
OMA tag. A failed build may leave Docker cache or an unreviewed untagged image;
the command does not delete images or prune shared Docker storage.

Private fixture/config, SQLite audit and a sanitized `result.json` are retained
under the normal Manager state directory for diagnosis; the final output gives
its location. The report includes native IDs/hashes/checks and ownership intent,
not bot tokens, keys, raw guest responses or native logs. It records whether a
runtime grant was possible and whether native resource absence was confirmed.
Do not publish raw SQLite or private host files. A hard host/process crash can
leave an owned test sandbox and private temporary CLI credential copies; its durable marker/UUID report must be reviewed
before any separately authorized cleanup. Future runs use new names and never
silently adopt or remove leftovers.

Installing/updating this package, reading help or preparing these docs does not
execute the test. Run it only when authorized to create its isolated test resources
and contact Telegram/OpenShell. No live operation was performed during implementation.
For manual diagnostics, the earlier [detailed procedure](telegram-approval-live.md)
remains available; it is not necessary for the normal one-command workflow.

The execution contract is verified against the immutable OpenShell 0.1.2 source:
[CLI global Gateway/workspace flags](https://github.com/NVIDIA/OpenShell/blob/6648bd0c290efbc41ba131ee9831ee45cd431f94/crates/openshell-cli/src/main.rs#L435),
[noninteractive exec flags](https://github.com/NVIDIA/OpenShell/blob/6648bd0c290efbc41ba131ee9831ee45cd431f94/crates/openshell-cli/src/main.rs#L1673),
[authenticated exec implementation and timeout](https://github.com/NVIDIA/OpenShell/blob/6648bd0c290efbc41ba131ee9831ee45cd431f94/crates/openshell-cli/src/run.rs#L1812),
[local Docker image resolution](https://github.com/NVIDIA/OpenShell/blob/6648bd0c290efbc41ba131ee9831ee45cd431f94/crates/openshell-driver-vm/src/driver.rs#L3203),
and [sandbox-local proposal opt-in](https://github.com/NVIDIA/OpenShell/blob/6648bd0c290efbc41ba131ee9831ee45cd431f94/crates/openshell-core/src/settings.rs#L134).
Runtime CLI help must also advertise the required execution flags before any VM
is created. Native authorization and decisions continue through the existing
[verified proposal adapter](../native-api-v0.1.2.md).

## Native verification diagnostics

Creation now prints the owned sandbox name and UUID verification result, followed
by native admission and guard checkpoints. `result.json` stores only fixed codes
and pass/wait/fail/timeout states under `checks.native_verification`, updated before
cleanup. No full policy, server admission prose, provider data or token is recorded.
The last failed/waiting code identifies the blocker; earlier PASS checks are
historical observations, not a promise that later state remained unchanged.

| Checkpoint | Meaning and action |
| --- | --- |
| `sandbox_identity`, `config_binding`, `guest_tools` | UUID/project/image/scope binding or pinned guest tools differ. Stop; inspect the owned test binding and installed revision. Never adopt another sandbox. |
| `sandbox_ready`, `initial_native_admission`, `native_admission` | Starting/pending states may converge. Rejected admission fails immediately; inspect native compatibility locally without publishing raw errors. |
| `config_schema`, `config_admitted`, `policy_source`, `config_revision` | Unsupported structured data, invalid effective configuration, global policy or missing revision. Stop and check pinned API/config compatibility; do not replace global policy. |
| `provider_revision_attachment` | Missing provider-state revision/attachment epoch, even for zero providers. Stop; check pinned native API compatibility without adding credentials/providers. |
| `admission_instance`, `admission_policy_revision`, `admission_config_revision`, `admission_provider_revision` | Supervisor observation differs from current desired configuration. Bounded 120-second convergence; a timeout reports the specific mismatch. Investigate supervisor configuration loading, not a broader permission grant. |
| `filesystem_guard`, `landlock_guard`, `process_guard`, `middleware_guard` | Effective static authority differs from the reviewed bootstrap. Fail immediately. Never reconcile by weakening the guard. |
| `loaded_schema`, `loaded_schema_hash`, `loaded_revision_version`, `loaded_state`, `active_policy_version` | Schema/hash shape, revision identity, LOADED state and active revision are reported separately. Pending loads may converge; failed/superseded loads fail immediately. |
| `manual_approval_mode`, `deny_all_network` | AUTO/unsupported mode or unexpected bootstrap network rules. Stop; no test proposal may be submitted. |
| `rpc_method_status`, `rpc_method_precondition`, `native_unclassified` | Fixed lowercase native read/transport codes. UNAVAILABLE/deadline/resource exhaustion/aborted may converge; authentication/permission/precondition failures stop. An unclassified error fails closed without raw exception disclosure. |

The bootstrap explicitly sets `process.run_as_user` and `process.run_as_group`
to `sandbox` **before creation**. OpenShell v0.1.2's policy parser preserves an
omitted process block, and canonicalization does not insert these fields. The
previous bootstrap omitted it while our strict adapter expected explicit fields,
which reproduces a permanent `process_guard` failure offline. Existing images,
Gateway settings and live sandbox policies are not changed by this correction.
The original live report did not retain invariant evidence, so this reproducible
incompatibility is not proof that it was the only failure on that host.

Pinned source evidence:
[policy parser maps the optional process block](https://github.com/NVIDIA/OpenShell/blob/6648bd0c290efbc41ba131ee9831ee45cd431f94/crates/openshell-policy/src/lib.rs#L621),
[canonicalization preserves process omission](https://github.com/NVIDIA/OpenShell/blob/6648bd0c290efbc41ba131ee9831ee45cd431f94/crates/openshell-policy/src/lib.rs#L1872),
[create validates and persists the supplied policy](https://github.com/NVIDIA/OpenShell/blob/6648bd0c290efbc41ba131ee9831ee45cd431f94/crates/openshell-server/src/grpc/sandbox.rs#L531),
[provider revision hashes even an empty record set](https://github.com/NVIDIA/OpenShell/blob/6648bd0c290efbc41ba131ee9831ee45cd431f94/crates/openshell-server/src/grpc/policy.rs#L2970).

## Persistent per-run evidence

Diagnostics begin before preflight, at
`~/.local/state/openshell-manager/approval-live/<random-run-id>/`. Directories
must be owned by the host user and mode 0700; files are created at 0600.
Symlinks, hard links and permissive existing modes are refused. The parent and
managed bridge append to the same files under a process-shared lock:

| Artifact | Evidence |
| --- | --- |
| `events.jsonl` | Preflight revision/runtime pins, image verification/cache reuse, step start/end, monotonic waits/deadlines, signals and child exits |
| `native.jsonl` | RPC names/correlation/status/latency, mutation intent, receipt observations, every reached confirmation predicate and unexecuted predicates marked `not_evaluated` |
| `telegram.jsonl` | Notice/message correlation, delivery status, user/chat/message match booleans, selected action, decision outcome/latency |
| `sandbox.jsonl` | Creation intent, verified UUID ownership, Ready, sandbox-local setting, cleanup start/end and orphan warnings |
| `commands.jsonl` | Allowlisted executable/operation, exit code, timeout/deadline/duration and fixed error category |
| `result.json` | Ownership intent, workflow outcome/checks, failing stage/invariant, mutation/receipt/grant/cleanup facts and `confirmed` / `suspected` / `unknown` conclusions |
| `facts.json` | Bounded sticky mutation/receipt/grant observations and last failure; survives stream rotation |

Events have UTC timestamps, unique stable event IDs, monotonic elapsed times and
a shared run reference. Native sandbox/proposal, Telegram message and decision
IDs are hashed before recording; the decision reference joins the local UUID to
the mutation RPC correlation. IDs never substitute for native identity checks.
Commands never record arguments, process environments, stdout or stderr. Logs
never record bot/callback/review tokens, raw updates, certificates/keys, policy
or proposal bodies, credentials, native rationale or guest output. Fields are
admitted by an explicit typed allowlist before serialization.

Each stream is capped at 1 MiB with one 1 MiB rotated predecessor. Before native
mutation, after each response, and before cleanup, the event is flushed and
fsynced. Critical facts and final results use private atomic writes plus file
and directory fsync. Cleanup can continue if diagnostic storage becomes
unwritable; this reports a final diagnostic-write failure. Diagnostics survive
test failure, Ctrl-C and successful sandbox deletion.

Under the single-test lock, subsequent runs retain at most the newest 20 runs'
diagnostic files for 30 days. Expiration removes only diagnostic artifacts;
other fixture/state files are untouched. An unconfirmed sandbox's ownership
`result.json` is retained for separately authorized investigation. This exception
prevents retention from deleting the evidence needed to identify an orphan.
Runtime artifacts remain outside the checkout; do not upload or commit them.

## Confirmation and supplied live observations

Approval confirmation distinguishes the RPC returning a receipt from the
supervisor admitting the desired configuration and the exact policy revision
becoming LOADED/active. Identity, creation time, review binding, risk hash,
rule name/bytes, provider revision, effective candidate hash, receipt revision/hash,
LOADED state and active revision have separate fixed codes. The receipt hash
is compared to the stored policy revision; the candidate effective hash is
compared to effective configuration. These are different authorities in the
native API and are not assumed interchangeable.

Confirmation makes at most four read attempts, separated by ten seconds, only
for classified transient states. This provides 30 seconds of waiting for the
pinned supervisor's [default ten-second policy poll](https://github.com/NVIDIA/OpenShell/blob/6648bd0c290efbc41ba131ee9831ee45cd431f94/crates/openshell-supervisor/src/lib.rs#L1023).
The previous three-second wait could expire before the next supervisor poll,
even with a valid approval receipt and candidate policy. Each native RPC retains its 10-second timeout.
Pending admission/load and known transient transport codes can converge.
A LOADED revision with a lagging older active version also gets a bounded
read-only retry: native status reporting persists LOADED before updating the
sandbox's active pointer. A newer active version is drift. Permanent identity,
rule, provider or hash drift stops immediately, even when a load is simultaneously pending. Unexecuted predicates are explicitly marked;
they are not evidence of a pass. If the RPC response is lost, receipt predicates
are `unknown`; independent exact native reads may still establish the outcome.
No approval or rejection RPC is repeated. Crash/uncertainty reconciliation has
at most four additional read-only cycles per request, durably counted in the
existing queue; a permanent mismatch disables further reconciliation. The
original receipt is retained separately from final confirmation data.

After durable callback handling, the Telegram polling cursor advances before
`answerCallbackQuery`. An expired or failed acknowledgement cannot keep an old
callback ahead of later decisions. Decision/status reconciliation remains
durable and does not replay native mutations.

Failure output includes the failing stage and sanitized invariant/RPC code,
mutation attempted, receipt received, possible runtime grant, confirmed owned
sandbox cleanup and the private diagnostic directory. A receipt proves receipt
of the native response; it alone does not prove LOADED or agent access.

The supplied Run 1 observations **confirm** native admission/initialization,
an approval callback, `native_approve_claimed` → `native_needs_review`, an existing
90-byte `native_result`, and successful owned cleanup. Read-only inspection of existing private queues found matching approval-failure
records: their 90-byte JSON receipt has exactly `version` and `hash`, version 2
against reviewed version 1, with receipt hash equal to the reviewed candidate
and different from the previous policy. No raw receipt, identifiers or tokens
are reproduced here. The failed confirmation predicate was not retained.
Policy convergence is **suspected**: the old confirmation stopped at the first
unmet admission/load predicate, and native status reporting can expose LOADED
before the active version advances. The exact failed predicate and whether
runtime access became available remain **unknown**; no Run 1 root cause is asserted. The supplied Run 2 rejection
completed successfully after the operator deliberately chose Reject; this
change preserves rejection checks and does not claim a rejection-path defect.

Pinned source supports the separation: approval returns the stored merge
revision/hash after updating chunk status
([bulk approval implementation](https://github.com/NVIDIA/OpenShell/blob/6648bd0c290efbc41ba131ee9831ee45cd431f94/crates/openshell-server/src/grpc/policy.rs#L5774));
effective configuration reports its effective policy hash
([configuration response](https://github.com/NVIDIA/OpenShell/blob/6648bd0c290efbc41ba131ee9831ee45cd431f94/crates/openshell-server/src/grpc/policy.rs#L3506)).
[Status reporting writes LOADED before the active version](https://github.com/NVIDIA/OpenShell/blob/6648bd0c290efbc41ba131ee9831ee45cd431f94/crates/openshell-server/src/grpc/policy.rs#L4609)
and [status reads the active pointer separately](https://github.com/NVIDIA/OpenShell/blob/6648bd0c290efbc41ba131ee9831ee45cd431f94/crates/openshell-server/src/grpc/policy.rs#L4256).
The new synthetic tests exercise these transitions; no live test was rerun.
