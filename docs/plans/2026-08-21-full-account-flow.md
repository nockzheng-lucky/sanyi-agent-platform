# 完整用户流程实施计划：注册 → 登录 → 申请 Key → 使用因子

日期：2026-08-21
状态：计划，12:00 后实施
目标：把当前“拿初始令牌登录页面”升级为完整闭环，页面不再依赖 `?token=`。

## 1. 目标流程

```
未登录用户
  → /register 注册（用户名 + 密码 + 同意协议）
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
  id, username(unique), password_hash, status,
  agreement_version, created_at, last_login_at

user_keys
  id, user_id -> users.id,
  name, token_hash, token_prefix, status(active/revoked),
  quota_total, quota_used,
  rate_limit_per_min, allow_ips,
  expires_at, last_used_at, created_at

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
POST /api/v1/auth/register     {username, password, agree:true}
POST /api/v1/auth/login        {username, password}
POST /api/v1/auth/logout
GET  /api/v1/auth/me           当前用户 + 会话状态
```

安全规则：
- 密码只存 argon2/bcrypt 哈希；
- 登录/注册按 IP + 用户名限流，连续失败锁定；
- 登录错误统一返回“用户名或密码错误”，不暴露用户是否存在；
- 会话 ID 服务端存储，HttpOnly + SameSite=Lax + Secure(生产)；
- 登录成功后轮换 session id。

### 3.2 Key 管理

```text
GET    /api/v1/keys                我的 Key 列表（不含明文）
POST   /api/v1/keys               申请 Key {name, expiresInDays, allowIps?}
POST   /api/v1/keys/{id}/reveal   查看 Key（需重新输入密码或二次确认）
POST   /api/v1/keys/{id}/revoke   撤销
POST   /api/v1/keys/{id}/rotate   轮换（旧 Key 立即失效）
GET    /api/v1/keys/{id}/usage    该 Key 的用量
```

规则：
- 明文只显示一次，库存 SHA-256；
- 每用户最多 5 个 active Key（MVP）；
- 默认有效期 90 天，可申请 30/90/365；
- Key 与订阅状态绑定：订阅有效才可调用因子。

### 3.3 订阅与用量（月费制占位）

```text
GET /api/v1/subscription          {status, expiresAt, plan}
GET /api/v1/usage                最近调用记录
```

当前无支付，MVP 规则：
- 新注册用户默认 `trial`，有效期 7 天；
- 到期后因子调用返回明确错误；
- 管理端可手动激活/延期；
- 支付和真实订阅后续接。

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

### Phase 1：认证与用户（约 2 小时）
- [ ] users 表 + 密码哈希 + 注册/登录/登出 API；
- [ ] 会话升级为 user session；
- [ ] 登录/注册限流与统一错误；
- [ ] 单元测试：注册、登录、密码错误、会话过期、登出。

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
- [ ] 邮箱/手机验证、密码找回；
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

## 7. 需要你确认的问题

1. 注册字段：先只要“用户名 + 密码”，还是必须手机号/邮箱？
2. Key 默认有效期和每用户最多数量；
3. 新用户试用期：7 天是否合适；
4. 用户协议文案现在有没有，还是我先写占位版；
5. 实施是先在本地完成再部署 101，还是边做边上（建议前者）。
