# 系统收敛、动态出口与可靠性闭环

本文回答四个具体问题：旧架构哪些能停、动态住宅出口怎样接、SQLite/幂等/lease
分别解决什么问题，以及怎样让面板只在账号真正可用后显示成功。

## 0. 结论先行

1. **新 register-panel 已完成一个单账号供给闭环。** 新 auth 已经过精确 provider、
   controller import、CLIProxy 热加载和公网数据面，最终状态为 `verified`。controller
   与 CLIProxy 不再由面板分别写两份状态。
2. **旧补号组当前保留。** 这是用户明确决定。`pool-controller` 现在还是新面板凭据导入、
   健康状态和 `ops.canxu.top` 的正式组成；没有新的退役动作。
3. **动态住宅的关键不是“每个 HTTP 请求换 IP”，而是“每个账号生成一个新 session，
   账号全流程保持同一出口，下一个账号再换”。** OVH 能直连供应商 gateway 时直接接；
   直连不稳时才用 mihomo `dialer-proxy` 增加中转层。
4. **managed proxy pool 是出口控制器，SQLite lease 是并发所有权。** 前者判断代理是否
   健康、何时冷却；后者保证同一时刻只有一个 worker 占用该账号任务和 session。
5. **面板自动成功口径已经收紧。** 开启门禁时，必须经过“精确 token 探针、controller
   导入、CLIProxy 热加载、现有数据面探针”后才进入 `verified`；默认开关仍关闭。

## 1. 证据范围与当前快照

### 1.1 证据边界

| 证据 | 核验范围 | 用途 |
|---|---|---|
| OVH 只读运行审计 | 2026-07-31 | 进程、容器、内存、磁盘、目录挂载和真实依赖 |
| 当前仓库源码 | 本地与 OVH 运行基线 `7af0e72` | 判断当前能力和接线缺口 |
| 上游仓库 | `d0b7c6cdf30e2193acd1e9eb6d56b0e5201daad1` | managed proxy pool 的已合并实现 |
| LINUX DO 本地完整导出与近期主题 | #2673498、#2680750，核验到 2026-08-01 | 社区实践、WARP 和出口策略 |
| 供应商与 mihomo 官方文档 | 2026-07-31 重新核验 | session、sticky、health-check、链式出口语义 |

社区经验只用于提出 canary 假设。WARP 的模式和边界以 Cloudflare 官方文档为准；当前
系统是否可用以 OVH 精确探针和数据面回归为准。

### 1.2 当前真实拓扑

```mermaid
flowchart LR
    operator["运维者"] -->|"SSH 隧道"| panel

    subgraph new_panel["新 register-panel - OVH"]
        panel["Monitor<br/>systemd / loopback"]
        worker["1 个注册 worker<br/>Xvfb + Camoufox"]
        proxy["Managed proxy pool<br/>WARP local proxy"]
        panel_store[("私有 cpa_auth<br/>当前 3 条 auth")]
        panel --> worker --> panel_store
        proxy --> worker
    end

    worker --> mail["Cloudflare 邮箱"]
    worker --> xai["xAI 注册 / SSO / OAuth"]

    subgraph old_supply["旧补号控制面 - 仍在运行"]
        controller["pool-controller"]
        old_worker["pool-auth-worker"]
        standby["pool-auth-worker-standby"]
        browser_ctx["browser-registration-context"]
        mail_relay["mail-relay"]
        controller --> old_worker
        controller --> standby
        old_worker --> browser_ctx
        old_worker --> mail_relay
    end

    subgraph data_plane["现有 AI 数据面 - 必须保留"]
        gateway["api-gateway"] --> newapi["New API"]
        newapi --> cpa["CLIProxyAPI"]
        newapi --> pg[("PostgreSQL")]
        newapi --> redis[("Redis")]
        cpa --> live_auth[("CLIProxy auth volume<br/>104 enabled")]
    end

    worker -->|"/v1/credentials/import"| controller
    controller -->|"Management API"| cpa
    tunnel["cloudflared"] --> controller
    tunnel --> newapi
```

新面板不直接把“本地文件存在”当成功；controller 是正式导入入口，CLIProxy 是运行池，
公网数据面是最后验收。旧补号组按当前决策继续运行。

## 2. 旧架构是否应该去掉

### 2.1 资源审计

