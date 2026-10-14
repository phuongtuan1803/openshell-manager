# OpenShell Manager — Thiết kế tách repository & kế hoạch triển khai

> **Trạng thái:** PROPOSAL / PLAN — chưa triển khai migration hay Telegram Approval.
>
> **Nguồn:** private repository \`phuongtuan1803/one-man-agency\`, nhánh \`main\`, commit tham chiếu \`f23b8926bf4adc663e5baca936c80a5844cec6ce\` (2026-10-14).
>
> **Đích:** public repository \`phuongtuan1803/openshell-manager\`.
>
> **Quy tắc:** Chỉ tài liệu này được thêm trong bước lập kế hoạch. Chưa chuyển source, chưa sửa OMA, chưa thay đổi Gateway/MicroVM/credentials/policy và chưa tạo PR triển khai.

## 1. Mục tiêu và ranh giới

**Mục tiêu:** Tách bộ công cụ quản lý OpenShell đang nằm trong OMA thành một sản phẩm độc lập, có thể sử dụng cho nhiều repository, trong khi duy trì hành vi, quyền, trạng thái và quy trình đang chạy. Sau khi nền tảng ổn định, bổ sung phê duyệt yêu cầu quyền bằng Telegram trên **OpenShell host**.

**Không thuộc phạm vi migration ban đầu:**
- Không fork hay sửa OpenShell upstream; đây là manager/wrapper trên OpenShell.
- Không nâng cấp OpenShell, Codex, Claude Code hoặc OpenCode trong cùng PR migration.
- Không thay đổi OMA runtime agents, LangGraph, n8n hoặc M11 Gate.
- Không tự cấp quyền rộng, tự merge PR, tự thay đổi remote/working tree khi dirty.
- Không triển khai Telegram Approval trong PR tách core; Telegram là giai đoạn riêng.
- Không tái cấu trúc hệ thống chung, xây dashboard, multi-host hoặc auto-approve phức tạp.

**Tiêu chí thành công:** Manager chạy độc lập; mỗi project có profile riêng; OMA dùng phiên bản Manager được pin; test/CI chia đúng ownership; có kế hoạch triển khai Telegram P1 riêng. Các bước đều có rollback an toàn.

## 2. Hiện trạng đã xác minh

Trong OMA, triển khai nằm ở \`infra/dev-sandbox/\`:
- \`control.py\`: entrypoint và orchestration, state, sandbox, rules, session.
- \`bootstrap.py\`, \`setup.sh\`, \`start.sh\`, \`verify.sh\`, \`rules.sh\`: khởi tạo/vận hành.
- \`agents.py\`: các adapter Codex, Claude Code, OpenCode; **chỉ Codex có live verification**.
- \`guest.py\`, \`session.py\`: guest execution, checkout sync, khóa khi agent hoạt động.
- \`workflow.py\`, \`github_provider.py\`, \`github.sh\`: scoped task, tests, GitHub provider và PR.
- \`snapshot.py\`, \`export.sh\`, \`refresh.sh\`, \`validate*.py\`: source export, kiểm tra.
- \`Dockerfile*\`, \`gateway.vm.example.toml\`, \`policy.yaml\`, \`policies/*\`, \`phase2.json\`, \`source-manifest.txt\`, \`edit-scope.txt\`: image, policy và cấu hình.
- Test có trong \`tools/tests/test_dev_sandbox*.py\`.
- Runbook nguồn: \`docs/src/runbooks/OpenShell Development Sandbox.report.json\` và các output dẫn xuất.

**Phụ thuộc OMA cần loại bỏ khỏi core:** các chuỗi \`oma-dev-phase2\`, \`oma-github\`, \`/sandbox/oma\`, đường dẫn \`.work/openshell-phase2\`, Git remote hardcoded, repo-relative test/scope paths, lệnh render runbook qua tooling riêng của OMA. Không chuyển các giả định này thành defaults chung.

**Điểm quan trọng:** Phiên bản hiện được pin: OpenShell 0.1.2; Codex CLI 0.162.1. Hiện tại \`control.py\` dùng compatibility inbox phân tích structured TCP DENIED logs; đây **không** phải native proposal transport đầy đủ. Các trường method/path có thể không có. Phải xác minh lệnh/API native trên bản cài thực tế trước khi xây Telegram bridge.

**Public/private boundary:** Manager là **public**, OMA là **private**. Không chép nguyên cây OMA, không chép history, bằng chứng có dữ liệu riêng, log, token, secrets, auth profile, payload hoặc toàn bộ runbook OMA một cách tự động. Mọi file code/config chuyển sang public phải được rà soát độc lập trước commit/push. Chỉ giữ ví dụ generic; OMA-specific values để trong OMA hoặc local host.

## 3. Kiến trúc mục tiêu

~~~mermaid
flowchart LR
    subgraph PROJECT["Project repository: OMA or another project"]
        PC["Project profile"]
        CODE["Project source"]
    end
    subgraph MANAGER["openshell-manager repository"]
        CLI["CLI and scripts"]
        CORE["Sandbox controller"]
        AG["Agent adapters"]
        POL["Policy workflow"]
        WF["Scoped task / Git workflow"]
        TG["Telegram approval bridge - later phase"]
    end
    subgraph HOST["Linux Host"]
        GW["OpenShell Gateway"]
        VM["MicroVM sandbox"]
    end
    PC --> CLI
    CODE --> WF
    CLI --> CORE
    CORE --> AG
    CORE --> POL
    CORE --> WF
    POL --> GW
    AG --> GW
    GW --> VM
    TG -. "verified decision" .-> POL
~~~

### Ownership

| Thành phần | Chủ sở hữu sau migration | Ghi chú |
| --- | --- | --- |
| CLI, controller, bootstrap, session, sandbox lifecycle | Manager | Không import package từ OMA |
| Agent adapters, GitHub workflow adapter, tests core | Manager | Hành vi current giữ nguyên |
| Project URL, sandbox name, branch, workspace, test command | Project profile trong OMA | Không hardcode trong Manager |
| Project network/filesystem scope, provider target, edit scope | Project profile trong OMA | Quyền không được rộng hơn hiện tại |
| Gateway config, TLS, secrets, active state | Host | Không commit Git |
| Telegram bridge & approval store (giai đoạn sau) | Manager chạy trên host | Không phụ thuộc M11 Gate |
| OMA business approval qua M11 Gate | OMA | Không di chuyển hoặc đồng nhất với OpenShell approval |

### Cấu trúc hướng tới (chưa tạo)

~~~text
openshell-manager/
├── config/
│   ├── defaults.yaml
│   ├── agents/
│   ├── policies/
│   ├── schemas/
│   └── telegram.example.yaml
├── src/openshell_manager/
│   ├── core/
│   ├── agents/
│   ├── policy/
│   ├── projects/
│   └── approval/             # Thêm trong giai đoạn Telegram
├── scripts/                  # setup/start/verify
├── tests/
└── docs/
    ├── plans/                # File này
    └── runbooks/

one-man-agency/
└── infra/openshell/
    ├── project.yaml          # Repo/workspace/sandbox/agent
    ├── policy.yaml           # Project-specific policy
    ├── github-provider.yaml  # Chỉ endpoints OMA
    ├── scope.yaml            # Manifest / editable paths
    └── version.lock          # Pin immutable Manager revision

Linux host (không commit):
~/.config/openshell/gateway.toml
~/.config/openshell-manager/host.yaml
~/.config/openshell-manager/telegram.env   # mode 0600; giai đoạn Telegram
~/.local/state/openshell-manager/           # logs/state/approval journal
~~~

**Ưu tiên:** Cấu trúc trên là đích định hướng. Khi PR1 có thể giữ package/file layout gần code hiện có để giảm refactor; không cần xây framework config mới trong một lần.

**Config resolution:** Manager defaults → project config → host runtime settings. Validated resolved config dùng để kiểm tra; merge cấu hình **không được tự mở rộng quyền**. Thay đổi effective security policy vẫn qua procedure kiểm tra revision/identity và approval. Giữ riêng secrets, local state, evidence.

## 4. Kế hoạch migration theo PR

| PR | Repository | Công việc | Điều kiện hoàn thành |
| --- | --- | --- | --- |
| PR1 — Import & inventory | Manager | Import có chọn lọc core/script/test; giữ logic; bỏ bằng chứng nhạy cảm; tạo README và test đơn vị | Test đã import pass; không có secret; không thay đổi OMA |
| PR2 — Generalize config | Manager | Gỡ hardcode OMA, thêm project profile/schema, kiểm tra policy/path/locking, package entrypoint | Chạy profile test cho OMA và một fixture generic; không làm yếu security |
| PR3 — Telegram Approval P1 | Manager | Native proposal adapter, host-side approval queue và Telegram bridge, security tests | Approve/reject/timeout/stale/offline/replay verified; deny by default |
| PR4 — OMA integration | OMA | Chuyển cấu hình project; pin Manager revision; launcher tương thích; chạy migration verify | Codex + Git sync + workflow tương đương; không đụng dirty tree/active session |
| PR5 — Cleanup | OMA | Sau cutover: xóa duplicate core/tests; update docs/CI; giữ hướng dẫn rollback | OMA không còn sở hữu implementation; tests chạy đúng repo |

**Thứ tự bắt buộc:** PR1 → PR2 → (PR3 có thể triển khai sau PR2, trước/sau PR4 nếu độc lập) → PR4 → PR5. Không xóa source cũ trước PR4 acceptance; không gộp migration với thay đổi cấp quyền.

### PR1 — Hướng dẫn thực thi cho Codex

1. Từ commit OMA đã pin, lập inventory file và import dependency graph; ghi rõ phần generic vs project-specific.
2. Chỉ port source core, adapter, scripts và unit tests thực sự cần; không copy \`evidence/\`, \`*.evidence.json\`, \`.work/\`, generated reports hoặc secrets.
3. Tất cả config/profile công khai chỉ chứa placeholder hoặc dữ liệu generic. Không commit nội dung private OMA chưa review.
4. Giữ entrypoint hiện tại tương đương (setup/start/verify, rules và export) trước khi refactor cấu trúc sâu.
5. Test offline/mocked trước, không tạo sandbox/đổi policy/cài dependencies trên host chỉ để chạy thử.
6. Ghi bảng old path → new path và các hardcode còn chờ PR2 trong PR description hoặc docs; không tự sửa OMA.
7. Chạy một lần bộ kiểm tra cần thiết sau khi hoàn tất. Không chạy lại live/paid eval có bằng chứng hợp lệ còn mới (trong vòng 1 tháng).
8. Dừng và báo nếu thấy credential, private data, public disclosure risk, version drift hoặc thao tác cần quyền host.

### PR2 — Điều kiện decoupling

- Không còn \`REPO=HERE.parent.parent\`, \`NAME=oma-dev-phase2\`, path \`/sandbox/oma\` hoặc \`REPOSITORY=phuongtuan1803/one-man-agency\` trong core generic.
- Tên sandbox hợp lệ theo giới hạn native CLI; không tự đổi sandbox đang tồn tại.
- Git checkout sync chỉ fast-forward khi an toàn; dirty/diverged/worktree active phải skip với lý do, không ghi đè.
- Các quyền GitHub fetch/push/PR do project provider profile giới hạn; không cho push main/merge/force-push theo mặc định.
- Ngăn project A truy cập state, credentials và đường dẫn project B.
- Config pin/version/lock rõ ràng, không tự update Manager khi có active session.

## 5. Telegram Approval P1 — giai đoạn sau khi core đã độc lập

### Hành vi

~~~mermaid
flowchart LR
    A["Agent in sandbox"] --> B["OpenShell denial / proposal"]
    B --> C["Host proposal collector"]
    C --> D["Pending approval store"]
    D --> E["Telegram Bot"]
    E -->|"Approve / Reject / Details"| F["Host decision verifier"]
    F -->|"Matched and valid"| G["Native decision or scoped policy update"]
    F -->|"Invalid / expired"| H["Deny and request review"]
    G --> I["OpenShell effective policy"]
    I --> A
~~~

**Bảo lưu thiết kế đã chọn:** native OpenShell proposal/decision ưu tiên; compatibility fallback chỉ dùng khi native route không có hoặc chưa hỗ trợ. Ở fallback hiện tại, parsing TCP DENIED không đủ method/path và không bảo đảm đầy đủ; tuyệt đối không biến log parsing thành auto-grant. Khi dữ liệu chưa đủ, gửi thông báo + hướng dẫn review thủ công trên host.

**Host bridge:** một tiến trình riêng trên host; polling Telegram outbound, không mở inbound port, không đẩy bot token vào guest. Ưu tiên bot riêng với OMA M11 Gate để tránh hai polling consumers cùng một bot.

**Notification tối thiểu:** sandbox, agent/session, action, target, executable, requested access, lý do, risk, rule/proposal ID, TTL, effective revision; không gửi secret hay nội dung source. Telegram buttons: Approve rule, Reject, Details. **Không gọi là Approve once** nếu native operation cấp rule tồn tại sau lần quyết định.

**Security invariants:** xác thực user_id và chat_id, callback bound to proposal + sandbox identity + version/hash, expiry + idempotency + chống replay, recheck native effective policy trước apply, audit decision/revision, failed/offline/unknown -> deny. Không tự persist approved rule vào project config; thay đổi lâu dài vẫn qua PR/review.

**Acceptance:** tests bao phủ approve, reject, duplicate press, unauthorized sender, stale revision, stale/expired request, policy drift, concurrent requests, Telegram failures, restart/recovery, fallback thiếu dữ liệu và không vượt scope. Live native test là bước được phê duyệt riêng trên host.

## 6. Validation, rollback & stop conditions

**Kiểm tra migration:**
- So sánh behavior setup, policy check, sandbox identity, agent launch, Git sync và scoped workflow trước/sau.
- Không build/replace/restart Gateway hoặc MicroVM đang chạy trong migration đơn thuần.
- Không gọi model API trả phí hoặc thử nghiệm live lặp lại nếu không có yêu cầu rõ ràng.
- CI của Manager kiểm thử core + security; CI OMA chỉ kiểm tra integration/pin theo phạm vi nhỏ.
- Verify rằng không file public mới nào chứa OMA credentials, snapshot private, raw logs, personal data hoặc secret material.

**Rollback:** Giữ \`infra/dev-sandbox/\` cũ trong OMA tới khi PR4 acceptance pass; ghi rõ previous pinned commit và lệnh chạy cũ. Không xóa local state hoặc credential store. Nếu test/behavior không tương đương, revert OMA integration về launcher cũ, không cố tự sửa policy.

**Dừng để Founder quyết định khi:** (1) cần public hóa code/config private, (2) cần thay đổi effective sandbox/network permissions, (3) cần host credential/token mới, (4) cần xóa/tái tạo sandbox hoặc migrate state, (5) phát hiện native API không đáp ứng contract, (6) thay đổi out-of-scope.

**Governance:** Mọi PR triển khai chỉ tạo sau khi được yêu cầu/phê duyệt; không tự merge. Tài liệu proposal có thể được cập nhật theo review trước khi code.

## 7. Handoff cho Codex

**Lệnh ở host (không chạy trong guest OMA hiện có):** clone cả hai repo cạnh nhau; dùng Manager làm working directory. OMA chỉ để đọc. Tránh mở Codex từ trong OMA checkout rồi sửa đồng thời 2 repo.

**Mục tiêu phiên đầu:** thực hiện riêng PR1 trên manager, sau khi xác minh public-disclosure boundary. Không sửa OMA, không kích hoạt Telegram, không nâng cấp CLI/Gateway, không xóa source cũ, không tạo PR/merge cho tới khi được yêu cầu.

**Đầu ra cần có:** inventory + migration mapping, imported code/test files, kết quả offline tests, danh sách OMA coupling còn lại cho PR2, public data audit, summary và blockers.