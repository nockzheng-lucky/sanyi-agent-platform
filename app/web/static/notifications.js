"use strict";

async function api(url, options = {}) {
  const resp = await fetch(url, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(body.detail?.message || body.message || `请求失败（${resp.status}）`);
  }
  return body.data;
}

function setError(message) {
  const el = document.getElementById("pushplusError");
  if (el) el.textContent = message || "";
}

function setHint(message) {
  const el = document.getElementById("pushplusHint");
  if (el) el.textContent = message || "";
}

async function refresh() {
  const status = document.getElementById("pushplusStatus");
  const unbind = document.getElementById("unbindBtn");
  try {
    const data = await api("/api/v1/notifications/pushplus");
    const channel = data.pushplus;
    if (channel) {
      status.textContent = `已绑定：${channel.tokenMasked} · 绑定时间 ${channel.updatedAt || channel.createdAt}`;
      if (unbind) unbind.style.display = "";
    } else {
      status.textContent = "尚未绑定。绑定后，你的订阅出现新信号会推送到你的 Pushplus。";
      if (unbind) unbind.style.display = "none";
    }
  } catch (err) {
    status.textContent = "读取失败，请重新登录后再试。";
    setError(err.message);
  }
}

document.getElementById("pushplusForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const token = document.getElementById("pushplusToken").value.trim();
  if (!token) {
    setError("请输入 Pushplus token");
    return;
  }
  setError("");
  setHint("");
  const btn = document.getElementById("bindBtn");
  btn.disabled = true;
  try {
    await api("/api/v1/notifications/pushplus", {
      method: "POST",
      body: JSON.stringify({ token }),
    });
    document.getElementById("pushplusToken").value = "";
    setHint("绑定成功");
    await refresh();
  } catch (err) {
    setError(err.message);
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("unbindBtn").addEventListener("click", async () => {
  if (!window.confirm("确认解绑 Pushplus？解绑后将不再推送。")) return;
  setError("");
  setHint("");
  try {
    await api("/api/v1/notifications/pushplus", { method: "DELETE" });
    setHint("已解绑");
    await refresh();
  } catch (err) {
    setError(err.message);
  }
});

refresh();
