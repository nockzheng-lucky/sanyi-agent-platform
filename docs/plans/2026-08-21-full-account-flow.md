# 完整用户流程实施计划：注册 → 登录 → 申请 Key → 使用因子

日期：2026-08-21
状态：计划，12:00 后实施
目标：把当前“拿初始令牌登录页面”升级为完整闭环，页面不再依赖 `?token=`。

## 1. 目标流程

```
未登录用户
  → /register 输入手机号
  → 发送短信验证码（本地/测试环境可走 MOCK 通道）
  → 输入验证码 + 设置密码 + 同意用户协议
  → 自动登录并跳转 /chat
  → 系统检查：有没有可用 Key？
       ├─ 没有 → 引导到 /keys 申请 Key
       └─ 有   → 进入 /chat
  → 页面 Agent 使用当前用户的 Key 调用因子
  → 用量归属用户和 Key，写入 usage_logs
  → 用户可随时查看/撤销/轮换 Key
```

管理端（最小）：
```
Admin 登录 → 用户列表、Key 列表、禁用/启用、用量查询
```

## 2. 数据模型升级

在现有 SQLite `platform.sqlite3` 上增量迁移，不改 signal_events 逻辑。

```text
users
  id, phone_hash(unique), phone_masked, password_hash, status,
  agreement_version, created_at, last_login_at

sms_codes
  id, phone_hash, code_hash, purpose, expires_at,
  attempts, verified, created_at

user_keys
  id, user_id -> users.id,
  name, token_hash, token_prefix, status(active/revoked),
  quota_total, quota_used,
  rate_limit_per_min, allow_ips,
  expires_at NULLABLE,           -- 可选：不填=长期，安全靠撤销/轮换
  last_used_at, created_at

chat_sessions（扩展）
  id, user_id -> users.id,
  session_hash, expires_at, created_at, user_agent, ip

usage_logs（扩展）
  + user_id, key_id
```

现有 `tokens` 表迁移策略：
- 新表 `user_keys` 上线；
- 当前 `initial-admin-v2` 令牌迁移为 bootstrap admin 用户的 Key，验证通过后仅保留给运维测试；
- 旧 `/chat?token=` 入口保留为开发/兼容入口，生产登录流程不再使用。

## 3. 后端接口

### 3.1 认证

```text
POST /api/v1/auth/sms-code    {phone, purpose:"register"}  发送验证码
POST /api/v1/auth/register    {phone, code, password, agree:true}
POST /api/v1/auth/login       {phone, password}
POST /api/v1/auth/logout
GET  /api/v1/auth/me          当前用户 + 会话状态
```

手机号规则：
- 只接受中国大陆手机号格式（MVP），存 HMAC(phone) 哈希 + 掩码尾号；
- 验证码 6 位，5 分钟有效，同号 60 秒一次、单日上限；
- 短信通道做成 adapter：本地 MOCK（日志/固定码），101 接真实短信前不放开注册。

安全规则：
- 密码只存 argon2/bcrypt 哈希；
- 登录/注册按 IP + 手机号限流，连续失败锁定；
- 登录错误统一返回“手机号或密码错误”，不暴露用户是否存在；
- 会话 ID 服务端存储，HttpOnly + SameSite=Lax + Secure(生产)；
- 登录成功后轮换 session id。

### 3.2 Key 管理

```text
GET    /api/v1/keys                我的 Key 列表（不含明文）
POST   /api/v1/keys               申请 Key {name, expiresInDays?, allowIps?}
POST   /api/v1/keys/{id}/reveal   查看 Key（需重新输入密码或二次确认）
POST   /api/v1/keys/{id}/revoke   撤销
POST   /api/v1/keys/{id}/rotate   轮换（旧 Key 立即失效）
GET    /api/v1/keys/{id}/usage    该 Key 的用量
```

规则：
- 明文只显示一次，库存 SHA-256；
- 每用户最多 5 个 active Key（MVP）；
- `expiresInDays` 可选：不填 = 长期 Key，安全靠撤销/轮换；填 30/90/365 则到期失效；
- **Key 有效期不负责控制月费**。Key 只负责“身份凭证”，订阅单独控制“能不能用”；
- 月费到期时：Key 保留，但因子/聊天返回明确提示“订阅已到期”，续费后立即恢复。

