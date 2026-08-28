# 三易信号 Agent 平台 · 交接文档

日期：2026-08-22
用途：给新对话/新 Agent 的完整上下文。读完本文档即可继续开发、测试、部署。

---

## 1. 项目是什么

三易引擎的“门信号因子”对外开放平台：

- 数据源：101 服务器上 sanyi-green 的 `gate_events.sqlite3`；
- 筛选：当天交易日内的地门信号（5m / 15m / 1h）；
- 产品形态：
  - 用户手机号注册 / 登录；
  - 申请 `sk-sanyi-...` Key；
  - 页面 Agent（DeepSeek）解读门信号；
  - 实时信号推送（SSE + 声音 + 浏览器通知）；
  - 订阅：礼品卡兑换（当前 MOCK）；
  - 管理后台：用户、Key、用量、订阅申请。

---

## 2. 代码与部署位置

### 本地仓库
```text
/Users/gaomengyuan/dev/sanyi-agent-platform
分支：main
```

### 101 服务器
```text
/home/ubuntu/sanyi-agent-platform
服务：sanyi-agent-platform.service
监听：127.0.0.1:8100
域名：https://signal.shhghf.com
证书：/etc/nginx/ssl/signal.shhghf.com.pem
      /etc/nginx/ssl/signal.shhghf.com.key
nginx 配置：/etc/nginx/sites-available/signal.shhghf.com.conf
          → symlink /etc/nginx/sites-enabled/
```

SSH（本地 Mac 直连，无需 ssh_cluster 配置）：
```bash
ssh ubuntu@101.35.210.32
```

---

## 3. 当前账号

### 管理员
```text
手机号：15822408517
密码：888888
角色：admin
```
登录后侧边栏才会出现“管理后台”。普通用户看不到管理后台。

### 其他已有账号
数据库 `users` 表里有若干测试用户，大多密码为 `password123`（烟雾测试账号）。

---

## 4. 关键配置（服务器 /home/ubuntu/sanyi-agent-platform/.env）

```bash
SANYI_DATA_DIR=/home/ubuntu/sanyi-agent-platform/data
SANYI_GATE_EVENTS_DB=/home/ubuntu/projects/sanyi-green/runtime/state/gate_events.sqlite3
SANYI_TRADING_SESSIONS=09:00-11:30,13:00-15:00,21:00-02:30
SANYI_SIGNAL_POLL_ENABLED=true
SANYI_SIGNAL_POLL_SECONDS=30
SANYI_COOKIE_SECURE=true
SANYI_SMS_MOCK=true
SANYI_SUBSCRIPTION_REQUIRED=false

LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-flash
LLM_API_KEY=<已在 .env 中，不要打印>
```

重要：
- `PHONE_HASH_SECRET` 未配置时自动生成到 `data/phone_secret`；
- `SANYI_SMS_MOCK=true`：注册验证码会通过 `debugCode` 返回并自动填入；
- `SANYI_SUBSCRIPTION_REQUIRED=false`：订阅目前不拦截使用；接支付后改 `true`；
- Key 加密存储，加密密钥由 `PHONE_HASH_SECRET` 派生。

---

## 5. 当前功能状态

### 已完成
- 手机号注册 / 登录 / 登出；
- Key 申请、默认隐藏、显示/隐藏、复制、删除（删除后立即从列表消失）；
- 侧边栏导航：信号聊天 / Key 管理 / 订阅（admin 多一个管理后台）；
- 聊天页无 Key 时打开并顶部提示，不自动跳转；
- 页面 Agent 调 DeepSeek function calling；
- 门信号读取自 `gate_events.sqlite3`，只取当天交易日；
- 具体合约展示：`tscode` / `actual_contract`；
- 实时信号 SSE 推送、动画、声音、浏览器通知；
- 订阅：MOCK 礼品卡兑换 + 时长累加；
- 管理后台：用户禁用/启用、Key 强制删除、订阅申请确认、用量查询；
- 用户协议/隐私政策占位页；
- HTTPS + nginx 正式域名。

### Mock 数据 / 测试代码
```text
短信验证码：自动填
礼品卡：MOCK30 / MOCK90 / MOCK365
```

### 待办
1. 真实腾讯云短信接入；
2. 真实礼品卡生成 / 支付；
3. 正式用户协议 / 隐私政策（法务确认）；
4. UIUX 正式重做；
5. MCP + Skill + REST 开放给用户自己的 Agent；
6. 管理后台增强；
7. Web Push（页面关闭也能收通知）。

---

## 6. 目录结构