| 项目 | 当前观测 | 判断 |
|---|---:|---|
| register-panel monitor | 进程 RSS 约 26 MiB；systemd 快照约 14 MiB | 常驻开销很小，保留 |
| `/opt/grok-register-panel` | 约 1.6 GiB | 主要不是日志或旧组件 |
| Camoufox cache | 约 1.3 GiB | 注册时必需；删除只会在下次重新下载 |
| Python `.venv` | 约 329 MiB | 运行依赖，保留 |
| 旧补号 5 个容器 | 实际工作集合计约 155 MiB | 桥接验收后可释放 |
| 两个已停止旧容器 | 0 RAM；可写层合计约 138 MiB | 可在观察期后单独删除 |
| Docker images / build cache | 宿主机层面有数 GiB 可回收提示 | 与其他业务共享，禁止直接全局 prune |

Compose 中的内存 limit 是上限，不等于真实占用。旧补号组当前实际约 155 MiB，确实
值得收敛，但它不是这台机器的主要磁盘来源；最大的 1.3 GiB 是新方案仍然需要的
Camoufox 浏览器引擎。

### 2.2 必须保留、可停、最后处理

| 组件 | 决策 | 原因 / 退出条件 |
|---|---|---|
| `grok-register-panel.service` | 保留 | 新控制台与注册入口 |
| `.venv`、Camoufox cache、Xvfb | 保留 | Camoufox 运行依赖；Xvfb 只在任务期活跃 |
| `config.json`、`accounts/`、`cpa_auth/`、`log/` | 保留并备份 | 配置、凭据和审计事实来源 |
| 未使用的邮箱 provider 源码 | 保留源码、关闭配置 | 不常驻、不耗显著 RAM；删除会增加升级和回归成本 |
| `pool-auth-worker` | 当前保留 | 用户已决定暂不移除旧补号链 |
| `pool-auth-worker-standby` | 当前保留 | 与旧 worker 同一决策 |
| `browser-registration-context` | 当前保留 | 仍服务旧补号 worker；不是新 Camoufox/Xvfb |
| `mail-relay` | 当前保留 | 仍服务旧补号链 |
| `pool-controller` | 必须保留 | 新面板已通过它导入 auth；并承担健康和公开运维入口 |
| `api-gateway`、`new-api`、`cli-proxy-api`、PostgreSQL、Redis | 保留 | 这是实际 API 数据面，不属于旧注册链 |
| `cloudflared` | 保留并改依赖 | 同时暴露 New API；移除 controller 前先调整 route/depends_on |

### 2.3 将来若重新决定收敛时的可回滚顺序

```mermaid
flowchart TD
    g0["G0 备份配置、auth 和 compose<br/>记录当前容器与请求基线"] --> g1
    g1["G1 controller import 桥与四门禁<br/>当前已完成"] --> g2
    g2["G2 单账号完整 canary<br/>精确 token + 上传 + 热加载 + 数据面"] --> decision{"连续样本均 verified?"}
    decision -->|"否"| rollback["保持旧补号组运行<br/>修复分类后的单一缺口"]
    decision -->|"是"| g3["G3 只 stop 旧 worker / standby<br/>browser context / mail relay"]
    g3 --> observe["观察至少一个完整补号周期<br/>并做 24 小时数据面回归"]
    observe --> g4["G4 把 controller 的健康与运维能力<br/>迁入新面板或独立轻量任务"]
    g4 --> g5["G5 调整 cloudflared 后 stop controller"]
    g5 --> cleanup["保留回滚期后按容器名删除<br/>不做全局 docker prune"]
```

当前不执行 G3。将来若用户重新决定收敛，第一阶段仍应使用 `stop` 而不是 `rm`，并且
至少要求以下门禁持续通过：

- 新 auth 已通过**该 auth 自身**的最小 provider 请求；
- controller import 成功，CLIProxy 的 auth 列表能看到相同文件名/大小；
- 现有 api-gateway/New API 路径返回有效 `2xx` JSON；
- 停止旧 worker 不影响已有账号的真实客户端请求。

## 3. 动态住宅出口：最近社区方案与推荐落地

### 3.1 LINUX DO 最近讨论中反复出现的共识

