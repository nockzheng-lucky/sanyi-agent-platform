"use strict";

async function api(url, options = {}) {
  const resp = await fetch(url, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(body.detail?.message || body.message || `请求失败（${resp.status}）`);
  return body.data;
}

async function refresh() {
  const sub = await api("/api/v1/subscription");
  const status = document.getElementById("status");
  status.textContent = sub.active
    ? `当前权益：${sub.plan || "已开通"}，到期 ${sub.expiresAt || "长期"}`
    : "当前权益：未开通或已到期";

  const reqs = await api("/api/v1/subscription/requests");
  const list = document.getElementById("requestList");
  list.textContent = "";
  if (!reqs.requests.length) {
    list.innerHTML = '<p class="hint">暂无申请</p>';
    return;
  }
  for (const r of reqs.requests) {
    const div = document.createElement("div");
    div.className = "key-row";
    div.innerHTML = `<div><strong>${r.plan}</strong> · ¥${(r.amount_cents / 100).toFixed(2)} / ${r.period_days}天<br><small>状态：${r.status} · 提交：${r.created_at}</small></div>`;
    list.appendChild(div);
  }
}

document.getElementById("subForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    await api("/api/v1/subscription/requests", {
      method: "POST",
      body: JSON.stringify({
        plan: document.getElementById("plan").value,
        paymentNote: document.getElementById("paymentNote").value.trim(),
      }),
    });
    document.getElementById("formError").textContent = "申请已提交，等待管理员确认";
    refresh();
  } catch (err) {
    document.getElementById("formError").textContent = err.message;
  }
});

refresh().catch((err) => {
  if (err.message.includes("401")) window.location.href = "/login";
  else document.getElementById("formError").textContent = err.message;
});
