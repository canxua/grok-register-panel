# 当前状态

这是供下一次 Codex 或 Grok 会话接手的动态快照。现网事实或优先级变化后更新本文件；
稳定架构放在关联的架构文档中。

## 快照

- **核验时间：** 2026-08-01 01:50 Asia/Shanghai
- **本地仓库：** `/Users/jack/project/github/grok-register-panel`
- **OVH register-panel 运行基线：** `7af0e72`
- **AI Stack controller 契约修复：** `f31b737`
- **可写远端：** `https://github.com/canxua/grok-register-panel` 的 `main`
- **原始上游：** `lij768423-svg/grok-register-panel`
- **OVH register-panel：** `/opt/grok-register-panel`
- **同机 AI Stack：** `/opt/ai-stack`
- **新注册面板：** OVH `127.0.0.1:18080`，只通过 SSH tunnel 访问
- **AI Stack 控制台：** `https://ops.canxu.top/dashboard`
- **公网数据面：** `https://api.canxu.top`

本地访问新注册面板：

```bash
ssh -N -L 18080:127.0.0.1:18080 ovh-ai-stack
```

随后打开 `http://127.0.0.1:18080`。这不是 `ops.canxu.top`；后者是 AI Stack 的只读
运维控制台。

## 已确认事实

1. 注册账号、SSO、面板 auth、代理池和日志都保存在 OVH。当前正式 `cpa_auth/` 有
   3 个私有文件，隔离目录另有 1 个 canary 文件；本地 checkout 不保存这些秘密。
2. `grok-register-panel.service` 已部署 `7af0e72`，处于 `active/enabled`，loopback
   `/api/health` 返回 `ok=true`。面板服务在线不等于注册任务正在运行。
3. 官方 Cloudflare WARP 客户端以 local proxy 模式监听 `127.0.0.1:40000`；
   `warp-cli` 为 `Connected / healthy`。受管代理池看到 1 个健康出口。宿主机默认路由
   仍是 `ens3`，所以 AI Stack、SSH、邮箱管理 API 和普通宿主机流量没有被全局接管。
4. 2026-07-31 的单账号 canary 使用上述受管出口，在约 59 秒内完成邮箱 OTP、
   Turnstile、SSO 和 OAuth token 获取，`botFlagSource=0`。这证明当前路径至少有一个
   完整注册样本，不等于长期成功率或动态住宅能力已经得到证明。
5. 同一 canary 的 Device Flow token 能签发，但精确 provider 请求返回 `403
   permission-denied`；随后用同一 SSO 走 Authorization Code，token 带
   `referrer=grok-build`，精确 provider 请求成功。生产默认已改为 Authorization Code
   优先，Device Flow 仅作回退。
6. 严格 provider 探针仍拒绝通用 `200` 和 `status=incomplete`。探针现用
   `Reply with exactly pong.` 和 `max_output_tokens=128`；旧预算 2/16 会把可用 token
   错判为失败。
7. 该账号随后由恢复流程在约 7 秒内完成
   `provider_verified -> pool_uploaded -> pool_loaded -> data_plane_verifying -> verified`，
   报告为 `1/1 verified`、失败 0、待处理 0。
8. register-panel 现在通过 AI Stack 的 `/v1/credentials/import` 导入已验证 auth，
   controller 再统一写入 CLIProxy。控制器契约已允许 CLIProxy 现有的
   `xai-<email>.json` 文件名，同时仍拒绝路径分隔符和非 JSON 名称。
9. 最终现场回归为：精确 provider `200`、控制器导入 `200`、公网数据面 `200`；
   controller `103 ACTIVE / 119 total`，CLIProxy `104 enabled`。本次新 auth 在两边都
   精确匹配 1 条，已经进入 AI Stack 监控，不再只是 CLIProxy 文件。
10. controller 与 CLIProxy 仍相差 1 个 active/enabled 口径，需要单独盘点历史凭据；
    这个差值不能用来否定本次新 auth 的精确匹配结果。
11. `registration_enabled=false`、`CPA_AUTO_VERIFY=0`，且没有
    `run_until_100.py` 或 `run_batch_headless.py` 子进程。当前自动注册没有在执行。