| 帖子证据 | 社区观察 | 工程结论 |
|---|---|---|
| [#3](https://linux.do/t/topic/2673498/3)、[#15](https://linux.do/t/topic/2673498/15) | 注册用动态住宅，后续使用用静态住宅 | 注册出口池与使用出口池分离 |
| [#38](https://linux.do/t/topic/2673498/38)、[#102](https://linux.do/t/topic/2673498/102) | 单 IP 高并发和短窗口密度容易出问题 | 按出口预算速率，不只限制全局 worker 数 |
| [#48](https://linux.do/t/topic/2673498/48)、[#105](https://linux.do/t/topic/2673498/105) | 注册存在 IP/窗口约束；有人每账号换动态 IP | 下一个账号换 session，本账号全程 sticky |
| [#76](https://linux.do/t/topic/2673498/76) | 动态供应商也可能反复给到同一坏 IP | 记录 exit IP/ASN 历史，不能只相信“已旋转” |
| [#109](https://linux.do/t/topic/2673498/109) | 作者提到 ipfast 动态出口 | 这是单一社区样本，不足以形成供应商排名 |
| [#114](https://linux.do/t/topic/2673498/114)、[#143](https://linux.do/t/topic/2673498/143) | Camoufox 配机场轮换、机场链论坛代理池均有成功样本 | 直连 gateway 不通时，可用 mihomo 做透明中转 |
| [#142](https://linux.do/t/topic/2673498/142) | 有样本称 `$4/GiB`，20 多次尝试消耗超过 500 MiB | 失败重试会显著放大流量成本，先做熔断 |
| [#147](https://linux.do/t/topic/2673498/147) | 社区提到 DataImpulse 共享住宅 `$1/GiB` | 价格须以官方当前页为准，帖子链接含推广属性 |
| [#151](https://linux.do/t/topic/2673498/151) | 有人猜测 OAuth 前后 IP 一致性会影响结果 | 作为待验证假设；工程上仍应保持端到端 sticky |

帖子里的 21 秒和 54 秒样本分别以 risk deny、`Access denied` 结束，不是成功速度基线。
当前 WARP canary 的两段有效执行时间约 66 秒；日常容量仍按约 50 至 80 秒/verified
估算，一个样本不能形成 P95。

### 3.2 当前出口与真实动态住宅的边界

当前已部署 Cloudflare WARP local proxy，受管池探活为健康，宿主机默认路由不变。它在
一个单账号 canary 中解决了 OVH 直连 Turnstile 路径，但 WARP 不是住宅 ASN，也不提供
每账号 session 轮换。

若后续证据证明必须使用真实动态住宅，推荐顺序不是先选品牌，而是先固定连接语义：

1. **首选：OVH 直接连接住宅供应商 gateway。** 每个 task 生成唯一 `SESSION_ID`，
   将同一条代理 URL 从注册页、OTP、SSO 一直用到 token 完成。
2. **备选：mihomo loopback 链式出口。** 只有 OVH 到供应商 gateway 的路由质量或访问
   稳定性确有问题时，才让住宅代理通过 `dialer-proxy` 拨号到中转节点。注册程序仍只
   使用 `127.0.0.1:PORT` 一层入口。
3. **不采用：每请求自动轮换。** OTP 前后、SSO 与 Device Flow 中途换出口，会把一次
   注册拆成多个风险画像，也无法准确归因失败。

```mermaid
sequenceDiagram
    autonumber
    participant Scheduler as Scheduler / SQLite
    participant Pool as Managed Proxy Pool
    participant Provider as 住宅供应商 Gateway
    participant Worker as Camoufox Worker
    participant XAI as xAI

    Scheduler->>Pool: claim task + request SESSION_ID
    Pool->>Provider: 用 SESSION_ID 探测出口
    Provider-->>Pool: exit IP / ASN / latency
    Pool->>Pool: 去重、黑名单、近期复用和冷却检查
    Pool-->>Worker: lease(PROXY_ID, SESSION_ID, TTL)
    Worker->>XAI: 注册页 / OTP / Turnstile
    Worker->>XAI: SSO / Device Flow / token
    Note over Worker,XAI: 整个账号保持同一代理 URL 和出口
    Worker->>Pool: heartbeat + phase
    alt 成功
        Worker->>Pool: verified / release lease
    else 临时网络错误
        Worker->>Pool: network failure / 短冷却
    else risk deny
        Worker->>Pool: risk failure / 长冷却
    else 出口中途变化
        Worker->>Pool: quarantine session / 不继续该账号
    end
```

DataImpulse 当前[住宅代理页](https://dataimpulse.com/residential-proxies/)公开支持
HTTP(S)/SOCKS5、rotating 和 sticky sessions；其官方
[`sessid` 文档](https://docs.dataimpulse.com/proxies/parameters/session-id.md)说明同一
session 平均保持约 30 分钟，但住宅 peer 离线时仍可能自动换 IP；
[`sessttl` 文档](https://docs.dataimpulse.com/proxies/parameters/session-interval.md)允许
控制 sticky 轮换间隔。这说明“sticky”是尽力保持，不是永久不变，所以 worker 仍要在
开始前记录 exit IP，并在关键边界复核。

供应商 URL 只在 0600 secret 中按任务渲染，不写进公开 JSON、状态接口或日志：

```text
http://PROXY_USER__sessid.SESSION_ID:TOKEN@HOST:PORT
```

具体参数拼接以供应商控制台生成结果为准；通用参数规则可查其
[官方参数文档](https://docs.dataimpulse.com/proxies/parameters.md)。若需要链式出口，
使用 mihomo 官方支持的 [`dialer-proxy`](https://wiki.metacubex.one/config/proxies/dialer-proxy/)
能力，形式如下：

```yaml
proxies:
  - name: residential-session
    type: http
    server: HOST
    port: PORT
    username: PROXY_USER__sessid.SESSION_ID
    password: TOKEN
    dialer-proxy: transit-node
```

### 3.3 “上游受管代理池、探活、冷却”到底是什么

这里的“受管”不是指供应商替我们管理，而是 register-panel 自己维护代理状态。上游
[PR #6](https://github.com/lij768423-svg/grok-register-panel/pull/6) 已在
[`d0b7c6c`](https://github.com/lij768423-svg/grok-register-panel/commit/d0b7c6cdf30e2193acd1e9eb6d56b0e5201daad1)
合并以下能力：

- 导入、规范化、去重、启用/禁用代理，状态原子写入 `log/proxy_pool.json`；
- 最多 4 线程探测出口 IP、ASN/组织和延迟；
- worker 只拿 `enabled + healthy` 的条目；配置了受管池却无可用代理时 fail closed；
- 网络错误默认短冷却 90 秒，registration risk 默认长冷却 1800 秒；
- 同一账号保持固定代理，下一个账号或 risk 后轮换；API 和日志做凭据脱敏。

三个词可以这样理解：

| 概念 | 类比 | 实际职责 |
|---|---|---|
| managed pool | 出租车调度台 | 维护有哪些出口、当前状态和历史表现 |
| health check | 出车前验车 | 通过同一代理查询外部 IP/ASN/延迟，不只测 TCP 端口 |
| cooldown | 暂停派单 | 失败后到 `next_retry_at` 前不再分配，避免持续撞同一坏出口 |
| lease | 临时车钥匙 | 某 worker 在 TTL 内独占某 task/session，崩溃后可回收 |

当前 OVH 已部署 `webui/proxy_store.py`、面板导入/探活/冷却 API、worker 固定出口和
恢复任务继承出口。池内目前有 1 个健康 WARP local proxy；历史住宅样本的 `407` 不是
当前出口状态。缺少的是持久 lease 和真实动态住宅 provider，不是 managed pool 代码。

### 3.4 必须补充的失败分类

| 事件 | 动作 | 理由 |
|---|---|---|
| timeout、connect reset、临时 5xx | 当前 session 短冷却；有限重试 | 可能是瞬时链路问题 |
| 明确 registration risk / policy deny | exit IP/ASN 长冷却 | 短时间重试通常只会重复命中 |
| `407 Proxy Authentication Required` | 供应商级熔断，暂停新任务 | 常见于凭据、余额或套餐失效，不能只处罚一个 IP |
| 邮箱 API 401、收信失败 | 不处罚代理 | 错误域不同，避免错误归因 |
| 探测 exit IP 与注册中途 exit IP 不同 | 隔离 session，当前账号不继续 | sticky 失效，账号风险画像已经变化 |
| 所有受管代理均不可用 | fail closed | 禁止悄悄回退 OVH 直连造成出口策略漂移 |

mihomo 的 [proxy-provider health-check](https://wiki.metacubex.one/config/proxy-providers/)
也支持 URL、间隔、超时和期望 HTTP 状态。它适合判断“中转节点是否活着”；业务层仍需
register-panel 自己记录住宅 exit IP、registration risk 和账号级 session，因为 mihomo
不知道某次注册属于哪个账号。

## 4. SQLite 持久状态、账号幂等键和 proxy lease

### 4.1 为什么现在的 JSON 不够

当前 `batch_supervisor.py` 的临时进度文件只记录 `completed` 和 `target`，结束后会删除；
`register_results.jsonl` 是审计日志，不具备唯一约束、事务 claim 或崩溃恢复能力。
因此重启后只能知道“大概完成几个”，不知道哪一个 slot 正在做、哪个账号已写入、哪个
session 仍被 worker 占用。

### 4.2 三个概念的通俗解释

- **SQLite 持久状态**像一本断电也不会丢的值班账：每个任务走到 OTP、SSO、token 还是
  verified 都有明确记录，服务重启后从原阶段恢复。
- **账号幂等键**像订单号：同一个账号的重试、重复回调和补录都使用同一个 key，数据库
  的 UNIQUE 约束保证只落一份，不会覆盖已有 token 或重复增加成功数。
- **proxy lease**像有过期时间的临时钥匙：worker claim 后独占 task/session，定时续租；
  worker 崩溃后 lease 到期，调度器才能把任务安全交给别人。

它们解决的是三个不同维度：状态是否丢、结果是否重复、并发是否争抢。

### 4.3 最小单机模型

对当前 1 至 2 workers，不需要先引入 Redis、PostgreSQL 或跨主机 MQ。建议数据库为
`log/state.sqlite3`，权限 0600，启用 WAL、foreign keys 和 5 秒 busy timeout：

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    target_count INTEGER NOT NULL,
    workers INTEGER NOT NULL,
    status TEXT NOT NULL,
    config_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    ordinal INTEGER NOT NULL,
    idem_key TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    worker_id TEXT,
    proxy_id TEXT,
    lease_until INTEGER,
    heartbeat_at INTEGER,
    email TEXT,
    result_kind TEXT,
    error TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(job_id, ordinal),
    UNIQUE(job_id, idem_key)
);

CREATE TABLE accounts (
    id TEXT PRIMARY KEY,
    idem_key TEXT NOT NULL UNIQUE,
    normalized_email TEXT NOT NULL UNIQUE,
    file_path TEXT NOT NULL,
    status TEXT NOT NULL,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE proxies (
    id TEXT PRIMARY KEY,
    public_ref TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL,
    lease_owner TEXT,
    lease_until INTEGER,
    heartbeat_at INTEGER,
    fail_count INTEGER NOT NULL DEFAULT 0,
    next_retry_at INTEGER,
    exit_ip TEXT,
    asn TEXT,
    last_error TEXT,
    updated_at INTEGER NOT NULL
);
```

`public_ref` 只保存脱敏 ID 或 secret 的引用，不保存带用户名/密码的代理 URL；账号表只
保存结果文件路径和状态，access token、refresh token、密码继续留在 0600 auth/account
文件中。task 幂等键可用 `job:<JOB_ID>:slot:<ORDINAL>`，账号幂等键使用规范化邮箱的
稳定散列；二者不要混成一个 key。

### 4.4 claim、心跳与崩溃恢复

```mermaid
stateDiagram-v2
    [*] --> pending
    pending --> leased: BEGIN IMMEDIATE + claim
    leased --> running: worker 启动
    running --> running: 每 10 秒 heartbeat
    running --> verified: 完整验收通过
    running --> retry_wait: 可重试错误
    running --> terminal_failed: risk / provider denied / 配置错误
    retry_wait --> pending: next_retry_at 到期
    leased --> pending: lease 超过 60 秒未续租
    running --> pending: worker 崩溃且 lease 到期
    verified --> [*]
    terminal_failed --> [*]
```

claim 必须在 `BEGIN IMMEDIATE` 事务中完成“选择 pending 行 + 写 worker/lease”，避免
两个 worker 同时选中同一 task。建议 task/proxy lease TTL 60 秒、每 10 秒 heartbeat；
恢复任务只回收已过期 lease，不抢仍有心跳的 worker。SQLite 备份使用在线 `.backup`
或 `VACUUM INTO`，WAL 活跃时不要直接复制单个主库文件。

## 5. 怎样让面板自动显示“真正成功”

### 5.1 假成功怎样被消除

旧逻辑把“auth 已写文件”视作成功。当前 `add_sso_to_cpa()` 在 `CPA_AUTO_VERIFY=1` 时
先使用本次 access token 做严格 provider 探针，再走 controller import、CLIProxy 热加载
和公网数据面。任何一门失败都写入 pending，不增加 verified；历史记录保持
`legacy_unverified`。生产默认仍为 `CPA_AUTO_VERIFY=0`，只有受控 canary 临时开启。

### 5.2 目标状态机

```mermaid
stateDiagram-v2
    [*] --> token_written
    token_written --> provider_verifying
    provider_verifying --> provider_verified: 本次 token 返回有效 2xx JSON
    provider_verifying --> provider_denied: 明确 Access denied / invalid token
    provider_verifying --> verification_retry: timeout / 429 / 临时 5xx
    verification_retry --> provider_verifying: 有界退避
    verification_retry --> verification_failed: 超过重试预算
    provider_verified --> pool_uploading
    pool_uploading --> pool_uploaded: controller import 接受并登记
    pool_uploading --> pool_sync_failed: 鉴权 / 配置 / 写入失败
    pool_uploaded --> pool_loaded: auth 列表出现文件名与指纹
    pool_loaded --> data_plane_verifying
    data_plane_verifying --> verified: 现有 API 路径返回有效 2xx JSON
    data_plane_verifying --> data_plane_failed: 路由 / quota / 选择器异常
    verified --> [*]
    provider_denied --> [*]
    verification_failed --> [*]
    pool_sync_failed --> [*]
    data_plane_failed --> [*]
```

只有 `verified` 计入“自动成功”。`token_written` 代表资产已经生成，失败时应保留私有
auth 供诊断和人工重放，不能自动删除或重新注册同一邮箱。

### 5.3 四道自动门禁

1. **精确 provider 验证**：调用现有 `probe_cpa_record(record)`，它直接使用本次 token，
   能证明不是旧池里其他健康账号替它返回成功。
2. **同步验证**：通过 loopback `CPA_CONTROLLER_URL` 调用 `/v1/credentials/import`；
   controller 负责写 CLIProxy 并 upsert 账号/凭据状态。Token 只在 0600 env 中。
3. **热加载验证**：轮询 auth 列表，确认本次文件名和非秘密指纹已经被 CLIProxy 看到。
4. **真实数据面验证**：从现有 api-gateway/New API 入口发送最小请求，要求 2xx、合法
   JSON 和预期模型响应；这验证的是用户真实调用路径，而不只是 provider 直连。

如果 CLIProxy 后续提供“指定 auth 做 probe”的接口，应把第 3、4 步合并为精确 per-auth
数据面验证。在此之前，必须同时保留第 1 步精确验证和第 4 步全链路验证，避免通用请求
被旧 auth 接管后产生假阳性。

### 5.4 错误分类与面板行为

| 分类 | 例子 | 自动行为 | 面板状态 |
|---|---|---|---|
| 临时验证错误 | timeout、429、可恢复 5xx | 最多 2 次，1 秒/5 秒退避 | `verifying` 后 `verification_failed` |
| 明确 provider 拒绝 | 响应语义为 Access denied、invalid token/grant | 不重试、不重注册，保留 auth | `provider_denied` |
| pool 同步错误 | controller 401/403/422、地址或 Token 缺失 | 终止当前同步，允许修配置后重放 | `pool_sync_failed` |
| 数据面错误 | gateway 5xx、quota、无可用 auth | 不把账号判死；修数据面后重放 probe | `data_plane_failed` |
| 成功 | 精确 token、热加载、真实路径均通过 | 原子写 `verified_at` | `verified` |

面板 API 应返回 masked email、阶段、HTTP status、耗时、attempt 和最近一次非敏感摘要；
禁止返回 token、代理凭据或原始 auth JSON。历史 `ok` 迁移为 `legacy_unverified`，不要在
没有补跑探针的情况下批量改成 `verified`。

上线时用 `CPA_AUTO_VERIFY=false` 作为默认 feature flag，先对单个 canary 开启。恢复任务
只有在 `verified` 后才消费 `sso_pending`；失败记录保留，面板提供“只重试验证/同步”动作，
不得重新走注册、OTP 和 OAuth。

## 6. 缺口、优先级与验收定义

| 优先级 | 能力 | 当前状态 | 解决方案 | 完成定义 |
|---|---|---|---|---|
| P0 | 新 panel -> AI Stack 凭据桥 | **已部署并进入 controller 监控** | controller import + CLIProxy hot load | 新 auth 在两边各精确匹配 1 条 |
| P0 | 自动成功终态 | **新账号 canary 已到达 `verified`** | 四门禁 + `verified` 状态 | provider/controller/pool/public API 均 200 |
| P0 | 代理日志脱敏 | **代码与发布测试已完成** | 所有输出统一 `redact_proxy()` | 测试与日志扫描无 credential |
| P0 | 当前可用注册出口 | **WARP local proxy 健康，1 个 canary 通过** | 保持单账号验证，不把 WARP 宣称为住宅 | 默认路由不变、同账号出口稳定 |
| P1 | 真实动态住宅资源 | **未接入，也不是当前硬阻塞** | 只有持续数据证明需要时再接 sticky provider | 同账号不变、下账号可轮换、ASN 可观测 |
| P1 | managed proxy pool | **已部署并被注册/恢复共用** | 继续补 SQLite lease | 探活、冷却、禁用、fail closed 已有测试 |
| P1 | SQLite 任务账本 | **未实现** | jobs/tasks/accounts/proxies + WAL/lease | 重启、重复回调、worker crash 均不重复计数 |
| P1 | 旧补号 worker 收敛 | **按用户决定保留** | 当前不执行；未来按 G0-G3 | 只有新决定后才进入观察期 |
| P1 | controller | **正式保留** | 作为统一导入和健康状态源 | 不再规划近期停用 |
| P2 | 2 worker | **当前固定 1** | 两个独立健康 session 后 canary | 成功率/P95 不劣化且无 lease 冲突 |
| P2 | 指标与成本 | **只有结果 JSONL** | 阶段时间、出口、流量、失败域指标 | 可算每个 verified 账号成本与 P95 |

### 当前需要的下一步证据

- 间隔足够的更多单账号 canary，用于计算 verified 成功率和阶段 P95；
- controller `ACTIVE=103` 与 CLIProxy `enabled=104` 的那 1 条历史差异盘点；
- 是否部署 CPA-Manager-Plus 作为逐凭据额度读模型的明确决定。

controller、Management API 与数据面密钥均通过专用 `0600` bridge env 接入。动态住宅、
SQLite 和额度看板仍不能写成“已完成”。

## 7. 推荐实施批次

1. **批次 A：闭环。已完成。** controller 桥、精确 probe、热加载和真实数据面均通过；
   继续保持 `batch=1/workers=1/max_slot_retry=0`。
2. **批次 B：受管出口。基础完成。** WARP local proxy 已进入 managed pool；真实动态住宅
   只有出现明确需求证据后再接入。
3. **批次 C：持久化。下一优先。** 引入 SQLite 和 lease，把 JSONL 降级为审计流；做 kill -9、服务
   重启、重复 callback 和 lease 过期回收测试。
4. **批次 D：历史池治理。** 盘点 controller/CLIProxy 差 1 条，明确 import、quarantine、
   retire 的唯一状态源；旧组件当前不收敛。
5. **批次 E：小幅扩容。** 只有两个独立 session 连续 canary 稳定后升到 2 workers；
   目标仍是 verified 成功率和单位成本，不是浏览器启动数。

## 8. 不应做的操作

- 不要因为新面板“注册成功”就直接删除旧补号 volume 或 auth；
- 不要把 Camoufox cache/Xvfb 当成旧 noVNC/browser 容器删除；
- 不要执行宿主机级 `docker system prune -a`，它会影响同机数据面；
- 不要让受管代理池耗尽时静默回退 OVH 直连；
- 不要在同一账号中途自动换 IP，也不要用通用 API 200 替代本次 auth 的精确验证；
- 不要把 token、代理 URL、Management key 写进 SQLite、面板 API 或 JSONL。

## 9. 相关入口

- [当前注册架构与实测流程](OPERATIONS_ARCHITECTURE.md)
- [部署与回滚](../DEPLOYMENT.md)
- [发布检查](../RELEASE_CHECKLIST.md)
- [上游 managed proxy pool PR #6](https://github.com/lij768423-svg/grok-register-panel/pull/6)
- [DataImpulse session id](https://docs.dataimpulse.com/proxies/parameters/session-id.md)
- [DataImpulse session interval](https://docs.dataimpulse.com/proxies/parameters/session-interval.md)
- [mihomo proxy providers](https://wiki.metacubex.one/config/proxy-providers/)
- [mihomo dialer-proxy](https://wiki.metacubex.one/config/proxies/dialer-proxy/)