```text
app/
  main.py                  FastAPI 入口、页面路由
  config.py                环境配置
  db.py                    SQLite schema + 迁移 + 基础存储
  accounts.py              用户/短信/密码/Key/订阅/管理端函数
  security.py              鉴权、会话、限流、actor
  factor_registry.py       因子注册表
  factors/dimen_gate_signal.py  地门信号因子
  engine/
    gate_reader.py         读取 gate_events.sqlite3
    poller.py              30 秒轮询 + 新事件推送
    session_clock.py       交易时段判断
    signal_bus.py          SSE 内存总线
  agent/                   LLM function calling
  api/                     auth/keys/admin/subscription/factors/signals/chat
  web/static/              前端页面（登录/注册/keys/chat/admin/subscription/协议）
docs/
  plans/2026-08-21-full-account-flow.md
  deployments/2026-08-20-101-online-test.md
deploy/
  sanyi-agent-platform.service
  nginx-signal.shhghf.com.conf
  .env.production.example
scripts/
  create_token.py
  create_admin.py
```

---

## 7. 本地开发与测试

```bash
cd /Users/gaomengyuan/dev/sanyi-agent-platform
. .venv/bin/activate

# 测试（必须全绿再部署）
python -m pytest -q

# 本地启动
LLM_MOCK=1 python -m uvicorn app.main:app --reload --port 8100
```

注意：
- Python 3.9 环境没有 `hashlib.scrypt`，密码哈希已用 PBKDF2；
- 依赖：fastapi / uvicorn / httpx / jsonschema / pydantic / dotenv / cryptography；
- 新增依赖时用清华镜像安装：
```bash
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple <package>
```

---

## 8. 部署到 101 的标准流程

```bash
cd /Users/gaomengyuan/dev/sanyi-agent-platform
. .venv/bin/activate
python -m pytest -q

# 提交
git add <files>
git commit -m "..."   # 注意 config.py 等文件不要漏

# 打包并上传
git archive --format=tar.gz -o /tmp/sanyi-agent-platform.tar.gz HEAD
scp /tmp/sanyi-agent-platform.tar.gz ubuntu@101.35.210.32:/tmp/

# 服务器上部署（先备份数据库，再解压重启）
ssh ubuntu@101.35.210.32
cd /home/ubuntu/sanyi-agent-platform
STAMP=$(date +%Y%m%d_%H%M%S)
cp data/platform.sqlite3 backups/platform.sqlite3.before-$STAMP
tar -xzf /tmp/sanyi-agent-platform.tar.gz -C /home/ubuntu/sanyi-agent-platform
sudo systemctl restart sanyi-agent-platform
systemctl status sanyi-agent-platform
curl -sS https://signal.shhghf.com/api/health
```

部署后必须做的验证：
1. `/api/health` 返回 200；
2. 登录管理员或测试账号；
3. 实际走一遍受影响的功能；
4. 有 Key/订阅/权限相关改动时，用 API 或页面做端到端验证。

---

## 9. 数据库关键表

```text
users                  手机号哈希、密码哈希、角色、订阅到期
user_keys              Key 哈希 + Fernet 加密存储
user_sessions          登录会话
sms_codes              MOCK 验证码
signal_events          信号事件（含具体合约）
subscription_requests  旧的线下支付确认申请
gift_card_redemptions  礼品卡兑换记录
usage_logs             调用日志（user_id / key_id）
tokens                旧版 legacy token（兼容）
chat_sessions          旧版 token 会话（兼容）
```

数据库文件：`/home/ubuntu/sanyi-agent-platform/data/platform.sqlite3`
备份目录：`/home/ubuntu/sanyi-agent-platform/backups`

---

## 10. 安全约定

- 访问日志已关闭，不要在日志里打印 Key 明文；
- `.env` 权限 600，Key 文件权限 600；
- 用户密码 PBKDF2，Key Fernet 加密；
- 页面 cookie HttpOnly + Secure；
- 不要修改 sanyi-green 目录下任何文件，门信号 SQLite 只读；
- 不要动 nginx 上 qh.shhghf.com 和 shenyi-preview.shhghf.com；
- 生产写入前备份数据库；
- 当前正式域名已经可用，不要再使用本地 SSH 隧道测试。

---

## 11. 已知坑

- `nginx 1.18` 不支持 `http2 on;`，要用 `listen 443 ssl http2;`；
- `?token=` 旧入口仍兼容 legacy token；
- 旧版 Key（无加密备份）不支持“显示/复制”，提示用户删除重建；
- 测试日期相关用例要使用相对时间，不要写死历史日期；
- bash 工具中避免用含中文的 heredoc，容易异常；修改文件优先用 `str_replace_editor`；
- 每次 `git add` 前用 `git status --short` 检查是否有漏提交文件（历史上漏过 `config.py` 导致服务启动失败）。
