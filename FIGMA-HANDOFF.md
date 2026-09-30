# Figma UI/UX 交接包

- 项目：三易引擎 Agent 平台
- 线上地址：https://signal.shhghf.com/chat
- 生产服务器：qh.shhghf.com（101.35.210.32），代码目录 `/home/ubuntu/sanyi-agent-platform`
- 生成时间：2026-09-30

## 默认分支说明（figma-handoff-20260930）

当前默认分支是 **线上生产代码的干净同步快照**（2026-09-30 从 qh.shhghf.com 同步），
作为 Figma 重新设计的**唯一起点**。已排除：

- `.env` / `.env.bak*` 等真实配置
- `data/`（SQLite 用户库与密钥）
- `backups/`、`receipts/`、`state/`、`.venv/`、`build/`、`__pycache__/`、`*.pyc`
- 生产机根目录的旧版重复模块（`/agent /api /engine /factors /mcp /web` 等，服务实际使用 `app/`）

## 其他分支

- `local-working-20260930`：本机 `/Users/gaomengyuan/dev/sanyi-agent-platform` 工作区快照
  （git `main` 头 + 当时未提交的本地改动）。
- `codex/figma-ui-baseline-20260921`：上一轮 Figma UI baseline 分支
  （commit f60c519，含 strategies 页与 shared shell 样式基线）。
- `main`：本地 git 仓库主分支头（334761a）。

## 前端静态资源（设计师重点关注）

路径：`app/web/static/`

| 文件 | 说明 |
|---|---|
| `index.html` | Agent / 主聊天页（`/chat`） |
| `chat.js` | 聊天页主逻辑（历史、流式、订阅面板） |
| `chat_panel.js` | 策略页内嵌 Agent 对话面板 |
| `strategies.html` / `strategies.js` | 策略订阅漏斗页 |
| `factors.html` / `factors.js` | 因子列表页 |
| `style.css` | 全部共享样式与设计变量（`:root` 色板在文件开头） |
| `auth.js` / `login.html` / `register.html` | 登录注册 |
| `keys.html` / `admin.html` / `notifications.html` / `subscription.html` | Key 管理、后台、通知与订阅说明页 |
| `agreement.html` / `privacy.html` | 协议与隐私页 |

路由由 `app/main.py` 定义：`/chat` `/factors` `/crypto` `/strategies` `/keys` `/admin` `/login` `/register` 等。

## 本地运行（可选）

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
cp .env.example .env   # 按需填写；页面 UI 可用 LLM_MOCK=1
uvicorn app.main:app --reload --port 8100
```

打开 http://127.0.0.1:8100/chat

## 交接注意

- 后端生产代码同仓库；Figma 只需读 `app/web/static/` 即可，但保留后端便于理解字段与交互契约。
- 设计变量集中在前端 `style.css` 的 `:root`；改动 UI 时优先从变量层开始。