### 3.3 订阅与用量（月费制占位）

```text
GET /api/v1/subscription          {status, expiresAt, plan}
GET /api/v1/usage                最近调用记录
```

当前无支付，MVP 规则（**无试用期**）：
- 用户注册成功后即为 `active`，没有 trial；
- 未来接入支付后，改为“支付成功才 active”，未支付不可用；
- 管理端现在可手动停用/恢复（风控用），不提供免费延期概念；
- 真实月费订阅上线前，所有 MVP 用户视为已授权测试用户。

### 3.4 页面聊天归属

- `/api/chat/session` 返回：用户信息 + 当前默认 Key 状态；
- `/api/chat` 内部解析：用户 → 默认 active Key → 因子调用归属该 Key；
- 页面 JS 不再接触 Key 明文。

## 4. 前端页面

MVP 先保证功能，UIUX 后续统一重做。

```text
/login        登录
/register     注册（含协议勾选）
/keys         Key 申请与列表
/chat         现有页面，改为登录会话驱动
/logout       退出
```

关键交互：
- 注册成功后自动登录并跳转 `/chat`；
- 没有 Key 时 `/chat` 顶部提示“先申请 Key”并提供跳转；
- 申请 Key 成功后：明文显示一次 + “复制”按钮 + 提示安全保存；
- 撤销 Key 后，聊天立即停止该 Key 的因子调用。

## 5. 实施步骤（12:00 后开始）

### Phase 1：手机号认证与用户（约 2.5 小时）
- [ ] users/sms_codes 表 + 手机号 HMAC + 密码哈希；
- [ ] 短信 adapter（MOCK 先行）+ 注册/登录/登出 API；
- [ ] 会话升级为 user session；
- [ ] 登录/注册/验证码限流与统一错误；
- [ ] 单元测试：发码、注册、登录、密码错误、会话过期、登出。

### Phase 2：Key 生命周期（约 1.5 小时）
- [ ] user_keys 表 + 迁移脚本（备份优先）；
- [ ] 申请/列表/查看/撤销/轮换 API；
- [ ] Key 与用户、用量归属；
- [ ] 测试：申请只显示一次、撤销后立即失效、轮换正确。

### Phase 3：页面闭环（约 1.5 小时）
- [ ] `/login`、`/register`、`/keys` 静态页面；
- [ ] `/chat` 改为用户会话，去掉对 `?token=` 的依赖（保留兼容）；
- [ ] 无 Key 引导申请；
- [ ] 测试：注册 → 登录 → 申请 Key → 聊天调因子 → 用量正确。

### Phase 4：101 部署（约 1 小时）
- [ ] 备份 `platform.sqlite3`；
- [ ] 数据迁移 + 冒烟测试；
- [ ] systemd 重启；
- [ ] 回滚方案：恢复备份 DB + 回退 commit。

### Phase 5：后续（不阻塞 MVP）
- [ ] 真实短信供应商接入；
- [ ] 密码找回（短信验证码重置）；
- [ ] 管理后台；
- [ ] 支付/月费订阅；
- [ ] HTTPS 域名 + 正式 UIUX。

## 6. 验收标准

1. 新用户可注册 → 自动登录；
2. 用户可申请 Key，明文只出现一次；
3. 无 Key 用户进入聊天会被正确引导；
4. 撤销 Key 后，该 Key 立即无法调用因子；
5. 每次因子调用都能在 usage_logs 查到 user + key；
6. 旧数据不丢，signal_events 与门信号推送不受影响；
7. 测试全绿，101 部署可回滚。

## 7. 已确认决策

1. 注册：**手机号注册**（短信验证码；先 MOCK，接入真实短信前不开放线上注册）；
2. Key：**有效期可选**，不填 = 长期；Key 只做身份凭证，**不控制月费**，订阅单独控制；
3. 试用期：**无**。MVP 阶段注册即 active；接支付后改为支付成功才 active；
4. 用户协议：先写占位版。这属于合规要求的一部分（用户协议/隐私政策/风险提示），正式收费前必须替换为最终版并法务确认；
5. 实施顺序：**本地开发测试完成 → 再部署 101**，保留回滚。