12. Trellis `v1.1.0` 已同时接入 Codex 与 Grok；Codex 读 `AGENTS.md`，Grok 复用
    `.grok/` 和共享 hook，本地 BM25 brain 是当前可靠检索基线。

## 当前运行模式

- 新面板：服务常驻，注册任务手动启动；生产保持一账号、一 worker、零 slot retry。
- 自动补号：关闭；没有 timer、cron 或面板子任务在持续注册。
- 成功口径：必须依次通过精确 provider、controller/CLIProxy 导入、热加载和公网数据面。
- 注册出口：当前使用 WARP local proxy 的受管出口；不可称为动态住宅。
- 使用出口：现有 CLIProxy/AI Stack 路径保持不变，未切到住宅网络。
- 旧 pool/controller：按用户决定保留；controller 现在还是凭据监控和导入的正式组成。

## 已解决的根因

| 故障 | 已确认根因 | 修复 |
|---|---|---|
| OVH 普通 `curl` signup 为 403 | 请求指纹被边缘 WAF 区分，不是整台 OVH 完全断网 | 预检使用浏览器兼容指纹，正式注册走 Camoufox |
| OVH 直连卡 Turnstile | 当前 OVH 直连会话在资料页没有过挑战；发生在 SSO 之前 | 注册 worker 使用受管 WARP local proxy，单 canary 已通过 |
| token 已签发但 Grok Build 403 | Device token 的 provider eligibility/referrer 不满足当前数据面 | Authorization Code 优先，同一 SSO 精确探针已 200 |
| provider HTTP 200 仍判失败 | 输出预算太小，响应成为 `incomplete` | 使用 128-token 完成预算并继续做语义校验 |
| 新 auth 在 CLIProxy 但不进控制台 | 面板绕过 controller 直传，两个状态库分叉 | 统一调用 controller import API，再做 CLIProxy 热加载检查 |
| controller import 返回 422 | 文件名含 `@`，控制器模型与 CLIProxy 命名契约不一致 | 允许 `@`，继续拒绝路径穿越字符 |
| 恢复时路径与注册时不同 | 恢复任务没有继承受管代理 | 恢复任务读取同一个健康代理快照，配置池为空时 fail closed |

## 仍未完成

1. **持续成功率未知。** 当前只有一个 WARP 新账号闭环样本；保持自动注册关闭，后续只做
   间隔足够的单账号 canary，记录阶段耗时和失败域。
2. **WARP 不是动态住宅。** 它解决了这次出口/Turnstile路径，但不提供住宅 ASN、按账号
   轮换或国家选择。只有供应商确实提供动态住宅 gateway 时，才可启用账号级 sticky
   session。
3. **持久任务账本未实现。** 当前结果以私有文件和 JSONL 保存，尚无 SQLite task、
   account、proxy lease、heartbeat 和幂等重放。
4. **历史池差 1 条。** controller `ACTIVE=103` 与 CLIProxy `enabled=104` 尚未逐条审计；
   新 auth 已精确对齐，剩余差异属于旧数据治理。
5. **逐凭据额度页未部署。** CLIProxy 可以提供池和队列能力，但中央额度读模型仍建议
   使用 CPA-Manager-Plus；细节见 `CREDENTIAL_QUOTA_DASHBOARD.md`。
6. **向量检索未启用。** QMD BM25 正常；本机 Metal 后端仍阻塞 vector/hybrid，且不影响
   Codex/Grok 的文档连续性。

## 恢复检查

```bash
git status --short --branch
bash brain/bin/qmd search "WARP controller import verified"
PYTHON_BIN=.venv/bin/python bash scripts/run_tests.sh
ssh -o ClearAllForwardings=yes ovh-ai-stack \
  'sudo systemctl is-active grok-register-panel.service; sudo warp-cli --accept-tos status'
```

生产事实会漂移。重新回答“是否在注册、多少凭据健康”前，必须同时检查进程、controller、
CLIProxy 和最近精确探针，不能只看截图。

## 关联文档

- [注册架构](OPERATIONS_ARCHITECTURE.md)
- [收敛与可靠性缺口](CONVERGENCE_AND_GAPS.md)
- [逐凭据额度与用量看板](CREDENTIAL_QUOTA_DASHBOARD.md)
- [Trellis 接入](TRELLIS.md)
- [部署与回滚](../DEPLOYMENT.md)
