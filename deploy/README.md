# 101 部署候选说明

> 本目录只是候选部署文件。101 是生产机，必须由对应 Line Owner 走正式部署流程；
> 本仓库不会直接执行 SSH、systemd 或任何生产写入。

## 目录（独立于 sanyi）

```text
/home/ubuntu/sanyi-agent-platform/
├── .env                 # 从 deploy/.env.production.example 复制，填 LLM_API_KEY
├── data/                # SQLite：令牌/日志/信号事件，由 systemd ReadWritePaths 放行
├── app/                 # 应用代码（git checkout）
└── .venv/               # Python 虚拟环境
```

## 只读边界

- 读：`/home/ubuntu/projects/sanyi-green/runtime/state/gate_events.sqlite3`
- 写：仅 `/home/ubuntu/sanyi-agent-platform/data`
- 不写、不删除、不改动 `/home/ubuntu/projects/sanyi*` 下任何内容。

## 上线命令（供部署 Owner 参考，非自动执行）

```bash
sudo mkdir -p /home/ubuntu/sanyi-agent-platform/data
sudo chown -R ubuntu:ubuntu /home/ubuntu/sanyi-agent-platform
sudo cp deploy/sanyi-agent-platform.service /etc/systemd/system/sanyi-agent-platform.service
sudo systemctl daemon-reload
sudo systemctl enable --now sanyi-agent-platform
systemctl status sanyi-agent-platform
journalctl -u sanyi-agent-platform -n 50
```

## 验收

1. `curl http://127.0.0.1:8100/api/health` 返回 code=0；
2. 日志出现信号轮询 baseline，无读/写权限报错；
3. 交易时段新地门信号出现后，页面 `/chat` 弹出信号卡片；
4. 页面聊天调用 `deepseek-v4-flash` 正常流式返回。
