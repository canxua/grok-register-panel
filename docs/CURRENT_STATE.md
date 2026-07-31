# 当前状态

这是供下一次 Codex 或 Grok 会话接手的动态快照。现网事实或优先级变化后更新本文件；
稳定架构放在关联的架构文档中。

## 快照

- **核验时间：** 2026-07-31 22:34 Asia/Shanghai
- **本地仓库：** `/Users/jack/project/github/grok-register-panel`
- **OVH 运行代码修复基线：** `fa28bdf`（文档交接提交可能更新其后的 HEAD）
- **可写远端：** `https://github.com/canxua/grok-register-panel` 的 `main`
- **原始上游：** `lij768423-svg/grok-register-panel`；当前 GitHub 身份对其 push 返回 `403`
- **OVH register-panel：** `/opt/grok-register-panel`
- **同机 AI Stack：** `/opt/ai-stack`
- **新面板入口：** 仅监听 `127.0.0.1:18080`，需通过 SSH tunnel 访问
- **公网数据面：** `api.canxu.top`
- **运维控制台：** `ops.canxu.top`
- **本地测试：** `.venv` / Python 3.11.14，完整发布测试通过

## 已确认的现网事实

1. 注册账号、SSO、CPA auth 和日志都保存在 OVH；本地 checkout 的对应目录只有占位文件。
2. `grok-register-panel.service` 已部署 `fa28bdf`，处于 `active/enabled`；loopback health 和
   鉴权 API 均返回 `200`。
3. 生产已创建独立、root-only 的 `.cpa-bridge.env`，systemd 只注入 Management API、
   New API 和四门禁所需值，没有把 controller 的数据库 secret 整包加载进面板。
4. 2026-07-31 的已有 SSO 单凭据回放完整经过
   `token_written -> provider_verified -> pool_uploaded -> pool_loaded ->
   data_plane_verifying -> verified`。精确 provider、CLIProxy 热加载和公网数据面均返回
   `200`；成功记录已从 `sso_pending` 原子出队。
5. 回放首次失败不是 token 被拒：provider 已返回 `HTTP 200`，但探针把
   `max_output_tokens` 设为 `2`，极可能令响应成为 `incomplete`。`fa28bdf` 将固定小预算
   提升到 `16`，仍严格拒绝 `incomplete` 和通用 `200`；修复后同一凭据回放通过。
6. 新账号端到端 canary 仍未闭环。单账号、单 worker、零 slot retry 的测试在约 92 秒后
   停于资料页 Turnstile：`wait-cf:0 (cf_rounds=1/3, 建议换出口)`；它发生在 SSO/auth
   生成之前，因此没有进入 CPA 状态机，也不是桥接失败。
7. canary 后 `CPA_AUTO_VERIFY` 已恢复为 `0`；当前没有注册子进程、timer 或 cron
   自动注册任务。下一次只在受控单账号 canary 期间临时开启。
8. OVH 的 `cpa_auth` 仍有 2 个私有 auth 文件。另有 1 个隔离 canary 文件；运行目录和
   文件保持 `0700/0600`。文件数量不等于逐凭据健康数。
9. 2026-07-31 12:42 的 controller 为 100 个 `ACTIVE` 行、CLIProxy 为 102 个 auth JSON。
   截图中的 101 是当时 controller 的活动行计数，不代表 101 条都刚刚通过独立 provider
   请求。`ACTIVE`、auth 文件数、`verified` 必须分别展示。
10. Managed proxy pool 代码、探活、冷却、fail-closed 和脱敏测试已经合并并部署；但现有
    住宅代理材料抽样返回 `407`，所以它目前没有可用出口，不能提升注册并发。
11. Trellis v1.1.0 已接入 Codex/Grok；`AGENTS.md`、当前状态、决策、gotchas 和本地 BM25
    brain 构成交接链。向量检索仍受本机 QMD/Metal 后端问题影响，不作为启动依赖。

## 当前运行模式

- 新面板：手动 `batch`，固定一账号、一 worker、零 slot retry。
- 连续自动注册：未运行。
- 四门禁：代码和生产桥接回放已验证；默认开关关闭。
- 旧 pool/controller 组件：继续运行并保留，按用户决定暂不收敛。
- 注册出口：OVH 直连能打开 signup，但最新资料页 Turnstile canary 超时。
- 住宅代理：功能可用、外部凭据不可用（`407`）。

## 未完成事项（按优先级）

1. 提供一份余额和授权正常、允许 OVH 连接的住宅代理，先探活，再用同一账号全流程
   sticky session 跑一个新账号 canary；只有它到达 `verified` 才算注册端闭环。
2. 保持旧组件运行，累积多个新账号端到端样本后再讨论退役；不得因一次已有 SSO 回放
   成功就删除旧 auth、volume 或 controller。
3. 增加 SQLite 的 task/account/proxy lease、心跳、幂等键和重放，再考虑升到 2 workers。
4. 增加逐凭据额度视图。现网 CLIProxyAPI 为 `v7.2.88`，官方上游已移除内置 usage
   statistics；中央 Web 看板优先采用 CPA-Manager-Plus，桌面快速核验可用 CLIProxy
   Quota Tray。provider quota、本地 token/cost 和 auth health 必须分开展示。
5. 增加阶段耗时、出口、流量和失败域指标，以 `verified` 成功率、P95 和单凭据成本衡量。

## 恢复检查

```bash
git status --short --branch
bash brain/bin/qmd search "OVH CPA verified Turnstile"
PYTHON_BIN=.venv/bin/python bash scripts/run_tests.sh
```

生产修改前重新核验进程、服务、代理和凭据计数；这些事实会漂移。

## 关联文档

- [注册架构](OPERATIONS_ARCHITECTURE.md)
- [收敛与可靠性缺口](CONVERGENCE_AND_GAPS.md)
- [Trellis 接入](TRELLIS.md)
- [逐凭据额度与用量看板](CREDENTIAL_QUOTA_DASHBOARD.md)
- [部署与回滚](../DEPLOYMENT.md)
